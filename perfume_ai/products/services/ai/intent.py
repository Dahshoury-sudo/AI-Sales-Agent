import json
import re

from .client import chat
from ..bottle_wording import strip_bottle_wording
from products.scent_tags import SCENT_TAG_LABELS
from products.occasions import DAILY, OCCASIONS
from ..sales.scent_preferences import normalize_scent_intent, explicit_scent_updates

# Legacy exclusion fields remain validated when older conversations supply them.
AVOID_TRAITS = frozenset({"heavy", "suffocating", "sweet", "loud", "strong", "old"})

# An explicit positive performance request resolves contradictory legacy exclusions.
_PROJECTION_AXIS = frozenset({"heavy", "loud", "strong"})

_WANTED_STRENGTH = frozenset({"strong", "heavy", "loud", "enormous", "beast", "nuclear"})

# The sentinel for the store's own blends. Emitted positively in `brand` (see the first rule in
# the prompt below) and negatively in `exclude_brands` for a customer who wants real designer
# houses instead. Named here rather than repeated as a literal because `_sanitize` now has to
# compare the two slots against each other.
STORE_BRAND_EXCLUSIVE = "STORE_BRAND_EXCLUSIVE"

# How many houses one customer can plausibly rule out in one conversation. Unlike `avoid_traits`
# there is no closed vocabulary to fall back on — a brand is free text — so a cap is the only
# bound there is, and this field is both a hard SQL filter and persisted across turns
# (conversation_service.PERSISTED_PREFERENCE_KEYS), which makes an extraction runaway expensive
# twice over.
MAX_EXCLUDED_BRANDS = 5


def _explicit_recommendation_updates(message):
    """Ground clear generic daytime/performance requests even if extraction omits them.

    The bounded vocabulary excludes perfume names, negations, quantities and fact
    questions. Other messages continue through normal extraction unchanged.
    """
    from ..static_faq_service import normalize_arabic

    text = re.sub(r"[،,.!?؟]", " ", normalize_arabic(message or ""))
    words = text.split()
    words = [word[1:] if word.startswith("و") and len(word) > 1 else word for word in words]
    tokens = set(words)
    request_words = {"رشحلي", "ترشحلي", "رشح", "ترشح", "عايز", "عاوز", "محتاج"}
    daytime_words = {"نهار", "نهاري", "للنهار", "الصبح", "صباحي", "للصبح", "للصباح"}
    projection_words = {"فواح", "فواحه"}
    longevity_words = {"ثابت", "ثابته"}
    vocabulary = request_words | daytime_words | projection_words | longevity_words | {
        "طيب", "طب", "ايه", "ممكن", "لي", "ليا", "حاجه", "عطر", "برفان", "برفيوم", "ريحه",
        "حلو", "حلوه", "كويس", "كويسه", "رجالي", "حريمي", "للجنسين", "و", "يكون", "تكون",
    }
    if not (tokens & request_words) or not tokens <= vocabulary:
        return {}
    updates = {}
    if tokens & daytime_words:
        updates["occasion"] = DAILY
    if tokens & projection_words:
        updates["projection"] = "strong"
    if tokens & longevity_words:
        updates["longevity"] = "long-lasting"
    return updates


