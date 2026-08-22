"""Recognise what kind of objection a customer just raised.

Objection handling was the weakest behaviour in evaluation: the bot answered "the
perfume I bought from you didn't last" by explaining skin chemistry and bottle
economics, which is a defence, not a response. Addressing an objection requires
knowing *which* objection it is — a price complaint and a longevity complaint need
different evidence and a different opening move.

Deliberately keyword-based rather than a twelfth classifier intent:

  * it costs no LLM call, on a path already spending two or three per turn;
  * it is directly unit-testable, where classifier behaviour is not;
  * when it misses, the caller falls back to today's behaviour rather than a worse one.

The trade is that novel phrasings go unrecognised. That is the right way round for a
detector that changes which prompt a paying customer receives.
"""

from dataclasses import dataclass

from ..static_faq_service import normalize_arabic

# Ordered most-specific first: "ليه أدفع الفرق" also contains price words, and
# "جبت من عندكم وكان مكتوب ثابت" contains both a past purchase and a longevity doubt.
# The first match wins, so the narrower pattern has to come first.
_PATTERNS = (
    (
        "authenticity_doubt",
        (
            "تقليد", "مضروب", "مغشوش", "fake", "اصلي ولا", "اصلى ولا",
            "مش اصلي", "مش اصلى", "بتاع السوق", "رخيص وتقليد",
        ),
    ),
    (
        "longevity_doubt",
        (
            "مش ثابت", "مبيثبتش", "ميثبتش", "مش بيثبت", "الثبات وحش",
            "ثباته وحش", "ريحته بتطير", "بتطير بسرعه", "مش بحسه",
            "مش بحسها", "بعد ساعتين", "بعد ساعه", "الثبات ضعيف",
            "خايف الثبات",
        ),
    ),
    (
        "price_gap",
        (
            "ليه ادفع", "ليه اجيب ده ب", "ليه الفرق", "ايه الفرق في السعر",
            "ادفع الفرق", "بدل 500", "وانا ممكن اجيب", "ممكن اجيب حاجه ب",
        ),
    ),
    (
        "price",
        (
            "غالي", "غاليه", "كتير اوي", "مش قادر", "مش معايا", "معايا بس",
            "السعر عالي", "سعره عالي", "فوق ميزانيتي", "ارخص من كده",
        ),
    ),
    (
        "cant_choose",
        ("مش عارف اختار", "مش عارفه اختار", "محتار", "محتاره", "مش قادر اختار"),
    ),
    (
        "wont_like",
        (
            "مش عارف اذا هتعجبني", "لو مش عجبني", "مش عارف هتعجبني",
            "ميعجبنيش", "لو ملقاهاش حلوه", "مش عارف هيعجبها",
            "مش عارف هيعجبه",
        ),
    ),
    (
        "not_sure",
        ("مش واثق", "مش متاكد", "مش مطمن", "خايف", "خوفي", "قلقان"),
    ),
    (
        "thinking",
        ("هفكر", "هشوف", "هرجعلك", "خليني افكر", "لسه بفكر", "هبصلك تاني"),
    ),
    (
        "tried_before",
        (
            "جبت من عندكم", "جربت قبل كده", "اشتريت قبل كده", "اشتريت من عندكم",
            "المره اللي فاتت", "طلبت قبل كده", "العطر اللي اشتريته",
            "العطر اللي جبته",
        ),
    ),
    (
        # A formed negative opinion, as opposed to `wont_like`'s forward-looking worry.
        # This is the commonest rejection in Arabic retail and the detector had no
        # pattern for it at all, so "العطر اللي رشحتوه ليا مش عاجبني خالص" fell through
        # to the recommendation branch and a dissatisfied customer was answered with
        # "بتدور على عطر رجالي ولا حريمي؟". The persona already has the right play for
        # this ("ممنوع ترشح تاني على طول — اسأله الأول إيه اللي مش عاجبه"); it just
        # never got the chance to apply.
        #
        # Ordered LAST on purpose, against this tuple's most-specific-first rule: a
        # rejection that also mentions a past purchase ("جربت قبل كده ومعجبنيش") is
        # better served by `tried_before`, whose playbook already opens by acknowledging
        # the earlier disappointment. This entry is for a rejection with no purchase
        # attached.
        "rejected",
        (
            "مش عاجبني", "مش عاجبتني", "معجبنيش", "ماعجبنيش", "مش حبيته",
            "مش حبيتها", "لا مش دي", "لا مش ده", "مش دي اللي عايزها",
            "الريحه مش حلوه", "ريحته مش حلوه", "مش عاجبه",
            "مش عجبني", "مش عجبتني",
        ),
    ),
)

# Language that means the customer is talking about a purchase they already made, which
# turns an objection into a complaint: the response has to resolve before it sells.
_PAST_PURCHASE = (
    "جبت من عندكم", "جربت قبل كده", "اشتريت قبل كده", "اشتريت من عندكم",
    "المره اللي فاتت", "طلبت قبل كده", "العطر اللي اشتريته", "العطر اللي جبته",
    "اللي جبته منكم", "اللي طلبته",
    # A recommendation the customer acted on is a past interaction too: "العطر اللي
    # رشحتوه ليا" has to resolve before anything is sold into it.
    "اللي رشحتوه", "اللي رشحتهولي", "العطر اللي رشحته", "اللي نصحتوني",
)


