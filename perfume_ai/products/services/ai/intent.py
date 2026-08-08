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
    "gender": "must be 'male', 'female', 'unisex' or null",
    "perfume_type": "must be 'oriental', 'western', 'niche', 'ultra_niche' or null",
    "season": "season like 'summer', 'winter' or null",
    "occasion": "like 'evening', 'office', 'party' or null",
    "max_price": float or null,
    "longevity": "like 'long-lasting', 'moderate', 'eternal' or null",
    "projection": "like 'strong', 'moderate', 'intimate' or null",
    "exclude_names": ["perfume1", "perfume2"] or [],
    "notes": ["note1", "note2"] or []
}}

Rules:
- If the user asks for the store's own brand, exclusive perfumes, or custom blends (e.g. "البراند بتاعكو", "عطوركم الخاصة", "من عندكم", "تركيبكم", "بتاعكم"), set 'brand' to 'STORE_BRAND_EXCLUSIVE'.
- If the user mentions a specific budget (e.g. "under 1000"), set max_price.
- If the user mentions a brand name in Arabic (e.g. ديور, شانيل, توم فورد), MUST translate it to its English name (e.g. 'Dior', 'Chanel', 'Tom Ford') and put it in 'brand'.
- If the user mentions a gender in Arabic (e.g. رجالي, حريمي), map it exactly to 'male', 'female', or 'unisex'.
- If the user mentions a perfume type in Arabic (e.g. عطور شرقية, عطور غربية, نيش, الترا نيش, الترانيش, بريميوم), map it exactly to 'oriental', 'western', 'niche', or 'ultra_niche'.
- CRITICAL — Infer gender from context: Even if the user doesn't say "رجالي" or "حريمي" explicitly, you MUST infer the gender from contextual clues:
  • Male context: عريس, لصاحبي, لأخويا, لأبويا, لخطيبي, لجوزي, شاب, ولد, لابني, لعمي, لخالي, هدية لراجل, أنا راجل, أنا ولد
  • Female context: عروسة, عروسه, لصاحبتي, لأختي, لماما, لخطيبتي, لمراتي, بنت, لبنتي, لطنطي, لخالتي, هدية لبنت, أنا بنت, ست
  If any of these clues exist, set gender accordingly ('male' or 'female'). Only leave gender as null if there is absolutely NO clue about gender in the message or conversation history.
- If the user asks for a perfume similar to a specific known perfume (e.g. "عايز حاجة زي كريد" or "بديل سوفاج"), use your general knowledge to extract the main olfactory notes of that famous perfume and put them in the 'notes' array (e.g. ["pineapple", "birch"]). ALSO, add the name of that perfume to 'exclude_names' so we don't recommend the exact same one back. Do NOT put the famous perfume's name in the 'brand' field unless the user explicitly wants to buy from that brand.
- If the user asks for alternatives (e.g. "في حاجة تانية", "عندك ايه تاني", "ايه تاني"), you MUST read the history and extract the names of ALL perfumes the assistant previously recommended, and add them to the 'exclude_names' array. This ensures we don't recommend the exact same perfumes again.
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

    response = chat(messages, response_format={"type": "json_object"})

    try:
        return json.loads(response)
    except Exception:
        return {}