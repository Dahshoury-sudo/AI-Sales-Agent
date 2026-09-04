"""Prove a perfume is not in the catalogue before we tell a customer we do not carry it.

The bot used to answer an unrecognised perfume name with "لحظة أتأكدلك منه" and only deny it if
the customer asked a second time. That deferral was not laziness — it was there because of a
customer who was told Versace Eros was unavailable while it sat in stock at 1019 جنيه. Denying a
perfume we sell is the worst outcome in this system, so the design chose to stall rather than risk
it.

The stall has its own cost, and it is the one this module exists to remove: nothing in the pipeline
ever looks the name up, so the promise cannot be kept. Conversations 795, 798, 799, 816 and 817 all
end the same way — the customer waits, chases, and gets the promise again. 816 turn 3's entire
reply was "لحظة أتأكدلك منه يا فندم." Someone who came to buy was given nothing to buy.

So the denial moves to the first ask, and this module is what makes that safe: a denial now
requires positive evidence that the whole active catalogue has no match, and anything short of
that evidence produces a request to clarify instead. That is why the answer is three states and
not a boolean — see `catalogue_verdict`.

Deliberately a leaf: it imports `products.models` and `.sales.naming` and nothing else. It is not
in `product_info` (already 795 lines, and this has four callers there and elsewhere) and not in
`naming` (scoped to matching primitives, with no opinion about what to say to anyone).
"""

from products.models import Product

from .sales import naming

# Verdicts. Strings rather than an enum because they are compared in prompt-assembly code next to
# other marker strings and read straight out of test assertions.
PRESENT = "PRESENT"
ABSENT = "ABSENT"
UNKNOWN = "UNKNOWN"


def _has_arabic(text):
    """Does the name contain Arabic letters?

    Which script the customer typed decides who is allowed to answer the question. `Product.name`
    holds Latin spellings only — there is no alias column and no Arabic-name column — so Python
    cannot rule an Arabic-script name out of a Latin-only catalogue no matter how careful the token
    matching is. "جنتل مان" shares no character with "Gentleman". Only the extractor, which is
    handed the entire catalogue in its prompt, is in a position to say that name is not in it.

    Both Arabic blocks, because the catalogue's transliterations use letters from the supplement:
    "ڤيرزاتشي" opens with U+06A4, which is outside the base block.
    """
    return any(
        "؀" <= char <= "ۿ" or "ݐ" <= char <= "ݿ" for char in text or ""
    )


def _reported_unplaced(name, resolution):
    """Did the extractor report this exact name as one it could not place?

    Token-set comparison rather than string equality, because the two spellings travel by different
    routes and arrive with different edges. `product_resolver._unplaced_names` strips a leading
    conjunction and nothing else, and the caller here may be passing the span back from
    `Message.internal_context` after a round trip through the record. `naming.tokens` normalises
    alef and ya variants, drops tashkeel and punctuation, and discards filler — so "لادور بخور" and
    "و لادور بخور؟" compare equal, while two genuinely different names still do not.
    """
    wanted = naming.tokens(name)
    if not wanted:
        return False
    return any(
        naming.tokens(reported) == wanted for reported in getattr(resolution, "unplaced", ()) or ()
    )


def catalogue_verdict(name, store, resolution=None):
    """Is this perfume name `PRESENT` in the catalogue, `ABSENT` from it, or `UNKNOWN`?

    Three states, not a boolean, and the third one is the point. `confirm_absent() -> bool` forces
    every uncertain case to be silently filed as one of the other two: read as absent it denies a
    perfume nobody verified, read as present it answers about a perfume nobody identified. "We
    could not tell" has to survive as its own answer, because it has its own reply — ask the
    customer to retype the name, which is a question we can actually resolve, rather than a promise
    to check that no part of this system can keep.

    `resolution` is the `product_resolver.Resolution` this turn produced, when there is one. It
    carries the only two facts Python does not have: whether the extractor call succeeded at all,
    and which Arabic spans the extractor could not place against the catalogue it was shown.
    Attributes are read through `getattr` so a plain list — which is what every
    `mock.patch(..., return_value=[])` in the suite supplies — degrades to "no extra information".

    The ladder below is ordered so that every rung whose answer is "we are not sure" is checked
    *before* the rung that would deny. Only two paths reach `ABSENT`, and each requires a witness.
    """
    name = (name or "").strip()
    if not name:
        return UNKNOWN

    # Nothing to check the name against. A store-less call is a unit test or a misrouted request,
    # and neither is grounds for telling a customer anything about our stock.
    if store is None:
        return UNKNOWN

    # Function words, a size, or a chase verb — "ده", "لقيتو", "90 ملي". `identifying_tokens` is
    # the same residue `may_name_a_perfume` gates on, so a span that names nothing cannot be
    # denied as though it named something. 835 turn 2 sent "ها لقيتو ؟" down this path.
    if not naming.identifying_tokens(name):
        return UNKNOWN

    # The extractor never answered: an API error, a timeout, a malformed payload. Its empty result
    # is not evidence of anything. Without this rung a provider blip denies a stocked perfume.
    if getattr(resolution, "failed", False):
        return UNKNOWN

    products = Product.objects.filter(is_active=True, store=store).select_related("brand")

    exact, partial = naming.candidates(name, store, products=products)
    if exact or partial:
        # Anything the deterministic matcher can place is ours to answer about, and that
        # deliberately includes the case `match_product` refuses: two rows tying on the same
        # identifying tokens. `match_product` returns None there, which is correct for "which one
        # do they mean" and catastrophic for "do we have it" — an ambiguous tie means we have two.
        return PRESENT

    # The active set, not `fallback.SELLABLE`. A product whose variants are all out of stock is
    # still in the catalogue, and the honest reply is a size-scoped answer built from its real
    # rows — "متاح ٥٠ مل بس" — not "we don't carry it". Denials here are about the catalogue;
    # stock is the found-branch's business.

    if naming.names_a_bare_brand(name, store, products=products):
        # `candidates` drops bare-brand partials on purpose, so "Dior" arrives here as ([], []) —
        # shaped exactly like a name we have never heard of. Denying it would tell a customer we
        # do not carry Dior while three Diors sit on the shelf. Ask which one they mean.
        #
        # 🔴 This rung reads one alphabet. `Brand.name` is "Dior", the customer writes "ديور", and
        # there is no alias column, no Arabic-name column and no transliteration in this codebase —
        # so `tokens("ديور") <= tokens("Dior")` is False and an Arabic house name falls through to
        # the witness rung below, where a witness denies it. Closing that here needs an Arabic→Latin
        # bridge, and a guessed one underneath a denial is the mistake this whole module exists to
        # prevent. What covers it instead: `product_resolver`'s rule 9 forbids reporting a bare house
        # name as unplaced, so the witness should never exist; and `router._escalate_absent_name`
        # tells the owner about every denied name on the turn it is denied, so a slip reaches a person
        # immediately rather than sitting in a transcript. Read both before trusting this rung.
        return UNKNOWN

    if _has_arabic(name):
        # Python cannot clear an Arabic name against Latin rows (see `_has_arabic`). The extractor
        # can: it was shown every catalogue name and told to report, in the customer's own script,
        # each name it could not place. That report is the witness, and it is the only one.
        return ABSENT if _reported_unplaced(name, resolution) else UNKNOWN

    # Latin script, tokenises to something identifying, no candidate anywhere in the active
    # catalogue, not a brand. Same alphabet as the rows we searched, so the search was capable of
    # finding it and did not.
    return ABSENT
