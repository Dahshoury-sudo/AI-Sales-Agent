"""Strip phrasing the prompt forbids but the model still produces.

`conv_651.txt` closed three separate replies with "تحب تعرف أسعارهم والأحجام؟" — a
question quoted *inside* the persona (prompts.py, PERSONA section) as forbidden. With
dozens of competing absolute rules the model drops some of them, so the ones that are
purely mechanical are enforced here instead of asked for again.

Applied before the reply is saved, not at send time: a banned phrase persisted to
Message flows back through build_llm_history on the next turn and reads to the model
as an example of its own acceptable output.

This deliberately does not add a replacement CTA. The persona allows a reply to end
after answering, and inventing a closer here would put words in the model's mouth that
the product data may not support.
"""

import logging
import re

logger = logging.getLogger(__name__)


# Each pattern matches a whole trailing question, including the whitespace before it,
# so removing it leaves the preceding sentence intact.
BANNED_CLOSERS = (
    # "تحب تعرف الأسعار والأحجام المتاحة؟" / "تحب تعرف أسعارهم والأحجام؟"
    re.compile(
        r"\s*(?:و\s*)?تحب\s+تعرف\s+(?:ال)?[أاإ]?سعار\S*"
        r"(?:\s*و\s*(?:ال)?[أاإ]?حجام\S*)?(?:\s+المتاحة)?\s*[؟?]"
    ),
    # "عايز حاجة تانية؟" / "محتاج مساعدة في حاجة؟" / "محتاج حاجة تانية؟"
    re.compile(r"\s*(?:عايز|محتاج|تحب)\s+(?:حاجة\s+تانية|مساعدة(?:\s+في\s+حاجة)?)\s*[؟?]"),
    # "عطر معين في بالك؟"
    re.compile(r"\s*(?:فيه\s+)?عطر\s+معين\s+في\s+بالك\s*[؟?]"),
)


def sanitize_reply(reply, conversation=None):
    """Remove forbidden filler questions from a generated reply.

    Returns the cleaned text. If a reply is nothing but a banned question, the
    original is kept — sending an empty message is worse than sending a weak one.
    """
    if not reply:
        return reply

    cleaned = reply
    removed = []
    for pattern in BANNED_CLOSERS:
        cleaned, count = pattern.subn("", cleaned)
        if count:
            removed.append(pattern.pattern)

    cleaned = cleaned.strip()

    if not cleaned:
        return reply

    if removed:
        logger.info(
            "Stripped %d banned closer(s) from reply%s. The prompt already forbids "
            "these; frequent hits mean the persona rules are being dropped.",
            len(removed),
            f" (conversation #{conversation.id})" if conversation is not None else "",
        )

    return cleaned


# Closing questions — asking for the order. Legitimate at the right moment and premature
# everywhere else, so unlike BANNED_CLOSERS these are NOT stripped by sanitize_reply.
# strip_premature_closing is called per-branch, only where the sales stage says the
# customer has not earned a close yet: still comparing, still objecting, still trying to
# remember a name. sanitize_reply must stay byte-identical for a legitimate close —
# "الـ 90 ملي أوفر بكتير. أجيبلك الـ 90 ولا الـ 50؟" is pinned as passing through untouched.
PREMATURE_CLOSERS = (
    # "تحب أساعدك في الطلب؟" / "تحب اساعدك في الاوردر؟"
    re.compile(r"\s*(?:و\s*)?تحب\s+[أاإ]?ساعدك\s+في\s+(?:ال)?(?:طلب|اوردر|أوردر)\S*\s*[؟?]"),
    # "تحب تطلب؟" / "تحب تطلبه؟" / "تحب نطلبه؟"
    re.compile(r"\s*(?:و\s*)?تحب\s+[نت]طلب\S*\s*[؟?]"),
    # "أجيبلك الـ90 ولا الـ50؟" — a size close.
    re.compile(r"\s*[أا]جيبلك\s+(?:الـ?\s*)?\d+\s*(?:ملي)?\s*ولا\s+(?:الـ?\s*)?\d+\s*(?:ملي)?\s*[؟?]"),
    # "نسجل الطلب؟" / "نكمل الاوردر؟"
    re.compile(r"\s*(?:و\s*)?ن(?:سجل|كمل)\s+(?:ال)?(?:طلب|اوردر|أوردر)\S*\s*[؟?]"),
    # "تحب نكمل الطلب؟"
    re.compile(r"\s*(?:و\s*)?تحب\s+نكمل\S*\s*[؟?]"),
)


