from django.db.models import Case, DecimalField, Min, OuterRef, Q, Subquery, When
from products.models import Brand, Product, ProductVariant

from .product_formatting import is_variant_available
from .sales import naming, ranking, similarity
from .sales.notes import expand_request_term
from .sales.value import budget_ceiling, budget_tier


# The AI only ever picks 1-2 perfumes out of whatever we hand it, but every
# product costs ~15 lines of prompt text. Without a cap the "no exact match"
# branch below serialises the entire filtered catalogue into a single request.
MAX_PRODUCTS_IN_CONTEXT = 12

# How many products the Python scorer will look at before trimming to the prompt cap.
# Ranking has to see more candidates than it returns or it cannot reorder anything, but
# it also cannot walk an unbounded catalogue on every turn.
MAX_CANDIDATES_TO_SCORE = 60

# Products that can actually be sold: a brand bottle is compounded to order, an original
# bottle is a counted unit. Both conditions of the original clause sit inside one Q() on
# purpose — split across two, they would match different joined variant rows and a product
# with any original at all would qualify regardless of its stock.
SELLABLE = Q(variants__bottle_type="normal") | Q(
    variants__bottle_type="original", variants__stock__gt=0
)

# The sentinel the extractor emits for the store's own blends — positively in `brand`, negatively
# in `exclude_brands` (ai/intent.py, where it is also named). Was a bare literal in two places
# here and is now needed in four.
STORE_BRAND_EXCLUSIVE = "STORE_BRAND_EXCLUSIVE"

# Cheapest brand bottle, as a correlated subquery rather than
# `annotate(Min('variants__price', ...))`. The queryset already filters on the
# multi-valued `variants` relation, and an aggregate over a relation that is also
# filtered on is computed across the duplicated join rows — so the annotation would be
# quietly wrong. A subquery is evaluated independently of the join.
_CHEAPEST_BRAND_PRICE = Subquery(
    ProductVariant.objects.filter(product=OuterRef("pk"), bottle_type="normal")
    .order_by("price")
    .values("price")[:1],
    output_field=DecimalField(max_digits=10, decimal_places=2),
)


def _by_value(queryset):
    """Order candidates cheapest-brand-bottle first, then by id.

    Replaces `order_by('-oil_stock_grams', 'id')`. That ordering existed to avoid leading
    with empty shelves, a concern that disappears once brand bottles are always
    available — but *something* has to order the shortlist deterministically, because the
    prompts tell the model to stay on a perfume once the customer shows interest and a
    list that reshuffled between turns would undermine that.

    Cheapest-first is the deliberate replacement: the previous ordering was by bulk oil
    inventory, which is commercially arbitrary, and this catalogue serves a
    price-sensitive market. `id` breaks ties so the result is stable.
    """
    return queryset.annotate(_cheapest=_CHEAPEST_BRAND_PRICE).order_by("_cheapest", "id")


def _shortlist(queryset):
    """Trim a candidate queryset down to what fits comfortably in one prompt."""
    return _by_value(queryset)[:MAX_PRODUCTS_IN_CONTEXT]


def _notes_query(notes):
    """One OR query across every requested note.

    Previously each note was a separate chained `.filter()`, i.e. an AND: a perfume had
    to contain *all* of them. Three or four notes essentially never co-occur in one
    product, so a similarity request — which is expanded into exactly that many notes —
    matched nothing, and the caller silently fell through to a branch filtering on
    gender and brand alone. That is the whole mechanism behind "something like Sauvage"
    returning same-brand perfumes that smell nothing alike.

    Partial matches now survive as candidates and are separated by score instead.
    """
    query = Q()
    for note in notes:
        # Accord words ("مسكر", "فريش") name a family, not an ingredient. The expansion
        # table lives in sales.notes so the ranker scores exactly what this filters on.
        for term in expand_request_term(note):
            query |= (
                Q(top_notes__icontains=term)
                | Q(middle_notes__icontains=term)
                | Q(base_notes__icontains=term)
            )
    return query


def _resolve_reference(intent, store):
    """The perfume the customer asked to be matched against, if any.

    Prefers a catalogue hit, whose real notes are the best evidence we can have. Falls
    back to the notes the extractor supplied from general knowledge, which is weaker and
    is labelled as such so the reply phrases it more cautiously.

    Matched through sales.naming rather than `name__icontains=<whole string>`: the
    extractor returns "9pm by Afnan" for a row named "Afnan 9PM", and "Ambiro" for
    "Ambero", both of which a substring test misses entirely — so the strongest evidence
    available (the product's own recorded notes) was being discarded in favour of the
    model's guess about a perfume we actually stock.
    """
    name = intent.get("similar_to")
    if not name:
        return None

    if store is not None:
        match = naming.match_product(name, store)
        if match is not None:
            # Re-fetch with variants so downstream performance comparisons do not
            # trigger a query per candidate.
            match = (
                Product.objects.filter(pk=match.pk).prefetch_related("variants").first()
            ) or match
            return similarity.reference_from_product(match)

    reference_notes = intent.get("similar_to_notes") or []
    if reference_notes:
        return similarity.reference_from_notes(name, reference_notes)
    return None


