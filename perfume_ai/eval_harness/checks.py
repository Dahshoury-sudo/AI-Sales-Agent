# -*- coding: utf-8 -*-
"""Deterministic, non-LLM checks on a generated reply.

These exist because factual correctness must be *proven*, not judged. An LLM judge
asked "did it invent a price?" will sometimes say no when it did. Cross-referencing
every number in the reply against the store's own rows cannot.

Everything here is read-only against the database.
"""

import re
from collections import namedtuple

# Latin tokens that appear in replies without naming a product.
_LATIN_ALLOWLIST = {
    "ml", "egp", "eau", "de", "parfum", "edp", "edt", "ok", "instapay", "www",
    "com", "perfamix", "https", "http", "review", "sale", "box", "the", "and",
    "for", "you", "vip", "dm", "qp", "no", "yes", "pm", "am",
}

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'&.-]{1,}")

# Claims of certainty the product data cannot support.
_GUARANTEE = (
    re.compile(r"مضمون"),
    re.compile(r"[أا]ضمن"),
    re.compile(r"\b(?:100|١٠٠)\s*%"),
    re.compile(r"نسبه?\s*\d{1,3}\s*%"),
    re.compile(r"بنسبه?\s+\d{1,3}"),
    re.compile(r"[هح]تعجب\S*\s+[أا]كيد"),
    re.compile(r"[أا]كيد\s+[هح]تعجب"),
)

# Manufactured scarcity / urgency.
_URGENCY = (
    re.compile(r"الكمي[هة]\s+(?:بت)?خلص"),
    re.compile(r"[آأا]خر\s+فرص"),
    re.compile(r"لفتر[هة]\s+محدود"),
    re.compile(r"عرض\s+ينتهي"),
    re.compile(r"بسرع[هة]\s+قبل"),
)

# Closing questions — asking for the order. Broader than reply_sanitizer's patterns
# on purpose: the point is to measure what leaks PAST the sanitizer.
#
# `تحب أجهزلك` was missing, which is why R3's "تحبي أجهزلك واحدة؟" showed no finding here while
# rescore.py would have flagged it high. The size-choice pattern moved to _NARROWING, since a
# size question is earned one stage earlier than an order ask.
_CLOSING = (
    re.compile(r"تحب[يى]?\s+[أا]?ساعدك\s+في\s+(?:ال)?(?:طلب|[أا]وردر)"),
    re.compile(r"تحب[يى]?\s+[نت]طلب"),
    re.compile(r"تحب[يى]?\s+[أا]?جهز\s*ل?ك"),
    re.compile(r"ن(?:سجل|كمل)\s+(?:ال)?(?:طلب|[أا]وردر)"),
    re.compile(r"[أا]سجلك?\s*(?:ال)?طلب"),
    re.compile(r"تحب[يى]?\s+نكمل"),
    re.compile(r"عايز\s+تطلب"),
    re.compile(r"نبعتلك\S*\s+الطلب"),
)

# A size choice. Narrowing rather than closing, so it is premature only before a
# recommendation has been made.
_NARROWING = (re.compile(r"[أا]جيبلك\s+(?:الـ?\s*)?\d+"),)

# Not a close, even though `عايز تطلب` matches above: asking WHICH perfume is a clarifying
# question, and the agent cannot close on an order it has not identified yet. This produced
# two false `premature_close` findings on the one scenario where the agent was in fact stuck.
_NOT_A_CLOSE = (
    re.compile(r"مش\s+واضح\S*\s+عايز\s+تطلب"),
    re.compile(r"عايز\s+تطلب\s+[أا]نهي"),
)

# Empty filler questions the persona bans outright.
_FILLER = (
    re.compile(r"(?:عايز|محتاج|تحب)\s+حاج[هة]\s+تاني[هة]\s*[؟?]"),
    re.compile(r"محتاج\s+مساعد[هة]"),
    re.compile(r"تحب[يى]?\s+تعرف\s+(?:ال)?[أا]?سعار"),
    re.compile(r"عطر\s+معين\s+في\s+بالك"),
    re.compile(r"[أا]قدر\s+[أا]ساعدك\s+(?:في\s+)?[أا]?ي[هة]?\s*[؟?]"),
)

# Re-asking the budget has to be an actual QUESTION. A bare `ميزانيتك` fired on every
# statement that merely referred to it — "الـ90 أغلى من ميزانيتك", "داخل ميزانيتك",
# "أعلى شوية من ميزانيتك" — and produced four false `reasked_budget` findings in one
# six-scenario run, none of them a re-ask. These are rescore.py's stricter forms, which
# were already correct; the loose ones lived on here.
_ASK_BUDGET = (
    re.compile(r"ميزانيتك\s+(?:في\s+حدود\s+)?كام"),
    re.compile(r"حدود\s+كام"),
    re.compile(r"ميزانيتك\s+[أا]يه"),
    re.compile(r"في\s+رينج\s+[أا]يه"),
    re.compile(r"السعر\s+اللي\s+في\s+بالك"),
)

_ASK_GENDER = (
    re.compile(r"رجالي\s+ولا\s+حريمي"),
    re.compile(r"حريمي\s+ولا\s+رجالي"),
    re.compile(r"لنفسك\s+ولا\s+هدي[هة]"),
    re.compile(r"للرجال\s+ولا\s+للستات"),
)

# Availability denial — used to catch "we don't have it" while stock exists.
#
# "مش موجود" was added after conversation 726: the reply said "مش موجودين في البيانات اللي
# معايا دلوقتي" about two perfumes that were active with both bottle types in stock, and the
# four patterns above it all missed, so the suite scored the turn clean.
_DENIAL = (
    re.compile(r"مفيش\s+عندنا"),
    re.compile(r"مش\s+متوفر"),
    re.compile(r"غير\s+متوفر"),
    re.compile(r"مفيش\s+حاليا"),
    re.compile(r"مش\s+موجود"),
    re.compile(r"غير\s+موجود"),
    re.compile(r"مش\s+عندنا"),
)

