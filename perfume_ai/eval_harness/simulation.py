# -*- coding: utf-8 -*-
"""Adaptive customer simulations driven through the real chat pipeline.

Each seller turn goes through exactly what the web chat view runs —
`receive_event` → `generate_event(event, route, sanitize_reply)` — so routing, preference memory,
catalogue lookup, generation, formatting, sanitising, cart/order state and the human-handoff gate
are all live. The reply graded is `event.result["reply"]`, the text the customer would receive.

The customer is a separate model call with a customer-only prompt (`CUSTOMER_SYSTEM_PROMPT`). It
sees the visible transcript and its own private brief; the seller sees only the customer's message
text. Nothing from the brief, the success criteria or the assertions reaches the seller.

Every scenario runs inside a transaction that is rolled back, against a disposable database (see
scripts/simulate_customers.py), with outbound HTTP disabled.
"""

import json
import re
import time
import traceback

from django.db import transaction

from eval_harness import checks
from eval_harness import runner

CUSTOMER_SYSTEM_PROMPT = """أنت بتمثل دور عميل مصري حقيقي بيكلم شات بوت محل عطور اسمه Perfamix على الموبايل.
مهمتك الوحيدة: تكتب رسالة العميل الجاية وبس. أنت مش البياع، ومش بتقيّم، ومش بتشرح.

قواعد:
- اكتب زي ما العميل ده بيكتب فعلاً (الأسلوب تحت)، رسالة واحدة قصيرة غالباً.
- رد على اللي البياع قاله فعلاً في آخر رسالة. لو سألك سؤال جاوبه بطبيعتك أو تجاهله لو شخصيتك كده.
- متقولش كل تفضيلاتك مرة واحدة. اكشف المعلومة لما تتسأل عنها أو لما تيجي طبيعي.
- لو البياع فهمك غلط أو تجاهل حاجة قلتها، صححه زي أي عميل.
- النقط اللي في "beats" اعملها لما الموقف يسمح، مش بالترتيب الحرفي ومش كلها لو المحادثة مشيت في اتجاه تاني. متكررش نفس الـ beat.
- متقولش أبداً إنك عميل تجريبي أو موديل أو إن فيه سيناريو.
- متخترعش أسعار أو أسماء برفانات مش موجودة في كلام البياع، إلا اسم مذكور في البريف بتاعك.
- لما المحادثة تخلص طبيعي (اشتريت، أو قلت شكراً ومشيت، أو اتحولت لحد من الفريق) خلّي end = true.
- لو آخر رسالة ليك كانت وداع أو شكر ختامي وماحدش سألك حاجة جديدة، خلي end = true والرسالة فاضية.

رجّع JSON بس بالشكل ده:
{"message": "نص رسالة العميل", "end": false}
"""


def _brief(scenario):
    customer = scenario["customer"]
    beats = "\n".join(f"- {beat}" for beat in customer.get("beats", []))
    return (
        f"شخصيتك: {customer['profile']}\n"
        f"هدفك: {customer['goal']}\n"
        f"تفضيلاتك الخاصة (متقولهاش غير لما تيجي مناسبة): {customer['private']}\n"
        f"أسلوب كتابتك: {customer['style']}\n"
        f"مشاعرك خلال المحادثة: {customer['emotions']}\n"
        f"نهاية المحادثة: {customer['ending']}\n"
        f"beats:\n{beats}"
    )


def _transcript(turns):
    lines = []
    for turn in turns:
        lines.append(f"أنت (العميل): {turn['user']}")
        reply = turn.get("reply") or "(مفيش رد — المحادثة اتحولت لفريق خدمة العملاء)"
        lines.append(f"البياع: {reply}")
    return "\n".join(lines)


