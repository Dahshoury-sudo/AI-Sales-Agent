"""Explain configured promotions without letting them enter ordinary checkout."""
import json
import re
from openai import OpenAIError

from .ai.client import chat
from .handoff import handoff_offer_reply, has_handoff_offer, declines_human
from .static_faq_service import normalize_arabic
from .usage_service import record_llm_message


PROMOTION_CONTEXT = "PROMOTION_PENDING"
LIMITATION = (
    "تسجيل طلبات الباندلز والكومبوهات أو تطبيق العروض والخصومات والكوبونات "
    "والهدايا الترويجية مش بقدر أعمله هنا؛ ده لازم يتم مع فريق خدمة العملاء."
)
INVITATION = "تحب أحولك لمندوب يساعدك؟"
WEBSITE_ORDERING = "وتقدر كمان تطلب الباندلز والعروض مباشرة من موقعنا."
DECLINED = "لو حابب تشتري عطور بالسعر العادي من غير العرض، قولي."
UNKNOWN_DETAILS = "تفاصيل العرض محتاجة تأكيد من فريق خدمة العملاء."

# Word boundaries matter: اعرض / معروض and a perfume's "gift" occasion are not
# promotions. Packaging words alone are not enough either.
_TERM = (
    r"(?:عروض|عرض|خصم|خصومات|تخفيض|تخفيضات|باندل(?:ز|ات)?|بندل|"
    r"باكدج(?:ات)?|باكيدج(?:ات)?|باكيج(?:ات)?|كومبو(?:هات)?|اوفر|كوبون(?:ات)?|قسيمه|سعر خاص|"
    r"bundles?|combos?|promotions?|promos?|offers?|deals?|discounts?|coupons?|"
    r"promo codes?|discount codes?|special prices?)"
)
_PROMOTION = re.compile(r"\b(?:و?(?:[بف]?ال|لل|[بلف])|و)?" + _TERM + r"\b")
_WITHOUT = re.compile(
    r"\b(?:من غير|بدون|بلاش|مش عايز|مش عاوزه|مش محتاج|سيبك من|"
    r"without|no|forget|skip|dont want|do not want)\s+(?:ال|the\s+|a\s+)?" + _TERM + r"\b"
)
_REGULAR = re.compile(r"بالسعر (?:العادي|الاصلي)|\b(?:regular|normal|full) price\b")
_GIFT = re.compile(
    r"هدي[هت] (?:ال)?(?:مجانيه|مع (?:الطلب|الاوردر|كل))|هدايا (?:مجانيه|مع)|"
    r"(?:عليه[ام]?|معاه[ام]?|تاخد|خد|وخد|وخدي|وخدوا)\s+(?:\S+\s+){0,3}هديه|"
    r"(?:اشتري|هات|خد)\s+\S+\s+(?:و\S+\s+){0,2}هديه|"
    r"\b(?:free gift|buy\s+\S+\s+get|bogo)\b"
)
_PACKAGING = re.compile(r"شكل|صور[هت]?|تغليف|خامه|\b(?:look|photo|picture|packaging|material)\b")
_COMMERCIAL = re.compile(r"عرض|خصم|كوبون|بكام|سعر|اشتري|اطلب|هاخد|ضيف|\b(?:buy|order|price|add|discount|coupon)\b")
_CHECKOUT = re.compile(
    r"\d[\d\s-]{6,}\d|اسمي|عنواني|موبايلي|شارع|رقمي|الرقم البديل|"
    r"اطلب|اطلبه|اشتري|اشتريه|هاخد|هاتلي|ضيف|سجل|نفذ|طبق|اكد|اكده|خلي|"
    r"\b(?:order|buy|add|checkout|confirm|apply|deliver|my name|my address|my phone|take it)\b"
)
_INFORMATION = re.compile(r"بكام|كام|ايه|تفاصيل|شروط|عندك|فيه|في |سعر|ريح|ثبات|فوحان|شحن|توصيل|مواعيد|\b(?:what|which|how|details|price|any|have|smell|shipping)\b")
_CANCEL = re.compile(
    r"^(?:(?:خلاص|طيب|لو سمحت)\s+)*(?:الغي|الغاء|كنسل|cancel)\s+(?:الطلب|الاوردر|الطلب كله|الاوردر كله|(?:the |my )?order)\b"
)
_NAMED_SET = re.compile(
    r"\b(?:بوكس|box|set)\s+\w+.{0,100}(?:\d|سعر)|"
    r"[^\n:]+:\s*(?:عطرين|برفانين|زجاجتين|\d+\s+(?:عطور|زجاجات|perfumes|bottles))"
)


