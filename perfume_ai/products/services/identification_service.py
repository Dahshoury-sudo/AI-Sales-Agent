"""Name a perfume from a customer's description, with confidence proportional to evidence.

"مش فاكر اسم البرفان، الزجاجة سودا والريحة فيها فانيليا وحاجة حلوة وثابتة" was answered
"العطر اللي بتوصفه هو Black Opium" — a guess from four vague clues, presented as fact.

The correction is not a softer adjective in the prompt. It is that the hedge is *computed*:
how many clue types we could actually verify against stored data, and how far the best
candidate is ahead of the runner-up, decide which wording the model is required to use. A
low-confidence match cannot be phrased as certainty because the phrasing is handed to it.

Two clue types deserve special mention. Bottle colour and bottle shape are what customers
lead with, and the catalogue has no field for either — so they can never count as
evidence. They lower confidence and they give the single clarifying question something
useful to ask, which is the honest use of an unverifiable clue.
"""

import json
from dataclasses import dataclass, field

from django.db.models import Q

from .ai.client import chat
from .ai.prompts import get_system_prompt
from .product_formatting import format_products
from .sales.notes import NOTE_FAMILIES, parse_notes
from products.models import Product

_CLUE_PROMPT = """
The customer is trying to remember the name of a perfume. Extract only the clues they
actually gave. Return ONLY valid JSON.

{
  "notes": ["english note names they described, e.g. vanilla, rose, coffee"],
  "sweet": true or false,
  "gender": "male" | "female" | "unisex" | null,
  "brand_hint": "brand name in English, or null",
  "longevity_hint": "long" | "short" | null,
  "name_fragment": "any part of the name they recall, or null",
  "bottle_color": "colour they described, or null",
  "bottle_shape": "shape they described, or null",
  "likely_known_perfume": "the name of the famous perfume this description most matches, or null"
}

Rules:
- Translate Arabic descriptions to English note names ("فانيليا" -> "vanilla", "قهوة" -> "coffee").
- In Egyptian dialect "حلوة" usually means "nice", NOT "sweet". Only set "sweet" to true if
  they clearly mean sugary/gourmand ("مسكرة", "حلوة زي الحلويات", "سويت").
- "likely_known_perfume" is your own best guess from general perfume knowledge. Set it only
  if the description genuinely points at one well-known perfume. Otherwise null.
- Do NOT invent clues the customer did not give.
"""

# Clue types we can check against a stored field. Everything else is unverifiable, and
# unverifiable clues must not raise confidence.
VERIFIABLE_CLUES = ("notes", "sweet", "gender", "brand_hint", "longevity_hint", "name_fragment")
UNVERIFIABLE_CLUES = ("bottle_color", "bottle_shape")

# Clues that actually *identify* rather than describe. Vanilla, sweet, female and
# long-lasting are shared by thousands of perfumes, so no number of them adds up to
# certainty — and a large margin over the runner-up can simply mean the catalogue is thin,
# not that the evidence is strong. High confidence therefore requires something nominal.
# This is what keeps "الزجاجة سودا وفيها فانيليا" at "غالبًا" instead of "ده".
NOMINAL_CLUES = frozenset({"name_fragment", "brand_hint"})

# Confidence thresholds. `matched` counts distinct clue *types* that hit, not individual
# notes: three notes from one description is one kind of evidence, not three.
HIGH_CLUE_TYPES = 3
HIGH_MARGIN = 0.25
MEDIUM_MARGIN = 0.10

_GOURMAND = frozenset(
    note for note, accords in NOTE_FAMILIES.items() if "gourmand" in accords
)


@dataclass
class Candidate:
    product: object
    score: float = 0.0
    matched_types: set = field(default_factory=set)


def extract_clues(message, history=None, store=None):
    messages = [{"role": "system", "content": _CLUE_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})
    try:
        response = chat(
            messages, profile="extract", response_format={"type": "json_object"}
        )
        return json.loads(response)
    except Exception:
        return {}


def _candidate_pool(clues, store):
    """Products worth scoring: anything sharing a note, brand or name fragment."""
    queryset = Product.objects.filter(is_active=True).prefetch_related("variants")
    if store:
        queryset = queryset.filter(store=store)

    query = Q()
    terms = list(clues.get("notes") or ())
    if clues.get("sweet"):
        terms.extend(sorted(_GOURMAND)[:12])
    for term in terms:
        query |= (
            Q(top_notes__icontains=term)
            | Q(middle_notes__icontains=term)
            | Q(base_notes__icontains=term)
        )
    if clues.get("brand_hint"):
        query |= Q(brand__name__icontains=clues["brand_hint"])
    if clues.get("name_fragment"):
        query |= Q(name__icontains=clues["name_fragment"])

    if not query:
        return []
    return list(queryset.filter(query).distinct()[:40])


