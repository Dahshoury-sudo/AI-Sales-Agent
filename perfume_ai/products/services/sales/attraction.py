"""Customers who want a perfume that makes other people like them.

"عايز برفان يخلي البنات تحبني" is 20 of the 3,759 stored customer messages. Before this module:

* The audience was read as the wearer. "البنات" is a women's word in `gender`, so conversation 1345
  (2026-11-20) was offered 212 Sexy Women, a women's perfume, by a man asking what women like.
* The goal itself was dropped: it is not a scent label or an occasion, so the bare scent question
  went out and the recommendation never related to what was asked (conversation 1346).
* A question about a named perfume ("Sauvage هيخلي البنات تحبني؟") was answered freely, sometimes with
  invented appeal ("جذابة للبنات جداً", conversation 659), sometimes with a lecture (1263).

Owner decisions, 2026-11-20:

1. The audience never sets the gender, and the gender is not inferred from it either: with no gender
   stated, the usual "محتاج برفان رجالي ولا حريمي؟" is asked.
2. Attraction is not an occasion. The customer wants something people in general like, not a date
   perfume.
3. Appeal comes from the owner's product descriptions: a perfume counts as appealing when its
   description says so (seductive, attractive, ...). That is the only appeal data the store has.
4. About a named perfume: no promise, only what is recorded about it.

Keyword-based for the reasons `appreciation` gives: no extra model call, unit-testable, and a miss
falls back to the previous behaviour.
"""

import re

from ..static_faq_service import normalize_arabic

GOAL_KEY = "appeal_goal"

# Said once, in front of the first question on the turn the goal is stated. It claims nothing about
# any perfume; which perfumes are described as attractive is decided by the data afterwards.
ACKNOWLEDGEMENT = "تمام، هركز على العطور الجذابة اللي عندنا."

def appealing_line(names):
    """The line attractive perfumes earn in the reply note: said plainly, never "موصوف عندنا إنه جذاب".

    The old wording was copied by the model as "ووصفه عندنا جذاب" (conversation 8189); the owner wants
    no reply to cite where its information comes from (2026-11-20).
    """
    return " و".join(names) + (" ريحته جذابة." if len(names) == 1 else " ريحتهم جذابة.")

_WOMEN = r"(?:ال)?(?:بنات|ستات|حريم|نسوان|مزز)|البنت|الست"
_MEN = r"(?:ال)?(?:رجاله|رجال|شباب|ولاد)"
_PEOPLE = r"(?:ال)?ناس|اللي\s+حوالي(?:ا|يا)|الكل"
_AUDIENCE = rf"(?:كل\s+)?(?P<audience>{_WOMEN}|{_MEN}|{_PEOPLE})(?!\w)"

# The verb forms customers use: يخلي / هيخلي / بيخلي / تخلي / هتخلي, and the plural "دول هيخلو".
_CAUSE = r"(?:[يت]|ه[يت]|ب[يت])خل(?:ي|و|وا)"
_APPEAL_VERB = r"(?:[يت]|ه[يت]|ب[يت])(?:عجب|جذب|شد|لفت|غري|بهر)(?:و|وا)?"
# What the audience does to the customer: "البنات تحبني", "تموت فيا", "الحريم تتجنن عليه",
# "البنات بيحبوه". A bare "بتحب" is not here: "البنت بتاعتي بتحب الفانيليا" is a taste, not this goal.
_LOVE = (
    r"(?:[يت]|ه[يت]|ب[يت])?(?:حب(?:ني|وني|ه|وه|ها|وها)|موت(?:وا)?\s+(?:فيا|عليا|فيه|عليه)|"
    r"تجنن(?:وا)?|تهبل(?:وا)?|هيج(?:وا)?|عجب(?:وا)?\s+(?:بيا|بي|بيه)|"
    r"لف(?:وا)?\s+حوالي(?:ا|يا|ه)|جري\s+ورا(?:يا|ه)|بص(?:وا)?\s+عليا|نجذب(?:وا)?|"
    r"مدح(?:وا)?(?:ني)?|معجب(?:ين|ه)?\s+بيا)"
)
# The same reactions said to the customer, as a reply would put them: "هيخلي البنات تحبك".
_LOVE_YOU = r"(?:[يت]|ه[يت]|ب[يت])?(?:حب|موت|تجنن|تهبل|عجب|نجذب|لف|جري|بص|مدح|هيج)\w*"

