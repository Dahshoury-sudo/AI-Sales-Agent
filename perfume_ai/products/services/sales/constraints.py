"""What the customer has already told us, and the one thing worth asking next.

The failure this module exists for: a customer said "رجالي، ريحته فخمة وثابتة، مناسب
للخروجات بالليل، مش عايز حاجة تقيلة أو تخنق اللي حواليا" and was answered with
"ميزانيتك في حدود كام؟" and nothing else. Every constraint was extracted correctly and
then thrown away, because the router's budget gate built its prompt from a hardcoded
sentence that never mentioned them.

Two jobs, therefore. Render the constraints we hold so a reply can acknowledge them
without reciting them back; and decide whether asking anything at all is worth a turn.

The Arabic here is a *hint to the model*, not a scripted reply. Scripting it is what
produced the identical budget sentence every time.
"""

from ..static_faq_service import normalize_arabic

# The slots that describe taste. max_price is deliberately excluded: it is the thing the
# budget gate is deciding whether to ask about, so counting it would let a bare budget
# stand in for knowing anything about the customer.
TASTE_KEYS = (
    "notes", "avoid_notes", "avoid_traits", "occasion", "season",
    "longevity", "projection", "perfume_type", "brand", "similar_to",
    "wants_uncommon",
)

# Enough to recommend from. Below this a recommendation would be guesswork and asking is
# genuinely the better move; at or above it, blocking the whole turn on a budget question
# reads as not having listened. Three is what the evaluation's worst case supplies
# (occasion + longevity + "not heavy"), and it recommends correctly at that level.
MIN_CONSTRAINTS_TO_RECOMMEND = 3


def _is_set(value):
    """A preference the customer actually expressed, as opposed to an empty slot.

    `False` counts as unset: `wants_uncommon: false` is the extractor reporting the absence
    of a preference, and a plain truthiness test would have scored it as one — inflating
    the constraint count on every request that carried the field at all.
    """
    if value is False:
        return False
    return value not in (None, "", [], {}, ())


def taste_constraint_count(intent):
    """How many distinct taste constraints the customer has expressed.

    List-valued slots count per entry, capped at two each. An expert saying "فيها
    ambroxan و lavender" has given two facts, and scoring `notes` as a single constraint
    left them one short of the threshold — so the whole turn went on a budget question
    while three perfumes carrying exactly those notes sat in the catalogue. The cap stops
    one over-eager extraction from a single sentence clearing the bar on its own.
    """
    if not intent:
        return 0

    total = 0
    for key in TASTE_KEYS:
        value = intent.get(key)
        if not _is_set(value):
            continue
        if isinstance(value, (list, tuple, set)):
            total += min(2, len([entry for entry in value if _is_set(entry)]))
        else:
            total += 1
    return total


# How a customer says the budget is not a constraint. Treated as answering the budget
# question rather than leaving it open: "مش مهم السعر" followed by "ميزانيتك كام؟" is the
# bot not listening, which is the failure this module exists to prevent.
_BUDGET_OPEN = (
    "مش مهم السعر", "السعر مش مهم", "مش فارقه السعر", "مش مشكله السعر",
    "السعر مش مشكله", "مهما كان السعر", "اي سعر", "مفتوح الميزانيه",
    "مش بهتم بالسعر", "الفلوس مش مشكله", "بدون حد للسعر",
)


def budget_is_open(message, history=None):
    """Did the customer say price is not a constraint?"""
    text = normalize_arabic(message or "")
    for entry in history or ():
        if entry.get("role") == "user":
            text += " " + normalize_arabic(entry.get("content", ""))
    return any(normalize_arabic(phrase) in text for phrase in _BUDGET_OPEN)


def can_recommend_without_budget(intent):
    """Whether we know enough to recommend rather than ask for a budget first.

    A similarity request clears this on its own. "عايز حاجة شبه Sauvage" tells us more
    about what to put in front of the customer than three vague slots do, and stopping to
    ask a budget before answering it is the same not-listening failure the constraint
    count exists to prevent.
    """
    if not intent:
        return False
    if _is_set(intent.get("similar_to")):
        return True
    return taste_constraint_count(intent) >= MIN_CONSTRAINTS_TO_RECOMMEND


# Slot values arrive from the extractor in English. Anything unrecognised is echoed as
# written rather than dropped — an unmapped value is still something the customer said.
_GENDER = {"male": "رجالي", "female": "حريمي", "unisex": "للجنسين"}
_OCCASION = {
    "evening": "للسهرات بالليل", "night": "للليل", "office": "للشغل",
    "work": "للشغل", "party": "للمناسبات والحفلات", "casual": "يومي",
    "daily": "يومي", "formal": "للمناسبات الرسمية", "wedding": "للفرح",
    "date": "للخروجات",
}
_SEASON = {
    "summer": "للصيف", "winter": "للشتا", "spring": "للربيع",
    "autumn": "للخريف", "fall": "للخريف", "all seasons": "لكل الفصول",
}
_LONGEVITY = {
    "long-lasting": "ثابت", "long lasting": "ثابت", "eternal": "ثابت جداً",
    "moderate": "ثباته متوسط",
}
_PROJECTION = {
    "strong": "فواح", "enormous": "فواح جداً", "moderate": "فوحانه متوسط",
    "intimate": "هادي وقريب",
}
_PERFUME_TYPE = {
    "oriental": "شرقي", "western": "غربي", "niche": "نيش",
    "ultra_niche": "الترا نيش",
}
_TRAITS = {
    "heavy": "مش تقيل", "suffocating": "مش خانق", "sweet": "مش مسكر",
    "strong": "مش قوي أوي", "loud": "مش فواح أوي", "old": "مش كلاسيكي",
}


