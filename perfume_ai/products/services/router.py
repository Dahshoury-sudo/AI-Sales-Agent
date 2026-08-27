from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order, restore_stock, clear_cart
from .general_service import handle_general as _handle_general_raw
from .conversation_service import merge_preferences
from .notification_service import notify_handoff
from .usage_service import record_llm_message
from .identification_service import identify_perfume
from .objection_service import handle_objection
from .reply_sanitizer import soften_marketing_language, strip_premature_closing
from .sales import constraints as sales_constraints
from .sales import described as sales_described
from .sales import gender as sales_gender
from .sales import naming as sales_naming
from .sales import objection as sales_objection
from .sales import stage as sales_stage
from products.models import Order
from django.db import transaction
from difflib import SequenceMatcher


# Classifications where an objection in the message should take over. A customer objecting
# to a price is usually classified `faq` or `product_info`, and answering that as a product
# question is exactly the defend-instead-of-address failure. `order` and `order_cancel` are
# absent on purpose: mid-checkout hesitation is handled by the order flow, which holds the
# cart state this branch does not.
OBJECTION_ELIGIBLE = frozenset(
    {"faq", "handoff", "recommendation", "product_info", "greeting"}
)

# An explicit request for a person still goes to handoff, even when it carries an objection.
_ASKED_FOR_HUMAN = (
    "اكلم حد", "أكلم حد", "حد حقيقي", "موظف", "مندوب", "خدمة العملاء",
    "حد من الفريق", "بشري", "انسان", "إنسان", "اتكلم مع حد",
)


def _wants_a_human(message):
    return any(phrase in (message or "") for phrase in _ASKED_FOR_HUMAN)


def _finalize(reply, stage):
    """Post-process a generated reply according to the stage it was produced in.

    Both passes are code rather than prompt rules, for the reason reply_sanitizer's module
    docstring already gives: the persona forbade a closing question and the bot closed
    three replies in a row anyway. Applied only to model-generated text — scripted replies
    return directly and are pinned byte-for-byte by ScriptedRepliesSurviveSanitizingTests.
    """
    reply = soften_marketing_language(reply)
    if not sales_stage.closing_allowed(stage):
        reply = strip_premature_closing(reply, stage)
    return reply