# Telling the customer about "البيانات" at all is a leak, whatever it denies. The catalogue,
# the injected shortlist and the system's own plumbing are internal; a salesperson names the
# perfume or asks which one you meant, not "it is not in the data I have". prompts.py rule 3
# forbids it explicitly.
_DATA_LEAK = re.compile(r"في\s+البيانات|البيانات\s+اللي\s+معاي")

# Denials that are correct, and which the patterns above match anyway. `مش\s+متوفر` has no
# trailing boundary, so it also matches متوفرة / متوفرين / متوفر منه — and `مش\s+موجود` matches
# موجودة / موجودين the same way.
#
#   * A bottle-type-scoped denial. `product_formatting._original_bottle_status` dictates
#     "للاسف مش متوفر منه زجاجة أوريجينال حالياً" verbatim for any global-brand perfume with no
#     original variant, and prompts.py tells the model to reproduce it بالحرف — so it appears in
#     correct replies by design. Versace Eros has no original bottle, so the *fixed* 1099 reply
#     still contains this sentence.
#   * A size-scoped denial ("حجم 50 ملي غير متوفر", "الـ50 ملي مش متوفر"). Only original bottles
#     can run out, so these can be true; suppressed rather than validated, because `truth` has
#     no per-size availability and a conservative miss beats a false alarm.
_DENIAL_SCOPED = (
    re.compile(r"[أا]وريجينال"),
    re.compile(r"زجاج[ةه]\s+(?:ال)?[أا]وريجينال"),
    re.compile(r"زجاجات\s+(?:ال)?براند"),
    re.compile(r"حجم\s+\d+"),
    re.compile(r"الـ?\s*\d+\s*ملي"),
)

# "مفيش عندنا حاجة شبه X" denies a RESEMBLANCE, not a product. It says nothing about whether X
# is stocked — and it is the honest answer the similarity rules ask for when no close match
# exists. Scenario S1 produces it verbatim, then recommends two perfumes in the same breath.
_DENIAL_SIMILARITY = (
    re.compile(r"شبه"),
    re.compile(r"زي\s"),
    re.compile(r"مثل"),
    re.compile(r"بديل"),
    re.compile(r"نفس\s"),
    # "مفيش عندنا حاجة قريبة من X" — the same statement in the wording the similarity
    # instruction actually produces. Scenario S3 says it verbatim and was scored a critical
    # false_denial for it while recommending two alternatives in the same breath.
    re.compile(r"قريب"),
)

# A reply that denies one thing and offers another is ordinary, so the denied name has to sit in
# the same clause as the denial. Without this, S1's "مفيش عندنا حاجة شبه Dior Sauvage… لكن ممكن
# يعجبك Luna Rossa Carbon" was read as denying Luna Rossa Carbon — a perfume it was recommending.
_CLAUSE = re.compile(r"[.،,؛;!?؟\n]+")

# `naming.mentioned_in` reads only `.name`, so the catalogue names can be handed to it without
# dragging ORM instances into a `truth` dict that the runner shares across five threads.
_NameOnly = namedtuple("_NameOnly", "name")


def _false_denial(reply, truth):
    """An active, sellable perfume the reply says we do not have.

    The 1099 defect: "عطر Versace Eros مش متوفر عندنا حالياً" while Eros was active at 1019 EGP.

    Three kinds of correct denial are excluded — one scoped to a bottle type (dictated verbatim
    by `product_formatting._original_bottle_status`, so it appears in correct replies by design),
    one scoped to a size (only original bottles can run out, so it may well be true), and one
    denying a resemblance rather than a product.

    Name matching goes through `sales.naming.mentioned_in` rather than a substring test, so a
    reordered or slightly mistyped name still resolves — "9pm by Afnan" for "Afnan 9PM" is that
    function's own documented case, and a substring test missed it entirely. The catalogue names
    are wrapped in a name-only stand-in because `mentioned_in` reads nothing but `.name`, which
    keeps ORM instances out of a `truth` dict shared across the runner's thread pool.

    Returns the offending name, or None.
    """
    from products.services.sales import naming

    available = truth.get("available_names") or ()
    if not available:
        return None

    candidates = [_NameOnly(name) for name in available if name]

    for clause in _CLAUSE.split(reply or ""):
        if not any(pattern.search(clause) for pattern in _DENIAL):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SCOPED):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SIMILARITY):
            continue
        hits = naming.mentioned_in(clause, candidates)
        if hits:
            # Longest wins: catalogue names nest, and "Stronger With You Intensely" in the text
            # also satisfies every token of "Stronger With You".
            return max((hit.name for hit in hits), key=len)
    return None


def _contradictory_availability(reply):
    """A clause that denies a perfume and promises to check on it in the same breath.

    Conversation 795 turn 4, verbatim: "عطر الكساندريا 2 مش موجود عندنا، لحظة أتأكدلك منه". Both
    halves cannot be true — either we know it is absent or we are still finding out — and the
    customer is left unable to tell which. `_false_denial` scores this clean, because الكساندريا 2
    is in no catalogue and so never reaches `truth["available_names"]`.

    Needs no ground truth at all, which is the point: the defect is internal to the sentence.

    The deferral markers come from `described._DEFERRAL` rather than a second copy, so a new
    phrasing added for the referent logic is caught here too. The clause split is the same one
    `_false_denial` uses — but note that a comma *inside* the offending sentence would separate
    the two halves, so a denial clause is also paired with the clause that follows it. That is
    the shape turn 4 actually has.

    Returns the offending text, or None.
    """
    from products.services.sales.described import _DEFERRAL
    from products.services.static_faq_service import normalize_arabic

    clauses = [clause for clause in _CLAUSE.split(reply or "") if clause.strip()]
    markers = [normalize_arabic(marker) for marker in _DEFERRAL]

    for index, clause in enumerate(clauses):
        if not any(pattern.search(clause) for pattern in _DENIAL):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SCOPED):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SIMILARITY):
            continue
        # This clause and the next one: "مش موجود عندنا، لحظة أتأكدلك منه" is one sentence to a
        # reader and two clauses to the splitter.
        window = " ".join(clauses[index:index + 2])
        normalized = normalize_arabic(window)
        if any(marker in normalized for marker in markers):
            return window.strip()
    return None


