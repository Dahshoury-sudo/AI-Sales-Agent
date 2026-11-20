from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import (
    get_product_info,
    occasion_reference_requested,
    ABSENCE_DENIED_MARKER,
    NAME_UNREADABLE_MARKER,
    BARE_DENIAL,
    denial_only,
)
from .comparison_service import compare_products
from .order_service import handle_order, restore_stock, clear_cart
from .general_service import handle_general as _handle_general_raw
from .conversation_service import merge_preferences
from .notification_service import create_notification, notify_handoff
from .handoff import HANDOFF_CONFIRMATION, handoff_offer_reply, has_handoff_offer, requests_human
from . import promotion_service
from .performance_policy import enforce_reply, performance_turn
from .usage_service import record_llm_message
from .identification_service import identify_perfume
from .objection_service import handle_objection
from .product_resolver import confident_catalogue_match
from .reply_sanitizer import soften_marketing_language, strip_premature_closing
from .bottle_wording import NORMAL_BOTTLE, ORIGINAL_BOTTLE
from .sales import constraints as sales_constraints
from .sales import described as sales_described
from .sales import gender as sales_gender
from .sales import naming as sales_naming
from .sales import objection as sales_objection
from .sales import repetition
from .sales.followups import ASKS_TO_CHOOSE, accepts_price_offer, numeric_reference
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

# Classifications the catalogue may overrule, when the customer's own letters unmistakably spell a
# perfume we stock. See the long comment at the correction itself for conversation 1106 and why the
# catalogue outranks the classifier here.
#
# 🔴 The omissions carry the safety. `order` and `order_cancel` hold a cart, `comparison` holds two
# perfumes, and `identification` is a customer who cannot spell the name they are trying to recall —
# which is the one intent whose whole premise is that the letters should NOT be trusted. Rerouting
# any of them would discard state this branch does not have. `faq` and `out_of_domain` are listed
# because the ta-marbuta spelling ("عندكو خمرة") lands on them rather than on `recommendation`.
_CATALOGUE_CORRECTABLE = frozenset({"recommendation", "faq", "out_of_domain"})

# An explicit request for a person still goes to handoff, even when it carries an objection.
_ASKED_FOR_HUMAN = (
    "اكلم حد", "أكلم حد", "حد حقيقي", "موظف", "مندوب", "خدمة العملاء",
    "حد من الفريق", "بشري", "انسان", "إنسان", "اتكلم مع حد",
)


def _wants_a_human(message):
    return any(phrase in (message or "") for phrase in _ASKED_FOR_HUMAN)


from contextvars import ContextVar
# The conversation whose turn `route` is answering, for one reply only (the pattern
# performance_policy uses). `_rephrased` checks drafts from general call sites that never
# received it, and the customer's own cart and orders are price evidence there.
_TURN_CONVERSATION = ContextVar("perfume_turn_conversation", default=None)

# A choice among the perfumes already on the table, as opposed to a request for new ones.
# Applied to normalize_arabic output.
import re as _re
_CHOICE_AMONG_DISCUSSED = _re.compile(
    r"(?<!\w)(?:فيهم|منهم|بينهم|من\s+(?:ال)?(?:اتنين|تلاته|التلاته|ثلاثه)|of\s+them|between\s+them)(?!\w)")
_WANTS_OTHERS = _re.compile(r"(?<!\w)(?:غيرهم|غير\s+دول|تاني(?:ين)?|تانيه|جديد|other|others|else)(?!\w)")
# "خلاص اختارلي انت" while two options are on the table asks for one of them; read as a scent
# delegation it discarded her floral preference and brought a sweet perfume (S06 replay t8).
_DELEGATES_CHOICE = _re.compile(r"(?<!\w)(?:اختار(?:لي|يلي)|انت\s+اختار|انتي\s+اختاري|(?:علي|على)\s+ذوقك)(?!\w)")
_COMPARES_WITH_THE_OTHER = _re.compile(
    r"(?<!\w)(?:ال)?(?:يميز|فرق|بيفرق|يفرق|احسن|افضل|اخف|اتقل|اهدي|اقوي|ارخص|اغلي)\w*[^؟?.]{0,40}?"
    r"(?<!\w)(?:عن|من|و|وبين|بين)\s*(?:ال)?(?:تاني|تانيه|اولاني|اولانيه|اخر|اخري)(?!\w)")
_PICK_WORDS = _re.compile(r"(?<!\w)(?:انهي|مين|انسب|احسن|احلي|افضل|اختار\w*)(?!\w)")
# An explicit request for a different option, narrower than _WANTS_OTHERS: a bare "اي تاني؟" keeps
# continuity with what is on the table.
# Turning down everything on the table: "ولا واحد فيهم واضح إنه مش مسكر!", "أنا مش عايز حاجة حلاوتها مش
# مؤكدة! رشحلي…", "ما ترشحليش حاجة حلاوتها مش مؤكدة". The same two perfumes came back after each
# (simulation S10 round 2 batch 2, t3, t5, t8).
_REJECTS_THE_TABLE = _re.compile(
    r"ولا\s+واحد[ه]?\s+في(?:هم|هن)|الاتنين\s+مش\s+واضح|(?:ما\s+)?ترشحليش|متترشحليش|"
    r"مش\s+عايز\s+(?:اي\s+)?حاج(?:ه|ات)\s+\S+\s+مش\s+(?:مؤكد|موكد|متاكد|واضح)")
_RECOMMEND_VERB = _re.compile(r"(?<!\w)و?(?:رشح\w*|ترشح\w*|اقترح\w*|تقترح\w*)(?!\w)|\b(?:recommend|suggest)\b")
_ASKS_FOR_OTHERS = _re.compile(
    r"حاج(?:ه|ات)\s+تاني(?:ه)?|تاني\s+حاج(?:ه|ات)|غيرهم|غير\s+دول|(?:اختيار|ترشيح)(?:ات)?\s+تاني(?:ه)?|"
    r"something\s+else|other\s+(?:ones?|options?)")
# "مش عايزه، رشحلي…" turns down the perfume we were just talking about. The pronoun must close the
# phrase: "مش عايزه حاجة مسكرة" is a woman saying what she does not want, not a rejection.
_REJECTS_IT = _re.compile(
    r"(?<!\w)(?:مش|مبقتش)\s+(?:عايز|عاوز|حاب|حابب)(?:ه|ها|هم)(?=\s*(?:[،,.!؟?]|$|خالص|تاني|ده|دي|و\s))"
    r"|(?<!\w)بلاش\s+(?:ده|دي|دا|هو|هي)(?!\w)|(?<!\w)مش\s+عاجب(?:ني|اني)(?!\w)")


def _rejected_subject(message, conversation, store):
    """The perfume(s) a "مش عايزه" in this message refers to: the subject of our latest reply.

    S10 round 2, t6: told 212 VIB Man's sweetness was unconfirmed, the customer answered "طيب طالما
    الحلاوة مش مؤكدة مش عايزه، رشحلي حاجة مضمونة…" and got 212 VIB Man again first.
    """
    from .static_faq_service import normalize_arabic
    match = _REJECTS_IT.search(normalize_arabic(message or ""))
    if not match or conversation is None:
        return []
    latest = sales_described.offered_in_order(conversation, store, latest_only=True)
    plural = match.group().rstrip().endswith("هم")
    return latest if len(latest) == 1 or (plural and latest) else []


# A question about a combined total or about fitting more bottles in it.
# Explicit buying words keep a total question in checkout ("هاخدهم الاتنين، الإجمالي كام؟").
_PURCHASE_WORDS = _re.compile(r"(?<!\w)(?:هاخد\w*|اطلب\w*|اشتري\w*|هشتري\w*|احجز\w*|اكد\w*|كمل\s+الطلب)(?!\w)")


_KEEPS_THE_COUNT = _re.compile(r"(?<!\w)خلي(?:ها|هم|هوم)\s+(?:اتنين|2|تلاته|3|واحده|1|زجاجتين)(?!\w)")
# A wish for an unnamed perfume ("عايز حاجة لماما", "رشحلي عطر يكون هادي"), and the words that buy.
_WANTS_SOMETHING_FOR_SOMEONE = _re.compile(
    r"(?<!\w)و?(?:عايز\w*|عاوز\w*|محتاج\w*|رشح\w*|ترشح\w*|شوف\w*|اختار\w*)\s+(?:\S+\s+){0,2}?"
    r"(?:حاجه|(?:عطر|برفان|هديه)\s+(?:ل|تكون|يكون|فيه|فيها))")
_BUYING_WORDS = _re.compile(
    r"(?<!\w)و?(?:ضيف\w*|اطلب\w*|هطلب\w*|هاخد\w*|اخد\w*|خد\w*|هشتري\w*|اشتري\w*|احجز\w*|هحجز\w*|ابعت\w*|"
    r"order|buy|add)(?!\w)")


def _asked_to_keep_the_count(history):
    """Our latest reply asked whether to keep fewer bottles within the same combined budget."""
    latest = next((row.get("content", "") for row in reversed(history or []) if row.get("role") == "assistant"), "")
    return "تحب نخليها" in latest and "في نفس الميزانية" in latest


def _count_within_total(message, conversation):
    """A question about the total or a bottle count, asked under a saved combined budget."""
    from .static_faq_service import normalize_arabic
    return bool(conversation is not None and (conversation.preferences or {}).get("budget_scope") == "total"
                and _TOTAL_QUESTION.search(normalize_arabic(message or "")))


def _total_question_needs_recommendation(request_type, normalized, conversation, active_cart):
    """With a combined budget, "الإجمالي كده كام؟ ولو عايز 3 بنفس الـ1600 ينفع؟" needs the flow that
    computes totals and feasibility; product info may not state a total, so it answered with bare
    price blocks four turns running (simulation S03 replay t3-t7). The classifier also sent it to
    checkout ("تحب تطلب أنهي عطر؟", S03 final 2 t3), and the empty cart that turn created then blocked
    the re-route on the next question: an empty cart is not a cart in use.
    """
    cart_in_use = active_cart is not None and (active_cart.items.exists() or bool(active_cart.pending_items))
    return bool(request_type in ("product_info", "faq", "comparison", "order") and conversation is not None
                and not cart_in_use and (conversation.preferences or {}).get("budget_scope") == "total"
                and _TOTAL_QUESTION.search(normalized) and not _PURCHASE_WORDS.search(normalized))
_TOTAL_QUESTION = _re.compile(
    r"(?<!\w)و?(?:الاجمالي|اجمالي|المجموع|كله\s+بكام|(?:الاتنين|التلاته|التلات)\s+(?:هيطلعوا|هيبقوا|هيكونوا|بكام|كام)|"
    r"ينفع\s+\d+|لو\s+(?:عايز\s+)?\d+\s+(?:بنفس|في\s+نفس))")

# Hesitation that a usable recommendation answers better than a diagnostic question. Price
# objections and complaints about a past purchase keep their own handling.
_SOFT_OBJECTIONS = frozenset({"not_sure", "cant_choose", "rejected", "wont_like"})
# A price objection that goes on to ask for another perfume: "مفيش حاجة شبهه في حدود ١٠٠٠؟", "عندك
# حاجة تانية أرخص؟", "في بديل تحت 900؟". The bare objection ("غالي أوي") has none of these.
_ASKS_FOR_AN_ALTERNATIVE = _re.compile(
    r"(?<!\w)(?:مفيش|في|فيه|عندك|عندكم|عندكو)\s+(?:\S+\s+)?حاجه|حاجه\s+تانيه|(?<!\w)بديل|(?<!\w)شبه\w*|"
    r"في\s+حدود|(?<!\w)(?:تحت|لحد)\s+(?:ال)?[\d٠-٩]")


def _something_fits_the_budget(budget, conversation, store):
    """Whether the store sells anything at or under this budget (or no budget is set)."""
    from decimal import Decimal, InvalidOperation
    from .objection_service import _price_floor
    floor = _price_floor(store, conversation)
    if budget in (None, "") or floor is None:
        return True
    try:
        return floor <= Decimal(str(budget))
    except InvalidOperation:
        return True


def _actionable_request(intent, message):
    """Does this recommendation turn carry enough to act on: a preference or an explicit ask?"""
    import re
    from .sales.scent_preferences import has_scent_preference
    from .static_faq_service import normalize_arabic
    intent = intent or {}
    return bool(
        has_scent_preference(intent) or intent.get("max_price") or intent.get("occasion")
        or re.search(r"رشح|ترشح|اقترح|اختار(?:لي|يلي)|انت\s+اختار|recommend|suggest", normalize_arabic(message or ""))
    )


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
    every greeting, FAQ answer, out-of-domain redirect, musk deferral, and
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
        message=message,
    )
    return _finalize(reply, stage), context



_CHALLENGE = _re.compile(r"(?<!\w)(?:ليه|ازاي|انت|انتي|مش\s+انت)\s+(?:\S+\s+)?(?:قلت|قولت|بتقول|كتبت|قلتلي|قولتلي)(?!\w)")
_ADMITS_MISTAKE = _re.compile(
    r"بالغلط|(?<!مش )(?<!\w)غلطت(?!\w)|غلط(?:ه)?\s+مني|كان\s+غلط|اعتذر\w*\s+عن\s+(?:الغلط|اللخبط)")


# A sentence about what a perfume suits, as the claim checker reads one.
_OCCASION_SENTENCE = _re.compile(
    r"(?<!\w)و?(?:مناسب\w*|يناسب\w*|تناسب\w*|ينفع\s+ل\w+|ينفعوا\s+ل\w+)(?!\w)|\bsuitable\b|"
    r"(?<!\w)(?:لل|ل)(?:سهر\w*|خروج\w*|شغل|مناسبات|صيف|شتا|نهار|ليل)(?!\w)")


def _challenge_retraction_issues(reply, message):
    """A mistake admitted because the customer questioned what we said.

    "طيب ليه قلت مفيش حاجة تحت 700؟" got "معاك حق، أنا غلطت لما قلت مفيش حاجة تحت 700" while nothing
    under 700 is stocked (simulation S11 round 2 batch 4, t3). The retry is told to check the earlier
    statement against the data and explain it.
    """
    from .static_faq_service import normalize_arabic
    if not message or not _CHALLENGE.search(normalize_arabic(message)) or not _ADMITS_MISTAKE.search(normalize_arabic(reply or "")):
        return []
    return ["retraction: العميل بيسأل ليه قلت كده؛ راجع الكلام اللي اتقال على البيانات واشرحه منها، "
            "ومتقولش إنه كان غلط إلا لو البيانات بتقول كده"]


