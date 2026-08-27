"""Match a perfume name the model produced against a name the catalogue actually holds.

Three separate defects traced to the same missing primitive:

  * `similar_to="9pm by Afnan"` found nothing, because `_resolve_reference` matched with
    `name__icontains=<whole string>` and the row is called "Afnan 9PM". So the similarity
    engine fell back to notes the model *guessed* while the real notes sat in the same
    database.
  * `exclude_names=["9pm by Afnan"]` excluded nothing for the same reason, leaving the
    perfume the customer asked for an alternative to sitting in the candidate pool.
  * `similar_to="Ambiro"` (one letter wrong, for "Ambero") failed both of the above and
    was then persisted to conversation.preferences, poisoning every later turn.

`product_resolver.resolve_products` already resolves names tolerantly, but it costs an
LLM call and is prompt-driven. These call sites are on the hot path and need a cheap,
deterministic, testable match — so the rule is stated here once instead of being
re-approximated at each site.
"""

import re

from ..static_faq_service import normalize_arabic

# Words that carry no identifying information, so an overlap on them alone is not a
# match. Deliberately excludes "homme", "femme", "intense", "extrait" and the like: those
# ARE identifying (Dior Homme is not Dior Sauvage), and stripping them made
# "Dior Homme Intense" indistinguishable from the bare brand word "Dior".
_STOPWORDS = frozenset({
    "eau", "de", "la", "le", "du", "des", "parfum", "perfume", "edp", "edt",
    "cologne", "pour", "by", "the",
    "عطر", "برفان", "بارفان", "بتاع", "من",
})


def tokens(text):
    """Identifying tokens of a name, normalised and stripped of filler.

    Punctuation is replaced with whitespace rather than left attached. Splitting on
    whitespace alone left "Sauvage؟" as a single token, so "بكام Dior Sauvage؟" — an entirely
    ordinary way to ask a price — matched no product at all, while the same message with a
    space before the "؟" matched fine. That silently defeated every deterministic call site:
    `mentioned_in` on the order-cancel branch, `match_product` as the resolver's post-filter,
    and the named-perfume guard in `product_info`.

    `\\W` covers Arabic punctuation (؟ ، ؛) as well as Latin, and Python's `\\w` includes
    Arabic letters and digits, so names carrying numbers ("Afnan 9PM", "XJ 1861 Naxos",
    "Baccarat Rouge 540") tokenise unchanged.
    """
    cleaned = re.sub(r"\W+", " ", normalize_arabic(text or ""), flags=re.UNICODE)
    return {
        token
        for token in cleaned.split()
        if len(token) > 1 and token not in _STOPWORDS
    }


def _similar_enough(left, right):
    """One-edit tolerance for a single-token difference.

    Exists for "Ambiro" vs "Ambero" — a one-character slip in model output that
    otherwise costs the reference, the exclusion, and the persisted preference all at
    once. Deliberately narrow: same length, exactly one differing character, and long
    enough that the coincidence rate is low.
    """
    if left == right:
        return True
    if len(left) != len(right) or len(left) < 5:
        return False
    return sum(1 for a, b in zip(left, right) if a != b) == 1


def _overlap(wanted, candidate):
    """How many of `wanted`'s tokens the candidate carries, allowing one typo each."""
    hits = 0
    for token in wanted:
        if any(_similar_enough(token, other) for other in candidate):
            hits += 1
    return hits


def match_product(name, store, products=None):
    """The catalogue product a name refers to, or None if it is ambiguous.

    A candidate qualifies when one name's identifying tokens are contained in the
    other's, so "sauvage" matches "Dior Sauvage" and "9pm afnan" matches "Afnan 9PM".

    Ambiguity returns None rather than a guess. A bare brand word like "Dior" is a subset
    of three different perfume names here, and silently picking one of them would be
    worse than not matching: `exclude_names=["Dior"]` has to keep excluding every Dior,
    which is exactly what the plain substring filter does when this returns None. An
    exact token match always wins, so "Stronger With You" still resolves to itself rather
    than to "Stronger With You Intensely".
    """
    wanted = tokens(name)
    if not wanted or store is None:
        return None

    if products is None:
        from products.models import Product

        products = Product.objects.filter(store=store, is_active=True).select_related("brand")

    exact, partial = [], []
    for product in products:
        candidate = tokens(product.name)
        if not candidate:
            continue
        hits = _overlap(wanted, candidate)
        reverse = _overlap(candidate, wanted)
        # Subset in either direction, measured with the typo tolerance applied.
        if hits < len(wanted) and reverse < len(candidate):
            continue
        if len(candidate) == len(wanted) and hits == len(wanted):
            exact.append(product)
            continue
        # A bare brand word ("شانيل", "Tom Ford") names a house, not a perfume. It may
        # happen to be a subset of exactly one product name, and returning that product
        # would present an arbitrary pick as though the customer had named it — the
        # similarity engine would then cite its real notes as evidence for a request that
        # never mentioned it. Falling through to None keeps the reference on
        # general-knowledge notes, which the prompt labels as the weaker evidence it is.
        try:
            brand_tokens = tokens(product.brand.name)
        except Exception:
            brand_tokens = set()
        if brand_tokens and wanted <= brand_tokens:
            continue
        partial.append(product)

    if len(exact) == 1:
        return exact[0]
    if exact:
        # Two rows with the same identifying tokens is a catalogue problem, not something
        # to resolve by guessing.
        return None
    return partial[0] if len(partial) == 1 else None


def resolve_names(names, store, products=None):
    """Turn model-produced names into the catalogue spellings they refer to.

    Unmatched names are kept as written: an exclusion the customer clearly meant should
    still be attempted as a substring rather than silently dropped, and a similarity
    target we do not stock is still usable as a general-knowledge reference.
    """
    resolved = []
    for name in names or ():
        if not name:
            continue
        product = match_product(name, store, products)
        value = product.name if product else name
        if value not in resolved:
            resolved.append(value)
    return resolved


def mentioned_in(text, products):
    """Which of `products` the text names.

    Companion to `match_product`, reversed: that one takes a name and finds the product,
    this takes free text and finds every product it refers to. Reuses the same `tokens()`
    and stopword handling, so "Noirvel (90ml)" resolves the way "9pm by Afnan" already does.

    Written for the router's cancel branch. "مش عايز 1 × Noirvel (90ml)" is a request to
    remove one line of two, but it was classified `order_cancel` — "مش عايز" was a listed
    example of it — and the branch then wiped the whole cart, name and address included. The
    order flow already knows how to remove a single item; it needed a way to tell that this
    message names one.

    A product matches when every one of its identifying tokens appears in the text, so
    "Le Male" is not matched by a message that only says "Le". `_similar_enough` gives the
    same one-character tolerance as elsewhere, since a customer retyping a name from a
    summary line will occasionally miss a letter.
    """
    haystack = tokens(text)
    if not haystack:
        return []

    found = []
    for product in products:
        wanted = tokens(product.name)
        if not wanted:
            continue
        if all(
            any(_similar_enough(token, other) for other in haystack)
            for token in wanted
        ):
            found.append(product)
    return found