# The context blocks that say, in so many words, that the system has no data on what was asked.
# `product_info` emits the first when the resolver could not place a name and the second on its
# not-found branch. A denial written against either is a denial the agent had no basis for.
_NO_DATA_CONTEXT = (
    "═══ سؤال معلّق ═══",
    "لم يتم التعرف على اسم منتج محدد",
)


def _unbacked_denial(reply, context):
    """A denial made on a turn where the injected data said nothing either way.

    `prompts.py` rule 3 is unconditional: the agent may never tell a customer a perfume is
    absent on the strength of the catalogue not containing it. Missing from the data and missing
    from the shop are different facts, and only the store owner knows the second.

    `_false_denial` cannot see this. It asks whether the denied name is a *stocked* product, so
    it is silent on exactly the perfumes the agent knows least about — every name outside the
    catalogue, which is every name this branch fires on.

    Scoped to the no-data contexts rather than run on every reply, because a denial is legitimate
    elsewhere: a size that has run out, or an original bottle that was never made, both come with
    real data behind them. Those turns carry product rows, not one of these markers.

    One no-data turn is exempt, and it is the turn where a denial is not merely allowed but
    required: `products.services.absence` swept the whole active catalogue for the name the customer
    typed and found nothing. `product_info` marks that turn `ABSENCE_DENIED`, and the reply is
    required to deny plainly and offer alternatives. Flagging the denial there would score the fix
    as the defect — and `_contradictory_availability` still holds that reply to not promising
    another check.

    The exemption is narrow on purpose. `_NO_DATA_CONTEXT[1]` appears on every not-found turn,
    including the store-policy, browsing and unintelligible cases, and on every `NAME_UNREADABLE`
    turn — where nobody verified anything and a denial is exactly the Versace Eros failure. None of
    those carry the marker, so this still fires on all of them.

    Returns the offending clause, or None.
    """
    # Imported here, not at module scope, like every other Django import in this file: it is not
    # safe to touch `products.services` before the harness has configured settings. Imported at
    # all rather than copied, so the marker cannot drift from the one `product_info` writes.
    from products.services.product_info import ABSENCE_DENIED_MARKER

    context = context or ""
    if not any(marker in context for marker in _NO_DATA_CONTEXT):
        return None
    if ABSENCE_DENIED_MARKER in context:
        return None

    for clause in _CLAUSE.split(reply or ""):
        if not any(pattern.search(clause) for pattern in _DENIAL):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SCOPED):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SIMILARITY):
            continue
        return clause.strip()
    return None


def _stalled_when_denial_required(reply, context):
    """A promise to go and check, on the one turn where there is nothing left to check.

    `ABSENCE_DENIED` means `products.services.absence` already swept the whole active catalogue for
    the name the customer typed and found nothing. The reply is then required to say so and to pitch
    stocked perfumes instead. "لحظة أتأكدلك منه" on that turn is not a hedge, it is a promise nobody
    in this pipeline keeps: no job looks the name up between two messages and no owner reply comes
    back, so the customer waits on an answer that never arrives. Conversation 816 turn 3 is the whole
    failure in one message — its entire text was `لحظة أتأكدلك منه يا فندم.`

    Critical rather than high because `router` already retries the turn when it sees exactly this,
    so anything reaching here got a second attempt and stalled anyway. That is what makes the retry
    observable: without this check a surviving stall is indistinguishable from a clean reply.

    The predicate is `described.promises_a_lookup`, the same one the router retries on, so the check
    and the guard cannot drift apart and score opposite verdicts on one reply. Deliberately keyed on
    the marker and not on the phrase alone: red line 2 and the store-policy case at
    `product_info`'s not-found branch (ب) still script the promise legitimately, and those turns
    carry no marker.

    Returns the offending phrase, or None.
    """
    from products.services.product_info import ABSENCE_DENIED_MARKER
    from products.services.sales import described
    from products.services.static_faq_service import normalize_arabic

    if ABSENCE_DENIED_MARKER not in (context or ""):
        return None
    if not described.promises_a_lookup(reply):
        return None

    # `promises_a_lookup` is this same membership test over the same tuple, so one of these matches
    # by construction; the loop only exists to name *which* promise in the finding.
    normalized = normalize_arabic(reply or "")
    return next(
        (
            marker
            for marker in described._DEFERRAL
            if normalize_arabic(marker) in normalized
        ),
        "وعد بالمراجعة",
    )


def _denial_without_alternatives(reply, context, truth):
    """A bare denial on a turn that was told to offer something in the same breath.

    The deterministic half of `scenarios_conv772.py`'s turn-1 probe: a customer who asked about a
    perfume we do not carry came here to buy a perfume, and "مش عندنا" on its own ends the
    conversation as surely as the stall it replaced. `product_info` widens the alternatives pool on
    every fully-absent turn precisely so the reply has stocked perfumes to name.

    High, not critical: the fact stated is true and the customer is not misled, only unserved.

    Scoped to `ABSENCE_DENIED` turns, which is what keeps it quiet everywhere a denial legitimately
    stands alone — a sold-out size, a perfume with no original bottle, a `NAME_UNREADABLE` abstain.
    The partial case needs no exemption of its own: it carries rows for the perfumes we *did* place,
    the reply names them, and those names are in `available_names`, so this passes without ever
    requiring the fresh alternatives that turn deliberately withholds.

    Detects the offer by naming, not by wording — any active, sellable catalogue perfume named
    anywhere in the reply counts. A reply that gestures at alternatives without naming one ("عندنا
    عطور تانية حلوة") is a real defect and is caught, which is the intent: an unnamed perfume cannot
    be bought.

    Bounded by `_DENIAL`, so a denial phrased outside those patterns is a conservative miss rather
    than a false alarm. Returns the lone denial clause, or None.
    """
    from products.services.product_info import ABSENCE_DENIED_MARKER
    from products.services.sales import naming

    if ABSENCE_DENIED_MARKER not in (context or ""):
        return None

    available = truth.get("available_names") or ()
    if not available:
        return None

    offending = None
    for clause in _CLAUSE.split(reply or ""):
        if not any(pattern.search(clause) for pattern in _DENIAL):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SCOPED):
            continue
        if any(pattern.search(clause) for pattern in _DENIAL_SIMILARITY):
            continue
        offending = clause.strip()
        break

    if not offending:
        return None
    if naming.mentioned_in(reply or "", [_NameOnly(name) for name in available if name]):
        return None
    return offending


