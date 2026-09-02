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

from .sales import naming
from .sales.value import (
    BUDGET_TOLERANCE,
    budget_tier,
    is_store_exclusive,
    size_value,
    size_value_note,
)


# A size priced just over the stated budget is still worth offering as an upsell —
# recommendation's budget_note asks for exactly that. Past this multiple it is far
# enough out that offering it reads as not having listened to the customer.
#
# The number and the predicate that reads it live in sales.value now, because selection and
# labelling both need them and having only this module know the number is what let the two
# drift apart. Imported above rather than redefined, so `product_formatting.BUDGET_TOLERANCE`
# keeps resolving for existing callers.

_BUDGET_LABELS = {
    "in": " ✅ (داخل الميزانية)",
    "near": " ⚠️ (أعلى شوية من الميزانية — تقدر تعرضه مع التوضيح)",
    "far": " ❌ (أعلى من الميزانية بكتير — ممنوع تعرضه)",
}


def budget_label(price, max_price):
    """Tag one price against the customer's budget.

    Without this the model sees a bare list of sizes and prices and cannot tell
    which are affordable, so a customer who said 500 could be shown a 3800 bottle
    as though it were a normal option.

    The tiering itself is `sales.value.budget_tier`, shared with the search filter and the
    ranking bonus so that a size this function calls offerable cannot have had its product
    dropped upstream.

    A "near" size carries the overage as a number, because every instruction about such a
    size asks the model to state it — persona rule prompts.py:104, both of recommendation's
    budget notes and its price_instruction all say some form of "قول إنه أعلى بكام". Asking
    for a figure that is nowhere in the data is how the model learns to produce one, and it
    then produces one on turns where there is nothing to state: conversation 912, budget
    1200, reported an in-budget 1046 as "أعلى من ميزانيتك شوية بـ 124 جنيه" — a difference
    invented in the wrong direction about a size labelled ✅.

    Forbidding that in prose was tried first and did not hold (~1 in 20 turns still did it).
    The fix that does is the one `recommendation._in_budget_note` already used for the same
    failure in evaluation scenario X3: name the figure rather than ask for it. Now the only
    sizes with a difference to quote are the ones that have one, so on a ✅ line the request
    is unfillable rather than merely banned.

    "far" deliberately gets no figure. It is the one tier the model may not offer at all, so
    a number there would only be a number to leak.
    """
    if max_price is None:
        return ""
    tier = budget_tier(price, max_price)
    if tier != "near":
        return _BUDGET_LABELS[tier]
    over = Decimal(str(price)) - Decimal(str(max_price))
    return (
        f" ⚠️ (أعلى شوية من الميزانية بـ {over:.0f} جنيه — تقدر تعرضه بشرط تقول الفرق ده "
        f"بالرقم زي ما هو)"
    )


# Original bottles are counted physical units, so a low count is a real fact worth
# saying. At or below this many, say so.
#
# This no longer applies to brand bottles. It used to: "stock" for a brand bottle was
# however many bottles the remaining bulk oil could fill, and the resulting
# "(2 زجاجة فقط)" went into the prompt as scarcity. But oil_stock_grams only ever went
# down — every confirmed order decremented it and nothing replenished it automatically —
# so that count drifted away from reality and the scarcity line became a false urgency
# claim. Brand bottles are compounded to order, so they carry no count at all now.
LOW_BOTTLE_THRESHOLD = 3


def is_variant_available(variant):
    """Whether this size can actually be sold.

    A brand ("تركيب") bottle is compounded to order, so it is always available for an
    active product. An original bottle is a discrete physical unit that either exists or
    does not.

    This replaces `_bottles_fillable`, which divided bulk oil by the bottle's oil
    requirement. That number looked authoritative and was quietly wrong: oil_stock_grams
    decremented on every order and was only ever topped up by hand, so products slid to
    zero and vanished from the catalogue with nobody told. `Dark Aura` finished that slide
    (5g at 30%, unable to fill even a 50ml bottle) and `Dior Sauvage` was six bottles from
    it.
    """
    if variant.bottle_type == "normal":
        return True
    return (variant.stock or 0) > 0


