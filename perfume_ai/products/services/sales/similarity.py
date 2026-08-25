"""Perfume-to-perfume similarity, scored from the note data we actually hold.

"عايز حاجة شبه Sauvage" used to be answered by asking the model for Sauvage's notes,
dropping them into `intent["notes"]`, and AND-filtering the catalogue on all of them at
once. Three or four notes almost never co-occur in one product, so the filter came back
empty, the search silently fell through to a branch that filters on gender and brand
only, and the customer was handed same-brand perfumes that smell nothing alike.

Similarity is therefore computed here, from data, as a *score* rather than a filter —
and deliberately kept separable from the other axes a recommendation runs on. "Same
occasion" and "same gender" are not similarity; conflating them is what produced
Fahrenheit as an answer for Sauvage.

Two rules this module exists to enforce:

  * The numeric score never reaches a prompt. Handing a model 0.62 is how "95% similar
    to the original" gets born. Callers get a band and the literal shared note names.
  * Below LOOSE there is no close match, and saying so is the correct answer. The
    honesty path is a return value, not an afterthought.
"""

from dataclasses import dataclass

from .notes import families, parse_notes

# Base notes are the drydown — what someone means when they say what a perfume smells
# like. Top notes are the first ten minutes. Weighting them equally made two perfumes
# that merely opened alike look related.
LAYER_WEIGHTS = {"base": 1.0, "middle": 0.7, "top": 0.45}

# Honesty bands. Tunable: these were chosen for a catalogue whose note fields are
# sparsely and inconsistently filled, so they are deliberately forgiving. Revisit them
# against real data rather than treating them as constants of nature.
CLOSE = 0.55
LOOSE = 0.30

# How much of the score comes from exact shared notes versus shared accord families.
# Families carry real weight because store-typed note text rarely matches across
# products; notes carry more because an exact match is stronger evidence.
NOTE_SHARE = 0.6
FAMILY_SHARE = 0.4


@dataclass(frozen=True)
class Reference:
    """The perfume a customer asked to be matched against."""

    name: str
    profile: dict
    accords: frozenset
    # "catalogue" when we resolved it to a product we actually sell and read its real
    # notes; "general_knowledge" when the notes came from the model's world knowledge
    # because we do not stock it. The second is weaker evidence and callers phrase it
    # more cautiously.
    source: str
    product: object = None

    @property
    def is_usable(self):
        return bool(self.profile or self.accords)


@dataclass(frozen=True)
class SimilarityResult:
    """Why one perfume is or is not like the reference.

    Every axis is reported separately so a caller can say "same vibe, but heavier"
    instead of collapsing everything into one number.
    """

    score: float
    band: str
    shared_notes: tuple = ()
    shared_accords: tuple = ()
    same_gender: bool = None
    same_season: bool = None
    same_occasion: bool = None
    # "similar" / "stronger" / "lighter" / None when either side has no data.
    performance: str = None

    @property
    def is_close(self):
        return self.band == "close"


def note_profile(product):
    """Weighted note profile for one product, keyed by normalised note name.

    A note appearing in more than one layer keeps its strongest weight rather than
    summing, so a note listed in all three layers cannot dominate the profile.
    """
    profile = {}
    for layer, weight in LAYER_WEIGHTS.items():
        raw = getattr(product, f"{layer}_notes", "")
        for note in parse_notes(raw):
            if profile.get(note, 0) < weight:
                profile[note] = weight
    return profile


def reference_from_product(product):
    """Best case: the customer named something we stock, so we know its real notes."""
    profile = note_profile(product)
    return Reference(
        name=product.name,
        profile=profile,
        accords=families(profile),
        source="catalogue",
        product=product,
    )


def reference_from_notes(name, notes):
    """Fallback: we do not stock it, so the notes are the model's general knowledge.

    Given a flat note list with no layer information, every note is weighted as a
    middle note — neither claiming drydown authority nor dismissing it as an opener.
    """
    parsed = []
    for note in notes or ():
        parsed.extend(parse_notes(note))
    profile = {note: LAYER_WEIGHTS["middle"] for note in parsed}
    return Reference(
        name=name or "",
        profile=profile,
        accords=families(profile),
        source="general_knowledge",
    )


def _weighted_dice(left, right):
    """Overlap of two weighted note profiles, 0-1.

    Dice rather than Jaccard: it rewards the shared portion more generously, which
    suits profiles of very different lengths — a 4-note catalogue entry compared
    against a 12-note reference should not be punished for brevity.
    """
    if not left or not right:
        return 0.0
    shared = sum(min(left[note], right[note]) for note in left.keys() & right.keys())
    total = sum(left.values()) + sum(right.values())
    return (2 * shared / total) if total else 0.0


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


# Coarse ordinal readings of two free-text fields. Anything unrecognised returns None
# rather than a guess: the whole point of this module is not to manufacture facts.
_PROJECTION_WORDS = (
    (("intimate", "soft", "skin", "خفيف", "هادي"), 1),
    (("moderate", "medium", "متوسط"), 2),
    (("strong", "heavy", "قوي", "فواح"), 3),
    (("enormous", "beast", "nuclear", "جبار"), 4),
)
_LONGEVITY_WORDS = (
    (("weak", "poor", "short", "ضعيف"), 1),
    (("moderate", "medium", "متوسط"), 2),
    (("long", "long-lasting", "ثابت", "طويل"), 3),
    (("eternal", "very long", "يومين", "ابدي"), 4),
)


