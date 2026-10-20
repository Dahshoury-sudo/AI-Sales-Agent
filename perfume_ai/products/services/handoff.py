"""Interpret replies to the bot's latest human-transfer invitation.

Short approvals must not depend on the topic classifier: conversation 1122's
"ماشي" was sent to checkout before the promotion branch could accept it.
Unrecognized wording still goes through the existing contextual classifier.
"""
import re

from .static_faq_service import normalize_arabic


_HUMAN = re.compile(
    r"مندوب|موظف|بشري|انسان|حد حقيقي|حد من (?:الفريق|فريقنا)|"
    r"خدمه العملاء|فريق(?:نا| المبيعات| الدعم)|"
    r"\b(?:human|real person|agent|support team|sales team|customer service)\b"
)
_TRANSFER = re.compile(
    r"احولك|نحولك|تحويلك|اوصلك|نوصلك|تتكلم|تتكلمي|تكلم|اكلم|تتواصل|يكلمك|يتواصل معاك|"
    r"\b(?:transfer|connect|speak|talk|put you in touch)\b"
)
_INVITATION = re.compile(
    r"تحب|حابب|حابه|عايز|عاوزه|تريد|هل|اقدر|نقدر|ممكن|"
    r"\b(?:would you like|do you want|shall i|can i|i can|want me to|would you prefer)\b"
)
_UNAVAILABLE = re.compile(
    r"(?:مش|لا) (?:بقدر|اقدر|ممكن) (?:احولك|اوصلك)|"
    r"(?:مقدرش|ماقدرش) (?:احولك|اوصلك)|"
    r"\b(?:cannot|can't|unable to) (?:transfer|connect)\b"
)
_QUESTION = re.compile(r"^(?:ايه|انهي|كام|بكام|what|which|how|do you|would you)\b")
_ALTERNATIVE = re.compile(r"\b(?:ولا|or)\b")
_FOLLOWUP = re.compile(r"(?:تحب|تحب كده|حابب|هل تحب|shall i|would you like that|want me to)")
_YES = re.compile(
    r"(?:اه|اها|ايوه|ايوا|نعم|تمام|ماشي|اوك|اوكي|حاضر|موافق|يلا|اشطا|"
    r"yes|yeah|yep|ok|okay|sure|absolutely)(?:\s|$)"
)
_DO_IT = re.compile(
    r"(?:حولني|اتصل بيا|اتصلوا بيا|go ahead|please do|do it|"
    r"(?:please )?(?:transfer|connect) me)(?:\s|$)"
)
_NO = re.compile(r"(?:لا|لا شكرا|لاء|no|nope|no thanks)(?:\s|$)")
_REFUSAL = re.compile(
    r"(?:مش|مو) (?:عايز|عاوزه|حابب|محتاج) (?:مندوب|موظف|حد|انسان|تحويل|اكلم)|"
    r"متحولنيش|ما تحولنيش|لا تحولني|بلاش (?:تحولني|مندوب|تحويل)|"
    r"\b(?:dont|do not) (?:transfer|connect|hand me off)|"
    r"\bno (?:human|agent|transfer|thanks)\b|"
    r"\b(?:dont|do not) (?:want|need) (?:a |an |to speak to a |to talk to a )?"
    r"(?:human|person|agent)|"
    r"(?:عايزك انت|انت اللي|انت جاوب|انت تنفذ|انت نفذ)|"
    r"\b(?:you answer|you do it|you to (?:answer|apply|do it))\b"
)
_DEFER = re.compile(
    r"^(?:(?:بس|لكن|but|please)\s+)?(?:مش دلوقتي|بعدين|استني|لسه|not now|later|wait|hold on)\b|"
    r"قبل (?:التحويل|ما تحولني)|(?:جاوبني|رد عليا).{0,60}الاول|"
    r"\b(?:before (?:you )?(?:transfer|connect)|answer.{0,60}first)\b"
)


def _words(text):
    text = normalize_arabic(text or "").replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def has_handoff_offer(history):
    """Only the latest assistant invitation can make a bare approval a transfer.

    Callers supply delivered history. A human message, if supplied by another
    caller, is a boundary too; an old bot offer cannot speak for a human agent.
    """
    for row in reversed(history or []):
        if row.get("role") == "agent":
            return False
        if row.get("role") != "assistant":
            continue
        text = normalize_arabic(row.get("content", ""))
        parts = [(m[1].strip(" ،,;؛:"), m[2]) for m in re.finditer(r"([^.!?؟\n]+)([.!?؟\n]*)", text) if _words(m[1])]
        for index in range(len(parts) - 1, -1, -1):
            invitation, punctuation = parts[index]
            if not ("?" in punctuation or "؟" in punctuation or _INVITATION.search(invitation)
                    or _QUESTION.search(_words(invitation))):
                # An explanation or courtesy after the question doesn't change
                # what yes answers. A newer question in this reply does.
                continue
            if _FOLLOWUP.fullmatch(_words(invitation)) and index > 0:
                invitation = parts[index - 1][0]
            return bool(
                _INVITATION.search(invitation)
                and _TRANSFER.search(invitation)
                and _HUMAN.search(invitation)
                and not _UNAVAILABLE.search(invitation)
                and not _ALTERNATIVE.search(invitation)
            )
        return False
    return False


def handoff_offer_reply(message, history):
    """True: accept now; False: not consent; None: let classification interpret it.

    Match approval at word boundaries, never inside words like "معاه". A clear
    approval may include a question for the human; refusals and delays win.
    """
    text = _words(message)
    approval = _YES.match(text)
    if not has_handoff_offer(history):
        # A bare yes to a different question must not revive an older offer,
        # even if the classifier mistakenly labels it handoff.
        return False if approval and not text[approval.end():].strip() else None
    rest = text[approval.end():].strip() if approval else text
    while extra_approval := _YES.match(rest):
        rest = rest[extra_approval.end():].strip()
    if _NO.match(text) or _REFUSAL.search(text) or _DEFER.search(rest):
        return False
    if re.fullmatch(r"(?:مش عايز|مش محتاج|بلاش)(?: شكرا)?", rest):
        return False
    if approval or _DO_IT.match(text):
        return True
    return None