def _ordered_by_ids(queryset, ids):
    """Re-order a queryset to match a ranked id list.

    Returns a QuerySet rather than a list on purpose: `recommend()` calls `.exists()` on
    this, and the existing tests call `len()` and iterate. Case/When keeps that contract
    intact while letting Python decide the order.
    """
    if not ids:
        return queryset.none()
    ordering = Case(*[When(id=identifier, then=position) for position, identifier in enumerate(ids)])
    return queryset.filter(id__in=ids).order_by(ordering)


def _obtainable_only(queryset):
    """Drop products no size of which can actually be sold.

    With brand bottles always available, the only thing this can now exclude is a product
    whose every variant is an original with zero stock — `SELLABLE` already covers that in
    SQL, so this is a belt-and-braces pass over the same rule expressed through
    `is_variant_available`.

    Kept rather than deleted because the two expressions can drift: `SELLABLE` is a join
    condition and this is the per-variant predicate the renderer uses to mark sizes out of
    stock. If they ever disagree, the model gets shown a size the order flow then refuses,
    which is the failure this function was originally written for.
    """
    unobtainable = [
        product.id
        for product in queryset.prefetch_related("variants")
        # Scoped to products that HAVE sizes but none of them sellable. A product with no
        # variants at all keeps whatever behaviour it had before.
        if product.variants.all()
        and not any(is_variant_available(variant) for variant in product.variants.all())
    ]
    return queryset.exclude(id__in=unobtainable) if unobtainable else queryset


def blocked_brand_ids(exclude_brands, store):
    """Which of this store's brands the customer has ruled out, as ids.

    Resolved against real `Brand` rows rather than filtered with
    `.exclude(brand__name__icontains=...)`, for the same reason `naming.resolve_names` sits on the
    `exclude_names` path below. `icontains` fails asymmetrically: in the positive direction a loose
    match hands the ranker extra candidates and nothing is lost, while in the negative direction it
    DELETES rows and nothing downstream can tell that it happened — `_drop_reason` cannot name it,
    `describe_filters` cannot name it, and the customer sees a shorter list with no explanation. A
    two-letter string would take a house with it; a mis-extracted "Perfume" would empty a shop.

    Matched with `naming.tokens` in either direction, so "Dior" resolves a row recorded as
    "Christian Dior" and "Tom Ford" resolves "Tom Ford Beauty" — the same tolerance the positive
    `brand__name__icontains` already gives, expressed against a finite list of real names instead
    of against a LIKE pattern. `tokens` drops its stopwords, so an entry like "Le" tokenises to
    nothing and resolves to nothing rather than to Le Labo, and an Arabic entry tokenises to tokens
    no `Brand.name` can carry: the Latin-only boundary of this codebase, failing closed.

    Returning ids rather than a Q() is deliberate — it keeps the exclusion legible to the rest of
    the turn. An entry that resolved to nothing did not narrow the search, so `describe_filters`
    must not offer it back as a constraint to relax and `_drop_reason` must not blame it.
    """
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
    """Why a perfume the conversation was on no longer qualifies.

    Computed rather than hinted. The note used to tell the model to say why "(السعر مثلاً)",
    and the model duly asserted price as the cause for a perfume dropped on *gender* — the
    customer had said "راجل" and no budget existed yet. Offering an example invited a guess,
    and a guess about why something was withdrawn is a trust failure, not a wording problem.

    Every branch only claims what it can prove. An earlier version returned a price reason
    whenever a budget existed at all, including a fallback that fired when the perfume *was*
    affordable — so a perfume excluded on season was reported as "مفيش منه حجم داخل ميزانيته"
    while its 50ml sat at 550 against an 800 budget. Reproducing the original bug one layer
    down is easy here; the guard is that each check verifies its own cause.

    Ordered to match how the filters are applied: the `base` criteria first, since those are
    what removed the product from the candidate pool, then budget, which lives on `exact`.

    Returns a short Arabic phrase, or None when the cause is not one we can name — in which
    case the caller says the perfume dropped without inventing a reason for it.
    """
    gender = (intent.get("gender") or "").lower()
    if gender and product.gender not in (gender, "unisex"):
        return "مش من نفس النوع اللي طلبه"

    perfume_type = (intent.get("perfume_type") or "").lower()
    if perfume_type and (product.perfume_type or "").lower() != perfume_type:
        return "مش من الفئة اللي طلبها"

    season = intent.get("season")
    if season and not _text_season_hit(product.season, season):
        return "مش لنفس الموسم اللي قاله"

    brand = intent.get("brand")
    if brand and brand != STORE_BRAND_EXCLUSIVE:
        if brand.lower() not in (product.brand.name or "").lower():
            return "مش من البراند اللي طلبه"

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

    # Budget last, and only when it is demonstrably the blocker.
    #
    # "Demonstrably" now includes the tolerance band: a perfume whose cheapest size is a little
    # over the stated number has not been ruled out by price, it is an upsell the reply is
    # allowed to make. Blaming price for it would withdraw a perfume that is still on offer.
    if max_price:
        cheapest = min(
            (variant.price for variant in product.variants.all()), default=None
        )
        if cheapest is not None and budget_tier(cheapest, max_price) == "far":
            return f"أرخص حجم فيه {cheapest:.0f} جنيه، فوق ميزانيته"

    return None


