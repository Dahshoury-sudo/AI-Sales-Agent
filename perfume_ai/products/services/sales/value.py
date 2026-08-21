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
"""

from dataclasses import dataclass
from decimal import Decimal


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


def size_value_note(value):
    """Render a SizeValue for a prompt, keeping "costs more" and "better per ml" apart.

    The explicit ban at the end is not decoration. The persona already says not to invent
    prices and the model still called a dearer bottle cheaper, because the input sentence
    invited it. This one states the direction of the difference in words.
    """
    if value is None:
        return ""

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

    return (
        f"💡 Value Pick: الـ {value.best.volume} ملي أحسن value — "
        f"كمية أكتر بـ {value.extra_volume_pct}%، {money}. "
        f"ابدأ بيه بدل ما تسرد الأسعار كلها من الأول، وبعدها اذكر باقي الأحجام "
        f"باختصار. {warning}"
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
