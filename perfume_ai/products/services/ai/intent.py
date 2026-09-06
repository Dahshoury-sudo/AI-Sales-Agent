import json

from .client import chat

# The closed vocabulary the prompt asks for. Enforced here as well as asked for there,
# because the prompt is advice and this is arithmetic: `avoid_traits` is the only extracted
# field scored as a PENALTY (ranking.WEIGHTS["avoid"] is -3.0), so a value the customer never
# said does not merely add noise — it actively pushes away perfumes that suit them.
#
# Evaluation scenario M1 is the case: "مش عايز حاجه منتشره" (not widely owned) came back as
# avoid_traits ["loud", "mainstream"] and persisted for the whole conversation. "loud" is in
# vocabulary and is read as heaviness (ranking.py:213), so seven of eleven candidates were
# penalised -3.0 — including the longest-lasting ones, one turn before the customer said
# longevity was their top priority. "mainstream" is not in vocabulary at all and reached the
# Arabic prompt as the literal string "مش mainstream".
AVOID_TRAITS = frozenset({"heavy", "suffocating", "sweet", "loud", "strong", "old"})

# Traits that describe the same axis as a positive `projection` request. You cannot want a
# strong projection and simultaneously want to avoid strength — the extractor emitting both is
# a polarity slip, not two constraints, and `_WANTED_PROJECTION` in ranking maps
# "strong"/"heavy"/"loud" as *requests* while `avoid_heavy` reads the identical strings as
# *exclusions*. When they collide the positive request wins, because it is the one the customer
# has to have said out loud: nothing sets `projection` by accident.
#
# This is the deterministic half of the polarity fix. The prompt asks for it too, but a prompt
# is advice and `avoid_traits` is the only extracted field scored as a penalty — "عايزه حاجه
# تقيله للشتا" came back as avoid_traits ["heavy"], which put -3.0 on every heavy perfume and
# had the reply describe a winter oriental as "خفيف ومش خانق" to a customer who asked for heavy.
_PROJECTION_AXIS = frozenset({"heavy", "loud", "strong"})

# The `projection` values that mean "I want strength". Mirrors the upper half of
# ranking._WANTED_PROJECTION; a request for "moderate" or "intimate" contradicts nothing.
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


def _sanitize(intent):
    """Drop extracted values that are outside a closed vocabulary or contradict each other.

    Two fields are filtered, and for the same underlying reason: both are read downstream as
    something stronger than a preference, so a value the customer never said does not merely add
    noise. `avoid_traits` is the only extracted field scored as a PENALTY, and `exclude_brands` is
    a hard SQL filter — the harsher of the two, since a penalty can be outweighed and a deleted
    row cannot come back. Every other free-text field is matched against the catalogue downstream,
    where an unknown value simply fails to match.

    `avoid_traits`, two filters:
      * outside the closed vocabulary — "mainstream" reached the Arabic prompt as the literal
        string "مش mainstream";
      * contradicting a positive request on the same axis — see `_PROJECTION_AXIS`.

    `exclude_brands`: capped, deduped, Latin-only, and reconciled against `brand`.
    """
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
You are an expert perfume intent extractor.
{store_name_text}

Analyze the user's latest message and conversation history to extract their search criteria.
Return ONLY valid JSON.

Schema:
{{
    "brand": "brand name or null",
    "exclude_brands": ["brand1", "brand2"] or [] — HOUSES they said they do NOT want,
    "gender": "must be 'male', 'female', 'unisex', 'multiple', or null",
    "perfume_type": "must be 'oriental', 'western', 'niche', 'ultra_niche' or null",
    "season": "season like 'summer', 'winter' or null",
    "occasion": "like 'evening', 'office', 'party' or null",
    "max_price": float or null,
    "longevity": "like 'long-lasting', 'moderate', 'eternal' or null",
    "projection": "like 'strong', 'moderate', 'intimate' or null",
    "exclude_names": ["perfume1", "perfume2"] or [],
    "notes": ["note1", "note2"] or [],
    "similar_to": "name of the ONE perfume they want something similar to, or null",
    "similar_to_notes": ["note1", "note2"] or [],
    "avoid_notes": ["note1", "note2"] or [],
    "avoid_traits": subset of ["heavy", "suffocating", "sweet", "loud", "strong", "old"] or [] — CLOSED list, nothing else,
    "wants_uncommon": true or false
}}