def score_candidates(clues, products):
    """Rank products by how many *verifiable* clue types each satisfies.

    Two things stop this from being a coin flip. Note matches are weighted by how rare
    the note is in the candidate pool, because "vanilla" is shared by a third of the
    catalogue and identifies nothing on its own; and "sweet" scores by how gourmand the
    product actually is rather than a flat point for containing one sweet note.

    Without those, "الزجاجة سودا والريحة فيها فانيليا وحاجة حلوة وثابتة" produced eight
    products tied at exactly 3.50, and the sort fell through to `product.id` — handing
    the answer to Dior Sauvage, a fresh masculine, ahead of Black Opium, Good Girl and
    Vanilo, which are literally black-bottled sweet gourmands.
    """
    wanted_notes = [str(note).strip().lower() for note in (clues.get("notes") or ()) if note]
    candidates = []

    profiles = {}
    for product in products:
        profile = set()
        for layer in ("top_notes", "middle_notes", "base_notes"):
            profile.update(parse_notes(getattr(product, layer, "")))
        profiles[product.id] = profile

    # How common each requested note is among the candidates. A note in nearly every
    # candidate cannot discriminate between them.
    pool_size = max(1, len(products))
    rarity = {}
    for note in wanted_notes:
        frequency = sum(
            1 for profile in profiles.values() if any(note in existing for existing in profile)
        )
        # Never zero: a note every candidate shares still counts a little.
        rarity[note] = max(0.15, 1.0 - (frequency / pool_size))

    for product in products:
        entry = Candidate(product=product)
        profile = profiles[product.id]

        hits = [note for note in wanted_notes if any(note in existing for existing in profile)]
        if hits and wanted_notes:
            weighted = sum(rarity[note] for note in hits) / len(wanted_notes)
            entry.score += 2.0 * weighted
            entry.matched_types.add("notes")

        if clues.get("sweet"):
            gourmand = len(profile & _GOURMAND)
            if gourmand:
                # Proportional to how gourmand it really is, capped so a dessert of a
                # perfume cannot outscore every other kind of evidence on its own.
                entry.score += min(1.0, gourmand / 3.0)
                entry.matched_types.add("sweet")

        if clues.get("gender") and product.gender == str(clues["gender"]).lower():
            entry.score += 0.8
            entry.matched_types.add("gender")

        if clues.get("brand_hint") and str(clues["brand_hint"]).lower() in product.brand.name.lower():
            entry.score += 1.5
            entry.matched_types.add("brand_hint")

        if clues.get("name_fragment") and str(clues["name_fragment"]).lower() in product.name.lower():
            entry.score += 2.0
            entry.matched_types.add("name_fragment")

        hint = clues.get("longevity_hint")
        if hint and (product.longevity or "").strip():
            recorded = product.longevity.lower()
            long_lasting = any(
                word in recorded for word in ("long", "ثابت", "eternal", "10", "12", "8")
            )
            if (hint == "long") == long_lasting:
                entry.score += 0.5
                entry.matched_types.add("longevity_hint")

        if entry.score > 0:
            candidates.append(entry)

    candidates.sort(key=lambda entry: (-entry.score, entry.product.id))
    return candidates


def confidence_tier(candidates):
    """high / medium / low / none, from verifiable evidence and the margin.

    The margin matters as much as the score: two perfumes that both contain vanilla and
    are both long-lasting are not identified by those facts, however well each one scores.

    And no amount of purely descriptive agreement reaches "high" — see NOMINAL_CLUES.
    """
    if not candidates:
        return "none"

    best = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else 0.0
    margin = (best.score - runner_up) / best.score if best.score else 0.0
    clue_types = len(best.matched_types)
    has_nominal = bool(best.matched_types & NOMINAL_CLUES)

    if has_nominal and clue_types >= HIGH_CLUE_TYPES and margin >= HIGH_MARGIN:
        return "high"
    # Both conditions, not either. As an OR, three descriptive clue types returned
    # "medium" — and therefore "غالبًا X" — even at a margin of zero, which is precisely
    # the case where the evidence does not distinguish the winner from the field.
    if clue_types >= 2 and margin >= MEDIUM_MARGIN:
        return "medium"
    return "low"