@dataclass(frozen=True)
class Objection:
    kind: str
    matched: tuple = ()
    # True when the customer is describing something they already bought. The reply must
    # acknowledge and resolve that before recommending anything.
    past_purchase: bool = False

    @property
    def is_complaint(self):
        return self.past_purchase


def _normalize(text):
    """normalize_arabic plus tatweel removal.

    normalize_arabic folds alef/ya/ta-marbuta and strips tashkeel but leaves the tatweel
    (ـ) alone, because StaticFAQ keywords depend on its exact behaviour and must not be
    changed from here. Customers write "بـ1200" constantly, so it is stripped locally.
    """
    return normalize_arabic(text).replace("ـ", "")


def detect(message, history=None):
    """The objection in this message, or None.

    Only the customer's own words are examined. History is accepted so callers do not
    have to special-case it, but scanning bot replies would match the objection words
    the bot itself uses when handling one.
    """
    if not message:
        return None

    normalized = _normalize(message)
    if not normalized:
        return None

    past_purchase = any(phrase in normalized for phrase in _PAST_PURCHASE)

    for kind, phrases in _PATTERNS:
        hits = tuple(phrase for phrase in phrases if phrase in normalized)
        if hits:
            return Objection(kind=kind, matched=hits, past_purchase=past_purchase)

    return None


# What each objection needs in front of it before any selling happens. These are prompt
# fragments rather than replies: the model still writes the Arabic, but the *move* is
# decided here so it cannot default to explaining.
PLAYBOOK = {
    "price": (
        "اعترف إن السعر مبلغ حقيقي ومتعتذرش عنه. بعدها وضّح القيمة بالأرقام "
        "اللي في البيانات بس (سعر الملي، الحجم، الثبات لو مكتوب)، واعرض الحجم "
        "الأصغر كنقطة دخول."
    ),
    "price_gap": (
        "الفرق بين السعرين هو السؤال — جاوب عليه بالفروقات الحقيقية الموجودة في "
        "البيانات بس. 🔴 لو العميل بيقارن عطر بسعر تاني (مثلاً \"ليه 1200 وانا ممكن "
        "اجيب حاجة بـ500\")، الإجابة الصح هي المقارنة بين العطرين مش بين أحجام نفس "
        "العطر — ممنوع تحوّل السؤال لكلام عن 50 ملي و90 ملي. ولو الفرق الوحيد هو "
        "الحجم أو شكل الزجاجة، قول كده بصراحة. ولو أولويته إنه يشتري أرخص حاجة "
        "ريحتها حلوة، قوله إن الأرخص أنسب ليه واعرضه عليه بالاسم والسعر."
    ),
    "longevity_doubt": (
        "ابدأ بالاعتراف بإن ده مضايقه فعلاً — مفيش شرح قبل ده. بعدها اتكلم عن "
        "الثبات المكتوب في بيانات العطر ده بالتحديد. لو الثبات مش مسجل في "
        "البيانات، ممنوع تقول رقم ساعات."
    ),
    "authenticity_doubt": (
        "طمّنه على اللي تعرفه فعلاً من حقائق الستور بس. ❌ ممنوع تقول \"مضمون\" "
        "ولا \"100%\" ولا تخترع نسبة تشابه — لو فيه نسبة مكتوبة في حقائق الستور، "
        "انقلها زي ما هي وبس."
    ),
    "tried_before": (
        "ده عميل جرب قبل كده وخرج مش مرتاح. اعترف بده الأول وبوضوح، واسأله سؤال "
        "واحد يحدد المشكلة، وبعدها بس رشّح."
    ),
    "not_sure": (
        "خُد قلقه بجدية ومتقللش منه. ضيّق الاختيار لحاجة واحدة واضحة بدل ما "
        "تعرض عليه كل حاجة."
    ),
    "thinking": (
        "اقبل بلطف ومتضغطش ومتعيدش نفس الترشيح. سيب حاجة واحدة ملموسة بس لو "
        "موجودة فعلاً في البيانات."
    ),
    "cant_choose": (
        "شيل عنه حِمل الاختيار: اسأله سؤال واحد يفرّق (أخف ولا أقوى؟ يومي ولا "
        "للمناسبات؟) وبعدها اختار له أنت."
    ),
    "wont_like": (
        "متوعدش إنها هتعجبه. قلّل المخاطرة بحاجة حقيقية: الحجم الأصغر كبداية، "
        "أو إنه يشمه في الستور لو فيه فرع في حقائق الستور."
    ),
    "rejected": (
        "العميل جرب أو شاف ترشيح ومش عاجبه. ❌ ممنوع ترشح حاجة تانية على طول — ده "
        "بيحس العميل إنك بتخبط. اعترف الأول في جملة قصيرة، وبعدها اسأل سؤال واحد "
        "يحدد إيه اللي مش عاجبه بالظبط (تقيل أوي؟ مسكر أوي؟ ثباته؟ ريحة تانية خالص؟). "
        "استنى رده قبل أي ترشيح."
    ),
}
