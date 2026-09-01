import json
from django.db.models import Q
from products.models import Product
from .ai.client import chat
from .sales import naming
from .static_faq_service import normalize_arabic


class Resolution(list):
    """The perfumes this message could be placed on, carrying the names that could not be.

    A `list` subclass rather than a tuple or a dataclass, because `resolve_products` has six call
    sites and every one of them — plus every `mock.patch(..., return_value=[...])` in the test
    suite — treats the result as a plain list of products. Widening the return type would have
    meant touching all of them for the benefit of the one caller that needs the extra half.
    Callers read the extra half as `getattr(result, "unplaced", ())`, which degrades to today's
    behaviour for anything that is still a plain list.

    Why the channel has to exist at all: `product_info` decided a name was unplaceable by looking
    at whether `products` came back empty, which is only true when *every* name failed. Conversation
    836 asked for three perfumes, two were in stock, and the third — الكساندريا 2 — was dropped in
    silence: no pending record, no owner notification, and the customer asked three times for a
    price nobody had been told to look up. The resolver knew which name it could not place; there
    was simply nowhere for it to say so.
    """

    def __init__(self, products=(), unplaced=()):
        super().__init__(products)
        self.unplaced = tuple(unplaced)


def _unplaced_names(candidates, message, store, products):
    """The model's unplaced spans, minus everything Python can prove wrong about them.

    Whatever survives here is written verbatim into the conversation record as an open question, and
    the next turn reads that record back as fact — so a hallucinated span becomes a perfume the
    customer never named, denied by name in our own words. Three guards, each for a failure that
    would otherwise be indistinguishable from a real one:

      * a span the deterministic matcher *can* place is stocked, and recording it would let the next
        turn deny a perfume we carry — red line 3, and the worst outcome in this file;
      * a span with no identifying tokens is function words or a chase verb ("لقيتو"), which is what
        the resolver returns when asked to place a message that names nothing;
      * a span whose words are not in the customer's message was invented here, not read.

    The last check is substring-wise per token rather than a token-subset test, because Arabic
    attaches conjunctions: 836 turn 1 tokenises to "والكساندريا" and the name is "الكساندريا 2".
    """
    normalized_message = normalize_arabic(message or "")
    kept = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        # The model is told to strip the conjunction; do not rely on it having done so.
        name = candidate.strip().lstrip("و").strip()
        if not name or name in kept:
            continue
        if naming.match_product(name, store, products=products):
            continue
        if not naming.identifying_tokens(name):
            continue
        if not all(token in normalized_message for token in naming.tokens(name)):
            continue
        kept.append(name)
    return kept