def handle_general(message, history=None, store=None, stage=sales_stage.DISCOVERY):
    """A model-generated reply with no product data, finalized like every other path.

    Wraps general_service.handle_general because `route` returns through it in sixteen
    places and `_finalize` was only reached in five. Everything on those sixteen paths —
    every greeting, FAQ answer, out-of-domain redirect, promotion and musk deferral, and
    every one of the discovery gates — returned raw model output: neither
    soften_marketing_language nor strip_premature_closing ever ran on it. So a greeting
    could close the sale and nothing removed the close, which is most of why premature
    closing survived at all.

    Wrapping here rather than editing sixteen call sites keeps the diff honest and makes
    it impossible for a seventeenth branch to be added that forgets. The default stage
    forbids closing, which is correct for every one of these branches: none of them is a
    customer who has chosen anything.

    Scripted replies are deliberately NOT routed through this — they return directly from
    `route` and stay byte-for-byte identical, as ScriptedRepliesSurviveSanitizingTests
    pins them.
    """
    reply, context = _handle_general_raw(message, history, store)
    return _finalize(reply, stage), context



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

    # Vague questions the bot falls back on when it has nothing better to say.
    # Deliberately excludes the handoff wording ("حولت طلبك", "فريق خدمة العملاء",
    # "هيتواصلوا معاك"): the musk, promotion and handoff branches all *script* the
    # bot to say exactly that, so counting it flagged the router's own output. A
    # customer asking about offers, then musk, then for a human produced three
    # scripted handoff replies and got the turn hijacked below. Handoff looping is
    # already prevented by _was_already_handed_off and the classifier's
    # HANDOFF ANTI-LOOP RULES, so this detector does not need to police it.
    stuck_phrases = [
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
    """Check if the conversation was already handed off to a human.

    Matches "فريق" rather than "الفريق": the musk (router.py:353) and promotion
    (:403) branches script "حولت المحادثة لفريق المبيعات", where the ل prefix means
    the alef-lam form never appears. Requiring it missed those two branches
    entirely, so a customer handed off through them was handed off a second time —
    with a second notify_handoff — the next time they asked for a human.
    """
    if not history:
        return False
    for msg in history:
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            if "حولت" in content and ("خدمة العملاء" in content or "فريق" in content):
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
    #
    # Billing starts on this line, so the counter goes immediately above it: the
    # StaticFAQ match and the goodbye shortcut both returned earlier without spending
    # anything, and classify() is the first model call on every path that remains.
    record_llm_message(store)
    request_type = classify(message, history)

    # An objection is detected from the customer's own words rather than asked of the
    # classifier: it costs no extra model call, it is directly testable, and when it misses
    # the turn simply falls through to the behaviour it had before. It outranks the
    # classification because "غالي" arrives labelled faq or product_info, and answering
    # those as ordinary questions is the defend-instead-of-address bug.
    objection = None
    if request_type in OBJECTION_ELIGIBLE and not _wants_a_human(message):
        objection = sales_objection.detect(message, history)

    if objection is not None:
        reply, context = handle_objection(
            message, objection, history, store, conversation
        )
        stage = (
            sales_stage.COMPLAINT if objection.is_complaint else sales_stage.OBJECTION
        )
        return _finalize(reply, stage), context

    if request_type == "identification":
        reply, context = identify_perfume(message, history, store)
        return _finalize(reply, sales_stage.IDENTIFICATION), context

    # --- Anti-repetition: detect semantic repetition (same idea, different words) ---
    semantic_rep = _detect_semantic_repetition(history)
    text_rep = _count_recent_repetitions(history)
    
    if text_rep >= 3 or semantic_rep >= 3:
        # Bot has been repeating itself — force a conversation redirect
        # Fetch real products from DB to prevent hallucination when suggesting alternatives.
        # Deterministic rather than order_by('?'), so a repeated conversation can be
        # replayed and the customer is not shown a random gender mix.
        from .fallback import suggest_alternatives

        random_products = suggest_alternatives(store)
        
        products_context = ""
        if random_products:
            products_list = []
            for p in random_products:
                available_variants = []
                has_original_bottle = False
                for v in p.variants.all():
                    if v.bottle_type == 'normal':
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
        # Restore anything the customer said before the 8-message window cut it off.
        # Merged here, before every check below, so the gender and budget prompts and
        # search_products all see the full picture rather than a truncated one.
        intent = merge_preferences(conversation, intent, message)
        
        # Check if user explicitly insisted on multiple genders (rejected unisex)
        if intent.get("gender") == "multiple":
            return handle_general(
                f"""العميل بعتلي: "{message}"

العميل مُصر يشتري عطرين مختلفين (رجالي وحريمي) في نفس الوقت ومش عايز حاجة للجنسين.
قوله بلطف شديد: "ممتاز جداً! عشان أقدر أركز وأجيبلك أحسن حاجة لكل واحد فيكم، خلينا نختارهم واحد واحد. تحب نبدأ بالرجالي ولا الحريمي الأول؟"
❌ ممنوع ترشح أي عطر دلوقتي — استنى لما يختار هيبدأ بإيه.""",
                history, store
            )

        # Resolve gender from data before considering asking for it. The old gate
        # scanned only for literal Arabic gender words, so "عايز حاجة شبه سوفاج" —
        # a perfume this store stocks as gender=male — read as unknown and burned the
        # customer's most informative turn on "رجالي ولا حريمي؟". Three of four
        # lookalike requests in evaluation were answered that way.
        resolved_gender = sales_gender.resolve(intent, message, history, store)
        if resolved_gender:
            intent["gender"] = resolved_gender

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
            # The new slots count too, and "شبه Sauvage" is the most specific thing a
            # customer can say — without these, asking for a lookalike registered as
            # having said nothing about their taste and got answered with "قولي ذوقك".
            # Additive only: this can make has_taste_info true where it was false, never
            # the reverse, so no path that works today changes.
            intent.get("similar_to"),
            intent.get("avoid_notes"),
            intent.get("avoid_traits"),
            intent.get("wants_uncommon"),
        ])
        
        has_budget = intent.get("max_price") is not None
        # "مش مهم السعر" answers the budget question. Asking it anyway is the same
        # not-listening failure as re-asking a stated number.
        if not has_budget and sales_constraints.budget_is_open(message, history):
            has_budget = True

        # The gender gate is now a last resort rather than the first move. Ask only when
        # the data could not resolve it AND the customer has told us nothing else — at
        # or above that bar, answering the request and folding the gender question into
        # the same reply is what a salesperson actually does, and it is what the
        # `similar_to` clause in has_taste_info was always meant to protect.
        gender_unknown = not intent.get("gender")
        if gender_unknown and not has_taste_info:
            return handle_general(
                f"""العميل بعتلي: "{message}"

العميل ده عايز ترشيح عطر بس مش واضح عايز رجالي ولا حريمي.
اسأله سؤال واحد مختصر: بتدور على عطر رجالي ولا حريمي؟
ممنوع ترشح أي عطر قبل ما تعرف الإجابة. سؤال واحد بس ومتطولش.""",
                history, store
            )

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
                # A gift-giver who does not know the recipient's taste cannot answer "what
                # do you like?" — asking it anyway is what produced two perfumes and
                # "الاتنين مضمونين". Ask the one question they *can* answer instead.
                is_gift, recipient_taste_known = sales_constraints.gift_context(
                    message, history, intent
                )
                if is_gift and not recipient_taste_known:
                    return handle_general(
                        f"""العميل بعتلي: "{message}"
{sales_constraints.GIFT_UNCERTAINTY_HINT}""",
                        history, store
                    )

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
                # The customer told us plenty and the whole turn was still blocked on a
                # budget question that mentioned none of it: "عايز برفان رجالي ريحته فخمة
                # وثابتة، مناسب للخروجات بالليل، بس مش عايز حاجة تقيلة" was answered with
                # "ميزانيتك في حدود كام؟" and nothing else. Above the constraint threshold
                # we recommend and fold the budget probe into that reply instead; below it
                # we still ask, but with what they said attached so the question
                # acknowledges it. The wording is a hint, not a script — the behaviour
                # being replaced was one hardcoded sentence, and mandating a different
                # single sentence would be the same bug in a nicer costume.
                if not sales_constraints.can_recommend_without_budget(intent):
                    return handle_general(
                        f"""العميل بعتلي: "{message}"
{sales_constraints.acknowledgement_hint(intent)}
العميل ده قال تفضيلاته بس لسه متحددش ميزانيته.
اعترف باللي قاله في نص جملة قصيرة بأسلوبك، وبعدها اسأله عن الميزانية في سؤال واحد مختصر.

❌ ممنوع ترشح أي عطر دلوقتي — استنى لما يرد الأول.
❌ ممنوع تعيد سرد كل تفاصيل طلبه عليه.""",
                        history, store
                    )
        
        # What the conversation is already on, so ranking can hold those perfumes near the
        # top instead of re-deriving a fresh shortlist every turn. Without it a customer who
        # merely added a budget lost the perfume they had been converging on.
        # Reads the saved internal_context of our recent replies, not just their text, so a
        # perfume named only while being withdrawn does not count as still under discussion —
        # that loop announced the same withdrawal on turn after turn.
        keep = sales_described.under_discussion(conversation, store)
        results = search_products(intent, store, keep=keep)
        response, context = recommend(message, results["products"], history, alternatives=results["alternatives"], store=store, intent=intent, search=results, gender_unknown=gender_unknown)

        if _is_repetitive(response, history):
            # Re-try with anti-repetition hint instead of handle_general (which lacks product context and may hallucinate)
            modified_msg = f"{message}\n\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم تختار منتجات مختلفة تماماً وتقدمها بأسلوب جديد."
            response, context = recommend(modified_msg, results["products"], history, alternatives=results["alternatives"], store=store, intent=intent, search=results, gender_unknown=gender_unknown)

        # A customer being shown options for the first time has not chosen anything yet, so
        # this turn has not earned "تحب أساعدك في الطلب؟".
        stage = sales_stage.derive(request_type, message, intent, objection, history)
        return _finalize(response, stage), context

    elif request_type == "product_info":
        response, context = get_product_info(message, history, store, conversation)

        if _is_repetitive(response, history):
            # Re-try with anti-repetition hint instead of handle_general (which lacks product data and may hallucinate)
            modified_msg = f"{message}\n\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم ترد بأسلوب مختلف تماماً."
            response, context = get_product_info(modified_msg, history, store, conversation)

        # A price or size question is purchase-adjacent and may close; "ريحته عاملة ايه؟"
        # is a factual question and may not.
        stage = sales_stage.derive(request_type, message, None, objection, history)
        return _finalize(response, stage), context

    elif request_type == "comparison":
        response, context = compare_products(message, history, store)
        # Still weighing two options — differentiate, do not close.
        return _finalize(response, sales_stage.COMPARISON), context

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

            # Removing one line of several is an *edit*, not a cancellation. handle_order's
            # extractor already does this correctly — rule 5 drops the named perfume and
            # keeps the rest — but the message never reached it: "مش عايز" was a listed
            # example of order_cancel, so "مش عايز 1 × Noirvel (90ml)" wiped a two-item cart
            # along with the customer's name, phone and address, and they retyped everything
            # to order the one perfume they had wanted all along.
            #
            # Enforced here rather than left to the classifier because a dropped prompt rule
            # on this branch destroys a sale. Gated on more than one item: naming the only
            # item in the cart genuinely is a cancellation, and the extractor's `cart_cleared`
            # flag already covers that.
            if cart and cart.items.count() > 1:
                items = list(cart.items.select_related("variant__product"))
                in_cart = [item.variant.product for item in items]
                if sales_naming.mentioned_in(message, in_cart):
                    return handle_order(message, history, store, conversation)

            if cart and cart.items.exists():
                clear_cart(conversation, keep_details=True)
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

الرسالة دي مش متعلقة بالعطور. رد عليه بأسلوب ودود ومحترم ومختصر، ووجهه بلطف إنك متخصص في العطور وتقدر تساعده يختار عطر مميز.
🔴 لو الرسالة كلام عشوائي أو حروف مش مفهومة: قول "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر؟" وبس. ❌ ممنوع تهزر ولا تعمل نكتة ولا تلعب بالكلام — ده مكتوب في أسلوبك كخط أحمر.
ممنوع تكرر نفس الرد كل مرة.""",
            history, store
        )

    # Fallback for anything not explicitly matched
    return handle_general(message, history, store)