from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION

from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order, restore_stock
from .general_service import handle_general
from .notification_service import notify_handoff
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

    # --- Static FAQ check (before AI — saves tokens) ---
    from .static_faq_service import match_static_faq
    faq_match = match_static_faq(message, store)
    if faq_match:
        return faq_match["answer"], ""

    # --- Goodbye loop detection ---
    if _is_goodbye_loop(history):
        goodbye_words = ["سلام", "باي", "مع السلامة", "bye", "شكرا"]
        msg_clean = message.strip()
        if any(msg_clean == gw or msg_clean.startswith(gw) for gw in goodbye_words) and len(msg_clean) < 30:
            return "نورتنا يا فندم! 😊 لو احتجت أي حاجة في المستقبل، إحنا هنا في خدمتك 24 ساعة. يوم سعيد!", ""

    # --- Parallel classify + pre-fetch intent for recommendation path ---
    # Fire both API calls simultaneously; classify result decides if intent is needed.
    # This saves ~4-6 seconds on every recommendation request (the most common path).
    intent_future = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        classify_future = executor.submit(classify, message, history)
        intent_future   = executor.submit(extract_intent, message, history)
        request_type = classify_future.result()  # wait for classifier first
        # intent_future keeps running in background; we'll .result() it only if needed

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
                has_original_bottle = False
                for v in p.variants.all():
                    if v.bottle_type == 'normal' and p.oil_stock_grams >= (v.volume * p.concentration_percentage) / 100:
                        available_variants.append(f"{v.volume} ملي بـ {v.price} جنيه")
                    elif v.bottle_type == 'original' and (v.stock or 0) > 0:
                        available_variants.append(f"{v.volume} ملي أوريجينال بـ {v.price} جنيه")
                        has_original_bottle = True
                if available_variants:
                    original_bottle_status = "" if has_original_bottle else " - (لا يوجد زجاجة أوريجينال)"
                    products_list.append(f"• {p.name} ({', '.join(available_variants)}){original_bottle_status}")
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
        # intent_future was already running in parallel — just collect the result now
        intent = intent_future.result()
        
        # Check if gender is missing and not inferable from conversation history
        if not intent.get("gender"):
            # Check if gender was mentioned in recent conversation history
            gender_mentioned = False
            if history:
                gender_keywords_male = ["رجالي", "رجالى", "للرجال", "male", "رجاليه", "ولادي", "شبابي", "شاب", "رجاله", "عريس", "لصاحبي", "لأخويا", "لأبويا", "لخطيبي", "لجوزي", "لابني", "لعمي", "لخالي", "أنا راجل"]
                gender_keywords_female = ["حريمي", "حريمى", "للبنات", "للستات", "female", "نسائي", "بناتي", "بنت", "بنات", "عروسة", "عروسه", "لصاحبتي", "لأختي", "لماما", "لخطيبتي", "لمراتي", "لبنتي", "لطنطي", "لخالتي", "أنا بنت"]
                for msg in history:
                    content = msg.get("content", "")
                    if any(kw in content for kw in gender_keywords_male + gender_keywords_female):
                        gender_mentioned = True
                        break
            
            # Also check the current message
            msg_lower = message
            gender_keywords_all = ["رجالي", "رجالى", "للرجال", "male", "حريمي", "حريمى", "للبنات", "للستات", "female", "نسائي", "يونيسيكس", "unisex", "بناتي", "ولادي", "شبابي", "شاب", "رجاله", "عريس", "عروسة", "عروسه", "لصاحبي", "لصاحبتي", "لأخويا", "لأختي", "لأبويا", "لماما", "لخطيبي", "لخطيبتي", "لجوزي", "لمراتي", "لابني", "لبنتي", "أنا راجل", "أنا بنت"]
            if any(kw in msg_lower for kw in gender_keywords_all):
                gender_mentioned = True
            
            if not gender_mentioned:
                # Gender not known — ask the customer first before recommending
                return handle_general(
                    f"""العميل بعتلي: "{message}"

العميل ده عايز ترشيح عطر بس مش واضح عايز رجالي ولا حريمي.
اسأله سؤال واحد مختصر: بتدور على عطر رجالي ولا حريمي؟
ممنوع ترشح أي عطر قبل ما تعرف الإجابة. سؤال واحد بس ومتطولش.""",
                    history, store
                )
        
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

    elif request_type == "musk_mix_product":
        msg_clean = message.strip().lower()

        # Check if user is accepting a previous handoff offer
        acceptance_words = ["آه", "اه", "ايوه", "ايوا", "أيوه", "تمام", "اوك", "ok", "يلا", "ماشي", "حوّلني", "حولني", "اتصل بيا", "اتصلوا بيا"]
        last_bot_was_musk = False
        if history:
            for msg in reversed(history):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if any(w in content for w in ["تخصص المندوب", "مندوب بشري", "تحولك لمندوب", "مش في تخصصي"]):
                        last_bot_was_musk = True
                    break

        if last_bot_was_musk and any(w in msg_clean for w in acceptance_words):
            # Customer accepted handoff
            if conversation:
                conversation.needs_human = True
                conversation.save()
                notify_handoff(conversation)
            return handle_general(
                """العميل وافق على التحويل لمندوب عشان يتابع معاه طلب المسك أو الميكس.
اعتذرله بلطف وقوله إنك حولت المحادثة لفريق المبيعات وإنهم هيتواصلوا معاه في أقرب وقت.
ممنوع تكرر نفس الصيغة — نوّع في أسلوبك.""",
                history, store
            )

        return handle_general(
            f"""العميل بعتلي: "{message}"

العميل ده بيسأل عن مسكات أو ميكسات كمنتج قائم بذاته.

تعليماتك:
1. وضّح بأسلوب لطيف إن المسكات والميكسات دي من تخصص المندوب البشري عندنا — أنت كمساعد آلي متخصص في عطور البرفان بس.
2. اعرض عليه إنك تحوله لمندوب بشري من الفريق يساعده في الموضوع ده.
❌ ممنوع تحاول تجاوب على أسئلة المسك أو الميكس أو تعمل ترشيح منهم.
❌ ممنوع تخترع معلومات عن منتجات المسك أو الميكس.""",
            history, store
        )

    elif request_type == "promotion":
        msg_clean = message.strip().lower()

        # Detect if user is insisting the bot execute the offer
        insistence_keywords = [
            "انت تنفذ", "انت نفذ", "نفذلي", "نفذه", "طبق العرض", "طبقلي", "حطلي الخصم",
            "اضف الخصم", "ضيف الخصم", "عايزك تعمل الخصم", "اعمل الخصم", "انت بقى اعمل",
            "لا انا عايزك", "انا عايزك انت", "بس انت", "مش عايز مندوب", "مش محتاج مندوب"
        ]
        is_insisting = any(kw in msg_clean for kw in insistence_keywords)

        # Also detect insistence from context: last bot message was about promotions, user is pushing back
        last_bot_was_promotion = False
        if history:
            for msg in reversed(history):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if any(w in content for w in ["تحب أحولك لمندوب", "مندوب من فريقنا", "مش بتقدر تطبق", "المندوب البشري"]):
                        last_bot_was_promotion = True
                    break

        # Check if the user is accepting the handoff offer
        acceptance_words = ["آه", "اه", "ايوه", "ايوا", "أيوه", "تمام", "اوك", "ok", "يلا", "ماشي", "حوّلني", "حولني", "اتصل بيا", "اتصلوا بيا"]

        if last_bot_was_promotion and any(w in msg_clean for w in acceptance_words) and not is_insisting:
            # Customer accepted handoff for promotion — treat as handoff
            if conversation:
                conversation.needs_human = True
                conversation.save()
                notify_handoff(conversation)
            return handle_general(
                f"""العميل وافق على التحويل لمندوب عشان يتابع عرض معاه.
اعتذرله بلطف وقوله إنك حولت المحادثة لفريق المبيعات وإنهم هيتواصلوا معاه في أقرب وقت.
ممنوع تكرر نفس الصيغة — نوّع في أسلوبك.""",
                history, store
            )

        if is_insisting:
            # Customer is insisting the bot execute the offer — firm, clear refusal
            return handle_general(
                f"""العميل بعتلي: "{message}"

العميل ده بيصرّ إن أنا (كبوت) أطبقله العرض أو الخصم بنفسي.

ردك لازم يكون واضح وحازم بأسلوب محترم:
1. وضّح بشكل قاطع إنك كمساعد آلي **مش في إمكانياتك** تطبق أو تنفذ أي عرض أو خصم — ده مش خيار، ده حقيقة تقنية.
2. اعتذر بلطف على عدم قدرتك على تنفيذ هذا الطلب.
3. اعرض عليه مرة تانية التحويل لمندوب بشري هو الوحيد القادر يطبق العرض فعلاً.
❌ ممنوع توهمه إنك هتطبق الخصم أو إنك ممكن تعمله في الطلب.
❌ ممنوع تعتذر وتسيب الموضوع — لازم تعرضله المندوب كحل بديل فعلي.""",
                history, store
            )


        # First-time promotion inquiry — show offers + disclaimer
        return handle_general(
            f"""العميل بعتلي: "{message}"

العميل ده سأل عن عروض أو خصومات أو أوفر.

تعليماتك:
1. اعرض عليه العروض الموجودة في الـ Store Custom Instructions بشكل واضح ومنظم. لو مفيش عروض في التعليمات، قوله: "مفيش عروض حالياً يا فندم."
2. وضّح بوضوح وبشكل صريح في ردك إنك كمساعد آلي **مش بتقدر تطبق أو تنفذ** أي عرض بنفسك — ده بيعمله المندوب البشري بس.
3. اعرض عليه إنك تحوله لمندوب بشري عشان يتابع العرض معاه ويطبقه فعلاً.
❌ ممنوع تقول إنك هتطبق الخصم أو هتضيفه للطلب.""",
            history, store
        )


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
                return "تم الغاء اخر اوردر تم تسجيله يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""
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
                notify_handoff(conversation)  # Notify store owner in dashboard
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