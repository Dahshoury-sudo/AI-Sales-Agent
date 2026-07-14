from .ai.client import chat
from .ai.prompts import get_system_prompt


def handle_general(message, history=None, store=None):
    """
    Handle general messages (greetings, FAQ, redirected handoffs, out-of-domain, etc.)
    Now includes anti-repetition context in the system prompt.
    """
    # Build anti-repetition context from recent bot messages
    anti_rep_context = ""
    if history:
        recent_bot_msgs = []
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                recent_bot_msgs.append(msg["content"])
            if len(recent_bot_msgs) >= 3:
                break
        
        if recent_bot_msgs:
            anti_rep_context = "\n\n🔴 تنبيه مهم — ردودك السابقة (ممنوع تكررها أو تشابهها):\n"
            for i, prev in enumerate(recent_bot_msgs, 1):
                # Truncate long messages to save tokens
                truncated = prev[:150] + "..." if len(prev) > 150 else prev
                anti_rep_context += f"{i}. \"{truncated}\"\n"
            anti_rep_context += "🔴 لازم ردك الجديد يكون مختلف تماماً عن الردود دي. استخدم كلمات مختلفة وأسلوب مختلف.\n"

    system_prompt = get_system_prompt(store) + anti_rep_context

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
        "content": message
    })

    return chat(messages), ""
