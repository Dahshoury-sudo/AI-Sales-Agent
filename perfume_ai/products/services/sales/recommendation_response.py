"""Check recommendations against current ranked evidence, across all brands."""
import re

from ..static_faq_service import normalize_arabic
from . import naming, ranking, similarity


def instruction(intent):
    if (intent or {}).get("recommendation_mode") != "new_goal":
        return ""
    return (
        "\nالعميل بدأ طلب ترشيح جديد. قيّم الاختيارات حسب طلبه الحالي وابدأ بالأكثر مطابقة "
        "في القائمة، حتى لو كنت رشحت غيره قبل كده. اعرض عطر أو عطرين فقط. "
        "ممنوع تتمسك بترشيح قديم أو تنقل شروط أو مقارنة من الطلب السابق. "
        "لو الفوحان قوي متقولش خفيف أو هادي؛ ولو المناسبة المسجلة مختلفة متدعيش إنه مناسب "
        "للمناسبة المطلوبة. لو الفوحان متساوي متقولش واحد أهدى أو أخف من التاني، "
        "ولو الثبات متساوي متقولش واحد ثابت أكتر. قارن النوتات المسجلة بدل اختراع فرق أداء. "
        "للمسجد اشرح النوتات والفوحان المسجلين من غير ضمان إنه مريح لكل الناس.\n"
    )


def response_issues(reply, products, intent, search=None):
    search = search or {}
    issues = []
    current = (intent or {}).get("recommendation_mode") == "new_goal"
    ordered = naming.names_in(reply, [p.name for p in products])
    if current and products:
        if not ordered:
            issues.append("قدم عطر مناسب من القائمة مباشرة")
        if len(ordered) > 2:
            issues.append("اكتف بعطر أو عطرين")
        entries = search.get("ranked") or {}
        leader = entries.get(products[0].pk)
        chosen = next((p for p in products if ordered and p.name == ordered[0]), None)
        entry = entries.get(chosen.pk) if chosen else None
        if leader and entry and ranking.preference_fit_key(leader) < ranking.preference_fit_key(entry):
            issues.append(f"ابدأ بـ{products[0].name} لأنه أكثر مطابقة للطلب الحالي")
    # The model still sees old comparison prose in history after the goal changes.
    # An absent current reference cannot license repeating that comparison.
    if current and not search.get("similarity"):
        issues.extend(similarity.alternative_wording_issues(reply))
    mentioned_products = [p for p in products if p.name in ordered]
    reference = search.get("reference_product")
    if reference and naming.names_in(reply, [reference.name]):
        mentioned_products.append(reference)
        if re.search(r"(?m)^\s*(?:[•🔹*\-]\s*)?" + re.escape(reference.name) + r"\s*[:—–-]|"
                     r"(?:ارشحلك|أرشحلك|جرب)\s+" + re.escape(reference.name), reply, re.I):
            issues.append("مرجع المقارنة ليس بديلاً؛ لا تعرضه ضمن الترشيحات")
    if current and len(mentioned_products) > 1:
        text = normalize_arabic(reply)
        projections = {(p.projection or "").strip().casefold() for p in mentioned_products}
        if len(projections) == 1 and "" not in projections and re.search(
            r"(?<!\w)(?:اخف|اهدي)\s+(?:شويه|من|للنهار|للمسجد)|فوحانه\s+(?:اقوي|اقل|اعلي)", text,
        ):
            issues.append("الفوحان المسجل متساوي؛ لا تدعي أن أحدهما أهدى أو أخف أو أقوى")
        durations = {similarity.peak_hours(p.longevity) for p in mentioned_products}
        if len(durations) == 1 and None not in durations and re.search(
            r"ثباته\s+(?:وفوحانه\s+)?(?:اقوي|اطول|اعلي|اكتر)|ثابت\s+اكتر|يدوم\s+اطول", text,
        ):
            issues.append("الثبات المسجل متساوي؛ لا تدعي ثبات أطول لأحدهما")
    if not products:
        return issues
    by_name = {p.name.casefold(): p for p in products}
    names = re.compile("|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True)), re.I)
    mentions = list(names.finditer(reply or ""))
    for index, mention in enumerate(mentions):
        product = by_name[mention.group().casefold()]
        end = mentions[index + 1].start() if index + 1 < len(mentions) else len(reply)
        text = normalize_arabic(reply[mention.end():end])
        # Validate the occasion actually claimed, even when the customer only
        # said 'mosque'. That word is not a time of day and licenses neither claim.
        for clause in re.split(r"[\n.!؟?،;]+", text):
            if re.search(r"مش\s+(?:مناسب|بيناسب|ينفع)|لا\s+يناسب|not\s+suitable", clause):
                continue
            if not re.search(r"يناسب|تناسب|مناسب|ينفع|suitable", clause):
                continue
            for claimed, pattern in (("daytime", r"نهار|الصبح|يومي|daytime|daily|morning"),
                                     ("casual", r"خروجات|casual"),
                                     ("evening", r"سهرات|سهره|مساء|ليل|evening|night")):
                if re.search(pattern, clause) and ranking._occasion_verdict(product.occasion, claimed)[0] == ranking.CONFLICT:
                    issues.append(f"لا تنسب مناسبة مخالفة إلى {product.name}؛ مناسبته المسجلة {product.occasion}")
        if re.search(r"فوحان\w*[^.!؟?\n]{0,30}\d+\s*ساع", text) and not re.search(r"ثبات|ثابت|يدوم", text):
            issues.append("الساعات تصف الثبات، وليس الفوحان")
        actual = similarity._ordinal(product.projection, similarity._PROJECTION_WORDS) if product.projection else None
        if actual is not None and actual > 2 and re.search(r"خفيف|هادي|هادئ|فوحانه\s+متوسط", text):
            if not re.search(r"مش\s+(?:خفيف|هادي|هادئ)|غير\s+خفيف", text):
                issues.append(f"لا تصف {product.name} بالفوحان الهادئ؛ المسجل {product.projection}")
    return list(dict.fromkeys(issues))
