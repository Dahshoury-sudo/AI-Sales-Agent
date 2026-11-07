"""Check recommendations against current ranked evidence, across all brands."""
import re

from products.occasions import occasion_groups, occasion_matches
from ..static_faq_service import normalize_arabic
from . import naming, ranking, similarity


def _choice_limit(intent):
    count = (intent or {}).get("recommendation_count") or 2
    if (intent or {}).get("budget_scope") == "total":
        count = (intent or {}).get("purchase_quantity") or 2
    return count if type(count) is int and count > 0 else 2


def instruction(intent):
    if (intent or {}).get("recommendation_mode") != "new_goal":
        return ""
    count = _choice_limit(intent)
    return (
        "\nالعميل بدأ طلب ترشيح جديد. قيّم الاختيارات حسب طلبه الحالي وابدأ بالأكثر مطابقة "
        f"في القائمة، حتى لو كنت رشحت غيره قبل كده. اعرض لحد {count} عطور حسب طلبه، مع سبب قصير لكل عطر. "
        "من غير تلخيص طلبه أو سؤال ختامي لازم أو نقط مزخرفة. "
        "ممنوع تتمسك بترشيح قديم أو تنقل شروط أو مقارنة من الطلب السابق. "
        "لو المناسبة المسجلة مختلفة متدعيش إنه مناسب للمناسبة المطلوبة. "
        "الثبات ممتاز والفوحان قوي لكل عطورنا، فمتخترعش فرق أداء بين الاختيارات. "
        "اوصف ريحة كل ترشيح حسب قاعدة التاجات فقط في الخط الأحمر 6؛ "
        "النوتات للمكونات المطلوبة صراحة فقط، والطابع الخفيف لا يعني فوحان هادي.\n"
    )


def response_issues(reply, products, intent, search=None):
    search = search or {}
    issues = []
    current = (intent or {}).get("recommendation_mode") == "new_goal"
    ordered = naming.names_in(reply, [p.name for p in products])
    if current and products:
        if not ordered:
            issues.append("قدم عطر مناسب من القائمة مباشرة")
        count = _choice_limit(intent)
        if len(ordered) > count:
            issues.append(f"اكتف بعدد الترشيحات المطلوب: {count}")
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
    from ..performance_policy import claim_issues
    issues.extend(claim_issues(reply))
    if not products:
        return issues
    by_name = {p.name.casefold(): p for p in products}
    names = re.compile("|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True)), re.I)
    mentions = list(names.finditer(reply or ""))
    for index, mention in enumerate(mentions):
        product = by_name[mention.group().casefold()]
        end = mentions[index + 1].start() if index + 1 < len(mentions) else len(reply)
        text = normalize_arabic(reply[mention.end():end])
        # Suitability claims use the same six categories as candidate filtering.
        for clause in re.split(r"[\n.!؟?،;]+", text):
            if re.search(r"مش\s+(?:مناسب|بيناسب|ينفع)|لا\s+يناسب|not\s+suitable", clause):
                continue
            if not re.search(r"يناسب|تناسب|مناسب|ينفع|suitable", clause):
                continue
            for claimed in occasion_groups(clause):
                if not occasion_matches(product.occasion, claimed):
                    issues.append(f"لا تنسب مناسبة مخالفة إلى {product.name}؛ مناسبته المسجلة {product.occasion}")
    return list(dict.fromkeys(issues))
