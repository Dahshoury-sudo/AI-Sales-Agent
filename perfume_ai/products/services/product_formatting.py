"""One renderer for product data in LLM prompts.

This block was copy-pasted into four places — the recommendation prompt, both
branches of product_info, and the comparison prompt — and had already drifted:
only product_info told the model a perfume's type, and comparison used a bare
"Name:" instead of "Name (الاسم الصحيح):", the label that tells the model to use
the database spelling rather than the customer's misspelling.

Divergence here means the model sees different facts depending on which branch
answered, which is the exact inconsistency the prompts spend hundreds of lines
trying to prevent. Both drifts are resolved by including the superset.
"""

from decimal import Decimal
from itertools import islice


# A size priced just over the stated budget is still worth offering as an upsell —
# recommendation's budget_note asks for exactly that. Past this multiple it is far
# enough out that offering it reads as not having listened to the customer.
BUDGET_TOLERANCE = Decimal("1.2")


def budget_label(price, max_price):
    """Tag one price against the customer's budget.

    Without this the model sees a bare list of sizes and prices and cannot tell
    which are affordable, so a customer who said 500 could be shown a 3800 bottle
    as though it were a normal option.
    """
    if max_price is None:
        return ""
    if price <= max_price:
        return " ✅ (داخل الميزانية)"
    if price <= max_price * BUDGET_TOLERANCE:
        return " ⚠️ (أعلى شوية من الميزانية — تقدر تعرضه مع التوضيح)"
    return " ❌ (أعلى من الميزانية بكتير — ممنوع تعرضه)"


# A brand bottle is filled to order from bulk oil, so "stock" is however many
# bottles the remaining oil can fill. At or below this many, say so: the transcript
# showed the bot reading the original-bottle "(2 زجاجة فقط)" out as a neutral fact,
# and brand bottles had no scarcity signal at all.
LOW_BOTTLE_THRESHOLD = 3


