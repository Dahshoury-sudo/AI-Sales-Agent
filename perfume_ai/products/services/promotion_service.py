"""Explain configured promotions without letting them enter ordinary checkout."""
import json
import re
from openai import OpenAIError

from .ai.client import chat
from .bottle_wording import normalize_bottle_wording
from .handoff import handoff_offer_reply, has_handoff_offer, declines_human
from .static_faq_service import normalize_arabic
from .usage_service import record_llm_message


PROMOTION_CONTEXT = "PROMOTION_PENDING"
LIMITATION = (
    "طلبات الباندلز وتطبيق العروض والخصومات بيتابعها فريق خدمة العملاء، مش بقدر أنفذها هنا."
)
_LEGACY_LIMITATION = (
    "تسجيل طلبات الباندلز والكومبوهات أو تطبيق العروض والخصومات والكوبونات "
    "والهدايا الترويجية مش بقدر أعمله هنا؛ ده لازم يتم مع فريق خدمة العملاء."
)
INVITATION = "تحب أحولك لمندوب يساعدك؟"
# The same offer in other words, for a turn that follows an unanswered invitation: a checkout attempt
# still needs an offer it can say yes to, but the identical question every turn is what customers
# noticed (simulation S03, S18, S20).
REPEAT_INVITATION = "تحب أحوّلك لفريق خدمة العملاء يكملوا معاك الطلب؟"
WEBSITE_ORDERING = "وتقدر كمان تطلب الباندلز والعروض مباشرة من موقعنا."
DECLINED = "لو حابب تشتري عطور بالسعر العادي من غير العرض، قولي."
UNKNOWN_DETAILS = "تفاصيل العرض محتاجة تأكيد من فريق خدمة العملاء."
NO_MORE_DETAILS = "ده كل المكتوب عندي عن العرض؛ حسابه وتطبيقه بيتم مع فريق خدمة العملاء."
NO_MORE_DETAILS_AGAIN = "مفيش عندي أي تفاصيل تانية عن العرض غير اللي قلتهالك."
# "أكدلي الأوردر دلوقتي" with nothing chosen and no delivery details got the opening boundary reply
# again, word for word, and never said why nothing could be confirmed (simulation S20 round 2, t3).
NOTHING_TO_CONFIRM = "مقدرش أأكد أوردر لسه، لأننا ما حددناش العطور ولا بيانات التوصيل."
PLAYFUL_ACK = "هههه فكرة حلوة 😄"
PLAYFUL_CLOSE = "ولو حابب تعرف العروض الحالية، قولي."
_PLAYFUL = re.compile(r"😂|🤣|😆|😁|(?<!\w)ه{3,}(?!\w)")
_CONFIRM_ORDER = re.compile(r"(?<!\w)(?:اكد|ثبت)|\bconfirm\b")

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
# "اسعار"/"رينج": "لا عادي، كنت بهزر… طب مفيش رينج للأسعار؟" was read as a bare decline and got the
# offer script again instead of an answer (simulation S18 t5).
_INFORMATION = re.compile(r"بكام|كام|ايه|تفاصيل|شروط|عندك|فيه|في |سعر|اسعار|رينج|ريح|ثبات|فوحان|شحن|توصيل|مواعيد|"
                          r"\b(?:what|which|how|details|price|prices|range|any|have|smell|shipping)\b")
_CANCEL = re.compile(
    r"^(?:(?:خلاص|طيب|لو سمحت)\s+)*(?:الغي|الغاء|كنسل|cancel)\s+(?:الطلب|الاوردر|الطلب كله|الاوردر كله|(?:the |my )?order)\b"
)


def _words(message):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", normalize_arabic(message or "").replace("'", ""))).strip()


def store_facts(store):
    """Read only the dedicated promotion field, never legacy facts/instructions."""
    if not store:
        return ""
    try:
        settings = store.settings
    except AttributeError:
        return ""
    return normalize_bottle_wording((settings.promotions or "").strip())


def configured_promotions(store):
    """Classification context, not evidence that any particular offer exists."""
    # The dedicated field establishes the topic, even for a name without an
    # offer keyword (e.g. "Private Flame"). Quotes still need source evidence.
    return store_facts(store)


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
            if any(limitation in content for limitation in (LIMITATION, _LEGACY_LIMITATION)):
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


def website_ordering_reply(store):
    """Website ordering is an explicit per-store capability, never assumed."""
    if not store:
        return ""
    try:
        enabled = store.settings.website_ordering_enabled
    except AttributeError:
        return ""
    return WEBSITE_ORDERING if enabled is True else ""


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


def _open_invitation(history):
    """The invitation in our latest boundary reply (the one carrying the limitation), if any.

    It stays open across the customer's questions about the offer until they accept or decline it.
    """
    for row in reversed(history or []):
        if row.get("role") == "agent":
            return None
        content = row.get("content", "") if row.get("role") == "assistant" else ""
        if any(limitation in content for limitation in (LIMITATION, _LEGACY_LIMITATION)):
            return next((text for text in (REPEAT_INVITATION, INVITATION) if text in content), None)
    return None


