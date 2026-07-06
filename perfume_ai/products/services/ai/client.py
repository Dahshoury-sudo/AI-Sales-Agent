from openai import OpenAI
from django.conf import settings

client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
)


def chat(messages, temperature=0.3, response_format=None):
    kwargs = {
        "model": settings.OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    
    if response_format:
        kwargs["response_format"] = response_format
        
    response = client.chat.completions.create(**kwargs)

    return response.choices[0].message.content


def test():
    return chat([
        {
            "role": "user",
            "content": "Say hello."
        }
    ])