Rules:
- If the user asks for the store's own brand, exclusive perfumes, or custom blends (e.g. "البراند بتاعكو", "عطوركم الخاصة", "من عندكم", "تركيبكم", "بتاعكم"), set 'brand' to 'STORE_BRAND_EXCLUSIVE'. 🔴 And the mirror: if they REJECT the store's own blends and ask for real designer houses instead (e.g. "مش عايز تركيبات بتاعتكم", "عايز براندات أصلية", "بلاش تركيبكم", "عايز الأصلي مش تركيب", "مش عايز حاجة من تصميمكم"), put that SAME sentinel 'STORE_BRAND_EXCLUSIVE' in 'exclude_brands' and leave 'brand' null. ❌ Never in both.
- If the user mentions a specific budget (e.g. "under 1000"), set max_price.
- If the user mentions a brand name in Arabic (e.g. ديور, شانيل, توم فورد), MUST translate it to its English name (e.g. 'Dior', 'Chanel', 'Tom Ford') and put it in 'brand'.
- If the user mentions a gender in Arabic (e.g. رجالي, حريمي), or uses terms like "bi" or "bisexual", map it exactly to 'male', 'female', or 'unisex' (map "bi" and "bisexual" to 'unisex').
- If the user mentions a perfume type in Arabic (e.g. عطور شرقية, عطور غربية, نيش, الترا نيش, الترانيش, بريميوم), map it exactly to 'oriental', 'western', 'niche', or 'ultra_niche'.
- CRITICAL — Infer gender from context: Even if the user doesn't say "رجالي" or "حريمي" explicitly, you MUST infer the gender from contextual clues:
  • Male context: عريس, لصاحبي, لأخويا, لأبويا, لخطيبي, لجوزي, شاب, ولد, لابني, لعمي, لخالي, هدية لراجل, أنا راجل, أنا ولد
  • Female context: عروسة, عروسه, لصاحبتي, لأختي, لماما, لخطيبتي, لمراتي, بنت, لبنتي, لطنطي, لخالتي, هدية لبنت, أنا بنت, ست
  If any of these clues exist, set gender accordingly ('male' or 'female'). Only leave gender as null if there is absolutely NO clue about gender in the message or conversation history.
- CRITICAL — Multiple Genders: If the user explicitly asks for BOTH male and female perfumes in the same message (e.g. "واحد ليا وواحد لمراتي", "رجالي وحريمي"), you MUST set 'gender' to 'unisex' to safely retrieve perfumes suitable for both. HOWEVER, if the user explicitly INSISTS on having separate distinct perfumes and REJECTS unisex (e.g. "مش عايز للجنسين عايز رجالي لوحده وحريمي لوحده"), you MUST set 'gender' to 'multiple'.
- CRITICAL — SIMILARITY: If the user EXPLICITLY asks for a perfume SIMILAR to a specific known perfume (e.g. "عايز حاجة زي كريد", "بديل سوفاج", "شبه كذا"):
  1. Put that perfume's name in 'similar_to' (English, correctly spelled). This is the most important field in that case — the ranking is driven by it.
  2. Use your general knowledge to extract that perfume's main olfactory notes into 'similar_to_notes' (e.g. ["bergamot", "pepper", "ambroxan"]). Put them in 'similar_to_notes', NOT in 'notes' — 'notes' is for ingredients the user asked for directly.
  3. Add that perfume's name to 'exclude_names' so we don't recommend the exact same one back.