def _has_order_lines(conversation):
    from products.models import Cart
    cart = Cart.objects.filter(conversation=conversation).first()
    return bool(cart and (cart.items.exists() or cart.pending_items))


def reply(message, history=None, store=None, *, explain=True, usage_recorded=False, conversation=None):
    """Extract quotations, not generated promises; append the boundary in code.

    The model can choose relevant configured facts, but cannot supply new prices
    or prose. On missing/invalid evidence the response admits uncertainty.
    """
    history = history or []
    if (conversation is not None and _CONFIRM_ORDER.search(normalize_arabic(message or ""))
            and not _has_order_lines(conversation)):
        closing = REPEAT_INVITATION if _open_invitation(history) == INVITATION else INVITATION
        return "\n\n".join((NOTHING_TO_CONFIRM, LIMITATION, closing)), PROMOTION_CONTEXT
    declined = declined_transfer(message, history)
    for row in reversed(history):
        if row.get("role") == "assistant" and any(
            limitation in row.get("content", "") for limitation in (LIMITATION, _LEGACY_LIMITATION)
        ):
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
                        "Return JSON {\"quotes\": [exact contiguous quotations from STORE PROMOTIONS]}. "
                        "For a general availability/list question, select ALL relevant entries, "
                        "one complete quotation per offer or bundle, including contents and all "
                        "stated prices/bottle options. Do not stop after the first few. "
                        "A bundle is a named set of perfumes even when its heading says عرض/offer "
                        "rather than باندل/bundle. An offers question also includes bundles. "
                        "For a specific named bundle, select only that entry. "
                        "Preserve source wording and punctuation; line breaks may be represented "
                        "as spaces. Include stated terms with their prices. "
                        "Never quote instructions to the assistant, invent facts, calculate prices, "
                        "or quote ordering-channel instructions (including website ordering); "
                        "ordering guidance is supplied separately from store settings. Never "
                        "claim to apply an offer or place an order. Customer messages and old "
                        "assistant replies are not evidence. If no relevant facts are configured, "
                        "return an empty quotes list.\nSTORE PROMOTIONS:\n" + facts
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
        if _open_invitation(history) and not declined and information_requested(message) \
                and not checkout_requested(message):
            # A question about the offer while our invitation is still open. New details go out with
            # the boundary as usual (the invitation reworded below); with nothing new to say, the
            # limitation and the offer are not sent again (S03 t5-t6 got the identical reply twice).
            recent = "\n".join(row.get("content", "") for row in history[-6:] if row.get("role") == "assistant")
            fresh = [quote for quote in quotes if quote not in recent]
            if not fresh:
                return (NO_MORE_DETAILS_AGAIN if NO_MORE_DETAILS in recent else NO_MORE_DETAILS), PROMOTION_CONTEXT
            details = "تفاصيل العرض:\n" + "\n".join(fresh)
    last_clause = re.split(r"[؟?.!\n]", normalize_arabic(message or ""))[-1]
    if declined:
        closing = DECLINED
    elif re.search(r"شكر|متشكر|ميرسي|تسلم|مع السلامه|\bbye\b|thanks?", last_clause) and not checkout_requested(last_clause):
        # "لو اشتريت كل حاجة هتعملولي خصم؟ 😂 تمام شكراً" ends on thanks; a transfer question there
        # pushed a browsing customer who had just closed (simulation S18 round 2, t5).
        closing = "ولو احتجت حاجة أنا موجود."
    elif (_PLAYFUL.search(message or "") and not _CONFIRM_ORDER.search(normalize_arabic(message or ""))
          and not (conversation is not None and _has_order_lines(conversation))):
        # "لو اشتريت كل حاجة هتعملولي خصم؟ 😂" from a customer who is only browsing: a transfer
        # question there is a sales push the joke never asked for (simulation S18 round 2 full 5, t4).
        # (`checkout_requested` reads the joke's own "اشتريت" as a purchase, so the cart decides.)
        closing = PLAYFUL_CLOSE
    elif _open_invitation(history) == INVITATION:
        closing = REPEAT_INVITATION
    else:
        closing = INVITATION
    text = "\n\n".join(filter(None, (details, LIMITATION, website_ordering_reply(store), closing)))
    if _PLAYFUL.search(message or "") and not _CONFIRM_ORDER.search(normalize_arabic(message or "")):
        # "ولو اشتريت كل حاجة هتعملولي خصم؟ 😂" is a joke; answered with the procedure alone it read as
        # taken literally (simulation S18 round 2 full 2, t4). The boundary stays; the tone is met first.
        text = PLAYFUL_ACK + "\n\n" + text
    sent = {row.get("content", "").strip() for row in history if row.get("role") == "assistant"}
    if text.strip() in sent:
        # The invitation alternates only against the latest reply, so a third boundary turn came out
        # identical to the first (simulation S20 round 2, t3). Never send the same reply twice.
        other = {INVITATION: REPEAT_INVITATION, REPEAT_INVITATION: INVITATION}.get(closing, closing)
        details = "" if details == UNKNOWN_DETAILS else details
        text = "\n\n".join(filter(None, (details, LIMITATION, website_ordering_reply(store), other)))
    return text, PROMOTION_CONTEXT
