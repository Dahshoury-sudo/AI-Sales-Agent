from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order, restore_stock
from .general_service import handle_general
from products.models import Order
from django.db import transaction
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


def _detect_semantic_repetition(history):
    """
    Detect if the bot is saying the same IDEA even with different words.
    Checks for repeated key phrases across recent bot messages.
    """
    if not history:
        return 0
    
    bot_msgs = [msg["content"] for msg in history if msg.get("role") == "assistant"]
    if len(bot_msgs) < 3:
        return 0
    
    # Key phrases that indicate the bot is stuck in a pattern
    stuck_phrases = [
        "حولت طلبك", "حولت رسالتك", "حولت مشكلتك", "فريق خدمة العملاء", "هيتواصلوا معاك",
        "تحت أمرك", "أنا موجود", "لو في أي حاجة",
        "بتحب الفريش ولا", "عطر معين في بالك", "محتاج ترشيح",
    ]
    
    # Count how many of the last 4 bot messages contain the same stuck phrase
    recent = bot_msgs[-4:] if len(bot_msgs) >= 4 else bot_msgs
    
    max_repeat = 0
    for phrase in stuck_phrases:
        count = sum(1 for msg in recent if phrase in msg)
        max_repeat = max(max_repeat, count)
    
    return max_repeat


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
    for msg in history:
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if "حولت" in content and ("خدمة العملاء" in content or "الفريق" in content):
                return True
    return False


def _is_goodbye_loop(history):
    """
    Detect if the user is repeating 'سلام' or goodbye messages.
    Returns True if the user said goodbye 2+ times in a row.
    """
    if not history:
        return False
    
    goodbye_words = ["سلام", "باي", "مع السلامة", "bye", "شكرا", "سلام عليكم"]
    
    consecutive_goodbyes = 0
    for msg in reversed(history):
        if msg.get("role") == "user":
            content = msg["content"].strip()
            if any(content.strip() == gw or content.strip().startswith(gw) for gw in goodbye_words) and len(content) < 30:
                consecutive_goodbyes += 1
            else:
                break
    
    return consecutive_goodbyes >= 2


def route(message, history=None, store=None, conversation=None):
    if history is None:
        history = []

    # --- Goodbye loop detection ---
    if _is_goodbye_loop(history):
        goodbye_words = ["سلام", "باي", "مع السلامة", "bye", "شكرا"]
        msg_clean = message.strip()
        if any(msg_clean == gw or msg_clean.startswith(gw) for gw in goodbye_words) and len(msg_clean) < 30:
            return "نورتنا يا فندم! 😊 لو احتجت أي حاجة في المستقبل، إحنا هنا في خدمتك 24 ساعة. يوم سعيد!", ""

    request_type = classify(message, history)

    # --- Anti-repetition: detect semantic repetition (same idea, different words) ---
    semantic_rep = _detect_semantic_repetition(history)
    text_rep = _count_recent_repetitions(history)
    
    if text_rep >= 3 or semantic_rep >= 3:
        # Bot has been repeating itself — force a conversation redirect
        # Fetch real products from DB to prevent hallucination when suggesting alternatives
        from products.models import Product
        from django.db.models import Q
        random_products = Product.objects.filter(
            store=store, is_active=True
        ).filter(
            Q(oil_stock_grams__gt=0) | Q(variants__stock__gt=0)
        ).distinct().order_by('?')[:3]
        
        products_context = ""
        if random_products.exists():
            products_list = []
            for p in random_products:
                available_variants = []
                for v in p.variants.all():
                    if v.bottle_type == 'normal' and p.oil_stock_grams >= (v.volume * p.concentration_percentage) / 100:
                        available_variants.append(f"{v.volume}ml بـ {v.price} جنيه")
                    elif v.bottle_type == 'original' and (v.stock or 0) > 0:
                        available_variants.append(f"{v.volume}ml أوريجينال بـ {v.price} جنيه")
                if available_variants:
                    products_list.append(f"• {p.name} ({', '.join(available_variants)})")
            if products_list:
                products_context = "\n\n═══ منتجات متوفرة يمكنك اقتراحها (ممنوع تذكر أي منتج غيرهم) ═══\n" + "\n".join(products_list)
        
        return handle_general(
            f"""العميل بعتلي: "{message}"

⚠️ تنبيه: أنت كررت نفس الفكرة أكتر من 3 مرات. ممنوع تكرر نفس المحتوى تاني.

لو العميل بيسأل عن حاجة مش في تخصصك (زي خصم أو تعويض أو شكوى)، قوله بوضوح: "للأسف أنا مش أقدر أعمل خصومات أو تعويضات، بس فريق خدمة العملاء هيتواصل معاك. في الأثناء تحب أساعدك تختار عطر جديد؟"

لو العميل مش عايز حاجة وبيقول "سلام" بس، ودعه بشكل لطيف ومختصر جداً.

غير الموضوع تماماً واعرض على العميل حاجة جديدة ومختلفة.
❌ ممنوع تذكر أي منتج أو سعر مش موجود في القائمة التالية.{products_context}""",
            history, store
        )

    if request_type == "recommendation":
        intent = extract_intent(message, history)
        results = search_products(intent, store)
        response, context = recommend(message, results["products"], history, alternatives=results["alternatives"], store=store)
        
        if _is_repetitive(response, history):
            # Re-try with anti-repetition hint instead of handle_general (which lacks product context and may hallucinate)
            modified_msg = f"{message}\n\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم تختار منتجات مختلفة تماماً وتقدمها بأسلوب جديد."
            response, context = recommend(modified_msg, results["products"], history, alternatives=results["alternatives"], store=store)
        
        return response, context

    elif request_type == "product_info":
        response, context = get_product_info(message, history, store)
        
        if _is_repetitive(response, history):
            # Re-try with anti-repetition hint instead of handle_general (which lacks product data and may hallucinate)
            modified_msg = f"{message}\n\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم ترد بأسلوب مختلف تماماً."
            response, context = get_product_info(modified_msg, history, store)
        
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
                with transaction.atomic():
                    latest_order.status = "cancelled"
                    latest_order.bot_notes = "تم إلغاء الطلب بواسطة البوت بناءً على طلب العميل."
                    latest_order.save()
                    restore_stock(latest_order)
                return "تم الغاء الطلب اللي تم تسجيله بنجاح يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""
            else:
                return "مفيش طلب نشط حالياً عشان ألغيه يا فندم. لو كنت حابب تعمل طلب جديد أو محتاج أي مساعدة، أنا تحت أمرك!", ""
        return "مفيش طلب نشط حالياً عشان ألغيه يا فندم. لو كنت حابب تعمل طلب جديد أو محتاج أي مساعدة، أنا تحت أمرك!", ""
        
    elif request_type == "handoff":
        already_handed_off = _was_already_handed_off(history)
        
        if already_handed_off:
            # Already handed off before — don't repeat the same message
            return handle_general(
                f"""العميل بعتلي: "{message}"

⚠️ العميل ده اتحول لخدمة العملاء قبل كده بالفعل. ممنوع تقوله "حولت طلبك" أو "فريق خدمة العملاء هيتواصل" تاني.
بدل كده:
- لو بيشتكي: قوله "فاهمك وفريقنا شغال على الموضوع" بشكل مختصر جداً (جملة واحدة بس) واعرض عليه يساعده في حاجة تانية.
- لو بيسأل عن عطر: ساعده عادي.
- لو مش عايز حاجة: ودعه بأدب.""",
                history, store
            )
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