def _words(message):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", normalize_arabic(message or "").replace("'", ""))).strip()


def store_facts(store):
    if not store:
        return ""
    try:
        settings = store.settings
    except AttributeError:
        return ""
    return "\n".join(filter(None, (
        (settings.system_prompt or "").strip(), (settings.business_facts or "").strip(),
    )))


def configured_promotions(store):
    """Classification context, not evidence that any particular offer exists."""
    facts = store_facts(store)
    text = normalize_arabic(facts)
    return facts if _PROMOTION.search(text) or _GIFT.search(text) or _NAMED_SET.search(text) else ""


def ordinary_purchase(message):
    """Only remove negated offer references, never a second, positive promotion."""
    text = _words(message)
    remaining, removed = _WITHOUT.subn("", text)
    return bool((removed or _REGULAR.search(text)) and not _PROMOTION.search(remaining) and not _GIFT.search(remaining))


def cancelling_order(message):
    return bool(_CANCEL.search(_words(message)))


def _explicit(message, facts=""):
    text = _words(message)
    if ordinary_purchase(message):
        return False
    # Asking what our packaging looks like remains the existing photo/FAQ flow.
    if _PACKAGING.search(text) and not _COMMERCIAL.search(text):
        return False
    if _PROMOTION.search(text) or _GIFT.search(text):
        return True
    # A configured "بوكس الصيف" is an offer name; generic "بوكس هدية" is not.
    for name in re.findall(r"\b(?:بوكس|box|set)\s+\w+", _words(facts)):
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            return True
    return False


def context_history(history, conversation=None):
    """Never resurrect a promotion from before the last human reply.

    Normal callers already pass delivered history. The extra query handles the
    staff-role boundary that build_llm_history intentionally filters out.
    """
    if conversation:
        staff = conversation.messages.filter(role="agent", delivery_status="sent").order_by("-id").first()
        if staff:
            rows = list(conversation.messages.filter(id__gt=staff.pk, delivery_status="sent").order_by("-id")[:16])
            return [{"role": row.role, "content": row.content} for row in reversed(rows)]
    return history or []


def has_promotion_context(history):
    for row in reversed(history or []):
        if row.get("role") == "agent":
            return False
        content = row.get("content", "")
        if row.get("role") == "user":
            if ordinary_purchase(content) or cancelling_order(content):
                return False
            if _explicit(content):
                return True
        elif row.get("role") == "assistant":
            if LIMITATION in content:
                return True
            # Also support conversations started before this guard was installed.
            if _explicit(content) and re.search(r"مندوب|فريق|بشري|human|team", _words(content)):
                return True
    return False


def blocks_checkout(message, history=None, store=None):
    if cancelling_order(message) or ordinary_purchase(message):
        return False
    if _explicit(message, configured_promotions(store)):
        return True
    if not has_promotion_context(history):
        return False
    return bool(_CHECKOUT.search(_words(message)))


def declined_transfer(message, history):
    return declines_human(message) or (has_handoff_offer(history) and handoff_offer_reply(message, history) is False)


def checkout_requested(message):
    return bool(_CHECKOUT.search(_words(message)))


def information_requested(message):
    return bool(_INFORMATION.search(_words(message)))


