"""Price and value arithmetic, stated in language that cannot be misread.

The bug this replaces: the size upsell was rendered as "الـ90 ملي أوفر — كمية أكتر بـ80%
بفرق 302 جنيه بس". Every number in that sentence is correct and the sentence is still
wrong, because "أوفر" next to a bare price difference reads as *cheaper by 302* — and the
model duly told a customer the more expensive bottle saved them money.

Two facts have to stay separate: the bigger bottle costs more in total, and it costs less
per millilitre. Collapsing them into one word is the whole defect, so this module returns
them as separate fields and the renderer states both.

Also fixed here: the old comparison picked its baseline by smallest *volume* while picking
the winner by lowest *price per ml*, with nothing guaranteeing the winner cost more. Sizes
like 30ml@500 alongside 50ml@400 produced "بفرق -100 جنيه بس". The baseline is now the
lowest-*priced* bottle, so the difference cannot be negative.

Third defect, fixed by the tier below: correct arithmetic is not automatically a selling
point. `size_value` fires on any per-ml gap at all, which is 60 of 92 catalogue products —
the smallest being 0.4% — and every one of them arrived labelled "أحسن value" with an order
to lead with it, so the bot opened nearly every reply with a value verdict and the
superlative stopped carrying information. The gap is still reported below
STRONG_VALUE_SAVING_PCT, because the misreading guarded against above does not get less
likely when the saving is small; what goes is the claim that a 0.4% gap is the best choice.
"""

from dataclasses import dataclass
from decimal import Decimal

# The per-ml saving a size must clear before it is sold as *the* value pick. Set from the
# catalogue's own distribution: the median gap is 12.6%, so a lower bar makes the verdict the
# default rather than a recommendation. At 22% it applies to 10 of 92 products — Light Blue,
# Black Opium, Fahrenheit, Armani Code and Le Male, the only ones where the per-ml gap is wide
# enough for the claim to carry its own weight.
#
# There is clear air around this number: no catalogue row sits between 18.3% and 23.7%, so
# anything from 20% to 23.6% selects the same ten products, and a repricing has to move a
# perfume a long way to change its tier.
#
# Compared in Decimal, never float. Rows have sat hundredths of a point apart before — Asad at
# 15.022% against Eros at 14.998%, both of which render as "15.0%" — so a float round-trip
# would make the tier of a pair like that a coin toss whenever the threshold lands near them.
STRONG_VALUE_SAVING_PCT = Decimal("22")

# How far above a stated budget a size may sit and still be worth offering. A size just over
# the number the customer named is a real option — `budget_label` has always said so in as many
# words ("تقدر تعرضه مع التوضيح") — and past this multiple it is far enough out that offering it
# reads as not having listened.
#
# Lives here, next to the rest of the price arithmetic, because three separate places needed to
# answer "is this offerable against the budget?" and only one of them used this number. The
# other two compared against the bare budget, which is what lost conversation 757: a customer
# asked for a fresh gym perfume with 1000 to spend, and the two perfumes that actually matched
# had their 90ml at 1032 and 1100 — inside this band, labelled offerable, ranked first in the
# model's own context — while the reply went to a Formal and an Evening/Fall-Winter perfume
# whose only merit was a 90ml under 1000.
BUDGET_TOLERANCE = Decimal("1.2")


def as_budget(value):
    """Coerce an extracted budget to Decimal, or None when it is unusable.

    The intent schema asks for a float and a model may answer "500" or "500.0", so this goes
    through `str` rather than `Decimal(value)` directly — `Decimal(999.99)` is the binary
    expansion 999.98999…, which can fall the wrong side of a boundary comparison.

    Mirrors `recommendation._coerce_budget` and `ranking._as_decimal`, which each predate this
    module owning the budget arithmetic. Callers that already hold a Decimal do not need it.
    """
    if value is None:
        return None
    try:
        budget = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return budget if budget > 0 else None