def _numbers(text):
    return {match.group().replace(",", "") for match in _NUM.finditer(text or "")}


def build_ground_truth(store):
    """Everything the agent is factually allowed to say about this store."""
    from products.models import Product
    from products.services.product_formatting import is_variant_available

    prices, volumes, names, name_tokens, brands = set(), set(), set(), set(), set()
    longevity_numbers = set()
    # Active products with at least one sellable bottle. A denial of one of these is always
    # wrong, which is what the denial check needs and what `names` cannot express: `names`
    # deliberately stays unfiltered so it can still catch a hallucinated or deactivated
    # product being named, and narrowing it would turn a deactivated name into an
    # `unknown_latin_token` false positive instead.
    #
    # `is_variant_available` is reused rather than re-derived: a brand bottle is compounded to
    # order so it always counts, an original counts only while stock remains.
    available_names = set()

    products = Product.objects.filter(store=store).prefetch_related("variants").select_related("brand")
    for product in products:
        names.add(product.name)
        brands.add(product.brand.name)
        if product.is_active and any(
            is_variant_available(variant) for variant in product.variants.all()
        ):
            available_names.add(product.name)
        for token in re.findall(r"[A-Za-z0-9]+", f"{product.name} {product.brand.name}"):
            if len(token) > 1:
                name_tokens.add(token.lower())
        for number in re.findall(r"\d+", product.longevity or ""):
            longevity_numbers.add(number)
        for variant in product.variants.all():
            volumes.add(str(int(variant.volume)))
            prices.add(str(int(variant.price)))
            prices.add(f"{variant.price:.2f}")
            prices.add(f"{variant.price:.1f}")

    store_text = ""
    try:
        settings_row = store.settings
        store_text = " ".join([
            settings_row.system_prompt or "",
            settings_row.business_facts or "",
            settings_row.payment_instructions or "",
        ])
    except Exception:
        pass
    for faq in store.static_faqs.all():
        store_text += " " + (faq.answer or "")

    return {
        "prices": prices,
        "volumes": volumes,
        "names": names,
        "available_names": available_names,
        "name_tokens": name_tokens,
        "brands": brands,
        "longevity_numbers": longevity_numbers,
        "store_numbers": _numbers(store_text),
        "store_text": store_text,
        "catalog_size": products.count(),
    }


def _allowed_numbers(truth, context, customer_text):
    """Numbers this reply may legitimately contain."""
    allowed = set()
    allowed |= truth["prices"]
    allowed |= truth["volumes"]
    allowed |= truth["longevity_numbers"]
    allowed |= truth["store_numbers"]
    allowed |= _numbers(context)
    allowed |= _numbers(customer_text)
    # Small integers are counts, hours, sizes, list markers — never a price claim.
    allowed |= {str(n) for n in range(0, 100)}
    # Order totals: any sum of catalogue prices times a small quantity.
    numeric_prices = sorted({int(float(p)) for p in truth["prices"]})
    for price in numeric_prices:
        for quantity in range(1, 5):
            allowed.add(str(price * quantity))
        for other in numeric_prices:
            allowed.add(str(price + other))
    return allowed