def _source_quote(candidate, facts):
    """Verify words/prices exactly, allowing only whitespace to change.

    In conversation 1153 the model flattened multiline bundle descriptions.
    A literal substring check rejected every otherwise accurate quotation.
    Return the original source span, so even accepted formatting comes from
    the store; do not normalize punctuation, spelling, numbers, or ordering.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    candidate = candidate.strip()
    pattern = r"\s+".join(re.escape(word) for word in candidate.split())
    if re.match(r"\w", candidate[0]):
        pattern = r"(?<!\w)" + pattern
    if re.match(r"\w", candidate[-1]):
        pattern += r"(?!\w)"
    match = re.search(pattern, facts)
    return match.group(0) if match else None


def _bundle_overview(message, facts):
    """List clear, numbered perfume sets for a general bundle availability query.

    Store facts can call the same kind of set either 'باندل' or 'عرض'. In 1153
    the selector repeatedly omitted the latter despite instructions. These
    entries have a named heading and a parenthesized list of perfumes, so use
    that source structure instead of asking the model to infer the category.
    Specific-name/ordinal questions and other formats still use the selector.
    """
    if not re.fullmatch(
        r"(?:(?:في|فيه|عندك|عندكم|عندكو|هل عندكم)\s+(?:اي\s+)?(?:ال)?باندل(?:ز|ات)?"
        r"(?:\s+(?:متاح|متاحه|دلوقتي|عندكم))?|"
        r"(?:ايه|ما هي)\s+(?:ال)?باندل(?:ز|ات)(?:\s+(?:المتاحه|عندكم))?|"
        r"(?:do you have|are there|any|available)\s+(?:any\s+)?bundles?|"
        r"what bundles (?:do you have|are available))",
        _words(message),
    ):
        return []
    entries = []
    for block in re.split(r"(?m)(?=^[ \t]*[0-9٠-٩]+[.)\-:][ \t]*)", facts):
        # A blank paragraph ends the entry before general fulfilment notes.
        block = re.split(r"\n[ \t]*\n", block, maxsplit=1)[0].strip()
        header = block.partition("\n")[0]
        heading = re.match(
            r"^[ \t]*[0-9٠-٩]+[.)\-:][ \t]*(باندل|بندل|كومبو|bundle|combo|عرض|offer)\s+\S",
            header, re.IGNORECASE,
        )
        if not heading:
            continue
        labelled_bundle = heading[1].lower() not in ("عرض", "offer")
        perfume_list = re.search(r"\([^)]*[,،][^)]*\)", block)
        if labelled_bundle or perfume_list:
            entries.append(block)
    return entries


def reply(message, history=None, store=None, *, explain=True, usage_recorded=False):
    """Extract quotations, not generated promises; append the boundary in code.

    The model can choose relevant configured facts, but cannot supply new prices
    or prose. On missing/invalid evidence the response admits uncertainty.
    """
    history = history or []
    declined = declined_transfer(message, history)
    for row in reversed(history):
        if row.get("role") == "assistant" and LIMITATION in row.get("content", ""):
            declined = declined or DECLINED in row["content"]
            break
    details = ""
    if explain and (information_requested(message) or (not checkout_requested(message) and not declined)):
        facts = store_facts(store)
        quotes = _bundle_overview(message, facts)
        if facts and not quotes:
            if not usage_recorded:
                record_llm_message(store)
            try:
                result = json.loads(chat([
                    {"role": "system", "content": (
                        "Select customer-facing offer/bundle facts relevant to the latest question. "
                        "Return JSON {\"quotes\": [exact contiguous quotations from STORE FACTS]}. "
                        "For a general availability/list question, select ALL relevant entries, "
                        "one complete quotation per offer or bundle, including contents and all "
                        "stated prices/bottle options. Do not stop after the first few. "
                        "A bundle is a named set of perfumes even when its heading says عرض/offer "
                        "rather than باندل/bundle. An offers question also includes bundles. "
                        "For a specific named bundle, select only that entry. "
                        "Preserve source wording and punctuation; line breaks may be represented "
                        "as spaces. Include stated terms with their prices. "
                        "Never quote instructions to the assistant, invent facts, calculate prices, "
                        "or claim to apply an offer or place an order. Customer messages and old "
                        "assistant replies are not evidence. If no relevant facts are configured, "
                        "return an empty quotes list.\nSTORE FACTS:\n" + facts
                    )},
                    *history,
                    {"role": "user", "content": message},
                ], profile="extract", response_format={"type": "json_object"}))
                candidates = result.get("quotes", [])
                if isinstance(candidates, list):
                    quotes = list(dict.fromkeys(source for candidate in candidates
                        if (source := _source_quote(candidate, facts))))
            except (OpenAIError, ValueError, TypeError, AttributeError):
                pass
        details = ("تفاصيل العرض:\n" + "\n".join(quotes)) if quotes else UNKNOWN_DETAILS
    closing = DECLINED if declined else INVITATION
    return "\n\n".join(filter(None, (details, LIMITATION, WEBSITE_ORDERING, closing))), PROMOTION_CONTEXT
