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


def _sanitize(intent):
    """Drop extracted values that are outside a closed vocabulary or contradict each other.

    Only `avoid_traits` is filtered. The other free-text fields are matched against the
    catalogue downstream, where an unknown value simply fails to match; this one is scored
    directly, so a bad value has to be removed before it reaches the ranker.

    Two filters:
      * outside the closed vocabulary — "mainstream" reached the Arabic prompt as the literal
        string "مش mainstream";
      * contradicting a positive request on the same axis — see `_PROJECTION_AXIS`.
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
- If the user asks for the store's own brand, exclusive perfumes, or custom blends (e.g. "البراند بتاعكو", "عطوركم الخاصة", "من عندكم", "تركيبكم", "بتاعكم"), set 'brand' to 'STORE_BRAND_EXCLUSIVE'.
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