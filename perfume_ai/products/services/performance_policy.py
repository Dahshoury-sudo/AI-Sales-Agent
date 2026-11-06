"""The store's performance policy, independent of historical catalog measurements.

The request context lasts for one reply only. ContextVar keeps simultaneous chats
isolated; public service entry points also work when called without the router.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import signature
import re


EXCELLENT = "ممتاز"
STRONG = "قوي"
LONGEVITY_HOURS = "أكتر من 8 ساعات"
GENERAL_REPLY = "ثباته ممتاز وفوحانه قوي."
UNKNOWN_REPLY = "ما عنديش معلومات مؤكدة عن ثبات العطر ده وفوحانه"


def _normal(text):
    # One-to-one translation also lets claim spans address the original string.
    return str(text or "").lower().translate(str.maketrans(
        "أإآىة٠١٢٣٤٥٦٧٨٩", "ااايه0123456789",
    ))


_LONGEVITY = re.compile(
    r"(?<!\w)(?:[وفب]?ال|[وف])?(?:ثبات\w*|ثابت\w*|اثبت\w*|(?:ب[يت]|[يت])ثبت\w*|"
    r"يدوم\w*|بيدوم\w*|تدوم\w*|بيستمر\w*|longevity|lasting|lasts?|lasted)(?!\w)"
)
_PROJECTION = re.compile(
    r"(?<!\w)(?:[وفب]?ال|[وف])?(?:فوحان\w*|فواح\w*|انتشار(?:ه|ها|هم)?|"
    r"projection|sillage|projects?)(?!\w)"
)
_PERFORMANCE = re.compile(r"(?<!\w)(?:ال)?اداء(?:ه|ها|هم)?(?!\w)|\bperformance\b")
_UNIT = r"(?:ساعه|ساعات|ساعتين|ساعتي?ن|يوم|يومين|ايام|اسبوع|اسابيع|دقيقه|دقائق|hours?|hrs?|days?|weeks?|minutes?)"
_NUMBER = r"(?:\d+(?:[.,]\d+)?|واحده?|اتنين|اثنين|تلاته?|ثلاثه?|اربع[ه]?|خمس[ه]?|ست[ه]?|سبع[ه]?|تمان[يه]*|ثمان[يه]*|تسع[ه]?|عشر[ه]?|اتناشر|اثنا عشر|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty.four|forty.eight)"
_MEASURE = re.compile(rf"(?<!\w){_NUMBER}(?:\s*[-–—/]\s*{_NUMBER})?\s*\+?\s*{_UNIT}(?!\w)")
_DURATION = re.compile(rf"{_MEASURE.pattern}|(?<!\w)(?:ساعتين|يومين|طول اليوم|اليوم كله|يوم كامل|all day|overnight)(?!\w)")
_DURATION_PREFIX = re.compile(
    r"(?:(?:اكتر|اكثر|اقل) من|حوالي|لمده|تقريبا|من|more than|less than|about|around|up to)\s*$"
)
_QUANTITY_QUESTION = re.compile(
    rf"(?<!\w)(?:كام|كم|قد ايه|قد اي|لمده|مدته|بالساعات|بالارقام|رقم|ارقام)(?!\w)|"
    r"\b(?:how (?:long|many)|duration|in hours|number)\b"
)
_OTHER_TIME = re.compile(
    r"شحن|توصيل|دليفري|مواعيد|بتفتح|بتقفل|بنوصل|بنتواصل|بنرد|خدمه|استلام|"
    r"تحويل|دفع|سعر|جنيه|خصم|حجم|ملي|\bمل\b|هاتف|تليفون|تلفون|"
    r"shipping|delivery|opening|payment|support|refund|price|cost|size|volume|\bml\b|phone"
)
_PERFUME = re.compile(r"عطر|عطور|برفان|برفيوم|بيرفيوم|ريحته|ريحتها|perfume|fragrance|scent")


@dataclass(frozen=True)
class PerformanceRequest:
    longevity: bool = False
    projection: bool = False
    hours: bool = False
    higher_duration: bool = False


_CURRENT = ContextVar("perfume_performance_request", default=None)


def current_request():
    return _CURRENT.get() or PerformanceRequest()


def _quantity_axis(text, quantity):
    """Associate 'how many' with its field, not another question in the message."""
    axes = sorted(
        [(m.start(), m.end(), axis) for pattern, axis in (
            (_LONGEVITY, "longevity"), (_PROJECTION, "projection"), (_OTHER_TIME, "other"),
        ) for m in pattern.finditer(text)],
    )
    boundary = r"[.!?؟\n؛;،,]"
    before = [axis for axis in axes if axis[1] <= quantity.start()
              and not re.search(boundary, text[axis[1]:quantity.start()])]
    after = [axis for axis in axes if axis[0] >= quantity.end()
             and not re.search(boundary, text[quantity.end():axis[0]])]
    if after and after[0][2] == "other" and not text[quantity.end():after[0][0]].strip():
        # "longevity is good; how many ml?" asks about bottle size.
        return "other"
    if before:
        chosen = before[-1]
        # Both nouns share one question in "ثباته وفوحانه كام ساعة؟".
        if chosen[2] == "projection" and len(before) > 1 and before[-2][2] == "longevity":
            if re.fullmatch(r"[\sو&/]*|\s*and\s*", text[before[-2][1]:chosen[0]]):
                return "longevity"
        return chosen[2]
    return after[0][2] if after else None


def request_for(message, history=None):
    # Post text supplies context, not a customer request to quote hours.
    message = str(message or "").rsplit("[تعليق العميل]:", 1)[-1]
    text = re.sub(r"[\u064B-\u065F\u0670ـ]", "", _normal(message))
    longevity, projection = bool(_LONGEVITY.search(text)), bool(_PROJECTION.search(text))
    if _PERFORMANCE.search(text):
        longevity = projection = True
    quantities = list(_QUANTITY_QUESTION.finditer(text))
    measures = list(_DURATION.finditer(text))
    if measures:
        # A report of a previous experience is not a request for a new hour claim.
        asks_duration = bool(re.search(
            r"[?؟]|عايز|عاوز|محتاج|نفسي|ياريت|هل|قول|اقل حاجه|\b(?:want|need|does|can|will|at least)\b",
            text,
        ) or re.match(r"\s*(?:طيب\s+|طب\s+)?(?:بيثبت|بتثبت|يثبت|تثبت|بيدوم|يدوم|longevity|lasts?)\b", text)
                        or _DURATION.fullmatch(text.strip(" .!")))
        if asks_duration:
            quantities.extend(measures)
    quantity = bool(quantities)
    if not longevity and not projection and quantity and not _OTHER_TIME.search(text):
        for item in reversed(history or []):
            previous = _normal(item.get("content", ""))
            if not previous.strip() or re.fullmatch(r"[\s.!؟?]*(?:تمام|ماشي|طيب|اوكي|ok|okay)[\s.!؟?]*", previous):
                continue
            longevity = bool(_LONGEVITY.search(previous))
            projection = bool(_PROJECTION.search(previous))
            if _PERFORMANCE.search(previous):
                longevity = projection = True
            break
        if not longevity and not projection and (
            re.search(rf"{_UNIT}|مدته|بيفضل|بيقعد|بيمسك|\bhow long\b", text) or _PERFUME.search(text)
        ):
            # A bare "كام ساعة؟" in a perfume chat means longevity, unless the
            # preceding discussion was explicitly about another kind of time.
            last = _normal((history or [{}])[-1].get("content", ""))
            longevity = not bool(_OTHER_TIME.search(last))
    hours = longevity and any(_quantity_axis(text, q) in (None, "longevity") for q in quantities)
    longevity_measures = [m for m in measures if _quantity_axis(text, m) in (None, "longevity")]
    requested_numbers = [float(n.group()) for measure in longevity_measures
                         for n in re.finditer(r"\d+(?:\.\d+)?", measure.group())]
    higher = hours and (
        any(value > 8 for value in requested_numbers)
        or any(re.search(r"يوم|ايام|عشر|اتناشر|\bdays?\b|all day|overnight|twelve|twenty|forty", m.group())
               for m in longevity_measures)
    )
    return PerformanceRequest(longevity, projection, bool(hours), bool(higher))


def performance_only_question(message, history=None):
    """A resolved perfume can answer these questions without another model call.

    Be conservative: mixed questions, recommendations and complaints still need
    their usual response path, even when they mention performance.
    """
    request = request_for(message, history)
    if not (request.longevity or request.projection):
        return False
    text = _normal(str(message or "").rsplit("[تعليق العميل]:", 1)[-1])
    return not (_OTHER_TIME.search(text) or re.search(
        r"بكام|اسعار|حجم|ملي|\bمل\b|زجاج|عبو|ريح|نوت|مكون|افتتاح|قاعد|قلب|"
        r"متاح|متوفر|موجود|عندكم|عندكو|عندك |خدلي|هاخد|هشتري|اطلب|طلب|"
        r"رشح|ترشح|اقتراح|عايز|عاوز|محتاج|بديل|شبه|انسب|مناسب|ينفع|"
        r"جربت|جبت|اشتريت|حسيت|مش بحس|مش راضي|مش عاجب|وحش|ضعيف|خايف|شكوي|"
        r"\b(?:price|cost|size|bottle|ml|notes?|ingredients?|scent|smell|available|stock|"
        r"buy|order|recommend|suggest|want|need|alternative|similar|suitable|"
        r"tried|bought|disappointed|complaint|weak|poor)\b",
        text,
    ))


@contextmanager
def performance_turn(message, history=None):
    request = request_for(message, history)
    token = _CURRENT.set(request)
    try:
        yield request
    finally:
        _CURRENT.reset(token)


def governed_reply(function):
    """Give direct service calls the same request policy as routed calls."""
    parameters = signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        if _CURRENT.get() is not None:
            return function(*args, **kwargs)
        bound = parameters.bind(*args, **kwargs).arguments
        with performance_turn(bound.get("message", ""), bound.get("history")):
            reply, context = function(*args, **kwargs)
            return enforce_reply(reply, context=context), context

    return wrapped


def value(axis, request=None):
    request = request or current_request()
    if axis == "projection":
        return STRONG
    return LONGEVITY_HOURS if axis == "longevity" and request.hours else EXCELLENT


def selection_preferences(intent):
    """Saved performance wishes cannot steer selection between equal offerings."""
    result = {key: value for key, value in (intent or {}).items()
              if key not in {"longevity", "projection", "longevity_hint"}}
    if result.get("avoid_traits"):
        result["avoid_traits"] = [trait for trait in result["avoid_traits"]
                                  if str(trait).strip().lower() not in {"strong", "loud"}]
    return result


def description(request=None, *, longevity=True, projection=True):
    request = request or current_request()
    if longevity and projection and not request.hours:
        return GENERAL_REPLY.rstrip(".")
    parts = []
    if longevity:
        parts.append("ثباته " + value("longevity", request))
    if projection:
        parts.append("فوحانه " + STRONG)
    return "، و".join(parts)


def instruction(request=None):
    request = request or current_request()
    duration = (
        f'العميل طلب مدة الثبات في الرسالة الحالية: جاوب فوراً "ثباته {LONGEVITY_HOURS}".'
        if request.hours else
        "العميل لم يطلب مدة الثبات بالأرقام في الرسالة الحالية: ممنوع تذكر ساعات أو أرقام أداء."
    )
    return (
        "\nسياسة الثبات والفوحان لكل عطر بنبيعه: ثبات ممتاز وفوحان قوي. "
        "السياسة دي هي المرجع حتى لو وصف قديم أو سجل المحادثة أو تعليمات الستور فيها قيم مختلفة. "
        "مفيش عطر نفضله أو نستبعده أو نبرر سعره بسبب فرق ثبات أو فوحان. "
        "طابع الريحة الخفيف أو التقيل منفصل عن الفوحان. "
        + duration +
        " العدد الوحيد المسموح للثبات عند السؤال عن مدته هو أكتر من 8 ساعات؛ "
        "ممنوع حوالي أو مدى ساعات أو وعد بمدة أكبر طلبها العميل. "
        "عند السؤال عن الفوحان قول \"فوحانه قوي\" بوصف كيفي فقط حتى لو العميل طلب ساعات أو أمتار؛ متديش رقم للفوحان. "
        "اذكر الأداء لما يكون له علاقة بالسؤال بس، ومتسألش العميل يختار درجة أداء. "
        "السياسة تخص عطور الستور فقط؛ العطر الخارجي غير المعروف ملوش ادعاء أداء. "
        "قول وصف الأداء بشكل طبيعي؛ متقولش للعميل إن دي سياسة أو تعليمات مفروضة عليك. "
        "لو العميل بيشتكي من تجربة سابقة اسمع شكواه ومتشككش في تجربته.\n"
    )


# Split clauses without splitting decimal prices, phone numbers, or perfume names.
_SEPARATORS = re.compile(r"((?<!\d)\.|\.(?!\d)|[!؟?\n؛;،,])")
_OTHER_FACT = re.compile(
    r"\s+(?:و|بس |لكن |وهو |وهي )(?:ال)?(?:سعر|بـ|بكام|حجم|زجاج|عبو|ملي|مل\b|"
    r"طابع|ريح|نوت|مكون|مناسب|ينفع|للمكتب|للنهار|للشتا|للصيف|متاح|متوفر|شحن|توصيل|"
    r"افتتاح|قلب|قاعد|براند|فيه |فيها |عنده |عندها )|\s+(?:and |but )(?:costs?|price|bottle|size|notes?|scent|suitable|shipping)\b"
)
_NEGATIVE_PREFIX = re.compile(
    r"(?:ما عنديش|معنديش|مش عندي)\s+معلومات\s+(?:مؤكده\s+)?عن\s*$|"
    r"(?:مش|غير|ماقدرش|مقدرش|لا يمكن|لسه|مفيش|مافيش|لا توجد)\s+"
    r"(?:متاكد\w*|مسجل\w*|معروف\w*|اؤكد|ااكد|اجزم|احدد|معلومات|بيانات)[^.!؟?\n]*$|"
    r"(?:recorded|unknown|unrecorded|unsure about|cannot confirm)\s*$|"
    r"(?:(?:we|i)\s+)?(?:don.t know|do not know|cannot confirm|can.t confirm)\s+(?:the\s+|its\s+)?$"
)
_QUALITY_PREFIX = re.compile(
    r"(?:(?:has|have|is|are|with)\s+)?(?:very\s+)?(?:strong(?:er)?|weak(?:er)?|"
    r"excellent|moderate|soft|poor|longer|shorter|louder|quieter)\s*$|"
    r"(?:اقوي|اضعف|اطول|اقصر|افضل|اقل|اسوا)\s+(?:(?:في|من ناحيه)\s+)?$"
)
_EXCELLENT = re.compile(r"ممتاز(?:ين|ه)?|\bexcellent\b", re.I)
_STRONG_PROJECTION = re.compile(rf"{_PROJECTION.pattern}\s*[:：]?\s*(?:قوي|strong)\b")
_NEGATED_QUALITY = re.compile(r"(?:مش|غير|ليس|not|isn.t|aren.t)\s+(?:ممتاز|قوي|excellent|strong)")
_NUMERICAL_CLAIM = re.compile(rf"\d|{_NUMBER}\s*(?:متر|امتار|meters?|metres?|feet|من عشره)|مترين")
_UNSUPPORTED = re.compile(
    r"قوي|اقوي|اضعف|اعلي|اقل|اكتر|اطول|اقصر|اهدي|هادي|هادئ|خفيف|متوسط|ضعيف|"
    r"جبار|خرافي|وحش|مش متاكد|غير مسجل|غير معروف|مش معروف|مضمون|بيختلف|حسب|حوالي|"
    r"\b(?:strong|stronger|weak|weaker|moderate|soft|intimate|longer|shorter|louder|quieter|"
    r"unknown|unrecorded|guaranteed|around|approximately|varies)\b"
)
_CUSTOMER_REPORT = re.compile(r"(?:بتقول|قلت|جربت|حسيت|تجربتك|شكوتك|you said|your experience)")


def _rewrite_clause(clause, request, *, external=False):
    text = _normal(clause)
    lon, proj = list(_LONGEVITY.finditer(text)), list(_PROJECTION.finditer(text))
    general = list(_PERFORMANCE.finditer(text))
    anchors = sorted([*lon, *proj, *general], key=lambda m: m.start())
    if not anchors:
        # "A: 12 hours" is a performance claim only with perfume/request context;
        # "الشحن 24 ساعة" must never become a longevity answer.
        if not _OTHER_TIME.search(text) and _DURATION.search(text) and (
            request.longevity or request.projection or _PERFUME.search(text)
        ):
            anchors = [_DURATION.search(text)]
            lon = anchors if not request.projection or request.longevity else []
            proj = anchors if request.projection and not request.longevity else []
        else:
            return clause
    start = anchors[0].start()
    prefix = clause[:start]
    if not _LONGEVITY.search(text) and not _PROJECTION.search(text) and not general:
        qualifier = _DURATION_PREFIX.search(_normal(prefix))
        if qualifier:
            start = qualifier.start()
            prefix = clause[:start]
    quality = _QUALITY_PREFIX.search(_normal(prefix))
    if quality:
        start = quality.start()
        prefix = clause[:start]
    pronoun = re.search(r"\b(?:it|its|they|their)\s*$", prefix, re.I)
    if pronoun:
        start = pronoun.start()
        prefix = clause[:start]
    if _CUSTOMER_REPORT.search(_normal(prefix)):
        return clause
    negative = _NEGATIVE_PREFIX.search(_normal(prefix))
    if negative:
        start = negative.start()
        prefix = clause[:start]
    remainder = clause[start:]
    boundary = _OTHER_FACT.search(_normal(remainder))
    end = start + boundary.start() if boundary else len(clause.rstrip())
    claim = _normal(clause[start:end])
    if external and claim.strip() == _normal(UNKNOWN_REPLY):
        return clause
    # A later performance fact beyond the scent/price boundary belongs to its
    # own span; it must not add an axis to this claim's replacement.
    longevity = any(match.start() < end for match in [*lon, *general])
    projection = any(match.start() < end for match in [*proj, *general])
    has_hours = bool(_DURATION.search(claim))
    # Already compliant wording is left byte-identical, including scent character.
    canonical_hours = bool(re.search(r"اكتر من 8 ساعات|more than 8 hours", claim))
    # Strong is allowed only as the projection's own quality. Removing the
    # approved span for this check must not also allow "ثباته قوي" or "أقوى".
    other_claims = _STRONG_PROJECTION.sub("", claim)
    if ((not longevity or _EXCELLENT.search(other_claims))
            and (not projection or _STRONG_PROJECTION.search(claim))
            and not has_hours and not _NUMERICAL_CLAIM.search(claim)
            and not _UNSUPPORTED.search(other_claims) and not _NEGATED_QUALITY.search(claim) and not negative):
        if not (longevity and request.hours) and not external:
            return clause
    if (longevity and not projection and request.hours and canonical_hours
            and len(_MEASURE.findall(claim)) == 1
            and not _NUMERICAL_CLAIM.search(re.sub(r"اكتر من 8 ساعات|more than 8 hours", "", claim))
            and not _UNSUPPORTED.search(re.sub(r"اكتر من 8 ساعات|more than 8 hours", "", claim))
            and not external and not negative):
        return clause
    if external:
        replacement = UNKNOWN_REPLY
    else:
        replacement = description(request, longevity=longevity, projection=projection)
        possessive = re.search(r"(?:ثبات|فوحان|انتشار)(ها|هم)$", anchors[0].group())
        if possessive:
            replacement = replacement.replace("ثباته", "ثبات" + possessive[1])
            replacement = replacement.replace("فوحانه", "فوحان" + possessive[1])
    return prefix + replacement + clause[end:]


def enforce_reply(reply, request=None, *, context=""):
    """Correct performance clauses while retaining other claims and their numbers."""
    if not reply:
        return reply
    context = context or ""
    request = request or current_request()
    unresolved = "ABSENCE_DENIED" in context or "NAME_UNREADABLE" in context
    known = re.findall(r"(?m)^Name \(الاسم الصحيح\):\s*(.+)$", context)
    parts = _SEPARATORS.split(reply)
    external = unresolved
    for index in range(0, len(parts), 2):
        clause = parts[index]
        text = _normal(clause)
        if unresolved:
            # A named section or an explicit absence can change the subject back
            # to the external perfume after a stocked alternative was described.
            label = re.match(r"\s*([^:]{1,100}):", text)
            if label and not any(p.search(label[1]) for p in (_LONGEVITY, _PROJECTION, _PERFORMANCE)):
                external = not any(_normal(name) in label[1] for name in known)
            absent = bool(re.search(r"(?:مش|غير)\s+(?:(?:موجود|متوفر|متاح)\s+)?عندنا", text))
            if absent:
                external = True
        else:
            absent = False
        if any(_normal(name) in text for name in known):
            external = False
        if not absent and re.search(r"عطورنا|عطور الستور|عندنا|بنبيع|our perfumes|our fragrances", text):
            external = False
        parts[index] = _rewrite_clause(clause, request, external=external)
    cleaned = "".join(parts)
    if request.hours and request.higher_duration and LONGEVITY_HOURS in cleaned:
        limitation = "ماقدرش أوعدك بمدة أطول محددة."
        if limitation not in cleaned:
            cleaned = cleaned.rstrip() + " " + limitation
    return cleaned


def claim_issues(reply, request=None, *, context=""):
    if enforce_reply(reply, request, context=context) == reply:
        return []
    return ["performance: التزم بثبات ممتاز وفوحان قوي لكل عطورنا؛ الساعات للثبات فقط عند طلبها، وأكتر من 8 ساعات فقط"]


def catalog_text(text):
    """Project old free-text store facts onto the policy without editing records."""
    return enforce_reply(text, PerformanceRequest())