def budget_ceiling(max_price):
    """The highest price still worth offering against this budget, or None if unusable.

    Exists because a caller needs the bound as a *number* for a SQL comparison rather than a
    predicate. Doing that inline cost a production TypeError: `max_price` arrives from the
    intent as a float, and float × Decimal is unsupported — the one arithmetic pairing
    `_coerce_budget`'s own docstring warns about. Int × Decimal is fine, which is exactly why
    tests passing 1000 did not catch what 1000.0 does.
    """
    budget = as_budget(max_price)
    return None if budget is None else budget * BUDGET_TOLERANCE


def budget_tier(price, max_price):
    """Where one price sits against a budget: "in", "near", or "far".

    One rule with three callers — `product_formatting.budget_label` for what the model is told
    about a size, `search_service` for which products are eligible, and `ranking` for how much
    budget credit a product earns. Those had drifted apart, and the drift was invisible because
    each was locally reasonable: the label said a size 3% over was offerable while the search
    filter had already dropped its product.

    "near" means over the budget but within `BUDGET_TOLERANCE` — recommendable, with the real
    price and the overage said out loud. "far" is not offerable at all.

    No budget means nothing can be over it, so everything is "in".
    """
    budget = as_budget(max_price)
    if budget is None:
        return "in"
    ceiling = budget * BUDGET_TOLERANCE
    price = Decimal(str(price))
    if price <= budget:
        return "in"
    if price <= ceiling:
        return "near"
    return "far"


def price_per_ml(variant):
    """Cost of one millilitre, or None when the size makes that meaningless."""
    if not variant or not variant.volume or variant.volume <= 0:
        return None
    return Decimal(variant.price) / Decimal(variant.volume)


def is_store_exclusive(product):
    """A perfume whose brand is the store itself, i.e. the store's own blend.

    Lives here rather than in product_formatting because both the renderer and the value
    comparison need it, and importing the renderer from here would be a cycle.
    """
    return bool(product.store and product.brand.name.lower() == product.store.name.lower())


@dataclass(frozen=True)
class SizeValue:
    """The better-value size among a product's bottles, and by exactly how much."""

    baseline: object
    best: object
    extra_volume_pct: int
    extra_price: Decimal
    baseline_per_ml: Decimal
    best_per_ml: Decimal

    @property
    def costs_more(self):
        return self.extra_price > 0

    @property
    def saving_pct(self):
        """How much cheaper the best size is per millilitre, as a percentage.

        Decimal in, Decimal out — see STRONG_VALUE_SAVING_PCT on why this must not go
        through float.
        """
        return (self.baseline_per_ml - self.best_per_ml) / self.baseline_per_ml * 100

    @property
    def is_strong(self):
        """Whether the per-ml gap is wide enough to sell as the value pick."""
        return self.saving_pct >= STRONG_VALUE_SAVING_PCT


def size_value(variants):
    """Compare already-eligible bottles and return the value pick, or None.

    Callers filter for eligibility first (fillable, in budget, non-zero volume) because
    that requires oil-stock knowledge this module deliberately does not have.

    Returns None when there is nothing honest to recommend: fewer than two bottles, or
    the cheapest bottle is already the best value per ml.
    """
    priced = [variant for variant in variants if variant.volume and variant.volume > 0]
    if len(priced) < 2:
        return None

    # Baseline is the cheapest bottle, not the smallest. Picking the smallest is what
    # allowed a negative "extra" price.
    baseline = min(priced, key=lambda variant: (variant.price, variant.volume))
    best = min(priced, key=lambda variant: (price_per_ml(variant), variant.volume))

    # Both come from `priced`, so identity is a valid comparison here.
    if best is baseline:
        return None
    if best.volume <= baseline.volume:
        return None

    baseline_per_ml = price_per_ml(baseline)
    best_per_ml = price_per_ml(best)
    if baseline_per_ml is None or best_per_ml is None:
        return None
    if best_per_ml >= baseline_per_ml:
        return None

    return SizeValue(
        baseline=baseline,
        best=best,
        extra_volume_pct=round(
            (best.volume - baseline.volume) / baseline.volume * 100
        ),
        extra_price=Decimal(best.price) - Decimal(baseline.price),
        baseline_per_ml=baseline_per_ml,
        best_per_ml=best_per_ml,
    )