_CONSTRUCTIONS = (
    # "يخلي البنات تحبني", "هتخلي البنات تعجب بيا", "يخلي الناس تحبني"
    re.compile(rf"(?<!\w){_CAUSE}\s+{_AUDIENCE}"),
    # "يعجب البنات", "هيعجبو البنات معايا", "يلفت نظر البنات", "يشد انتباه الناس"
    re.compile(rf"(?<!\w){_APPEAL_VERB}\s+(?:(?:نظر|انتباه|اعجاب)\s+)?{_AUDIENCE}"),
    # "البنات تحبني", "الحريم تتجنن عليه", "الستات تموت فيا"
    re.compile(rf"(?<!\w){_AUDIENCE}\s+(?:\S+\s+){{0,2}}?{_LOVE}(?!\w)"),
)

# No audience named: "عايز برفان جذاب", "يلفت الانظار", "يشد الانتباه", "ملفته للنتباه وجذابه".
_UNADDRESSED = re.compile(
    # و attaches to the next word: "وتكون ثابته وملفته للنتباه وجذابه" (conversation 1079).
    r"(?<!\w)و?(?:(?:عطر|برفان|برفيوم|ريحه|حاجه)\s+(?:\S+\s+){0,3}?(?:جذاب|جذابه|مغري|مغريه)|"
    # Directly after the noun only: "عايز 90 ملي من سكسي" and "برفان 212 سكسي" name a perfume.
    r"(?:عطر|برفان|برفيوم|ريحه|حاجه)\s+سكسي|"
    r"(?:[يت]|ه[يت]|ب[يت])(?:لفت|شد)\s+(?:ال)?(?:انظار|نظر|انتباه|نتباه)|"
    r"ملفت(?:ه)?\s+(?:لل|ل)?(?:انظار|نظر|انتباه|نتباه)|"
    r"(?:[يت]|ه[يت])خليني\s+(?:\S+\s+)?(?:جذاب|ملفت|مميز)|"
    r"attract(?:s|ive)?\s+(?:women|girls|ladies|people|men|guys)|"
    r"(?:women|girls|ladies|people)\s+(?:will\s+)?(?:love|like|adore|notice)\s+me|"
    r"compliment(?:s|\s+getter))(?!\w)",
    re.I,
)


def _normalize(text):
    return normalize_arabic(text or "").replace("ـ", "")


def _audience_kind(word):
    word = re.sub(r"^كل\s+", "", word)
    if re.fullmatch(_WOMEN, word):
        return "women"
    if re.fullmatch(_MEN, word):
        return "men"
    return "people"


def detect(message):
    """Whose liking the customer is after: "women", "men", "people", or None when it is not this."""
    text = _normalize(message)
    if not text:
        return None
    for pattern in _CONSTRUCTIONS:
        match = pattern.search(text)
        if match:
            return _audience_kind(match.group("audience"))
    if _UNADDRESSED.search(text):
        return "people"
    return None


def audience_spans(text):
    """Character spans of the audience in attraction phrases, for text already normalised.

    `gender` masks these: in "يخلي البنات تحبني" the women are who the customer wants to please, not
    who will wear the perfume.
    """
    spans = []
    for pattern in _CONSTRUCTIONS:
        for match in pattern.finditer(text or ""):
            spans.append(match.span("audience"))
    return spans


