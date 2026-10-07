import json
import logging
import hashlib
import uuid
from functools import wraps
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import F, Q
from products.models import Cart, CartItem, Order, OrderItem, Product, ProductVariant, Conversation
from .ai.client import chat
from .fallback import suggest_alternatives
from .product_resolver import resolve_product
from .notification_service import notify_new_order
from .sales.value import budget_tier, stated_budget

logger = logging.getLogger(__name__)


def _serialized(function):
    @wraps(function)
    def run(message, history, store, conversation):
        from .conversation_service import conversation_lock
        with conversation_lock(f"conversation:{conversation.pk}"):
            conversation.refresh_from_db()
            return function(message, history, store, conversation)
    return run


def _fingerprint(cart, items):
    state = {
        "details": [str(getattr(cart, key) or "").strip() for key in
                    ("customer_name", "customer_phone", "secondary_phone", "shipping_address")],
        "items": sorted((i["variant"].pk, i["quantity"], i["bottle_type"], str(Decimal(i["price"]).quantize(Decimal(".01")))) for i in items),
        "pending": cart.pending_items,
    }
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _saved_lines(cart):
    lines = _cart_items_as_products_data(cart) + list(cart.pending_items or [])
    if not lines and cart.pending_product_id:
        lines = [{"name": cart.pending_product.name, "quantity": 1}]
    for line in lines:
        line.setdefault("line_id", uuid.uuid4().hex)
    return lines


def _merge_lines(cart, data):
    """Omission is not removal. IDs distinguish separate sizes of the same perfume."""
    lines = _saved_lines(cart)
    removed = data.get("removed_line_ids", [])
    if not isinstance(removed, list) or any(not isinstance(x, str) for x in removed):
        raise ValueError("Invalid removed_line_ids")
    lines = [line for line in lines if line["line_id"] not in removed]
    supplied = data.get("products", [])
    if supplied is None:
        supplied = []
    if not isinstance(supplied, list):
        raise ValueError("Invalid products")
    touched = set()
    for entry in supplied:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError("Invalid product name")
        entry = {k: v for k, v in entry.items() if k in ("name", "line_id", "quantity", "volume", "bottle_type")}
        for key in ("quantity", "volume"):
            value = entry.get(key)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"Invalid {key}")
        if entry.get("bottle_type") not in (None, "normal", "original"):
            raise ValueError("Invalid bottle_type")
        target = next((x for x in lines if entry.get("line_id") and x["line_id"] == entry["line_id"]), None)
        same_name = [x for x in lines if x["name"].casefold() == entry["name"].casefold() and x["line_id"] not in touched]
        if target is None and len(same_name) == 1:
            target = same_name[0]
        if target is None:
            target = {"line_id": uuid.uuid4().hex, "quantity": 1}
            lines.append(target)
        target.update({k: v for k, v in entry.items() if v is not None and k != "line_id"})
        touched.add(target["line_id"])
    return lines


class _LineProblem(Exception):
    pass


def get_cart(conversation):
    """The conversation's in-progress cart, created on first use."""
    cart, _ = Cart.objects.get_or_create(conversation=conversation)
    return cart


def clear_cart(conversation, keep_details=False):
    """Empty the in-progress cart. No stock to restore — none was ever taken.

    `keep_details=True` carries the customer's name, phone and address into a fresh cart. A
    cancellation used to delete the row outright, so a customer who cancelled one thing and
    re-ordered had to retype every contact detail they had just given — which is exactly what
    happened when "مش عايز 1 × Noirvel (90ml)" was read as cancelling the whole order.

    Deliberately delete-and-recreate rather than emptying the row in place. `_summary_was_shown`
    scopes its check to `created_at__gte=cart.created_at`, so a surviving row keeps its old
    timestamp and a summary sent *before* the cancellation would still authorise a confirmation
    *after* it — creating an order the customer was never shown a total for. Recreating advances
    `created_at`, so that guard stays honest while the details survive.

    `create_order_in_db` keeps the default: once a cart has become an Order, the next order in
    the conversation starts genuinely empty.
    """
    carried = None
    if keep_details:
        cart = Cart.objects.filter(conversation=conversation).first()
        if cart:
            carried = {
                "customer_name": cart.customer_name,
                "customer_phone": cart.customer_phone,
                "secondary_phone": cart.secondary_phone,
                "shipping_address": cart.shipping_address,
            }

    Cart.objects.filter(conversation=conversation).delete()

    if carried and any(carried.values()):
        Cart.objects.create(conversation=conversation, **carried)


# The total line of the order summary generated below. Its presence in the thread is
# the evidence that the customer was actually shown a total before agreeing to it.
CONFIRMATION_SUMMARY_MARKER = "💰 الإجمالي:"


def _summary_was_shown(conversation, cart, items=None):
    quote = cart.quote or {}
    if not conversation or not quote.get("message_id") or not quote.get("digest"):
        return False
    if items is not None and quote["digest"] != _fingerprint(cart, items):
        return False
    return conversation.messages.filter(pk=quote["message_id"], role="assistant", delivery_status="sent").exists()


def _quote_summary(conversation, cart, items):
    """Persist the exact cart/prices awaiting approval and render its public summary."""
    summary = "تمام، راجع معايا تفاصيل الطلب كده:\n\n"
    summary += f"👤 الاسم: {cart.customer_name}\n"
    summary += f"📱 الموبايل: {cart.customer_phone}\n"
    if cart.secondary_phone:
        summary += f"📞 موبايل بديل: {cart.secondary_phone}\n"
    summary += f"📍 العنوان: {cart.shipping_address}\n\n🛍️ الطلب:\n"
    for item in items:
        bottle_disp = "أوريجينال" if item['bottle_type'] == "original" else "البراند"
        summary += f"- {item['quantity']} × {item['variant'].product.name} ({item['variant'].volume}ml) - زجاجة {bottle_disp} (السعر: {item['price'] * item['quantity']} جنيه)\n"
    total = sum((item['price'] * item['quantity'] for item in items), Decimal('0'))
    summary += f"\n💰 الإجمالي: {total} جنيه.\n"
    summary += _over_budget_warning(conversation, items)
    summary += "\nكل البيانات كده تمام ونأكد الطلب، ولا تحب تعدل حاجة؟"
    cart.quote = {"token": uuid.uuid4().hex, "digest": _fingerprint(cart, items),
                  "summary_hash": hashlib.sha256(summary.encode()).hexdigest()}
    cart.save(update_fields=["quote"])
    return summary


