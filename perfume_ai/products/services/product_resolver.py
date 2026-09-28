import json
import logging

from django.db.models import Q
from products.models import Product
from .ai.client import chat
from .sales import naming
from .static_faq_service import normalize_arabic

logger = logging.getLogger(__name__)


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

    `failed` is the third channel, and it exists because the two above cannot distinguish "the
    model read this message and placed nothing" from "we never got an answer". `resolve_products`
    swallows every exception into empty lists, so an API timeout or a malformed JSON payload looks
    identical to a message that names no perfume. That was harmless while an unplaceable name only
    produced a promise to check — but `products.services.absence` turns an unplaceable name into a
    *denial*, and a denial issued because the extractor blipped is the Versace Eros incident with
    an infrastructure cause. Read it as `getattr(result, "failed", False)` so a plain list, and
    every patched `return_value=[]`, means "no failure to report" rather than crashing.
    """

    def __init__(self, products=(), unplaced=(), failed=False):
        super().__init__(products)
        self.unplaced = tuple(unplaced)
        self.failed = bool(failed)


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


_FAMILIES_HEADER = (
    "Perfume lines (each line below is ONE family: same house, one name nested inside another. "
    "A customer naming the base plus one extra word is naming the VARIANT, not the base):"
)


def _families_block(catalogue):
    """The catalogue's lines, listed once each, for the prompt.

    Conversation 1021 turn 22 is why this is here. A customer asked for "لامال لكريز" — Le Male
    Elixir, active in that store at that moment — and the extractor reported it unplaced, which
    `absence.catalogue_verdict` reads as its witness, so the customer was told by name that we do not
    carry it. Two turns later the same conversation listed it as available.

    Nothing was wrong with the model's reasoning given what it was shown. `Le Male`, `Le Male Elixir`
    and `Ultra Male` were three unrelated strings in a flat list, so a name whose head is a line root
    and whose tail is a flanker word ("لامال" + "لكريز") had no reading except "one perfume I cannot
    find". The grouping that makes it readable already existed in `naming.line_mates` — its only
    caller used it to warn the *customer*, one layer after resolution had already failed.

    Emitted as a block of its own AFTER the catalogue listing rather than annotated onto each `- name`
    line: rules 1 and 4 tell the model to return the exact name from that list, so the line has to
    stay the name and nothing else.

    Each family is printed once. `naming.families` is keyed per name, so a three-perfume line appears
    under all three of its members; grouping is rooted and therefore symmetric within a brand, so
    those three entries collapse to one set. Where two roots genuinely overlap the sets differ and
    both are printed, which is two honest hints rather than a lost one.

    Empty string when no line in the catalogue has a flanker, which keeps the prompt byte-identical
    to before for such a store.
    """
    grouped = naming.families([(name, brand_id) for name, _, brand_id in catalogue])
    if not grouped:
        return ""

    lines, seen = [], set()
    for name, mates in sorted(grouped.items()):
        family = tuple(sorted([name, *mates]))
        if family in seen:
            continue
        seen.add(family)
        lines.append("- " + " / ".join(family))
    return "\n\n" + _FAMILIES_HEADER + "\n" + "\n".join(lines)


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
    products = Product.objects.filter(is_active=True).select_related("brand")
    if store:
        products = products.filter(store=store)

    # The brand goes in beside the name, and it is load-bearing rather than decorative. This
    # catalogue lists Versace Eros under the bare name "Eros", so a customer writing
    # "ڤيرزاتشي ايروس" — the brand and the perfume, the way people actually name a perfume — left
    # the model matching two words against one, with nothing in the prompt saying the two belong
    # together. Conversation 772 turn 2 is that turn: Eros is active at 666 and 1019 and the
    # extractor placed nothing.
    #
    # 🔴 This is a false-*miss* fix, and under the current policy a false miss on a stocked perfume
    # is no longer a harmless stall. `absence.catalogue_verdict` reads the resolver's unplaced report
    # as its witness, so an Arabic name the extractor fails to place is denied by name on that same
    # turn — which for 772 turn 2 would mean telling a customer we do not sell a perfume sitting in
    # stock. Anything that improves placement is a safety fix now, not a quality one.
    #
    # The name is printed first and alone-able: rules 1 and 4 ask for the exact name from this list,
    # and the bracket is labelled so it reads as an annotation. An echo that includes the brand
    # anyway still lands — `naming.match_product` places "Eros Versace" on "Eros" by token subset.
    catalogue = list(products.values_list("name", "brand__name", "brand_id"))
    product_names = "\n".join(
        f"- {name}" + (f"  [brand: {brand}]" if brand else "")
        for name, brand, _ in catalogue
    )
    families_block = _families_block(catalogue)

    from .sales import described as sales_described

    offered_block = sales_described.offered_context_block(conversation, store)

    prompt = f"""
