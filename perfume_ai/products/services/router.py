from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order, restore_stock, clear_cart
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

    # --- Classify the request ---
    # Intent extraction is deliberately NOT started here. It is only needed by the
    # recommendation branch below, and running it up-front cost one wasted LLM call
    # on every other message. Firing it in a ThreadPoolExecutor did not help: the
    # `with` block exits via shutdown(wait=True), so it blocked on both calls
    # anyway.
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

⚠️ تنبيه هام: لقد استخدمت نفس العبارات أو الأفكار عدة مرات في ردودك السابقة.
الرجاء تغيير أسلوبك تماماً واستخدام كلمات مختلفة.
تفاعل مع رسالة العميل بشكل طبيعي ولكن بصياغة جديدة كلياً لتجنب التكرار.
❌ ممنوع تذكر أي منتج أو سعر مش موجود في القائمة التالية.{products_context}""",
            history, store
        )

    if request_type == "recommendation":
        # The only branch that needs the extracted intent.
        intent = extract_intent(message, history, store)
        
        # Check if user explicitly insisted on multiple genders (rejected unisex)
        if intent.get("gender") == "multiple":
            return handle_general(
                f"""العميل بعتلي: "{message}"

العميل مُصر يشتري عطرين مختلفين (رجالي وحريمي) في نفس الوقت ومش عايز حاجة للجنسين.
قوله بلطف شديد: "ممتاز جداً! عشان أقدر أركز وأجيبلك أحسن حاجة لكل واحد فيكم، خلينا نختارهم واحد واحد. تحب نبدأ بالرجالي ولا الحريمي الأول؟"
❌ ممنوع ترشح أي عطر دلوقتي — استنى لما يختار هيبدأ بإيه.""",
                history, store
            )

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
            msg_lower = message.lower()
            gender_keywords_all = ["رجالي", "رجالى", "للرجال", "male", "حريمي", "حريمى", "للبنات", "للستات", "female", "نسائي", "يونيسيكس", "unisex", "bi", "bisexual", "باي", "بايسكشوال", "بايسيكشوال", "بناتي", "ولادي", "شبابي", "شاب", "رجاله", "عريس", "عروسة", "عروسه", "لصاحبي", "لصاحبتي", "لأخويا", "لأختي", "لأبويا", "لماما", "لخطيبي", "لخطيبتي", "لجوزي", "لمراتي", "لابني", "لبنتي", "أنا راجل", "أنا بنت"]
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
        
        # Check if the intent is too vague (only gender, nothing about taste/preferences)
        # Ask about preferences before recommending blindly
        has_taste_info = any([
            intent.get("brand"),
            intent.get("perfume_type"),
            intent.get("season"),
            intent.get("occasion"),
            intent.get("longevity"),
            intent.get("projection"),
            intent.get("max_price"),
            intent.get("notes"),
        ])
        
        has_budget = intent.get("max_price") is not None
        
        if not has_taste_info or not has_budget:
            # Check if bot already asked about preferences or budget recently (avoid looping)
            already_asked_preferences = False
            already_asked_budget = False
            if history:
                preference_indicators = [
                    "بتحب", "تفضل", "ذوقك", "نوعية", "فريش", "عود", "خشبي",
                    "سويت", "تقيل", "خفيف", "فواح", "هادي", "ريحة معينة",
                    "ايه الريحة", "نوع العطر", "بتميل", "ستايلك",
                ]
                budget_indicators = [
                    "ميزانيتك", "حدود كام", "السعر اللي", "في رينج", "بكام",
                ]
                # Check last 3 bot messages only
                bot_count = 0
                for msg in reversed(history):
                    if msg.get("role") == "assistant":
                        content = msg.get("content", "")
                        if any(ind in content for ind in preference_indicators):
                            already_asked_preferences = True
                        if any(ind in content for ind in budget_indicators):
                            already_asked_budget = True
                        bot_count += 1
                        if bot_count >= 3:
                            break
            
            if not has_taste_info and not already_asked_preferences:
                return handle_general(
                    f"""العميل بعتلي: "{message}"

العميل ده عايز ترشيح عطر ({intent.get('gender', 'غير محدد')}) بس مقلش أي حاجة عن ذوقه أو تفضيلاته.

اسأله في رسالة واحدة مختصرة وودودة فيها اختيارات واضحة تغطي ذوقه، زي كده بالظبط:
"قولي ذوقك 😊 يعني بتحب الفريش والخفيف ولا التقيل والخشبي ولا العود؟ ولا بتحب الحاجات المسكرة مثلا؟ وميزانيتك في حدود كام؟"

⚠️ لازم تكون رسالة واحدة مختصرة فيها كل الاختيارات مع بعض (مش أسئلة منفصلة). الهدف تفهم ذوقه وميزانيته في رسالة واحدة.
❌ ممنوع ترشح أي عطر دلوقتي — استنى لما يرد الأول.""",
                    history, store
                )
                
            if has_taste_info and not has_budget and not already_asked_budget:
                return handle_general(
                    f"""العميل بعتلي: "{message}"

العميل ده عايز ترشيح عطر وقال تفضيلاته، بس لسه متحددش ميزانيته.
اسأله سؤال واحد مختصر عن الميزانية عشان تقدر ترشحله أنسب حاجة، زي كده:
"تمام 👌 ميزانيتك في حدود كام عشان أرشحلك أنسب حاجة؟"

❌ ممنوع ترشح أي عطر دلوقتي — استنى لما يرد الأول.""",
                    history, store
                )
        
        results = search_products(intent, store)
        response, context = recommend(message, results["products"], history, alternatives=results["alternatives"], store=store, intent=intent)
        
        if _is_repetitive(response, history):
            # Re-try with anti-repetition hint instead of handle_general (which lacks product context and may hallucinate)
            modified_msg = f"{message}\n\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم تختار منتجات مختلفة تماماً وتقدمها بأسلوب جديد."
            response, context = recommend(modified_msg, results["products"], history, alternatives=results["alternatives"], store=store, intent=intent)
        
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
2. عرّف العميل إنه يقدر يطلب المسكات والميكسات مباشرة من خلال موقعنا الإلكتروني، واعرض عليه برضه إنك تحوله لمندوب بشري من الفريق لو حابب حد يساعده فيهم.
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
            # An order still being assembled lives in a Cart and has taken no
            # stock, so cancelling it is just dropping the cart. Only a confirmed
            # order needs its stock returned.
            cart = getattr(conversation, "cart", None)
            if cart and cart.items.exists():
                clear_cart(conversation)
                return "تم إلغاء الطلب اللي كنا بنجهزه يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""

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