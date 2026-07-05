from google import genai
from google.genai import types
from django.conf import settings

from products.schemas import IntentSchema


client = genai.Client(api_key=settings.GEMINI_API_KEY)


def extract_intent(message):

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=f"""
Extract the perfume search intent.

Customer:
{message}
""",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=IntentSchema,
        ),
    )

    return response.parsed