Extract the exact perfume names the user is inquiring about.
Look at the conversation history if the user is using pronouns or referring to something previously mentioned (like "بكام ده" or "عامل كام" or "الاتنين").

Available Perfumes in Database (the name is what you return; "[brand: …]" is an annotation, never part of the name):
{product_names}{families_block}
{offered_block}
Rules:
1. Translate Arabic names to English and fix spelling mistakes to match the exact names in the database. A customer usually names the house and the perfume together ("ڤيرزاتشي ايروس", "ديور سوفاج") while this list may hold the perfume alone ("Eros", "Sauvage") — match the brand against the "[brand: …]" annotation and return the name.
2. Be HIGHLY tolerant of phonetic Arabic transliterations and typos (e.g., 'فريساتشي يورس' or 'ايروس' -> 'Versace Eros', 'ديور سيفاج' -> 'Dior Sauvage', 'امبيرو' -> 'Ambero', 'جوب' -> 'Joop!', 'هوجو' -> 'Hugo Boss', 'كارولينا' -> 'Carolina Herrera'). Match the brand name in the "[brand: …]" annotation — "جوب" is Joop!, so a customer saying "برفيوم جوب" is asking about Joop! products.
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
   • 🔴 ❌ Never put **اسم بيت عطور موجود عندنا** here — a house that we carry is not a perfume. Scan the "[brand: …]" annotations above: if the brand appears there ("ديور", "شانيل", "فيرزاتشي", "Tom Ford", "عندكو حاجة من رصاصي"), reporting it as unplaced is how a customer asking "عندكو ديور ؟" gets told we do not sell Dior with three Diors on the shelf. If the customer named only a house WE CARRY, both lists are empty and the reply asks which perfume they meant. (Python catches this for a Latin spelling and **cannot** catch it for an Arabic one — the catalogue holds Latin names only — so on "ديور" this rule is the only guard there is.)
   • ⚠️ BUT if the brand does NOT appear anywhere in the "[brand: …]" annotations — i.e. we carry NOTHING from that house — then the customer is asking about a brand we genuinely do not stock. In that case, put the name in "unplaced" so the system can tell them honestly. Example: a customer asks "في BMW" or "في جوب" and no product above has [brand: BMW] or [brand: Joop!] → unplaced gets the name.
   • A customer asked "عايز اعرف اسعار بلو دي شانيل وسوفاج والكساندريا 2": two of those are in the list and one is not, so perfumes gets the two and unplaced gets ["الكساندريا 2"]. Leaving it out of both is how that customer got asked to wait three times for an answer nobody was looking up.
10. 🔴 CRITICAL — BRAND-ONLY QUERIES. When the customer names only a brand (not a specific perfume) and that brand IS in the catalogue (appears in "[brand: …]"), return ALL perfumes from that brand in "perfumes". Example: "في من جان بول" → return all products with [brand: Jean Paul Gaultier]. "في من جوب" → if Joop! appears as a brand, return all Joop! products.
11. 🔴 CRITICAL — LINES AND FLANKERS. Read the "Perfume lines" block above. When the customer's words are a line's base PLUS an extra word, they are naming the VARIANT on that line, not an unknown perfume — return that exact name. "لامال لكريز" is Le Male + Elixir → "Le Male Elixir". "سترونجر انتنسلي" is Stronger With You + Intensely → "Stronger With You Intensely". "دو جوي انتنس" is Joy + Intense → "Joy Intense".
   • If the extra word does not pin down one variant, return the line's base and let the reply ask which one they meant.
   • ❌ NEVER report such a name as "unplaced". A name built out of a line we stock is a name we stock. Reporting "لامال لكريز" as unplaced told a customer we do not sell Le Male Elixir while it was in stock, and the same conversation listed it as available two turns later.
