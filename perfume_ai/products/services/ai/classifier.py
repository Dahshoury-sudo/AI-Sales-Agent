from .client import chat


def classify(message: str):
    system_prompt = """
You are a request classifier.

Return ONLY one of these words.

recommendation
product_info
comparison
order
greeting
faq
"""

    response = chat([
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": message
        }
    ])

    return response.strip().lower()