def resolve_products(message: str, history=None, store=None, conversation=None):
    """
    Try to resolve multiple products from the user's message using AI extraction.

    `conversation` is optional and used only to anchor pronoun resolution: it supplies the
    perfumes we most recently offered, derived from `Message.internal_context`. Without it the
    only reference guidance is one sentence below plus a rule scoped to short confirmations, and
    a doubt utterance matches neither — "مش متوفر متأكد ؟" about Versace Eros resolved to two
    perfumes from two turns earlier (conversation 1099) because nothing pointed at the newest.

    Returns a `Resolution` — a list of products that also reports which named perfumes it could not
    place. See that class for why the second half is needed.
    """
    products = Product.objects.filter(is_active=True)
    if store:
        products = products.filter(store=store)
    product_names = list(products.values_list('name', flat=True))

    from .sales import described as sales_described

    offered_block = sales_described.offered_context_block(conversation, store)

    prompt = f"""
Extract the exact perfume names the user is inquiring about.
Look at the conversation history if the user is using pronouns or referring to something previously mentioned (like "بكام ده" or "عامل كام" or "الاتنين").

Available Perfumes in Database:
{product_names}
{offered_block}
Rules:
1. Translate Arabic names to English and fix spelling mistakes to match the exact names in the database.
2. Be HIGHLY tolerant of phonetic Arabic transliterations and typos (e.g., 'فريساتشي يورس' or 'ايروس' -> 'Versace Eros', 'ديور سيفاج' -> 'Dior Sauvage', 'امبيرو' -> 'Ambero').
3. Check if the requested perfumes exist in the Available Perfumes list.
4. If the perfumes exist in the list, return their exact names from the list.
5. CRITICAL: If a requested perfume is absolutely NOT in the list, ignore it and DO NOT include it in the output. ❌ NEVER hallucinate or return a random/different perfume from the list just to fill the output. If you can't confidently map the user's word to a perfume in the list, return an empty list.
6. CRITICAL: If the user's message is a short confirmation (e.g. "ماشي", "تمام", "ايوة", "اه", "قول سعرهم") in response to the assistant's offer to show prices or details, you MUST extract ALL the perfume names that the assistant explicitly recommended or mentioned in its IMMEDIATELY PRECEDING message.
7. 🔴 CRITICAL — THE NEWEST TURN WINS. If the user names no perfume and is instead reacting to what you just said — doubting it ("مش متوفر متأكد ؟", "متأكد؟", "بجد؟"), asking about it ("بكام؟", "ثباته ايه؟", "فيه أحجام تانية؟"), or pointing at it ("ده", "دي") — the subject is the perfume in the "PERFUMES YOU JUST OFFERED" block above, entry 1 unless they say otherwise. ❌ NEVER reach further back in the history for a perfume you offered on an earlier turn: a customer who questions what you just said is talking about what you just said, not about something two turns ago.
8. 🔴 CRITICAL — IF they name several perfumes, return ALL of them. When the message DOES name two or three perfumes, map each one independently and return every one you can place — ❌ never drop one because you were less sure of its spelling. A customer who asked "عايز ديور سيفاج و بلو دى شنيل و ايروس، بكام التلاته؟" got two prices and "let me check on that one" for the third, which was in the catalogue the whole time.
   ⚠️ This rule NEVER creates a name. It only stops you dropping one. If the message names NO perfume at all — "العطر اللي رشحتوه ليا مش عاجبني", "مش عايز حاجة", "ايه تاني" — the answer is still an empty list. Rule 5 wins: returning a perfume the customer never mentioned is the worst possible output, and guessing one from a complaint told a customer their opinion was about a perfume nobody had named.
9. 🔴 CRITICAL — REPORT WHAT YOU LEFT OUT. Every name you drop under rule 5 because it is not in the Available Perfumes list MUST appear in the "unplaced" list.
   • Quote it in the **customer's own words and the customer's own script** — if they wrote it in Arabic, return the Arabic exactly as they typed it. ❌ Never translate it, never transliterate it into Latin letters, never correct its spelling: we are saying we do not know this perfume, so a spelling we invented for it is a fabrication.
   • Strip a leading conjunction ("والكساندريا 2" -> "الكساندريا 2") and nothing else.
   • ❌ Never put a perfume that IS in the list here. ❌ Never put pronouns, question words or verbs here ("لقيتو", "ده", "بكام") — if the message names no perfume, "unplaced" is empty too, exactly like "perfumes".
   • A customer asked "عايز اعرف اسعار بلو دي شانيل وسوفاج والكساندريا 2": two of those are in the list and one is not, so perfumes gets the two and unplaced gets ["الكساندريا 2"]. Leaving it out of both is how that customer got asked to wait three times for an answer nobody was looking up.
"""
    prompt += """
Output format MUST be valid JSON:
{"perfumes": ["Exact Name 1", "Exact Name 2"], "unplaced": ["اسم العطر بكلام العميل"]}
(Both lists may be empty. "perfumes" holds names FROM the list above; "unplaced" holds names that are NOT in it.)
"""
    try:
        messages = [{"role": "system", "content": prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        response = chat(messages, profile="extract", response_format={"type": "json_object"})

        data = json.loads(response)
        p_names = data.get("perfumes", [])
        raw_unplaced = data.get("unplaced", [])
    except Exception:
        p_names = []
        raw_unplaced = []

    if not isinstance(raw_unplaced, list):
        raw_unplaced = []
    unplaced = _unplaced_names(raw_unplaced, message, store, products)

    if not isinstance(p_names, list) or not p_names:
        return Resolution([], unplaced)

    resolved = []
    for p_name in p_names:
        if not p_name: continue
        # First try exact match from the list
        exact_match = products.filter(name__iexact=p_name).first()
        if exact_match:
            resolved.append(exact_match)
            continue

        # Then the deterministic token matcher, which handles reordering ("9pm by Afnan"
        # for "Afnan 9PM") and a one-character slip ("Ambiro" for "Ambero"), and returns
        # nothing when a name is ambiguous.
        #
        # This replaces a loose AND of `icontains` over each word, which was actively
        # dangerous: it resolved a mis-transliterated "اوداورا" to *Dark Aura* — a
        # different real perfume — and the bot then confidently compared the wrong one.
        # The prompt above tells the model never to substitute a different perfume; the
        # Python fallback was doing exactly that behind its back.
        match = naming.match_product(p_name, store, products=products)
        if match and match not in resolved:
            resolved.append(match)

    return Resolution(resolved, unplaced)


def resolve_product(message: str, history=None, store=None, conversation=None):
    """
    Try to resolve a single product. Returns the first matched product or None.
    """
    resolved = resolve_products(message, history, store, conversation)
    return resolved[0] if resolved else None