def _cart_context(cart):
    """Render the saved cart for the extractor prompt.

    This is the fix for the truncation bug: the model reads the cart here instead
    of reconstructing it from a conversation history that only goes back 8
    messages.

    Rendered as JSON in the same shape the extractor must return, deliberately.
    An earlier version printed readable Arabic with "(مش متوفر)" for unknown
    fields, and the model echoed that string back as the customer's name — a
    non-empty value, so it passed the missing-field checks and confirmed orders
    with no contact details at all.
    """
    items = [
        {
            "name": item.variant.product.name,
            "volume": item.variant.volume,
            "bottle_type": item.bottle_type,
            "quantity": item.quantity,
        }
        for item in cart.items.select_related('variant__product').all()
    ]
    state = {
        "products": _saved_lines(cart),
        # A perfume chosen but not yet sized. Reported separately from `products`
        # because it has no variant and therefore no price — but reporting it at all is
        # what stops the next turn losing it: an empty `products` list used to be the
        # only signal, and the rule below (correctly) forbids refilling an empty cart
        # from history, so the perfume vanished.
        "pending_product": (
            cart.pending_product.name if cart.pending_product_id else None
        ),
        "customer_name": cart.customer_name or None,
        "customer_phone": cart.customer_phone or None,
        "customer_secondary_phone": cart.secondary_phone or None,
        "shipping_address": cart.shipping_address or None,
    }

    return f"""

═══ SAVED CART — authoritative current state, NOT the history ═══
{json.dumps(state, ensure_ascii=False, indent=2)}

A field that is null above is genuinely unknown. Return null for it too, unless
the customer's LATEST message supplies it. NEVER invent a value, and never copy
placeholder or descriptive text into a field.

If "pending_product" is set, the customer has already chosen that perfume and only
the size (and/or bottle type) is still missing. Include it in "products" — carrying
over any quantity, size or bottle type the latest message supplies — instead of
returning an empty list.
"""


def _looks_like_phone(value):
    """Guard against a hallucinated or echoed value reaching customer_phone.

    The extractor is a language model, so no prompt wording makes its output
    trustworthy enough to write into an Order unchecked. An Egyptian mobile is
    11 digits; requiring 7 catches placeholder text and prose without rejecting
    numbers the customer typed with spaces or dashes.
    """
    return bool(value) and sum(character.isdigit() for character in str(value)) >= 7


# Shown when a store hasn't configured payment_instructions. Deliberately says
# nothing concrete: emitting another store's payment account is worse than asking
# the customer to wait for the team.
PAYMENT_FALLBACK = (
    "فريق المبيعات هيتواصل معاك في أقرب وقت يأكدلك تفاصيل الدفع والشحن."
)


def _payment_instructions(store):
    """This store's own payment block for the order confirmation.

    Was hardcoded in create_order_in_db, which meant every store's customers were
    sent the first store's InstaPay link — money to the wrong account.
    """
    try:
        instructions = (store.settings.payment_instructions or "").strip()
    except Exception:
        logger.warning(f"No StoreSettings for store '{store.name}'; using payment fallback.")
        return PAYMENT_FALLBACK

    if not instructions:
        logger.warning(
            f"Store '{store.name}' has no payment_instructions configured; "
            f"the customer was not given payment details."
        )
        return PAYMENT_FALLBACK

    return instructions