def mask_audience(text):
    """`text` with every attraction audience blanked out, same length so offsets stay valid."""
    text = text or ""
    for start, end in sorted(audience_spans(text), reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


# Owner-written descriptions are mostly English ("a sweet, seductive touch"). Synonyms of attractive
# only; "addictive" and "enchanting" describe the smell to the wearer, not other people's reaction.
_APPEAL = re.compile(
    r"(?<!\w)(?:seduct\w*|attractive\w*|alluring|allure|irresistib\w*|magnetic\w*|sensual\w*|"
    r"sexy|captivating|جذاب\w*|مغري\w*|جاذبي\w*|سكسي)(?!\w)",
    re.I,
)


def describes_appeal(product):
    """Whether the owner's description of this perfume says it is attractive.

    The perfume's own name is removed first: "212 Sexy" is a name, not a description.
    """
    description = _normalize(getattr(product, "description", "") or "")
    name = _normalize(getattr(product, "name", "") or "")
    if name:
        description = description.replace(name, " ")
    return bool(_APPEAL.search(description))


def acknowledged(history):
    """Whether an earlier reply in this conversation already said ACKNOWLEDGEMENT."""
    return any(row.get("role") == "assistant" and ACKNOWLEDGEMENT in (row.get("content") or "")
               for row in history or ())


def with_acknowledgement(question, message, history=None):
    """The first question asked on the turn the goal is stated, opened by the acknowledgement once."""
    if detect(message) and not acknowledged(history):
        return ACKNOWLEDGEMENT + "\n" + question
    return question


def appeal_note(response, products, intent):
    """After a recommendation for this goal, say which offered perfume is described as attractive.

    Deterministic, like `router._uncommon_note`: the model is told the same fact, and this makes sure
    the one recorded answer to the customer's goal reaches them even when the draft leaves it out.
    """
    if not response or not (intent or {}).get(GOAL_KEY) or re.search(r"جذاب", _normalize(response)):
        return response
    from . import naming
    by_name = {product.name: product for product in products}
    named = [name for name in naming.names_in(response, list(by_name)) if describes_appeal(by_name[name])]
    if not named:
        return response
    return response + "\n\n" + appealing_line(named)


RECOMMENDATION_RULE = (
    "The customer wants a perfume other people find attractive (preferences.appeal_goal). "
    "Call a perfume جذاب only when its attractive_scent is true, and say it plainly (\"ريحته جذابة\"), "
    "never that it is described, recorded or classified as attractive. "
    "If none of your choices has it, recommend on scent alone and make no attraction claim. "
    "Never promise how women, men or people will react to the customer."
)


def named_rules(products):
    """Instructions for "X هيخلي البنات تحبني؟" about the named perfume(s) in `products`."""
    appealing = [product.name for product in products if describes_appeal(product)]
    if appealing:
        appeal = ("- العطور دي ريحتها جذابة، وتقدر تقول كده عنها بس (زي \"ريحته جذابة\"): "
                  + "، ".join(appealing) + ".\n"
                  "- ❌ ممنوع تقول عن أي عطر تاني إنه جذاب.\n")
    else:
        appeal = "- ❌ ممنوع تقول عن أي عطر هنا إنه جذاب.\n"
    return (
        "\nالعميل بيسأل إذا كان العطر ده هيخلي الناس (أو البنات/الستات) تحبه أو تنجذبله:\n"
        "- ابدأ بجملة قصيرة إنك متقدرش تضمن ده، زي \"مقدرش أضمن ده يا فندم\".\n"
        "- بعدها قول حقايقه بس: مناسباته وثباته وفوحانه زي ما هي في بياناته، من غير ما تقول للعميل "
        "إنها مسجلة أو موصوفة عندنا.\n"
        + appeal +
        "- ❌ ممنوع تقول إنه هيجذب البنات أو هيخليهم يحبوه أو إنه بيعجب أغلب الناس.\n"
        "- ❌ ممنوع تنصحه أو توعظه، زي \"الأهم تختاره عشان يعجبك\".\n"
        "- خلي الرد جملتين بالكتير.\n"
    )


# Promises about other people's reaction. Checked on every reply: no data supports any of them.
_PROMISE = re.compile(
    rf"(?<!\w)(?:{_CAUSE}(?:ك|كي)?\s+{_AUDIENCE}\s+(?:\S+\s+){{0,2}}?{_LOVE_YOU}|"
    rf"(?:ه[يت]|ب[يت])(?:جذب|عجب|شد|بهر)(?:و|وا)?\s+(?:كل\s+)?(?:ال)?(?:بنات|ستات|حريم|نسوان|ناس|رجاله)|"
    r"(?:جذاب|مغري)\S*\s+(?:جدا\s+)?(?:لل|عند\s+ال)(?:بنات|ستات|حريم|نسوان|ناس|رجاله))"
)
_HEDGE = re.compile(
    r"(?<!\w)(?:مقدرش|ماقدرش|ميقدرش|مايقدرش|محدش\s+يقدر|مش\s+(?:هقدر|متاكد|مؤكد|مضمون|بالضروره)|"
    r"مفيش\s+(?:عطر|برفان|حاجه)|مضمنش|ماضمنش|لا\s+يضمن|مش\s+بيضمن|ميضمنش|"
    r"مش\s+(?:موصوف|مسجل|مكتوب))(?!\w)"
)
_CLAIM = re.compile(r"(?<!\w)(?:جذاب\w*|مغري\w*|[يت]?جذب|ه[يت]جذب|ب[يت]جذب|سكسي|sexy|seductive|attractive)(?!\w)", re.I)


def promise_issues(reply):
    """Unhedged promises that the perfume will make people like the customer."""
    text = _normalize(reply)
    for match in _PROMISE.finditer(text):
        if not _HEDGE.search(text[max(0, match.start() - 60):match.start()]):
            return ["appeal: ممنوع تضمن رد فعل الناس أو إن العطر هيجذب البنات"]
    return []


def unbacked_claim_issues(reply, facts):
    """On a turn about this goal, "جذاب" said of a perfume whose description does not say so."""
    from ..scent_description import _attributed_segments

    issues = []
    for name, segment in _attributed_segments(reply, facts or {}).items():
        if (facts.get(name) or {}).get("appeal"):
            continue
        claim = _CLAIM.search(_normalize(segment))
        if claim and not _HEDGE.search(_normalize(segment)[max(0, claim.start() - 60):claim.start()]):
            issues.append(f"appeal: متقولش إن {name} جذاب")
    return issues