- CRITICAL: DO NOT put a perfume in 'exclude_names' if the user just names it (e.g. "سوفاج", "عايز سوفاج"). Only exclude it if they explicitly ask for an ALTERNATIVE ("بديل", "زي", "شبه").
- If the user asks for alternatives or to see more options (e.g. "في حاجة تانية", "عندك ايه تاني", "ايه تاني", "ايه اللي عندك", "ايه المتاح", "وريني ايه عندك", "عندك ايه", "غيره", "حاجة غير كده"), you MUST read the history and extract the names of ALL perfumes the assistant previously recommended, and add them to the 'exclude_names' array. This ensures we don't recommend the exact same perfumes again — a customer who asks what else you have and is shown the same two perfumes reads it as not being listened to.
- CRITICAL — "SAME VIBE" IS A SIMILARITY REQUEST: if the user asks for something in the same character as a perfume already discussed (e.g. "عايز حاجة تانية في نفس الجو", "نفس الستايل", "زي اللي جبته", "قريب من اللي اشتريته", "نفس النوع بس مختلف"), you MUST set 'similar_to' to that perfume's name from the history AND add it to 'exclude_names'. Returning only 'exclude_names' loses the whole point of the request — there is then nothing to match against, and the customer who told us exactly what they like gets asked what they like.
- 🔴 CRITICAL — A PERFUME NAMED IN THE LATEST MESSAGE IS NEVER DROPPED. If the user names a specific perfume, that name MUST appear somewhere in your output. The rules above cover "زي X" and "نفس الجو", but a name can arrive with neither phrasing, and those cases were being returned with the perfume mentioned nowhere at all:
  • They say they like it or already own it ("بحب سوفاج", "اشتريت امبيرو وعجبني", "عندي بلو دي شانيل") → 'similar_to' (+ 'similar_to_notes'). Liking a perfume is the strongest taste signal there is.
  • They ask why it was not offered ("ليه مرشحتش versace eros", "ومال سوفاج", "مرشحتليش X ليه") → 'similar_to' as well. They are telling you what they were hoping for.
  • They reject it ("مش عايز سوفاج", "بلاش امبيرو") → 'exclude_names'.
  ❌ Returning gender/budget/season and no reference to the perfume they just named is the failure this rule exists to stop: the name then never becomes a search key, and whether that perfume reaches the customer is luck. A customer asked "ليه مرشحتش versace eros" and was told it was unavailable while it sat in the catalogue at 1019 جنيه.
  ❌ NAMING A PERFUME IS NOT NAMING A BRAND. "بحب سوفاج" and "ليه مرشحتش versace eros" set 'similar_to' ONLY — do NOT also set 'brand' to that perfume's house. 'brand' is exclusively for an explicit request for a house ("عندك حاجة من ديور", "براند شانيل"). Inferring brand='Dior' from "سوفاج" collapsed a twelve-perfume shortlist down to two Dior products, and the customer had just said "مش عايز حاجه منتشره" — so the one constraint they cared about was answered with the two most mainstream perfumes in the store.
- CRITICAL — EXCLUSIONS: If the user says what they do NOT want, capture it:
  🔴🔴 POLARITY FIRST. 'avoid_traits' is ONLY for what they said they do NOT want. Before you put anything in it, check whether the sentence was negated. The SAME word means opposite things:
    • "عايز عطر تقيل" / "عايزه حاجه تقيله للشتا" / "بحب العطور التقيلة" → they WANT heaviness. That is `projection: "strong"` (and `perfume_type: "oriental"` if they said شرقي). ❌ avoid_traits stays EMPTY.
    • "مش عايز حاجة تقيلة" / "من غير تقل" / "حاجة خفيفة" → they do NOT want it. NOW `avoid_traits: ["heavy"]`.
    Same for فواح / قوي: wanted → 'projection', rejected → 'avoid_traits'. Getting this backwards is the single most damaging error you can make: avoid_traits is scored as a PENALTY, so inverting it pushes away the exact perfumes they asked for and the reply then tells them their heavy winter perfume is "خفيف ومش خانق". A customer who said "عايزه حاجه تقيله للشتا" was handed avoid_traits ["heavy"].
    ❌ NEVER return the same axis as both a want and an avoid — `projection: "strong"` together with `avoid_traits: ["strong"]` (or `["heavy"]`, or `["loud"]`) is a contradiction, and it will be discarded.
  • A specific ingredient they don't want (e.g. "مش بحب العود", "من غير مسك") → 'avoid_notes' (English).
  • A characteristic they don't want → 'avoid_traits'. This is a CLOSED list of exactly six values and you may return NOTHING else: "heavy" (تقيل), "suffocating" (يخنق/بيخنق اللي حواليا), "sweet" (مسكر), "loud" (فواح أوي), "strong" (قوي أوي), "old" (كلاسيكي/ريحة قديمة). A value outside this list is discarded, so inventing one silently loses the customer's constraint.
  • 🔴 A HOUSE they don't want → 'exclude_brands' (English, same translation rule as 'brand': "مش عايز حاجة من ديور" → ["Dior"], "بلاش شانيل" → ["Chanel"], "أي حاجة غير توم فورد" → ["Tom Ford"]). One entry per house actually named.
  ❌ A PERFUME IS NOT A HOUSE. This is the rule "NAMING A PERFUME IS NOT NAMING A BRAND" above, read backwards, and it is the same error with a bigger blast radius. "مش عايز سوفاج" / "بلاش امبيرو" reject ONE perfume → 'exclude_names', and 'exclude_brands' stays EMPTY. Inferring exclude_brands ["Dior"] from "مش عايز سوفاج" deletes every Dior in the shop over one bottle the customer didn't like — and unlike a note or a trait this is a HARD database filter, so nothing later in the pipeline can put those perfumes back or even tell that they went. If they name a perfume, exclude the perfume.
  ❌ SWITCHING HOUSES IS NOT AN EXCLUSION. "لا مش ديور، عايز شانيل" sets brand='Chanel' and NOTHING in 'exclude_brands' — the STATE MANAGEMENT rule below already drops the old brand. Recording the abandoned house as an exclusion turns a changed mind into a permanent ban, because this field is remembered for the rest of the conversation.
  ❌ NEVER the same value in 'brand' and in 'exclude_brands' — "عايز ديور" and "مش عايز ديور" cannot both be true of one message. A house asked FOR goes in 'brand' and nowhere else; the contradiction will be discarded.
  • Example: "مش عايز حاجة تقيلة أو تخنق اللي حواليا" → avoid_traits: ["heavy", "suffocating"].
  ❌ Never put an avoided thing in 'notes' — that would search FOR the thing they rejected.
  ❌ POPULARITY IS NOT INTENSITY. "مش منتشر" / "مش مشهور" / "مش موجود عند حد" describe how MANY people own a perfume, not how strong it smells. They set 'wants_uncommon' ONLY. Putting them in 'avoid_traits' as "loud"/"strong"/"heavy" — or inventing "mainstream" — is a serious error: 'avoid_traits' is a heavy PENALTY, so it would push away the powerful, long-lasting perfumes the customer never objected to. A customer who said "مش منتشرة" has said nothing whatsoever about strength.
