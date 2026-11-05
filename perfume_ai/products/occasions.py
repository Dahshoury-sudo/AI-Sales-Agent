"""The six catalog occasions, shared by customer interpretation and matching.

Longer phrases are matched first and consumed as a whole. In particular, a date
night is not a second nightlife category, and the comma in the beach label is
part of that label rather than a separator between occasions.
"""

import re


DAILY = "Daily & Casual"
WORK = "Work & Office"
NIGHT_OUT = "Night Out & Hanging Out"
ROMANTIC = "Date Night & Romantic"
FORMAL = "Formal & Special Occasions"
ACTIVE = "Beach, Summer & Gym"
OCCASIONS = (DAILY, WORK, NIGHT_OUT, ROMANTIC, FORMAL, ACTIVE)

OCCASION_DESCRIPTIONS = {
    DAILY: "للاستخدام اليومي والكاجوال",
    WORK: "للشغل والمكتب",
    NIGHT_OUT: "للخروجات والسهرات",
    ROMANTIC: "للديت والمواعيد الرومانسية",
    FORMAL: "للمناسبات الرسمية والخاصة",
    ACTIVE: "للبحر والصيف والجيم",
}


def normalize_text(value):
    """Normalize spelling and punctuation without importing Django models."""
    text = str(value or "").casefold().translate(str.maketrans("إأآٱىة", "اااايه"))
    text = re.sub(r"[\u064b-\u065f\u0670ـ]", "", text).replace("’", "'")
    return " ".join(re.sub(r"[^\w\s']", " ", text).split())


_ALIASES = {
    DAILY: (
        "daily", "casual", "everyday", "every day", "day", "daytime", "morning",
        "university", "college", "school", "errands", "casual outings", "light outings",
        "mosque", "prayer", "يومي", "يوميه", "كل يوم", "كاجوال", "نهار", "نهاري",
        "الصبح", "صباح", "صباحي", "جامعه", "مدرسه", "مشاوير", "مسجد", "صلاه",
        "استخدام يومي", "استخدام اليومي",
        "خروجات خفيفه", "خروجات الخفيفه", "خروجات بسيطه",
    ),
    WORK: (
        "work", "office", "business", "meeting", "meetings", "job interview", "interview",
        "شغل", "عمل", "مكتب", "دوام", "اجتماع", "اجتماعات", "مقابله عمل", "انترفيو",
    ),
    NIGHT_OUT: (
        "night out", "hanging out", "hang out", "outings", "evening", "night", "nighttime",
        "party", "parties", "club", "clubbing", "with friends", "office party", "work party",
        "خروجات", "خروج", "سهره", "سهرات", "مساء", "مسائي", "ليل", "ليلي",
        "حفله", "حفلات", "مع صحابي", "مع اصحابي",
    ),
    ROMANTIC: (
        "date night", "date", "dates", "romantic", "romance", "romantic dinner",
        "ديت", "رومانسي", "رومانسيه", "رومانتيك", "موعد غرامي", "عشا رومانسي",
        "عشاء رومانسي", "مع حبيبتي", "مع حبيبي",
    ),
    FORMAL: (
        "formal", "special occasion", "special occasions", "wedding", "weddings",
        "wedding party", "engagement", "ceremony", "gala", "graduation",
        "رسمي", "رسميه", "مناسبات", "مناسبه رسميه", "مناسبه خاصه", "مناسبات خاصه",
        "فرح", "افراح", "زفاف", "خطوبه", "حفله تخرج", "تخرج",
    ),
    ACTIVE: (
        "beach", "summer", "gym", "sport", "sports", "sporty", "workout", "workouts",
        "training", "exercise", "running", "pool", "بحر", "شاطئ", "شاطيء", "شاطي",
        "صيف", "صيفي", "جيم", "رياضه", "رياضي", "تمرين", "تمارين", "سباحه",
    ),
}


def _aliases():
    result = {}
    for category, aliases in _ALIASES.items():
        for alias in (category, category.replace("&", "and"), *aliases):
            text = normalize_text(alias)
            result[text] = category
            if re.match(r"[\u0600-\u06ff]", text):
                # Arabic articles, prepositions and conjunctions attach to the
                # first word; these forms also cover short answers like للشغل.
                root = text[2:] if text.startswith("ال") else text
                for prefix in ("", "ال", "ل", "لل", "بال", "ب", "و", "وال", "ول", "ولل", "وبال"):
                    result[prefix + root] = category
    return result


_BY_ALIAS = _aliases()
_MENTION = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(alias) for alias in sorted(_BY_ALIAS, key=lambda alias: (-len(alias), alias))
) + r")(?!\w)")
_NEGATIVE = re.compile(
    r"(?<!\w)(?:مش|لا|بلاش|بدون|غير|not|no|without|avoid|don't|dont)"
    r"(?:\s+(?:عايز(?:ه)?|عاوز(?:ه)?|حاجه|يكون|عايزه|want|it|something|for|the|a)){0,5}\s*$"
)
_CONNECTOR = re.compile(r"\s*(?:و|او|ولا|and|or|nor)\s*")


def occasion_groups(value, *, positive_only=False):
    """Recognized categories in a catalog value or a piece of customer prose."""
    text = normalize_text(value)
    groups = set()
    previous_end, previous_negative = 0, False
    for match in _MENTION.finditer(text):
        negative = bool(_NEGATIVE.search(text[:match.start()])) or bool(
            previous_negative and _CONNECTOR.fullmatch(text[previous_end:match.start()])
        )
        if not positive_only or not negative:
            groups.add(_BY_ALIAS[match.group()])
        previous_end, previous_negative = match.end(), negative
    return groups


def normalize_occasion(value, *, positive_only=False):
    """One canonical occasion, or None when the text is unknown/ambiguous."""
    groups = occasion_groups(value, positive_only=positive_only)
    return next(iter(groups)) if len(groups) == 1 else None


def occasion_alias(value):
    """An entire known label/alias, useful for bounded references to an offer."""
    return _BY_ALIAS.get(normalize_text(value))


def occasion_matches(recorded, wanted):
    if not wanted:
        return True
    return bool(occasion_groups(recorded) & occasion_groups(wanted))


def describe_occasion(value):
    groups = occasion_groups(value)
    return "، ".join(OCCASION_DESCRIPTIONS[group] for group in OCCASIONS if group in groups)
