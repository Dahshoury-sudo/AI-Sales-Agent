from openai import OpenAI
from django.conf import settings

client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
)


def chat(messages, temperature=0.3):
    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        temperature=temperature,
    )

    return response.choices[0].message.content


def test():
    return chat([
        {
            "role": "user",
            "content": "Say hello."
        }
    ])