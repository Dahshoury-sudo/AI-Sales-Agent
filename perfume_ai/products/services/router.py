from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order
from .general_service import handle_general
from products.models import Order
from difflib import SequenceMatcher


def _is_repetitive(new_response, history):
    """
    Check if the bot's new response is too similar to recent bot responses.
    Returns True if repetition detected.
    """
    if not history:
        return False
    
    # Get last 4 bot responses from history
    recent_bot_msgs = []
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            recent_bot_msgs.append(msg["content"])
        if len(recent_bot_msgs) >= 4:
            break
    
    for prev_msg in recent_bot_msgs:
        similarity = SequenceMatcher(None, new_response.strip(), prev_msg.strip()).ratio()
        if similarity > 0.7:  # 70% similar = repetition
            return True
    
    return False


def _count_recent_repetitions(history):
    """
    Count how many times in a row the bot repeated similar messages.
    """
    if not history:
        return 0
    
    bot_msgs = [msg["content"] for msg in history if msg.get("role") == "assistant"]
    if len(bot_msgs) < 2:
        return 0
    
    count = 0
    last = bot_msgs[-1]
    for msg in reversed(bot_msgs[:-1]):
        similarity = SequenceMatcher(None, last.strip(), msg.strip()).ratio()
        if similarity > 0.7:
            count += 1
        else:
            break
    
    return count


def _was_already_handed_off(history):
    """Check if the conversation was already handed off to a human."""
    if not history:
        return False
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if "تم تحويل المحادثة" in content or "ممثلي خدمة العملاء" in content:
                return True
            # Only check the last few bot messages
            break
    return False


def route(message, history=None, store=None, conversation=None):
    if history is None:
        history = []

    request_type = classify(message, history)

    # --- Anti-repetition: detect repetition loops ---
    repetition_count = _count_recent_repetitions(history)
    
    if repetition_count >= 3:
        # Bot has been repeating itself 3+ times — force a conversation redirect
        return handle_general(
            "العميل والبوت دخلوا في دور تكرار. غير الموضوع تماماً واسأل العميل سؤال جديد أو اقترح عليه عطر مميز. ممنوع تكرر أي حاجة اتقالت قبل كده.",
            history, store
        )

    if request_type == "recommendation":
        intent = extract_intent(message, history)
        results = search_products(intent, store)
        response, context = recommend(message, results["products"], history, alternatives=results["alternatives"], store=store)
        
        if _is_repetitive(response, history):
            response, context = handle_general(message, history, store)
        
        return response, context

    elif request_type == "product_info":
        response, context = get_product_info(message, history, store)
        
        if _is_repetitive(response, history):
            response, context = handle_general(message, history, store)
        
        return response, context

    elif request_type == "comparison":
        return compare_products(message, history, store)

    elif request_type in ["greeting", "faq"]:
        return handle_general(message, history, store)
        
        
    elif request_type == "order":
        if not conversation:
            return "محتاج الأول تبدأ محادثة جديدة عشان أقدر أسجللك الطلب يا فندم.", ""
        return handle_order(message, history, store, conversation)
        
    elif request_type == "order_cancel":
        if conversation:
            latest_order = Order.objects.filter(conversation=conversation, status="pending").order_by('-created_at').first()
            if latest_order:
                latest_order.status = "cancelled"
                latest_order.bot_notes = "تم إلغاء الطلب بواسطة البوت بناءً على طلب العميل."
                latest_order.save()
                return "تم الغاء الطلب اللي تم تسجيله بنجاح يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""
        return "ولا يهمك يا فندم، نورتنا في أي وقت! ولو احتجت أي مساعدة إحنا موجودين 24 ساعة تحت أمرك.", ""
        
    elif request_type == "handoff":
        already_handed_off = _was_already_handed_off(history)
        
        if already_handed_off:
            # Already handed off before — don't repeat the same message
            # Instead, handle it gracefully through the AI
            return handle_general(message, history, store)
        else:
            # First time handoff
            if conversation:
                conversation.needs_human = True
                conversation.save()
            return handle_general(
                f"""العميل بعتلي الرسالة دي: "{message}"

العميل ده محتاج يتكلم مع حد بشري. اعتذرله بلطف وقوله إنك حولت المحادثة لفريق خدمة العملاء وإنهم هيتواصلوا معاه في أقرب وقت. 
كمان اسأله لو في أي حاجة تانية تقدر تساعده فيها في الأثناء.
ممنوع تستخدم نفس الصيغة كل مرة — نوع في أسلوبك.""",
                history, store
            )
        
    elif request_type == "out_of_domain":
        # Smart out-of-domain response via AI instead of hardcoded
        return handle_general(
            f"""العميل بعتلي الرسالة دي: "{message}"

الرسالة دي مش متعلقة بالعطور. رد عليه بأسلوب ودود وخفيف (مش جامد)، ووجهه بلطف إنك متخصص في العطور وتقدر تساعده يختار عطر مميز.
لو ممكن تربط الموضوع بالعطور بشكل مضحك أو ذكي، يبقى أحسن.
ممنوع تكرر نفس الرد كل مرة.""",
            history, store
        )

    # Fallback for anything not explicitly matched
    return handle_general(message, history, store)