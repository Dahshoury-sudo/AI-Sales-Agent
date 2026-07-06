from .ai.client import chat
from .ai.prompts import get_system_prompt


def handle_general(message, history=None, store=None):
    messages = [
        {
            "role": "system",
            "content": get_system_prompt(store),
        }
    ]
    
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": message
    })

    return chat(messages), ""
