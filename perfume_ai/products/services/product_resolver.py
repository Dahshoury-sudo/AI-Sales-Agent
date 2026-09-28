import json
import logging
import re

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

    `ambiguous` is the fourth, and it is the placement channel's version of `unplaced`: a span the
    model placed on a perfume the customer's own letters do not support, which we withheld rather
    than price. Read as `getattr(result, "ambiguous", ())`. It is deliberately NOT merged into
    `unplaced` — that channel is the witness `absence.catalogue_verdict` denies on, so putting an
    ambiguous span there would turn "we are not sure which perfume you mean" into "we do not sell
    it", which is the worse error in the other direction. See `_verify_placement`.
    """

    def __init__(self, products=(), unplaced=(), failed=False, ambiguous=()):
        super().__init__(products)
        self.unplaced = tuple(unplaced)
        self.failed = bool(failed)
        self.ambiguous = tuple(ambiguous)


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


# The placement channel's guard, and the four numbers that arm it. Read `_verify_placement` for
# what they mean; they are here, and not in `naming`, because that module is scoped to matching
# primitives with no opinion about what to say to anyone (`absence.py:19-21`).
#
# 🔴 Provisional, and fitted to 265 single-product Arabic placements from THREE stores (40, 45 and
# 188 rows). Re-measure with `eval_harness.backtest_placement`, which prints the frontier each
# threshold is sitting on, before trusting them against a different catalogue or resolver model.
_MIN_COVERAGE = 0.70  # share of the customer's letters the top row has to explain
_GAP = 0.10           # how far the best row must separate from the runner-up
_PICK_RATIO = 0.75    # the model's pick must score at most this much of the best row
_MIN_CHARS = 5        # characters that actually lined up, via SequenceMatcher.get_matching_blocks

_HAS_LATIN = re.compile(r"[A-Za-z]")


def _disagreement(text, pick, store, products):
    """Do the customer's own letters point somewhere other than the perfume we placed?

    Returns `(span, ranking, scores)` when they clearly do, or `None`. `ranking` is
    `naming.phonetic_ranking`'s full ordered list, handed back so the caller can build a shortlist
    without scoring the catalogue a second time; `scores` is
    `(best_score, second_score, pick_score, matched_chars, coverage)`, carried so the caller can log
    why.

    The rule, and each clause is doing separate work:

        coverage >= _MIN_COVERAGE             the top row explains nearly all of what was typed
        best_score >= second + _GAP           and it separates from the field
        pick_score <= _PICK_RATIO * best       and it is not the one the model chose
        matched_chars >= _MIN_CHARS            on enough real characters to mean anything

    **Coverage is `matched_chars / len(transliteration)` — the clause that actually decides**, and it
    is the one to keep if this ever has to be cut down to a single test. Measured over 265 real
    placements (`eval_harness.backtest_placement`), of which 8 are the known defect:

    | | coverage |
    |---|---|
    | the 8 true positives (`الترامل`, `الترا ميل`, `التراميل` → Terre d'Hermes) | 0.833 – 1.000 |
    | the highest of the **257 placements this leaves alone** (46 reach the clause) | **0.588** |

    0.70 sits in the middle of that gap. What it means in words: a perfume name misspelled
    phonetically is still almost entirely *made of* that perfume's sounds, while a sentence that
    merely happens to score well against some row explains only half its own letters. That is why it
    rejects the whole class the ratio could not — "لا انا بسأل بس اسعارو اي" ("no, I'm just asking
    what its prices are") scores 0.500 against *Lattafa Asad* with a **0.159** separation, above any
    gap threshold that still catches the bug, and covers 0.462.

    🔴 Two statistics were measured and rejected before this one. Do not reintroduce either.

      * **An absolute floor** (`best_score >= 0.60`) was backtested clean and then disproved.
        `SequenceMatcher.ratio()` is `2M/(len(a)+len(b))`, so "some row scores above 0.60" is a
        lottery whose odds grow with the catalogue: bootstrapped over 345 real spans, 4.9% at 35
        rows, 23.9% at 188, about 45% at 400. Tuned at 188 it would have been reported clean by an
        eval harness running against 40 rows — it could not have falsified it — and would then have
        fired on half the turns of a larger tenant. It also cannot reach short names at all: the
        *correct* row scores `Si` 0.500, `Eros` 0.444, `Pi` 0.154, `212` 0.083.
      * **The separation gap alone.** It survives the catalogue-size argument — more rows raise the
        runner-up, so a bigger catalogue makes this *more* cautious — but it does not separate the
        data. The tightest true positive sits at **0.121** and the false positive above at **0.159**,
        i.e. the false positive is *better* separated than the bug. There is no threshold. The gap is
        kept as a cheap structural filter at 0.10, below every true positive, and coverage is what
        carries the decision.

    `matched_chars >= 5` disqualifies junk that scores well on almost nothing: "راجل" ("a man" — a
    gender statement) hits 0.667 against ZARA GOLD on M=4, and "سي" matches `Si` on M=1.

    Three scope restrictions, because the backtest population was narrower than a bare rule would
    be. The caller applies the first; the other two are here:

      * exactly one product placed — a multi-name message concatenates into a span that matches
        nothing in particular, so this is blind there by accident rather than by design;
      * no Latin characters in the span — "عايز حاجه شبه Baccarat Rouge بس مش هي" asks for
        something *like* a perfume it names outright, and scores 0.703 against it while the correct
        answer scores low. Refusing mixed spans is what keeps that from flagging;
      * at least two rows to compare, since a separation from nothing is not a separation.

    ⚠️ **Recall is not claimed, and the misses are not hypothetical.** This is a high-precision net:
    8 of 8 known defects, 0 of 257 other placements. Two of those 257 are the same bug getting
    through, both in conversation 726, both answered *Afnan 9PM*:

        انااا بتكلم دلوقتي سعر سترينجر وذ يو انتنسلي عامل كام   cov 0.412   ← asks its price outright
        انااا عله سترينجر وذ يو انتنسلي                          cov 0.538

    Neither is ambiguous to a reader, and the top row is *Stronger With You Intensely* in both. They
    sit low only because the filler around the name is not stripped — `انااا`, `بتكلم`, `دلوقتي`,
    `عله` are not in `naming._REFERENTIAL`, so they stay in the span and dilute the ratio. **That is
    where the recall is, and it is a vocabulary fix, not a threshold fix.** Lowering `_MIN_COVERAGE`
    to 0.40 to reach them would drag in every frontier row in the backtest; adding those words to
    `_REFERENTIAL` would raise their coverage to roughly the 0.778 the clean spellings of the same
    name already score (conversations 723 and 727, correctly placed). Deferred because
    `_REFERENTIAL` also feeds `get_product_info`'s resolver gate and needs its own regression floor.
    """
    span = naming.arabic_span(text)
    if not span or _HAS_LATIN.search(span):
        return None

    latin = naming.transliterate(span)
    ranking = naming.phonetic_ranking(text, store, products=products)
    if not latin or len(ranking) < 2:
        return None

    best_score, matched_chars, best = ranking[0]
    second_score = ranking[1][0]
    coverage = matched_chars / len(latin)
    if best.pk == pick.pk:
        return None
    if matched_chars < _MIN_CHARS:
        return None
    if coverage < _MIN_COVERAGE:
        return None
    if best_score < second_score + _GAP:
        return None

    pick_score = next((score for score, _, row in ranking if row.pk == pick.pk), 0.0)
    if pick_score > _PICK_RATIO * best_score:
        return None

    return span, ranking, (best_score, second_score, pick_score, matched_chars, coverage)


def _verify_placement(message, resolved, store, products):
    """Second-guess a placement the customer's letters do not support. `(resolved, ambiguous)`.

    The symmetric half of `_unplaced_names` above, and it exists because the two channels were not
    defended alike. A denial passes three Python guards *and* a whole second model call
    (`confirm_unplaced`); a placement was checked against the catalogue and nothing else — the loop
    below asks `products.filter(name__iexact=…)` whether the row is real, never whether it is what
    the customer typed. Conversation 1041 is two messages long: "في التراميل ؟" — الترا + ميل,
    Ultra Male, active in that store — answered "Terre d'Hermes متوفر عندنا" with its real prices
    and no hedge. Terre d'Hermès has no L sound in it anywhere. The same pair of perfumes did this
    on 8 turns across 5 conversations before anyone reported it.

    Nothing in Python can *place* an Arabic name — that asymmetry is real and is why
    `naming.phonetic_ranking` must not be used to pick a row. But Python can notice a disagreement,
    and a disagreement is enough to stop and ask. On one:

      * **one narrow model call**, `confirm_placement`, showing the customer's exact span and a
        shortlist of the pick plus the phonetic runners-up. It may answer only from that shortlist
        or NONE, which is what makes it safe by construction — the lesson from `confirm_unplaced`,
        whose free-form answer needed a relatedness check bolted on afterwards.
      * **names one** → place it; the customer gets the right perfume and its price.
      * **NONE, or a failure** → withhold the placement and report the span as `ambiguous`. The
        existing plumbing turns that into the retype request it already has wording for: with
        nothing placed, `product_info` reads `named_but_unresolved`, the verdict falls to UNKNOWN,
        and `_UNREADABLE_NAME_RULES` asks which perfume they meant without denying anything.

    🔴 Withholding on a *failure* is the opposite reading from `confirm_unplaced`, which raises so a
    blip cannot manufacture a denial. Both point the same way once you ask what the blip would
    produce: there, silence must not become "we do not sell it"; here, it must not become a
    confidently wrong price. Withholding costs the customer one extra round-trip. Keeping the
    placement costs them the bug.

    ❌ The phonetic winner is never substituted for the model's pick. That is the "اوداورا" →
    *Dark Aura* behaviour removed from the loop below, and it is worse than either honest outcome.
    """
    if len(resolved) != 1:
        return resolved, ()

    pick = resolved[0]
    disagreement = _disagreement(message, pick, store, products)
    if not disagreement:
        return resolved, ()

    span, ranking, scores = disagreement
    best = ranking[0][2]
    best_score, second_score, pick_score, matched_chars, coverage = scores
    logger.info(
        "placement: span=%r translit=%r pick=%r (%.3f) vs best=%r (%.3f) second=%.3f "
        "chars=%d cov=%.3f",
        span, naming.transliterate(span), pick.name, pick_score,
        best.name, best_score, second_score, matched_chars, coverage,
    )

    # The pick and the top row, plus the next two the letters could plausibly be, so the confirmer
    # is not forced to choose between exactly two when the customer meant a third. Capped there
    # because a longer list is a worse question, not a better one.
    shortlist = [pick, best]
    for _, _, row in ranking[1:3]:
        if all(row.pk != other.pk for other in shortlist):
            shortlist.append(row)

    try:
        answer = confirm_placement(span, shortlist, store)
    except Exception:
        logger.exception("placement: confirming %r failed; withholding rather than pricing", span)
        return [], (span,)

    if answer is None:
        logger.info("placement: %r not confirmed; withholding and asking", span)
        return [], (span,)

    logger.info("placement: %r corrected from %r to %r", span, pick.name, answer.name)
    return [answer], ()


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


def resolve_products(message: str, history=None, store=None, conversation=None, verify=True):
    """
    Try to resolve multiple products from the user's message using AI extraction.

    `conversation` is optional and used only to anchor pronoun resolution: it supplies the
    perfumes we most recently offered, derived from `Message.internal_context`. Without it the
    only reference guidance is one sentence below plus a rule scoped to short confirmations, and
    a doubt utterance matches neither — "مش متوفر متأكد ؟" about Versace Eros resolved to two
    perfumes from two turns earlier (conversation 1099) because nothing pointed at the newest.

    `verify=False` switches off `_verify_placement` — the guard that second-guesses a single
    placement the customer's Arabic does not support. Off for callers whose `message` is not the
    customer's own words (`resolve_product`) or where a wrong row is a soft failure not worth a
    confirming call (`objection_service`); each such call site carries the reason. It is a keyword
    with a safe default so a new caller is guarded without having to know this exists.

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

    # The placement channel's only check on the customer's own message. Everything above this line
    # validated the model's answer against the CATALOGUE — `message` is not read once between the
    # start of the loop and here — which is why conversation 1041 priced Terre d'Hermes for a
    # customer who typed "في التراميل ؟" (Ultra Male, stocked) and was right about every row it
    # touched. See `_verify_placement`.
    ambiguous = ()
    if verify:
        resolved, ambiguous = _verify_placement(message, resolved, store, products)

    _log_outcome(message, resolved, unplaced, failed, ambiguous)
    return Resolution(resolved, unplaced, failed=failed, ambiguous=ambiguous)


def _log_outcome(message, resolved, unplaced, failed, ambiguous=()):
    """One line per extraction: what went in, what was placed, what was reported unplaced.

    Diagnosing conversation 1021 meant dumping the conversation out of production, because nothing
    recorded why a name had been denied — this module logged only inside its `except`, and an unplaced
    report is the sole witness `absence.catalogue_verdict` has for an Arabic name. A denial with no
    trace of the decision behind it is not something we should have to reconstruct twice.

    `ambiguous` says whether a placement was withheld on this turn. The scores behind that decision
    — span, transliteration, the pick and the top row with their ratios, the runner-up, and the
    matched-character count — are logged by `_verify_placement` itself, on the same turn and through
    the same logger, because that is where they exist and threading six floats back up through a
    function that runs on every single turn buys nothing. Grep `placement:` for the arithmetic and
    `resolver:` for the outcome; a withheld turn has both.

    At INFO, and the message is truncated: this runs on every turn.
    """
    logger.info(
        "resolver: message=%r placed=%s unplaced=%s ambiguous=%s failed=%s",
        (message or "")[:120],
        [product.name for product in resolved],
        list(unplaced),
        list(ambiguous),
        failed,
    )


def resolve_product(message: str, history=None, store=None, conversation=None):
    """
    Try to resolve a single product. Returns the first matched product or None.
    """
    # `verify=False`, and for two independent reasons. Its one caller (`order_service.py:605`) passes
    # a name the *order* extractor produced, not the customer's own words, so the guard's premise —
    # "compare what we placed against what the customer typed" — is simply false here. And a withheld
    # placement would come back as `None` from the `resolved[0] if resolved else None` below, which
    # `handle_order` reads as "no such perfume" and silently drops the order line. A soft wrong row
    # is recoverable; a vanished line item is not.
    resolved = resolve_products(message, history, store, conversation, verify=False)
    return resolved[0] if resolved else None


class _CarriedHouse:
    """The span names a house we stock, so it is not deniable — but it is not one perfume either."""

    def __repr__(self):
        return "CARRIED_HOUSE"


CARRIED_HOUSE = _CarriedHouse()


class _Unverified:
    """The rescue named a perfume the customer's own letters do not support. Neither outcome holds."""

    def __repr__(self):
        return "UNVERIFIED"


UNVERIFIED = _Unverified()


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

    **Four outcomes, and each one is the safe reading of its answer:**

      * a `Product` — the span is ours after all. The caller places it and the customer gets the
        perfume and its price instead of a denial.
      * `CARRIED_HOUSE` — the span is a house we stock, named on its own. Not one perfume, so there is
        nothing to place, but emphatically not deniable either: this is how "عندكو ديور ؟" would
        otherwise get told we do not sell Dior with three Diors on the shelf. The caller must read it
        as UNKNOWN and ask which perfume they meant.
      * `UNVERIFIED` — it named a row, and `_disagreement` says the customer's own letters point
        somewhere else entirely. Neither reading survives: we have no rescue to offer and no evidence
        of absence either. 🔴 This is why it is a third value and **not `None`** — `None` means "we
        checked and it is absent", so `_confirm_before_denying` (`product_info.py:361-363`) logs
        *"confirmed absent; denial stands"* and denies by name. The caller must read this as UNKNOWN.
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

    # And then checked against the customer's own letters, with the same predicate that guards the
    # placement channel. This rescue was the *other* half of conversation 1041's defect: a call whose
    # prompt above says "prefer a name over 'NONE' when the words plausibly fit one", on the highest-
    # stakes path in the file, accepting whatever came back because the row existed in the catalogue.
    # That is the check the placement loop was missing, one channel over — and here it costs nothing,
    # because the ranking is arithmetic over rows already in memory rather than another call.
    if match is not None and _disagreement(name, match, store, products):
        logger.info(
            "confirm_unplaced: %r -> %r rejected, the letters point elsewhere (UNVERIFIED)",
            (name or "")[:120], match.name,
        )
        return UNVERIFIED

    logger.info(
        "confirm_unplaced: %r -> %r (%s)",
        (name or "")[:120], answer, "placed" if match else "unmatchable, denial stands",
    )
    return match


def confirm_placement(span, shortlist, store=None):
    """Which of these perfumes did the customer mean? A `Product` from the shortlist, or `None`.

    The other side of `confirm_unplaced`, on the other channel, and deliberately the narrower call of
    the two. `confirm_unplaced` has to be given the whole catalogue, because its question is "is this
    anywhere in here". This one already knows the answer is one of two or three rows — `_disagreement`
    established that the customer's letters separate one row from the field, and the model's own pick
    is the other — so the question is a choice between them.

    🔴 **It may answer only from the shortlist, or NONE, and an answer outside it is read as NONE.**
    That constraint is what makes this call safe by construction, and it is the lesson from
    `confirm_unplaced` above: a confirmer allowed to answer freely returned rows that had nothing to
    do with what the customer typed, and needed a relatedness check bolted on after the fact. A
    confirmer choosing from a vetted shortlist cannot do that at all.

    The span is passed in the customer's own script, untranslated, for the same reason rule 9 of the
    extractor prompt demands it: we are asking what these letters say, so correcting them first would
    be asking about our own guess.

    **Raises** on a transport or JSON failure. The caller withholds on a raise — see
    `_verify_placement`, which records why that is the opposite reading from this module's other
    confirmer and yet the same principle.
    """
    if not span or not shortlist:
        return None

    listing = "\n".join(f"- {product.name}" for product in shortlist)
    prompt = f"""A customer wrote a perfume name in Arabic letters. Our extractor read it as one of the perfumes below, but the letters the customer actually typed look closer to a different one. We need to know which they meant before we quote a price, because quoting the wrong perfume's price by name is worse than asking.

The perfumes it could be:
{listing}

Read the customer's letters phonetically, as an Egyptian customer would type a French or English name in Arabic script. Sound them out:
• "الترا ميل" / "الترامل" / "التراميل" are all Ultra Male — الترا is "ultra", ميل is "male", and Egyptians write them joined as often as spaced.
• "لامال" is Le Male. "تير دي هيرميس" is Terre d'Hermes. "سوفاج" is Sauvage.
• Letters get dropped and doubled in the middle of a name; the first and last sounds are the reliable ones.

Answer with ONE of:
{{"name": "<the exact name from the list above>"}}   — the letters clearly say this one
{{"name": "NONE"}}                                   — you cannot tell which of them it is

Choose a name only when the letters actually support it. ❌ Do NOT pick one because it is more popular, or because it appears first, or to avoid answering NONE. "NONE" is a good answer here: it makes us ask the customer to write the name again, which costs them one message. Naming the wrong perfume costs them the price of the wrong perfume. ❌ Do NOT answer with any name that is not in the list above.

Output MUST be valid JSON."""

    response = chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": span}],
        profile="resolve",
        response_format={"type": "json_object"},
    )
    data = json.loads(response)
    if not isinstance(data, dict):
        raise ValueError(f"placement confirmer returned {type(data).__name__}, expected an object")

    answer = (data.get("name") or "").strip()
    if not answer or answer.upper() == "NONE":
        return None

    # Only from the shortlist. An answer off it is read as NONE rather than resolved against the
    # catalogue: the whole safety property of this call is that its output space is the rows we
    # vetted, and a free resolution here would hand back the loophole the constraint just closed.
    normalized = answer.casefold().strip()
    for product in shortlist:
        if product.name.casefold().strip() == normalized:
            return product

    logger.info(
        "confirm_placement: %r answered %r, which is not on the shortlist %s; reading as NONE",
        span[:120], answer, [product.name for product in shortlist],
    )
    return None