def _size_lines(product, variants, max_price=None):
    """Split this product's sizes into available and out-of-stock lines.

    Brand bottles are compounded to order, so they are always available and carry no
    scarcity count. Only original bottles can be out of stock.

    The `volume > 0` guard stays: a 0ml variant is a configuration error, and offering it
    would put a size on the price list that cannot be filled with anything.
    """
    available, out_of_stock = [], []
    for variant in variants:
        if variant.bottle_type == "normal":
            if variant.volume and variant.volume > 0:
                available.append(
                    f"- الـ {variant.volume} ملي: {variant.price} EGP"
                    f"{budget_label(variant.price, max_price)}"
                )
        elif variant.bottle_type == "original":
            stock = variant.stock or 0
            if stock > 0:
                low_stock = (
                    f" ({stock} زجاجة فقط)" if stock <= LOW_BOTTLE_THRESHOLD else ""
                )
                available.append(
                    f"- زجاجة أوريجينال {variant.volume} ملي: {variant.price} EGP{low_stock}"
                    f"{budget_label(variant.price, max_price)}"
                )
            else:
                out_of_stock.append(f"زجاجة أوريجينال {variant.volume} ملي")
    return available, out_of_stock


# The store's own blend. Defined in sales.value because the value comparison needs it too,
# and importing this module from there would be a cycle.
_is_store_exclusive = is_store_exclusive


def value_pick_note(product, variants, max_price=None):
    """State which brand-bottle size is the better value, and by how much.

    The model was handed a bare list of sizes and prices and read it back as a price
    sheet: "الـ 50 بـ 642، والـ 90 بـ 944" with no recommendation, at the exact moment
    the customer asked to buy. The upsell is arithmetic, so it is computed rather than
    left to the model, which cannot reliably do it and must not invent it.

    The arithmetic and the wording live in sales.value. That split is what fixed the two
    defects this note used to carry: a bigger bottle described as "أوفر" next to a bare
    price difference, which the model read as *cheaper by 302*, and a baseline chosen by
    smallest volume rather than lowest price, which could render a negative difference.

    Only compares in-budget brand bottles: recommending a size the customer already said
    they cannot afford is not an upsell.
    """
    eligible = [
        variant for variant in variants
        if variant.bottle_type == "normal"
        and (max_price is None or variant.price <= max_price)
        and variant.volume > 0
    ]
    return size_value_note(size_value(eligible))


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


# The fields a customer asks about that carry a factual claim. A blank one used to render
# as "Longevity: " with nothing after it, which reads to the model as a gap to fill — and
# it filled them, quoting hours and projection figures no row contained. Naming the gap
# and forbidding it by name is the whole fix.
_CLAIM_FIELDS = (
    ("longevity", "الثبات", "ممنوع تذكر رقم ساعات"),
    ("projection", "الفوحان", "ممنوع تقيّم الفوحان"),
    ("season", "الموسم", "ممنوع تقول مناسب لموسم معين"),
    ("occasion", "المناسبة", "ممنوع تقول مناسب لمناسبة معينة"),
)

_NOT_RECORDED = "غير مسجل"


def _missing_data_note(product):
    """One line naming every unrecorded claim field, and banning invention for each."""
    missing = [
        (label, ban) for attribute, label, ban in _CLAIM_FIELDS
        if not (getattr(product, attribute, "") or "").strip()
    ]
    if not any((getattr(product, layer, "") or "").strip()
               for layer in ("top_notes", "middle_notes", "base_notes")):
        missing.append(("النوتات", "ممنوع تخترع نوتات أو تقول ريحته فيها إيه"))

    if not missing:
        return ""

    labels = "، ".join(label for label, _ in missing)
    bans = "، ".join(ban for _, ban in missing)
    return f"⚠️ بيانات ناقصة للعطر ده ({labels}): {bans}."


# Brief mode withholds the note and performance fields. `_missing_data_note` covers a field the
# *store* never filled in; this covers one that is filled in and simply not being shown — which
# reads to the model exactly the same way, as a gap to fill.
#
# Conversation 795 filled one. An unanchored "Stronger With You" rendered brief, priced at
# 400/700, and sold with "فيها لمسة بخور خفيفة". Its recorded notes are cardamom, pineapple,
# cinnamon, vanilla, chestnut and amberwood — no incense at any layer — so the invented note was
# the whole reason to buy, and nothing in the block said not to invent one.
_WITHHELD_DATA_NOTE = (
    "⚠️ النوتات والثبات والفوحان والموسم والمناسبة مش معروضين للعطر ده هنا: "
    "❌ ممنوع تقول ريحته فيها إيه، ولا تقيّم ثباته أو فوحانه، ولا تقول إنه مناسب لموسم "
    "أو مناسبة — إلا لو مكتوب حرفياً في سطر Description تحت. أي حاجة غير كده تبقى من "
    "عندك، مش من البيانات."
)


