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


# The vocabulary a customer uses to ask about a perfume already on the table, rather than to
# name a new one. Written in `normalize_arabic` form — "زجاجه" not "زجاجة", "متاكد" not "متأكد" —
# because `tokens` normalises before comparing. Words already in `_STOPWORDS` ("عطر", "برفان",
# "من") never reach this set.
#
# 🔴 Never add a word here that could be part of a perfume name, transliterated or not. "سبورت",
# "هوم", "بلو", "دارك" and "مليون" are all name components in this catalogue and are all absent
# on purpose. Adding one would make `may_name_a_perfume` blind to that perfume for good.
_REFERENTIAL = frozenset({
    # Pointers and pronouns.
    "ده", "دي", "دا", "هو", "هي", "اللي", "منه", "منها", "بتاعه", "بتاعها",
    # Question words.
    "بكام", "كام", "ايه", "ليه", "امتي", "فين", "هل", "عامل", "عامله", "ازاي",
    # Price, size and bottle vocabulary.
    "سعر", "سعره", "سعرها", "الاسعار", "اسعار", "حجم", "حجمه", "الحجم",
    "الاحجام", "احجام", "ملي", "ml", "زجاجه", "الزجاجه", "اوريجينال",
    "الاوريجينال", "علبه", "بوكس",
    # Attribute vocabulary.
    "ريحه", "ريحته", "ريحتها", "ثبات", "ثباته", "ثباتها", "فوحان", "فوحانه",
    "نوتات", "نوتاته", "مكونات", "مكوناته", "مناسب", "مناسبه", "ينفع", "يناسب",
    "موسم", "موسمه",
    # Availability and doubt.
    "متوفر", "متوفره", "موجود", "موجوده", "متاح", "متاكد", "بجد", "مش", "ولا",
    "فيه", "في", "عندكم", "عندك", "عندكو", "لسه",
    # Discourse particles and confirmations.
    "طب", "طيب", "بقول", "بقولك", "قول", "قولي", "ماشي", "تمام", "ايوه", "اه",
    "لا", "كمان", "برضه", "بس", "خلاص", "يعني", "امال",
    # Quantifiers and ordinals.
    "كل", "واحد", "واحده", "لوحده", "لوحدها", "الاتنين", "التلاته", "التاني",
    "الاول", "الاخير", "بعض", "تاني", "تانيه",
})


def may_name_a_perfume(text):
    """Could this message be naming a perfume, as opposed to asking about one already offered?

    A gate, not a matcher: it answers "is it worth looking a name up at all", and it exists
    because `mentioned_in` cannot answer that question. `mentioned_in` needs the catalogue's
    Latin tokens to appear in the text, so an Arabic-script name matches nothing — and
    `product_info` was reading that empty result as proof the customer had named nothing, then
    answering about whatever it had offered on the previous turn. A customer asked
    "طب اكوا دي جيو ؟" and was told about Y Eau de Parfum, a different perfume from a different
    brand that happened to be the previous recommendation (conversation 738).

    Deliberately liberal, and the asymmetry is the whole point:

      * True on a message that names nothing costs one resolver call, which comes back empty,
        and the caller falls back to the referent it would have used anyway — latency, never a
        wrong answer.
      * False on a message that DOES name a perfume is the conversation-738 bug.

    So `_REFERENTIAL` never has to be exhaustive. A word missing from it makes this slower, not
    wrong, which is why the list above is safe to extend but dangerous to extend carelessly.
    """
    for token in tokens(text):
        if token in _REFERENTIAL:
            continue
        # "90 ملي", "50" — a size, not a name. Names carrying digits ("Afnan 9PM",
        # "XJ 1861 Naxos") tokenise with their words attached, so this cannot swallow one.
        if token.isdigit():
            continue
        return True
    return False


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


def names_in(text, names):
    """Which of `names` the text actually says, in order of first appearance.

    Catalogue names nest. "Stronger With You" is a prefix of "Stronger With You Intensely",
    so the obvious `name.lower() in text` reports the base as said whenever only the flanker
    was said — and every caller downstream then treats two perfumes as one.

    Conversation 768 is what that costs. Turn 1 named only Intensely, the search had injected
    both rows, so `described.under_discussion` recorded the base as under discussion without
    it ever having been said; `ranking.WEIGHTS["continuity"]` promoted that phantom into turn
    3's answer, and the customer was quoted two prices for what they reasonably read as one
    perfume. Asked about it, the bot apologised and retracted the correct one.

    The rule is to consume the LONGEST match at each position: a span already claimed by
    "Stronger With You Intensely" cannot also be claimed by the shorter name nested inside it.
    A name that genuinely appears elsewhere in the text is still found there, so a reply naming
    both perfumes yields both.

    Matched case-insensitively on the stored spelling, which is what every caller needs —
    names are stored and emitted in Latin. An Arabic transliteration matches nothing here, the
    same limitation `already_described` already documents, and that turn behaves as it does
    today.
    """
    haystack = (text or "").lower()
    if not haystack:
        return []

    longest_first = sorted((name for name in names if name), key=len, reverse=True)
    found, remaining = [], set(longest_first)
    position = 0
    while position < len(haystack) and remaining:
        for name in longest_first:
            if name in remaining and haystack.startswith(name.lower(), position):
                found.append(name)
                remaining.discard(name)
                position += len(name)
                break
        else:
            position += 1
    return found


def line_mates(name, catalogue):
    """The other perfumes on `name`'s line: same brand, one name nested in the other.

    A flanker is not "a completely different perfume" the way conversation 738's Acqua di Gio
    was — it shares a brand, a name and a family, and differs in scent, composition and price.
    Nothing in the injected data said so, so on conversation 768's last turn, where only the
    base's row was injected, the model had no way to know the 780 it had quoted three turns
    earlier belonged to a *different* perfume. It apologised for a mistake it had not made and
    declared the base's 700 "السعر الصحيح", leaving the customer believing Intensely costs 700.

    `catalogue` is (name, brand_id) pairs, so one query serves a whole batch.

    "Same line" is deliberately narrow: same brand, and one name's identifying tokens
    contained in the other's. Nesting is the condition that both makes a customer's shorthand
    ambiguous ("سترونجر" fits three rows) and defeats a substring test, so it is the condition
    worth guarding. `Dior Homme Intense` and `Dior Homme Sport` are NOT grouped — neither
    name's tokens contain the other's — and that is an accepted limit, not an oversight.
    Widening to "same brand + 2 shared tokens" would be a change to this function alone.

    Resolved through the line's ROOT rather than pairwise, which is what makes it transitive.
    Asked about Intensely, pairwise containment finds only the base: Absolutely is neither a
    subset nor a superset of it. Rooting on {stronger, with, you} finds both.
    """
    own = tokens(name)
    if not own:
        return []

    brand = None
    for other_name, brand_id in catalogue:
        if other_name == name:
            brand = brand_id
            break
    if brand is None:
        return []

    same_brand = [
        (other_name, tokens(other_name))
        for other_name, brand_id in catalogue
        if brand_id == brand and other_name
    ]

    # The fewest-token name this one contains — itself, when it is already the line's base.
    root = own
    for _, other in same_brand:
        if other and other < root:
            root = other

    return sorted(
        other_name for other_name, other in same_brand
        if other_name != name and other >= root
    )