def _lookup(table, value):
    if not _is_set(value):
        return None
    return table.get(str(value).strip().lower(), str(value).strip())


def describe(intent):
    """Short Arabic phrases for everything the customer has told us."""
    if not intent:
        return []

    phrases = []

    for table, key in (
        (_GENDER, "gender"),
        (_PERFUME_TYPE, "perfume_type"),
        (_OCCASION, "occasion"),
        (_SEASON, "season"),
        (_LONGEVITY, "longevity"),
        (_PROJECTION, "projection"),
    ):
        # "multiple" is a routing signal, not a taste — the router asks which to start
        # with, so echoing it back as a preference would be nonsense.
        if key == "gender" and intent.get(key) == "multiple":
            continue
        phrase = _lookup(table, intent.get(key))
        if phrase:
            phrases.append(phrase)

    if _is_set(intent.get("brand")) and intent.get("brand") != "STORE_BRAND_EXCLUSIVE":
        phrases.append(f"من {intent['brand']}")
    elif intent.get("brand") == "STORE_BRAND_EXCLUSIVE":
        phrases.append("من تركيباتنا الخاصة")

    if _is_set(intent.get("similar_to")):
        phrases.append(f"شبه {intent['similar_to']}")

    if _is_set(intent.get("notes")):
        phrases.append("فيه " + "، ".join(str(note) for note in intent["notes"][:4]))

    for trait in intent.get("avoid_traits") or ():
        phrase = _TRAITS.get(str(trait).strip().lower())
        phrases.append(phrase or f"مش {trait}")

    if _is_set(intent.get("avoid_notes")):
        phrases.append("من غير " + "، ".join(str(note) for note in intent["avoid_notes"][:3]))

    if _is_set(intent.get("wants_uncommon")):
        phrases.append("حاجة مش منتشرة")

    if _is_set(intent.get("max_price")):
        try:
            phrases.append(f"في حدود {int(float(intent['max_price']))} جنيه")
        except (TypeError, ValueError):
            pass

    return phrases


def acknowledgement_hint(intent):
    """Tell the model what it already knows, and to nod to it once — briefly.

    The instruction to vary the wording is load-bearing: the behaviour being replaced was
    a single hardcoded sentence, and a single mandated sentence would be the same bug in
    a nicer costume.
    """
    phrases = describe(intent)
    if not phrases:
        return ""

    return (
        "\n🧠 العميل قال بالفعل: " + "، ".join(phrases) + ".\n"
        "- اعترف بطلبه في نص جملة قصيرة بأسلوبك وكمّل (مثال للفكرة مش للصيغة: \"فهمتك\"). "
        "❌ ممنوع تعيد سرد كل التفاصيل دي عليه، وممنوع تستخدم نفس الصيغة في كل رد.\n"
        "- ❌ ممنوع تسأله عن أي حاجة من دي تاني.\n"
    )


# Gift language. Reuses the relational vocabulary the extractor already infers gender
# from, because "لمراتي" is simultaneously a gender clue and a gift clue.
_GIFT_MARKERS = (
    "هديه", "هدية", "بريزنت", "present", "gift",
    "لمراتي", "لمراتى", "لجوزي", "لخطيبتي", "لخطيبي", "لصاحبتي", "لصاحبي",
    "لاختي", "لاخويا", "لماما", "لبابا", "لابويا", "لبنتي", "لابني",
    "لطنطي", "لخالتي", "لعمي", "لخالي", "لحد", "لواحده", "لواحد",
)

# Language that says the giver does not know the recipient's taste. This is the signal
# that turns a gift into an uncertainty problem rather than a search problem.
_TASTE_UNKNOWN = (
    "مش عارف بتحب", "مش عارف هي بتحب", "مش عارفه بتحب", "مش عارف ذوقها",
    "مش عارف ذوقه", "مش عارف بيحب", "معرفش بتحب", "معرفش ذوقها",
    "مش عارف تحب", "مش عارف اجيب ايه", "مش عارف اختار ليها",
)


def gift_context(message, history=None, intent=None):
    """Whether this is a gift, and whether we know anything about the recipient's taste.

    `taste_known` is true when the customer told us something usable — either explicit
    taste constraints, or a perfume the recipient already likes. It is what separates
    "recommend for her" from "we are both guessing".
    """
    normalized = normalize_arabic(message or "")
    for entry in history or ():
        if entry.get("role") == "user":
            normalized += " " + normalize_arabic(entry.get("content", ""))

    is_gift = any(marker in normalized for marker in _GIFT_MARKERS)
    said_unknown = any(marker in normalized for marker in _TASTE_UNKNOWN)

    taste_known = taste_constraint_count(intent) > 0 and not said_unknown
    return is_gift, taste_known


GIFT_UNCERTAINTY_HINT = (
    "\n🎁 دي هدية والعميل مش عارف ذوق المستلم:\n"
    "- اعترف بده بصراحة — إنك بتساعده يقرب لذوقها مش بتضمنه.\n"
    "- ❌ ممنوع تقول \"مضمون\" ولا \"الاتنين مضمونين\" ولا \"هتعجبها أكيد\".\n"
    "- اسأل سؤال واحد عالي القيمة: لو فاكر اسم برفان كانت بتحبه أو بتستعمله، "
    "يبعتهولك وده هيقربك لذوقها.\n"
    "- تقدر ترشح حاجة واحدة أو اتنين من النوع اللي بيعجب أغلب الناس، بس قول "
    "إنهم اختيار آمن مش اختيار مضمون.\n"
)
