import json

from .client import chat


def extract_intent(message: str, history=None):
    system_prompt = """
You are an expert perfume intent extractor.

Analyze the user's latest message and conversation history to extract their search criteria.
Return ONLY valid JSON.

Schema:
{
    "brand": "brand name or null",
    "gender": "must be 'male', 'female', 'unisex' or null",
    "season": "season like 'summer', 'winter' or null",
    "occasion": "like 'evening', 'office', 'party' or null",
    "max_price": float or null,
    "notes": ["note1", "note2"] or []
}

Rules:
- If the user mentions a specific budget (e.g. "under 1000"), set max_price.
- If the user mentions a gender in Arabic (e.g. رجالي, حريمي), map it exactly to 'male', 'female', or 'unisex'.
- If the user mentions specific ingredients (like vanilla, oud, فانيليا), translate to English and put them in 'notes'.
- CRITICAL: In Egyptian/Arabic dialect, "حلو" usually means "nice/good" (e.g. "عندكو اي حلو" = "what nice things do you have"). DO NOT translate "حلو" to the "sweet" note. Only add "sweet" to notes if the user explicitly says "عطر مسكر" or "عطر سويتي".
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

    return json.loads(response)