# The wording each tier is allowed. Chosen in code so a weak guess cannot be rendered as
# a fact — which is the entire defect this module addresses.
TIER_WORDING = {
    "high": 'ابدأ بـ "ده {name}" — الأدلة كافية.',
    "medium": 'ابدأ بـ "غالبًا {name}" — ❌ ممنوع تقوله إنه متأكد.',
    "low": 'ابدأ بـ "الوصف ده قريب من {name}، بس مش متأكد" — ❌ ممنوع تأكيد الاسم.',
}


def _unverifiable_note(clues):
    given = [clue for clue in UNVERIFIABLE_CLUES if clues.get(clue)]
    if not given:
        return ""
    labels = {"bottle_color": "لون الزجاجة", "bottle_shape": "شكل الزجاجة"}
    described = "، ".join(labels[clue] for clue in given)
    return (
        f"⚠️ العميل وصف {described}، وإحنا مش بنسجل ده في بياناتنا — "
        f"فمينفعش تعتمد عليه كدليل ولا تقول إنه بيطابق. "
        f"لكن ينفع تسأله عنه أو عن الريحة عشان تضيّق."
    )


def identify_perfume(message, history=None, store=None):
    """Answer "what was that perfume called?" at a confidence the evidence supports."""
    clues = extract_clues(message, history, store)
    candidates = score_candidates(clues, _candidate_pool(clues, store))
    tier = confidence_tier(candidates)
    unverifiable = _unverifiable_note(clues)

    if candidates:
        shortlist = [entry.product for entry in candidates[:2]]
        context = format_products(shortlist)
        wording = TIER_WORDING[tier].format(name=shortlist[0].name)
        instructions = f"""
═══ تعليمات التعرف على العطر ═══
1. 🔴 {wording}
2. اذكر الحاجة الواحدة أو الاتنين اللي فعلاً بتطابق وصفه من بيانات العطر (النوتات مثلاً)، عشان يعرف إنك بتقارن بحقيقة.
3. 🔴 اسأل سؤال واحد بس يساعدك تتأكد (زي: الريحة كانت فيها قهوة ولا ورد؟ / فاكر جزء من الاسم؟). ❌ ممنوع تسأل أكتر من سؤال.
4. ❌ ممنوع تقول إن الوصف "مطابق تماماً" أو تأكد الاسم لو مش مأكد.
5. متحاولش تبيع في الرد ده — العميل بيحاول يتعرف على عطر، مش بيشتري. ❌ ممنوع تسأله يطلب.
{unverifiable}
"""
    elif clues.get("likely_known_perfume"):
        # Nothing in the catalogue fits, but the clues point somewhere. Naming it is
        # allowed here and only here, and only alongside "we don't stock it" — the persona
        # otherwise treats a perfume absent from the data as non-existent. Saying the name
        # and then offering the closest thing we do sell is more useful than pretending
        # not to know, provided we never imply availability.
        from .search_service import SELLABLE

        alternatives = Product.objects.filter(
            store=store, is_active=True
        ).filter(SELLABLE).distinct()[:3]
        context = format_products(alternatives, brief=True) if alternatives else ""
        instructions = f"""
═══ تعليمات التعرف على العطر ═══
1. 🔴 الوصف بيشاور على "{clues['likely_known_perfume']}" بس العطر ده مش موجود عندنا.
   قوله كده بالظبط بالترتيب: "غالبًا {clues['likely_known_perfume']}" + إنك مش متأكد + إنه مش متوفر عندنا.
2. ❌ ممنوع توحي إننا بنبيعه أو إنه متوفر.
3. بعد كده اعرض عليه إنك ترشحله حاجة قريبة من الوصف من اللي عندنا (من القائمة تحت بس لو فيه قائمة).
4. 🔴 سؤال واحد بس لو محتاج توضيح.
{unverifiable}
"""
    else:
        context = ""
        instructions = f"""
═══ تعليمات التعرف على العطر ═══
1. 🔴 الوصف مش كافي تحدد عطر معين. ❌ ممنوع تخترع اسم عطر أو تخمّن.
2. قوله بصراحة إنك محتاج تفاصيل أكتر عشان تعرف تحدده.
3. 🔴 اسأل سؤال واحد بس عالي القيمة (زي: الريحة كانت فيها إيه بالظبط؟ ولا فاكر جزء من الاسم؟).
{unverifiable}
"""

    messages = [{"role": "system", "content": get_system_prompt(store)}]
    if history:
        messages.extend(history)
    messages.append({
        "role": "user",
        "content": f"""
═══ العميل بيحاول يتعرف على عطر ═══
{message}

{("═══ أقرب العطور من بياناتنا ═══" + chr(10) + context) if context else ""}
{instructions}
""",
    })

    return chat(messages, profile="converse"), context