def next_customer_message(scenario, turns, create, model):
    """One customer message, or None when the customer has naturally finished."""
    number = len(turns) + 1
    status = (
        f"دي رسالتك رقم {number}. الحد الأدنى المعتاد للمحادثة دي {scenario['min_turns']} رسايل "
        f"والحد الأقصى {scenario['max_turns']}."
    )
    if number < scenario["min_turns"]:
        status += " لسه بدري تنهي المحادثة إلا لو البياع خلّص طلبك فعلاً أو اتحولت للفريق."
    elif number >= scenario["max_turns"]:
        status += " دي آخر رسالة ليك: اقفل المحادثة بطريقة طبيعية."
    prompt = (
        f"البريف بتاعك:\n{_brief(scenario)}\n\nالمحادثة لحد دلوقتي:\n{_transcript(turns)}\n\n"
        f"{status}\nاكتب رسالتك الجاية كـ JSON."
    )
    response = create(
        model=model,
        messages=[{"role": "system", "content": CUSTOMER_SYSTEM_PROMPT},
                  {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        reasoning_effort="none",
        temperature=0.6,
    )
    data = json.loads(response.choices[0].message.content)
    message = str(data.get("message") or "").strip()
    if not message:
        return None
    return message


def install_claim_probe():
    """Record every draft the product-claim checker rejected, and why.

    `router._rephrased` imports `product_claim_issues` at call time, so patching the module
    attribute reaches it; the recommendation module bound its own copy at import and is patched too.
    A turn whose final reply is the generic fallback ("ماقدرش أأكد المعلومة دي") is otherwise
    undiagnosable: the rejected drafts never reach the transcript.
    """
    from products.services import reply_sanitizer
    from products.services.ai import scent_recommendation
    original = reply_sanitizer.product_claim_issues

    def probe(reply, facts, **kwargs):
        issues = original(reply, facts, **kwargs)
        if issues:
            runner._rec().setdefault("claim_checks", []).append(
                {"draft": (reply or "")[:500], "issues": list(issues)[:8]})
        return issues

    reply_sanitizer.product_claim_issues = probe
    scent_recommendation.product_claim_issues = probe


def _cart_state(conversation, context):
    from products.models import Cart, Order
    from products.services.sales.described import _is_cart_context
    cart = Cart.objects.filter(conversation=conversation).first()
    return {
        "items": [{"name": item.variant.product.name, "volume": item.variant.volume,
                   "bottle_type": item.bottle_type, "quantity": item.quantity,
                   "price": str(item.variant.price)}
                  for item in cart.items.select_related("variant__product")] if cart else [],
        "pending": cart.pending_items if cart else [],
        "order_count": Order.objects.filter(conversation=conversation).count(),
        "reply_kind": "checkout" if _is_cart_context(context) else "other",
        "contact_fields": [field for field in ("customer_name", "customer_phone", "secondary_phone", "shipping_address")
                           if cart and getattr(cart, field)],
    }


def _orders(conversation):
    from products.models import Order
    return [{
        "id": order.pk, "status": order.status, "total_price": str(order.total_price),
        "customer_name": order.customer_name,
        "items": [{"name": item.product_snapshot_name or (item.variant.product.name if item.variant else ""),
                   "volume": item.volume_snapshot or (item.variant.volume if item.variant else None),
                   "quantity": item.quantity, "bottle_type": item.bottle_type,
                   "price": str(item.price_at_time_of_order)} for item in order.items.all()],
    } for order in Order.objects.filter(conversation=conversation).prefetch_related("items__variant__product")]


def _active_budget(budgets, customer_messages):
    """The latest scenario budget whose number the customer has actually typed."""
    latest, position = None, -1
    for budget in budgets or ():
        for index, text in enumerate(customer_messages):
            if re.search(rf"(?<!\d){int(budget)}(?!\d)", text or "") and index >= position:
                latest, position = budget, index
    return latest


def _active_exclusions(rules, customer_messages):
    joined = "\n".join(customer_messages)
    return {rule["tag"] for rule in rules or () if re.search(rule["trigger"], joined, re.I)}


def run_simulated_scenario(scenario, truth, tag_map, simulate, on_turn=None):
    """Play one adaptive conversation and grade every reply deterministically."""
    from products.models import Conversation, InboundEvent, Store
    from products.services.conversation_service import (
        conversation_lock, generate_event, pending_through, receive_event,
    )
    from products.services.reply_sanitizer import sanitize_reply
    from products.services.router import route
    from products.services.sales.naming import names_in

    store = Store.objects.get(name=runner.STORE_NAME)
    assertions = scenario.get("assert", {})
    record = {"id": scenario["id"], "title": scenario["title"], "persona": scenario["persona"],
              "turns": [], "findings": [], "error": None, "ended_by": None}
    started = time.time()
    try:
        with transaction.atomic():
            conversation = None
            message = scenario["opening"]
            previous_reply = ""
            for index in range(1, scenario["max_turns"] + 1):
                runner._local.turn = {}
                turn_started = time.time()
                orders_before = len(_orders(conversation)) if conversation else 0
                handed_off_before = bool(conversation and Conversation.objects.get(pk=conversation.pk).needs_human)
                error = None
                try:
                    event = receive_event(store, "web", "", message, None, conversation)
                    conversation = event.conversation
                    with conversation_lock(f"conversation:{event.conversation_id}"):
                        for waiting in pending_through(event):
                            generate_event(waiting, route, sanitize_reply)
                    event.refresh_from_db()
                    reply = (event.result or {}).get("reply", "")
                except Exception as exc:  # a crash is a finding, not a lost scenario
                    error = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()
                    reply = ""
                    if conversation is not None:
                        InboundEvent.objects.filter(conversation=conversation, status="pending").update(
                            status="complete", result={"reply": "", "error": error})
                conversation.refresh_from_db()
                state = dict(runner._local.turn)
                context = conversation.messages.filter(role="assistant").order_by("-id").values_list(
                    "internal_context", flat=True).first() if reply else ""
                cart = _cart_state(conversation, context)
                orders = _orders(conversation)
                customer_messages = [turn["user"] for turn in record["turns"]] + [message]
                budget = _active_budget(assertions.get("budgets"), customer_messages)
                exclusions = _active_exclusions(assertions.get("excluded_tags"), customer_messages)
                named_by_customer = set(names_in(message, list(tag_map)))
                recommended = [name for name in names_in(reply, list(tag_map)) if name not in named_by_customer]

                findings = []
                if error:
                    findings.append(("crash", "critical", f"turn {index} raised {error}"))
                if reply:
                    findings.extend(checks.check_reply(
                        reply, truth=truth, context=context, customer_text=message, turn_state=state,
                        history_text="\n".join(f"{t['user']}\n{t['reply']}\n{t.get('context') or ''}" for t in record["turns"]),
                        history=[item for t in record["turns"] for item in (
                            {"role": "user", "content": t["user"]}, {"role": "assistant", "content": t["reply"]})],
                    ))
                    repeat = checks.check_repeated_reply(reply, [t["reply"] for t in record["turns"]])
                    if repeat:
                        findings.append(("repeated_reply", "critical",
                                         f"{repeat[0]:.0%} identical to an earlier reply: '{repeat[1][:100]}'"))
                    if budget:
                        over = checks.check_budget_respected(reply, budget, truth)
                        if over:
                            findings.append(("over_budget_offer", "high", f"quoted {over} against a stated budget of {budget}"))
                        stated = checks.check_stated_total(reply, budget, truth)
                        if stated:
                            findings.append(("over_budget_total", "critical",
                                             f"stated a total of {stated} against a budget of {budget} without acknowledgement"))
                findings.extend(checks.check_behavior(
                    reply, customer_text=message, previous_reply=previous_reply, turn_index=index,
                    scenario_assert=assertions,
                    needs_human_after=bool(conversation.needs_human),
                    order_delta=len(orders) - orders_before, tag_map=tag_map, recommended_names=recommended,
                    active_budget_known=bool(budget) and index > 1 and any(
                        re.search(rf"(?<!\d){int(budget)}(?!\d)", t["user"]) for t in record["turns"]),
                    active_exclusions=exclusions,
                ))
                turn = {
                    "n": index, "user": message, "reply": reply, "seconds": round(time.time() - turn_started, 2),
                    "handoff_active_before": handed_off_before,
                    "needs_human_after": bool(conversation.needs_human),
                    "classification": state.get("classification"), "objection": state.get("objection"),
                    "raw_intent": state.get("raw_intent"), "merged_intent": state.get("merged_intent"),
                    "pending_relax": state.get("pending_relax"),
                    "stage": state.get("stage") or state.get("derived_stage"),
                    "search": state.get("search"), "offered": state.get("offered"),
                    "saved_preferences": dict(conversation.preferences or {}),
                    "cart": cart, "orders": orders, "context": context,
                    "active_budget": budget, "active_exclusions": sorted(exclusions),
                    "recommended_names": recommended,
                    "claim_checks": state.get("claim_checks") or [],
                    "findings": [list(f) for f in findings], "error": error,
                }
                record["turns"].append(turn)
                record["findings"].extend(findings)
                previous_reply = reply
                if on_turn:
                    on_turn(record, turn)
                if conversation.needs_human:
                    # The chat view stops generating once the conversation is with staff.
                    record["ended_by"] = "handoff"
                    break
                if index >= scenario["max_turns"]:
                    record["ended_by"] = "max_turns"
                    break
                message = simulate(scenario, record["turns"])
                if message is None:
                    record["ended_by"] = "customer"
                    break
            record["needs_human"] = bool(conversation.needs_human)
            record["orders"] = _orders(conversation)
            record["final_preferences"] = dict(conversation.preferences or {})
            record["findings"].extend(checks.check_conversation(record, assertions))
            raise runner._Rollback
    except runner._Rollback:
        pass
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    except BaseException as exc:
        # The spending guard stops paid calls with a BaseException so application fallbacks cannot
        # swallow it. Keep the partial transcript and report the scenario as incomplete.
        if type(exc).__name__ != "BudgetStop":
            raise
        record["error"] = f"BudgetStop: {exc}"
        record["budget_stopped"] = True
    record["findings"] = [list(f) for f in record["findings"]]
    record["seconds"] = round(time.time() - started, 1)
    return record