def _line_mate_note(mates):
    """Name the other perfumes on this one's line, and forbid merging them into one.

    This catalogue holds Stronger With You, Stronger With You Intensely and Stronger With You
    Absolutely: one brand, one name family, three different scents at three different prices.
    Nothing in the data said so. In conversation 768 the bot recommended Intensely at 780, offered
    the base at 700 two turns later without a word about it being a different perfume, and when the
    customer said "انت قولت سعرين مختلفين للسترونجر" it apologised and declared 700
    "السعر الصحيح". Both prices were right, and the customer left believing Intensely costs 700.

    On that last turn only the base's row was injected, so the model had nothing to reason from —
    which is why `naming.line_mates` reads the catalogue rather than this batch, and the names here
    may well have no data behind them. That is what the closing clause is for: an unpriced name in
    a prompt is otherwise an invitation to offer it.

    Empty for every perfume with no flankers, which is nearly all of them, and the note vanishes.
    """
    if not mates:
        return ""
    return (
        f"⚠️ عطر مختلف عن: {'، '.join(mates)} — نفس البراند ونفس بداية الاسم، "
        "بس ريحة مختلفة وتركيبة مختلفة وسعر مختلف.\n"
        "   ❌ ممنوع تعاملهم كعطر واحد، وممنوع تقول سعر واحد منهم على إنه سعر التاني.\n"
        "   ❌ لو العميل قال اسم مختصر ينفع على أكتر من واحد فيهم، اسأله يقصد أنهي واحد.\n"
        "   ✅ لو بترشح واحد منهم والعميل سمع قبل كده عن واحد تاني، قوله بوضوح إن ده عطر "
        "تاني مختلف — مش نفس اللي اتكلمنا عنه قبل كده.\n"
        "   ❌ الأسماء دي للتوضيح بس، مش قايمة منتجات: ممنوع تعرض أو تسعّر واحد منهم "
        "بياناته مش موجودة فوق."
    )


def _field_or_placeholder(value):
    return value if (value or "").strip() else _NOT_RECORDED


def format_product(product, max_price=None, brief=False, show_prices=True,
                   show_value_pick=True, line_mates=None):
    """Render one product as a prompt block.

    brief=True drops stock status, out-of-stock sizes and the scent/performance fields, and
    says so in `_WITHHELD_DATA_NOTE` — because a withheld field and an unrecorded one read to
    the model identically, and both read as a gap to fill. Two callers want it:
    `recommendation._format_products` for a perfume the customer has already had described, so
    the same scent sentence stops coming back every turn, and `identification_service` for a
    guess we do not stock. Prices stay in both cases, so "بكام" is still answerable.

    show_prices=False drops sizes, prices and the value pick. Comparison needs it: that
    prompt forbids mentioning any price, while this block was simultaneously instructing
    the model to lead with them — two opposite orders in one request, and an "أوفر" verdict
    about one perfume's size ladder could be read back as a verdict about the other.

    show_value_pick=False drops only the value pick, keeping sizes and prices. Recommendation
    needs the narrower version: with a budget stated it must still show every price so the
    ✅/⚠️/❌ budget labels mean something, but a turn about *which perfume* should not open
    with a verdict about *which size*. Leading with the size on every recommended perfume is
    how one injected line became the opening sentence of nearly every reply.

    line_mates is the list of catalogue names on this perfume's line (see `_line_mate_note`),
    supplied by `format_products` because it takes a query the whole batch can share. Present on
    the brief block too: brief means the customer has heard of this perfume already, which is
    when confusing it with its flanker is most likely, not least.
    """
    variants = list(product.variants.all())
    available, out_of_stock = _size_lines(product, variants, max_price)

    available_text = "\n".join(available) if available else "لا توجد أحجام متوفرة حالياً"
    brand_display = (
        "⭐ عطر تركيب حصري خاص بالمتجر" if _is_store_exclusive(product) else product.brand.name
    )
    perfume_type = product.get_perfume_type_display() if product.perfume_type else "غير محدد"
    sizes_block = (
        f"Available Sizes & Prices:\n{available_text}\n" if show_prices else ""
    )

    if brief:
        # The exclusive note stays even in brief mode: brief is what the "no exact
        # match" branch renders, which is the branch that answered "النيش مش متوفرة"
        # while holding three store-exclusive blends.
        #
        # The flanker warning stays for a sharper reason. Brief is also what
        # `recommendation._format_products` renders for a perfume the customer has *already* had
        # described, and re-offering a perfume the customer half-remembers is conversation 768's
        # exact shape — the turn where two correct prices became one retracted apology. Withholding
        # the warning on precisely the turn it matters most is backwards.
        brief_extras = "\n".join(
            line for line in (
                _line_mate_note(line_mates),
                _exclusive_selling_note(product),
                _WITHHELD_DATA_NOTE,
            ) if line
        )
        return f"""
Name (الاسم الصحيح): {product.name}
Brand: {brand_display}
Original Bottle: {_original_bottle_status(product, variants)}
{sizes_block}{brief_extras}
Gender: {product.gender}
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
            # Identity before price: which perfume this is has to settle before the model
            # starts recommending a size of it.
            _line_mate_note(line_mates),
            value_pick_note(product, variants, max_price)
            if show_prices and show_value_pick else "",
            _exclusive_selling_note(product),
            _missing_data_note(product),
        ) if line
    )
    extras_block = f"{extras}\n" if extras else ""
    out_of_stock_block = (
        f"Out of Stock Sizes (DO NOT OFFER unless explicitly asked):\n{out_of_stock_text}\n"
        if show_prices else ""
    )

    return f"""