def _sanitize(intent):
    """Validate legacy exclusions and resolve contradictory brand or performance updates."""
    if not isinstance(intent, dict):
        return {}

    traits = intent.get("avoid_traits")
    if isinstance(traits, (list, tuple, set)):
        kept = [
            trait for trait in traits
            if str(trait).strip().lower() in AVOID_TRAITS
        ]

        # A positive projection request outranks an avoid on the same axis. Deliberately not
        # inferred from the message — only from two extracted fields disagreeing, so no
        # polarity is being guessed here.
        if str(intent.get("projection") or "").strip().lower() in _WANTED_STRENGTH:
            kept = [
                trait for trait in kept
                if str(trait).strip().lower() not in _PROJECTION_AXIS
            ]

        intent["avoid_traits"] = kept

    brands = intent.get("exclude_brands")
    if isinstance(brands, (list, tuple, set)):
        # The positive request wins a collision, for the reason `_PROJECTION_AXIS` gives one field
        # up: this function sees the RAW output of a single call about a single message, so `brand`
        # and `exclude_brands` naming the same house is one polarity slip and not two constraints.
        # Staleness is not what this is looking at — a `brand` gap-filled from five turns ago has
        # not reached here yet, and conversation_service resolves that collision the other way
        # round on purpose (see `_withdrawn_by_exclusion`).
        #
        # Positive, specifically, because of the direction the documented failure runs in. The
        # prompt's "A PERFUME IS NOT A HOUSE" rule exists because the model infers a HOUSE from a
        # rejected PERFUME, so a spurious value here is far likelier to be the exclusion than the
        # request — and a spurious exclusion is invisible: nothing tells the customer that six
        # Diors were removed, while a spurious `brand` announces itself by returning nothing and
        # is already withdrawable in one turn (conversation_service._BRAND_RELAX_MARKERS).
        wanted = str(intent.get("brand") or "").strip().lower()

        kept, seen = [], set()
        for entry in brands:
            text = str(entry or "").strip()
            if not text:
                continue
            key = text.lower()
            if key == wanted or key in seen:
                continue
            # Latin only, by construction rather than by preference: `Brand.name` holds "Chanel"
            # and never "شانيل" (sales/naming.py:names_a_bare_brand), so an untranslated entry can
            # never match a brand row. Keeping it would filter nothing while still rendering into
            # the reply as a constraint we are honouring — `describe_filters` would say "من غير
            # شانيل" about a search that excluded nothing. The sentinel is Latin, so it survives.
            if not any("a" <= character <= "z" for character in key):
                continue
            seen.add(key)
            kept.append(text)
            if len(kept) >= MAX_EXCLUDED_BRANDS:
                break

        intent["exclude_brands"] = kept

    return intent