"""
    prompt += """
Output format MUST be valid JSON:
{"perfumes": ["Exact Name 1", "Exact Name 2"], "unplaced": ["اسم العطر بكلام العميل"]}
(Both lists may be empty. "perfumes" holds names FROM the list above; "unplaced" holds names that are NOT in it.)
"""
    failed = False
    try:
        messages = [{"role": "system", "content": prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        response = chat(messages, profile="resolve", response_format={"type": "json_object"})

        data = json.loads(response)
        if not isinstance(data, dict):
            raise ValueError(f"extractor returned {type(data).__name__}, expected an object")
        p_names = data.get("perfumes", [])
        raw_unplaced = data.get("unplaced", [])
        if not isinstance(p_names, list) or not isinstance(raw_unplaced, list):
            raise ValueError("extractor returned a non-list for perfumes or unplaced")
    except Exception:
        # Report the failure rather than presenting it as an empty answer. A caller that is
        # about to tell the customer we do not carry a perfume needs to know the difference:
        # see `Resolution.failed`.
        logger.exception("Perfume extraction failed for message: %r", (message or "")[:200])
        p_names = []
        raw_unplaced = []
        failed = True

    unplaced = _unplaced_names(raw_unplaced, message, store, products)

    if not p_names:
        _log_outcome(message, (), unplaced, failed)
        return Resolution([], unplaced, failed=failed)

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

    _log_outcome(message, resolved, unplaced, failed)
    return Resolution(resolved, unplaced, failed=failed)


def _log_outcome(message, resolved, unplaced, failed):
    """One line per extraction: what went in, what was placed, what was reported unplaced.

    Diagnosing conversation 1021 meant dumping the conversation out of production, because nothing
    recorded why a name had been denied — this module logged only inside its `except`, and an unplaced
    report is the sole witness `absence.catalogue_verdict` has for an Arabic name. A denial with no
    trace of the decision behind it is not something we should have to reconstruct twice.

    At INFO, and the message is truncated: this runs on every turn.
    """
    logger.info(
        "resolver: message=%r placed=%s unplaced=%s failed=%s",
        (message or "")[:120],
        [product.name for product in resolved],
        list(unplaced),
        failed,
    )


def resolve_product(message: str, history=None, store=None, conversation=None):
    """
    Try to resolve a single product. Returns the first matched product or None.
    """
    resolved = resolve_products(message, history, store, conversation)
    return resolved[0] if resolved else None


class _CarriedHouse:
    """The span names a house we stock, so it is not deniable — but it is not one perfume either."""

    def __repr__(self):
        return "CARRIED_HOUSE"


CARRIED_HOUSE = _CarriedHouse()


def confirm_unplaced(name, store=None):
    """Ask a second time before we deny an Arabic name by name. The confirming witness.

    `absence.catalogue_verdict` has exactly one witness for a name written in Arabic: the extractor's
    own `unplaced` report (`absence.py:140-144`). It cannot have another. `Product.name` and
    `Brand.name` hold Latin spellings only — there is no alias column, no Arabic-name column, no
    transliteration table — so `naming.candidates` returns `([], [])` for every Arabic string whether
    we stock the perfume or not, and all three guards in `_unplaced_names` above are no-ops for an
    Arabic compound. One LLM call therefore decides, alone, whether a customer is told by name that we
    do not sell something. Conversation 1021 turn 22 is what that costs: "لامال لكريز" was reported
    unplaced and denied while Le Male Elixir sat active in that store.

    So this is a second, narrower call on the denial path only. Two reasons it is worth the latency:

      * It is a far easier question than the one that failed. `resolve_products` reads a whole
        conversation, resolves pronouns, handles several names at once and obeys eleven rules; this
        asks one thing about one span, and gets the line structure handed to it.
      * The cost is asymmetric in the same direction as everything else here. A wasted call on a
        genuinely absent perfume costs a second; a false denial costs the customer.

    Called only when we are about to deny, which is rare, so this does not touch the ordinary turn.

    **Three outcomes, and each one is the safe reading of its answer:**

      * a `Product` — the span is ours after all. The caller places it and the customer gets the
        perfume and its price instead of a denial.
      * `CARRIED_HOUSE` — the span is a house we stock, named on its own. Not one perfume, so there is
        nothing to place, but emphatically not deniable either: this is how "عندكو ديور ؟" would
        otherwise get told we do not sell Dior with three Diors on the shelf. The caller must read it
        as UNKNOWN and ask which perfume they meant.
      * `None` — genuinely not ours. The caller denies, exactly as today.

    **Raises** on a transport or JSON failure rather than returning `None`, because those two must not
    be confused: `None` means "we checked and it is absent", and an API blip is not evidence of
    absence. The caller is required to catch it and fall back to UNKNOWN — the same principle as the
    `resolution.failed` rung at `absence.py:106`.
    """
    products = Product.objects.filter(is_active=True).select_related("brand")
    if store:
        products = products.filter(store=store)

    catalogue = list(products.values_list("name", "brand__name", "brand_id"))
    if not catalogue:
        return None

    listing = "\n".join(
        f"- {p_name}" + (f"  [brand: {brand}]" if brand else "")
        for p_name, brand, _ in catalogue
    )
    prompt = f"""A customer asked about a perfume and our extractor could not find it in our catalogue.