def peak_hours(text):
    """The largest hour figure a free-text performance field carries, or None.

    Split out of `_ordinal` so a caller can discriminate *inside* an ordinal band without
    re-banding it. `_ordinal` maps everything from 7 to 12 hours onto 3, which is the whole
    7-12 range a real catalogue lives in — so an 8-hour perfume and an 11-hour one are
    indistinguishable to it, and a customer who says الثبات أهم حاجة changes nothing.
    Re-banding is not the fix: `_ordinal` also drives `_performance`, whose stronger/lighter
    verdict appears in the similarity reason line for every comparison in the product.
    """
    if not text:
        return None
    lowered = str(text).lower()
    digits = "".join(character if character.isdigit() else " " for character in lowered)
    hours = [int(part) for part in digits.split() if part]
    return max(hours) if hours else None


def _ordinal(text, vocabulary):
    if not text:
        return None
    lowered = str(text).lower()
    for words, rank in vocabulary:
        if any(word in lowered for word in words):
            return rank
    # "8 hours" and "10-12 ساعة" carry a number rather than a word.
    peak = peak_hours(lowered)
    if peak is not None:
        if peak <= 3:
            return 1
        if peak <= 6:
            return 2
        if peak <= 12:
            return 3
        return 4
    return None


def _performance(reference_product, product):
    """Whether this perfume performs like the reference, where both are recorded."""
    if reference_product is None:
        return None

    for attribute, vocabulary in (
        ("projection", _PROJECTION_WORDS),
        ("longevity", _LONGEVITY_WORDS),
    ):
        left = _ordinal(getattr(reference_product, attribute, ""), vocabulary)
        right = _ordinal(getattr(product, attribute, ""), vocabulary)
        if left is None or right is None:
            continue
        if right > left:
            return "stronger"
        if right < left:
            return "lighter"
        return "similar"
    return None


def _same_free_text(left, right):
    """Whether two free-text fields overlap on any word.

    Returns None when either side is blank. Blank is not "different" — most catalogue
    rows leave these empty, and treating empty as a mismatch is what made the old
    occasion filter delete sparsely-filled products from the candidate set entirely.
    """
    if not left or not right:
        return None
    left_words = {word for word in str(left).lower().replace(",", " ").split() if len(word) > 2}
    right_words = {word for word in str(right).lower().replace(",", " ").split() if len(word) > 2}
    if not left_words or not right_words:
        return None
    return bool(left_words & right_words)


def band_for(score):
    if score >= CLOSE:
        return "close"
    if score >= LOOSE:
        return "loose"
    return "none"


def compare(reference, product):
    """Score one product against the reference the customer named."""
    if reference is None or not reference.is_usable:
        return SimilarityResult(score=0.0, band="none")

    profile = note_profile(product)
    accords = families(profile)

    note_score = _weighted_dice(reference.profile, profile)
    accord_score = _jaccard(reference.accords, accords)
    score = NOTE_SHARE * note_score + FAMILY_SHARE * accord_score

    shared_notes = tuple(
        note for note in profile if note in reference.profile
    )
    shared_accords = tuple(sorted(reference.accords & accords))

    reference_product = reference.product
    return SimilarityResult(
        score=round(score, 4),
        band=band_for(score),
        shared_notes=shared_notes,
        shared_accords=shared_accords,
        same_gender=(
            reference_product.gender == product.gender
            if reference_product is not None and reference_product.gender and product.gender
            else None
        ),
        same_season=_same_free_text(
            getattr(reference_product, "season", None), product.season
        ) if reference_product is not None else None,
        same_occasion=_same_free_text(
            getattr(reference_product, "occasion", None), product.occasion
        ) if reference_product is not None else None,
        performance=_performance(reference_product, product),
    )


def describe(reference, result):
    """One Arabic line of evidence for a prompt — never a percentage.

    The shared notes are named literally so the reply can justify the claim, and the
    percentage ban is restated here because this string is the only thing standing
    between a similarity score and "شبهه بنسبة 95%".
    """
    if reference is None or result.band == "none":
        return ""

    strength = "قريب فعلاً من" if result.is_close else "فيه شبه مع"
    parts = [f"{strength} {reference.name}"]

    if result.shared_notes:
        parts.append("مشترك في: " + "، ".join(result.shared_notes[:5]))
    elif result.shared_accords:
        parts.append("نفس عائلة الريحة: " + "، ".join(result.shared_accords[:3]))

    if result.performance == "stronger":
        parts.append("بس أقوى منه")
    elif result.performance == "lighter":
        parts.append("بس أخف منه")

    if reference.source == "general_knowledge":
        parts.append("(المقارنة على النوتات المعروفة للعطر ده، مش على عطر عندنا)")

    return "Match: " + " — ".join(parts) + ". ❌ ممنوع تذكر نسبة مئوية للتشابه."
