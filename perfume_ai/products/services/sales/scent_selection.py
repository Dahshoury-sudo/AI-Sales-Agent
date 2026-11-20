"""Choose the nearest eligible perfumes, including feasible combined budgets."""
from collections import Counter, defaultdict

from .scent_matching import candidate_key, diverse_pick, fit_key, order_candidates, shared_other_tags
from .value import as_budget, eligible_variants


def combined_budget_quotes(selected, intent):
    """Quote the exact affordable bottle combination chosen for a total budget."""
    from ..product_formatting import ProductQuote

    budget = as_budget(intent.get("max_price"))
    count = intent.get("purchase_quantity") or 2
    if budget is None or len(selected) != count:
        raise ValueError("Choose the requested bottle count within the combined budget.")
    quotes = [ProductQuote.from_variant(min(
        (v for v in eligible_variants(product, intent) if v.volume > 0),
        key=lambda variant: (variant.price, variant.pk),
    )) for product in selected]
    if sum(quote.price for quote in quotes) > budget:
        raise ValueError("The selected bottles exceed the combined budget.")
    return quotes


def nearest_selection(candidates, intent, count, *, keep=(), offered=(), tie_breaker=None):
    if intent.get("budget_scope") != "total":
        return diverse_pick(candidates, intent, count, keep=keep, offered=offered, tie_breaker=tie_breaker)
    ordered = order_candidates(candidates, intent, keep=keep, offered=offered, tie_breaker=tie_breaker)

    budget = as_budget(intent.get("max_price"))
    if budget is None or len(ordered) < count:
        return []
    prices = {p.pk: min(v.price for v in eligible_variants(p, intent) if v.volume > 0) for p in ordered}
    fits = {p.pk: fit_key(p, intent) for p in ordered}

    # Find the best feasible sequence of fits. Cheapest first within equal fits
    # leaves the most money for the next fit, so an expensive tie cannot force
    # a worse companion. Each choice reserves enough for all remaining bottles.
    by_fit = sorted(ordered, key=lambda p: (fits[p.pk], prices[p.pk], p.pk))
    profile, available_budget = [], budget
    for index, product in enumerate(by_fit):
        needed = count - len(profile) - 1
        remaining = sorted(prices[p.pk] for p in by_fit[index + 1:])
        if len(remaining) >= needed and prices[product.pk] + sum(remaining[:needed]) <= available_budget:
            profile.append(fits[product.pk])
            available_budget -= prices[product.pk]
            if len(profile) == count:
                break
    if len(profile) != count:
        return []

    # Honor context and brand ties only when the same optimal fit profile and
    # combined budget remain possible. This does not enumerate combinations.
    # Within those ties, each bottle smells as different from the earlier ones as the budget allows.
    preference, tail = candidate_key(intent, keep=keep, offered=offered, tie_breaker=tie_breaker)
    ranks = {p.pk: (preference(p), tail(p)) for p in ordered}
    required = Counter(profile)
    chosen, used = [], set()

    def completion_cost(excluded):
        grouped = defaultdict(list)
        for product in ordered:
            key = fits[product.pk]
            if product.pk not in excluded and required[key]:
                grouped[key].append(prices[product.pk])
        total = 0
        for key, quantity in required.items():
            if len(grouped[key]) < quantity:
                return None
            total += sum(sorted(grouped[key])[:quantity])
        return total

    for key in profile:
        required[key] -= 1
        nearest_first = sorted(ordered, key=lambda p: (
            ranks[p.pk][0], len(shared_other_tags(p, chosen, intent)), ranks[p.pk][1]))
        for product in nearest_first:
            if product.pk in used or fits[product.pk] != key:
                continue
            rest = completion_cost(used | {product.pk})
            if rest is not None and prices[product.pk] + rest <= budget:
                chosen.append(product)
                used.add(product.pk)
                budget -= prices[product.pk]
                break
        else:
            return []
    return chosen
