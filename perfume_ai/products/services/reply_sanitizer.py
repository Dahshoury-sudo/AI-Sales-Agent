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
#
# The optional connector in front of every one of them has to absorb a *whole* conditional
# opener rather than half of one. `(?:و\s*)?` on its own matched the و **inside** لو ("if"),
# because both letters are word characters and the group happily started mid-word — so
# "…وأغنى شوية. لو تحب تعرف الأسعار؟" was stripped down to "…وأغنى شوية. ل" and the reply
# ended on an orphaned letter. Evaluation caught that in a live reply (scenario M3).
# A `\bو` fix is not enough: it turns the orphaned ل into an orphaned لو, which is a reply
# ending in "if". The alternation below consumes لو / ولو / a bare و together with the
# question, and _trim_dangling_connector is the backstop for whatever slips past it.
_LEAD = r"\s*(?:(?:و\s*)?لو\s+|و\s*)?"

BANNED_CLOSERS = (
    # "تحب تعرف الأسعار والأحجام المتاحة؟" / "تحب تعرف أسعارهم والأحجام؟"
    re.compile(
        _LEAD + r"تحب\s+تعرف\s+(?:ال)?[أاإ]?سعار\S*"
        r"(?:\s*و\s*(?:ال)?[أاإ]?حجام\S*)?(?:\s+المتاحة)?\s*[؟?]"
    ),
    # The same forbidden move in the first person, which is how the model actually
    # phrased it in evaluation: "تحب أعرفك الأسعار والأحجام؟" / "تحب أقولك الأسعار؟".
    # The pattern above requires "تعرف" and matched none of these, so the single
    # question the persona quotes verbatim as forbidden went out repeatedly.
    re.compile(
        _LEAD + r"تحب\s+[أاإ](?:عرفك|قولك)\s+(?:على\s+)?(?:ال)?[أاإ]?سعار\S*"
        r"(?:\s*و\s*(?:ال)?[أاإ]?حجام\S*)?(?:\s+المتاحة)?\s*[؟?]"
    ),
    # "تحب أعرفك أكتر عن الأحجام دي؟" — same emptiness, different noun.
    re.compile(_LEAD + r"تحب\s+[أاإ]عرفك\s+[أاإ]كتر\s+عن\s+[^؟?]{0,40}[؟?]"),
    # "عايز حاجة تانية؟" / "محتاج مساعدة في حاجة؟" / "محتاج حاجة تانية؟"
    re.compile(_LEAD + r"(?:عايز|محتاج|تحب)\s+(?:حاجة\s+تانية|مساعدة(?:\s+في\s+حاجة)?)\s*[؟?]"),
    # The statement form, which carries no question mark and so escaped the pattern
    # above: "لو حابب أساعدك في حاجة تانية، تحت أمرك."
    re.compile(_LEAD + r"حابب\s+[أاإ]?ساعدك\s+في\s+حاج[ةه]\s+تاني[ةه][^.؟?]*[.؟?]?"),
    # "عطر معين في بالك؟"
    re.compile(_LEAD + r"(?:فيه\s+)?عطر\s+معين\s+في\s+بالك\s*[؟?]"),
    # "أقدر أساعدك إزاي؟" / "أقدر أساعدك في إيه؟" — the persona's own banned opener.
    re.compile(_LEAD + r"[أاإ]قدر\s+[أاإ]ساعدك\s+(?:في\s+)?[أاإ]?(?:يه|زاي)\s*[؟?]"),
)


# Connectors whose only job is to introduce the clause that was just removed. Trimmed only
# when a strip actually happened, and only mid-text (never the whole reply), so a reply that
# legitimately ends on one is left alone. "بس" and "كمان" are deliberately absent: both are
# ordinary sentence-final words in Egyptian ("دي الأسعار بس", "وفيه 50 ملي كمان").
_DANGLING_TAIL = re.compile(r"\s+(?:ل|لو|ولو|و|أو|او|لكن|يعني)\s*[.،,؟?!]*\s*$")


def _trim_dangling_connector(text):
    """Drop a connector left stranded at the end by a stripped question."""
    return _DANGLING_TAIL.sub("", text).rstrip()



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

    if removed:
        cleaned = _trim_dangling_connector(cleaned)

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
    # "تحب أساعدك في الطلب؟" / "تحب اساعدك في طلب واحد فيهم؟"
    # `[^؟?]{0,30}` rather than `\S*\s*` because every one of these patterns used to
    # require the question mark to sit immediately after the order word. Real replies put
    # words in between — "تحب أساعدك في طلب واحد فيهم؟", "تحب تطلبه تاني؟" — and every
    # one of them walked straight through.
    re.compile(_LEAD + r"تحب\s+[أاإ]?ساعدك\s+في\s+(?:ال)?(?:طلب|اوردر|أوردر)[^؟?]{0,30}[؟?]"),
    # The same close without the "في": "تحب أساعدك تطلب واحد فيهم؟".
    re.compile(_LEAD + r"تحب\s+[أاإ]?ساعدك\s+[نت]طلب[^؟?]{0,30}[؟?]"),
    # "تحب أجهزلك واحد منهم؟" — a close carrying no order word at all.
    re.compile(_LEAD + r"تحب\s+[أاإ]?جهز\s*ل?ك[^؟?]{0,40}[؟?]"),
    # "تحب تطلب؟" / "تحب تطلبه تاني؟" / "تحب نطلبه؟"
    re.compile(_LEAD + r"تحب\s+[نت]طلب[^؟?]{0,30}[؟?]"),
    # "أجيبلك الـ90 ولا الـ50؟" — a size close.
    re.compile(_LEAD + r"[أا]جيبلك\s+(?:الـ?\s*)?\d+\s*(?:ملي)?\s*ولا\s+(?:الـ?\s*)?\d+\s*(?:ملي)?\s*[؟?]"),
    # "نسجل الطلب؟" / "نكمل الاوردر؟" / "تحب نكمل الطلب؟"
    # The optional "تحب" matters for ordering: without it this pattern matched only
    # "نكمل الطلب؟" out of "تحب نكمل الطلب؟" and left the verb stranded, because it is
    # tried before the "تحب نكمل" pattern below and consumed the tail first.
    re.compile(_LEAD + r"(?:تحب\s+)?ن(?:سجل|كمل)\s+(?:ال)?(?:طلب|اوردر|أوردر)[^؟?]{0,20}[؟?]"),
    # "تحب نكمل؟" — the same close with no order word for the pattern above to anchor on.
    re.compile(_LEAD + r"تحب\s+نكمل[^؟?]{0,25}[؟?]"),
    # The statement form, which carries no question mark and so escaped every pattern above:
    # "لو تحب أساعدك في الطلب أو تحب تجرب العطور في الستور تحت أمرك." went out at stage
    # 'discovery'. BANNED_CLOSERS already carries a statement variant for the same reason;
    # this family needed one too.
    re.compile(
        _LEAD + r"تحب\s+[أاإ]?ساعدك\s+في\s+(?:ال)?(?:طلب|اوردر|أوردر)[^.؟?]*[.؟?]?"
    ),
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
    if removed:
        cleaned = _trim_dangling_connector(cleaned)
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
