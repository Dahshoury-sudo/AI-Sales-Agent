import json
from .client import chat
from products.models import ConversationEvaluation
from products.services.conversation_service import get_conversation_messages

def evaluate_conversation(conversation):
    # Get all messages for the conversation to grade it accurately
    messages_qs = get_conversation_messages(conversation, limit=50)
    history_text = ""
    for msg in messages_qs:
        history_text += f"{msg.role}: {msg.content}\n"
        if msg.internal_context:
            history_text += f"[SYSTEM CONTEXT PROVIDED TO AI: {msg.internal_context}]\n"
    
    if not history_text:
        return None
        
    prompt = """
You are an expert AI evaluator.
Review the following conversation between a user and an AI perfume sales agent.
Grade the AI's performance from 0 to 100 on the following metrics:
- intent_score: Did the AI correctly understand the user's intent?
- search_score: Did the AI find the right products or correctly state none were found? CRITICAL: If the user asks for a general recommendation (e.g. "حلو", "عطر جميل") and the AI replies that it found NO products matching their request without offering any alternatives, PENALIZE this score heavily. A smart sales agent should always recommend best-sellers instead of giving up.
- product_info_score: Did the AI provide accurate product information?
- comparison_score: Was the comparison helpful and accurate?
- order_score: If an order was requested, did the AI collect details properly?
- has_hallucination: True or False. Did the AI invent any product, price, or policy that wasn't provided in the [SYSTEM CONTEXT PROVIDED TO AI]? If the AI mentioned a product that is NOT in the system context, this MUST be True.

Calculate an `overall_score` (0-100) based on the above.
Add a brief `evaluation_notes` in Arabic explaining the scores.

If a specific feature (like comparison or order) did not occur in this chat, default its score to 100.

Return ONLY valid JSON:
{
    "intent_score": 100,
    "search_score": 100,
    "product_info_score": 100,
    "comparison_score": 100,
    "order_score": 100,
    "overall_score": 100,
    "has_hallucination": false,
    "evaluation_notes": "..."
}
"""

    response = chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Conversation History:\n{history_text}"}
    ], response_format={"type": "json_object"})
    
    try:
        data = json.loads(response)
        
        eval_obj, created = ConversationEvaluation.objects.update_or_create(
            conversation=conversation,
            defaults={
                "intent_score": data.get("intent_score", 100),
                "search_score": data.get("search_score", 100),
                "product_info_score": data.get("product_info_score", 100),
                "comparison_score": data.get("comparison_score", 100),
                "order_score": data.get("order_score", 100),
                "overall_score": data.get("overall_score", 100.0),
                "has_hallucination": data.get("has_hallucination", False),
                "evaluation_notes": data.get("evaluation_notes", "")
            }
        )
        return eval_obj
    except Exception as e:
        print(f"Evaluation failed: {e}")
        return None