- If the user wants something not mainstream (e.g. "مش منتشرة", "مش موجودة عند حد", "حاجة مختلفة", "مش مشهورة", "حاجة نادرة"), set 'wants_uncommon' to true — and leave 'avoid_traits' untouched.
- If the user asks for high longevity (e.g. "ثبات عالي", "ثباته يومين"), set 'longevity' to 'long-lasting' or 'eternal'.
- If the user asks for strong projection (e.g. "فواح جدا", "بيسيب أثر"), set 'projection' to 'strong' or 'enormous'.
- If the user mentions specific ingredients (like vanilla, oud, فانيليا), translate to English and put them in 'notes'.
- CRITICAL: In Egyptian dialect, "حلو" means "nice/good". DO NOT translate "حلو" to the "sweet" note unless the user explicitly asks for a sweet perfume (e.g. "عطر مسكر", "عطر سويتي", "حاجة مسكرة", "gourmand"). If they do ask for a sweet perfume, just add the word "sweet" to the 'notes' array.
- 🔴🔴 CRITICAL — ZERO HALLUCINATION: You are an EXTRACTOR, not a recommender. You MUST only return what the user EXPLICITLY said or CLEARLY implied. If the user only said "رجالي" (male), return ONLY gender="male" and leave EVERYTHING else null/empty. DO NOT infer, guess, or fill in notes, perfume_type, season, occasion, longevity, projection, or avoid_traits unless the user EXPLICITLY mentioned them. 'avoid_traits' is the most damaging field to guess, because it is scored as a penalty rather than a preference — a trait the customer never rejected actively pushes away perfumes that suit them. Returning a field the user never asked about is the worst possible error — it causes the bot to tell the customer "فهمتك عايز سويت" when they never said "سويت", which makes the bot look broken. When in doubt, leave the field null/empty.
- STATE MANAGEMENT: Accumulate preferences from the history (e.g., if they asked for 'female' before, and now say 'Dior', return both). BUT if the user's latest message changes or overrides a previous preference (e.g., they wanted 'Xerjoff' before but now want 'Dior'), OVERRIDE the old preference and ONLY return the NEW one ('Dior'). Do NOT include outdated criteria from the history.
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

    response = chat(messages, profile="extract", response_format={"type": "json_object"})

    try:
        return _sanitize(json.loads(response))
    except Exception:
        return {}