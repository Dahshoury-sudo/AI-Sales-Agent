from django.db.models import Q

from products.models import Brand, Product
from .sales import naming
from .sales.brand_preference import matches_brand, preferred_brand_name


# Brand bottles are compounded to order; original bottles need a counted unit.
SELLABLE = Q(variants__bottle_type="normal") | Q(
    variants__bottle_type="original", variants__stock__gt=0
)
STORE_BRAND_EXCLUSIVE = "STORE_BRAND_EXCLUSIVE"


def blocked_brand_ids(exclude_brands, store):
    """Resolve excluded houses against real brands in this store, including the store brand."""
    if not exclude_brands or store is None:
        return frozenset()

    rows = list(
        # `product`, singular: the FK on Product declares no related_name, so the reverse query
        # name is the default lowercase model name.
        Brand.objects.filter(product__store=store, product__is_active=True)
        .values_list("id", "name")
        .distinct()
    )
    if not rows:
        return frozenset()

    store_name = (store.name or "").strip().lower()
    blocked = set()

    for raw in exclude_brands:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.upper() == STORE_BRAND_EXCLUSIVE:
            # The store's own blends, identified the one way this codebase identifies them:
            # `sales.value.is_store_exclusive` compares brand name to store name, and this has to
            # agree with it or a ⭐ product survives an exclusion that named it.
            blocked.update(
                identifier for identifier, name in rows
                if (name or "").strip().lower() == store_name
            )
            continue
        wanted = naming.tokens(text)
        if not wanted:
            continue
        for identifier, name in rows:
            recorded = naming.tokens(name)
            if recorded and (wanted <= recorded or recorded <= wanted):
                blocked.add(identifier)

    return frozenset(blocked)


def _drop_reason(product, intent, max_price, *, blocked_brands=frozenset()):
    """Explain a removed suggestion using a verified constraint or missing catalog fact."""
    gender = (intent.get("gender") or "").lower()
    if gender and product.gender not in (gender, "unisex"):
        return "مش من نفس النوع اللي طلبه"

    perfume_type = (intent.get("perfume_type") or "").lower()
    if perfume_type and (product.perfume_type or "").lower() != perfume_type:
        return "مش من الفئة اللي طلبها"

    from .sales.scent_matching import occasion_matches, season_matches, note_matches, performance_matches
    from .sales.value import eligible_variants

    season = intent.get("season")
    if season and not season_matches(product.season, season):
        return "مش لنفس الموسم اللي قاله"

    brand = intent.get("brand")
    if brand and intent.get("brand_strict") is True and not matches_brand(product, brand, product.store):
        return "مش من البراند اللي طلبه حصرياً"

    # Read off the resolved id set rather than re-matched from `intent`, so this cannot claim an
    # exclusion that `blocked_brand_ids` did not actually apply — the docstring's rule that every
    # branch here only says what it can prove.
    if blocked_brands and product.brand_id in blocked_brands:
        # Two sentences because they are two different facts, and the ⭐ one is the only place the
        # reply can say the true thing: this perfume left because it is ours.
        store_name = (product.store.name or "").strip().lower() if product.store else ""
        if store_name and (product.brand.name or "").strip().lower() == store_name:
            return "ده تركيب بتاعنا، وهو قال إنه عايز براندات أصلية"
        return f"من {product.brand.name}، والعميل قال إنه مش عايز البراند ده"

    if not occasion_matches(product.occasion, intent.get("occasion")):
        return f"المناسبة المسجلة له {product.occasion or 'غير محددة'}، مش المناسبة المطلوبة"
    if not product.scent_tags:
        return "طابع ريحته لسه مش متسجل"
    tags, wanted = set(product.scent_tags), set(intent.get("scent_tags") or ())
    if tags & set(intent.get("excluded_scent_tags") or ()):
        return "طابع ريحته من الأنواع اللي العميل استبعدها"
    if wanted and (not (tags & wanted) if intent.get("scent_match") == "any" else not wanted <= tags):
        reference = intent.get("similar_to")
        return f"طابع ريحته مش مطابق لطابع {reference}" if reference else "طابع ريحته مش مطابق للطلب"
    if any(not note_matches(product, note) for note in intent.get("notes") or ()):
        return "المكونات المسجلة مش بتطابق النوتات المطلوبة"
    if any(note_matches(product, note) for note in intent.get("avoid_notes") or ()):
        return "فيه نوتات العميل استبعدها"
    if not performance_matches(product, intent):
        return "الثبات أو الفوحان المسجل مش بيطابق الطلب"
    variants = [v for v in eligible_variants(product, {**intent, "max_price": None}) if v.volume > 0]
    if not variants:
        return "الحجم أو نوع الزجاجة المطلوب مش متاح"
    if max_price and not eligible_variants(product, intent):
        cheapest = min(v.price for v in variants)
        return f"أرخص حجم مناسب للطلب فيه {cheapest:.0f} جنيه، فوق ميزانيته"

    return None