def _rephrased(reply, context, history, store, regenerate, *, recommendation_intent=None,
               recommendation_candidates=None, message=None, preferred_occasion=None, budget=None):
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
    from .product_formatting import (product_claim_facts, factual_description,
        product_price_issues, quote_evidence, append_quotation, product_quotes, render_quotes, quote_context)
    from .reply_sanitizer import product_claim_issues
    from .price_reply import composition_issues, quotation_prose, fallback_price_reply, requested_product_details
    from products.models import Product
    catalogue = list(Product.objects.filter(store=store).select_related("brand").prefetch_related("variants")) if store else []
    evidence = quote_evidence(context)
    message = message if message is not None else evidence.get("request")
    # Only actual product rows and quotations supply evidence; names in notes,
    # labels (e.g. Pi in "Value Pick") and flanker warnings do not.
    row_names = sales_described._row_names(context or "", [p.name for p in catalogue])
    quoted_ids = {q.get("product_id") for q in evidence.get("quotes", [])}
    rows = [p for p in catalogue if p.name in row_names or p.pk in quoted_ids]
    # Reference rows supply comparison facts, not products to offer in recovery.
    offer_rows = list(recommendation_candidates) if recommendation_candidates is not None else rows
    facts = product_claim_facts(rows)
    from .scent_description import occasion_preference
    preferred_occasion = occasion_preference(
        message, product_names=facts,
        saved=preferred_occasion or (recommendation_intent or {}).get("occasion"),
    )

    def claims(draft, draft_context):
        # A missing external subject cannot make the displayed alternatives the
        # answer to its scent question. Any actual description still gets checked.
        scoped_message = message
        if any(marker in (draft_context or "") for marker in ("ABSENCE_DENIED", "NAME_UNREADABLE")):
            scoped_message = "" if message is not None else None
        return product_claim_issues(draft, facts, context=draft_context, message=scoped_message,
                                    history=history, preferred_occasion=preferred_occasion,
                                    description_required=recommendation_intent is not None) \
            + _challenge_retraction_issues(draft, message)
    from products.models import StoreSettings, StaticFAQ
    sources = []
    if store:
        config = StoreSettings.objects.filter(store=store).first()
        if config:
            sources += [config.business_facts or "", config.promotions or "", config.payment_instructions or ""]
        sources += list(StaticFAQ.objects.filter(store=store, is_active=True).values_list("answer", flat=True))
    sources += [m.get("content", "") for m in history or [] if m.get("role") == "user"]
    # The customer's own cart and orders, read from the database: the general branch has no
    # product rows, so a reply restating a confirmed order's price used to fail here.
    from .order_service import checkout_evidence
    checkout_products, checkout_lines = checkout_evidence(_TURN_CONVERSATION.get())
    sources += checkout_lines
    price_rows = rows + [p for p in checkout_products if p not in rows]
    if evidence.get("budget") is not None:
        sources.append("ميزانية " + evidence["budget"] + " جنيه")
    if evidence and not evidence.get("append"):
        sources.append(context)
    issues = (claims(reply, context)
              + product_price_issues(reply, price_rows, evidence=evidence, monetary_sources=sources, catalogue=catalogue)
              + composition_issues(reply, context))
    prose = quotation_prose(reply, evidence)
    repeats = repetition.repeated_sentences(prose, history, store=store)
    # Plus the frames, which are a different question about the same draft: that one asks whether a
    # sentence has been SAID before, this asks whether a sentence has been STARTED the same way
    # three times. Conversation 973's replays fail only the second — its closing frame ended four
    # of five replies while every remaining pair measured under `SENTENCE_THRESHOLD`, because the
    # model varies the reason clause and keeps the opening. Merged into one list so a draft failing
    # both gets one retry, not two.
    for sentence in repetition.repeated_frames(prose, history, store=store):
        if sentence not in repeats:
            repeats.append(sentence)
    if denial_only(context):
        # "ها لقيت اي ؟" after a denial is owed the denial again, and since 2026-11-13 that sentence
        # is the whole reply — so every chase repeated itself by construction and was regenerated,
        # which re-runs the extractor and the confirming call and can come back with a promise.
        repeats = [sentence for sentence in repeats if not sales_described.states_absence(sentence)]
    if not repeats and not issues:
        return append_quotation(reply, context), context, ()

    def checked(draft, draft_context):
        return (claims(draft, draft_context)
                + product_price_issues(draft, price_rows, evidence=quote_evidence(draft_context),
                                       monetary_sources=sources, catalogue=catalogue)
                + composition_issues(draft, draft_context))

    def without_unasked_occasion(draft, draft_context, found):
        """The draft minus its unasked occasion sentences, when those were its only fault and the rest
        passes every check; else None.

        "الاتنين طابعهم مسكر، لكن مش قادر أجزم… الاتنين مناسبتهم المسجلة للسهرات والخروجات." answered the
        question and was thrown away twice for its last sentence; the fallback said "Black XS: طابعه
        المسجل مسكر." and left the comparison unanswered (simulation S14 round 4 replay, t8).
        """
        if not found or not all("occasion suitability" in issue for issue in found):
            return None
        lines = []
        for line in (draft or "").split("\n"):
            kept = [sentence for sentence in _re.split(r"(?<=[.!؟?؛])\s+", line)
                    if not _OCCASION_SENTENCE.search(sales_naming.normalize_product_name(sentence))]
            if kept or not line.strip():
                lines.append(" ".join(kept))
        trimmed = _re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        trimmed = _re.sub(r"[؛،]\s*$", ".", trimmed)
        if not trimmed or trimmed == (draft or "").strip() or checked(trimmed, draft_context):
            return None
        return trimmed

    if not repeats:
        salvaged = without_unasked_occasion(reply, context, issues)
        if salvaged:
            return append_quotation(salvaged, context), context, ()

    hint = repetition.retry_hint(repeats)
    if issues:
        hint += "\nصحح الادعاءات غير المدعومة دي من بيانات العطر فقط: " + "؛ ".join(issues)
    try:
        retried, retried_context = regenerate(hint)
    except Exception:
        if not issues:
            raise
        retried, retried_context = "", context
    remaining = claims(retried, retried_context) + product_price_issues(
        retried, price_rows, evidence=quote_evidence(retried_context), monetary_sources=sources, catalogue=catalogue)
    remaining += composition_issues(retried, retried_context)
    salvaged = without_unasked_occasion(retried, retried_context, remaining)
    if salvaged:
        retried, remaining = salvaged, []
    if remaining or (issues and not (retried or "").strip()):
        if denial_only(context):
            # A turn whose whole answer is "we do not carry it" has one supported fallback, and it
            # is not a question about sizes or an offer to transfer — both of which the exits below
            # produced once the turn stopped carrying product rows. Not passed through
            # `_not_a_repeat` either: said twice, it is still the answer.
            return BARE_DENIAL, context, repeats
        if evidence.get("sections"):
            quoted_ids = {q.get("product_id") for q in evidence.get("quotes", [])}
            selected = [p for p in offer_rows if p.pk in quoted_ids]
            fallback, fallback_context = fallback_price_reply(context, selected, recommendation_intent)
            fallback_issues = (
                product_claim_issues(fallback, product_claim_facts(selected), context=fallback_context,
                                     message=message, history=history, preferred_occasion=preferred_occasion,
                                     description_required=recommendation_intent is not None)
                + product_price_issues(fallback, selected, evidence=quote_evidence(fallback_context),
                                       monetary_sources=sources, catalogue=catalogue)
                + composition_issues(fallback, fallback_context)
            )
            if not fallback_issues:
                return fallback, fallback_context, repeats
        if any(i.startswith("price:") for i in remaining or issues):
            ids = {q.get("product_id") for q in evidence.get("quotes", [])}
            mentioned = sales_naming.names_in(reply, [p.name for p in offer_rows])
            selected = [p for p in offer_rows if p.pk in ids or p.name in mentioned]
            if not selected and len(offer_rows) == 1 and not sales_naming.names_in(reply, [p.name for p in catalogue]):
                selected = offer_rows
            from .sales.value import as_budget
            # Without quote evidence the fallback used to render every size, so "غالي عليا" against
            # a 700 budget was answered with 1000 and 1375 (simulation S11 replay 2, t2). Keep the
            # requested size/bottle and the stated budget.
            budget = as_budget(evidence.get("budget")) or as_budget(budget)
            quotes = product_quotes(selected, message or "", budget)
            variant_ids = {q.get("variant_id") for q in evidence.get("quotes", [])}
            if variant_ids:
                quotes = [q for q in product_quotes(selected) if q.variant_id in variant_ids]
            if quotes:
                fallback = render_quotes(quotes, max_price=budget)
                if recommendation_intent is not None:
                    from .ai.recommendation import recommendation_fit
                    quoted_ids = {q.product_id for q in quotes}
                    explanation = recommendation_fit(
                        [p for p in selected if p.pk in quoted_ids], recommendation_intent,
                        message=message, history=history,
                    )
                    if explanation:
                        fallback = explanation + "\n\n" + fallback
                else:
                    details = requested_product_details(selected, message, preferred_occasion=preferred_occasion,
                                                        history=history)
                    if details:
                        fallback = "\n".join(details) + "\n\n" + fallback
                return fallback, quote_context(context, quotes, max_price=budget), repeats
            from .product_formatting import price_question
            if message and not offer_rows and _SHIPPING_TOPIC.search(sales_naming.normalize_product_name(message)):
                # A shipping question whose draft failed the price check must not be asked "which perfume
                # and size?" (simulation S12 t3, S13 t4, round 3 full run): the store's own shipping
                # sentence answers it.
                from .order_service import shipping_answer
                fact = shipping_answer(store)
                if fact:
                    return (fact if fact.endswith((".", "!", "؟")) else fact + "."), context, repeats
            # A message naming a perfume ("أكدته خلاص، Sauvage الـ90") still gets "which perfume and size?".
            if message and not offer_rows and not price_question(message, history, None) \
                    and not _re.search(r"[A-Za-z]{3,}", message):
                # "طب ولو مجاش على ذوقها أعمل إيه؟" asked about the exchange policy and got "تقصد أنهي عطر
                # وحجم؟… أقولك السعر" twice (simulation S07 round 2, t3-t4). With no perfume involved and
                # no price asked, only the sentences carrying an unsupported figure are dropped.
                draft = retried if (retried or "").strip() else reply
                left = remaining if (retried or "").strip() else issues
                flagged = {match.group(1) for issue in left if (match := _re.match(r"price: (\d+)", issue))}
                if flagged and all(issue.startswith("price:") for issue in left):
                    kept = [sentence for sentence in _re.split(r"(?<=[.!؟?])\s+|\n+", draft or "")
                            if sentence.strip() and not any(_re.search(r"(?<!\d)" + amount + r"(?!\d)", sentence)
                                                            for amount in flagged)]
                    if kept:
                        return _not_a_repeat(" ".join(kept), message, history), context, repeats
                return _not_a_repeat("المعلومة دي محتاجة تأكيد من فريق خدمة العملاء. تحب أحولك ليهم؟",
                                     message, history), context, repeats
            return "تقصد أنهي عطر وحجم؟ ممكن تكتبهم عشان أقولك السعر الحالي؟", context, repeats
        if any(i.startswith("authenticity:") for i in remaining or issues):
            return sales_constraints.formulation_reply(), context, repeats
        mentioned = sales_naming.names_in(reply, [p.name for p in offer_rows])
        by_name = {p.name: p for p in offer_rows}
        from .sales.recommendation_response import _choice_limit
        limit = _choice_limit(recommendation_intent)
        selected = [by_name[name] for name in mentioned[:limit]]
        if recommendation_intent is None:
            selected = [by_name[name] for name in mentioned]
            requested_names = sales_naming.names_in(message or "", list(by_name))
            selected += [by_name[name] for name in requested_names if by_name[name] not in selected]
            # The turn's own product rows are what an indirect question ("الأولاني كان إيه؟",
            # "قارنلي بينهم") is about, even when the rejected draft named neither of them.
            if not selected and 0 < len(offer_rows) <= 2 and not any(
                marker in (context or "") for marker in ("ABSENCE_DENIED", "NAME_UNREADABLE")
            ):
                selected = offer_rows
        if (recommendation_intent or {}).get("recommendation_mode") == "new_goal":
            selected = offer_rows[:limit]
        denied = "ABSENCE_DENIED" in (context or "")
        if denied and recommendation_intent is None and not selected:
            selected = offer_rows[:2]
        if recommendation_intent is not None:
            from .ai.recommendation import recommendation_fit
            fallback = recommendation_fit(selected, recommendation_intent, message=message, history=history)
        elif denied and selected:
            # A denial that still carries rows is a partially-resolved turn: the rows are perfumes the
            # customer asked about in the same message, not substitutes (a denial-only turn returned
            # `BARE_DENIAL` above). They are answered as asked, and the perfume we do not carry keeps
            # its own line — answered "متوفر" line by line with nothing after it, the fallback dropped
            # the denial (simulation S09 round 2, t1).
            lines = requested_product_details(selected, message, preferred_occasion=preferred_occasion,
                                              history=history)
            lines = lines or requested_product_details(selected, "", history=history, description_required=True)
            lines = lines or [f"{product.name}: {factual_description(product)}." for product in selected]
            fallback = "\n".join(lines) + "\nأما العطر التاني اللي سألت عنه فللأسف مش موجود عندنا."
        else:
            lines = requested_product_details(selected, message, preferred_occasion=preferred_occasion, history=history)
            if message is None and not lines:
                # Preserve legacy callers that have not supplied a question.
                lines = [f"{product.name}: {factual_description(product)}." for product in selected]
            if not lines and selected:
                # A general question about a named perfume ("إيه اللي يميزه؟", "قارنلي") requests no
                # single attribute, so the old fallback rendered nothing and sent "ماقدرش أأكد
                # المعلومة دي" in nine baseline turns (simulation S02, S05, S06, S08, S14). The
                # supported answer is the perfume's own tag description.
                lines = requested_product_details(selected, message, preferred_occasion=preferred_occasion,
                                                  history=history, description_required=True)
                lines = lines or [f"{product.name}: {factual_description(product)}." for product in selected]
            fallback = "\n".join(lines)
            if len(selected) == 2 and lines and ASKS_TO_CHOOSE.search(sales_naming.normalize_product_name(message or "")):
                # A pick or a difference was asked for; the two descriptions alone repeated an
                # earlier reply almost word for word and chose nothing (simulation S13 final 1, t4).
                fallback += "\n" + _label_difference(*selected)
                if _DELEGATES_CHOICE.search(sales_naming.normalize_product_name(message or "")):
                    fallback += "\n" + _pick_for_customer(*selected, _TURN_CONVERSATION.get())
        if not fallback and not selected:
            # The store's performance policy covers every stocked perfume, so a general longevity
            # question has a supported answer with no product row at all (simulation S16 t5-t6).
            from .performance_policy import current_request, description
            request = current_request()
            if request.longevity or request.projection:
                text = description(request, longevity=request.longevity, projection=request.projection)
                fallback = "كل عطورنا " + text.replace("ثباته ", "ثباتها ").replace("فوحانه ", "فوحانها ") + "."
        if any(i.startswith("popularity:") for i in remaining or issues):
            fallback = (fallback + " ماقدرش أجزم بمدى انتشار العطور دي.") if fallback else (
                "مقدرش أقولك أنهي الأكتر مبيعاً، بس أقدر أرشحلك على حسب الريحة اللي بتحبها.")
        if not fallback:
            # With product rows the doubt is which perfume; with none, the honest answer is the
            # team, which is a transfer this system can actually make (simulation S16 replay t7).
            fallback = ("تقصد أنهي عطر بالظبط؟" if offer_rows else
                        "المعلومة دي محتاجة تأكيد من فريق خدمة العملاء. تحب أحولك ليهم؟")
        fallback = _not_a_repeat(fallback, message, history)
        return append_quotation(fallback, context), context, repeats
    # A generator that returns nothing usable is not an improvement. `chat` can come back empty on a
    # provider error, and `reply_sanitizer`'s bail-rather-than-empty rule exists because a blank
    # reply is the one outcome worse than a flawed one.
    if not (retried or "").strip():
        return reply, context, repeats
    return append_quotation(retried, retried_context), retried_context, repeats


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
    complete answers: a plain denial, or a request to retype the name. Setting `needs_human` alongside either is what made conversations 816 and
    817 dead ends — `views.py` then answered every later message with silence, so a customer who went on
    to ask for something we do sell got nothing. "ماشي" and "اتأكد" each got nothing back. So notify
    the owner, who still wants to know a customer asked for something absent from the catalogue, and
    leave the bot able to keep serving.

    What earns a handoff is the customer coming back to the **same** perfume after being answered
    about it *twice*. There is nothing further the bot can truthfully say at that point: it has
    already swept the catalogue, or already asked for the name and been given the same one back.

    Twice, not once, and the second answer is not a wasted turn. A customer who chases immediately
    after the denial usually has not taken it in — 835 turn 2 and 836 turn 2 are both that — and the
    reply they are owed is the same answer said again, plainly.
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
        return True

    if denied:
        create_notification(
            store=store,
            notif_type="handoff",
            title="عميل سأل عن عطر مش في الكتالوج ❌",
            message=(
                f"محادثة #{conversation.id}: العميل سأل عن «{question}» — والعطر ده مش في بيانات "
                f"المتجر، فالبوت قاله إنه مش موجود عندنا وبس، من غير ما يعرض بدائل. "
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


def _denial_only_reply(reply, context, message, store):
    """Hold a denial-only turn to the denial. Returns the reply to send.

    Store owner, 2026-11-13: *"when someone asks for a perfume that is unavailable i want you to say
    it's not available and stop. don't offer alternatives."* `product_info` sends no substitute rows
    and forbids the pitch, and this is the deterministic line behind both: whatever the model wrote
    past the denial — a perfume from earlier in the chat, "تحب أرشحلك حاجة قريبة؟", a promise to
    check — is cut clause by clause (`described.denial_alone`), and a reply with no clean denial
    left in it becomes `BARE_DENIAL`.

    A trim rather than a regeneration: a second `get_product_info` is up to three model calls, can
    come back with a different verdict, and is no likelier to obey. Runs after `_rephrased` so it
    reads the reply actually being sent, and before the code-written additions (handoff
    confirmation, policy answers, "معنديش بيانات عن ريحة…"), which are not the model's to lose.

    Scoped by `product_info.denial_only`, so a partially-resolved turn — whose reply is mostly about
    perfumes the customer did ask for — and every other turn pass through untouched.
    """
    if not denial_only(context) or store is None:
        return reply
    from products.models import Product
    names = list(Product.objects.filter(store=store, is_active=True).values_list("name", flat=True))
    asked = f"{message or ''} {_deferred_question(context, message)}"
    return sales_described.denial_alone(reply, names, asked) or BARE_DENIAL


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


def _handoff(message, history, store, conversation):
    """Activate once, including a fresh offer after an earlier resolved handoff."""
    if conversation:
        from products.models import Conversation

        activated = Conversation.objects.filter(pk=conversation.pk, needs_human=False).update(needs_human=True)
        conversation.needs_human = True
        if activated:
            notify_handoff(conversation)
    from .static_faq_service import normalize_arabic
    if store and _re.search(r"(?:شحن|توصيل)\S*\s+(?:\S+\s+){0,2}?(?:بكام|كام)|(?:بكام|كام|مصاريف)\s+(?:\S+\s+)?(?:ال)?(?:شحن|توصيل)",
                            normalize_arabic(message or "")):
        # "آه حوّلني، بس عايز أعرف الشحن هيبقى بكام؟": the transfer does not take the fee question
        # with it unanswered (simulation S11 round 4 replay, t6).
        from .order_service import shipping_answer
        fee = shipping_answer(store)
        if fee:
            return HANDOFF_CONFIRMATION + "\n\n" + (fee if fee.endswith((".", "!", "؟")) else fee + "."), ""
    return HANDOFF_CONFIRMATION, ""


_PRICE_RANGE_QUESTION = _re.compile(
    r"(?:الاسعار|اسعاركم|اسعارك|اسعاركو)[^؟?.]{0,30}?(?:بتبدا|تبدا|بتبتدي|من\s+كام|كام|تقريبا|عامله|رينج)"
    r"|(?:رينج|range)\s+(?:لل|ال)?(?:اسعار|prices?)|price\s+range")


def _store_price_range(message, store, conversation=None):
    """"الأسعار بتبدأ من كام تقريبًا؟" answered with the owner's own sentence from the business facts.

    Owner decision (2026-11-12): the range lives in the business facts only. The model would not
    state it, answering "مش متاح عندي سعر بداية مؤكد" four times to a browsing customer (S18).
    """
    from products.models import Cart, StoreSettings
    from .static_faq_service import normalize_arabic
    from .product_info import _named_in_message
    if store is None:
        return ""
    cart = Cart.objects.filter(conversation=conversation).first() if conversation is not None else None
    if cart is not None and (cart.items.exists() or cart.pending_items):
        return ""
    if not _PRICE_RANGE_QUESTION.search(normalize_arabic(message or "")) or _named_in_message(message, store):
        return ""
    facts = StoreSettings.objects.filter(store=store).values_list("business_facts", flat=True).first() or ""
    for line in facts.splitlines():
        text = line.strip(" -•*\t")
        if "اسعار" in normalize_arabic(text) and _re.search(r"\d", text):
            return text if text.endswith((".", "!", "؟")) else text + "."
    return ""


# "أنا بحب الفريش للشغل، وأخويا بيحب السويت" marks the speaker with أنا (simulation S03 round 2 batch 2, t2).
_SELF_MARK = _re.compile(r"(?<!\w)(?:ليا|لي|لنفسي|عشاني|بتاعي|انا|اني)(?!\w)")


def _per_recipient_scents(message):
    """{"self": [tags], "brother": [tags]} when one message gives each person their own scent.

    "الفريش ليا للشغل، والسويت لأخويا للخروج" was merged into one fresh-and-sweet request, and
    both perfumes came back fresh and sweet, four turns running (simulation S03).
    """
    from .conversation_service import _BARE_RECIPIENT, _OTHER_RECIPIENT, _OTHER_RECIPIENTS
    from .scent_description import _tag_mentions
    from .static_faq_service import normalize_arabic
    by_word = {word: key for key, words in _OTHER_RECIPIENTS.items() for word in words}
    pairs = {}
    for segment in _re.split(r"[،,.!؟?\n]|\s+و(?=\S)", normalize_arabic(message or "")):
        # Within one person's clause the recipient may be its subject: "وأخويا بيحب السويت" (S03 round 2
        # batch 2, t2), not only "لأخويا".
        other = _OTHER_RECIPIENT.search(segment) or _BARE_RECIPIENT.search(segment)
        recipient = by_word.get(other.group(1)) if other else ("self" if _SELF_MARK.search(segment) else None)
        tags = [tag for tag, _, _, negative in _tag_mentions(segment) if not negative]
        if recipient and tags:
            pairs.setdefault(recipient, [])
            pairs[recipient] += [tag for tag in tags if tag not in pairs[recipient]]
    if len(pairs) == 2 and len({tuple(sorted(tags)) for tags in pairs.values()}) == 2:
        return pairs
    return {}


_RECIPIENT_LABELS = {"brother": "أخوك", "sister": "أختك", "mother": "مامتك", "father": "باباك", "wife": "مراتك",
                     "husband": "جوزك", "fiancee": "خطيبتك", "fiance": "خطيبك", "son": "ابنك", "daughter": "بنتك",
                     "friend": "صاحبك"}


def Conversation_sales_state(conversation, state):
    from products.models import Conversation
    Conversation.objects.filter(pk=conversation.pk).update(sales_state=state)
    conversation.sales_state = state


def _pair_recommendation(message, pair, intent, history, store, conversation):
    """One perfume per person from their own scent, recommended together; None if one is missing.

    Each person's search excludes the other's distinctive scent, so "fresh for me, sweet for my
    brother" cannot come back as two perfumes that are each fresh and sweet (simulation S03).
    """
    from .search_service import search_products
    from .sales.value import as_budget
    chosen = []
    share = {}
    total = as_budget(intent.get("max_price")) if intent.get("budget_scope") == "total" else None
    if total:
        # Each person's search gets an even share of a combined budget; searched with the whole 1600,
        # one pick could take a 1400 bottle and leave nothing that fits beside it.
        share = {"max_price": float(total / len(pair)), "budget_scope": "per_item"}
    from products.models import Product
    from .search_service import product_matches_request
    from .static_faq_service import normalize_arabic
    normalized = normalize_arabic(message or "")
    # "خليهم 2، بس كده الإجمالي 1500؟" keeps the pair on the table; searched again, the already-offered
    # picks ranked last and a different pair replaced the customer's (simulation S03 round 2 full 2, t4).
    # A pick stays while that person's scent is the same and nobody asked for other options.
    state = dict((conversation.sales_state or {}) if conversation is not None else {})
    picks = {} if (_ASKS_FOR_OTHERS.search(normalized) or _REJECTS_THE_TABLE.search(normalized)) \
        else dict(state.get("recipient_picks") or {})
    for recipient, tags in pair.items():
        others = {tag for key, other in pair.items() if key != recipient for tag in other} - set(tags)
        person = {**intent, **share, "scent_tags": list(tags), "scent_match": "any", "scent_open": False,
                  "excluded_scent_tags": sorted((set(intent.get("excluded_scent_tags") or ()) - set(tags)) | others),
                  "exclude_names": sorted(set(intent.get("exclude_names") or ()) | {p.name for _, p in chosen}),
                  "purchase_quantity": 1, "recommendation_count": 1, "similar_to": None}
        kept = picks.get(recipient) or {}
        previous = Product.objects.filter(store=store, name=kept.get("name"), is_active=True).first() \
            if kept.get("name") and sorted(kept.get("tags") or ()) == sorted(tags) else None
        if previous is not None and previous.name not in person["exclude_names"] \
                and product_matches_request(previous, person, store):
            chosen.append((recipient, previous))
            continue
        found = list(search_products(person, store, offered=sales_described.offered_ever(conversation, store))
                     ["products"][:1])
        if not found:
            return None
        chosen.append((recipient, found[0]))
    if conversation is not None:
        state["recipient_picks"] = {recipient: {"name": product.name, "tags": list(pair[recipient])}
                                    for recipient, product in chosen}
        Conversation_sales_state(conversation, {**(conversation.sales_state or {}), **state})
    tags = sorted({tag for values in pair.values() for tag in values})
    combined = {**intent, "scent_tags": tags, "scent_match": "any", "scent_open": False, "similar_to": None,
                "excluded_scent_tags": sorted(set(intent.get("excluded_scent_tags") or ()) - set(tags)),
                "purchase_quantity": max(2, int(intent.get("purchase_quantity") or 2)), "recommendation_count": 2}
    products = [product for _, product in chosen]
    response, context = recommend(message, products, history, store=store, intent=combined,
                                  search={"effective_intent": combined, "products": products}, gender_unknown=False)
    owners = dict(chosen)
    if "self" in owners and len(owners) == 2:
        other_key = next(key for key in owners if key != "self")
        label = _RECIPIENT_LABELS.get(other_key)
        if label and owners["self"].name in response and owners[other_key].name in response:
            response = f"{owners['self'].name} ليك، و{owners[other_key].name} لـ{label}.\n\n" + response
    stage = sales_stage.derive("recommendation", message, combined, None, history)
    return _finalize(response, stage), context


def _total_only_if_repeat(response, normalized, history, store=None):
    """A total question that would resend the same recommendation gets the total line alone.

    "الإجمالي كده كام بالظبط؟" re-ran the recommendation and sent the identical block again, three times
    in one conversation (simulation S03 round 2, t4, t5, t7). The line says what shipping adds, from
    the store's own facts: "1500 جنيه، من غير مصاريف الشحن" answered "ومفيش أي مصاريف تانية؟" and was
    read as "no shipping" (S03 round 2 full 5, t4).
    """
    keeps_count = bool(_KEEPS_THE_COUNT.search(normalized or "") or _re.search(r"شحن", normalized or ""))
    if not response or not (_TOTAL_QUESTION.search(normalized or "") or keeps_count):
        return response
    recent = [row.get("content", "") for row in (history or [])[-6:] if row.get("role") == "assistant"]
    if not any(SequenceMatcher(None, response, earlier).ratio() >= 0.9 for earlier in recent):
        return response
    total = next((line.strip() for line in response.splitlines() if line.strip().startswith("إجمالي ال")), "")
    if not total:
        return response
    from .order_service import shipping_line
    amounts = _re.findall(r"(\d+(?:\.\d+)?)\s*جنيه", total)
    shipping = shipping_line(store, amounts[-1]) if amounts else ""
    line = total.rstrip(".") + (". " + shipping if shipping else "، والمبلغ ده من غير مصاريف الشحن.")
    # "خلاص خليهم اتنين، بس مش هزود عن 1600 بالشحن" re-sent the identical pair block (simulation S03
    # round 2 batch 4, t5): the count is kept, so the picks are named once and the total follows.
    owners = response.splitlines()[0].strip()
    if keeps_count and not _TOTAL_QUESTION.search(normalized or "") and " ليك، و" in owners:
        return "تمام، " + owners + "\n" + line
    return line


_CHEAPER = _re.compile(r"ارخص|اقل\s+(?:من|سعر|حاجه)|\bcheaper\b|\bless\s+expensive\b")
_REPEAT_COMPLAINT = _re.compile(r"بتكرر|بتعيد|كررت|عيدت|نفس\s+(?:الترشيحات|الكلام|الحاجات|العطور|الاختيارات)")


def _repeats_recent(response, history):
    recent = [row.get("content", "") for row in (history or [])[-6:] if row.get("role") == "assistant"]
    return any(SequenceMatcher(None, response, earlier).ratio() >= 0.9 for earlier in recent)


def _nothing_cheaper(response, normalized, history, products, store, conversation):
    """"يعني مفيش أي حاجة أرخص من 750؟" answered directly when the same perfumes would come back.

    The question re-ran the recommendation and resent the earlier block word for word (simulation S11
    round 2 batch 2, t5). When the perfumes it would offer already sit at the store's lowest price for
    this customer, the answer is that nothing is cheaper, with their price.
    """
    if not response or not _CHEAPER.search(normalized or ""):
        return response
    recent = [row.get("content", "") for row in (history or [])[-6:] if row.get("role") == "assistant"]
    if not any(SequenceMatcher(None, response, earlier).ratio() >= 0.9 for earlier in recent):
        return response
    from .objection_service import _price_floor
    from .product_formatting import is_variant_available, money
    floor = _price_floor(store, conversation)
    by_name = {product.name: product for product in products}
    named = [by_name[name] for name in sales_naming.names_in(response, list(by_name))]
    cheapest = [min(((v.price, v.volume) for v in product.variants.all() if v.volume > 0 and is_variant_available(v)),
                    default=None) for product in named]
    if floor is None or not named or any(item is None or item[0] != floor for item in cheapest):
        return response
    volumes = {volume for _, volume in cheapest}
    size = f" للـ{volumes.pop()} ملي" if len(volumes) == 1 else ""
    return (f"{' و'.join(product.name for product in named)} بـ{money(floor)} جنيه{size}، ودي أقل أسعار عندنا؛ "
            "مفيش حاجة أرخص منها حاليًا.")


def _nothing_cheaper_exists(message, normalized, history, conversation, store):
    """The direct answer to a cheaper question when the price in question is already our lowest.

    With 750 the lowest price for this customer: "طب في بديل أرخص شوية؟" got "تقصد عطر شبه أنهي عطر؟"
    twice, "رشحلي… أقل من 750" got the same 750 perfume, "مفيش حاجة أرخص منه؟" got two more at 750,
    and "دول برضه بـ750؟ مفيش حاجة أرخص؟" got "تقصد أنهي عطر وحجم؟" (simulation S11 round 2 batch 5,
    t4-t8). The price asked about is the one the customer states, else the lowest we quoted lately;
    when nothing in the store is below it, that is the answer on every route.
    """
    if not store or not _CHEAPER.search(normalized or "") or len(_re.findall(r"[؟?]", message or "")) > 1:
        return ""
    from decimal import Decimal
    from .objection_service import _price_floor
    from .product_formatting import money
    floor = _price_floor(store, conversation)
    if floor is None:
        return ""
    stated = [Decimal(n) for n in _re.findall(r"(?<![\d.])(\d{3,5})(?![\d.]|\s*(?:ملي|مل|ml))", normalized)]
    replies = [row.get("content", "") for row in (history or []) if row.get("role") == "assistant"]
    quoted = [Decimal(n) for reply in replies[-2:] for n in _re.findall(r"بـ?\s*(\d{3,5})\s*جنيه", reply)]
    reference = min(stated) if stated else (min(quoted) if quoted else None)
    if reference is None or floor < reference:
        return ""
    line = f"مفيش حاجة أرخص من {money(floor)} جنيه عندنا حاليًا؛ ده أقل سعر عندنا."
    if any(line in reply for reply in replies[-3:]):
        return (f"زي ما قلتلك، {money(floor)} جنيه هو أقل سعر عندنا ومفيش أرخص منه حاليًا. "
                "لو حابب تكمل بيه قولي، ولو محتاج تفكر خد وقتك.")
    return line


# A demand for certainty, in the customer's own words: "تضمن", and also "تكون متأكد إنها مش مسكرة" and
# "مش عايز أخاطر", which got more perfumes and no word on what can be confirmed (simulation S10 round 3
# verify, t4-t8).
# The conjunction may be attached: "ومضمونة مش مسكرة" slipped past a pattern that began at the word
# (simulation S10 round 4 replay, t3).
_GUARANTEE = _re.compile(r"(?<!\w)[وفبل]?(?:ال)?(?:تضمن\w*|مضمون\w*|ضامن\w*|يضمن\w*|متاكد\w*|اتاكد\w*|اخاطر\w*|مخاطر\w*|"
                         r"مطمن\w*|اطمن\w*)(?!\w)")
def _not_sweet_line(products):
    """"X وY مش مسكرين، ومفيش فانيليا ولا عسل في نوتاتهم." for perfumes without the sweet label.

    Owner, 2026-11-20: a perfume without the sweet label is not sweet, and the customer who demands a
    guarantee is told so plainly, with no "100%" disclaimer. The notes clause is added only when every
    one of them has notes recorded and none has a sweet material (vanilla, honey…) in them.
    """
    from .sales.scent_matching import has_sweet_notes
    names = " و".join(product.name for product in products)
    one = len(products) == 1
    line = names + (" مش مسكر" if one else " مش مسكرين")
    noted = all(any((getattr(product, layer, "") or "").strip() for layer in ("top_notes", "middle_notes", "base_notes"))
                for product in products)
    if noted and not any(has_sweet_notes(product) for product in products):
        line += "، ومفيش فانيليا ولا عسل في " + ("نوتاته" if one else "نوتاتهم")
    return line + "."


def _confirm_not_sweet(normalized, store, on_table):
    """"متأكد إنهم مش مسكرين؟" about the perfumes on the table: answered from their labels.

    It used to exclude them and offer new ones, because their sweetness counted as unconfirmed; a
    second demand got a refusal to recommend anything (simulation S10 round 3 verify, t6/t8). Since
    the owner's 2026-11-20 decision a missing sweet label means not sweet, so the question about the
    perfumes already offered has a plain answer. Asking for other picks still gets new ones.
    """
    if not on_table or _RECOMMEND_VERB.search(normalized) or _ASKS_FOR_OTHERS.search(normalized) \
            or _REJECTS_THE_TABLE.search(normalized):
        return ""
    from products.models import Product
    shown = list(Product.objects.filter(store=store, is_active=True, name__in=list(on_table)).order_by("name"))
    if not shown:
        return ""
    sweet = [product for product in shown if "sweet" in (product.scent_tags or ())]
    not_sweet = [product for product in shown if product not in sweet]
    if not sweet:
        return "أيوه، " + _not_sweet_line(not_sweet)
    names, one = " و".join(product.name for product in sweet), len(sweet) == 1
    if not_sweet:
        return _not_sweet_line(not_sweet) + " أما " + names + (" فمسكر." if one else " فمسكرين.")
    return "لا، " + names + (" مسكر." if one else " مسكرين.")


def _guarantee_note(response, products, intent, normalized):
    """"رشحلي حاجة تضمن إنها مش مسكرة": say plainly which picks are not sweet.

    S10 round 2 full 3, t6-t7: each such demand got options with no word on their sweetness. Owner,
    2026-11-20: the picks without the sweet label are not sweet, said with no "100%" disclaimer and
    no "try it in the store first" tail.
    """
    if not response or not _GUARANTEE.search(normalized or "") \
            or "sweet" not in set((intent or {}).get("excluded_scent_tags") or ()):
        return response
    from .sales.scent_matching import has_sweet_notes
    by_name = {product.name: product for product in products}
    clean = [by_name[name] for name in sales_naming.names_in(response, list(by_name))
             if "sweet" not in (by_name[name].scent_tags or ()) and not has_sweet_notes(by_name[name])]
    return (_not_sweet_line(clean) + "\n\n" + response) if clean else response


def _without_sweet_notes(results):
    """On a "guaranteed not sweet" request, drop picks with vanilla or honey in their notes.

    Owner, 2026-11-20. Ranking already puts them after clean picks; here they go, as long as one clean
    pick is left.
    """
    from .sales.scent_matching import has_sweet_notes
    products = list(results.get("products") or ())
    clean = [product for product in products if not has_sweet_notes(product)]
    return {**results, "products": clean} if clean and len(clean) < len(products) else results


_RECALL = _re.compile(r"كنت\s+(?:قلت|قولت|قلتلي|قولتلي|رشحت\w*|اقترحت\w*)|(?:رشحت|اقترحت)(?:لي|هولي)?\s+(?:ايه|انهي)|"
                      r"كان\s+ايه\s+(?:اللي|الي)\s+(?:رشحته|قلته|قولته)")


def _recalled_offer(message, conversation, store):
    """"نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟" answered from what was offered to that recipient.

    A fresh search offered Black XS L'Exces, which nobody had mentioned (simulation S15 round 2 full 3, t5).
    """
    from .static_faq_service import normalize_arabic
    if conversation is None or not _RECALL.search(normalize_arabic(message or "")):
        return None
    state = conversation.sales_state or {}
    names = (state.get("recipient_offers") or {}).get(state.get("recipient", "self"))
    if not names:
        return None
    from .price_reply import requested_product_details
    from .product_formatting import format_products
    from .product_info import _products_named
    products = _products_named(names, store)
    lines = requested_product_details(products, "", description_required=True)
    if not lines:
        return None
    return "كنت رشحتلك " + " و".join(p.name for p in products) + ":\n" + "\n".join(lines), format_products(products)


def _uncommon_note(response, products, intent):
    """For "حاجة مميزة ومش منتشرة", say which offered perfume is niche or the store's own, from the data.

    S05 round 2 full 3, t3: asked "إيه اللي يميز ترشيحك؟", the reply gave only scent labels, never that
    Alexandria II is niche — the one recorded fact that answers the request.
    """
    if not response or not (intent or {}).get("wants_uncommon") or _re.search(r"نيش|حصري|niche", response):
        return response
    from .sales.scent_matching import is_uncommon
    from .sales.value import is_store_exclusive
    by_name = {product.name: product for product in products}
    lines = []
    for name in sales_naming.names_in(response, list(by_name)):
        product = by_name[name]
        if is_store_exclusive(product) or product.perfume_type == "store_own_made":
            lines.append(f"{name} تركيب حصري بتاعنا.")
        elif is_uncommon(product):
            label = {"niche": "النيش", "ultra_niche": "الألترا نيش"}.get(product.perfume_type, "النيش")
            lines.append(f"{name} من فئة {label}.")
    return response + ("\n\n" + " ".join(lines) if lines else "")


_OTHER_THAN_PERFORMANCE = _re.compile(r"بكام|سعر|ريح|نوت|طابع|مكونات|متوفر|موجود|حجم|زجاج|شحن|توصيل|اصلي|تركيب|"
                                     r"رشح|ترشح")


def _store_performance_answer(message, store, normalized):
    """A performance question about no named perfume gets the store policy, which covers them all.

    "طب بيقعد قد إيه بجد؟" was answered "ثباتها ممتاز" without the hours the policy gives for an explicit
    duration question, then "محتاجة تأكيد… تحب أحولك؟" (simulation S16 round 2 full 4, t3-t4).
    """
    from .performance_policy import current_request, description
    from .product_info import _named_in_message
    performance = current_request()
    # "ثبات سوفاج قد ايه؟" (a name in Arabic letters) and "ثباته قد ايه؟" (its longevity) are about one
    # perfume, which the resolver places; only a question about none gets the store-wide answer.
    if not (performance.longevity or performance.projection) or _named_in_message(message, store) \
            or _OTHER_THAN_PERFORMANCE.search(normalized or "") or sales_naming.may_name_a_perfume(message) \
            or _re.search(r"(?:ثبات|فوحان|ريحت)(?:ه|ها|هم)(?!\w)", normalized or ""):
        return None
    text = description(performance, longevity=performance.longevity, projection=performance.projection)
    return "كل عطورنا " + text.replace("ثباته ", "ثباتها ").replace("فوحانه ", "فوحانها ") + "."


def _recently_denied(conversation, turns=4):
    """Whether one of our last replies told the customer a named perfume is not stocked."""
    if conversation is None:
        return False
    contexts = conversation.messages.filter(role="assistant", delivery_status="sent").order_by(
        "-created_at", "-pk").values_list("internal_context", flat=True)[:turns]
    return any("ABSENCE_DENIED" in (context or "") for context in contexts)


def _closer_to_a_missing_perfume(message, normalized, conversation, store):
    """The honest line for "أنهي واحد فيهم أقرب لـ Club de Nuit؟" about a perfume we do not stock.

    It got the two price blocks and nothing else, which the judge read as picking the first one
    without support (simulation S09 round 2 batch 4 attempt 3, t5). Nothing recorded compares our
    perfumes with one we hold no data for, so the reply says that before anything else.
    """
    if not store or not _re.search(r"اقرب|اشبه|شبه|قريب", normalized or ""):
        return ""
    # "أنهي واحد فيهم أقرب…" asks which; "الـCigar ده قريب من Club de Nuit؟" asks whether.
    which = _re.search(r"(?<!\w)(?:انهي|مين|اي\s+واحد)(?!\w)", normalized) or _CHOICE_AMONG_DISCUSSED.search(normalized)
    tail = "فمقدرش أقول أنهي أقرب له." if which else "فمقدرش أقول إذا كان قريب منه."
    from products.models import Product
    names = list(Product.objects.filter(store=store, is_active=True).values_list("name", flat=True))
    for run in _re.findall(r"[A-Za-z][A-Za-z'’&.-]*(?:\s+[A-Za-z][A-Za-z'’&.-]*)*", message or ""):
        words = [word for word in _re.findall(r"[a-z]+", run.lower())
                 if word not in sales_naming.LATIN_FILLER and len(word) >= 3]
        if words and not sales_naming.names_in(run, names) and _recently_denied(conversation, turns=8):
            return f"معرفش ريحة {run.strip()} بالظبط، {tail}"
    # "طب أنهي واحد فيهم أقربله؟" names nothing: after a denial, "له" is the perfume we do not stock
    # (simulation S09 round 3 verify, t4; the same reading as `reference._after_denial`).
    if (which and _re.search(r"(?<!\w)(?:اقرب|اشبه)\s*(?:له|ليه|لو)(?!\w)|(?<!\w)(?:اقربله|اشبهله)(?!\w)", normalized)
            and not _re.search(r"[A-Za-z]{3}", message or "") and _recently_denied(conversation, turns=8)):
        return f"معرفش ريحة العطر اللي سألت عنه بالظبط، {tail}"
    return ""


# The reply already says the missing perfume's scent is unknown: the line above, or the model's own
# words, including the wording used before 2026-11-20.
_SAYS_SCENT_UNKNOWN = _re.compile(r"معرفش\s+ريح|معنديش\s+بيانات")


_ASKS_ORIGINAL_BOTTLE = _re.compile(r"(?<!\w)(?:ال)?(?:اوريجنال|اوريجينال|اورجينال|اورجنال|اوريجنل)(?!\w)|\boriginal\b")


def _original_bottle_note(message, response, history):
    """One sentence, once per conversation, when a customer asks for the original bottle and gets its price.

    "فيه منهم زجاجة أورجينال؟" got "زجاجة اوريجنال 100 ملي بـ2400 جنيه." and nothing else, which can be
    read as an original perfume (simulation S05 round 3 verify, t5, medium). The store's own sentence
    says what differs. Not repeated: the owner's prompt asks for it not to ride on every price reply.
    """
    from .bottle_wording import BOTTLE_DIFFERENCE, ORIGINAL_BOTTLE
    from .static_faq_service import normalize_arabic
    if not response or ORIGINAL_BOTTLE not in response or "تركيب" in response \
            or not _ASKS_ORIGINAL_BOTTLE.search(normalize_arabic(message or "").lower()):
        return ""
    earlier = " ".join(row.get("content", "") for row in (history or []) if row.get("role") == "assistant")
    return "" if ("تركيب" in earlier or "التركيبة" in earlier) else BOTTLE_DIFFERENCE


def _not_a_repeat(fallback, message, history):
    """A fallback that says again what a recent reply already said is replaced, not resent.

    "طب هو قريب من Club de Nuit فعلًا؟" and then "خلاص هفكر شوية، تسلم" both got the same Ultra Male
    description back word for word (simulation S09 round 2, t6-t7). A closing message gets a short
    close; anything else gets one honest line instead of the same block.
    """
    recent = [row.get("content", "") for row in (history or [])[-6:] if row.get("role") == "assistant"]
    if not fallback or not any(SequenceMatcher(None, fallback, earlier).ratio() >= 0.9 for earlier in recent):
        return fallback
    from .sales.appreciation import closes_conversation, thanks_or_praise
    if closes_conversation(message) or (thanks_or_praise(message) and not _re.search(r"[؟?]", message or "")):
        return "تمام، خد وقتك، ولو احتجت حاجة أنا موجود."
    return "ده كل اللي أعرفه عن ده؛ أي تفاصيل أكتر محتاجة تأكيد من فريق خدمة العملاء."


def _label_difference(first, second):
    """What separates two perfumes, from their saved labels only."""
    from .scent_description import _PHRASES, selected_tags
    mine, theirs = selected_tags(first.scent_tags), selected_tags(second.scent_tags)
    only_first = [_PHRASES[tag] for tag in mine if tag not in theirs and tag in _PHRASES]
    only_second = [_PHRASES[tag] for tag in theirs if tag not in mine and tag in _PHRASES]
    if not only_first and not only_second:
        return "طابعهم متقارب جداً، فالفرق الأساسي بينهم في السعر والحجم."
    parts = []
    if only_first:
        parts.append(f"{first.name} " + " و".join(only_first))
    if only_second:
        parts.append(f"{second.name} " + " و".join(only_second))
    return "الفرق بينهم في الطابع: " + "، و".join(parts) + "."


def _pick_for_customer(first, second, conversation):
    """"اختارلي انت": one of the two, by the saved preferences, the first one shown on a tie.

    The fallback answered "خلاص اختارلي انت" with both descriptions and their difference, leaving the
    choice with an undecided customer again (simulation S06 round 2 full 3, t6).
    """
    from .scent_description import describe_scent
    preferences = (conversation.preferences or {}) if conversation is not None else {}
    wanted = set(preferences.get("scent_tags") or ())
    avoided = set(preferences.get("excluded_scent_tags") or ())

    def fit(product):
        tags = set(product.scent_tags or ())
        return len(tags & wanted) - len(tags & avoided)
    pick = second if fit(second) > fit(first) else first
    return f"لو هختارلك: {pick.name}؛ {describe_scent(pick.scent_tags, '')}."


def _thanked(message, reply, history):
    """Praise beside a question gets a short thanks before the answer, whatever wrote the answer.

    The persona asks the model to thank first, but a draft replaced by a fallback loses it: "تمام،
    ذوقك حلو. إيه اللي يميزه…؟" got the comparison and no word about the praise (simulation S05).
    """
    from .sales.appreciation import detect
    if not reply or not detect(message, history) or _re.search(r"تسلم|شكر|ميرسي|مبسوط|يسعدني|بيسعدني", reply):
        return reply
    return "تسلم يا فندم.\n\n" + reply


# "الشحن بياخد قد إيه؟" asks how long delivery takes; no keyword of the store's delivery-time FAQ
# matched it, and the bare "شحن" keyword answered the fee instead (simulation S14 round 2, t5).
_DELIVERY_TIME = _re.compile(
    r"(?:شحن|توصيل|يوصل|بيوصل|هيوصل|الاوردر)[^؟?\n]*(?:قد ايه|اد ايه|كام يوم|كام ايام|امتي|مده|بياخد وقت)"
    r"|(?:قد ايه|اد ايه|كام يوم|كام ايام)[^؟?\n]*(?:شحن|توصيل|يوصل)")
# A shipping question in Arabic, Franco or English. The special cases are left to the model, which
# has the whole business facts: same-day delivery, free shipping, a rush.
_SHIPPING_TOPIC = _re.compile(
    r"شحن|توصيل|(?<![a-z0-9])(?:sha7n|sha7en|shahn|sh7n|shipping|delivery|tawsil|tawseel|taw9il)(?![a-z0-9])")
_SHIPPING_SPECIAL = _re.compile(r"نفس\s+اليوم|النهارده|انهارده|بكره|مجان|اوبر|مستعجل|سريع|free|uber|same\s+day|today")
_GREETING_FRAGMENT = _re.compile(r"^\W*و?(?:السلام|سلام|ازيك|اهلا|هاي|مرحبا|صباح|مساء|hi|hello)", _re.I)
# "ينفع أبدلها؟", "لو مجاش على ذوقها أعمل إيه؟": the exchange policy, which a model paraphrase got
# wrong ("لو العطر زجاجة ستور خاصة، ينفع استبداله", simulation S07 round 2, t5). Asked as a question,
# so a cart swap ("عايز ابدل السوفاج بالبلو") is not read as one.
_EXCHANGE_QUESTION = _re.compile(
    r"(?:ينفع|ممكن|هل|اقدر|نقدر)\s+(?:\S+\s+){0,2}(?:ا|ن|ي|ت)?(?:بدل|رجع|استبدل|ترجع)\w*"
    r"|مج(?:ا|ت)ش\s+علي\s+ذوق|مطلع(?:ت)?ش\s+علي\s+ذوق|مع?جب(?:ها|هو|ني|ناش)ش|معجبتهاش"
    # The policy by its own names: "والضمان ده ينفع لو فتحته وجربته؟" went to the model, which invented
    # "لو فتحتيه وجربتيه، مش هينفع استبداله أو استرجاعه" (simulation S07 round 3 full run, t4, critical).
    # "تضمن" / "مضمون" are about a promise, not the policy, and do not match.
    r"|(?<!\w)(?:[وبل]?ال|لل|[وبل])?(?:ضمان|استبدال|استرجاع|مرتجع)(?!\w)")


def _strict_faq_match(text, store):
    """`match_static_faq` with whole-word keywords, for one question cut out of a longer message.

    A short fragment lets single keywords match, and they match as substrings: "إيه الفرق اللي
    بيدفعني أشتريه" hit the payment FAQ through the "دفع" inside "بيدفعني" (simulation S16 round 2 full
    3, t2). Each keyword part must stand as a word, with at most an article or و/ب/ل in front.
    """
    from products.models import StaticFAQ
    from .static_faq_service import _matched_reply, normalize_arabic
    if not store:
        return None
    normalized = normalize_arabic(text or "")
    for faq in StaticFAQ.objects.filter(store=store, is_active=True).order_by("-priority", "id"):
        for keyword in (normalize_arabic(k.strip()) for k in (faq.keywords or "").split(",") if k.strip()):
            parts = [part.strip() for part in keyword.split("+") if part.strip()]
            if parts and all(_re.search(r"(?<!\w)(?:[وبل]?ال|لل|[وبل])?" + _re.escape(part) + r"(?!\w)", normalized)
                             for part in parts):
                return _matched_reply(faq, text)
    return None


def _faq_lookup(text, store, strict=False):
    """The store FAQ entry for one question, with delivery-time and exchange paraphrases the
    keywords miss."""
    from .static_faq_service import match_static_faq, normalize_arabic
    normalized = normalize_arabic(text or "")
    # One question only: "أنهي فيهم أخف؟ ولو مجتش على ذوقها أعمل إيه؟" must still reach the comparison;
    # `_faq_answers` looks up each question of a longer message on its own.
    single = len([part for part in _re.split(r"[؟?\n]+", normalized) if part.strip()]) <= 1
    for pattern, canonical in ((_DELIVERY_TIME, "مدة التوصيل"), (_EXCHANGE_QUESTION, "استبدال")):
        if single and pattern.search(normalized):
            found = match_static_faq(canonical, store)
            if found and found.get("answer"):
                return found
    found = _strict_faq_match(text, store) if strict else match_static_faq(text, store)
    if not found and single and _SHIPPING_TOPIC.search(normalized) and not _SHIPPING_SPECIAL.search(normalized):
        # No FAQ entry answers it: the owner's own shipping sentence in the business facts does, when
        # the store keeps its terms there ("الشحن… في خلال من يومين ل 5 ايام عمل ب 60 جنيه لاي مكان في
        # مصر"). Any plain shipping question, as the FAQ entry's bare "شحن" keyword used to answer:
        # with that entry off, "الشحن بيوصل اسكندريه؟" and "el sha7n b kam?" went to the model, whose
        # draft failed the price check, and the reply was "تقصد أنهي عطر وحجم؟" (simulation S12 t3 and
        # S13 t4, round 3 full run). A delivery-time question needs a sentence that states a time.
        from .order_service import shipping_fact
        fact = shipping_fact(store)
        if fact and (not _DELIVERY_TIME.search(normalized) or _re.search(r"يوم|ايام|ساع|اسبوع", normalize_arabic(fact))):
            return {"answer": fact if fact.endswith((".", "!", "؟")) else fact + "."}
    return found


# What else a message may ask or request beside a store-policy question. Without one of these, the
# rest is a remark ("هفكر أطلبه إن شاء الله") and the policy answer is the whole reply.
_ASKS_SOMETHING_ELSE = _re.compile(
    r"(?<!\w)(?:بكام|كام|سعر\w*|ايه|ازاي|فين|امتي|ليه|مين|انهي|هل|عندك|عندكم|عايز\w*|عاوز\w*|محتاج\w*|"
    r"رشح\w*|ترشح\w*|ترشيح\w*|قارن\w*|قولي|وريني|ابعت\w*|اختار\w*)(?!\w)"
    r"|(?:في|فيه)\s+حاجه|الفرق|احسن|انسب|ريحت\w*|نوتات|ثبات\w*|فوحان\w*")


_FIXED_PRICE_QUESTION = _re.compile(
    r"(?:السعر|سعر\w*|التمن)\s+(?:\S+\s+)?ثابت|(?:يطلع|هيطلع|فيه|في|هيبقي|هيكون)\s+(?:\S+\s+)?زياد\w*|"
    r"هيزيد|بيزيد|مصاريف\s+(?:تانيه|زياده|اضافيه|خفيه)|رسوم\s+(?:تانيه|زياده|اضافيه)")


def _fixed_price_note(message, store):
    """"لو السعر ثابت قولّي بصراحة… مش عايز يطلع فيه زيادة" answered from the store's own facts.

    It was read as a longevity question and got "ثباته ممتاز" twice, with the concern itself never
    answered (simulation S11 round 2 full 5, t3-t4). The order is charged the catalogue price; the
    only addition the store states is shipping.
    """
    from .static_faq_service import normalize_arabic
    if not _FIXED_PRICE_QUESTION.search(normalize_arabic(message or "")):
        return ""
    from .order_service import flat_shipping_fee, _free_shipping_threshold
    from .product_formatting import money
    fee = flat_shipping_fee(store) if store else None
    if fee is None:
        return "السعر ده ثابت زي ما هو، والزيادة الوحيدة عليه هي مصاريف الشحن."
    threshold = _free_shipping_threshold(store)
    free = f"، والشحن مجاني لأي أوردر فوق {money(threshold)} جنيه" if threshold is not None else ""
    return f"السعر ده ثابت زي ما هو، ومفيش أي زيادة عليه غير مصاريف الشحن {money(fee)} جنيه{free}."


_SKIN_OR_CLOTHES = _re.compile(r"(?<!\w)(?:علي|ع)\s*(?:ال)?(?:جلد|بشره|هدوم|لبس|ملابس)(?!\w)")
# Worded without "الثبات": the performance-policy enforcer rewrites any clause that carries it.
SKIN_OR_CLOTHES_ANSWER = "مقدرش أحددلك إذا كانت المدة دي على الجلد ولا على الهدوم."
SKIN_OR_CLOTHES_AGAIN = ("زي ما قلتلك، مقدرش أحدد ده. لو مهم تتأكد منه قبل ما تقرر، "
                         "تحب أحولك لفريق خدمة العملاء؟")


def _skin_or_clothes_answer(normalized, history):
    """"الثبات ده على الجلد ولا الهدوم؟": the store's performance policy does not say which.

    It got "اكتبلي اسم العطر تاني… عشان أتأكد من المقصود" and then "ده كل اللي متسجل عندي عن ده"
    (simulation S16 round 2 full 6, t4-t5; question quality 4). The honest answer is that it is not
    recorded; asked again, the team is offered instead of the same sentence.
    """
    from .static_faq_service import normalize_arabic
    if not _SKIN_OR_CLOTHES.search(normalized or ""):
        return ""
    replies = [row.get("content", "") for row in (history or []) if row.get("role") == "assistant"]
    about_longevity = _re.search(r"ثبات|ثابت|يثبت|يقعد|ساع", normalized) or (
        replies and _re.search(r"ثبات|ساعات", normalize_arabic(replies[-1])))
    if not about_longevity:
        return ""
    return SKIN_OR_CLOTHES_AGAIN if any(SKIN_OR_CLOTHES_ANSWER in reply for reply in replies[-3:]) \
        else SKIN_OR_CLOTHES_ANSWER


_RETYPE_THE_NAME = _re.compile(
    r"(?:[اأ]كتب\w*|قول\w*|[اإ]بعت\w*)\s+(?:لي\s+)?(?:[اإ]سم\s+العطر|[اإ]سمه|ال[اإ]سم)\s+(?:تاني|مر[ةه]\s+تاني[ةه])")


def _without_retype_request(response, message, context):
    """Drop "اكتبلي اسمه تاني" when the customer never named a perfume.

    "وبيفضل ثابت قد إيه بجد؟" got the store's answer and then "بس عشان أجاوبك عن عطر بعينه اكتبلي
    اسمه تاني يا فندم" (simulation S16 round 2 full 5, t3; again in full 6, t4): "تاني" presumes a
    name that was never given. A turn on which a name could not be read (its context says so), or a
    message in Latin letters or one that says "اسمه", keeps the request.
    """
    if not response or not _RETYPE_THE_NAME.search(response) or _re.search(r"[A-Za-z]{3}|[اإ]سمه", message or "") \
            or any(marker in (context or "") for marker in ("NAME_UNREADABLE", "ABSENCE_DENIED", "PENDING_LOOKUP")):
        return response
    kept = []
    for sentence in _re.split(r"(?<=[.!؟?])\s+|\n+", response):
        ask = _RETYPE_THE_NAME.search(sentence)
        if not ask:
            kept.append(sentence)
            continue
        comma = max(sentence.rfind("،", 0, ask.start()), sentence.rfind(",", 0, ask.start()))
        head = sentence[:comma].strip() if comma > 0 else ""
        if len(head.split()) >= 3:
            kept.append(head + ".")
    return " ".join(kept).strip() or response


_STRENGTH_CHOICE = _re.compile(
    r"(?:اخف|خفيف\w*|هادي\w*|ناعم\w*|رقيق\w*)\s+(?:\S+\s+)?ولا\s+(?:اقوي|اتقل|اجمد|تقيل\w*|قوي\w*)"
    r"|(?:اقوي|اتقل|اجمد|تقيل\w*|قوي\w*)\s+(?:\S+\s+)?ولا\s+(?:اخف|خفيف\w*|هادي\w*|ناعم\w*)")


def _without_answered_strength_question(response, preferences):
    """Drop a "أخف ولا أقوى؟" question once the customer has said how strong she wants it.

    "بحب الحاجات الرقيقة اللي فيها ورد ومش تقيلة" was asked "تحبي الحاجة أخف ولا أقوى شوية؟" two turns
    later (simulation S06 round 2 full 5, t5).
    """
    from .static_faq_service import normalize_arabic
    prefs = preferences or {}
    stated = ({"light", "heavy"} & (set(prefs.get("scent_tags") or ()) | set(prefs.get("excluded_scent_tags") or ())
                                     | set(prefs.get("avoid_traits") or ())))
    if not stated or not response:
        return response
    kept = _re.sub(r"[^.!؟?\n]*[؟?]",
                   lambda m: "" if _STRENGTH_CHOICE.search(normalize_arabic(m.group())) else m.group(), response)
    kept = _re.sub(r"[ \t]+\n", "\n", kept).strip()
    return kept or response


def _is_exchange_question(normalized):
    """An exchange-policy question, not a return to an earlier option or a size swap.

    "ينفع نرجع للأولاني؟" goes back to a perfume and "ينفع أبدل الـ50 بالـ90؟" swaps a size; neither asks
    what the store does with a bottle that did not suit.
    """
    return bool(_EXCHANGE_QUESTION.search(normalized)) and not _re.search(
        r"(?:رجع|ترجع)\w*\s+(?:ل(?!و(?:\s|$))\S|علي\s)|بدل\w*\s+(?:\S+\s+){0,3}ب(?:ال|ـ)?\S", normalized)


# How the order is paid for. Whole words: "اللي بيدفعني أشتريه" is not a payment question.
_PAYMENT_QUESTION = _re.compile(
    r"(?<!\w)(?:[وبل]?ال|لل|[وبل])?(?:دفع|كاش|فيزا|تقسيط|انستاباي|instapay|cash|visa)(?!\w)"
    r"|(?<!\w)(?:ا|ه|ن|ت|ي)دفع\w*\s+(?:ازاي|كاش|عند|لما|وقت|بعد|قبل)")


def _policy_answer(answer, history):
    """The store's policy answer, shortened when it already went out in full.

    The exchange policy asked about again got the whole text again, and an identical reply after
    that (simulation S07 round 2 full 5, t3-t7). A repeat gets the exchange lines alone, quoted
    as written; a third ask, the first of them.
    """
    from .static_faq_service import normalize_arabic
    recent = [row.get("content", "") for row in (history or [])[-8:] if row.get("role") == "assistant"]
    if not any(answer in earlier for earlier in recent):
        return answer
    lines = [line.strip() for line in answer.splitlines()
             if _re.search(r"استبدال|استرجاع", normalize_arabic(line)) and not line.strip().endswith(":")]
    for brief in (("زي ما قلتلك:\n" + "\n".join(lines)) if lines else "",
                  ("زي ما قلتلك، " + lines[0]) if lines else ""):
        if brief and not any(brief in earlier for earlier in recent):
            return brief
    return answer


def _policy_side_questions(message, store, history=None):
    """(store answers, the rest of the message, whether the rest asks anything) for exchange and
    delivery questions.

    "طب هتعجبها أكيد؟ ولو مجتش على ذوقها أعمل إيه؟" sent the exchange half to the model, which narrowed
    a condition of the policy into a rule for every bottle (simulation S07 round 2 batch 3, t3). The
    store's own answer is given verbatim; the model answers the rest.

    Split by clause, not only by "؟": "تمام، هفكر في الـ50 ملي وأطلبه إن شاء الله، بس ينفع أبدله لو
    مجاش على ذوقها؟" is one question mark, went to the price branch whole, and got the 50 ml price
    twice with the exchange question unanswered (simulation S07 round 2 full 5, t5-t7). The other
    clauses of a policy sentence ("والميزانية لحد 1000") stay in the rest.
    """
    from .static_faq_service import normalize_arabic
    sentences = [part.strip() for part in _re.split(r"(?<=[؟?])\s*|(?<=[.!])\s+|\n+", message or "") if part.strip()]
    answers, rest, asks = [], [], False
    for sentence in sentences:
        clauses = [clause.strip() for clause in _re.split(r"[،,]+|\s+(?=(?:بس|لكن)\s)", sentence) if clause.strip()]
        kept = []
        for index, clause in enumerate(clauses):
            normalized = normalize_arabic(clause)
            found = (_faq_lookup(clause, store)
                     if _is_exchange_question(normalized) or _DELIVERY_TIME.search(normalized) else None)
            if found is None and _PAYMENT_QUESTION.search(normalized):
                # "…محتار بينه وبين Pure XS. الدفع بيكون عند الاستلام؟": the store's payment answer, as
                # written. Left to the model beside a hesitation, both drafts failed the claim check and
                # the reply dropped the question (simulation S14 round 3 verify, t9).
                found = _strict_faq_match(clause, store)
            answer = ((found or {}).get("answer") or "").strip()
            if answer:
                if answer not in answers:
                    answers.append(answer)
                continue
            kept.append(clause)
            if _ASKS_SOMETHING_ELSE.search(normalized) or (
                    index == len(clauses) - 1 and _re.search(r"[؟?]\s*$", sentence)):
                asks = True
        if kept:
            rest.append("، ".join(kept))
    if not answers:
        return [], message, True
    return [_policy_answer(answer, history) for answer in answers], " ".join(rest), asks


def _faq_answers(message, store, whole):
    """Answer every store question in the message, in the order asked.

    "طب الشحن بياخد قد إيه؟ والدفع عند الاستلام متاح؟" got the payment answer alone: one keyword
    match ended the turn and the delivery-time question went unanswered (simulation S14 round 2, t5).
    """
    fragments = [part.strip() for part in _re.split(r"[؟?\n]+", message or "") if part.strip()]
    if len(fragments) < 2:
        return whole
    answers = []
    for fragment in fragments:
        if _GREETING_FRAGMENT.search(fragment):
            continue
        answer = ((_faq_lookup(fragment, store, strict=True) or {}).get("answer") or "").strip()
        if answer and all(answer not in earlier for earlier in answers):
            answers.append(answer)
    if whole and all(whole.strip() not in earlier for earlier in answers):
        answers.append(whole.strip())
    return "\n\n".join(answers) if answers else whole


def _with_side_answers(message, reply, history, store, conversation):
    """Answer a shipping/hours question asked alongside a checkout step, after the order reply."""
    import hashlib
    import re
    from .static_faq_service import match_static_faq
    from products.models import Cart
    from .order_service import amount_with_shipping, shipping_answer
    order_reply = reply
    for fragment in re.split(r"[.؟?\n]", message or ""):
        if re.search(r"شحن|توصيل|بتوصل|بتفتح|مواعيد", fragment) and not re.search(r"\d{7}", fragment):
            # "الإجمالي 810 بالشحن صح؟" is about this order's sum, which only the checkout knows.
            total = amount_with_shipping(conversation, fragment)
            if total:
                if total not in reply:
                    reply += "\n\n" + total
                continue
            # A fee question ("والشحن كده 60؟") is the store's shipping answer. Sent to the general
            # model as a bare fragment, it never saw the details given in the same message and asked
            # for the name, phone and address again under the summary (simulation S11 final t4).
            fee = shipping_answer(store) if re.search(r"شحن|توصيل", fragment) and re.search(
                r"\d|بكام|كام|مصاريف|سعر|فلوس", fragment) else ""
            if fee:
                if fee not in reply:
                    reply += "\n\n" + fee
                continue
            faq = _faq_lookup(fragment.strip(), store)
            if faq and "answer" in faq:
                if faq["answer"] not in reply:
                    reply += "\n\n" + faq["answer"]
            else:
                answer, _ = handle_general(fragment.strip(), history, store)
                reply += "\n\n" + answer
    if reply != order_reply and conversation is not None:
        cart = Cart.objects.filter(conversation=conversation).first()
        if cart and cart.quote.get("summary_hash") == hashlib.sha256(order_reply.encode()).hexdigest():
            cart.quote = {**cart.quote, "summary_hash": hashlib.sha256(reply.encode()).hexdigest()}
            cart.save(update_fields=["quote"])
    return reply


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
    """Apply the performance policy to every route, including scripted replies."""
    token = _TURN_CONVERSATION.set(conversation)
    try:
        return _route_with_policy(message, history, store, conversation)
    finally:
        _TURN_CONVERSATION.reset(token)


NEAR_BUDGET = "أعلى حاجة بسيطة من ميزانيتك"
_NEAR_BUDGET_ON_A_PRICE = _re.compile(
    r"(بـ?\s*(\d[\d,]*)\s*جنيه)(?:\.\s*" + NEAR_BUDGET + r"|\s*\(" + NEAR_BUDGET + r"\))")


def _near_budget_once(reply, message, history):
    """Say "أعلى حاجة بسيطة من ميزانيتك" once for a price, not on every reply that quotes it.

    One 750 bottle against a 700 budget carried the sentence on four consecutive replies, and twice
    inside one of them (simulation S11, round 2 full 5 and 6: "repeating it across several
    consecutive replies sounds scripted"). The first quote of a price keeps it, on the price itself;
    later quotes of the same price, and a second copy in the model's prose, lose it. A message that
    asks about the budget keeps it: there the sentence is the answer. Only prices within the
    near-budget band ever carry the label, so nothing over budget goes unsaid.
    """
    from .product_formatting import FITS_BUDGET_QUESTION
    from .static_faq_service import normalize_arabic
    if NEAR_BUDGET not in (reply or ""):
        return reply
    asked = normalize_arabic(message or "")
    if _re.search(r"ميزاني|غالي|\bbudget\b", asked) or FITS_BUDGET_QUESTION.search(sales_naming.normalize_product_name(message or "")):
        earlier = []
    else:
        earlier = [row.get("content", "") for row in (history or [])[-6:] if row.get("role") == "assistant"]
    told = {match.group(2).replace(",", "") for text in earlier for match in _NEAR_BUDGET_ON_A_PRICE.finditer(text)}
    kept_one = False

    def on_price(match):
        nonlocal kept_one
        if match.group(2).replace(",", "") in told:
            return match.group(1)
        kept_one = True
        return match.group(0)

    cleaned = _NEAR_BUDGET_ON_A_PRICE.sub(on_price, reply)
    if kept_one or told:
        # A copy in the model's own sentence beside the labelled price, or after it was already said.
        lines = []
        for line in cleaned.split("\n"):
            if NEAR_BUDGET in line and not _NEAR_BUDGET_ON_A_PRICE.search(line):
                sentences = []
                for sentence in _re.split(r"(?<=[.!؟?])\s+", line):
                    if NEAR_BUDGET not in sentence:
                        sentences.append(sentence)
                        continue
                    comma = sentence.rfind("،", 0, sentence.find(NEAR_BUDGET))
                    if comma > 0 and len(sentence[:comma].split()) >= 2:
                        sentences.append(sentence[:comma].rstrip() + ".")
                line = " ".join(sentences)
                if not line.strip():
                    continue
            lines.append(line)
        cleaned = "\n".join(lines)
    return cleaned if cleaned.strip() else reply


# Asking for the source, not complaining that none was given: "من غير مصدر" beside a price question
# ("لسه بتكرر نفس النوتات من غير مصدر… الـ90 بكام؟") does not ask for it again.
_NOTES_SOURCE_QUESTION = _re.compile(
    r"(?<!\w)(?:عندك|عندكم|فين|ايه|هو|في|فيه)\s+(?:\S+\s+)?(?:ال)?مصدر|مصدر(?:ه|ها|هم)?\s+(?:ايه|فين|منين)|"
    r"من\s+مصدر\s+رسمي|مش\s+مصدر\w*|جبت(?:ه|ها|هم|يه|يها)?\s+منين|منين\s+(?:\S+\s+)?(?:ال)?(?:نوت|معلوم)|"
    # "إنت متأكد من المصدر؟" (S02 round 3 replay 3, t5): asked about the source, got the list again.
    r"متاكد\w*\s+من\s+(?:ال)?مصدر|معتمد\w*\s+علي|قايمه\s+ايه|"
    # "المعلومة دي من أنهي إصدار؟" (S02 round 3 verify, t4): the same question, got the list again.
    r"(?:المعلومه|المعلومات|النوتات|القايمه|الليسته)\s+(?:دي\s+)?من\s+(?:انهي|اي)(?!\w)|"
    # "عندكم Layton notes موثقة أكتر ولا نفس الحكاية؟", "والنوتات دي برضه من بياناتكم بس؟" (S02 round 4
    # replay, t7-t8): asked whether the notes are documented, got the list.
    r"(?<!\w)موثق\w*|(?:نوتات|notes)\s+(?:\S+\s+){0,2}?من\s+بيانات(?:كم|ك|كو)(?!\w)")
# Worded by the owner (2026-11-20): honest about having no official source, with no "recorded in
# our data".
NOTES_SOURCE = "دي النوتات اللي إحنا شغالين بيها للعطر ده، ومعنديش مصدر رسمي من البراند ليها."
NOTES_SOURCE_AGAIN = "زي ما قلتلك، دي النوتات اللي إحنا شغالين بيها، ومعنديش مصدر تاني ليها."


def _notes_source_note(normalized, history):
    """The honest answer to "جبتها منين؟" / "عندك مصدر للنوتات دي؟": the notes are our recorded data.

    Asked three times where the notes came from, the bot repeated the list twice and then said it had
    no source while presenting the same list as recorded (simulation S02 round 3 replay 2, t3-t5; the
    same gap in round 2 full 5, t7). Said once; a repeat gets the short form, then nothing more.
    """
    if not _NOTES_SOURCE_QUESTION.search(normalized or "") or not _re.search(r"نوت|notes?|مكونات|تركيب", normalized or ""):
        return ""
    said = "\n".join(row.get("content", "") for row in (history or [])[-6:] if row.get("role") == "assistant")
    if NOTES_SOURCE not in said:
        return NOTES_SOURCE
    return "" if NOTES_SOURCE_AGAIN in said else NOTES_SOURCE_AGAIN


_DRAFTING_LEAK = _re.compile(
    r"[^.!؟?\n]*(?:هيتضاف|هتتضاف|هيضاف|هتضاف|هينضاف|هيتكتب|هتتكتب)\s+(?:\S+\s+){0,2}(?:تلقائي|[اأ]وتوماتيك|لوحد)[^.!؟?\n]*[.!؟?]?")


def _without_drafting_leak(reply):
    """Drop a sentence that repeats our own drafting instruction to the customer.

    The quote prompt tells the model "الأسعار والأحجام هتتضاف تلقائياً بعد ردك"; it wrote "سعر الـ90 ملي
    هيتضاف تلقائيًا." into the reply, above the price itself (simulation S02 round 3 full run, t7,
    critical).
    """
    if not reply or not _DRAFTING_LEAK.search(reply):
        return reply
    lines = []
    for line in reply.split("\n"):
        stripped = _DRAFTING_LEAK.sub("", line).strip()
        if stripped or not line.strip():
            lines.append(stripped if stripped != line.strip() else line)
    return "\n".join(lines).strip() or reply


def _route_with_policy(message, history, store, conversation):
    with performance_turn(message, history):
        reply, context = _route(message, history, store, conversation)
        cleaned = enforce_reply(reply, context=context)
        cleaned = _near_budget_once(cleaned, message, history)
        cleaned = _without_drafting_leak(cleaned)
        if cleaned != reply and conversation is not None:
            # Checkout binds confirmation to the exact reply saved and delivered.
            import hashlib
            from products.models import Cart
            cart = Cart.objects.filter(conversation=conversation).first()
            if cart and cart.quote.get("summary_hash") == hashlib.sha256(reply.encode()).hexdigest():
                cart.quote = {**cart.quote, "summary_hash": hashlib.sha256(cleaned.encode()).hexdigest()}
                cart.save(update_fields=["quote"])
        return cleaned, context


def _route(message, history=None, store=None, conversation=None):
    if history is None:
        history = []

    # An answer to our transfer offer takes priority over checkout and FAQs.
    # In 1122 the classifier sent "ماشي" to order before promotion could see it.
    offer_history = history
    if conversation and has_handoff_offer(history):
        # LLM history omits staff messages. After staff resolve a conversation,
        # their last question must not expose an older bot offer as still open.
        latest_reply = conversation.messages.filter(
            role__in=("assistant", "agent"), delivery_status="sent"
        ).order_by("-created_at", "-id").first()
        if latest_reply and latest_reply.role == "agent":
            offer_history = []
    handoff_reply = handoff_offer_reply(message, offer_history)
    if handoff_reply is True:
        return _handoff(message, history, store, conversation)
    from .handoff import walks_away
    if handoff_reply is False and walks_away(message) and has_handoff_offer(offer_history) \
            and not requests_human(message):
        # "ماشي خلاص، مش هطلب حاجة. سيبك" after our transfer offer: the customer is leaving. A short
        # close, before the offers flow can answer the declined transfer with another sales line
        # (simulation S20 round 2 full 6, t5).
        return "تمام يا فندم، ولو احتجت أي حاجة أنا موجود.", ""

    # Offers must be checked before phone/address edits, FAQs, catalogue lookup,
    # and price objections. Those paths cannot fulfill promotional purchases.
    promotion_history = promotion_service.context_history(history, conversation)
    promotion_pending = promotion_service.has_promotion_context(promotion_history)
    promotion_facts = promotion_service.configured_promotions(store)
    promotion_request = promotion_service.blocks_checkout(message, promotion_history, store)
    regular_purchase = promotion_service.ordinary_purchase(message)
    if requests_human(message) and (promotion_pending or promotion_request):
        return _handoff(message, history, store, conversation)
    if promotion_request or (promotion_pending and not regular_purchase
                             and promotion_service.declined_transfer(message, promotion_history)
                             and not promotion_service.information_requested(message)):
        from .sales.appreciation import closes_conversation
        if not promotion_request and closes_conversation(message):
            # "لا مش دلوقتي… تمام شكراً" declined the transfer and closed; the offer script went
            # out again regardless (simulation S18 t5).
            return "تمام، ولو احتجت حاجة أنا موجود.", ""
        return promotion_service.reply(message, promotion_history, store, conversation=conversation)

    # Classify ambiguous offer names/context before the active-cart shortcut.
    # Reuse the result below and bill a message once, regardless of the path.
    request_type = None
    if not regular_purchase and not promotion_service.cancelling_order(message) and (promotion_pending or promotion_facts):
        record_llm_message(store)
        request_type = classify(message, promotion_history, promotion_context=promotion_facts)
        if request_type == "promotion" and not promotion_pending and _count_within_total(message, conversation) \
                and not promotion_service.blocks_checkout(message, promotion_history, store):
            # "طب لو عايز 3 بنفس الـ1600 ينفع؟" names no offer; read against "اشتري برفانين وخد التالت
            # هدية" it got the offer script twice and never the arithmetic (simulation S03 round 2
            # batch 3, t3-t4). A count within a combined budget is the totals flow's question.
            request_type = "recommendation"
        if request_type == "promotion" or (request_type == "order" and promotion_pending):
            return promotion_service.reply(message, promotion_history, store, conversation=conversation, usage_recorded=True)
        if request_type == "handoff" and handoff_reply is not False and has_handoff_offer(offer_history):
            return _handoff(message, history, store, conversation)

    # --- Static FAQ check (before AI — saves tokens) ---
    from .static_faq_service import match_static_faq, normalize_arabic
    import re
    normalized = normalize_arabic(message)
    gender_followup = sales_gender.pending_answer(message, history, conversation, store)
    if gender_followup and not sales_gender.question_pending(history) and conversation:
        latest = conversation.messages.filter(role="assistant", delivery_status="sent").order_by("-created_at", "-pk").first()
        if latest:
            history = [*history, {"role": "assistant", "content": latest.content}]
    accepted_prices = accepts_price_offer(message, history, conversation)
    original = sales_constraints.original_liquid_update(message)
    if original is not None or sales_constraints.explicit_updates(message).get("reset_preferences"):
        merge_preferences(conversation, sales_constraints.explicit_updates(message), message)
    requires_original = original if original is not None else bool(
        conversation and (conversation.preferences or {}).get("requires_original_liquid")
    )
    if original is True and not _wants_a_human(message):
        return sales_constraints.formulation_reply(), "ORIGINAL_LIQUID_REQUIRED"
    if "نوع السائل" in normalized or "مصنع" in normalized:
        formulation = match_static_faq("العطور اصليه ولا تركيب", store)
        explanation = (formulation or {}).get("answer", "شكل الزجاجة واسم العطر لوحدهم مش دليل على مين صنع السائل.")
        return explanation + "\n\nاسم مصنع الزيت تحديدًا محتاج تأكيد من الفريق.", ""
    price_range = _store_price_range(message, store, conversation)
    if price_range:
        return price_range, ""
    faq_match = _faq_lookup(message, store)
    from .sales.appreciation import closes_conversation
    if faq_match and closes_conversation(message) and not re.search(r"[؟?]", message or ""):
        # "كده النوتات دي للتركيبة عندكم… هفكر وأقارن شوية، شكراً" restates what it understood and
        # closes; the "تركيب" keyword sent the whole authenticity pitch to a customer leaving
        # (simulation S02 round 2 full 5, t8). The closing branch answers it.
        faq_match = None
    if faq_match and faq_match.get("promotion"):
        if (not regular_purchase and not promotion_service.cancelling_order(message)
                and not requests_human(message) and request_type != "handoff"):
            return promotion_service.reply(message, promotion_history, store, conversation=conversation, usage_recorded=request_type is not None)
        faq_match = None
    if faq_match and faq_match.get("answer") and any(
            entry.get("role") == "assistant" and faq_match["answer"].strip() in (entry.get("content") or "")
            for entry in history):
        # A keyword hit is not proof the customer wants the same canned text again: "إزاي ده بـ1000
        # والأصلي بآلاف؟" got the authenticity answer verbatim twice (S16 t2/t4). The model has the
        # FAQ facts in its prompt and answers the actual question instead.
        faq_match = None
    from products.models import Cart
    active_cart = Cart.objects.filter(conversation=conversation).first() if conversation else None
    if active_cart is not None and request_type not in ("handoff", "promotion", "order_cancel"):
        from .sales import second_gender
        if second_gender.decline(conversation, message):
            # "لا خلاص" to "تمام، نكمل بالحريمي؟": the order carries on from the step it was offered at.
            if request_type is None:
                record_llm_message(store)
            reply, context = handle_order(message, history, store, conversation)
            return _with_side_answers(message, reply, history, store, conversation), context
    from .objection_service import is_price_dispute, handle_price_dispute
    cancelling = bool(re.search(r"الغ[ياء]|الغي|كنسل|مش عايز (?:الطلب|الاوردر)|cancel", normalized))
    if is_price_dispute(message) and not cancelling and not _wants_a_human(message):
        return handle_price_dispute(message, store, conversation)
    checkout_update = active_cart and re.search(
        r"\d[\d\s-]{6,}\d|اسمي|عنواني|موبايلي|الرقم البديل|خلي|غيّر|غير الكميه|ضيف|شيل", message)
    if checkout_update and request_type in (None, "order") and not gender_followup:
        if request_type is None:
            record_llm_message(store)
        reply, context = handle_order(message, history, store, conversation)
        return _with_side_answers(message, reply, history, store, conversation), context
    if faq_match and request_type in (None, "faq", "greeting") and not accepted_prices and not gender_followup:
        answer = _faq_answers(message, store, faq_match["answer"])
        # "والميزانية خليها لحد 1500. والتوصيل بياخد قد إيه؟" got the delivery answer and lost the new
        # budget: this early return skipped the stated-preference merge below (simulation S14 round 2
        # batch 2, t6).
        stated = sales_constraints.stated_preference_updates(message) if conversation is not None else {}
        if stated:
            merge_preferences(conversation, stated, None, history=history, store=store)
            if stated.get("max_price"):
                from .product_formatting import money
                answer = f"تمام، الميزانية بقت لحد {money(stated['max_price'])} جنيه.\n\n" + answer
        return answer, ""

    if conversation is not None and request_type in (None, "order", "faq", "product_info") and not gender_followup:
        # "total 810 صح؟" with nothing in the cart: the quoted size and the shipping fee answer it.
        from .order_service import quoted_total_answer
        quoted_total = quoted_total_answer(conversation, message)
        if quoted_total:
            return quoted_total, ""

    # --- Goodbye loop detection ---
    if _is_goodbye_loop(history):
        goodbye_words = ["سلام", "باي", "مع السلامة", "bye", "شكرا"]
        msg_clean = message.strip()
        if any(msg_clean == gw or msg_clean.startswith(gw) for gw in goodbye_words) and len(msg_clean) < 30:
            return "نورتنا يا فندم، يومك جميل.", ""

    # --- Classify the request ---
    # Intent extraction is deliberately NOT started here. It is only needed by the
    # recommendation branch below, and running it up-front cost one wasted LLM call
    # on every other message. Firing it in a ThreadPoolExecutor did not help: the
    # `with` block exits via shutdown(wait=True), so it blocked on both calls
    # anyway.
    #
    # Ordinary FAQs can return before billing. When promotion context required
    # early classification, reuse that result without counting the message twice.
    if request_type is None:
        record_llm_message(store)
        request_type = "recommendation" if gender_followup else classify(message, history, promotion_context=promotion_facts)
    if gender_followup:
        request_type = "recommendation"
    if request_type == "promotion" and regular_purchase:
        # A classifier must not trap an explicit switch to normal prices.
        request_type = "order" if promotion_service.checkout_requested(message) else "faq"
    if request_type == "handoff":
        if handoff_reply is False:
            request_type = "faq"
        elif has_handoff_offer(offer_history):
            # The classifier resolves longer acceptances. An older handoff in
            # history must not suppress this fresh invitation and acceptance.
            return _handoff(message, history, store, conversation)
    from .sales.appreciation import closes_conversation as _closes
    # "تمام، هاجي أشمه في الفرع الأول وأقرر بعدين. شكراً" uses الأول as "first", not "the first one";
    # read as an ordinal, the customer's goodbye got the first perfume's description (simulation S10
    # round 2 batch 3, t10).
    closing_turn = _closes(message) and not _re.search(r"[؟?]", message or "")
    if sales_described.ordinal_position(message) is not None and request_type not in ("order", "order_cancel", "handoff", "promotion") \
            and not closing_turn:
        request_type = "product_info"
    if accepted_prices and request_type not in ("order_cancel", "handoff", "promotion"):
        request_type = "product_info"
    # A bounded pointer names an earlier offer, even when "give me" makes the
    # classifier choose checkout. Product/size/quantity requests do not match.
    if request_type in ("recommendation", "order", "product_info", "faq") and occasion_reference_requested(message):
        request_type = "product_info"
    from .sales.request_updates import request_mode
    recommendation_mode = "refinement" if gender_followup else request_mode(message, history, store)
    if closing_turn and request_type != "recommendation":
        # "كده النوتات دي للتركيبة عندكم مش نوتات رسمية… هفكر وأقارن شوية، شكراً" read as a new goal
        # and was sent to a fresh search (simulation S02 round 2 full 5, t8). A goodbye is not a request.
        recommendation_mode = ""
    if recommendation_mode and request_type in ("recommendation", "product_info", "faq", "order", "out_of_domain"):
        request_type = "recommendation"
    from .sales.scent_answers import question_pending as scent_question_pending
    choosing = (
        (request_type == "recommendation" and (_CHOICE_AMONG_DISCUSSED.search(normalized)
                                               or (_DELEGATES_CHOICE.search(normalized)
                                                   and not scent_question_pending(history))))
        # "أنهي واحد فيهم أحلى للصيف؟" classified as a product question still asks to pick (S13 t4).
        or (request_type == "product_info" and _CHOICE_AMONG_DISCUSSED.search(normalized)
            and _PICK_WORDS.search(normalized))
        # "إيه اللي يميز Alexandria II عن التاني؟" compares a named perfume with the other one on the
        # table; product info described Alexandria II alone (simulation S05 final 2 and replay, t4).
        or (request_type in ("product_info", "recommendation") and _COMPARES_WITH_THE_OTHER.search(normalized))
    )
    from .conversation_service import recipient_of
    # A request for someone else in the same message ("وكمان عايز حاجة لماما") is a new recommendation,
    # not a choice among this customer's options (S15 replay t3).
    if choosing and conversation is not None and recipient_of(message) in (None, "self") \
            and not _WANTS_OTHERS.search(normalized) and len(sales_described.under_discussion(conversation, store)) >= 2:
        # "أنهي فيهم أنسب للشغل؟" asks about the perfumes on the table. Sent to recommendation it
        # went through the gender and scent questions to two unrelated perfumes (S08 t2-t4), and
        # "اختارلي واحد فيهم" got a third perfume (S06 t7).
        request_type = "comparison"
    if _total_question_needs_recommendation(request_type, normalized, conversation, active_cart):
        request_type = "recommendation"
    if request_type == "order" and _KEEPS_THE_COUNT.search(normalized) and _asked_to_keep_the_count(history) \
            and not (active_cart is not None and (active_cart.items.exists() or active_cart.pending_items)):
        # "خليهم اتنين، بس التاني مش المفروض يبقى سويت لأخويا؟" answers "تحب نخليها 2 زجاجات في نفس
        # الميزانية، ولا نزود الميزانية؟"; sent to checkout it carted a perfume nobody chose and asked
        # its bottle type for four turns (simulation S03 round 2 batch 2, t4-t7).
        request_type = "recommendation"
    if (request_type == "order" and recipient_of(message) not in (None, "self")
            and _WANTS_SOMETHING_FOR_SOMEONE.search(normalized) and not _BUYING_WORDS.search(normalized)):
        # "Aventus شكله حلو، وكمان عايز حاجة لماما تكون هادية فيها ورد" likes one perfume and asks for
        # another person's; classified as an order, it was asked the Aventus bottle type and the
        # mother's request was skipped (simulation S15 round 3 verify, t3). Nothing in it buys.
        request_type = "recommendation"
    side_products = []
    if (request_type == "product_info" and conversation is not None and _ASKS_FOR_OTHERS.search(normalized)
            and _RECOMMEND_VERB.search(normalized)):
        # "طب رشحلي تاني حاجة كده، وريحة Pure XS الجلدية دي عاملة إزاي؟" asks for a new pick and about a
        # perfume on the table; product info answered the second half only (simulation S14 round 2
        # batch 2, t7). The pick is the recommendation; the named perfume's labels follow it.
        from .product_info import _named_in_message
        side_products = _named_in_message(message, store)
        request_type = "recommendation"
    if not active_cart and re.fullmatch(r"\s*[0-9٠-٩]{2,6}\s*(?:جنيه|ج)?\s*", message):
        reference = numeric_reference(message, store, conversation, history)
        if reference.status != "no_match":
            request_type = "product_info"
        elif history:
            request_type = "recommendation"
    if requires_original and request_type not in ("recommendation", "handoff", "order_cancel") and not _wants_a_human(message):
        return sales_constraints.formulation_reply(message, followup=True), "ORIGINAL_LIQUID_REQUIRED"

    # 🔴 The only reassignment of `request_type` in this file, and it is deliberate: the catalogue
    # outranks the classifier when the customer's own letters unmistakably spell a perfume we stock.
    #
    # Conversation 1106 is "عندكو خمره" — do you have Khamrah — answered with two boozy niche
    # perfumes while four Lattafa Khamrahs sat active in that store. `خمره` is both a perfume name
    # and the ordinary word for liquor, and booze is a real scent family, so reading it as a note is
    # not a mistake the prompt can be told out of: the evidence that settles it is the catalogue, and
    # `classify` never sees the catalogue. It returned `recommendation` 6 times in 10 on that message
    # while `resolve_products` placed `Khamrah` from it every time. A coin flip decided the turn.
    #
    # This also unsticks the follow-up for free. The customer rephrased to "برفان خمره" — Khamrah
    # *perfume*, the reasonable clarification — and that went from `product_info` 6/6 with no history
    # to `recommendation` 10/10 once turn 1's wrong answer was in the history. The check reads the
    # message alone, so turn 2 is corrected on its own evidence.
    #
    # Three classifications only, and the omissions are the point: `order` and `order_cancel` hold a
    # cart, `comparison` holds two perfumes, `identification` is a customer trying to recall a name
    # they cannot spell. Each carries state this branch would discard. `faq` and `out_of_domain` are
    # in because the ta-marbuta spelling "عندكو خمرة" lands on them.
    #
    # Placed above the objection block rather than below it so a corrected turn stays
    # objection-eligible — `product_info` is in `OBJECTION_ELIGIBLE` and `out_of_domain` is not, so a
    # customer grumbling about Khamrah's price reaches the playbook only on this side of it.
    if not recommendation_mode and request_type in _CATALOGUE_CORRECTABLE and not sales_naming.describes_rather_than_names(message):
        # Deterministic: no model call, and the one query `phonetic_ranking` makes. Abstains on
        # genuine browse requests, on spans carrying Latin characters, and on names we do not stock
        # — measured at 0 false positives over all 2961 stored customer messages
        # (`eval_harness.backtest_name_routing`).
        if confident_catalogue_match(message, store):
            request_type = "product_info"

    # An objection is detected from the customer's own words rather than asked of the
    # classifier: it costs no extra model call, it is directly testable, and when it misses
    # the turn simply falls through to the behaviour it had before. It outranks the
    # classification because "غالي" arrives labelled faq or product_info, and answering
    # those as ordinary questions is the defend-instead-of-address bug.
    if conversation is not None and request_type in (
            "product_info", "comparison", "faq", "greeting", "identification", "out_of_domain"):
        # Only recommendation turns ran preference extraction, so "وعلى فكرة مش بحب العود خالص" or
        # "والميزانية خليها لحد 1500" said inside a question was lost (simulation S14 t2/t4).
        # Deterministic and narrow; no message is passed so a question cannot become a wish.
        stated = sales_constraints.stated_preference_updates(message)
        if stated:
            merge_preferences(conversation, stated, None, history=history, store=store)

    if request_type in ("recommendation", "product_info", "comparison", "faq", "greeting") \
            and not _wants_a_human(message):
        nothing_cheaper = _nothing_cheaper_exists(message, normalized, history, conversation, store)
        if nothing_cheaper:
            return nothing_cheaper, ""

    fresh = {}
    previous_quantity = (conversation.preferences or {}).get("purchase_quantity") if conversation else None
    recommendation_policy = []
    if request_type == "recommendation":
        from .sales.scent_preferences import has_scent_preference
        from .sales.scent_answers import next_question as next_scent_question

        # "تمام، والشحن بياخد قد إيه؟ طب رشحلي كمان حاجة سويت" left the delivery question unanswered
        # twice (simulation S14 round 2 batch 3, t4-t5): the store's answer follows the recommendation.
        recommendation_policy, message, asks_more = _policy_side_questions(message, store, history)
        if recommendation_policy and not asks_more:
            # "ينفع أبدله لو مجاش على ذوقها؟ هفكر أطلبه" asks the policy and nothing else (S07 round 2 full 5).
            return "\n\n".join(recommendation_policy), ""
        normalized = normalize_arabic(message)
        fresh = extract_intent(message, history, store)
        supplied_style = has_scent_preference({key: value for key, value in fresh.items() if key != "similar_to"})
        previous_context = (conversation.messages.filter(role="assistant", delivery_status="sent")
                            .order_by("-created_at", "-pk").values_list("internal_context", flat=True).first()) if conversation else ""
        previous_reply = next((entry.get("content", "") for entry in reversed(history or []) if entry.get("role") == "assistant"), "")
        if supplied_style and ("SIMILARITY_NEEDS_PROFILE" in (previous_context or "")
                               or previous_reply.startswith("قولي طابع الريحة اللي بتدور عليه")):
            fresh["clear_preferences"] = list(set(fresh.get("clear_preferences") or []) | {"similar_to", "similar_to_notes"})
        intent = merge_preferences(
            conversation, fresh, message,
            pending=sales_described.pending_relaxations(conversation), history=history, store=store,
        )
        if intent.get("gender") == "multiple":
            # Both sides are remembered, each with its own scent, so the second is offered once the
            # first is chosen (sales.second_gender).
            from .sales import second_gender
            second_gender.start(conversation, message, fresh)
            return sales_gender.MULTIPLE_QUESTION, "MULTIPLE_RECIPIENTS"
        if not sales_gender.canonical(intent.get("gender")):
            if _recently_denied(conversation) and _re.search(r"مفيش|مش\s+موجود|شبه|زي|قريب", normalized):
                # "يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟" got the bare gender question, with no answer to
                # "يعني مفيش؟" and no word for the irritation (simulation S09 round 2 full 4, t2). Nothing
                # recorded describes the missing perfume, so no likeness is promised: "أقدر ألاقيلك حاجة
                # قريبة منه" failed the honest-similarity criterion (S09 round 2 full 5, t2).
                return ("أيوه للأسف مش عندنا، ومعرفش ريحته بالظبط، بس أقدر أرشحلك من اللي عندنا "
                        "على حسب الريحة اللي بتحبها. " + sales_gender.QUESTION, sales_gender.REQUIRED_MARKER)
            from .sales import attraction
            return (attraction.with_acknowledgement(sales_gender.QUESTION, message, history),
                    sales_gender.REQUIRED_MARKER)

    if request_type in ("product_info", "faq", "greeting", "comparison", "out_of_domain"):
        skin_or_clothes = _skin_or_clothes_answer(normalized, history)
        if skin_or_clothes:
            return skin_or_clothes, ""
    if request_type in ("product_info", "faq", "greeting", "comparison"):
        # Before the objection path: "تمام أميل لـ Fleur Musc، هفكر أطلبه إن شاء الله. بس لو مجاش على ذوقها
        # ينفع أبدله؟" was read as a "thinking" objection and the model paraphrased the exchange policy
        # without its fees or the free in-store exchange (simulation S07 round 2 full 6, t5).
        policy_alone, _, asks_more = _policy_side_questions(message, store, history)
        if policy_alone and not asks_more:
            # A complaint about a past purchase that mentions an exchange keeps its own handling. So
            # does "محتار بينه وبين Pure XS": the hesitation is answered and the store's answer follows.
            complaint = sales_objection.detect(message, history)
            if not (complaint is not None and (complaint.is_complaint or complaint.kind == "cant_choose")):
                return "\n\n".join(policy_alone), ""

    objection = None
    if request_type in OBJECTION_ELIGIBLE and not _wants_a_human(message):
        objection = sales_objection.detect(message, history)
        from .sales.scent_preferences import explicit_scent_updates
        stated_style = {key: value for key, value in explicit_scent_updates(message).items() if value}
        if (objection is not None and request_type == "recommendation" and objection.kind == "thinking"
                and _RECOMMEND_VERB.search(normalized)):
            # "طب رشحلي حاجة سويت للسهرات بس مش تقيلة أوي، وأنا هفكر في Wanted by Night كمان" asks for a
            # new pick; its "هفكر" is about another perfume, and the reply was "تمام، خد وقتك." (simulation
            # S14 round 2 batch 3, t9).
            objection = None
        if (objection is not None and request_type == "recommendation" and not objection.is_complaint
                and objection.kind in _SOFT_OBJECTIONS and _actionable_request({**fresh, **stated_style}, message)):
            # "مش متأكد… هي هادية ومش بتحب المسكر.. ترشحلي حاجة؟" carries everything needed to
            # recommend; the objection branch answered it by asking for a perfume name (S07 t2).
            objection = None
        if (objection is not None and request_type == "recommendation" and not objection.is_complaint
                and objection.kind in ("price", "expensive", "budget")
                and _ASKS_FOR_AN_ALTERNATIVE.search(normalized)
                and _something_fits_the_budget(intent.get("max_price"), conversation, store)):
            # "غالي أوي بصراحة، مفيش حاجة شبهه في حدود ١٠٠٠؟" asks for another perfume within a budget,
            # and the request had already been read (budget 1000, like Aventus). Answered as a price
            # objection, it got the same 1400 perfume described again (simulation S09 round 3 replay, t5).
            # With nothing in the store under the budget, the objection path's own answer stands (S11).
            objection = None

    if objection is not None:
        objection_policy = []
        if not objection.is_complaint and request_type != "recommendation":
            # A store question asked beside the hesitation ("…محتار بينهم. الدفع بيكون عند الاستلام؟")
            # is answered from the store's own text after the reply; the objection is the rest.
            answers, rest, asks_more = _policy_side_questions(message, store, history)
            if answers and rest.strip() and (asks_more or objection.kind == "cant_choose"):
                objection_policy, message = answers, rest
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
            message=message,
            budget=(conversation.preferences or {}).get("max_price") if conversation else None,
        )
        stage = (
            sales_stage.COMPLAINT if objection.is_complaint else sales_stage.OBJECTION
        )
        # "طب الـCigar ده قريب من Club de Nuit؟ هفكر فيه لو قريب": the question is about a perfume we
        # hold no data for, whatever kind of hesitation carried it (simulation S09 round 3 verify, t5).
        missing = _closer_to_a_missing_perfume(message, normalize_arabic(message), conversation, store)
        if missing and reply and not _SAYS_SCENT_UNKNOWN.search(reply):
            reply = missing + "\n\n" + reply
        return _finalize(reply, stage) + "".join("\n\n" + a for a in objection_policy), context

    if request_type == "identification":
        reply, context = identify_perfume(message, history, store, conversation)
        # A customer who cannot place a perfume goes round again, and `TIER_WORDING` hands back the
        # same sentence at the same confidence tier each time.
        reply, context, _ = _rephrased(
            reply, context, history, store,
            lambda hint: identify_perfume(message, history, store, conversation, retry_hint=hint),
            message=message,
        )
        return _finalize(reply, sales_stage.IDENTIFICATION), context

    # --- Anti-repetition: detect semantic repetition (same idea, different words) ---
    semantic_rep = _detect_semantic_repetition(history)
    text_rep = _count_recent_repetitions(history)
    
    # Not a checkout message: after four near-identical price answers, "خلاص هاخد الـ50 من الأولاني،
    # الشحن بكام؟" was redirected to the general model, which asked for contact details while the cart
    # stayed empty, and the next turn asked "تحب تطلب أنهي عطر؟" (simulation S11 round 2 batch 4, t6-t7).
    # The order flow holds the cart and is not what was repeating.
    # Nor straight after a denial: since 2026-11-13 a denial is one short sentence, so a customer who
    # asks about several perfumes we do not carry gets near-identical replies by design, and this
    # redirect would answer the next one with a shelf of suggestions and no absence check at all.
    if (request_type not in ("recommendation", "order", "order_cancel") and (text_rep >= 3 or semantic_rep >= 3)
            and not _recently_denied(conversation, turns=1)):
        # Bot has been repeating itself — force a conversation redirect
        # Fetch real products from DB to prevent hallucination when suggesting alternatives.
        # Deterministic rather than order_by('?'), so a repeated conversation can be
        # replayed and the customer is not shown a random gender mix.
        from .fallback import suggest_alternatives
        from .sales.value import eligible_variants
        from .scent_description import describe_scent

        saved_preferences = getattr(conversation, "preferences", None) or {}
        random_products = suggest_alternatives(store, intent=saved_preferences)
        total_quotes = []
        if random_products and saved_preferences.get("budget_scope") == "total":
            from .sales.scent_selection import combined_budget_quotes
            total_quotes = combined_budget_quotes(random_products, saved_preferences)
        quoted_variants = {quote.variant_id for quote in total_quotes}
        
        products_context = ""
        if random_products:
            products_list = []
            for p in random_products:
                available_variants = []
                has_original_bottle = False
                variants = eligible_variants(p, saved_preferences) if saved_preferences else p.variants.all()
                for v in variants:
                    if v.volume <= 0 or (total_quotes and v.pk not in quoted_variants):
                        continue
                    if v.bottle_type == 'normal':
                        available_variants.append(f"{NORMAL_BOTTLE} {v.volume} ملي بـ {v.price} جنيه")
                    elif v.bottle_type == 'original' and (v.stock or 0) > 0:
                        available_variants.append(f"{ORIGINAL_BOTTLE} {v.volume} ملي بـ {v.price} جنيه")
                        has_original_bottle = True
                if available_variants:
                    original_bottle_status = "" if has_original_bottle else f" - (لا يوجد {ORIGINAL_BOTTLE})"
                    style = describe_scent(p.scent_tags, p.occasion, preferred_occasion=saved_preferences.get("occasion"))
                    products_list.append(f"• {p.name} ({', '.join(available_variants)}){original_bottle_status}. {style}")
            if products_list:
                products_context = "\n\n═══ منتجات متوفرة يمكنك اقتراحها (ابدأ بالأقرب في القائمة، ممنوع تذكر أي منتج غيرهم) ═══\n" + "\n".join(products_list)
        
        if total_quotes:
            from .product_formatting import QUOTE_PROSE_INSTRUCTION
            products_context += QUOTE_PROSE_INSTRUCTION
        redirect_prompt = f"""العميل بعتلي: "{message}"

⚠️ تنبيه هام: لقد استخدمت نفس العبارات أو الأفكار عدة مرات في ردودك السابقة.
الرجاء تغيير أسلوبك تماماً واستخدام كلمات مختلفة.
تفاعل مع رسالة العميل بشكل طبيعي ولكن بصياغة جديدة كلياً لتجنب التكرار.
❌ ممنوع تذكر أي منتج أو سعر مش موجود في القائمة التالية.{products_context}"""
        response, context = handle_general(redirect_prompt, history, store)
        if total_quotes:
            from .product_formatting import quote_context
            from .price_reply import compose_price_reply
            from .product_formatting import money
            footer = f"إجمالي الـ{len(total_quotes)} زجاجات: {money(sum(q.price for q in total_quotes))} جنيه."
            context = quote_context(context, total_quotes, append=True,
                                    max_price=saved_preferences.get("max_price"), request=message, footer=footer)
            quotation_context = context

            def regenerate_alternatives(hint):
                draft, _ = handle_general(redirect_prompt + hint, history, store)
                return compose_price_reply(draft, quotation_context)

            response, context = compose_price_reply(response, quotation_context)
            response, context, _ = _rephrased(
                response, context, history, store, regenerate_alternatives,
                recommendation_intent=saved_preferences, recommendation_candidates=random_products,
                message=message,
            )
        return response, context

    if request_type == "recommendation":
        if intent.get("requires_original_liquid"):
            return sales_constraints.formulation_reply(message, followup=True), "ORIGINAL_LIQUID_REQUIRED"
        from products.models import Product
        reference = intent.get("similar_to")
        stocked_reference = bool(reference) and Product.objects.filter(
            store=store, name__iexact=str(reference), is_active=True).exists()
        absent_reference = _recently_denied(conversation) and not stocked_reference
        described = []
        if absent_reference:
            from .scent_description import _tag_mentions
            described = list(dict.fromkeys(tag for tag, _, _, negative in _tag_mentions(normalized) if not negative))
        # With no likeness word either: "رجالي، اللي عاجبني في Club de Nuit ريحته فيها سموكي وفاكهة"
        # describes the missing perfume, so the wish parser leaves it alone, and the scent question went
        # out right after the customer had answered it (simulation S09 round 2 batch 4 attempt 3, t3).
        if absent_reference and (intent.get("similarity_needs_reference") or reference
                                 or _re.search(r"شبه|زي|قريب|نفس\s+(?:الريحه|الستايل)", normalized)
                                 or (described and not has_scent_preference(intent))):
            # The reference is a perfume we just confirmed we do not stock, so it has no data to
            # compare with: "قريبة من ريحته" got "تقصد عطر شبه أنهي واحد: 1 Million ولا…؟" (our own
            # alternatives), and "ريحته فيها سموكي وفاكهة" was asked for again (simulation S09).
            if not described:
                return ("العطر ده مش عندنا فمعرفش ريحته بالظبط؛ قولي ريحته عاملة إزاي "
                        "(سموكي، فواكه، خشبي، فريش، مسكر…) وأرشحلك الأقرب ليها من عندنا."), "SIMILARITY_NEEDS_PROFILE"
            intent = {**intent, "scent_tags": described, "scent_match": "any", "scent_open": False,
                      "similarity_needs_reference": False, "similar_to": None}
            if conversation is not None:
                # Kept for the next turn: "فيه حاجة تانية؟" must not ask for the scent again.
                merge_preferences(conversation, {"scent_tags": described, "scent_match": "any", "scent_open": False},
                                  None, history=history, store=store)
        if intent.get("similarity_needs_reference"):
            candidates = intent.get("similarity_candidates") or []
            question = "تقصد عطر شبه أنهي واحد: " + " ولا ".join(candidates) + "؟" if candidates else "تقصد عطر شبه أنهي عطر أو إصدار؟"
            asked = next((row.get("content", "") for row in reversed(history or []) if row.get("role") == "assistant"), "")
            if question.strip() != asked.strip():
                return question, "SIMILARITY_NEEDS_REFERENCE"
            # Asked once already and not answered with a name: the same question again helps nobody
            # (simulation S11 round 2 batch 5, t5). The rest of the request is recommended on.
            intent = {**intent, "similarity_needs_reference": False}
        recalled = _recalled_offer(message, conversation, store)
        if recalled is not None:
            return recalled
        pair = _per_recipient_scents(message)
        if conversation is not None:
            state = dict(conversation.sales_state or {})
            if pair:
                state["recipient_scents"] = pair
                Conversation_sales_state(conversation, state)
            elif state.get("recipient_scents") and int(intent.get("purchase_quantity") or 0) == 2:
                pair = dict(state["recipient_scents"])
        if pair:
            response = _pair_recommendation(message, pair, intent, history, store, conversation)
            if response is not None:
                return (_total_only_if_repeat(response[0], normalized, history, store)
                        + "".join("\n\n" + a for a in recommendation_policy)), response[1]
        if not has_scent_preference(intent):
            from .sales import attraction
            return (attraction.with_acknowledgement(next_scent_question(message, history), message, history),
                    "SCENT_PREFERENCE_REQUIRED")

        on_table = sales_described.under_discussion(conversation, store)
        keep = on_table - sales_described.moved_past(conversation, store)
        if sales_constraints.preference_reversal(message) or intent.get("recommendation_mode") == "new_goal":
            keep = set()
        demands_guarantee = bool(_GUARANTEE.search(normalized)
                                 and "sweet" in set(intent.get("excluded_scent_tags") or ()))
        if demands_guarantee:
            confirmed = _confirm_not_sweet(normalized, store, on_table)
            if confirmed:
                return confirmed, ""
        wants_others = bool(_ASKS_FOR_OTHERS.search(normalized) or _REJECTS_THE_TABLE.search(normalized)
                            or demands_guarantee)
        # "رشحلي تاني حاجة كده" two side questions after the offer: the last two replies named nothing,
        # so nothing was "on the table" and the same two perfumes came back (simulation S14 round 4
        # replay, t7). What was offered over the last few replies is what "another" excludes.
        seen_lately = (set(sales_described.under_discussion(conversation, store, turns=6))
                       if wants_others and conversation is not None else set())
        narrowed = None
        if wants_others and (on_table or seen_lately):
            # "ترشحلي حاجة تانية" got the same two perfumes back (simulation S10 replay 2, t3).
            keep = set()
            if seen_lately - set(on_table):
                narrowed = {**intent, "exclude_names": sorted(set(intent.get("exclude_names") or ()) | set(on_table))}
            intent = {**intent, "exclude_names": sorted(set(intent.get("exclude_names") or ())
                                                        | set(on_table) | seen_lately)}
        rejected = _rejected_subject(message, conversation, store)
        if rejected:
            keep = set(keep) - set(rejected)
            intent = {**intent, "exclude_names": sorted(set(intent.get("exclude_names") or ()) | set(rejected))}
        results = search_products(intent, store, keep=keep, offered=sales_described.offered_ever(conversation, store))
        if narrowed is not None and not results.get("products"):
            # Nothing else fits: the older options are better than an empty answer.
            if rejected:
                narrowed = {**narrowed, "exclude_names": sorted(set(narrowed["exclude_names"]) | set(rejected))}
            intent = narrowed
            results = search_products(intent, store, keep=keep, offered=sales_described.offered_ever(conversation, store))
        if demands_guarantee:
            results = _without_sweet_notes(results)
        if results.get("needs_scent_reference"):
            return "قولي طابع الريحة اللي بتدور عليه: فريش، سويت، عودي، ولا حاجة تانية؟", "SIMILARITY_NEEDS_PROFILE"
        response, context = recommend(
            message, results["products"], history, store=store, intent=intent, search=results,
            gender_unknown=False,
        )
        from .ai.scent_recommendation import TOTAL_BUDGET_INFEASIBLE
        if (context == TOTAL_BUDGET_INFEASIBLE and intent.get("budget_scope") == "total"
                and conversation is not None and previous_quantity
                and intent.get("purchase_quantity") != previous_quantity):
            # A quantity that does not fit was a question ("لو عايز 3 بنفس الـ1600 ينفع؟"), not an
            # agreement; keeping it made every later turn fail the same way (simulation S03 t5-t7).
            from products.models import Conversation
            restored = {**(conversation.preferences or {}), "purchase_quantity": previous_quantity}
            Conversation.objects.filter(pk=conversation.pk).update(preferences=restored)
            conversation.preferences = restored
        if (response and context != TOTAL_BUDGET_INFEASIBLE and not _TOTAL_QUESTION.search(normalized)
                and not _CHEAPER.search(normalized) and _repeats_recent(response, history)):
            # "رشحلي حاجة تكون متأكد إنها مش مسكرة" got the pair from two turns earlier word for word
            # (simulation S10 round 2 batch 3, t6). The same request earns the next perfumes, not the
            # same block again.
            shown = sales_naming.names_in(response, [p.name for p in results["products"]])
            if shown:
                retry_intent = {**intent, "exclude_names": sorted(set(intent.get("exclude_names") or ()) | set(shown))}
                retry = search_products(retry_intent, store, keep=set(),
                                        offered=sales_described.offered_ever(conversation, store))
                if demands_guarantee:
                    retry = _without_sweet_notes(retry)
                if retry.get("products"):
                    response, context = recommend(message, retry["products"], history, store=store,
                                                  intent=retry_intent, search=retry, gender_unknown=False)
                    results, intent = retry, retry_intent
        if response and _REPEAT_COMPLAINT.search(normalized) and not _repeats_recent(response, history):
            # "إنت كده بتكرر نفس الترشيحات!" got new names with no word about the complaint (S10 t7).
            response = "حقك عليا. دي اختيارات تانية:\n\n" + response
        stage = sales_stage.derive(request_type, message, intent, objection, history)
        response = _nothing_cheaper(response, normalized, history, results["products"], store, conversation)
        response = _uncommon_note(response, results["products"], intent)
        from .sales.attraction import appeal_note
        response = appeal_note(response, results["products"], intent)
        response = _guarantee_note(response, results["products"], intent, normalized)
        if side_products and response:
            from .price_reply import requested_product_details
            details = requested_product_details(side_products, message, history=history, description_required=True)
            if details:
                response = response + "\n\n" + "\n".join(details)
        return (_finalize(_total_only_if_repeat(response, normalized, history, store), stage)
                + "".join("\n\n" + a for a in recommendation_policy)), context

    elif request_type == "product_info":
        if closing_turn and not _re.search(r"\d", message or ""):
            # "خلاص هختار Bloom… هطلبه بعدين، شكراً" decides and closes; it got Bloom's description
            # again (simulation S06 round 2 full 3, t7).
            return "تمام يا فندم، ولما تحب تطلبه ابعتلي.", ""
        # Only with no perfume on the table: a follow-up about the one under discussion keeps its own,
        # named answer (`performance_only_question`).
        performance_answer = (_store_performance_answer(message, store, normalized)
                              if not sales_described.offered_in_order(conversation, store) else None)
        if performance_answer:
            return performance_answer, ""
        policy, message, asks_more = _policy_side_questions(message, store, history)
        if policy and not asks_more:
            # "تمام، هفكر في الـ50 ملي وأطلبه، بس ينفع أبدله لو مجاش على ذوقها؟" got the 50 ml price twice
            # and no answer (simulation S07 round 2 full 5, t6-t7).
            return "\n\n".join(policy), ""
        response, context = get_product_info(message, history, store, conversation)

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
                    "قوله بوضوح وباعتذار قصير إن العطر مش موجود عندنا، وبس — ❌ من غير وعد، ومن غير "
                    "ما تعرض بديل ليه.\n"
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
            message=message, preferred_occasion=(conversation.preferences or {}).get("occasion") if conversation else None,
        )
        response = _denial_only_reply(response, context, message, store)

        # After the retry, so a deferral the retry introduced or removed is judged on the context
        # actually being sent.
        if _escalate_absent_name(conversation, store, context, message, history) is True:
            # The conversation is now with staff and the bot goes silent; say so instead of
            # leaving the customer waiting on an unanswered repeat (simulation S03 t7).
            response = response + "\n\n" + HANDOFF_CONFIRMATION

        response = _without_answered_strength_question(
            response, conversation.preferences if conversation is not None else None)
        response = _without_retype_request(response, message, context)
        fixed_price = _fixed_price_note(message, store)
        if fixed_price and fixed_price not in response:
            response = response.rstrip() + "\n" + fixed_price
        missing = _closer_to_a_missing_perfume(message, normalized, conversation, store)
        if missing and not _SAYS_SCENT_UNKNOWN.search(response):
            response = missing + "\n\n" + response
        original_note = _original_bottle_note(message, response, history)
        if original_note:
            response = response.rstrip() + "\n\n" + original_note
        notes_source = _notes_source_note(normalized, history)
        if notes_source and notes_source not in response:
            response = notes_source + "\n\n" + response

        # A price or size question is purchase-adjacent and may close; "ريحته عاملة ايه؟"
        # is a factual question and may not.
        stage = sales_stage.derive(request_type, message, None, objection, history)
        return _finalize(_thanked(message, response, history), stage) + "".join("\n\n" + a for a in policy), context

    elif request_type == "comparison":
        policy, message, asks_more = _policy_side_questions(message, store, history)
        if policy and not asks_more:
            return "\n\n".join(policy), ""
        response, context = compare_products(message, history, store, conversation)

        # The first repetition check of any kind on this branch. It needs one more than most: its
        # instruction 5 REQUIRES the closing frame "أنا أرشحلك X أكتر لأن…" — the frame conversation
        # 973 ended four of five replies on — so a customer who compares twice gets it twice by
        # construction, and nothing here was looking.
        response, context, _ = _rephrased(
            response, context, history, store,
            lambda hint: compare_products(message, history, store, conversation, retry_hint=hint),
            message=message, preferred_occasion=(conversation.preferences or {}).get("occasion") if conversation else None,
        )
        response = _denial_only_reply(response, context, message, store)

        # `compare_products` hands a turn it cannot place two perfumes on straight to
        # `get_product_info`, so this branch now produces the same markers the one above does —
        # and a customer comparing a perfume we stock against one we do not is exactly the case
        # the owner needs told about. Escalating in both places rather than once after the chain
        # keeps the other dozen branches' `return`s untouched; these two are the only callers of
        # `get_product_info`.
        if _escalate_absent_name(conversation, store, context, message, history) is True:
            # The conversation is now with staff and the bot goes silent; say so instead of
            # leaving the customer waiting on an unanswered repeat (simulation S03 t7).
            response = response + "\n\n" + HANDOFF_CONFIRMATION

        missing = _closer_to_a_missing_perfume(message, normalized, conversation, store)
        if missing and not _SAYS_SCENT_UNKNOWN.search(response):
            response = missing + "\n\n" + response

        # Still weighing two options — differentiate, do not close.
        return (_finalize(_thanked(message, response, history), sales_stage.COMPARISON)
                + "".join("\n\n" + a for a in policy)), context

    elif request_type in ["greeting", "faq"]:
        from .sales.appreciation import closes_conversation
        if closes_conversation(message) and not _re.search(r"[؟?]", message or ""):
            # "تمام، هفكر في الـ Cigar عشان سعره مناسب. تسلم يا باشا" got a smaller-size pitch
            # (simulation S09 round 2, t8). A customer who is closing gets a short close.
            return "العفو يا فندم، ولو احتجت أي حاجة أنا موجود.", ""
        from .performance_policy import current_request, description
        from .product_info import _named_in_message
        performance = current_request()
        if (performance.longevity or performance.projection) and not _named_in_message(message, store):
            # The store's performance policy covers every perfume. "طب بيقعد قد إيه بجد؟" got "محتاجة
            # تأكيد من فريق خدمة العملاء، تحب أحولك؟" and an unneeded handoff (simulation S16 round 2).
            text = description(performance, longevity=performance.longevity, projection=performance.projection)
            return "كل عطورنا " + text.replace("ثباته ", "ثباتها ").replace("فوحانه ", "فوحانها ") + ".", ""
        return handle_general(message, history, store)

    elif request_type == "musk_mix_product":
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
        return promotion_service.reply(message, promotion_history, store, conversation=conversation, usage_recorded=True)

    elif request_type == "order":
        if not conversation:
            return "ابدأ محادثة جديدة عشان أسجلك الطلب.", ""
        # The classifier route skipped the side answers the regex checkout route gives, so
        # "خلاص هاخد الـ50. الشحن بكام؟" got the cart and no shipping answer (simulation S11 t5).
        reply, context = handle_order(message, history, store, conversation)
        return _with_side_answers(message, reply, history, store, conversation), context
        
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

            if cart and (cart.items.exists() or cart.pending_items or cart.pending_product_id):
                clear_cart(conversation, keep_details=True)
                return "تمام، لغيت الطلب اللي كنا بنجهزه.", ""

            latest_order = Order.objects.filter(conversation=conversation, status="pending").order_by('-created_at').first()
            if latest_order:
                from .order_service import change_order_status
                change_order_status(latest_order.pk, store, "cancelled", bot_notes="تم إلغاء الطلب بواسطة البوت بناءً على طلب العميل.")
                return "تمام، لغيت آخر طلب اتسجل.", ""
            else:
                return "مفيش طلب نشط دلوقتي عشان ألغيه.", ""
        return "مفيش طلب نشط دلوقتي عشان ألغيه.", ""
        
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
            return _handoff(message, history, store, conversation)
        
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
