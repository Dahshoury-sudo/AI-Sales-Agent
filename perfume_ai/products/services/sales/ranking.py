"""Explainable ranking of candidate perfumes.

Before this there was no ranking at all. `search_products` applied a cascade of hard SQL
filters and ordered whatever survived by `-oil_stock_grams` — most bulk oil first. So no
signal could outrank another, because nothing was weighed against anything: a criterion
either deleted a product or did nothing.

That is the mechanism behind the "شبه Sauvage" failure. Similarity was expanded into notes
and AND-filtered, the filter emptied the set, and the fallback matched on gender and brand
alone — which is how "similar to Sauvage" returned Fahrenheit. Weighing the signals
instead makes the fix expressible: scent DNA is worth five times an occasion match, so a
same-occasion perfume that smells nothing alike cannot win.

Two properties worth preserving deliberately:

  * Every score carries `reasons`, in Arabic, naming the evidence. The prompt shows those
    rather than a number, so the model can justify a recommendation from data instead of
    inventing a justification — and cannot quote a similarity percentage that does not
    exist.
  * With no discriminating signal every score is equal and the tie-break falls through to
    the caller's ordering. That used to be `(-oil_stock_grams, id)`; oil tracking is gone,
    and `search_service._by_value` now hands candidates over cheapest-brand-bottle first,
    so an all-equal set comes back in that order.

That second property is load-bearing and was also, for a while, a trap. Weighing signals is
only half the job: a signal that every candidate satisfies *identically* weighs the same as
no signal at all. Conversation 736 ("عايز حاجه للجيم تكون فريش", 1200, male) tied eight
perfumes at exactly 7.50 — notes 2.0 + occasion 1.0 + gender 3.0 + budget 1.5 — because
`notes` was a boolean and every daytime occasion answered "gym" in full. The sort is stable,
so what reached the model was the fallback ordering: cheapest first. A Fall/Winter gourmand
led a gym request at 400 جنيه and the only `Sport`-tagged perfume in the catalogue came
fourth. Hence `note_fit` and `_PARTIAL_OCCASION_CREDIT`: both exist to make a signal
capable of ordering the candidates that satisfy it, not just separating them from the ones
that do not.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import similarity
from .notes import (
    HEAVY_FAMILIES,
    HEAVY_NOTES,
    LIGHT_FAMILIES,
    expand_request_term,
    families,
    opposing_families,
    request_families,
)
from .value import budget_tier

# Signal weights. The relative sizes are the point: an explicit request to be *like*
# something specific is the strongest thing a customer can say, and an explicit exclusion
# ("مش عايز حاجة تقيلة") must be able to sink a product that scores well on everything
# else. Occasion and season sit deliberately low — matching them is not similarity.
WEIGHTS = {
    "similarity": 5.0,
    "notes": 2.0,
    "avoid": -3.0,
    "gender": 3.0,
    # A perfume that suits either sex, credited ONLY while `gender` is unresolved — a stated
    # requirement is answered by the weight above and this one stays out of it.
    #
    # The arithmetic half of a preference that used to be prompt-only.
    # recommendation._gender_note asked the model to "فضّل اللي ينفع للجنسين", but
    # search_products applies no gender filter at all when gender is None
    # (search_service.py:244) and nothing here scored `unisex` — so what reached the model was
    # a mixed-gender shortlist, ordered with no such preference, plus an instruction to filter
    # it by eye. Prompt asking, arithmetic not delivering.
    #
    # Sized *strictly below every other weight in this table*, which is the whole
    # specification: a full match on any single stated signal — even the weakest, season at
    # 0.8 — outranks it. It has to be able to break a tie, since the failure recorded at the
    # top of this module is eight perfumes at exactly 7.50 where the sort fell through to
    # cheapest-first, but it must never decide against something the customer said out loud.
    #
    # 0.8 was the first attempt and was wrong, in a way worth recording because the mistake is
    # easy to repeat: it was reasoned from "a full note match earns WEIGHTS['notes'] = 2.0 and a
    # half match 1.0, so 0.8 cannot bridge that". `note_fit` is not boolean — it grades by mass
    # share, so a perfume whose two notes are the two requested ones scores ~0.5 per note, mean
    # 0.5, notes 1.0. The real gap between that and a 1-of-2 match is 0.5, and 0.8 stepped
    # straight over it: a unisex perfume matching one requested note outranked a gendered one
    # matching both. The test named for this pins the gap end to end rather than the constant.
    #
    # Note what this cannot promise. `notes` is a mean, so the gap between n-of-n and
    # (n-1)-of-n shrinks as n grows — at four requested notes it is ~0.25 — and no positive
    # constant is below every fractional gap. The guarantee is over *whole* signals, not
    # arbitrarily fine ones. Making it airtight would mean a secondary sort key instead of a
    # weight, which buys a guarantee at the cost of never breaking a near-tie, and near-ties
    # are most of the value here (the season lean below was worth adding over a gap of 0.06).
    #
    # Under `avoid` (-3.0) in magnitude so it cannot rescue a perfume a stated exclusion sank,
    # and under `continuity` (2.5) so it cannot pull the conversation off a perfume the
    # customer is converging on.
    #
    # What it buys off: a wrong-gender guess is not a one-turn cost. `keep` /
    # described.under_discussion holds those picks near the top for two further turns at
    # `continuity` each, so the mistake compounds before the customer's answer can undo it.
    #
    # Measured on the live catalogue (16 unisex of 80 active), it reorders the top five on
    # roughly three of eight realistic gender-unknown requests and leaves the rest untouched —
    # "سويت" promotes three unisex perfumes past a 0.01 gap, "فريش للصيف" changes nothing
    # because the taste gaps there run 0.2-0.3 wide. That is the intended shape, and it is why
    # neither prompt may tell the model the shortlist leads with a unisex perfume.
    "gender_safe": 0.4,
    # Already under discussion. Sized against the rest of this table on purpose:
    #
    #   * above the sum of the slots that shift as a conversation narrows (budget 1.5,
    #     occasion 1.0, longevity 1.0, projection 0.8, season 0.8), because a customer adding
    #     one of those is *refining* — so refining must not be able to displace the perfume
    #     they were converging on;
    #   * below `similarity`, so an explicit "عايز حاجة شبه X" still moves the conversation;
    #   * unable to rescue anything `avoid` (-3.0) has sunk, which keeps a real exclusion
    #     authoritative.
    #
    # Without it, conversation 997 dropped Le Male (50ml at 623) on the turn the customer said
    # "معايا 800". It survived every hard filter and sat at rank 3, and the prompt asks for the
    # best 1-2 — so "stay on the perfume he showed interest in" was unfollowable.
    "continuity": 2.5,
    "budget": 1.5,
    "longevity": 1.0,
    "occasion": 1.0,
    # What a *stated* occasion implies about season, when the customer named no season of
    # their own. Equal to `season` deliberately: nothing here is inferred about the customer
    # — they said "للجيم" outright — so this is a stated preference read against a recorded
    # field, and it should weigh what the same field weighs when named directly. It also has
    # to clear ~0.47 to do anything at all: below that an All-Seasons gourmand still outranks
    # the Spring/Summer perfume it was beating, and the signal changes no ordering.
    #
    # Still below `continuity` (2.5), so it cannot pull the conversation off a perfume the
    # customer is converging on, and occasion + season + occasion_season + projection = 3.6
    # stays under `similarity` (5.0).
    "occasion_season": 0.8,
    "projection": 0.8,
    "season": 0.8,
    "uncommon": 1.2,
}

# Below this a candidate matches nothing the customer discriminated on. Reported per
# entry rather than used to pick a branch: gender and stock contribute points to every
# candidate in the pool, so a threshold on the *total* score is really a threshold on a
# constant offset. search_service keeps its original exact-versus-alternatives decision
# (did anything match the literal criteria?) and uses ranking only for ordering.
RELEVANCE_FLOOR = 1.0


@dataclass
class Ranked:
    product: object
    score: float = 0.0
    reasons: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    result: object = None

    @property
    def matched_something(self):
        """Whether any positive signal fired, as opposed to baseline points only."""
        return bool(self.reasons)


def _as_decimal(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number > 0 else None


def has_signal(intent, reference=None):
    """Whether anything in this intent can actually discriminate between products.

    When nothing can, the caller keeps the legacy ordering rather than shuffling an
    all-equal list through a scorer.

    An unresolved gender counts, because `rank` now credits `unisex` with
    WEIGHTS["gender_safe"] in exactly that case. Without this clause the weight would be
    unreachable on the requests that need it most: search_service.py:349 skips `rank`
    entirely when this returns false, and the router's own `has_taste_info` counts `brand`,
    `perfume_type` and `max_price` (router.py:487-505) — none of which appear below. So
    "عايز حاجة من ديور" with no gender reached the recommend-and-ask path and was never
    scored at all, which is precisely the turn the unisex lean exists for.
    """
    if reference is not None and reference.is_usable:
        return True
    if not intent:
        return False
    # Checked before the key sweep, not folded into it, because it is the *absence* of a
    # value that discriminates here — `any()` over present keys cannot express that.
    if not intent.get("gender"):
        return True
    return any(
        intent.get(key)
        for key in ("notes", "avoid_notes", "avoid_traits", "occasion",
                    "longevity", "projection", "season", "wants_uncommon")
    )


def _text_hit(field_value, wanted):
    """Does a free-text catalogue field mention what the customer asked for?

    A blank field is a miss, never a match — but crucially the caller scores it as zero
    rather than excluding the product, which is the difference between this and the
    `icontains` AND-filter it replaces. That filter deleted every sparsely-filled product
    from the candidate set the moment a customer named an occasion.
    """
    if not field_value or not wanted:
        return False
    return str(wanted).strip().lower() in str(field_value).lower()


# What the extractor emits for these two slots, as the same 1-4 scale similarity._ordinal
# reads catalogue text onto.
_WANTED_LONGEVITY = {
    "weak": 1, "poor": 1, "short": 1,
    "moderate": 2, "medium": 2, "average": 2,
    "long": 3, "long-lasting": 3, "long lasting": 3, "longlasting": 3,
    "eternal": 4, "very long": 4, "very long-lasting": 4,
}
_WANTED_PROJECTION = {
    "intimate": 1, "soft": 1, "skin": 1, "close": 1,
    "moderate": 2, "medium": 2,
    "strong": 3, "heavy": 3, "loud": 3,
    "enormous": 4, "beast": 4, "nuclear": 4,
}


def _ordinal_hit(field_value, wanted, vocabulary, words):
    """Score a graded request against a graded catalogue value, 0.0-1.0.

    This replaces `_text_hit` for longevity and projection, and it exists because that
    substring test was dead on arrival for longevity: the extractor emits
    'long-lasting' / 'eternal' / 'moderate', while stores type '8 hours', '8–10 hrs',
    '12 hours'. No product in a real catalogue ever matched, so a customer whose stated
    top priority was الثبات contributed exactly zero to the ranking — and evaluation
    caught the bot recommending a 6-hour perfume while saying out loud that its
    longevity was lower.

    similarity._ordinal already parses both the words and the hour numbers onto a 1-4
    scale, so it is reused rather than reimplemented. Meeting or beating the request
    scores full; one grade short scores half, because a 7-hour perfume is a real answer
    to "ثابت" even if it is not the best one; further short scores nothing.
    """
    if not field_value or not wanted:
        return 0.0

    target = vocabulary.get(str(wanted).strip().lower())
    if target is None:
        # An unmapped request value falls back to the old substring behaviour rather
        # than silently scoring zero.
        return 1.0 if _text_hit(field_value, wanted) else 0.0

    actual = similarity._ordinal(field_value, words)
    if actual is None:
        return 0.0
    if actual >= target:
        return 1.0
    return 0.5 if target - actual == 1 else 0.0


# The widest ordinal band, in hours: `similarity._ordinal` maps everything from 7 to 12 onto
# 3, and 3 is what "long-lasting" asks for. A real catalogue lives almost entirely inside that
# band, so every candidate scored an identical 1.0 and longevity discriminated nothing —
# raising WEIGHTS["longevity"] to any value would have multiplied a constant and reordered
# nothing. Evaluation scenario M1: nine of eleven candidates tied, and the customer who had
# just said "بس اهم حاجه الثبات" was handed an 8-hour perfume over an 11-hour one.
_BAND_FLOOR, _BAND_CEILING = 7, 12

# Ordering *within* a band, never across one. Capped at a quarter of the signal's own weight,
# which keeps it far below `continuity` (2.5) — a refinement must not displace the perfume the
# customer was converging on — and leaves the full-credit hit untouched, so a product whose
# only reason was a longevity match still has one.
_WITHIN_BAND_FRACTION = 0.25

# What a product earns on budget when its best offerable size is over the stated number but
# inside the tolerance band. Half, so the two facts stay ordered correctly: a perfume the
# customer can buy at the price they named still beats an equally good one that costs more,
# but half a point cannot outweigh a real match — occasion (1.0) + occasion_season (0.8) +
# notes (2.0) clear it comfortably.
#
# That ordering is the whole fix for conversation 757. Full credit here would have made an
# over-budget perfume score the same as an affordable one, which is a different bug in the
# opposite direction; no credit at all would leave a product eligible for the candidate list
# and simultaneously flagged as having nothing the customer can afford.
_TOLERANCE_BUDGET_FRACTION = 0.5


def _within_band_bonus(key, recorded, hit):
    """Break a tie inside an ordinal band using the recorded hours.

    Additive on top of the hit, and deliberately tiny: it decides between two perfumes that
    already satisfy the request, not whether the request is satisfied.
    """
    if hit < 1.0:
        return 0.0
    peak = similarity.peak_hours(recorded)
    if peak is None:
        return 0.0

    span = _BAND_CEILING - _BAND_FLOOR
    above = min(max(peak - _BAND_FLOOR, 0), span)
    return WEIGHTS[key] * _WITHIN_BAND_FRACTION * (above / span)


def _best_budget_tier(product, max_price):
    """The best budget tier among this product's *sellable* bottles.

    A brand bottle is compounded to order so it always counts; an original counts only
    while stock remains. Worth checking separately from the SQL price filter, which
    qualifies a product through *any* variant under budget — so a product whose only
    affordable size is a sold-out original would otherwise score as in-budget.

    Returns "in" when some sellable size is inside the budget, "near" when the best one is
    only inside the tolerance band, and "far" when nothing is offerable. Replaces the old
    boolean: with the tolerance band now eligible, "affordable or not" could no longer
    express the difference between a perfume the customer can buy today and one that costs
    a little more than they said.
    """
    best = "far"
    for variant in product.variants.all():
        if variant.bottle_type == "normal":
            sellable = True
        else:
            sellable = (variant.stock or 0) > 0
        if not sellable:
            continue
        tier = budget_tier(variant.price, max_price)
        if tier == "in":
            return "in"
        if tier == "near":
            best = "near"
    return best


# How a requested note's fit splits between "how much of this perfume literally is that
# ingredient" and "does its accord composition lean that way". Sized to mirror
# similarity.NOTE_SHARE / FAMILY_SHARE, for the same reason: an exact note is stronger
# evidence, but store-typed note text is too sparse for exact matching alone to rank.
NOTE_MASS_SHARE = 0.6
ACCORD_SHARE = 0.4


def note_fit(term, profile, accords):
    """How well one perfume answers one requested note or accord, 0.0-1.0.

    Public because `fallback.suggest_alternatives` needs the same verdict. It cannot reach it
    through `rank`: a `Ranked.matched_something` is true for a budget reason as well as a note
    one, and the fallback path must promote a perfume only because it actually smells like
    what the customer asked for.

    This replaces a boolean membership test, and the boolean is the whole bug behind
    conversation 736. `notes: ["fresh"]` made `len(matched) / len(wanted)` either 1/1 or
    0/1, so eight perfumes collected an identical 2.0 and the shortlist fell through to
    `search_service._by_value` — price ascending. A Fall/Winter gourmand led a gym request
    because it costs 400 جنيه, and the one perfume the catalogue tags `Sport` came fourth.

    Note that layer weight is deliberately *not* the discriminator on its own.
    `similarity.LAYER_WEIGHTS` puts base at 1.0 and top at 0.45, which is right for "what
    does this smell like after an hour" and backwards for "is this fresh": citrus lives in
    the top. Ranking by the strongest matching note would put Stronger With You (`lavender`
    0.7, a middle note) above Invictus (`grapefruit` 0.45, a top note). The accord balance
    carries the discrimination; the mass term keeps a literal one-ingredient request honest.
    """
    total = sum(profile.values())
    if not total:
        return 0.0

    expansion = expand_request_term(term)
    matched_mass = sum(
        weight for note, weight in profile.items()
        if any(wanted in note for wanted in expansion)
    )
    if not matched_mass:
        return 0.0
    mass_share = matched_mass / total

    # Tolerant lookup on the product side: an accord request has to be able to see through
    # "marine notes" and "amberwood", or the perfumes whose note fields are written that way
    # read as having no character at all. The request side is a *stated* definition rather
    # than one derived from the expansion — deriving it let `neroli`'s secondary `floral`
    # widen "fresh" until a white floral counted as fresh. See notes.REQUEST_FAMILIES.
    wanted_families = request_families(term)
    if not wanted_families or not accords:
        # An ingredient outside the family table ("elemi", "hedione") can still be matched
        # literally; there is simply no composition to weigh it against.
        return mass_share

    opposed = opposing_families(wanted_families)
    balance = (
        len(accords & wanted_families) - len(accords & opposed)
    ) / len(accords)
    return NOTE_MASS_SHARE * mass_share + ACCORD_SHARE * max(balance, 0.0)


def rank(products, intent, reference=None, keep=()):
    """Score and order candidates, best first.

    `keep` names perfumes already under discussion, which earn WEIGHTS["continuity"]. That is
    what makes the persona's "stay on the perfume he showed interest in" rule followable at
    all: ranking re-runs from scratch every turn, and a perfume the customer was converging on
    could fall below the top 1-2 the prompt asks the model to choose from — at which point the
    competing rule "a product not in the data does not exist" made staying impossible.
    """
    intent = intent or {}
    keep = frozenset(keep or ())
    max_price = _as_decimal(intent.get("max_price"))
    wanted_notes = [note for note in (intent.get("notes") or ()) if note]
    avoid_notes = [note for note in (intent.get("avoid_notes") or ()) if note]
    avoid_traits = {str(trait).strip().lower() for trait in (intent.get("avoid_traits") or ())}
    avoid_heavy = bool({"heavy", "suffocating", "loud", "strong"} & avoid_traits)

    ranked = []
    for product in products:
        entry = Ranked(product=product)

        if reference is not None and reference.is_usable:
            result = similarity.compare(reference, product)
            entry.result = result
            entry.score += WEIGHTS["similarity"] * result.score
            description = similarity.describe(reference, result)
            if description:
                entry.reasons.append(description)
            elif result.band == "none":
                entry.mismatches.append(f"مش قريب من {reference.name}")

        profile = similarity.note_profile(product)
        accords = families(profile)

        matched_notes = []
        if wanted_notes:
            # Tolerant families here only. `accords` above stays exact because it feeds the
            # avoid_heavy penalty, which compares two products against each other; this asks
            # how much of *one* perfume is the requested accord, where a family missed for
            # want of an adjective is the whole answer.
            fit_accords = families(profile, tolerant=True)
            fits = []
            for note in wanted_notes:
                # Scored through the same expansion table the SQL filter uses. Without this
                # an accord request ("مسكر") filtered to the gourmands and then scored every
                # one of them zero, because no product has a note literally named "sweet" —
                # so the ordering fell back to bulk-oil stock and the reasons line came out
                # empty, leaving the model no evidence to recommend from.
                fit = note_fit(note, profile, fit_accords)
                fits.append(fit)
                if fit > 0:
                    matched_notes.append(note)
            # Proportional, not all-or-nothing: matching two of three requested notes is
            # a real partial match, and the AND-filter this replaces scored it as zero.
            # A term that matches nothing contributes a zero to the mean, which is what
            # keeps that proportionality intact now the per-term value is graded.
            entry.score += WEIGHTS["notes"] * (sum(fits) / len(fits))
            if matched_notes:
                # The requested terms, never the fit value: a number rendered here is how
                # "شبهه بنسبة 95%" gets born, and reasons_note is read straight into a prompt.
                entry.reasons.append("فيه " + "، ".join(str(n) for n in matched_notes))

        hit_avoided = [
            note for note in avoid_notes
            if any(str(note).strip().lower() in existing for existing in profile)
        ]
        if hit_avoided:
            entry.score += WEIGHTS["avoid"]
            entry.mismatches.append("فيه " + "، ".join(str(n) for n in hit_avoided))

        if avoid_heavy:
            heaviness = len(accords & HEAVY_FAMILIES) + len(set(profile) & HEAVY_NOTES)
            if heaviness >= 2:
                entry.score += WEIGHTS["avoid"]
                entry.mismatches.append("ممكن يكون تقيل على العميل")
            elif accords & LIGHT_FAMILIES:
                entry.score += abs(WEIGHTS["avoid"]) * 0.25
                entry.reasons.append("خفيف ومش خانق")

        for key, attribute, label, mismatch_template, verdict_for in (
            ("occasion", "occasion", "مناسب للمناسبة اللي قالها",
             "مناسبته المسجلة {recorded}، مش اللي قاله", _occasion_verdict),
            ("season", "season", "مناسب للموسم",
             "موسمه المسجل {recorded}، مش اللي قاله", _season_verdict),
        ):
            wanted = intent.get(key)
            if not wanted:
                continue
            recorded = (getattr(product, attribute, "") or "").strip()
            if not recorded:
                # Blank is unknown, never a mismatch. Treating empty as "different" is what
                # made the old icontains AND-filter delete every sparsely-filled product the
                # moment a customer named an occasion; _missing_data_note covers the gap.
                continue
            verdict, credit = verdict_for(recorded, wanted)
            if verdict == MATCH:
                entry.score += WEIGHTS[key] * credit
                entry.reasons.append(label)
            elif verdict == CONFLICT:
                # A conflicting value used to produce nothing at all, so the model saw a ✅
                # line pulling one way and no counter-signal — which is how a perfume recorded
                # Evening/Formal was sold as "مناسبة للنهار" two turns after the bot had
                # correctly called it an evening scent. The recorded value is named so the
                # reply can state the truth and pivot, rather than only stay quiet.
                entry.mismatches.append(mismatch_template.format(recorded=recorded))

        # What the occasion implies about season, when the customer named no season.
        #
        # Ranking-only, and that is not a stylistic choice: `intent["season"]` is a *hard SQL
        # filter* (search_service.py, `season__icontains`), so writing an inferred season into
        # the intent would delete every Fall/Winter perfume from the candidate set instead of
        # ranking it lower. Three more things read that slot and would all start lying —
        # `_drop_reason` would report "مش لنفس الموسم اللي قاله", `constraints._SEASON` would
        # echo "للصيف" back at a customer who never said it, and `constraints.TASTE_KEYS`
        # counts it toward the gate that decides whether we know enough to recommend at all.
        #
        # Guarded on the customer not having named a season, because if they did, that axis
        # already owns the question and is the stronger statement.
        if intent.get("occasion") and not intent.get("season"):
            recorded_season = (getattr(product, "season", "") or "").strip()
            lean = _occasion_season_lean(intent["occasion"], recorded_season)
            if lean == FAVOURED:
                entry.score += WEIGHTS["occasion_season"]
                entry.reasons.append(f"موسمه المسجل ({recorded_season}) مناسب للنشاط اللي قاله")
            elif lean == DISFAVOURED:
                entry.score -= WEIGHTS["occasion_season"]
                # Named rather than silent, for the same reason the occasion conflict is: the
                # reply has to be able to say why it is steering away from this one.
                entry.mismatches.append(
                    f"موسمه المسجل {recorded_season}، مش الأنسب للمجهود اللي قاله"
                )

        # Graded, not substring-matched — see _ordinal_hit. Partial credit is reported as
        # a mismatch rather than a reason so the reply cannot claim a perfume "matches"
        # a longevity request it only half meets.
        for key, attribute, vocabulary, words, label, short_label in (
            ("longevity", "longevity", _WANTED_LONGEVITY, similarity._LONGEVITY_WORDS,
             "ثباته بيطابق طلبه", "ثباته أقل من اللي طلبه شوية"),
            ("projection", "projection", _WANTED_PROJECTION, similarity._PROJECTION_WORDS,
             "فوحانه بيطابق طلبه", "فوحانه أقل من اللي طلبه شوية"),
        ):
            wanted = intent.get(key)
            if not wanted:
                continue
            recorded = getattr(product, attribute, "")
            hit = _ordinal_hit(recorded, wanted, vocabulary, words)
            if hit <= 0:
                entry.mismatches.append(
                    f"{'ثباته' if key == 'longevity' else 'فوحانه'} مش بيطابق طلبه"
                )
                continue
            entry.score += WEIGHTS[key] * hit + _within_band_bonus(key, recorded, hit)
            reason = label if hit >= 1.0 else short_label
            # The recorded value rides along so the reply can quote it instead of asserting
            # "وثباتهم كويس" with no number, which is what the evaluation caught it doing on
            # the turn the customer said longevity mattered most.
            if hit >= 1.0 and (recorded or "").strip():
                reason = f"{reason} ({str(recorded).strip()})"
            entry.reasons.append(reason)

        # A stated gender scores an exact match; an unresolved one leans safe instead.
        #
        # A unisex product deliberately earns nothing when a gender *was* stated, even though
        # search_service.py:245 admits it through the filter — an exact match is the better
        # answer to a requirement the customer made out loud, and that ordering predates this.
        #
        # The reason string is not decoration: reasons render into the "✅ ليه مناسب" line that
        # reasons_instruction points the model at for its justification, so the reply can state
        # the perfume works either way *from data* rather than asserting it — which red line 6
        # in the persona forbids. It is also what lets _gender_note stop guessing.
        wanted_gender = str(intent.get("gender") or "").lower()
        if wanted_gender:
            if product.gender == wanted_gender:
                entry.score += WEIGHTS["gender"]
        elif product.gender == "unisex":
            entry.score += WEIGHTS["gender_safe"]
            entry.reasons.append("ينفع للراجل وللست")

        if product.name in keep:
            entry.score += WEIGHTS["continuity"]
            entry.reasons.append("العميل بيتكلم عنه بالفعل")

        if intent.get("wants_uncommon"):
            from .value import is_store_exclusive

            if is_store_exclusive(product):
                entry.score += WEIGHTS["uncommon"]
                entry.reasons.append("تركيب حصري بتاعنا — مش منتشر عند حد تاني")

        if max_price is not None:
            tier = _best_budget_tier(product, max_price)
            if tier == "in":
                entry.score += WEIGHTS["budget"]
            elif tier == "near":
                entry.score += WEIGHTS["budget"] * _TOLERANCE_BUDGET_FRACTION
                entry.reasons.append("أقرب حجم مناسب أعلى شوية من ميزانيته")
            else:
                entry.mismatches.append("مفيش حجم متاح داخل ميزانيته")

        ranked.append(entry)

    # Equal scores fall through to the order the caller supplied, which
    # search_service._by_value has already sorted cheapest-brand-bottle first. The
    # `-oil_stock_grams` term that used to sit here went with oil tracking; there is
    # deliberately no replacement, because re-sorting on a second criterion here would
    # override the caller's ordering rather than defer to it.
    ranked.sort(key=lambda entry: -entry.score)
    return ranked


# Occasion text conflates two independent things — when a perfume is worn and how dressed-up
# it is — so "Evening/Formal" is both. A flat synonym table cannot express that: mapping
# office→formal let an evening perfume pass an office request and be sold as "مناسب للنهار",
# which is the exact bug the mismatch warning exists to prevent, while refusing to map
# anything flagged Le Male ("Casual") as unsuitable for daytime.
#
# So only the time-of-day axis is judged, and only a genuine conflict on it is a warning.
# Anything else — a vocabulary that simply does not line up — stays silent, on the same
# principle as a blank field: unknown is not a mismatch.
_DAYTIME_TERMS = ("daily", "casual", "everyday", "office", "work", "business", "sport", "gym")
_EVENING_TERMS = ("evening", "night", "dinner", "party", "parties", "club")

# Working out and going to the office are not the same errand, and collapsing them into one
# bucket is the second half of conversation 736. `_occasion_verdict("Casual/Evening", "gym")`
# matched on the word "casual" and took the full occasion weight, so Stronger With You —
# Fall/Winter, Casual/Evening — scored exactly what Invictus scored, and Invictus is the one
# product in the catalogue whose occasion field literally reads `Sport`.
_SPORT_TERMS = ("sport", "gym", "athletic", "training", "workout", "رياضة", "جيم")

# Asymmetric on purpose, and the asymmetry is the point. A sport fragrance genuinely *is* a
# daytime fragrance, so `Sport` has to keep satisfying "بالنهار" — sport therefore stays in
# the recorded-side tuple above. What was wrong was the *asked* side: a request for the gym
# was read as a request for daytime, which any office scent then answered in full.
_ASKED_DAYTIME_TERMS = tuple(
    term for term in _DAYTIME_TERMS if term not in _SPORT_TERMS
)

# What a Casual perfume earns against a gym request. Partial rather than CONFLICT because a
# casual fresh scent really is wearable to the gym — just less apt than something built for
# it — and calling half the catalogue a mismatch would manufacture warnings the reply then
# has to explain away. Graded credit is the pattern `_ordinal_hit` already uses for the two
# performance slots.
_PARTIAL_OCCASION_CREDIT = 0.5

# A perfume the store marked as year-round matches any season asked for. search_service
# already ORs `season__icontains="All Seasons"` into its filter; ranking has to agree, or a
# year-round perfume gets warned about for every specific season.
_ALL_SEASONS = ("all season", "all-season", "كل الفصول", "كل المواسم")

# An occasion can carry a season the customer never named. Going to the gym is exertion and
# heat, so what the store recorded as a Fall/Winter perfume is a worse answer than what it
# recorded as Spring/Summer — and after the two fixes above that difference still cost
# nothing, because the season block only runs when `intent["season"]` is set and a customer
# who says "عايز حاجه للجيم" never sets it. Dior Homme Sport (Spring/Summer) sat below four
# All-Seasons gourmands, and a Fall/Winter one was 0.06 behind it.
#
# Extensible by design — beach and travel belong here eventually — but only sport has an
# entry, because only sport is a claim about exertion rather than about a time of day.
_OCCASION_SEASON_LEAN = (
    (
        _SPORT_TERMS,
        ("spring", "summer", "ربيع", "صيف"),
        ("fall", "autumn", "winter", "خريف", "شتا"),
    ),
)

NEUTRAL, FAVOURED, DISFAVOURED = 0, 1, -1


def _occasion_season_lean(wanted, recorded):
    """Whether a recorded season suits the activity the customer named.

    NEUTRAL covers three different kinds of "no opinion" on purpose: a blank field, a
    year-round perfume, and an occasion that implies nothing about season. None of them is a
    mismatch — the same rule `_occasion_verdict` and `_season_verdict` follow, and the reason
    the old `icontains` AND-filter had to be replaced in the first place.
    """
    lowered = str(recorded or "").strip().lower()
    if not lowered:
        return NEUTRAL
    # Deferring to _ALL_SEASONS rather than re-listing the markers, so this cannot come to
    # disagree with _season_verdict about what the store meant by "All Seasons".
    if any(marker in lowered for marker in _ALL_SEASONS):
        return NEUTRAL

    asked = str(wanted or "").strip().lower()
    for terms, favoured, against in _OCCASION_SEASON_LEAN:
        if asked not in terms:
            continue
        # Favoured is checked first, so a perfume recorded across both halves of the year
        # ("Summer/Winter") is credited rather than warned about.
        if any(marker in lowered for marker in favoured):
            return FAVOURED
        if any(marker in lowered for marker in against):
            return DISFAVOURED
    return NEUTRAL

MATCH, CONFLICT, UNKNOWN = "match", "conflict", "unknown"


def _occasion_verdict(recorded, wanted):
    """(verdict, credit) for a recorded occasion against a requested one.

    The credit is a fraction of `WEIGHTS["occasion"]`, so a full match and a workable-but-
    not-ideal one are separable without either becoming a warning.
    """
    lowered = str(recorded).lower()
    asked = str(wanted).strip().lower()

    # First, always: a store that typed the customer's own word has already answered, and no
    # vocabulary table should get to second-guess it. "للجيم" against "للجيم والرياضة" is
    # decided here and never reaches the axis logic below.
    if _text_hit(lowered, asked):
        return MATCH, 1.0

    recorded_sport = any(term in lowered for term in _SPORT_TERMS)
    recorded_daytime = any(term in lowered for term in _DAYTIME_TERMS)
    recorded_evening = any(term in lowered for term in _EVENING_TERMS)
    asked_sport = asked in _SPORT_TERMS
    asked_daytime = asked in _ASKED_DAYTIME_TERMS
    asked_evening = asked in _EVENING_TERMS

    if asked_sport:
        if recorded_sport:
            return MATCH, 1.0
        # Checked before the evening branch so "Casual/Evening" is read on the half that
        # answers the request rather than the half that contradicts it.
        if recorded_daytime:
            return MATCH, _PARTIAL_OCCASION_CREDIT
        return (CONFLICT, 0.0) if recorded_evening else (UNKNOWN, 0.0)
    if asked_daytime:
        if recorded_daytime:
            return MATCH, 1.0
        return (CONFLICT, 0.0) if recorded_evening else (UNKNOWN, 0.0)
    if asked_evening:
        if recorded_evening:
            return MATCH, 1.0
        return (CONFLICT, 0.0) if recorded_daytime else (UNKNOWN, 0.0)

    # A register or an event ("formal", "wedding") says nothing about time of day, so there
    # is nothing here to contradict.
    return UNKNOWN, 0.0


def _season_verdict(recorded, wanted):
    """Seasons are a closed, unambiguous vocabulary, so a miss really is a conflict.

    Always full credit or none: unlike occasion there is no half-right season, and grading
    this axis would quietly rescale a signal already deliberately sized at 0.8.
    """
    if any(marker in str(recorded).lower() for marker in _ALL_SEASONS):
        return MATCH, 1.0
    return (MATCH, 1.0) if _text_hit(recorded, wanted) else (CONFLICT, 0.0)

def reasons_note(entry, mismatches_only=False):
    """The evidence line attached to a product block in the prompt.

    Reasons only — the numeric score is deliberately never rendered. Handing a model 0.62
    is how "شبهه بنسبة 95%" gets invented.

    `mismatches_only` drops the ✅ half and keeps the ⚠️ half. Needed because the two halves
    have opposite lifetimes: the ✅ reasons are what the model says to justify a
    recommendation, so re-sending them every turn is what made the bot recite "ثابت وفوحانه
    متوسط" four times — but the ⚠️ mismatches are a *safety* signal that never goes stale.
    Suppressing the whole line to stop the recital took the warning with it, and the reply
    promptly offered an Evening/Formal perfume as "أنسب للنهار في المكتب".
    """
    if entry is None:
        return ""
    parts = []
    if entry.reasons and not mismatches_only:
        parts.append("✅ ليه مناسب: " + " | ".join(entry.reasons))
    if entry.mismatches:
        parts.append("⚠️ مش مطابق في: " + " | ".join(entry.mismatches))
    return "\n".join(parts)
