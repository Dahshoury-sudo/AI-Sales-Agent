"""Interpret short choices only while the corresponding scent question is pending."""

import re

from ..static_faq_service import normalize_arabic


PREFERENCE_QUESTION = "بتحب الفريش، الخشبي، العود، ولا المسكر؟"
_LEGACY_PREFERENCE_QUESTION = "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه"
# Asked instead of repeating PREFERENCE_QUESTION to a customer who said they do not know: the same
# four category words went out three turns running to a beginner (simulation S01 t3-t4, S18 t3).
SIMPLE_PREFERENCE_QUESTION = (
    "ولا يهمك. تحبها منعشة وخفيفة زي ريحة الصابون بعد الدش، ولا دافية ومسكرة شوية زي الفانيليا؟ "
    "ولو مش فارقة معاك أختارلك أنا."
)
# Keep these orders aligned with the choices in each question.
_CHOICES = ("fresh", "woody", "oud", "sweet")
_SIMPLE_CHOICES = ("fresh", "sweet")
_SIMPLE_WORDS = {
    "fresh": ("منعش", "منعشه", "خفيف", "خفيفه", "الصابون", "صابون", "فريش", "دش"),
    "sweet": ("دافي", "دافيه", "دافيه", "مسكر", "مسكره", "فانيليا", "فانيلا", "سويت"),
}
# Applied to normalize_arabic output (ة→ه, أ→ا).
_UNSURE = re.compile(
    r"(?<!\w)(?:مش\s+(?:عارف|عارفه|عارفين|فاهم|فاهمه|متاكد|متاكده)|معرفش|ماعرفش|مش\s+بفهم|"
    r"idk|i\s+don'?t\s+know|not\s+sure|m[ae]?sh\s*3ar?f\w*)(?!\w)"
)
_INDIFFERENT = re.compile(r"(?<!\w)(?:مش\s+فارق(?:ه)?|اي\s+حاجه|اي\s+ريحه|whatever|any\s+(?:scent|one))(?!\w)")


def unsure(message):
    return bool(_UNSURE.search(normalize_arabic(message or "")))


def pending_question(history=None):
    """Which scent question the latest assistant reply asked: "standard", "simple" or None."""
    latest = next((row for row in reversed(history or []) if row.get("role") in ("assistant", "agent")), {})
    if latest.get("role") != "assistant":
        return None
    content = normalize_arabic(latest.get("content", ""))
    if content in {normalize_arabic(PREFERENCE_QUESTION), normalize_arabic(_LEGACY_PREFERENCE_QUESTION)}:
        return "standard"
    if content == normalize_arabic(SIMPLE_PREFERENCE_QUESTION):
        return "simple"
    return None


def next_question(message, history=None):
    """The scent question for a turn that still has no scent preference.

    A customer who just said they do not know gets everyday choices, never the same category list.
    """
    if unsure(message) and pending_question(history) != "simple":
        return SIMPLE_PREFERENCE_QUESTION
    return PREFERENCE_QUESTION
_ORDINALS = {
    word: index
    for index, words in enumerate((
        ("اول", "الاول", "الاولي", "اولاني", "اولانيه", "الاولاني", "الاولانيه", "1", "١", "first"),
        ("تاني", "تانيه", "التاني", "التانيه", "الثاني", "الثانيه", "2", "٢", "second"),
        ("تالت", "تالته", "التالت", "التالته", "الثالث", "الثالثه", "3", "٣", "third"),
        ("رابع", "رابعه", "الرابع", "الرابعه", "4", "٤", "fourth"),
    ))
    for word in words
}
_ORDINALS.update({word: -1 for word in ("اخر", "الاخير", "الاخيره", "last")})
_FILLERS = frozenset({
    "حاجه", "واحد", "واحده", "اختيار", "رقم", "طيب", "طب", "ممكن", "لو", "سمحت",
    "عايز", "عايزه", "عاوز", "عاوزه", "خلينا", "اختار", "بحب", "و", "او", "كمان",
    "the", "one", "option", "please", "i", "want", "and", "or", "also",
})


def question_pending(history=None):
    return pending_question(history) is not None


def answer_updates(message, history=None):
    pending = pending_question(history)
    if not pending:
        return {}
    text = normalize_arabic(message or "").strip(".!؟? ")
    from .occasion_preferences import _DELEGATE
    # Delegation, indifference, or still not knowing after the everyday choices: choose for them.
    if _DELEGATE.fullmatch(text) or _INDIFFERENT.search(text) or (pending == "simple" and _UNSURE.search(text)):
        return {"scent_open": True, "scent_tags": [], "scent_evidence": []}
    choices = _CHOICES if pending == "standard" else _SIMPLE_CHOICES

    evidence = []
    for token in re.findall(r"[^\s،,.!?؟]+", message or ""):
        word = normalize_arabic(token)
        if word.startswith("و") and word[1:] in _ORDINALS:
            word = word[1:]
        stripped = re.sub(r"^(?:ال|و)", "", word)
        simple_tag = next((tag for tag, words in _SIMPLE_WORDS.items()
                           if pending == "simple" and (word in words or stripped in words)), None)
        if simple_tag:
            evidence.append({"text": token, "tags": [simple_tag], "polarity": "positive"})
        elif word in _ORDINALS and -1 <= _ORDINALS[word] < len(choices):
            evidence.append({"text": token, "tags": [choices[_ORDINALS[word]]], "polarity": "positive"})
        elif word not in _FILLERS:
            return {}
    if not evidence:
        return {}
    return {
        "scent_tags": list(dict.fromkeys(tag for item in evidence for tag in item["tags"])),
        "excluded_scent_tags": [], "scent_open": False, "scent_evidence": evidence,
        "notes": [], "avoid_notes": [],
    }