def _over_budget_warning(conversation, items_to_create):
    """Flag a line, or a cart total, priced above the budget the customer stated earlier.

    The order flow never consulted `conversation.preferences`, so a 1085 line was assembled in
    silence against a stated 900 — and the only thing that caught it was the customer reading
    the summary. Computed rather than left to the model, for the same reason
    recommendation._in_budget_note is: whether a line exceeds a number is arithmetic.

    Phrased as a question rather than a refusal. The customer may well want it, and the summary
    is already the moment they are being asked to check.

    Three defects the evaluation found here, all fixed:

      * The function was never called. It was added with its tests and the summary block was
        never edited to interpolate it, so not even the per-line warning ever reached anybody.
      * It compared the *unit* price, so 2 × 780 against a stated 900 passed silently — the
        quantity was ignored even though the summary line prints `price * quantity`.
      * It never checked the total, so two individually-affordable lines could assemble a cart
        at any multiple of the budget. Scenario F1 reached 1560 against 900 this way.

    The per-line warning is kept alongside the total: they are different problems and a
    customer who is over on both should hear about both. One line that is itself over budget
    reports only once, since the total warning would be telling them the same thing twice.

    Two later corrections, both from conversation 931, where a 1200 stated for a single Versace
    was compared against a four-perfume 3138 basket:

      * The comparisons were bare `>`, so this was the fourth place in the codebase with its own
        budget arithmetic and the only one that did not know about BUDGET_TOLERANCE. A 1250 line
        against a stated 1200 was reported here as a breach while `budget_label` was
        simultaneously telling the model the same price was "تقدر تعرضه مع التوضيح" — the exact
        drift `budget_tier`'s docstring was written to end. Both comparisons go through it now,
        so only a "far" price raises an alarm.
      * The *wording* of the total warning, when the cart holds more than one perfume. `max_price`
        is a per-bottle ceiling in every other reader — `budget_label`, `budget_tier`,
        `search_service`'s eligibility filter, `ranking`'s budget credit, `value_pick_note`'s
        filter, and all three prompt branches that render prices — so no perfume in a 3138 basket
        costs 3138 and "أعلى من الميزانية اللي قلتها" is a verdict the arithmetic does not
        support. The disclosure is kept, because a customer with 900 in mind who reaches 1753
        wants to hear it and scenario F1 is why the total is checked at all; what goes is the
        verdict framing, which is what the model lifted and restated as a per-item breach on
        later turns. Multi-perfume carts now get the observation with its scope attached.
    """
    budget = stated_budget(conversation)
    if budget is None:
        return ""

    def _line_total(item):
        try:
            return Decimal(str(item["price"])) * int(item.get("quantity") or 1)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    over = [
        f"{item['variant'].product.name} ({item['variant'].volume} ملي) بـ {_line_total(item):.0f}"
        for item in items_to_create
        if budget_tier(_line_total(item), budget) == "far"
    ]
    total = sum((_line_total(item) for item in items_to_create), Decimal("0"))
    prefs = conversation.preferences or {}
    if prefs.get("budget_scope") == "total":
        return (f"\n⚠️ إجمالي الطلب {total:g} جنيه، أعلى من ميزانيتك الإجمالية {budget:g} جنيه. لازم نعدّل الاختيارات أو الميزانية قبل التأكيد.\n"
                if total > budget else "")
    if prefs.get("budget_strict") is True:
        expensive = [item for item in items_to_create if Decimal(item["price"]) > budget]
        return (f"\n⚠️ فيه اختيار أعلى من الحد الأقصى {budget:g} جنيه. لازم نعدّل الاختيارات أو الميزانية قبل التأكيد.\n" if expensive else "")

    # Distinct perfumes, not lines: 50ml + 90ml of one perfume is still one perfume's spend, and
    # its total is a figure the stated per-bottle number can be compared to. Two different
    # perfumes cannot be.
    perfumes = {
        getattr(item.get("variant"), "product_id", None) for item in items_to_create
    }

    parts = []
    if over:
        parts.append(
            "\n⚠️ للعلم: "
            + "، ".join(over)
            + f" — أعلى من الميزانية اللي قلتها ({int(budget)} جنيه). "
            "لو مش مقصود، قولي وأشيله.\n"
        )
    elif budget_tier(total, budget) == "far":
        # Only when no single line was already flagged: otherwise the customer is told the
        # same thing twice in one summary.
        if len(perfumes) > 1:
            parts.append(
                f"\n⚠️ للعلم: إجمالي الطلب {total:.0f} جنيه. الرقم اللي قلته "
                f"({int(budget)} جنيه). الطلب فيه أكتر من عطر — فالإجمالي أعلى "
                f"من الميزانية دي، مش عشان عطر فيهم غالي. لو مش مقصود، أقدر أشيل حاجة أو أنزل "
                f"حجم أصغر.\n"
            )
        else:
            parts.append(
                f"\n⚠️ للعلم: إجمالي الطلب {total:.0f} جنيه، أعلى من الميزانية اللي قلتها "
                f"({int(budget)} جنيه). لو مش مقصود، أقدر أشيل حاجة أو أنزل حجم أصغر.\n"
            )

    return "".join(parts)


def _save_cart_details(cart, name, phone, secondary_phone, address):
    """Persist whichever customer details are known so far.

    Called before validation so a name given early in a long conversation is kept
    even when the turn ends in a question about something else.
    """
    cart.customer_name = name or ""
    cart.customer_phone = phone or ""
    cart.secondary_phone = secondary_phone or ""
    cart.shipping_address = address or ""
    cart.save(update_fields=[
        "customer_name", "customer_phone", "secondary_phone",
        "shipping_address", "updated_at",
    ])


def _save_cart_items(cart, items_to_create, pending_product=None):
    """Replace the cart's items with the freshly resolved ones.

    Only fully resolved items can be stored, since CartItem requires a variant. A
    perfume the customer has named but not sized is recorded in `pending_product`
    instead — it used to be held only by the conversation history, which the extractor
    is forbidden to read back once the cart is empty, so it was lost on the next turn.
    """
    cart.items.exclude(variant_id__in=[item["variant"].pk for item in items_to_create]).delete()
    for item in items_to_create:
        CartItem.objects.update_or_create(
            cart=cart,
            variant=item["variant"],
            bottle_type=item["bottle_type"],
            defaults={"quantity": item["quantity"], "line_id": item.get("line_id") or uuid.uuid4().hex},
        )
    if cart.pending_product_id != (pending_product.id if pending_product else None):
        cart.pending_product = pending_product
        cart.save(update_fields=["pending_product", "updated_at"])


def _cart_items_as_products_data(cart):
    """The saved cart in the shape the extractor would have returned.

    Used as a fallback when the model returns an empty product list despite a
    saved cart existing — losing a cart to one bad extraction is the exact
    failure this whole change exists to prevent.
    """
    return [
        {
            "name": item.variant.product.name,
            "line_id": item.line_id or str(item.pk),
            "quantity": item.quantity,
            "volume": item.variant.volume,
            "bottle_type": item.bottle_type,
        }
        for item in cart.items.select_related('variant__product').all()
    ]



def restore_stock(order):
    """Return original bottles to stock for a cancelled order.

    Only originals hold stock. A brand bottle is compounded to order, so cancelling one
    consumes nothing and there is nothing to give back — the oil ledger this used to
    credit is gone.

    Should be called inside a transaction.
    """
    for item in order.items.select_related('variant__product').all():
        if item.bottle_type == "original":
            ProductVariant.objects.filter(id=item.variant_id).update(
                stock=F('stock') + item.quantity
            )
    logger.info(f"Stock restored for cancelled order #{order.id}")

def _offered_context(conversation, store):
    """The perfumes we just put in front of the customer, so a reference can resolve.

    The extractor is told an ordinal means "the perfume you named in your previous reply", but
    the reply reaches it only through a truncated history — nothing structured. So "تمام هاخد ده"
    could not be resolved and the customer was asked which perfume they meant, twice, after the
    bot had just named two (evaluation scenario F1).

    Thin wrapper over `sales.described.offered_context_block`, which `product_resolver` needs
    for the same reason. Kept as a name here because this is where the order extractor reads it.
    """
    from .sales import described as sales_described

    return sales_described.offered_context_block(conversation, store)