def check_reply(reply, *, truth, context, customer_text, turn_state, history_text=""):
    """Findings for one bot reply. Each finding is (code, severity, detail).

    `history_text` is everything said earlier in the conversation. Without it a number the
    customer typed two turns ago reads as invented — the summary that correctly echoed back
    the phone numbers from the previous turn produced two `invented_number` criticals, the
    single worst class of false positive this file can emit.
    """
    findings = []
    reply = reply or ""
    allowed = _allowed_numbers(
        truth, context, f"{customer_text or ''}\n{history_text or ''}"
    )

    # ── Invented numbers (prices) ──────────────────────────────────────────
    for number in _numbers(reply):
        base = number.split(".")[0]
        if number in allowed or base in allowed:
            continue
        try:
            value = float(number)
        except ValueError:
            continue
        if value >= 100:
            findings.append((
                "invented_number", "critical",
                f"'{number}' appears in the reply but is not a catalogue price, volume, "
                f"store fact, or anything in the injected context",
            ))

    # ── Invented product names (Latin script) ──────────────────────────────
    for match in _LATIN_WORD.finditer(reply):
        token = match.group().strip(".-&'").lower()
        if not token or token in _LATIN_ALLOWLIST or token.isdigit():
            continue
        if token in truth["name_tokens"]:
            continue
        if token in (context or "").lower():
            continue
        if token in (customer_text or "").lower():
            continue
        if token in truth["store_text"].lower():
            continue
        findings.append((
            "unknown_latin_token", "high",
            f"'{match.group()}' is not part of any catalogue product or brand name",
        ))

    # ── Any perfume name at all, on a turn with no product data ────────────
    if not (context or "").strip():
        for name in truth["names"]:
            if name.lower() in reply.lower():
                findings.append((
                    "named_product_without_data", "high",
                    f"named '{name}' on a turn where no product data was injected",
                ))
                break

    # ── Denying a perfume we actually stock ────────────────────────────────
    denied = _false_denial(reply, truth)
    if denied:
        findings.append((
            "false_denial", "critical",
            f"told the customer '{denied}' is not available, but it is active in the "
            f"catalogue with a sellable bottle",
        ))

    # ── Denying and deferring in the same breath ──────────────────────────
    # No ground truth needed: the sentence contradicts itself whatever the catalogue holds, and
    # a catalogue lookup is exactly what `_false_denial` needs and cannot have here. Conversation
    # 795 turn 4 said both halves about a perfume in no catalogue and scored clean.
    contradiction = _contradictory_availability(reply)
    if contradiction:
        findings.append((
            "contradictory_availability", "critical",
            f"denied a perfume and promised to check on it in one breath: '{contradiction}'",
        ))

    # ── Denying on a turn with no data either way ──────────────────────────
    unbacked = _unbacked_denial(reply, context)
    if unbacked:
        findings.append((
            "unbacked_denial", "critical",
            f"told the customer a perfume is not available on a turn whose injected data said "
            f"nothing about it: '{unbacked}'",
        ))

    # ── Stalling on a turn where the catalogue was already swept ──────────
    # Only fires past the router's own retry, so a finding here is a stall that survived being
    # told not to. 816 turn 3's entire reply was the promise.
    stall = _stalled_when_denial_required(reply, context)
    if stall:
        findings.append((
            "stalled_when_denial_required", "critical",
            f"promised to check ('{stall}') on a turn where the whole catalogue had already been "
            f"searched and the name was not in it — nothing looks it up after this reply",
        ))

    # ── Denying without offering anything instead ─────────────────────────
    bare = _denial_without_alternatives(reply, context, truth)
    if bare:
        findings.append((
            "denial_without_alternatives", "high",
            f"denied the perfume ('{bare}') without naming a single stocked perfume the customer "
            f"could buy instead",
        ))

    # ── Talking to the customer about the injected data ───────────────────
    # No ground truth needed: the customer should never learn that "البيانات" exists. This is
    # how conversation 726's false denial was phrased, which is also why it slipped past
    # `_false_denial` for as long as it did.
    leak = _DATA_LEAK.search(reply or "")
    if leak:
        findings.append((
            "internal_data_leak", "high",
            f"told the customer about the injected data ('{leak.group()}') instead of naming "
            f"the perfume or asking which one they meant",
        ))

    # ── Unsupported certainty ─────────────────────────────────────────────
    for pattern in _GUARANTEE:
        if pattern.search(reply):
            findings.append((
                "unsupported_guarantee", "high",
                f"guarantee/precision claim matched /{pattern.pattern}/",
            ))
            break

    # ── Manufactured urgency ──────────────────────────────────────────────
    for pattern in _URGENCY:
        if pattern.search(reply):
            findings.append((
                "false_urgency", "high", f"urgency claim matched /{pattern.pattern}/",
            ))
            break

    # ── Premature closing ─────────────────────────────────────────────────
    # Stage sets are imported from production rather than restated. Three hardcoded copies of
    # the same set had already drifted: R3's "تحبي أجهزلك واحدة؟" was invisible here while
    # rescore.py would have flagged it high and the judge scored it 9.
    from products.services.sales.stage import CLOSING_STAGES, SOFT_CLOSING_STAGES

    stage = turn_state.get("stage")
    closing_ok = stage in CLOSING_STAGES
    if not any(pattern.search(reply) for pattern in _NOT_A_CLOSE):
        for pattern in _NARROWING:
            if pattern.search(reply) and stage not in SOFT_CLOSING_STAGES:
                findings.append((
                    "premature_close", "medium",
                    f"size-choice CTA at stage '{stage}', before a recommendation was made",
                ))
                break
        for pattern in _CLOSING:
            if pattern.search(reply):
                if not closing_ok:
                    findings.append((
                        "premature_close", "medium",
                        f"closing question at stage '{stage}' (leaked past the sanitizer): "
                        f"/{pattern.pattern}/",
                    ))
                break

    # ── Banned filler ─────────────────────────────────────────────────────
    for pattern in _FILLER:
        if pattern.search(reply):
            findings.append((
                "filler_question", "low", f"banned empty question /{pattern.pattern}/",
            ))
            break

    # ── Re-asking what the customer already said ──────────────────────────
    intent = turn_state.get("merged_intent") or {}
    if intent.get("max_price"):
        for pattern in _ASK_BUDGET:
            if pattern.search(reply):
                findings.append((
                    "reasked_budget", "high",
                    f"asked for the budget again although max_price={intent['max_price']} is known",
                ))
                break
    if intent.get("gender") and intent.get("gender") != "multiple":
        for pattern in _ASK_GENDER:
            if pattern.search(reply):
                findings.append((
                    "reasked_gender", "high",
                    f"asked male/female again although gender={intent['gender']} is known",
                ))
                break

    # ── An over-budget claim the reply's own numbers contradict ────────────
    # Keyed on `merged_intent`, not the scenario's `assert_budget`: no replay file sets that
    # key, so reading it here would leave every replayed conversation — the ones lifted from
    # real failures, this check included — ungraded. `runner.py` grades the scenario budget
    # separately; wiring this there too would double-report the same turn.
    for claim, highest in check_false_over_budget(reply, intent.get("max_price"), truth):
        findings.append((
            "false_over_budget", "critical",
            f"told the customer \"{claim}\" although the highest price the reply quotes is "
            f"{highest:.0f}, inside the stated budget of {intent['max_price']}",
        ))

    # ── Verbosity ─────────────────────────────────────────────────────────
    if len(reply) > 700:
        findings.append((
            "too_long", "low", f"{len(reply)} characters — persona caps replies at ~4 short sentences",
        ))

    # ── Similarity band vs the claim made ─────────────────────────────────
    similarity = turn_state.get("similarity")
    if similarity and not similarity.get("has_close_match"):
        claimed = re.search(r"(شبه|زي\s|بديل|نفس\s+الريح|نفس\s+الجو)", reply)
        admitted = re.search(r"(مفيش|مش\s+لاقي|مختلف|مش\s+نفس)", reply)
        if claimed and not admitted:
            findings.append((
                "similarity_overclaim", "critical",
                f"best similarity band was '{similarity.get('best_band')}' (not close) for "
                f"'{similarity.get('reference_name')}', yet the reply asserts a lookalike "
                f"without admitting the gap",
            ))

    return findings


