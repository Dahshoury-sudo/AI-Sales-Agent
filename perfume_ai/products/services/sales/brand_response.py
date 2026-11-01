"""Keep cross-brand recommendations grounded in the ranked catalogue."""
import re

from . import naming
from .brand_preference import matches_brand, preferred_brand_name


def is_soft_preference(intent):
    return bool((intent or {}).get("brand")) and (intent or {}).get("brand_strict") is not True


def leads_outside_discussion(products, intent, keeping):
    """A fresh best fit must not receive instructions to stay on old choices."""
    return bool(products and is_soft_preference(intent) and products[0].name not in keeping)


def instruction(products, intent, store=None):
    if not products or not is_soft_preference(intent):
        return ""
    store = store or getattr(products[0], "store", None)
    preferred = preferred_brand_name(intent["brand"], store)
    alternatives = [p for p in products if not matches_brand(p, intent["brand"], store)]
    if not alternatives:
        return ""
    labels = "؛ ".join(f"{p.name} من {p.brand.name}" for p in alternatives)
    return (
        f"\nالعميل يفضل {preferred}، لكنه مش مشترط البراند ده لوحده. "
        f"الأفضل حسب طلبه الحالي هو {products[0].name}، ابدأ بيه حتى لو من براند تاني.\n"
        f"الاختيارات من براندات تانية: {labels}.\n"
        "وضح براند كل بديل بتعرضه وإنه مختلف عن البراند المطلوب، واربط ترشيحك بصفة "
        "مدعومة في بياناته. اعرضه مباشرة من غير سؤال إذا كان يقبل براند تاني. "
        "ممكن تضيف اختيار من البراند اللي فضله لو مناسب، لكن متفرضوش كاختيار تاني. "
        "إجمالي الاختيارات عطر أو عطرين فقط، شامل أي اختيار من البراند المفضل. "
        "لو الفوحان متساوي متقولش واحد أخف أو أهدى من التاني؛ قارن النوتات المسجلة "
        "بكلام طبيعي من غير نقل عناوين داخلية زي مش مطابق في أو Match. "
        "قارن الثبات والفوحان كل محور لوحده: قوة الفوحان لا تعني ثبات أطول. "
        "اسم البراند مش ضمان إن السائل أصلي؛ التزم بمعلومات نوع السائل والزجاجة.\n"
    )


def response_issues(reply, products, intent, search=None, store=None):
    if not products or not is_soft_preference(intent):
        return []
    store = store or getattr(products[0], "store", None)
    ordered = naming.names_in(reply, [p.name for p in products])
    if not ordered:
        return ["الرد لازم يقدم عطر مناسب من القائمة مباشرة"]
    issues = []
    if len(ordered) > 2:
        issues.append("اكتف بعطر أو عطرين إجمالاً، بما فيهم أي اختيار من البراند المفضل")
    from ..static_faq_service import normalize_arabic
    text = normalize_arabic(reply)
    if re.search(r"مش مطابق في|ليه مناسب\s*:|\bmatch\s*:", text):
        issues.append("اشرح الفروق الحقيقية بكلام طبيعي من غير عناوين التقييم الداخلية")
    mentioned = [p for p in products if p.name in ordered]
    reference = (search or {}).get("reference_product")
    if reference and naming.names_in(reply, [reference.name]):
        mentioned.append(reference)
    projections = {(p.projection or "").strip().casefold() for p in mentioned}
    if len(mentioned) > 1 and len(projections) == 1 and "" not in projections and re.search(
        r"(?<!\w)(?:اخف|اهدي)\s+(?:شويه|من|للنهار)|فوحانه\s+(?:اقوي|اقل|اعلي)", text,
    ):
        issues.append("الفوحان المسجل متساوي؛ لا تدعي أن أحدهما أخف أو أهدى أو أقوى")
    from .similarity import peak_hours
    durations = {peak_hours(p.longevity) for p in mentioned}
    if len(mentioned) > 1 and len(durations) == 1 and None not in durations and re.search(
        r"ثباته\s+(?:وفوحانه\s+)?(?:اقوي|اطول|اعلي|اكتر)|ثابت\s+اكتر|يدوم\s+اطول", text,
    ):
        issues.append("الثبات المسجل متساوي؛ لا تدعي ثبات أطول أو أقوى بسبب اختلاف الفوحان")
    ranked = (search or {}).get("ranked") or {}
    leader = ranked.get(products[0].pk)
    chosen = next(p for p in products if p.name == ordered[0])
    entry = ranked.get(chosen.pk)
    if leader and entry:
        from .ranking import preference_fit_key
        if preference_fit_key(leader) < preference_fit_key(entry):
            issues.append(f"ابدأ بـ{products[0].name} لأنه أكثر مطابقة لطلب العميل الحالي")
    preferred = preferred_brand_name(intent["brand"], store)
    for product in products:
        if product.name in ordered and not matches_brand(product, intent["brand"], store):
            if re.search(re.escape(product.name) + r"\s+(?:هو\s+)?(?:من|تابع)\s+" + re.escape(preferred), reply, re.I):
                issues.append(f"{product.name} من {product.brand.name}، مش من {preferred}")
    return issues


def disclose_brands(reply, products, intent, store=None):
    """Add missing brand disclosure even after a factual recovery replaces prose."""
    if not reply or not is_soft_preference(intent):
        return reply
    from ..static_faq_service import normalize_arabic

    products = list(products)
    ordered = naming.names_in(reply, [p.name for p in products])
    selected = [next(p for p in products if p.name == name) for name in ordered[:2]]
    selected = [p for p in selected if not matches_brand(p, intent["brand"], store)]
    if not selected:
        return reply
    store = store or getattr(selected[0], "store", None)
    preferred = preferred_brand_name(intent["brand"], store)
    normalized = normalize_arabic(reply)
    stated_difference = bool(re.search(r"براند(?:ات)?\s+(?:تاني|مختلف)|غير\s+" + re.escape(normalize_arabic(preferred)), normalized))
    if stated_difference and all(re.search(
        r"(?:من|براند)\s+" + re.escape(normalize_arabic(p.brand.name)) + r"(?!\w)", normalized,
    ) for p in selected):
        return reply
    labels = "، و".join(f"{p.name} من {p.brand.name}" for p in selected)
    difference = "براند تاني" if len(selected) == 1 else "براندات تانية"
    return reply.rstrip() + f"\n\n{labels}، {difference} غير {preferred}."