def product_matches_request(product, intent, store, *, blocked_brands=(), excluded_names=(), reference_id=None):
    """Reused after AI selection so changing stock or labels cannot license a stale offer."""
    from .sales.scent_matching import occasion_matches, season_matches, note_matches, performance_matches
    from .sales.value import eligible_variants

    if intent.get("occasion_needs_clarification"):
        return False
    if store is None or product.store_id != store.pk or not product.is_active or not product.scent_tags:
        return False
    if product.pk == reference_id or product.brand_id in blocked_brands or product.name in excluded_names:
        return False
    gender = intent.get("gender")
    if gender and product.gender not in (str(gender).lower(), "unisex"):
        return False
    perfume_type = intent.get("perfume_type")
    if perfume_type and product.perfume_type != str(perfume_type).lower():
        return False
    if intent.get("brand") and intent.get("brand_strict") is True and not matches_brand(product, intent["brand"], store):
        return False
    if not season_matches(product.season, intent.get("season")) or not occasion_matches(product.occasion, intent.get("occasion")):
        return False
    tags, wanted = set(product.scent_tags), set(intent.get("scent_tags") or ())
    if tags & set(intent.get("excluded_scent_tags") or ()):
        return False
    if wanted and (not (tags & wanted) if intent.get("scent_match") == "any" else not wanted <= tags):
        return False
    if any(not note_matches(product, note) for note in intent.get("notes") or ()):
        return False
    if any(note_matches(product, note) for note in intent.get("avoid_notes") or ()):
        return False
    return performance_matches(product, intent) and any(v.volume > 0 for v in eligible_variants(product, intent))


def search_products(intent, store=None, keep=(), offered=()):
    """All eligible labeled perfumes. AI chooses from these; there are no scent scores."""
    from .sales.scent_preferences import normalize_scent_intent
    from .sales.occasion_preferences import normalize_occasion_updates, PENDING_KEY

    intent = normalize_occasion_updates(normalize_scent_intent(intent))
    products = Product.objects.none()
    reference = None
    needs_reference = False
    if store is not None:
        products = Product.objects.filter(store=store, is_active=True).select_related("brand", "store").prefetch_related("variants")
        if intent.get("similar_to"):
            reference = naming.match_product(intent["similar_to"], store,
                                              products=Product.objects.filter(store=store).select_related("brand"))
            needs_reference = reference is None or not reference.scent_tags
            if not needs_reference:
                inherited = [tag for tag in reference.scent_tags if tag not in (intent.get("excluded_scent_tags") or ())]
                intent["scent_tags"] = list(dict.fromkeys([*inherited, *intent.get("scent_tags", [])]))
    blocked = blocked_brand_ids(intent.get("exclude_brands") or [], store)
    exclusions = list(intent.get("exclude_names") or [])
    if intent.get("exclude_name"):
        exclusions.append(intent["exclude_name"])
    excluded = frozenset(naming.resolve_names(exclusions, store)) if store and exclusions else frozenset()
    catalog = list(products.order_by("pk"))
    rows = [] if needs_reference else [p for p in catalog if product_matches_request(
        p, intent, store, blocked_brands=blocked, excluded_names=excluded,
        reference_id=reference.pk if reference else None,
    )]
    ordered = products.filter(pk__in=[p.pk for p in rows]).order_by("pk")
    surviving = set(keep or ()) & {p.name for p in rows}
    catalog_by_name = {p.name: p for p in catalog}
    dropped = {}
    for name in set(keep or ()) - surviving:
        product = catalog_by_name.get(name)
        dropped[name] = (_drop_reason(product, intent, intent.get("max_price"), blocked_brands=blocked)
                         if product is not None else None) or "مش مطابق للشروط الحالية"
    exhausted = bool(excluded and not rows and not needs_reference and any(
        product_matches_request(p, intent, store, blocked_brands=blocked,
                                reference_id=reference.pk if reference else None) for p in catalog
    ))
    preferred = preferred_brand_name(intent.get("brand"), store)
    relax_key = None
    if not rows and not needs_reference and not intent.get(PENDING_KEY):
        for key in ("scent_tags", "occasion", "longevity", "projection", "max_price", "excluded_scent_tags", "brand"):
            if not intent.get(key) or (key == "brand" and intent.get("brand_strict") is not True):
                continue
            relaxed = {name: value for name, value in intent.items() if name != key}
            if any(product_matches_request(p, relaxed, store, blocked_brands=blocked,
                                           excluded_names=excluded, reference_id=reference.pk if reference else None)
                   for p in catalog):
                relax_key = key
                break
    return {
        "products": ordered, "alternatives": None, "similarity": None,
        "effective_intent": intent, "needs_scent_reference": needs_reference,
        "needs_occasion": bool(intent.get(PENDING_KEY)),
        "relax_key": relax_key,
        "reference_product": reference, "ranked": {},
        "keeping": sorted(surviving), "offered": sorted(offered or ()),
        "dropped": dropped,
        "exhausted": exhausted, "preferred_brand": preferred,
        "brand_strict": bool(intent.get("brand")) and intent.get("brand_strict") is True,
        "cross_brand_ids": [p.pk for p in rows if intent.get("brand") and not matches_brand(p, intent["brand"], store)],
        "excluded_names": excluded, "blocked_brands": blocked,
    }