def _text_season_hit(recorded, wanted):
    lowered = (recorded or "").lower()
    return "all season" in lowered or str(wanted).strip().lower() in lowered


def search_products(intent, store=None, keep=()):
    queryset = Product.objects.filter(is_active=True).prefetch_related('variants')
    if store:
        queryset = queryset.filter(store=store)

    # Only products with something sellable in them.
    queryset = queryset.filter(SELLABLE).distinct()
    queryset = _obtainable_only(queryset)

    gender = intent.get("gender")
    perfume_type = intent.get("perfume_type")
    season = intent.get("season")
    max_price = intent.get("max_price")
    brand = intent.get("brand")
    exclude_names = intent.get("exclude_names") or []
    # Fallback to single exclude_name if present (backward compatibility)
    old_exclude = intent.get("exclude_name")
    if old_exclude and old_exclude not in exclude_names:
        exclude_names.append(old_exclude)

    # Read plainly, with no equivalent of the `old_exclude` append above: that idiom mutates the
    # caller's intent dict in place, which is a pre-existing bug and not one to reproduce.
    exclude_brands = intent.get("exclude_brands") or []

    notes = intent.get("notes") or []

    # Hard filters: the criteria a customer means literally. gender/brand/type/season
    # stay filters because a men's perfume is not a near-miss for a woman.
    #
    # The name exclusions used to be applied here, first. They now run below, after the
    # constraints, so that the queryset *without* them survives as `constrained` — see
    # `exhausted` for what that separation answers. The move is only a move: every clause
    # in this chain is a conjunction over single-valued fields, so the SQL is the same set.
    base = queryset
    if gender:
        base = base.filter(Q(gender=gender.lower()) | Q(gender="unisex"))
    if perfume_type:
        base = base.filter(perfume_type=perfume_type.lower())
    if season:
        base = base.filter(Q(season__icontains=season) | Q(season__icontains="All Seasons"))
    if brand:
        if brand == STORE_BRAND_EXCLUSIVE and store:
            base = base.filter(brand__name__iexact=store.name)
        else:
            base = base.filter(brand__name__icontains=brand)

    # On `base`, with the other hard filters, and ABOVE `constrained` — not below it with the name
    # exclusions. The distinction `constrained` exists to draw is between a perfume the customer
    # *disqualified* and one we are *withholding* because they have already seen it. A refused
    # house is the first kind: it belongs beside gender, which is also a requirement the customer
    # means literally.
    #
    # Below `constrained`, `exhausted` would go true for a search the customer's own exclusion
    # emptied, and `_no_match_instruction` would then say "دي كل الخيارات المتاحة حالياً" about a
    # catalogue full of perfumes it had just filtered out on their instruction — conversation 931's
    # fabricated list with a new cause. `exhausted` below therefore stays keyed on `exclude_names`.
    blocked_brands = blocked_brand_ids(exclude_brands, store)
    if blocked_brands:
        base = base.exclude(brand_id__in=blocked_brands)

    # occasion, longevity and projection are deliberately NOT filtered any more. They were
    # `icontains` ANDs despite being called soft, and `icontains` against an empty column
    # matches nothing — so naming an occasion silently deleted every product whose
    # occasion field the store never filled in. They are ranking signals now.
    reference = _resolve_reference(intent, store)

    # A perfume cannot be its own lookalike. Left in the pool it scores 1.0 against itself —
    # the largest single term in the table — so it wins its own "similar to X" search and hands
    # the model the one perfume the customer has already told us they know.
    #
    # Unconditional, and it did not used to be. There was a carve-out for a reference already
    # under discussion (`keep`), because excluding it removed it from `base` before the
    # keep/dropped logic could see it, and the model then had no data about the perfume the
    # customer was actively talking about — conversation 630: "Intensely مش مناسب لميزانيتك"
    # for a 780 EGP perfume on an 800 EGP budget.
    #
    # But `keep` membership is a proxy for "talking about it rather than wanting a replacement",
    # and the proxy fails whenever both are true at once. Evaluation scenario M1: the customer
    # says "بحب سوفاج", the reply names Dior Sauvage, and the next turn asks for something less
    # mainstream — so Sauvage was under discussion *and* the reference, and it came back first
    # in its own lookalike shortlist, pushing a real candidate off the twelve-slot context.
    #
    # The two needs are about *where* the perfume appears, not whether its data reaches the
    # model, so they are separated: it leaves the candidate list here, and `reference_product`
    # below carries it to the prompt as the comparison target instead
    # (recommendation._reference_block). Conversation 630 stays fixed without M1 breaking.
    if reference is not None and reference.product is not None:
        base = base.exclude(pk=reference.product.pk)

    # Everything the customer actually asked for, before anything is withheld because it has
    # already been offered. Kept so the empty-result branch can answer one question it could
    # not answer before: was this empty because we have run out of matches, or because
    # nothing ever matched?
    constrained = base

    # Exclusions are resolved to catalogue spellings first. An extractor that returns
    # "9pm by Afnan" for the row "Afnan 9PM" excluded nothing at all, so the perfume the
    # customer had just asked for an *alternative* to stayed in the running.
    for name in naming.resolve_names(exclude_names, store):
        base = base.exclude(name__icontains=name)

    # `exhausted` is the difference between "دي كل الخيارات المتاحة" being true and being a
    # fabrication, and it is a fact about the queryset rather than about the conversation.
    #
    # `recommend`'s no-match branch used to fork on `already_described` — had we shown this
    # customer anything at all, ever. Conversation 931 is why that is the wrong question:
    # three perfumes had been described and ordered, then the customer asked for Versace
    # حريمي, which matches nothing (Eros is male). Products had been shown, so the fork said
    # "these are all the options matching your request" — about a brand that had never been
    # offered once. Both replay runs reproduced it, on the turn the branch was rewritten for.
    #
    # The honest test is whether dropping the exclusions would have found anything. If it
    # would, the matches exist and have been used up, and exhaustion is the true story. If it
    # would not — Versace + حريمي — then the constraints emptied the search and no exclusion
    # had anything to do with it, whatever we happen to have shown earlier under different
    # constraints. `bool(exclude_names)` short-circuits the common case to zero extra queries,
    # and the remaining two run only on a search that has already come back empty.
    exhausted = bool(exclude_names) and not base.exists() and constrained.exists()

    exact = base
    if notes:
        exact = exact.filter(_notes_query(notes)).distinct()
    if max_price:
        # The tolerance band, not the bare budget. A product qualifies on having *a* size worth
        # offering, and a size a little over the stated number is one — budget_label says so to
        # the model in as many words, and every size still arrives labelled so the reply can be
        # honest about which is which.
        #
        # Conversation 757 is the cost of the stricter rule: at a 1000 budget it was inert (30
        # of 30 products qualified either way), but at 500 it admitted 4 products out of 30 and
        # hid Invictus — the only Sport perfume in the catalogue — from a customer who had asked
        # for something for the gym.
        #
        # The bound comes from `budget_ceiling` rather than being multiplied out here: the
        # intent hands over a float, and float × Decimal raises TypeError. Inlining it cost a
        # production error on the first real conversation after the change.
        ceiling = budget_ceiling(max_price)
        if ceiling is not None:
            exact = exact.filter(variants__price__lte=ceiling).distinct()

    # Split what the conversation is on into what still qualifies and what a new constraint
    # has just ruled out. The second half matters as much as the first: a perfume vanishing
    # without comment is what made conversation 997 read as random, while saying "Green Irish
    # Tweed خرج من الميزانية" is a useful answer.
    #
    # Budget is applied here rather than taken from `base`, because the price filter lives on
    # `exact` — so splitting on `base` alone reported nothing as dropped on exactly the turn
    # that motivated this (the customer said "معايا 800" while Green Irish Tweed was in the
    # conversation at 3300). Notes are deliberately NOT applied: those narrow the search, and
    # a perfume the customer is discussing should not be evicted because a requested accord
    # happens to be absent from it.
    keep = frozenset(keep or ())
    if keep:
        qualifying = base.filter(name__in=keep)
        if max_price:
            ceiling = budget_ceiling(max_price)
            if ceiling is not None:
                qualifying = qualifying.filter(
                    variants__price__lte=ceiling
                ).distinct()
        surviving = frozenset(qualifying.values_list("name", flat=True))
    else:
        surviving = frozenset()
    # Named with the real cause, product by product. A dropped perfume the customer was
    # discussing is a withdrawal, and a withdrawal needs a true explanation.
    lost = keep - surviving
    dropped = {}
    if lost:
        # `store` joins `brand` in the select: `_drop_reason`'s own-blend branch compares the two
        # names, so without it every dropped product costs an extra query.
        for product in Product.objects.filter(
            store=store, name__in=lost
        ).prefetch_related("variants").select_related("brand", "store") if store else ():
            dropped[product.name] = _drop_reason(
                product, intent, max_price, blocked_brands=blocked_brands
            )
        for name in lost:
            dropped.setdefault(name, None)

    # The reference leaves the candidate list above, so this is the only route its data has to
    # the prompt. Carried on every return path — including the empty ones, where the customer
    # named a perfume and got nothing back and the reply most needs to be able to talk about it.
    reference_product = reference.product if reference is not None else None

    # No ranking signal means nothing can discriminate between candidates, so the legacy
    # ordering is used untouched. This is what keeps the existing shortlist tests honest
    # rather than shuffling an all-equal list through a scorer.
    if not ranking.has_signal(intent, reference) and not keep:
        report = {"keeping": sorted(surviving), "dropped": dropped,
                  "reference_product": reference_product, "exhausted": exhausted}
        if exact.exists():
            return {"products": _shortlist(exact), "alternatives": None,
                    "similarity": None, **report}
        if base.exists():
            return {"products": base.none(), "alternatives": _shortlist(base),
                    "similarity": None, **report}
        return {"products": base.none(), "alternatives": None, "similarity": None, **report}

    # The exact-versus-alternatives decision stays exactly what it was: did anything match
    # the literal criteria? Ranking only decides the *order* within whichever set wins.
    # Letting the score pick the branch was wrong — gender and stock contribute points to
    # every candidate in the pool, so any threshold on the total is really a threshold on
    # a constant offset, and it wrongly promoted a zero-note-match set to "exact".
    matched = exact.exists()
    pool = exact if matched else base
    if not pool.exists():
        return {"products": base.none(), "alternatives": None, "similarity": None,
                "keeping": sorted(surviving), "dropped": dropped,
                "reference_product": reference_product, "exhausted": exhausted}

    candidates = list(_by_value(pool)[:MAX_CANDIDATES_TO_SCORE])
    if surviving:
        # The cap is ordered cheapest-first, so an expensive perfume under discussion could
        # fall outside it and never be scored at all — the continuity weight cannot lift a
        # candidate the scorer never sees. Append any survivor the slice missed.
        seen = {product.pk for product in candidates}
        candidates += [
            product for product in pool.filter(name__in=surviving)
            if product.pk not in seen
        ]
    ranked = ranking.rank(candidates, intent, reference=reference, keep=surviving)
    top = ranked[:MAX_PRODUCTS_IN_CONTEXT]
    ordered = _ordered_by_ids(pool, [entry.product.id for entry in top])

    return {
        "products": ordered if matched else pool.none(),
        "alternatives": None if matched else ordered,
        "similarity": _similarity_summary(reference, ranked),
        # Keyed by product id so the renderer can attach each product's own reasons.
        "ranked": {entry.product.id: entry for entry in top},
        "keeping": sorted(surviving),
        "dropped": dropped,
        "reference_product": reference_product,
        "exhausted": exhausted,
    }


def _similarity_summary(reference, ranked):
    """What to tell the caller about how close we actually got.

    The honesty path for "شبه Sauvage" lives here: when the best candidate is below the
    loose band there is no close match, and saying so is the correct answer rather than
    presenting the nearest perfume as though it were one.
    """
    if reference is None or not reference.is_usable:
        return None

    best = 0.0
    ref_pk = reference.product.pk if reference.product else None
    for entry in ranked:
        if entry.result is not None and entry.product.pk != ref_pk:
            best = max(best, entry.result.score)

    return {
        "reference_name": reference.name,
        "reference_source": reference.source,
        "best_band": similarity.band_for(best),
        "has_close_match": similarity.band_for(best) == "close",
    }