_WHICH_PERFUME = "تمام، بس مش واضحلي عايز تطلب أنهي عطر. ممكن تقولي اسم العطر اللي عايزه؟"


def _ask_which_perfume(conversation, store):
    """Ask which perfume they mean — and do not ask the same way twice.

    This literal went out byte-for-byte on two consecutive turns in evaluation scenario F1:
    "تمام هاخد ده" could not be resolved, and neither could "خليه 90 ملي بدل الـ50", so the
    customer answered a question and received the identical question back. The order branch
    does not pass through `_is_repetitive`, and a retry would not have helped anyway — this is
    a scripted reply, not model output.

    On a repeat, name the perfumes we actually offered instead. That is both a different
    sentence and a genuinely more useful one: it turns an open question into a choice.
    """
    from .sales import described as sales_described

    previous = (
        conversation.messages.filter(role="assistant", delivery_status="sent")
        .order_by("-created_at")
        .values_list("content", flat=True)
        .first()
        if conversation is not None else None
    )
    if (previous or "").strip() != _WHICH_PERFUME:
        return _WHICH_PERFUME

    try:
        offered = sales_described.offered_in_order(conversation, store)
    except Exception:
        offered = []
    if not offered:
        return "معلش، أنا مش لاقي العطر. ممكن تكتبلي اسمه وأنا أجيبلك سعره والأحجام؟"

    if len(offered) == 1:
        return f"تقصد {offered[0]}؟ لو أيوة قولي الحجم وأجهزلك الطلب."
    names = " ولا ".join(offered[:3])
    return f"تقصد {names}؟ قولي أنهي واحد والحجم وأجهزلك الطلب."


_DETAILS_ASK_MARKER = "عشان أأكدلك الطلب ناقصني"


def _already_asked_for_details(conversation):
    """Did our previous reply already ask for the personal details?"""
    if conversation is None:
        return False
    previous = (
        conversation.messages.filter(role="assistant", delivery_status="sent")
        .order_by("-created_at")
        .values_list("content", flat=True)
        .first()
    )
    return _DETAILS_ASK_MARKER in (previous or "")


def _short_missing(name, phone, secondary_phone, address):
    """The same list of missing fields, without repeating the long-form instructions."""
    parts = []
    if not name:
        parts.append("الاسم")
    if not phone:
        parts.append("رقم الموبايل")
    if not secondary_phone:
        parts.append("رقم بديل")
    if not address:
        parts.append("العنوان")
    return " و".join(parts) or "التفاصيل"


def _cart_recap(conversation, items_to_create, total_price):
    """One line naming what is in the cart and what it comes to, above a details request.

    Three reasons it exists, all from evaluation scenario F1, where the details request went out
    byte-for-byte on three consecutive turns:

      * The customer said "خليه 90 ملي بدل الـ50" and got the identical reply. The cart HAD
        changed; the reply just never said so, so the change was invisible and unconfirmable.
      * The customer asked "الاجمالي بقى كام؟" and got the identical reply again — the question
        went unanswered while the cart sat there holding the answer.
      * A running total is where the over-budget warning belongs earliest. Waiting for the full
        summary means the customer only learns they are over budget after handing over their
        name, phone and address.

    Deliberately NOT using CONFIRMATION_SUMMARY_MARKER ("💰 الإجمالي:"). _summary_was_shown greps
    the saved replies for that string to decide whether a bare "تمام" may confirm an order, so
    emitting it here would let a details request authorise a confirmation the customer was never
    properly shown.
    """
    if not items_to_create:
        return ""

    lines = "\n".join(
        f"- {item['quantity']} × {item['variant'].product.name} "
        f"({item['variant'].volume}ml) بـ {item['price'] * item['quantity']:.0f} جنيه"
        for item in items_to_create
    )
    recap = f"🛍️ الطلب لحد دلوقتي:\n{lines}\nالمجموع: {total_price:.0f} جنيه.\n"
    recap += _over_budget_warning(conversation, items_to_create)
    return recap + "\n"