def extract_intent(message: str, history=None, store=None):
    store_name_text = f"The name of the store is '{store.name}'." if store else ""
    system_prompt = f"""
Extract perfume search updates from the customer's LATEST message. {store_name_text}
Use history to understand answers and references. Saved preferences are merged separately:
do not invent preferences or copy withdrawn requirements. Return only a JSON object.

Fields (unspecified updates are null; use clear_preferences for explicit withdrawals):
brand, brand_strict (boolean), exclude_brands (English house names),
gender (male/female/unisex/multiple), perfume_type (oriental/western/niche/ultra_niche/store_own_made),
season, occasion (one of {list(OCCASIONS)}),
occasion_text (exact occasion/delegation words from the latest customer message),
occasion_delegated (boolean), max_price (number), longevity, projection,
scent_tags, excluded_scent_tags (arrays from {list(SCENT_TAG_LABELS)}),
scent_match (all/any), scent_open (boolean),
notes, avoid_notes (literal ingredients requested, in English),
similar_to (one named reference perfume, English), similar_to_notes (always []),
exclude_names (English perfume names), wants_uncommon (boolean),
requested_volume (integer ml), bottle_type (normal/original), purchase_quantity (integer),
recommendation_count (integer), budget_scope (per_item/total), budget_strict (boolean),
reset_preferences (boolean), clear_preferences (array of field names).

Scent rules:
- Interpret Arabic and English scent styles using only the listed labels. فريش = fresh,
  مسكر/سويت = sweet, عودي = oud, خشبي = woody, نضيف/نظافة = clean, تقيل = heavy.
- Several requested labels mean ALL (fresh AND sweet). Explicit alternatives mean ANY
  (fresh OR sweet). Exclusions go in excluded_scent_tags, never positive scent_tags.
- heavy/light describe scent character. They do not establish projection or longevity.
  Only extract projection/ longevity when the customer asks about those performance facts.
- An occasion alone does not imply scent labels: work is not automatically fresh.
- Set scent_open=true only when the customer explicitly accepts any scent style.
  A specific scent request sets scent_open=false. Bare confirmations do not open the preference.
- notes/avoid_notes are only explicit ingredient requirements. Do not expand scent labels
  into guessed ingredients and do not infer tags or notes for a reference perfume.
- For an explicit 'similar to X', 'same vibe as X', or anaphoric 'شبهه', set similar_to.
  A bare mention of liking or owning X is not by itself a request to find similar perfumes.
- Rejecting a perfume or asking for other options may set exclude_names from the actual
  previous offers. Never exclude every name mentioned in a comparison or question.

Occasion rules:
- Return exactly one of the six listed occasion labels, or null if no occasion update.
  Beach, Summer & Gym is ONE category. Date Night & Romantic is separate from nightlife.
- Map other situations to the closest category using the customer's context: university,
  errands, daytime and mosque/prayer -> Daily & Casual; work, meetings and job interviews
  -> Work & Office; friends, parties and clubbing -> Night Out & Hanging Out; romantic
  dinners/dates -> Date Night & Romantic; weddings, engagements, ceremonies -> Formal &
  Special Occasions; beach, summer use, sports and workouts -> Beach, Summer & Gym.
  Interpret unfamiliar situations too; quote the actual latest wording in occasion_text.
  Prefer specific context (a wedding party or romantic evening) over a generic word.
- When the customer leaves the occasion to you ('choose for me', 'انت اختار', 'على ذوقك'),
  return occasion_delegated=true and occasion=null unless they also state an occasion.
  Code keeps their existing explicit occasion, otherwise chooses Daily & Casual.
  Choosing a scent, size, brand, or one of previously offered perfumes does not delegate
  the occasion. Bare confirmations and omitted occasions are not occasion updates.
- Occasion delegation does not itself open the scent preference or remove other requirements.

Other requirements:
- Ordinary brand requests are preferences; brand_strict=true only for explicit exclusivity.
  'ديور بس يكون فريش' means Dior BUT fresh, not Dior only. Translate Arabic houses to English.
- STORE_BRAND_EXCLUSIVE means the store's own creations, positively in brand or negatively
  in exclude_brands. Buying from the store or choosing its bottle does not imply that brand.
- 'زجاجة الستور', 'زجاجة البراند', 'زجاجة المحل' = bottle_type normal.
  'زجاجة أوريجينال' / 'زجاجة بشكل الأوريجينال' = bottle_type original. Packaging is not authentic liquid.
- Gender comes from explicit wording or recipient context (لخطيبتي = female, لجوزي = male),
  not the customer's name. Two genders can use unisex; if they insist on separate choices use multiple.
- Keep an explicit budget as max_price. Exact ceilings ('under', 'at most', 'حد أقصى',
  'مش هقدر أزود') are strict. Approximate budgets allow the existing near-budget policy.
- requested_volume and bottle_type are mandatory when stated. Suggested sizes in assistant
  replies are not customer choices. purchase_quantity is bottles to BUY; recommendation_count
  is alternatives to SEE. A combined budget is total + budget_strict=true; do not divide it.
- Do not translate 'uncommon' into heavy/light/weak/loud. No popularity data is provided.
- Changing the recipient or explicitly discarding all requirements sets reset_preferences=true;
  extract only the NEW preferences. clear_preferences lists individually withdrawn fields,
  including scent_tags/excluded_scent_tags or max_price. 'عادي لو منتشر' withdraws wants_uncommon.
"""

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": message,
    })

    try:
        response = chat(messages, profile="extract", response_format={"type": "json_object"})
        intent = _sanitize(json.loads(response))
        from ..sales.constraints import explicit_updates, original_liquid_update
        intent.update(explicit_updates(message))
        # Packaging ownership is not a perfume-house preference. Keep a separately
        # stated request for exclusive creations, but discard this common inference.
        perfume_request = strip_bottle_wording(message)
        if perfume_request != message and not re.search(
            r"براند|تصميم|حصري|خاص|تركيبكم|بتاعك[وم]|عطوركم|من عندك[وم]",
            perfume_request,
        ):
            if intent.get("brand") == STORE_BRAND_EXCLUSIVE:
                intent["brand"] = None
            intent["exclude_brands"] = [
                brand for brand in (intent.get("exclude_brands") or [])
                if brand != STORE_BRAND_EXCLUSIVE
            ]
        # A follow-up about the total must not silently change the bottle choice.
        # Packaging changes need evidence in this turn; omission retains saved state.
        from ..static_faq_service import normalize_arabic
        text = normalize_arabic(message or "")
        if re.search(r"مش\s+(?:عايز(?:ه)?\s+)?(?:حاجه\s+)?تقيل", text):
            intent["avoid_traits"] = list(dict.fromkeys([*(intent.get("avoid_traits") or []), "heavy"]))
        # A suggested size in an assistant reply is not a customer choice.
        customer_text = " ".join([message or ""] + [
            entry.get("content", "") for entry in (history or []) if entry.get("role") == "user"
        ])
        if not explicit_updates(message).get("requested_volume"):
            intent.pop("requested_volume", None)
        sweet_evidence = normalize_arabic(customer_text)
        if intent.get("notes") and not re.search(r"مسكر|مسكره|سويت|sweet|gourmand|حلاوه", sweet_evidence):
            intent["notes"] = [note for note in intent.get("notes", [])
                               if str(note).lower() not in ("sweet", "gourmand", "حلو", "حلوه")]
        original = original_liquid_update(message)
        if original is None:
            intent.pop("requires_original_liquid", None)
        elif original:
            intent["exclude_brands"] = [b for b in intent.get("exclude_brands", [])
                                        if b != STORE_BRAND_EXCLUSIVE]
        if not any(marker in text for marker in ("زجاج", "ازاز", "اوريجينال", "اوريجنال", "اصلي", "original", "bottle", "تركيب", "براند")):
            intent.pop("bottle_type", None)
        for key in ("requested_volume", "purchase_quantity", "recommendation_count"):
            if intent.get(key) is not None and (type(intent[key]) is not int or intent[key] <= 0):
                intent.pop(key)
        if intent.get("bottle_type") not in (None, "normal", "original"):
            intent.pop("bottle_type")
        for key in ("wants_uncommon", "budget_strict", "brand_strict", "reset_preferences"):
            if intent.get(key) is not None and type(intent[key]) is not bool:
                intent.pop(key)
        from ..sales.brand_preference import sanitize_brand_intent
        intent = sanitize_brand_intent(intent, message, store)
        explicit = _explicit_recommendation_updates(message)
        intent.update(explicit)
        if explicit.get("projection"):
            intent["avoid_traits"] = [trait for trait in (intent.get("avoid_traits") or [])
                                      if str(trait).lower() not in _PROJECTION_AXIS]
        from ..sales.request_updates import prepare_updates
        intent = normalize_scent_intent(prepare_updates(intent, message, history, store)[0])
        if not intent.get("similar_to"):
            intent.update(explicit_scent_updates(message))
        return intent
    except Exception:
        from ..sales.request_updates import prepare_updates
        fallback = normalize_scent_intent(prepare_updates(_explicit_recommendation_updates(message), message, history, store)[0])
        fallback.update(explicit_scent_updates(message))
        from ..sales.occasion_preferences import PENDING_KEY, occasion_question
        if not fallback.get("occasion") and not fallback.get("occasion_delegated") and (
            occasion_question(history) or re.search(r"\b(?:perfume|fragrance|something)\s+for\b", message or "", re.I)
        ):
            fallback[PENDING_KEY] = True
        return fallback
