import json

from .client import chat


def extract_intent(message: str):
    system_prompt = """
You are a perfume shopping assistant.

Extract the customer's search intent.

Return ONLY valid JSON.

Schema:

{
    "brand": null,
    "gender": null,
    "season": null,
    "occasion": null,
    "max_price": null,
    "similar_to": null,
    "longevity": null,
    "projection": null,
    "notes": []
}
"""

    response = chat([
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": message,
        },
    ])

    return json.loads(response)