def strip_premature_closing(reply, stage=None):
    """Remove an order-closing question the current stage has not earned.

    Enforced here rather than asked for in the persona because the persona already asks:
    it says to close only when the customer is clearly buying, and the bot closed three
    replies in a row anyway. A stage that permits closing leaves the reply untouched.

    As with sanitize_reply, a reply that is *nothing but* a closing question is kept —
    sending an empty message is worse than sending a premature one.
    """
    if not reply:
        return reply

    cleaned = reply
    removed = 0
    for pattern in PREMATURE_CLOSERS:
        cleaned, count = pattern.subn("", cleaned)
        removed += count

    cleaned = cleaned.strip()
    if not cleaned:
        return reply

    if removed:
        logger.info(
            "Stripped %d premature closing question(s) at stage %s.", removed, stage
        )

    return cleaned


# Marketing filler. Replacements rather than deletions: cutting a phrase out of the middle
# of an Arabic sentence leaves it ungrammatical, which is worse than the filler. The
# intensifier goes and the adjective stays.
FLUFF_PATTERNS = (
    (re.compile(r"جذابة\s+جدا[ًا]?"), "جذابة"),
    (re.compile(r"جذاب\s+جدا[ًا]?"), "جذاب"),
    (re.compile(r"فخمة\s+جدا[ًا]?"), "فخمة"),
    (re.compile(r"فخم\s+جدا[ًا]?"), "فخم"),
    (re.compile(r"رائعة\s+جدا[ًا]?"), "حلوة"),
    (re.compile(r"تركيبة\s+رائعة"), "تركيبة حلوة"),
    (re.compile(r"لمسة\s+عصرية\s+جذابة"), "ريحة عصرية"),
    (re.compile(r"لمسة\s+عصرية"), "ريحة عصرية"),
    (re.compile(r"عبق\S*\s+"), ""),
)

# Quantified certainty the product data cannot support. "مضمون" alone is left alone: the
# persona's own trade-secrets line says "أضمنلك إن جودتها هتعجبك", and gagging that would
# break a pinned rule. What goes is the manufactured precision and the absolute guarantee.
UNSUPPORTED_CLAIMS = (
    (re.compile(r"مضمون(?:ين|ة)?\s*(?:100|١٠٠)\s*%"), "اختيار آمن"),
    (re.compile(r"(?:100|١٠٠)\s*%\s*مضمون(?:ين|ة)?"), "اختيار آمن"),
    (re.compile(r"مطابق\S*\s+للأصل\s+بنسبة\s+\d+\s*%"), "قريب من الأصل"),
    (re.compile(r"(?:شبه|تشابه|مشابه)\S*\s+(?:ب)?نسبة\s+\d+\s*%"), "قريب منه"),
    (re.compile(r"بنسبة\s+\d+\s*%\s+من\s+الأصل"), "قريب من الأصل"),
    (re.compile(r"الاتنين\s+مضمونين"), "الاتنين اختيار آمن"),
    (re.compile(r"مضمون(?:ة)?\s+(?:هتعجب|إنها\s+هتعجب)\S*"), "أغلب الناس بتحبها"),
)

# One emoji is punctuation; six is a brochure. Structural markers are protected: 🔹 opens
# each recommendation line, 💰 marks the order total that _summary_was_shown greps for, and
# the ✅/❌/⚠️/💡/⭐ set carries meaning the prompts depend on. Stripping any of those would
# damage the reply rather than tidy it.
PROTECTED_EMOJI = frozenset("🔹💰💡✅❌⚠⭐️")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF]"
)
MAX_EMOJI = 3


def _cap_emoji(reply):
    """Drop decorative emoji past the cap, keeping the earliest and all structural ones."""
    decorative = [
        match for match in _EMOJI.finditer(reply)
        if match.group() not in PROTECTED_EMOJI
    ]
    if len(decorative) <= MAX_EMOJI:
        return reply

    result = reply
    for match in reversed(decorative[MAX_EMOJI:]):
        result = result[: match.start()] + result[match.end() :]
    return result


def soften_marketing_language(reply):
    """Replace brochure phrasing and unsupported precision with plain seller Arabic.

    These are in code rather than the persona for the same reason as BANNED_CLOSERS: the
    persona's own approved-words list recommended "جذابة جداً", which is exactly the
    register the evaluation flagged. The list is fixed there; this catches what the model
    produces anyway.
    """
    if not reply:
        return reply

    cleaned = reply
    for pattern, replacement in UNSUPPORTED_CLAIMS:
        cleaned = pattern.sub(replacement, cleaned)
    for pattern, replacement in FLUFF_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)

    cleaned = _cap_emoji(cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = cleaned.strip()

    return cleaned or reply
