# -*- coding: utf-8 -*-
"""Deterministic, non-LLM checks on a generated reply.

These exist because factual correctness must be *proven*, not judged. An LLM judge
asked "did it invent a price?" will sometimes say no when it did. Cross-referencing
every number in the reply against the store's own rows cannot.

Everything here is read-only against the database.
"""

import re

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

# Empty filler questions the persona bans outright.
_FILLER = (
    re.compile(r"(?:عايز|محتاج|تحب)\s+حاج[هة]\s+تاني[هة]\s*[؟?]"),
    re.compile(r"محتاج\s+مساعد[هة]"),
    re.compile(r"تحب\s+تعرف\s+(?:ال)?[أا]?سعار"),
    re.compile(r"عطر\s+معين\s+في\s+بالك"),
    re.compile(r"[أا]قدر\s+[أا]ساعدك\s+(?:في\s+)?[أا]?ي[هة]?\s*[؟?]"),
)

_ASK_BUDGET = (
    re.compile(r"ميزانيتك"),
    re.compile(r"حدود\s+كام"),
    re.compile(r"في\s+رينج"),
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


def _numbers(text):
    return {match.group().replace(",", "") for match in _NUM.finditer(text or "")}


def build_ground_truth(store):
    """Everything the agent is factually allowed to say about this store."""
    from products.models import Product

    prices, volumes, names, name_tokens, brands = set(), set(), set(), set(), set()
    longevity_numbers = set()

    products = Product.objects.filter(store=store).prefetch_related("variants").select_related("brand")
    for product in products:
        names.add(product.name)
        brands.add(product.brand.name)
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


def check_reply(reply, *, truth, context, customer_text, turn_state):
    """Findings for one bot reply. Each finding is (code, severity, detail)."""
    findings = []
    reply = reply or ""
    allowed = _allowed_numbers(truth, context, customer_text)

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


def check_budget_respected(reply, budget, truth):
    """Any catalogue price quoted in the reply that is well above the stated budget."""
    over = []
    tolerance = budget * 1.2
    for number in _numbers(reply):
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
