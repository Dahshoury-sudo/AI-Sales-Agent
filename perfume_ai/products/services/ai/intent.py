import json

from .client import chat


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
    "avoid_traits": ["heavy", "suffocating", "sweet", "loud"] or [],
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
- If the user asks for alternatives (e.g. "في حاجة تانية", "عندك ايه تاني", "ايه تاني"), you MUST read the history and extract the names of ALL perfumes the assistant previously recommended, and add them to the 'exclude_names' array. This ensures we don't recommend the exact same perfumes again.
- CRITICAL — EXCLUSIONS: If the user says what they do NOT want, capture it:
  • A specific ingredient they don't want (e.g. "مش بحب العود", "من غير مسك") → 'avoid_notes' (English).
  • A characteristic they don't want → 'avoid_traits', using ONLY these values: "heavy" (تقيل), "suffocating" (يخنق/بيخنق اللي حواليا), "sweet" (مسكر), "loud" (فواح أوي), "strong" (قوي أوي), "old" (كلاسيكي/ريحة قديمة).
  • Example: "مش عايز حاجة تقيلة أو تخنق اللي حواليا" → avoid_traits: ["heavy", "suffocating"].
  ❌ Never put an avoided thing in 'notes' — that would search FOR the thing they rejected.
- If the user wants something not mainstream (e.g. "مش منتشرة", "مش موجودة عند حد", "حاجة مختلفة", "مش مشهورة", "حاجة نادرة"), set 'wants_uncommon' to true.
- If the user asks for high longevity (e.g. "ثبات عالي", "ثباته يومين"), set 'longevity' to 'long-lasting' or 'eternal'.
- If the user asks for strong projection (e.g. "فواح جدا", "بيسيب أثر"), set 'projection' to 'strong' or 'enormous'.
- If the user mentions specific ingredients (like vanilla, oud, فانيليا), translate to English and put them in 'notes'.
- CRITICAL: In Egyptian dialect, "حلو" means "nice/good". DO NOT translate "حلو" to the "sweet" note unless the user explicitly asks for a sweet perfume (e.g. "عطر مسكر", "عطر سويتي", "حاجة مسكرة", "gourmand"). If they do ask for a sweet perfume, just add the word "sweet" to the 'notes' array.
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
        return json.loads(response)
    except Exception:
        return {}