Name (الاسم الصحيح): {product.name}
Brand: {brand_display}
Stock Status: {stock_status}
Original Bottle: {_original_bottle_status(product, variants)}
{sizes_block}{extras_block}{out_of_stock_block}Gender: {product.gender}
Perfume Type: {perfume_type}
Season: {_field_or_placeholder(product.season)}
Occasion: {_field_or_placeholder(product.occasion)}
Longevity: {_field_or_placeholder(product.longevity)}
Projection: {_field_or_placeholder(product.projection)}
Top Notes: {_field_or_placeholder(product.top_notes)}
Middle Notes: {_field_or_placeholder(product.middle_notes)}
Base Notes: {_field_or_placeholder(product.base_notes)}
Description: {product.description}

-------------------------
"""


def _line_mates_for(products):
    """Map each product's name to its line-mates, with one catalogue query for the batch.

    Deliberately reads the catalogue rather than intersecting the batch. The turn that broke
    conversation 768 had *only* the base's row injected, so a batch-relative answer would have
    left that turn exactly as broken — the whole point is to name a perfume whose data is not
    here.

    Skipped unless the batch resolves to exactly one store. `Product.store` is nullable, callers
    may hand over rows built in a test or drawn from more than one catalogue, and "same brand"
    only means "same line" inside one of them.

    Never raises. This is the reply hot path: a missing warning costs a disclaimer, an exception
    costs the whole reply.

    One query per call, and `recommendation._format_products` calls the renderer once per product
    when ranking ran — so a recommendation turn pays a handful. Left alone deliberately: it is a
    two-column scan of one store's catalogue on a turn already spending seconds in LLM calls, and
    a cache here would either go stale on a newly added perfume or need invalidation nobody asked
    for.
    """
    stores = {getattr(product, "store_id", None) for product in products}
    if len(stores) != 1 or None in stores:
        return {}

    try:
        from products.models import Product

        catalogue = list(
            Product.objects.filter(store_id=stores.pop(), is_active=True)
            .values_list("name", "brand_id")
        )
        return {
            product.name: naming.line_mates(product.name, catalogue)
            for product in products
        }
    except Exception:
        return {}


def format_products(products, max_price=None, limit=None, brief=False, show_prices=True,
                    show_value_pick=True):
    """Render several products, optionally capped.

    The cap is a safety net on prompt size: callers should already be handing over
    a bounded queryset, but one unsliced queryset here used to serialise an entire
    catalogue into a single request.

    The slice is materialised so the line-mate lookup can be done once for the batch instead of
    once per product — and so an `islice` over a queryset is not consumed by the lookup before the
    render sees it.

    The lookup runs for brief batches too. It used to be skipped on the grounds that brief was
    throwaway; brief is what an already-described perfume renders as, which is exactly when a
    flanker gets mistaken for it.
    """
    selected = list(islice(products, limit) if limit else products)
    mates = _line_mates_for(selected)
    return "".join(
        format_product(
            product, max_price=max_price, brief=brief,
            show_prices=show_prices, show_value_pick=show_value_pick,
            line_mates=mates.get(product.name),
        )
        for product in selected
    )