We are about to tell them, by name, that we do not sell it. Check once more before we do.

Our full catalogue:
{listing}{_families_block(catalogue)}

Could the customer's words be one of the names above? Look for:
• An Arabic phonetic spelling of a Latin name — "لامال" is Le Male, "سوفاج" is Sauvage, "زار كولد" is ZARA GOLD, "امبيرو" is Ambero.
• The house and the perfume said together, where we list the perfume alone — "ڤيرزاتشي ايروس" is "Eros", "ديور سوفاج" is "Sauvage".
• A line's base plus a flanker word, per the "Perfume lines" block — "لامال لكريز" is Le Male + Elixir, so the answer is "Le Male Elixir".
• Ordinary typos and letter swaps (ڤ/ف, ب/پ, ج/چ), and letters the customer spelled out one by one.

Answer with ONE of:
{{"name": "<the exact name from the list>"}}   — you recognise it
{{"house": true}}                              — these words are a HOUSE we carry (it appears in a "[brand: …]" above), named on its own rather than one perfume
{{"name": "NONE"}}                             — the customer really is asking about a perfume we do not carry

Answer "NONE" only if you are confident, and prefer a name over "NONE" when the words plausibly fit one. Telling a customer we do not stock a perfume that is on our shelf is the worst outcome this check exists to prevent. But do NOT invent a match: an unrelated perfume returned here is just as wrong in the other direction.

Output MUST be valid JSON."""

    response = chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": name or ""}],
        profile="resolve",
        response_format={"type": "json_object"},
    )
    data = json.loads(response)
    if not isinstance(data, dict):
        raise ValueError(f"confirmer returned {type(data).__name__}, expected an object")

    if data.get("house") is True:
        logger.info("confirm_unplaced: %r -> CARRIED_HOUSE", (name or "")[:120])
        return CARRIED_HOUSE

    answer = (data.get("name") or "").strip()
    if not answer or answer.upper() == "NONE":
        logger.info("confirm_unplaced: %r -> NONE (denial stands)", (name or "")[:120])
        return None

    # Resolved the same two ways `resolve_products` resolves its own output, so a name echoed with
    # the brand attached ("Eros Versace") still lands and an invented one still comes back empty.
    match = (
        products.filter(name__iexact=answer).first()
        or naming.match_product(answer, store, products=products)
    )
    logger.info(
        "confirm_unplaced: %r -> %r (%s)",
        (name or "")[:120], answer, "placed" if match else "unmatchable, denial stands",
    )
    return match