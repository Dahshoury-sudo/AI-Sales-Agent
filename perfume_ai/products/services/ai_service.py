from google import genai
from django.conf import settings

client = genai.Client(api_key=settings.GEMINI_API_KEY)


def ask_ai(message, products):

    context = ""

    for p in products:
        context += f"""
Name: {p.name}
Brand: {p.brand}
Price: {p.price}
Gender: {p.gender}
Season: {p.season}
Longevity: {p.longevity}
Projection: {p.projection}

"""

    prompt = f"""
You are a professional perfume consultant.

Only recommend perfumes from this list.

If you don't find a suitable perfume say that politely.

Products:

{context}

Customer:

{message}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    return response.text