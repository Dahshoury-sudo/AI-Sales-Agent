"""Customer-facing packaging names, independent of stored bottle_type values."""
import re


NORMAL_BOTTLE = "زجاجة الستور الخاصة بينا"
ORIGINAL_BOTTLE = "زجاجة اوريجنال"
NORMAL_BOTTLES = "زجاجات الستور الخاصة بينا"
ORIGINAL_BOTTLES = "زجاجات اوريجنال"
BOTTLE_DIFFERENCE = "العطر في الاتنين نفس التركيبة من عندنا، والفرق في شكل الزجاجة وسعرها."


def bottle_label(bottle_type, *, plural=False):
    return {
        "normal": NORMAL_BOTTLES if plural else NORMAL_BOTTLE,
        "original": ORIGINAL_BOTTLES if plural else ORIGINAL_BOTTLE,
    }[bottle_type]


# Match bottle phrases only. In particular, never replace a perfume house's name,
# the word "brand" alone, or claims about the liquid being authentic.
_BOTTLE = r"(?<!\w)(?P<prefix>و?[بكف]?)(?:ال)?زجاج(?P<ending>[ةه]|ات)\s+"
_MANUFACTURER = r"(?!\s+(?:ال)?(?:[أاإ]صلي[ةه]?|[أاإ]صليين|عالمي[ةه]?)\b)"
_OWNER = r"(?:\s+(?:(?:الخاص[ةه]\s+(?:بينا|بنا))|بتاعت?نا))?"
_STORE = re.compile(
    _BOTTLE + r"(?:البراند|الستور|الاستور|المحل)\b" + _MANUFACTURER + _OWNER
)
_ORIGINAL = re.compile(
    _BOTTLE + r"(?:(?:ب(?:نفس\s+)?شكل|شكل)\s+)?(?:ال|ل)?[أاإآ]وري?جي?نال\b"
)
# A bare "مش" rejects too: "سعر Aventus الأصلي 100 ملي، مش زجاجة الستور" read as a store-bottle request
# left 100 ml + store bottle, which does not exist, and an in-stock original was denied (S02 final 2, t6).
_REJECTED = re.compile(r"(?:مش(?:\s+(?:عايز[ةه]?|عاوز[ةه]?|محتاج[ةه]?|في))?|بلاش|من غير|بدون|لا اريد)\s*$")


def normalize_bottle_wording(text):
    """Render legacy configured/generated copy without modifying stored source text.

    Canonical phrases survive unchanged, including when this runs more than once.
    Ownership suffixes are consumed to avoid repeating "الخاصة بينا".
    """
    if not text:
        return text

    def label(match, kind):
        # "زجاجة اوريجنال بنفس شكل زجاجة البراند" is the brand's own bottle shape; rewritten as the
        # store bottle it said the original bottle looks like ours (simulation S16 round 2 full 5, t1).
        if kind == "normal" and "البراند" in match.group() and re.search(r"شكل\s*$", match.string[:match.start()]):
            return match.group().rstrip() + " الأصلي"
        return match["prefix"] + bottle_label(kind, plural=match["ending"] == "ات")

    for pattern, kind in ((_STORE, "normal"), (_ORIGINAL, "original")):
        text = pattern.sub(lambda match: label(match, kind), text)
    return text


def explicit_bottle_type(text):
    """Recognize a named bottle, without confusing a store-brand perfume request.

    Both options in one message are a comparison/choice, not a selected type.
    Bare 'original'/'تركيب' are left to callers that support that shorthand.
    """
    text = text or ""
    normal, original = (
        any(not _REJECTED.search(text[:match.start()]) for match in pattern.finditer(text))
        for pattern in (_STORE, _ORIGINAL)
    )
    if normal != original:
        return "normal" if normal else "original"
    return None


def strip_bottle_wording(text):
    """Remove packaging phrases before interpreting perfume names or house requests."""
    return _ORIGINAL.sub(" ", _STORE.sub(" ", text or ""))
