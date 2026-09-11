from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import (
    get_product_info,
    ABSENCE_DENIED_MARKER,
    NAME_UNREADABLE_MARKER,
)
from .comparison_service import compare_products
from .order_service import handle_order, restore_stock, clear_cart
from .general_service import handle_general as _handle_general_raw
from .conversation_service import merge_preferences
from .notification_service import create_notification, notify_handoff
from .usage_service import record_llm_message
from .identification_service import identify_perfume
from .objection_service import handle_objection
from .reply_sanitizer import soften_marketing_language, strip_premature_closing
from .sales import constraints as sales_constraints
from .sales import described as sales_described
from .sales import gender as sales_gender
from .sales import naming as sales_naming
from .sales import objection as sales_objection
from .sales import repetition
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
        # A narrowing next step ("أجيبلك الـ90 ولا الـ50؟") survives one stage earlier than a
        # hard ask does. Without that, the only CTA that could reach a customer mid-conversation
        # was a walk-in invite, which nothing in this module matches.
        reply = strip_premature_closing(
            reply, stage, allow_soft=sales_stage.soft_closing_allowed(stage)
        )
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

    The sentence-repetition retry is here for the same reason the finalizer is: this is the one
    place all sixteen paths pass through. `general_service._anti_repetition_context` already shows
    the model its last four replies before it writes — that is prevention, and it was in force on
    every turn of conversation 973 — while `_rephrased` reads the finished draft and names the
    sentence it repeated. Complementary, not redundant.

    Before `_finalize`, deliberately: the retry should be judged on what the model produced, not on
    text `strip_premature_closing` has already cut a sentence out of.
    """
    reply, context = _handle_general_raw(message, history, store)
    reply, context, _ = _rephrased(
        reply, context, history, store,
        lambda hint: _handle_general_raw(message, history, store, retry_hint=hint),
    )
    return _finalize(reply, stage), context



def _rephrased(reply, context, history, store, regenerate):
    """One targeted regeneration when a draft repeats a sentence we already said.

    `_is_repetitive` above compares whole replies at 0.7 and is the right guard for a branch that
    re-sends its previous answer wholesale. It cannot see conversation 973, whose five replies peaked
    at 0.451 against each other while opening and closing on the same two sentence frames four times
    over — the repetition a customer actually notices sits below the reply. `sales.repetition` looks
    there; this wires it to a retry.

    `regenerate` is a zero-arg callable returning `(reply, context)`, so each branch closes over its
    own arguments and this stays ignorant of what produced the draft — the same reason
    `handle_general` wraps `_handle_general_raw` rather than sixteen call sites being edited.

    One retry, never a loop, and the second draft is returned whether or not it is still repetitive:
    a reply that repeats a sentence is better than a third model call, and far better than an
    unbounded chain. `_is_repetitive`'s own retry above makes the same choice.

    Returns `(reply, context, repeats)` — the sentences that triggered it, or `()` — so a caller that
    wants to log or test the decision can, without re-running the comparison.
    """
    repeats = repetition.repeated_sentences(reply, history, store=store)
    # Plus the frames, which are a different question about the same draft: that one asks whether a
    # sentence has been SAID before, this asks whether a sentence has been STARTED the same way
    # three times. Conversation 973's replays fail only the second — its closing frame ended four
    # of five replies while every remaining pair measured under `SENTENCE_THRESHOLD`, because the
    # model varies the reason clause and keeps the opening. Merged into one list so a draft failing
    # both gets one retry, not two.
    for sentence in repetition.repeated_frames(reply, history, store=store):
        if sentence not in repeats:
            repeats.append(sentence)
    if not repeats:
        return reply, context, ()

    retried, retried_context = regenerate(repetition.retry_hint(repeats))
    # A generator that returns nothing usable is not an improvement. `chat` can come back empty on a
    # provider error, and `reply_sanitizer`'s bail-rather-than-empty rule exists because a blank
    # reply is the one outcome worse than a flawed one.
    if not (retried or "").strip():
        return reply, context, repeats
    return retried, retried_context, repeats


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


def _count_repeated_customer_questions(message, history):
    """How many earlier customer messages are this same question again.

    The mirror of `_count_recent_repetitions`, which watches the bot. A bot repeating itself is
    a style failure; a *customer* repeating themselves is the bot failing to answer, and nothing
    in the pipeline noticed it. Conversation 795 asked "عندكو لادور بخور صح ؟" at turn 1 and
    again at turn 3, was answered both times with a different perfume's price list, and the
    conversation simply ended.

    Not consecutive, unlike the bot counter. A customer who asks, gets a non-answer, says
    "طب اتأكدلي", and then asks again has intervening messages — and that is precisely the
    shape worth catching. The whole recent window is searched instead.
    """
    if not history or not (message or "").strip():
        return 0

    current = message.strip()
    count = 0
    for msg in history:
        if msg.get("role") != "user":
            continue
        earlier = (msg.get("content") or "").strip()
        if not earlier:
            continue
        # 0.7, the same threshold `_count_recent_repetitions` uses. Reusing it keeps one
        # definition of "the same message again" in this module rather than two.
        if SequenceMatcher(None, current, earlier).ratio() > 0.7:
            count += 1

    return count


# Three near-identical questions — this one plus two before it — is a customer who has asked and
# not been answered twice. Two is not enough: a customer who rephrases once is normal.
_REPEATED_QUESTION_LIMIT = 2


def _deferred_question(context, message):
    """The perfume the owner has to go and look up, as the customer wrote it.

    Read out of this turn's `PENDING_LOOKUP` payload rather than taken from `message`, because the
    two are not the same text and the notification asserts something about it: "والعطر ده مش في
    بيانات المتجر". Quoting the whole message makes that claim about every perfume in it.

    Two turns get it wrong. A partially-resolved question — 836 turn 1, "عايز اعرف اسعار بلو دي
    شانيل وسوفاج والكساندريا 2" — names two perfumes we stock and one we do not, and the raw message
    told the owner we carry none of the three. And when the customer is chasing an earlier question
    the message is "ها لقيت اي"; the owner was sent to look that up. `described.pending_lookup` makes
    the same point about the customer-facing side ("an owner told to go and look up 'طب اتأكدلي' has
    been told nothing").

    The payload is the right text in both cases and is unchanged in the ordinary one: for a total
    miss `product_info` records the raw message, so this returns exactly what it returned before.
    Falls back to `message` when the context carries no payload, which keeps a marker written without
    a question — still a turn that happened — reporting something.

    It is also what `_escalate_absent_name` compares against the conversation's earlier open
    questions, so a name that arrives here wrong hands the conversation to a human for the wrong
    reason as well as mis-briefing the owner.
    """
    for line in (context or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(sales_described.PENDING_LOOKUP_MARKER):
            continue
        payload = stripped[len(sales_described.PENDING_LOOKUP_MARKER):].strip()
        if payload:
            return payload
        break
    return (message or "").strip()


def _escalate_absent_name(conversation, store, context, message, history):
    """Pull the owner in when the bot could not answer about a perfume the customer named.

    `context` is this turn's own prompt context, so a marker in it describes *this* reply.

    **The bot is never muzzled on the turn it answers.** Both replies this function sees are
    complete answers the customer can act on: a plain denial with alternatives named, or a request
    to retype the name. Setting `needs_human` alongside either is what made conversations 816 and
    817 dead ends — `views.py` then answered every later message with silence, and the alternative
    the bot had just pitched could not be sold. "ماشي" and "اتأكد" each got nothing back. So notify
    the owner, who still wants to know a customer asked for something absent from the catalogue, and
    leave the bot able to keep serving.

    What earns a handoff is the customer coming back to the **same** perfume after being answered
    about it *twice*. There is nothing further the bot can truthfully say at that point: it has
    already swept the catalogue, or already asked for the name and been given the same one back.

    Twice, not once, and the second answer is not a wasted turn. A customer who chases immediately
    after the denial usually has not taken it in — 835 turn 2 and 836 turn 2 are both that — and the
    reply they are owed is the same answer said again, plainly, with the alternatives pushed harder.
    Handing over on the first chase would put the silence one turn later than 816 and 817 had it
    instead of removing it. Two complete answers about one absent perfume is where the bot runs out
    of true things to say.

    That comparison is the safety property, and it is why counting markers is not enough. Three
    different absent perfumes in one conversation are three ordinary questions with three complete
    answers — under a count they would trip the handoff on the third, and 795 is the conversation
    where that matters (لادور بخور then الكساندريا 2, two different names). `naming.re_asks`
    compares the customer's own words, so only pressing on the same one hands over.

    A repeated customer question still hands off on its own account, independently of any marker.
    It catches what a marker cannot: the customer asked, the bot answered about something else
    entirely, and nothing was recorded.

    There is no third fork for a bare `PENDING_LOOKUP`. Every pending block carries a verdict
    (`product_info._pending_lookup_block` is the only writer of the marker and always appends one),
    so a context that recorded an open question without saying which way it went cannot be produced.
    The fork that used to exist here counted how many earlier turns had deferred and handed over on
    the second — the muzzle the paragraph above is about — so leaving it in as a fallback would have
    meant keeping the retired policy alive on an unreachable path.

    Returns nothing and raises nothing that matters to the reply — the customer's answer has
    already been generated, and an owner notification is not worth losing it over.
    """
    if conversation is None or store is None:
        return
    if getattr(conversation, "needs_human", False):
        return

    denied = ABSENCE_DENIED_MARKER in (context or "")
    unreadable = NAME_UNREADABLE_MARKER in (context or "")
    question = _deferred_question(context, message)

    out_of_answers = False
    if denied or unreadable:
        marker = ABSENCE_DENIED_MARKER if denied else NAME_UNREADABLE_MARKER
        # Both halves are required. `pressed_again` alone would hand over the first time a customer
        # rephrases a name we have never answered about; the marker count alone would hand over on
        # the third *different* absent perfume. Together they say: we answered this one, and they
        # are back on it.
        #
        # `replies_carrying` counts previous replies only — this turn's context reaches the database
        # after the router returns (`views.py:126`, `tasks.py:165`), so a first answer never counts
        # itself. Two of them means this turn is the third reply about the one perfume: the denial,
        # the denial restated for the chase, and now nothing left.
        pressed_again = any(
            sales_naming.re_asks(question, earlier)
            for earlier in sales_described.pending_questions(conversation)
        )
        out_of_answers = (
            pressed_again and sales_described.replies_carrying(conversation, marker) >= 2
        )

    repeats = _count_repeated_customer_questions(message, history)
    if out_of_answers or repeats >= _REPEATED_QUESTION_LIMIT:
        conversation.needs_human = True
        conversation.save()
        notify_handoff(conversation)
        return

    if denied:
        create_notification(
            store=store,
            notif_type="handoff",
            title="عميل سأل عن عطر مش في الكتالوج ❌",
            message=(
                f"محادثة #{conversation.id}: العميل سأل عن «{question}» — والعطر ده مش في بيانات "
                f"المتجر، فالبوت قاله إنه مش موجود عندنا وعرض عليه بدائل. "
                f"لو العطر ده عندنا فعلاً أو تحب تجيبه، راجع المحادثة ورد على العميل."
            ),
        )
        return

    if unreadable:
        create_notification(
            store=store,
            notif_type="handoff",
            title="عميل كتب اسم عطر مش واضح 🔍",
            message=(
                f"محادثة #{conversation.id}: العميل كتب «{question}» ومقدرناش نتأكد هو قاصد أنهي "
                f"عطر، فالبوت طلب منه يكتب الاسم تاني. "
                f"لو إنت فاهم هو بيقصد إيه، راجع المحادثة ورد على العميل."
            ),
        )


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
        # `_SEQUENCE` and `PLAYBOOK` prescribe the same answer shape for the same objection kind, so
        # a customer who says "غالي" twice gets the same three moves twice — the sentence check is
        # the only guard on this branch, and the only one that would see it.
        reply, context, _ = _rephrased(
            reply, context, history, store,
            lambda hint: handle_objection(
                message, objection, history, store, conversation, retry_hint=hint
            ),
        )
        stage = (
            sales_stage.COMPLAINT if objection.is_complaint else sales_stage.OBJECTION
        )
        return _finalize(reply, stage), context

    if request_type == "identification":
        reply, context = identify_perfume(message, history, store, conversation)
        # A customer who cannot place a perfume goes round again, and `TIER_WORDING` hands back the
        # same sentence at the same confidence tier each time.
        reply, context, _ = _rephrased(
            reply, context, history, store,
            lambda hint: identify_perfume(message, history, store, conversation, retry_hint=hint),
        )
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
        #
        # `pending` is what the previous reply offered to relax when a search came back empty.
        # Without it a customer accepting the offer in terse words ("التانية", "اه") is answering
        # a question nothing recorded, and the constraint that emptied the search is restored
        # from `preferences` on top of their answer — conversation 932, four times over.
        intent = merge_preferences(
            conversation, intent, message,
            pending=sales_described.pending_relaxations(conversation),
        )
        
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
"قولي ذوقك 😊 يعني بتحب الفريش والخفيف ولا التقيل والخشبي ولا العود؟ ولا بتحب الحاجات المسكرة مثلا؟"

⚠️ لازم تكون رسالة واحدة مختصرة فيها كل الاختيارات مع بعض (مش أسئلة منفصلة). الهدف تفهم ذوقه في رسالة واحدة.
❌ ممنوع ترشح أي عطر دلوقتي — استنى لما يرد الأول.""",
                    history, store
                )
                

        
        # What the conversation is already on, so ranking can hold those perfumes near the
        # top instead of re-deriving a fresh shortlist every turn. Without it a customer who
        # merely added a budget lost the perfume they had been converging on.
        # Reads the saved internal_context of our recent replies, not just their text, so a
        # perfume named only while being withdrawn does not count as still under discussion —
        # that loop announced the same withdrawal on turn after turn.
        keep = sales_described.under_discussion(conversation, store)

        # Minus anything the customer has told us to move past. That two-reply window is what
        # re-promoted conversation 973's turn 4: Bloom and Coco Mademoiselle were excluded by name
        # on turn 3, the refusal on turn 4 carried no `exclude_names` of its own, and they arrived
        # in `keep` and `offered` at once — continuity +2.5 against repeat -2.0 is still +0.5, which
        # ranked them above a fresh Good Girl that matched just as well. A perfume the customer has
        # moved past is not what the conversation is on, whatever the window says.
        #
        # Subtracted here rather than inside `under_discussion`, which `offered_in_order` also reads
        # to resolve a reference: "بكام Bloom؟" after moving past Bloom must still place Bloom.
        keep = keep - sales_described.moved_past(conversation, store)

        # And what he has already SEEN, over the whole conversation rather than the two-reply
        # window `keep` uses. `ranking.WEIGHTS["repeat"]` sinks these below fresh candidates
        # without removing them, so a perfume from five turns ago can still be asked about.
        #
        # Conversation 973 is the failure: turn 4's "مش عايز حاجه من البراند بتاعكو" is a refusal,
        # not a request for alternatives, so `ai/intent.py` left `exclude_names` empty and the
        # unchanged intent re-derived turn 2's shortlist verbatim. Nothing was wrong with the
        # search; nothing in it knew the customer had seen those two already.
        offered = sales_described.offered_ever(conversation, store)
        results = search_products(intent, store, keep=keep, offered=offered)

        # Both retries below regenerate with the same products and the same arguments; only the
        # instruction text differs. A closure rather than three near-identical nine-argument calls,
        # which is how the second one drifted from the first before now.
        def _recommend_again(hint="", msg=None):
            return recommend(
                msg if msg is not None else message,
                results["products"], history, alternatives=results["alternatives"], store=store,
                intent=intent, search=results, gender_unknown=gender_unknown, repeat_hint=hint,
            )

        response, context = _recommend_again()

        if _is_repetitive(response, history):
            # Re-try with anti-repetition hint instead of handle_general (which lacks product context and may hallucinate)
            modified_msg = f"{message}\n\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم تختار منتجات مختلفة تماماً وتقدمها بأسلوب جديد."
            response, context = _recommend_again(msg=modified_msg)

        # After the whole-reply guard above, and a different question from it: that one asks
        # whether this reply IS the previous reply (0.7 on the full text) and answers it by
        # ordering different PRODUCTS. This asks whether a sentence in it has been said before,
        # and answers it by asking for different WORDING. Conversation 973 was invisible to the
        # first — 0.451 at its peak — and is why the second exists; a reply can fail either
        # test without failing the other, so both run.
        response, context, _ = _rephrased(
            response, context, history, store, _recommend_again,
        )

        # A customer being shown options for the first time has not chosen anything yet, so
        # this turn has not earned "تحب أساعدك في الطلب؟".
        stage = sales_stage.derive(request_type, message, intent, objection, history)
        return _finalize(response, stage), context

    elif request_type == "product_info":
        response, context = get_product_info(message, history, store, conversation)

        if _is_repetitive(response, history):
            # Re-try with an anti-repetition hint instead of handle_general (which lacks product
            # data and may hallucinate). The hint goes in as instruction text, not appended to the
            # customer's message: `get_product_info` reads the message to decide what perfume was
            # named, and a warning glued onto it reads as a perfume name we cannot place. See that
            # function's docstring for what it did to conversation 816's last turn.
            response, context = get_product_info(
                message,
                history,
                store,
                conversation,
                retry_hint=(
                    "\n⚠️ تنبيه: ردك السابق كان مكرر لكلام قلته قبل كده. لازم ترد بأسلوب مختلف "
                    "تماماً. ❌ ومتغيّرش إجابتك نفسها عشان كده — لو العطر مش موجود عندنا، فهو لسه "
                    "مش موجود؛ غيّر الصيغة مش الحقيقة.\n"
                ),
            )

        # The one deterministic guard on the phrase this whole change exists to remove. Rules alone
        # are not enough here: "لحظة أتأكدلك منه" was the scripted reply on this branch for a long
        # time and is still correct two rules away (red line 2, and the store-policy case), so a
        # model that reaches for it on a verified-absence turn is doing something the prompt used to
        # ask for.
        #
        # A second generation rather than a strip. `reply_sanitizer` cannot do this: its
        # bail-rather-than-empty rule means a reply that is *only* the promise — 816 turn 3 —
        # strips to nothing and gets handed back unchanged. And the phrase cannot be stripped
        # globally anyway, because the turns where it is right share this code path.
        #
        # Scoped to ABSENCE_DENIED, so it never fires on a `NAME_UNREADABLE` turn (whose rules also
        # ban the promise, but where the reply is a question and the stakes are lower) or on the
        # store-policy question that legitimately scripts it. Costs one extra LLM call on a
        # violating turn and nothing at all otherwise.
        if ABSENCE_DENIED_MARKER in (context or "") and sales_described.promises_a_lookup(response):
            response, context = get_product_info(
                message,
                history,
                store,
                conversation,
                retry_hint=(
                    "\n🔴🔴 ردك السابق كان فيه وعد إنك هتتأكد وترد على العميل ("
                    "\"لحظة أتأكدلك\" أو \"هسأل وأرد عليك\" أو \"هشوفه لك\") — وده ممنوع في الرد ده. "
                    "إحنا **اتأكدنا خلاص** من العطر ده في الكتالوج كله ومش عندنا، ومفيش حد هيراجع "
                    "حاجة بعد كده، فالوعد ده بيسيب العميل مستني رد عمره ما هييجي. اكتب الرد تاني: "
                    "قوله بوضوح وباعتذار قصير إن العطر مش موجود عندنا، وفي نفس الرد اعرض عليه بديل "
                    "أو اتنين من العطور اللي في البيانات بالاسم الكامل.\n"
                ),
            )

        # And the sentence-level check, on the same `retry_hint` channel the whole-reply retry above
        # already uses. This branch answers "بكام؟" and "ريحته عاملة ايه؟" turn after turn, so its
        # price and note sentences are the most re-said in the system — and any one of them is too
        # small a fraction of its reply to move a whole-reply ratio.
        #
        # Below the absence retry, not above it: that one is a correctness fix (the reply promised a
        # lookup nobody performs) and this one is a phrasing fix, so the phrasing check should see the
        # reply we are actually going to send.
        response, context, _ = _rephrased(
            response, context, history, store,
            lambda hint: get_product_info(message, history, store, conversation, retry_hint=hint),
        )

        # After the retry, so a deferral the retry introduced or removed is judged on the context
        # actually being sent.
        _escalate_absent_name(conversation, store, context, message, history)

        # A price or size question is purchase-adjacent and may close; "ريحته عاملة ايه؟"
        # is a factual question and may not.
        stage = sales_stage.derive(request_type, message, None, objection, history)
        return _finalize(response, stage), context

    elif request_type == "comparison":
        response, context = compare_products(message, history, store, conversation)

        # The first repetition check of any kind on this branch. It needs one more than most: its
        # instruction 5 REQUIRES the closing frame "أنا أرشحلك X أكتر لأن…" — the frame conversation
        # 973 ended four of five replies on — so a customer who compares twice gets it twice by
        # construction, and nothing here was looking.
        response, context, _ = _rephrased(
            response, context, history, store,
            lambda hint: compare_products(message, history, store, conversation, retry_hint=hint),
        )

        # `compare_products` hands a turn it cannot place two perfumes on straight to
        # `get_product_info`, so this branch now produces the same markers the one above does —
        # and a customer comparing a perfume we stock against one we do not is exactly the case
        # the owner needs told about. Escalating in both places rather than once after the chain
        # keeps the other dozen branches' `return`s untouched; these two are the only callers of
        # `get_product_info`.
        _escalate_absent_name(conversation, store, context, message, history)

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