def _strip_product_names(text, truth):
    """Remove catalogue names before scanning a reply for numbers.

    "Baccarat Rouge 540" carries a number inside the product name, and it was read as a
    540 EGP price quoted against a 300 budget — a false `over_budget_offer` on a reply that
    had in fact handled the budget honestly. Longest name first so a nested name cannot leave
    a fragment behind.
    """
    cleaned = text or ""
    for name in sorted(truth.get("names") or (), key=len, reverse=True):
        if name:
            cleaned = re.sub(re.escape(name), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def check_budget_respected(reply, budget, truth):
    """Any catalogue price quoted in the reply that is well above the stated budget.

    A price named *as* being over budget is not a finding. Scenario X3 asks for something
    impossible at 300 EGP and the correct answer names the nearest options and says plainly
    that they cost more — the skill requires exactly that, so flagging it inverted the grade.
    Named *falsely*, though, buys no excusal: see `_acknowledged_a_real_overage`.
    """
    if _acknowledged_a_real_overage(reply, budget, truth):
        return []

    over = []
    tolerance = budget * 1.2
    for number in _numbers(_strip_product_names(reply, truth)):
        base = number.split(".")[0]
        if base not in {p.split(".")[0] for p in truth["prices"]}:
            continue
        try:
            value = float(base)
        except ValueError:
            continue
        if value > tolerance:
            over.append(value)
    return over


# "الإجمالي: 1560 جنيه" / "المجموع 1753" / "الطلب كله بـ 1560"
#
# `سعره الإجمالي 944` is excluded by the negative lookbehind: that is one bottle's total price,
# not an order total, and reading it as one flagged a correct product_info reply about Dior
# Sauvage's 90ml. "الإجمالي" only means a cart when it is not qualifying a price.
_STATED_TOTAL = re.compile(
    r"(?<!سعره\s)(?<!سعرها\s)(?<!السعر\s)(?<!سعر\s)"
    r"(?:الإجمالي|الاجمالي|المجموع|الطلب\s+كله)\s*"
    r"(?:هيبقى|بيبقى|يبقى|هو|بـ|ب|:)?\s*(\d[\d.,]*)"
)


# The reply saying, in its own words, that something is over the customer's budget.
#
# One tuple, two readings, and that is the point. A stated overage is correct salesmanship when
# it is true — the skill explicitly allows going over budget as long as it is named, so only a
# SILENT overage is a finding — and it is a `false_over_budget` critical when it is not. Both
# questions are asked of the same sentence, so both are asked of the same patterns.
#
# They used to be one tuple serving one reading, `_BUDGET_ACKNOWLEDGED`, and the false reading
# had no patterns at all. Conversation 931 is what that cost: the reply said `أغلى من ميزانيتك`
# about a 1019 bottle against a 1200 budget, and `[أا]على` covers أعلى with ع but not أغلى with
# غ, so the sentence matched nothing here. The turn was not mis-scored — it was never examined.
# Keeping the readings on one tuple is what stops that spelling gap re-opening on one side only.
_HIGHER = r"[أاإ][عغ]ل[ىي]"

# What real replies put between the comparative and the budget word: an intensifier, the
# over-budget glyph, or both. The old patterns allowed nothing between, which is also why
# conversation 912's `أعلى شوية ⚠️ عن ميزانيتك` read as neither an acknowledgement nor a claim.
_INTERLEAVED = r"(?:\s*(?:شوي[هة]|بشوي[هة]|كتير|بكتير|جدا[ًا]?|⚠️))*"

# The suffix is consumed so the span this reports is a whole word: a finding detail quotes the
# matched text back for a human to read, and `ميزاني` truncated mid-word is a worse bug report
# than the one it describes. Not `\S*` — that reaches past the noun and takes the full stop with
# it, which cost the sanitizer's own version of this pattern a reply with no terminator.
_BUDGET_WORD = r"(?:ال)?ميزاني[^\s،,.؟!?]*"

# (pattern, negatable). `negatable` is False for the two cores that open with مش — there the مش
# belongs to the claim, so treating it as a denial would make the claim invisible.
_OVER_BUDGET_CLAIMS = (
    (re.compile(_HIGHER + _INTERLEAVED + r"\s*(?:من|عن)\s+" + _BUDGET_WORD), True),
    (re.compile(r"[أا]كتر" + _INTERLEAVED + r"\s*(?:من|عن)\s+" + _BUDGET_WORD), True),
    (re.compile(r"فوق" + _INTERLEAVED + r"\s*" + _BUDGET_WORD), True),
    (re.compile(r"(?:خارج|بر[هة])" + _INTERLEAVED + r"\s*" + _BUDGET_WORD), True),
    (re.compile(
        r"(?:زياد[هة]|بيزيد|زايد)" + _INTERLEAVED + r"\s*(?:عن|على)\s+" + _BUDGET_WORD
    ), True),
    (re.compile(r"الفرق\s+\d[\d.,]*\s*(?:جنيه)?\s*(?:عن|من)\s+" + _BUDGET_WORD), True),
    (re.compile(r"مش\s+داخل" + _INTERLEAVED + r"\s*" + _BUDGET_WORD), False),
    (re.compile(r"مش\s+في" + _INTERLEAVED + r"\s*" + _BUDGET_WORD), False),
)

# The marker with no sentence around it: "الـ90 ملي بـ1019 جنيه ⚠️، والـ50 ملي بـ666 جنيه داخل
# الميزانية." — the first live replay of the sanitizer fix, turn 10, budget 1200, both sizes inside
# it. No claim pattern above matches that, because the model lifted the glyph and left the sentence
# out; the customer's next turn was "ازاي اعلي من ميزانيتي", which is the original complaint of
# conversation 931 arriving with nothing but a glyph behind it.
#
# ⚠️ has one meaning here — `product_formatting._BUDGET_LABELS["near"]`, "أعلى شوية من الميزانية" —
# and four prompt rules bind it to that meaning by name, so beside an in-budget price it tells the
# customer the same falsehood the sentence did.
#
# Positional, and that is the whole design. Every other ⚠️ this system emits either lives only in
# the injected context or *leads* its line — "⚠️ للعلم: إجمالي الطلب 3138 جنيه", "⚠️ العطور اللي تحت
# دي" — while the budget label alone is a suffix to a price. Requiring a number before the glyph is
# what separates the falsehood from a warning the reply was right to relay. The production guard
# strips the glyph unconditionally instead, once its own preconditions hold; the difference is
# deliberate and is what this file is for. Losing a glyph there costs nothing, whereas a critical
# finding on a true warning here would spend a human's attention and teach them to distrust the
# check.
_STRANDED_BUDGET_GLYPH = re.compile(r"\d[\d.,]*\s*(?:جنيه|جني[هة]|ج\.?م)?\s*⚠")

_NEGATORS = frozenset((
    "مش", "مِش", "ولا", "لا", "ماهو", "ماهوش", "مكانش", "مبقاش",
    # Quantified denials, which is how a retraction about *both* sizes is actually phrased:
    # "ولا واحد منهم أعلى من ميزانيتك", "مفيش حاجة فيهم فوق الميزانية".
    "مفيش", "مافيش", "ماحدش", "محدش",
))
_WHITESPACE = re.compile(r"\s+")
_CLAUSE_BREAK = re.compile(r"[،,.؟!?]")
# Where a claim's referent stops being findable — see `_price_in_scope`. A comma does not end it;
# a full stop or a line break does.
_SENTENCE_BREAK = re.compile(r"[.؟!?\n\r]")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _budget_limit(budget):
    """The budget as a float, or None when it is not a usable number.

    `merged_intent` is written by the extractor model, so this arrives as whatever it produced —
    a float, "1200", or something that is not a number at all. A bare `float(budget)` in the two
    callers would turn one bad extraction into a crashed eval run, which is a worse outcome than
    an ungraded turn. Mirrors `value.as_budget`, which exists for this in production code.
    """
    if budget is None:
        return None
    try:
        limit = float(budget)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def _negated_at(text, start):
    """Whether the claim starting at `start` is being denied rather than made.

    On the objection turn the *correct* reply is "هو مش أعلى من ميزانيتك", and every pattern
    above matches inside it — so without this, the one reply the fix exists to produce would be
    scored as the defect it retracts. Tokens rather than a lookbehind, because لا is a substring
    of ولا and of خلاص. Mirrors `rescore._NEGATED_GUARANTEE`, which exists for the same reason.

    Two bounds on how far back to look, and both are load-bearing in opposite directions:

    - Stop at the previous clause break. A negator in the clause before does not deny this one —
      "الـ90 بـ1019 جنيه مش بطال، بس أغلى من ميزانيتك" asserts the claim.
    - Within the clause, only the last three tokens. Scanning a whole clause would let any مش
      anywhere in a long sentence suppress the claim, and here a suppressed claim is a missed
      falsehood — in the production guard, a falsehood left in the reply.

    Three rather than two because that is what the quantified denial needs: "ولا واحد منهم أعلى
    من ميزانيتك" puts the negator three tokens out, and a two-token window read it as an
    assertion — which in the sanitizer meant deleting a retraction and mangling a correct reply.
    """
    before = text[:start]
    breaks = [match.end() for match in _CLAUSE_BREAK.finditer(before)]
    clause = before[breaks[-1]:] if breaks else before
    tokens = _WHITESPACE.split(clause.strip())[-3:]
    return any(token.strip("،,.!؟?ـ()") in _NEGATORS for token in tokens)


def _price_like(reply, truth):
    """Every number in the reply big enough to be a price, product names removed first.

    The ≥100 floor drops volumes, percentages and list markers. `_strip_product_names` runs
    first for the reason its own docstring gives: the 540 in "Baccarat Rouge 540" is not a price,
    and reading it as one already cost a false finding once.
    """
    text = _strip_product_names(reply or "", truth).translate(_ARABIC_INDIC)
    values = []
    for raw in _NUM.findall(text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if value >= 100:
            values.append(value)
    return values


def _budget_claim_spans(reply):
    """Every over-budget assertion in the reply as `(text, start)`, denials skipped."""
    spans = []
    for pattern, negatable in _OVER_BUDGET_CLAIMS:
        for match in pattern.finditer(reply or ""):
            if negatable and _negated_at(reply, match.start()):
                continue
            spans.append((match.group().strip(), match.start()))
    return spans


def _budget_claims(reply):
    """Every place the reply asserts something is over the budget, denials skipped.

    Positions deliberately dropped: this is the *acknowledgement* reading, and a reply that names
    an overage has named it wherever the sentence sits. Only the falsification reading needs to
    know where the claim is — see `_price_in_scope`.
    """
    return [text for text, _ in _budget_claim_spans(reply)]


def _price_in_scope(reply, start, truth):
    """Whether a price the claim at `start` could be about is actually in the reply.

    This check used to ask the reply-wide question — does anything here quote an in-budget price —
    and that is true of almost every priced reply, including one whose claim is about something it
    deliberately did not price. Two turns show it, and both had been graded clean for months:

        M1 turn 3, budget 700: "Dior Sauvage خرج من طلبك لأنه سعر الـ90 ملي أعلى من ميزانيتك بكتير.
        عندنا بدائل داخل الميزانية زي Ambero والـ50 ملي بـ601 جنيه، وDark Aura والـ50 ملي بـ680 جنيه."

        G2 turn 1, budget 500: four bullet lines pricing 50ml sizes at 400-480, then "لو حابب حجم
        أكبر، الـ90 ملي أغلى بكتير عن ميزانيتك، فالأفضل تبدأ بالـ50 ملي."

    Both claims are TRUE, and in both the claim's own subject has no figure beside it because the
    prompt forbids pricing an ❌ size. The prices belong to other perfumes, or other sizes, named
    elsewhere in the reply. Reporting these as critical would have been worse than the silence it
    replaced: a check that cries wolf on correct salesmanship is a check people learn to skip.

    Found by re-grading four archived run files after this check went in, which is the argument for
    keeping them. The production guard was stripping those sentences — turning M1's into
    "خرج من طلبك لأنه سعر الـ90 ملي." — so the same bound is now in `reply_sanitizer` too, arrived
    at from the same two turns and written separately, per this file's independence rule.

    Sentence scope, not clause: conversation 912's "سعره 1046 جنيه، أعلى شوية ⚠️ عن ميزانيتك" puts
    the price one comma from the claim and is the earlier report of the very bug this check exists
    for. Forward as well as back, because "أعلى من ميزانيتك بـ353 جنيه" states its figure after the
    claim, which is conversation 931's own wording.

    `_price_like` is applied to the scope text rather than to the reply, so the ≥100 floor and the
    product-name strip both hold here without the offsets having to survive the strip: "Baccarat
    Rouge 540 فوق ميزانيتك. وفيه Eros بـ666 جنيه." must not read 540 as the claim's referent.
    """
    text = reply or ""
    breaks = [match.end() for match in _SENTENCE_BREAK.finditer(text[:start])]
    scope_start = breaks[-1] if breaks else 0
    ahead = _SENTENCE_BREAK.search(text, start)
    scope_end = ahead.start() if ahead else len(text)
    return bool(_price_like(text[scope_start:scope_end], truth))


def _acknowledged_a_real_overage(reply, budget, truth):
    """Whether the reply names an overage that its own numbers bear out.

    Replaces a bare `any(pattern.search(...))` in the two budget checks. That excused a turn
    from them on the *wording* alone, never asking whether anything in the reply was in fact
    over — so a reply that claimed an overage falsely also bought itself silence on every
    sibling budget finding. The claim now has to be true to earn the excusal.
    """
    if not _budget_claims(reply):
        return False
    limit = _budget_limit(budget)
    if limit is None:
        return True
    return any(value > limit for value in _price_like(reply, truth))


def check_stated_total(reply, budget, truth):
    """A total the reply states out loud, checked against the stated budget.

    Deliberately independent of `_allowed_numbers`, which whitelists every catalogue price
    times one to four *and every pairwise sum* so that legitimate order totals are not flagged
    as invented. The side effect is that a fabricated total is unflaggable by construction:
    2 × 780 = 1560 is a product of real prices, so `invented_number` stayed silent while the
    agent quoted 1560 against a stated budget of 900 for a cart that did not exist.

    A stated total is the one number where the arithmetic being valid is not the point. What
    matters is whether the customer was told. A reply that names the overage has done its job.
    """
    if not budget:
        return []
    if _acknowledged_a_real_overage(reply, budget, truth):
        return []

    over = []
    for match in _STATED_TOTAL.finditer(reply or ""):
        raw = match.group(1).rstrip(".,").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > float(budget):
            over.append(value)
    return over


def check_false_over_budget(reply, budget, truth):
    """The reply telling the customer a price is over their budget when it is not.

    Conversation 931: budget 1200, Versace Eros' 50ml at 666 and 90ml at 1019, both of them
    labelled `✅ (داخل الميزانية)` in the injected context — and the reply said "الـ90 ملي بـ1019
    جنيه ⚠️ يعني أغلى من ميزانيتك بـ353 جنيه". 353 is 1019 − 666, the gap between the two *sizes*,
    printed by the value note one line below those labels and re-attributed to the budget. The
    customer objected twice. The claim was repeated, not retracted.

    Nothing in this file detected it, because "claimed over budget while actually in budget" had
    no check at all — the patterns existed only to *excuse* a turn, never to falsify it. This is
    the other reading of the same patterns.

    Returns one `(claim, highest_price_quoted)` per false claim. The rule, and why each step:

    - No usable budget: nothing to be wrong about.
    - No price-like number in the reply: the claim can be TRUE with the figure left out —
      "الـ90 ملي أعلى شوية من ميزانيتك" is a shape the code deliberately produces — so silence.
      This precondition is load-bearing, not defensive.
    - Some quoted price really is above the budget: the claim has a true referent, leave it.
    - Per claim, no price inside its own sentence: the same reasoning as the step above, asked
      where it belongs. A reply can price four alternatives and be telling the truth about a fifth
      thing it deliberately left unpriced — `_price_in_scope` has the two turns that proved it.
    - What is left is a claim standing beside a price that is inside the budget, and it is false
      about it.

    One-directional by construction: a true statement about a price the customer can see must
    name a number above the budget, and that number stops this check before it reports.

    A price-adjacent ⚠️ counts as one of these, sentence or no sentence — see
    `_STRANDED_BUDGET_GLYPH` for the replay turn that shows why, and for why the position matters.
    It is reported here but deliberately *not* added to `_budget_claims`: the two readings of that
    tuple are "is this false" and "does this excuse the turn from the silent-overage checks", and a
    bare glyph answers only the first. The skill permits going over budget when it is *named*, with
    both figures said out loud; a marker with no sentence has named nothing, so it must not buy the
    silence a real acknowledgement buys.

    Built on this module's own patterns rather than importing `reply_sanitizer`'s deliberately —
    the point is to measure what leaks PAST the sanitizer, and a shared regex would score the
    sanitizer's blind spots as clean. Their overlap is its coverage; their difference is its
    misses, which is the number worth watching.
    """
    limit = _budget_limit(budget)
    if limit is None:
        return []
    prices = _price_like(reply, truth)
    if not prices or max(prices) > limit:
        return []
    highest = max(prices)
    claims = [
        text for text, start in _budget_claim_spans(reply)
        if _price_in_scope(reply, start, truth)
    ]
    # After the claims, so the glyph inside "بـ1019 جنيه ⚠️ يعني أعلى من ميزانيتك" is not reported
    # twice as two separate findings about one sentence.
    if not claims and _STRANDED_BUDGET_GLYPH.search(_strip_product_names(reply or "", truth)):
        claims = ["⚠️ (بدون جملة)"]
    return [(claim, highest) for claim in claims]