def _money_and_warning(value):
    """The price facts, and the ban that stops them being read backwards.

    Shared by both tiers of `size_value_note`. The direction of the price difference has to
    be stated in words whether or not the gap is worth selling on, because the regression
    this module exists to prevent — a dearer bottle described as saving the customer money —
    does not get less likely when the saving is small.
    """
    if value.costs_more:
        money = (
            f"أغلى بـ {value.extra_price:.0f} جنيه في الإجمالي "
            f"({value.best.price:.0f} مقابل {value.baseline.price:.0f})، "
            f"بس سعر الملي أرخص: {value.best_per_ml:.1f} بدل "
            f"{value.baseline_per_ml:.1f} جنيه للملي"
        )
        warning = (
            "❌ ممنوع تقول إنه \"أرخص\" أو \"بيوفرلك فلوس\" — هو أغلى في الإجمالي، "
            "الأوفر في سعر الملي بس."
        )
    else:
        money = (
            f"نفس السعر تقريباً ({value.best.price:.0f} جنيه) وكمية أكتر بـ "
            f"{value.extra_volume_pct}%"
        )
        warning = "❌ ممنوع تخترع فرق سعر مش موجود."

    return money, warning


def size_value_note(value):
    """Render a SizeValue for a prompt, at whatever strength the arithmetic earns.

    The explicit ban at the end is not decoration. The persona already says not to invent
    prices and the model still called a dearer bottle cheaper, because the input sentence
    invited it. This one states the direction of the difference in words.

    Two tiers, because having only the loud one was its own defect — see the module
    docstring. Above STRONG_VALUE_SAVING_PCT the size is sold as the value pick. Below it
    the size is still named and every number still given, but the superlatives are banned by
    name rather than left for the model to reach for: a 0.4% per-ml gap presented as
    "أحسن اختيار من حيث القيمة" is a claim the data does not support.

    Both tiers say "ابدأ بالحجم ده" and then say what that does *not* mean. The old wording was
    a bare "ابدأ بيه", which reads as "open your reply with this" — and because the line is
    attached to one product and was the only per-product "start with" instruction in the whole
    context, the model obeyed it as a choice of *perfume*. Conversation 762: a customer wanted a
    fresh gym perfume with 1000 to spend, Invictus was ranked first and listed first with its
    50ml in budget at 540, and the reply led with Le Male — the 4th-ranked perfume, and the only
    one of the top four carrying this line. Invictus carries none, because its 90ml is dearer
    per ml than its 50ml, so a perfume was passed over for a reason that has nothing to do with
    whether it suits the gym.
    """
    if value is None:
        return ""

    money, warning = _money_and_warning(value)
    # Said on both tiers: this line orders *sizes inside this perfume*, and nothing else.
    scope = (
        f"لما تذكر أسعار العطر ده، ابدأ بالحجم ده بدل ما تسرد الأسعار كلها، وبعدها اذكر "
        f"باقي أحجامه باختصار. ⚠️ ده ترتيب الأحجام جوه العطر ده بس — مش سبب تبدأ بيه ردك "
        f"ولا تفضّله على عطر تاني أنسب لطلب العميل."
    )

    if value.is_strong:
        return (
            f"💡 Value Pick: الـ {value.best.volume} ملي أحسن value — "
            f"كمية أكتر بـ {value.extra_volume_pct}%، {money}. "
            f"{scope} {warning}"
        )

    return (
        f"💡 اقتراح حجم: الـ {value.best.volume} ملي — "
        f"كمية أكتر بـ {value.extra_volume_pct}%، {money}. "
        f"{scope} "
        f"❌ الفرق في سعر الملي بسيط، فممنوع تقول عليه \"أحسن قيمة\" ولا \"أحسن اختيار\" "
        f"ولا \"أفضل قيمة\" ولا \"أحسن قيمة مقابل سعر\" — اذكر الأرقام وسيب العميل يقرر. "
        f"{warning}"
    )


# Only dimensions we can read off stored data. Anything not populated is reported as
# unknown rather than quietly skipped, so the prompt can forbid inventing it instead of
# leaving a gap the model fills.
_TIER_LABELS = {
    "ultra_niche": "الترا نيش (أعلى فئة)",
    "niche": "نيش",
    "western": "غربي",
    "oriental": "شرقي",
}


