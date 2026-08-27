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
_CLOSING = (
    re.compile(r"تحب\s+[أا]?ساعدك\s+في\s+(?:ال)?(?:طلب|[أا]وردر)"),
    re.compile(r"تحب\s+[نت]طلب"),
    re.compile(r"ن(?:سجل|كمل)\s+(?:ال)?(?:طلب|[أا]وردر)"),
    re.compile(r"[أا]جيبلك\s+(?:الـ?\s*)?\d+"),
    re.compile(r"[أا]سجلك?\s*(?:ال)?طلب"),
    re.compile(r"تحب\s+نكمل"),
    re.compile(r"عايز\s+تطلب"),
    re.compile(r"نبعتلك\S*\s+الطلب"),
)

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
    re.compile(r"تحب\s+تعرف\s+(?:ال)?[أا]?سعار"),
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
_DENIAL = (
    re.compile(r"مفيش\s+عندنا"),
    re.compile(r"مش\s+متوفر"),
    re.compile(r"غير\s+متوفر"),
    re.compile(r"مفيش\s+حاليا"),
)

# Denials that are correct, and which the patterns above match anyway. `مش\s+متوفر` has no
# trailing boundary, so it also matches متوفرة / متوفرين / متوفر منه.
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
    stage = turn_state.get("stage")
    closing_ok = stage in ("purchase_intent", "order_collection")
    if not any(pattern.search(reply) for pattern in _NOT_A_CLOSE):
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
    """
    if any(pattern.search(reply or "") for pattern in _BUDGET_ACKNOWLEDGED):
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


# The reply telling the customer, in its own words, that it is over their budget. A total
# stated *with* this is correct salesmanship — the skill explicitly allows going over budget
# as long as it is named — so only a SILENT overage is a finding.
_BUDGET_ACKNOWLEDGED = (
    re.compile(r"[أا]على\s+من\s+(?:ال)?ميزاني"),
    re.compile(r"[أا]كتر\s+من\s+(?:ال)?ميزاني"),
    re.compile(r"فوق\s+(?:ال)?ميزاني"),
    re.compile(r"خارج\s+(?:ال)?ميزاني"),
    re.compile(r"زياد[هة]\s+عن\s+(?:ال)?ميزاني"),
    re.compile(r"مش\s+داخل\s+(?:ال)?ميزاني"),
)


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
    if any(pattern.search(reply or "") for pattern in _BUDGET_ACKNOWLEDGED):
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
