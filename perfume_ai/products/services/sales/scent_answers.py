"""Interpret short choices only while the corresponding scent question is pending."""

import re

from ..static_faq_service import normalize_arabic


PREFERENCE_QUESTION = "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه"
# Keep this order aligned with the choices in PREFERENCE_QUESTION.
_CHOICES = ("fresh", "woody", "oud", "sweet")
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
    latest = next((row for row in reversed(history or []) if row.get("role") in ("assistant", "agent")), {})
    return latest.get("role") == "assistant" and (
        normalize_arabic(latest.get("content", "")) == normalize_arabic(PREFERENCE_QUESTION)
    )


def answer_updates(message, history=None):
    if not question_pending(history):
        return {}
    text = normalize_arabic(message or "").strip(".!؟? ")
    from .occasion_preferences import _DELEGATE
    if _DELEGATE.fullmatch(text):
        return {"scent_open": True, "scent_tags": [], "scent_evidence": []}

    evidence = []
    for token in re.findall(r"[^\s،,.!?؟]+", message or ""):
        word = normalize_arabic(token)
        if word.startswith("و") and word[1:] in _ORDINALS:
            word = word[1:]
        if word in _ORDINALS:
            evidence.append({"text": token, "tags": [_CHOICES[_ORDINALS[word]]], "polarity": "positive"})
        elif word not in _FILLERS:
            return {}
    if not evidence:
        return {}
    return {
        "scent_tags": list(dict.fromkeys(tag for item in evidence for tag in item["tags"])),
        "excluded_scent_tags": [], "scent_open": False, "scent_evidence": evidence,
        "notes": [], "avoid_notes": [],
    }