def _bottles_fillable(product, variant):
    """How many of this brand-bottle size the remaining oil can fill.

    Returns 0 when a bottle would need no oil at all — a 0ml variant, or a product
    configured at 0% concentration. The previous arithmetic (`oil_stock_grams >=
    required_oil`) reported those as *available* at any stock level, including zero,
    which would offer the customer a size that cannot be filled. Treating them as
    out of stock is the deliberate choice: bad configuration should hide a size, not
    sell it.
    """
    required_oil = (variant.volume * product.concentration_percentage) / 100
    if required_oil <= 0:
        return 0
    return int(product.oil_stock_grams // required_oil)


def _size_lines(product, variants, max_price=None):
    """Split this product's sizes into available and out-of-stock lines.

    Brand bottles are filled from bulk oil, so availability is a calculation
    against oil_stock_grams; original bottles are counted units.
    """
    available, out_of_stock = [], []
    for variant in variants:
        if variant.bottle_type == "normal":
            fillable = _bottles_fillable(product, variant)
            if fillable > 0:
                low_stock = (
                    f" ({fillable} زجاجة فقط)" if fillable <= LOW_BOTTLE_THRESHOLD else ""
                )
                available.append(
                    f"- الـ {variant.volume} ملي: {variant.price} EGP{low_stock}"
                    f"{budget_label(variant.price, max_price)}"
                )
            else:
                out_of_stock.append(f"الـ {variant.volume} ملي")
        elif variant.bottle_type == "original":
            stock = variant.stock or 0
            if stock > 0:
                low_stock = f" ({stock} زجاجة فقط)" if stock <= 3 else ""
                available.append(
                    f"- زجاجة أوريجينال {variant.volume} ملي: {variant.price} EGP{low_stock}"
                    f"{budget_label(variant.price, max_price)}"
                )
            else:
                out_of_stock.append(f"زجاجة أوريجينال {variant.volume} ملي")
    return available, out_of_stock


def _is_store_exclusive(product):
    """A perfume whose brand is the store itself, i.e. the store's own blend."""
    return bool(product.store and product.brand.name.lower() == product.store.name.lower())


def value_pick_note(product, variants, max_price=None):
    """State which brand-bottle size is the better value, and by how much.

    The model was handed a bare list of sizes and prices and read it back as a price
    sheet: "الـ 50 بـ 642، والـ 90 بـ 944" with no recommendation, at the exact moment
    the customer asked to buy. The upsell is arithmetic — 90ml is 80% more perfume for
    47% more money — so it is computed here rather than left to the model, which cannot
    reliably do it and must not invent it.

    Only compares in-budget brand bottles: recommending a size the customer already
    said they cannot afford is not an upsell.
    """
    priced = []
    for variant in variants:
        if variant.bottle_type != "normal" or _bottles_fillable(product, variant) <= 0:
            continue
        if max_price is not None and variant.price > max_price:
            continue
        if variant.volume > 0:
            priced.append(variant)

    if len(priced) < 2:
        return ""

    smallest = min(priced, key=lambda v: v.volume)
    # Best value = lowest cost per ml. Ties go to the smaller bottle, which is the
    # safer recommendation for a first-time buyer.
    best = min(priced, key=lambda v: (v.price / v.volume, v.volume))

    if best.volume == smallest.volume:
        return ""

    extra_volume = round((best.volume - smallest.volume) / smallest.volume * 100)
    extra_price = best.price - smallest.price

    return (
        f"💡 Value Pick: الـ {best.volume} ملي أوفر — "
        f"كمية أكتر بـ {extra_volume}% بفرق {extra_price:.0f} جنيه بس "
        f"(بدل {smallest.price:.0f} جنيه للـ {smallest.volume} ملي). "
        f"ابدأ بيه بدل ما تسرد الأسعار كلها من الأول، وبعدها اذكر باقي الأحجام باختصار."
    )


def _exclusive_selling_note(product):
    """Tell the model what the ⭐ marker is worth commercially.

    The marker existed but carried no instruction, so when a customer asked for
    نيش the bot answered "not available" with three store-exclusive blends sitting
    in its context — the store's own highest-margin products and its real answer.
    """
    if not _is_store_exclusive(product):
        return ""
    return (
        "Sales note: ده عطر تركيب حصري من تصميم الستور، مش موجود عند أي حد تاني. "
        "لو العميل طلب نيش أو حاجة مميزة أو مختلفة — ده هو ردك، ممنوع تقول مفيش."
    )


def _original_bottle_status(product, variants):
    """What the bot may say if asked whether an original bottle exists.

    The exact wording is dictated here rather than left to the model, because the
    two "not available" cases mean different things: a store-exclusive blend has no
    original to copy, while a global brand simply isn't stocked in one.
    """
    if any(variant.bottle_type == "original" for variant in variants):
        return "Available (see sizes below)"
    if _is_store_exclusive(product):
        return (
            'NOT AVAILABLE — this is a store-exclusive perfume (NOT a global brand). '
            'If asked, say EXACTLY: "ده عطر من تصميمنا وابتكارنا إحنا يا فندم، فمفيش منه زجاجة أوريجينال."'
        )
    return (
        f'NOT AVAILABLE — this is a GLOBAL BRAND ({product.brand.name}) perfume, '
        f'NOT store-exclusive. If asked, say EXACTLY: "للاسف مش متوفر منه زجاجة أوريجينال حالياً". '
        f'❌ DO NOT say it is store-exclusive or حصري.'
    )


def format_product(product, max_price=None, brief=False):
    """Render one product as a prompt block.

    brief=True drops stock status, out-of-stock sizes and the scent/performance
    fields — used when suggesting alternatives, where the model only needs enough
    to name something plausible rather than to answer detailed questions.
    """
    variants = list(product.variants.all())
    available, out_of_stock = _size_lines(product, variants, max_price)

    available_text = "\n".join(available) if available else "لا توجد أحجام متوفرة حالياً"
    brand_display = (
        "⭐ عطر تركيب حصري خاص بالمتجر" if _is_store_exclusive(product) else product.brand.name
    )
    perfume_type = product.get_perfume_type_display() if product.perfume_type else "غير محدد"

    if brief:
        # The exclusive note stays even in brief mode: brief is what the "no exact
        # match" branch renders, which is the branch that answered "النيش مش متوفرة"
        # while holding three store-exclusive blends.
        exclusive_note = _exclusive_selling_note(product)
        exclusive_block = f"{exclusive_note}\n" if exclusive_note else ""
        return f"""
Name (الاسم الصحيح): {product.name}
Brand: {brand_display}
Original Bottle: {_original_bottle_status(product, variants)}
Available Sizes & Prices:
{available_text}
{exclusive_block}Gender: {product.gender}
Perfume Type: {perfume_type}
Description: {product.description}
-----------------------
"""

    stock_status = (
        "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if not available else "✅ متوفر"
    )
    out_of_stock_text = "، ".join(out_of_stock) if out_of_stock else "لا يوجد"
    extras = "\n".join(
        line for line in (
            value_pick_note(product, variants, max_price),
            _exclusive_selling_note(product),
        ) if line
    )
    extras_block = f"{extras}\n" if extras else ""

    return f"""
Name (الاسم الصحيح): {product.name}
Brand: {brand_display}
Stock Status: {stock_status}
Original Bottle: {_original_bottle_status(product, variants)}
Available Sizes & Prices:
{available_text}
{extras_block}Out of Stock Sizes (DO NOT OFFER unless explicitly asked):
{out_of_stock_text}
Gender: {product.gender}
Perfume Type: {perfume_type}
Season: {product.season}
Occasion: {product.occasion}
Longevity: {product.longevity}
Projection: {product.projection}
Top Notes: {product.top_notes}
Middle Notes: {product.middle_notes}
Base Notes: {product.base_notes}
Description: {product.description}

-------------------------
"""


def format_products(products, max_price=None, limit=None, brief=False):
    """Render several products, optionally capped.

    The cap is a safety net on prompt size: callers should already be handing over
    a bounded queryset, but one unsliced queryset here used to serialise an entire
    catalogue into a single request.
    """
    selected = islice(products, limit) if limit else products
    return "".join(
        format_product(product, max_price=max_price, brief=brief) for product in selected
    )