def _cheapest_per_ml(product):
    variants = [
        variant for variant in product.variants.all()
        if variant.bottle_type == "normal" and variant.volume
    ]
    rates = [price_per_ml(variant) for variant in variants]
    rates = [rate for rate in rates if rate is not None]
    return min(rates) if rates else None


def cross_product_value(cheaper, dearer):
    """Why one perfume costs more than another, from stored data only.

    Returns (dimensions, unknown). `dimensions` is a list of (label, cheaper, dearer)
    triples we can actually evidence; `unknown` names the axes we hold no data for, so
    the caller can ban speculation about them by name.

    This is the primitive the "ليه أدفع 1200 بدل 500؟" objection needs. Nothing in the
    codebase compared two perfumes numerically before — comparison rendered two
    independent text blocks and left the model to eyeball it.
    """
    dimensions = []
    unknown = []

    for label, attribute in (
        ("الثبات", "longevity"),
        ("الفوحان", "projection"),
        ("الموسم", "season"),
        ("المناسبة", "occasion"),
    ):
        left = (getattr(cheaper, attribute, "") or "").strip()
        right = (getattr(dearer, attribute, "") or "").strip()
        if left and right:
            dimensions.append((label, left, right))
        else:
            unknown.append(label)

    left_tier = _TIER_LABELS.get(cheaper.perfume_type or "")
    right_tier = _TIER_LABELS.get(dearer.perfume_type or "")
    if left_tier and right_tier and left_tier != right_tier:
        dimensions.append(("الفئة", left_tier, right_tier))
    elif not (left_tier and right_tier):
        unknown.append("الفئة")

    left_rate = _cheapest_per_ml(cheaper)
    right_rate = _cheapest_per_ml(dearer)
    if left_rate is not None and right_rate is not None:
        dimensions.append((
            "سعر الملي",
            f"{left_rate:.1f} جنيه",
            f"{right_rate:.1f} جنيه",
        ))

    left_notes = sum(
        1 for field in ("top_notes", "middle_notes", "base_notes")
        if (getattr(cheaper, field, "") or "").strip()
    )
    right_notes = sum(
        1 for field in ("top_notes", "middle_notes", "base_notes")
        if (getattr(dearer, field, "") or "").strip()
    )
    if left_notes and right_notes and left_notes != right_notes:
        dimensions.append((
            "تركيب النوتات",
            f"{left_notes} طبقات مسجلة",
            f"{right_notes} طبقات مسجلة",
        ))

    if is_store_exclusive(dearer) and not is_store_exclusive(cheaper):
        dimensions.append(("الحصرية", "براند عالمي", "تركيب حصري بتاعنا"))
    elif is_store_exclusive(cheaper) and not is_store_exclusive(dearer):
        dimensions.append(("الحصرية", "تركيب حصري بتاعنا", "براند عالمي"))

    return dimensions, unknown


def value_comparison_note(cheaper, dearer):
    """Render a cross-product value comparison, plus a ban on the axes we cannot see."""
    dimensions, unknown = cross_product_value(cheaper, dearer)
    if not dimensions and not unknown:
        return ""

    lines = [
        f"═══ الفرق الحقيقي بين {cheaper.name} و {dearer.name} "
        f"(من البيانات المسجلة بس) ═══"
    ]
    for label, left, right in dimensions:
        lines.append(f"- {label}: {cheaper.name} = {left} / {dearer.name} = {right}")

    if unknown:
        lines.append(
            "⚠️ الحاجات دي غير مسجلة عندنا للعطرين: " + "، ".join(unknown) +
            " — ❌ ممنوع تخترع فرق فيها."
        )

    lines.append(
        "✅ لو الفرق الحقيقي بسيط، قول كده بصراحة. ولو أولوية العميل إنه يشتري أرخص "
        "حاجة ريحتها حلوة، قوله إن الأرخص أنسب ليه — متجبرهوش على الأغلى."
    )
    return "\n".join(lines)