@_serialized
def handle_order(message, history, store, conversation):
    """
    Handles the order collection flow. Extracts Name, Phone, Address, Products, Quantities, and Confirmation.
    """
    from .static_faq_service import normalize_arabic
    if not Cart.objects.filter(conversation=conversation).exists() and normalize_arabic(message).strip(" .!؟") in ("تمام", "تمام اكد الطلب", "اكد الطلب", "ايوه", "اكد"):
        previous = Order.objects.filter(conversation=conversation, checkout_token__isnull=False).order_by("-id").first()
        if previous:
            return _order_success(previous), ""
    cart = get_cart(conversation)

    from .sales.constraints import explicit_updates
    from .conversation_service import merge_preferences
    updates = explicit_updates(message)
    if updates:
        merge_preferences(conversation, updates, message)

    prompt = """
You are an order detail extractor for an Arabic perfume store.

A "SAVED CART" section below holds the order as it currently stands. It is the
authoritative state — the conversation history is truncated and may not show
everything the customer already told us. Start from the saved cart and apply the
customer's LATEST message to it.

Rules:
1. "customer_name": The customer's name. Use the saved value unless the latest message gives a new one.
2. "customer_phone": The customer's primary phone. Use the saved value unless the latest message gives a new one.
3. "shipping_address": The customer's full delivery address. Use the saved value unless the latest message gives a new one.
4. "customer_secondary_phone": The alternative phone. Use the saved value unless the latest message gives a new one.
5. "products": The FULL list of products in the cart AFTER applying the latest message:
   - Customer adds a perfume → the saved items PLUS the new one.
   - Customer changes the size or bottle type of something already saved → return that perfume ONCE with the new size/type, do NOT duplicate it.
   - Customer removes a perfume → the saved items WITHOUT it.
   - Customer says nothing about products (just "تمام", or gives their phone/address) → return the saved items UNCHANGED.
   - Saved cart is empty and no perfume named yet → return [].
   🔴 POSITIONAL REFERENCE: "اول واحد" / "الأول" means the FIRST perfume in the "PERFUMES YOU
     JUST OFFERED" list below; "التاني" the second; "الأخير" the last. One ordinal means exactly
     ONE perfume — return that one only. "هات 90 ملي من اول واحد ده" put BOTH perfumes from the
     previous reply in the cart, including one 185 جنيه over the customer's stated budget.
   🔴 DEMONSTRATIVE REFERENCE: a bare "ده" / "دي" / "ديت" / "الاولاني" with no name, or "هاخد ده"
     / "عايز ده" / "خليه" / "نفسه", points at the FIRST entry in that list — the one you led with.
     "وضيف كمان واحد" / "واحد تاني" after it means a SECOND unit of that same perfume unless they
     name a different one. If the list below is empty you genuinely cannot tell, and only then
     should you ask which perfume they mean.
   🔴 A leading "ماشي" / "تمام" / "اوك" followed by a specific request is a REQUEST, not a
     blanket yes to everything on the table. "ماشي هات كذا" = they want كذا. Do not read it as
     accepting every perfume you had just listed.
   🚨 CRITICAL: an empty saved cart means any order visible in the history is ALREADY CLOSED and paid for. Do NOT pull products out of the history to refill it. Return [] unless the customer names a perfume in their LATEST message OR points at one with a demonstrative/ordinal, in which case resolve it against the "PERFUMES YOU JUST OFFERED" list below — that list is the perfumes of the CURRENT conversation, not of a finished order.
   For each product extract "bottle_type" ("original" for أوريجينال, "normal" for زجاجة البراند/تركيب/زجاجة الاستور/زجاجة المحل).
   CRITICAL: if the customer has not chosen a bottle type and none is saved, return null for it.
6. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). ALSO, if the assistant asked "ولا في حاجة حابب تعدلها؟" and the user replies with "لا", "لا شكرا", or "لا تمام" (meaning they don't want to modify), this is a confirmation to proceed, so return true. Otherwise, return false.
7. "cart_cleared": true ONLY IF the customer's latest message asks to remove or drop product(s) AND that leaves the cart EMPTY (e.g. the saved cart held one perfume and they said "شيله" or "مش عايزه"). If they removed one perfume out of several, return false and simply omit that perfume from "products". If they said nothing about removing anything, return false.
   ⚠️ This matters: an empty "products" list normally means the extractor lost track, and the saved cart is restored. "cart_cleared": true is how you say the cart is empty ON PURPOSE.

Carry each saved line_id unchanged. A changed size or quantity updates that line_id.
Never remove a saved line by omission: put explicitly removed line IDs in removed_line_ids.
Preserve unresolved names and all unsized quantities. An edit plus confirmation is NOT approval
of a new total. is_confirmed and cart_cleared must be JSON booleans, never strings.
Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "customer_secondary_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer, "bottle_type": null or "normal" or "original"}
    ],
    "removed_line_ids": [],
    "is_confirmed": false,
    "cart_cleared": false
}
""" + _cart_context(cart) + _offered_context(conversation, store)

    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    try:
        response = chat(messages, profile="reason", response_format={"type": "json_object"})
        data = json.loads(response)
    except Exception:
        return "مش فاهم تفاصيل الطلب كويس يا فندم. ممكن تقولي تاني عايز تطلب ايه بالظبط؟", ""

    if not isinstance(data, dict):
        return "ممكن توضح تفاصيل الطلب؟ الطلب المحفوظ زي ما هو.", ""
    try:
        for key in ("is_confirmed", "cart_cleared"):
            if type(data.get(key, False)) is not bool:
                raise ValueError("Invalid boolean")
        for key in ("customer_name", "customer_phone", "customer_secondary_phone", "shipping_address"):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise ValueError("Invalid contact field")
        products_data = _merge_lines(cart, data)
    except ValueError:
        return "ممكن توضح تفاصيل الطلب؟ الطلب المحفوظ زي ما هو ومش هيتأكد دلوقتي.", ""
    name = data.get("customer_name") or cart.customer_name or None
    phone = data.get("customer_phone") or cart.customer_phone or None
    secondary_phone = data.get("customer_secondary_phone") or cart.secondary_phone or None
    address = data.get("shipping_address") or cart.shipping_address or None
    is_confirmed = data.get("is_confirmed") is True
    _save_cart_details(cart, name, phone, secondary_phone, address)
    if data.get("cart_cleared") is True:
        clear_cart(conversation, keep_details=True)
        return "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟", ""
    if not products_data:
        if data.get("removed_line_ids"):
            clear_cart(conversation, keep_details=True)
            return "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟", ""
        return _ask_which_perfume(conversation, store), ""
    # Save the complete requested set before resolving individual lines. An unavailable
    # size must not discard the rest of the customer's request.
    cart.pending_items = products_data
    cart.save(update_fields=["pending_items"])
    # 1. Resolve products first to check stock and prices BEFORE asking for user info
    total_price = 0
    items_to_create = []
    context_data = []
    
    issues = []
    for p_data in products_data:
        try:
            if not isinstance(p_data, dict):
                continue
            p_name = p_data.get('name')
            qty = p_data.get('quantity', 1)
            req_volume = p_data.get('volume')
            if not p_name or not isinstance(p_name, str):
                continue
            try:
                qty = int(qty)
            except (ValueError, TypeError):
                qty = 1
            product = Product.objects.prefetch_related('variants').filter(store=store, name__iexact=p_name, is_active=True).first()
            if not product:
                product = resolve_product(message=p_name, store=store)
            if product:
                p_data['name'] = product.name
                p_data['product_obj'] = product
                variants = list(product.variants.all())
                if not variants:
                    raise _LineProblem((f'{product.name}: مفيش أحجام متاحة حالياً.', ''))
                bottle_type = p_data.get('bottle_type')
                available_normal = [v for v in variants if v.bottle_type == 'normal']
                available_original = [v for v in variants if v.bottle_type == 'original' and (v.stock or 0) > 0]
                if req_volume and (not bottle_type):
                    try:
                        vol = int(req_volume)
                        has_normal_vol = any((v.volume == vol for v in available_normal))
                        has_original_vol = any((v.volume == vol for v in available_original))
                        if has_normal_vol and (not has_original_vol):
                            bottle_type = 'normal'
                            p_data['bottle_type'] = 'normal'
                        elif has_original_vol and (not has_normal_vol):
                            bottle_type = 'original'
                            p_data['bottle_type'] = 'original'
                    except (ValueError, TypeError):
                        pass
                if not bottle_type:
                    if product.brand.name.lower() == store.name.lower():
                        bottle_type = 'normal'
                        p_data['bottle_type'] = 'normal'
                    elif not available_original and available_normal:
                        bottle_type = 'normal'
                        p_data['bottle_type'] = 'normal'
                    elif not available_normal and available_original:
                        bottle_type = 'original'
                        p_data['bottle_type'] = 'original'
                    elif not available_normal and (not available_original):
                        raise _LineProblem((f'للأسف عطر {product.name} نفد من المخزون بجميع أحجامه حالياً 😔', ''))
                    else:
                        p_data['product_obj'] = product
                        p_data['missing_bottle_type'] = True
                        continue
                elif bottle_type == 'original':
                    has_original = any((v.bottle_type == 'original' for v in variants))
                    if not has_original:
                        is_custom_blend = bool(product.store and product.brand.name.lower() == product.store.name.lower())
                        if available_normal:
                            if is_custom_blend:
                                raise _LineProblem((f'عذراً يا فندم، عطر {product.name} من تصميمنا وابتكارنا ولا يوجد منه زجاجة أوريجينال. متوفر فقط في زجاجة البراند الخاصة بينا. تحب تطلبه؟', ''))
                            else:
                                raise _LineProblem((f'عذراً يا فندم، غير متوفر زجاجات أوريجينال لعطر {product.name} حالياً. متوفر منه فقط زجاجة البراند التركيب بتاعتنا. تحب تطلبه؟', ''))
                        else:
                            raise _LineProblem((f'عذراً يا فندم، عطر {product.name} نفد من المخزون حالياً 😔.', ''))
                    elif not available_original:
                        if available_normal:
                            raise _LineProblem((f'عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} نفدت من المخزون حالياً 😔. متوفر منه زجاجات البراند التركيب. تحب تطلب زجاجة البراند؟', ''))
                        else:
                            raise _LineProblem((f'عذراً يا فندم، عطر {product.name} نفد من المخزون تماماً 😔.', ''))
                elif bottle_type == 'normal' and (not available_normal):
                    if available_original:
                        raise _LineProblem((f'عذراً يا فندم، زجاجات البراند التركيب لعطر {product.name} غير متوفرة حالياً 😔. متوفر منه الزجاجة الأوريجينال. تحب تطلبها؟', ''))
                if bottle_type == 'normal':
                    filtered_variants = available_normal
                elif bottle_type == 'original':
                    filtered_variants = available_original
                else:
                    filtered_variants = available_normal + available_original
                if req_volume:
                    try:
                        req_volume = int(req_volume)
                        selected_variant = next((v for v in filtered_variants if v.volume == req_volume), None)
                    except (ValueError, TypeError):
                        selected_variant = None
                else:
                    selected_variant = None
                if not selected_variant:
                    if not filtered_variants:
                        raise _LineProblem((f'للأسف عطر {product.name} نفد من المخزون حالياً 😔', ''))
                    if req_volume:
                        if bottle_type == 'original':
                            has_normal_vol = any((v.volume == req_volume for v in available_normal))
                            avail_orig_vols = '، '.join([f'{v.volume} ملي' for v in filtered_variants])
                            if has_normal_vol:
                                raise _LineProblem((f'عذراً يا فندم، الـ {req_volume} ملي من عطر {product.name} متاح في زجاجات البراند الخاصة بينا فقط وليس الأوريجينال. (الأوريجينال متاح منه: {avail_orig_vols}). تحب تطلب زجاجة البراند؟', ''))
                            else:
                                raise _LineProblem((f'عذراً يا فندم، حجم {req_volume} ملي غير متوفر من الزجاجات الأوريجينال لعطر {product.name}. (المتاح: {avail_orig_vols}). تحب تطلب حجم تاني؟', ''))
                        elif bottle_type == 'normal':
                            has_orig_vol = any((v.volume == req_volume for v in available_original))
                            avail_normal_vols = '، '.join([f'{v.volume} ملي' for v in filtered_variants])
                            if has_orig_vol:
                                raise _LineProblem((f'عذراً يا فندم، الـ {req_volume} ملي من عطر {product.name} متاح كزجاجة أوريجينال فقط حالياً. (زجاجات البراند المتاح منها: {avail_normal_vols}). تحب تطلب الأوريجينال؟', ''))
                            else:
                                raise _LineProblem((f'عذراً يا فندم، حجم {req_volume} ملي غير متوفر من زجاجات البراند لعطر {product.name}. (المتاح: {avail_normal_vols}). تحب تطلب حجم تاني؟', ''))
                        else:
                            avail_vols_display = []
                            for v in filtered_variants:
                                if v.bottle_type == 'original':
                                    avail_vols_display.append(f'{v.volume} ملي (زجاجة أوريجينال)')
                                else:
                                    avail_vols_display.append(f'{v.volume} ملي (زجاجة البراند)')
                            vols_str = '، '.join(avail_vols_display)
                            raise _LineProblem((f'عذراً يا فندم، حجم {req_volume} ملي غير متوفر حالياً لعطر {product.name}. المتاح: {vols_str}. تحب تطلب حاجة منهم؟', ''))
                    all_type_variants = [v for v in variants if v.bottle_type == bottle_type] if bottle_type else variants
                    if len(filtered_variants) == 1 and len(all_type_variants) == 1:
                        selected_variant = filtered_variants[0]
                    else:
                        p_data['product_obj'] = product
                        avail_vols_display = []
                        for v in filtered_variants:
                            if v.bottle_type == 'original':
                                avail_vols_display.append(f'{v.volume} ملي (زجاجة أوريجينال)')
                            else:
                                avail_vols_display.append(f'{v.volume} ملي')
                        p_data['available_volumes_display'] = avail_vols_display
                        continue
                if bottle_type == 'original':
                    stock = selected_variant.stock or 0
                    if stock == 0:
                        raise _LineProblem((f'عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} حجم {selected_variant.volume} ملي نفدت تماماً.', ''))
                    elif stock < qty:
                        raise _LineProblem((f'عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} المتوفرة حالياً {stock} زجاجة فقط من حجم {selected_variant.volume} ملي. تحب تطلب {stock} بس؟', ''))
                price = selected_variant.price
                total_price += price * qty
                items_to_create.append({'variant': selected_variant, 'quantity': qty, 'price': price, 'bottle_type': bottle_type, 'line_id': p_data['line_id']})
                p_data['_resolved'] = True
                p_data['name'] = product.name
                bottle_text = ' (زجاجة أوريجينال)' if bottle_type == 'original' else ' (زجاجة البراند)'
                context_data.append(f'{product.name} ({selected_variant.volume} ملي){bottle_text} x {qty} ({price * qty} EGP)')
        except _LineProblem as problem:
            issues.append(problem.args[0])
    context_str = ", ".join(context_data) if context_data else "No products found"

    combined = {}
    for item in items_to_create:
        pk = item["variant"].pk
        if pk in combined:
            combined[pk]["quantity"] += item["quantity"]
        else:
            combined[pk] = dict(item)
    items_to_create = list(combined.values())
    pending_lines = [{k: v for k, v in p.items() if k in ("name", "line_id", "quantity", "volume", "bottle_type")}
                     for p in products_data if not p.get("_resolved")]
    with transaction.atomic():
        _save_cart_items(cart, items_to_create)
        cart.pending_items = pending_lines
        cart.save(update_fields=["pending_items"])
    unknown = [p for p in products_data if not p.get("_resolved") and not p.get("product_obj")]
    if issues or unknown:
        details = [issue[0] for issue in issues]
        if unknown:
            details.append("لسه محتاج أتأكد من: " + "، ".join(p["name"] for p in unknown) + ". الاختيارات دي محفوظة ومش هأكد الطلب قبل ما نحددها أو تشيلها.")
        return _cart_recap(conversation, items_to_create, total_price) + "\n".join(details), context_str

    # 2. Check for missing product details FIRST (size, bottle type, quantity)
    product_missing_fields = []
    for p in products_data:
        if not isinstance(p, dict): continue
        
        missing_for_this_product = []
        if "product_obj" in p:
            if "available_volumes_display" in p:
                vols = "، ".join(p["available_volumes_display"])
                missing_for_this_product.append(f"الحجم المطلوب (متاح: {vols})")
            if p.get("missing_bottle_type"):
                missing_for_this_product.append("نوع الزجاجة (أوريجينال أم زجاجة البراند؟)")
            
        # Check quantities for products
        # A missing quantity defaults to one bottle rather than blocking the turn.
        # "عايز اطلب امبيرو 90 ملي" plainly means one, and answering it with "محتاج أعرف
        # كمية الزجاجات المطلوبة" is friction a salesperson would never add. Only an
        # explicit larger quantity changes it, and the summary shows the count before
        # anything is confirmed, so a customer who meant two can still correct it.
        if not p.get("quantity"):
            p["quantity"] = 1
            
        if missing_for_this_product:
            joined_missing = " و ".join(missing_for_this_product)
            product_missing_fields.append(f"{joined_missing} من عطر {p.get('name')}")

    if product_missing_fields:
        recap = _cart_recap(conversation, items_to_create, total_price)
        missing_text = " ولا ".join(product_missing_fields) if len(product_missing_fields) == 1 else " و ".join(product_missing_fields)
        # If it's just one product and they're missing size, ask with the perfume's REAL
        # sizes and acknowledge whatever else they just told us. The old line was a
        # hardcoded "تحب الـ50 ملي ولا الـ90 ملي؟" — wrong for any perfume stocked in
        # other sizes, and it repeated itself verbatim when the customer answered with
        # something else ("خليها 2 بدل واحدة" got the identical question back).
        if len(product_missing_fields) == 1 and "الحجم" in product_missing_fields[0]:
            pending = next(
                (p for p in products_data if isinstance(p, dict) and "available_volumes_display" in p),
                None,
            )
            if pending:
                sizes = " ولا ".join(pending["available_volumes_display"])
                quantity = pending.get("quantity") or 1
                count = f"{quantity} × " if quantity > 1 else ""
                return recap + f"تمام 👌 {count}{pending.get('name')} — تحب {sizes}؟", context_str
            return recap + f"تمام 👌 بس محتاج أعرف {missing_text}؟", context_str
        return recap + f"تمام 👌 بس محتاج أعرف {missing_text}؟", context_str

    # 3. Product details are complete — now check for missing personal info.
    # Phones go through _looks_like_phone rather than a truthiness check: the
    # extractor is a language model, and a non-numeric string it invented or
    # echoed must count as missing, not as a contact number.
    phone = phone if _looks_like_phone(phone) else None
    secondary_phone = secondary_phone if _looks_like_phone(secondary_phone) else None

    personal_missing_fields = []
    if not name:
        personal_missing_fields.append("الاسم")

    if not phone and not secondary_phone:
        personal_missing_fields.append("رقمين للموبايل واحد اساسي وواحد بديل")
    elif not phone:
        personal_missing_fields.append("رقم الموبايل الأساسي")
    elif not secondary_phone:
        personal_missing_fields.append("رقم موبايل بديل")
        
    if not address:
        personal_missing_fields.append("عنوانك بالتفصيل (المحافظة - المنطقة - رقم المنزل - اسم الشارع ) لو فى أي علامة مميزة بجوار المنزل")

    if personal_missing_fields:
        missing_text = " و ".join(personal_missing_fields)
        ask = f"تمام، عشان أأكدلك الطلب ناقصني بس {missing_text}."
        # Asking for the same fields a second time in a row gets the short form. The long
        # parenthesised address prompt going out verbatim on consecutive turns is what made
        # three replies identical in evaluation scenario F1, and re-reading the same
        # instruction is not what a customer who just answered something else needs.
        if _already_asked_for_details(conversation):
            ask = f"ولسه ناقص {_short_missing(name, phone, secondary_phone, address)}."
        return _cart_recap(conversation, items_to_create, total_price) + ask, context_str

    # A true is_confirmed only counts if the customer was actually shown the total
    # first. Without this, one spurious true creates the order and moves stock on a
    # turn where no summary was ever sent.
    strict_budget = (conversation.preferences or {}).get("budget_strict") is True or (conversation.preferences or {}).get("budget_scope") == "total"
    if strict_budget and _over_budget_warning(conversation, items_to_create):
        cart.quote = {}
        cart.save(update_fields=["quote"])
        return _cart_recap(conversation, items_to_create, total_price), context_str
    if not is_confirmed or not _summary_was_shown(conversation, cart, items_to_create):
        return _quote_summary(conversation, cart, items_to_create), context_str

    # All details collected and confirmed! Let's process the order.
    return create_order_in_db(store, name, phone, secondary_phone, address, total_price, items_to_create, context_str, conversation, quote_token=cart.quote.get("token"))


def _order_success(order):
    return (f"تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #{order.id}.\n"
            f"سيقوم فريق المبيعات بالتواصل معك قريباً.\n\n{_payment_instructions(order.store)}")


def deliver_order_notification(order_id):
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            if not order.notification_pending:
                return
            notify_new_order(order)
            order.notification_pending = False
            order.save(update_fields=["notification_pending"])
    except Exception:
        logger.exception("Order %s committed; notification remains pending", order_id)


def change_order_status(order_id, store, new_status, *, bot_notes=None):
    if new_status not in dict(Order.STATUS_CHOICES):
        raise ValueError("Invalid status")
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id, store=store)
        old_status = order.status
        if old_status == "cancelled" and new_status != "cancelled":
            raise ValueError("الطلب الملغي مينفعش يتفتح تاني. اعمل طلب جديد.")
        if old_status == new_status:
            return order
        order.status = new_status
        if bot_notes is not None:
            order.bot_notes = bot_notes
        order.save(update_fields=["status", "bot_notes"])
        if new_status == "cancelled":
            restore_stock(order)
        return order


def create_order_in_db(store, name, phone, secondary_phone, address, total_price, items_to_create, context_str, conversation, quote_token=None):
    try:
        with transaction.atomic():
            Conversation.objects.select_for_update().get(pk=conversation.pk)
            if quote_token:
                existing = Order.objects.filter(checkout_token=quote_token, store=store).first()
                if existing:
                    return _order_success(existing), context_str
            cart = Cart.objects.select_for_update().filter(conversation=conversation).first()
            if not cart or not quote_token or cart.quote.get("token") != quote_token:
                return "محتاج أعرض ملخص الطلب الحالي الأول قبل التأكيد.", context_str
            locked = {v.pk: v for v in ProductVariant.objects.select_for_update().filter(
                pk__in=[item["variant"].pk for item in items_to_create]).select_related("product").order_by("pk")}
            quantities = {}
            for item in items_to_create:
                variant = locked.get(item["variant"].pk)
                if not variant or not variant.product.is_active or variant.product.store_id != store.pk:
                    cart.quote = {}
                    cart.save(update_fields=["quote"])
                    return "توفر أحد الاختيارات اتغير. خلينا نراجع الطلب قبل التأكيد.", context_str
                if type(item["quantity"]) is not int or item["quantity"] <= 0:
                    raise ValueError("Invalid quantity")
                item["variant"] = variant
                item["price"] = variant.price
                quantities[variant.pk] = quantities.get(variant.pk, 0) + item["quantity"]
            if not _summary_was_shown(conversation, cart, items_to_create):
                return _quote_summary(conversation, cart, items_to_create), context_str
            for pk, quantity in quantities.items():
                variant = locked[pk]
                if variant.bottle_type == "original" and (variant.stock or 0) < quantity:
                    cart.quote = {}
                    cart.save(update_fields=["quote"])
                    return f"للأسف الكمية المطلوبة من {variant.product.name} مش متاحة حالياً. نراجع الكمية؟", context_str
            total_price = sum((item["price"] * item["quantity"] for item in items_to_create), Decimal("0"))
            order = Order.objects.create(store=store, customer_name=name, customer_phone=phone,
                secondary_phone=secondary_phone, shipping_address=address, total_price=total_price,
                status="pending", conversation=conversation, checkout_token=quote_token, notification_pending=True)
            for item in items_to_create:
                OrderItem.objects.create(order=order, variant=item["variant"], quantity=item["quantity"],
                    bottle_type=item["variant"].bottle_type, price_at_time_of_order=item["price"])
            for pk, quantity in quantities.items():
                if locked[pk].bottle_type == "original":
                    ProductVariant.objects.filter(pk=pk).update(stock=F("stock") - quantity)
            clear_cart(conversation)
            transaction.on_commit(lambda: deliver_order_notification(order.pk), robust=True)
        return _order_success(order), context_str
    except Exception:
        logger.exception("Failed to create order for store %s", store.pk)
        return "حصل مشكلة في تسجيل الطلب يا فندم. ممكن تجرب تاني ولو المشكلة استمرت هحولك لحد من الفريق يساعدك.", ""
