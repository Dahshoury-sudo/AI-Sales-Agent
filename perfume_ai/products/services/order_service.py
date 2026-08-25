import json
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import F, Q
from products.models import Cart, CartItem, Order, OrderItem, Product, ProductVariant
from .ai.client import chat
from .fallback import suggest_alternatives
from .product_resolver import resolve_product
from .notification_service import notify_new_order

logger = logging.getLogger(__name__)


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


def _summary_was_shown(conversation, cart):
    """Has this cart's order summary, including the total, already gone out?

    `is_confirmed` comes back from a language model, and the extractor runs on a
    reasoning model at its default temperature (it rejects temperature 0), so the
    same conversation can yield true on one run and false on the next. Creating an
    Order and decrementing stock is too consequential to rest on that. The prompt's
    own rule is that confirmation requires having summarised with the total price,
    and unlike most prompt rules that one is checkable here.

    Scoped to messages at or after cart.created_at: clear_cart drops the row when an
    order completes and get_cart makes a fresh one, so a previous order's summary
    cannot authorise this one.
    """
    if conversation is None:
        return False

    return conversation.messages.filter(
        role="assistant",
        content__contains=CONFIRMATION_SUMMARY_MARKER,
        created_at__gte=cart.created_at,
    ).exists()


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
        "products": items,
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
    """
    try:
        budget = conversation.preferences.get("max_price") if conversation else None
    except Exception:
        budget = None
    if not budget:
        return ""

    try:
        budget = Decimal(str(budget))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if budget <= 0:
        return ""

    def _line_total(item):
        try:
            return Decimal(str(item["price"])) * int(item.get("quantity") or 1)
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    over = [
        f"{item['variant'].product.name} ({item['variant'].volume} ملي) بـ {_line_total(item):.0f}"
        for item in items_to_create
        if _line_total(item) > budget
    ]
    total = sum((_line_total(item) for item in items_to_create), Decimal("0"))

    parts = []
    if over:
        parts.append(
            "\n⚠️ للعلم: "
            + "، ".join(over)
            + f" — أعلى من الميزانية اللي قلتها ({int(budget)} جنيه). "
            "لو مش مقصود، قولي وأشيله.\n"
        )
    elif total > budget:
        # Only when no single line was already flagged: otherwise the customer is told the
        # same thing twice in one summary.
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
    cart.items.all().delete()
    for item in items_to_create:
        CartItem.objects.update_or_create(
            cart=cart,
            variant=item["variant"],
            bottle_type=item["bottle_type"],
            defaults={"quantity": item["quantity"]},
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

    Derived from `Message.internal_context` via sales.described, not scraped from prose, so the
    anti-hallucination rule above is not weakened: every name here is one we demonstrably had
    real data for when we said it.
    """
    from .sales import described as sales_described

    try:
        offered = sales_described.offered_in_order(conversation, store)
    except Exception:
        offered = []
    if not offered:
        return ""

    lines = "\n".join(f"{index}. {name}" for index, name in enumerate(offered, start=1))
    return (
        "\n═══ PERFUMES YOU JUST OFFERED (in the order you named them) ═══\n"
        f"{lines}\n"
        "Resolve \"ده\" / \"اول واحد\" / \"التاني\" against this list. Entry 1 is what you led with.\n"
    )


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
        conversation.messages.filter(role="assistant")
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
        conversation.messages.filter(role="assistant")
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


def handle_order(message, history, store, conversation):
    """
    Handles the order collection flow. Extracts Name, Phone, Address, Products, Quantities, and Confirmation.
    """
    cart = get_cart(conversation)

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

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "customer_secondary_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer, "bottle_type": null or "normal" or "original"}
    ],
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

    # Fall back to the saved cart for anything the extractor left out, so a detail
    # given earlier in a long conversation survives a turn that doesn't repeat it.
    name = data.get("customer_name") or cart.customer_name or None
    phone = data.get("customer_phone") or cart.customer_phone or None
    secondary_phone = data.get("customer_secondary_phone") or cart.secondary_phone or None
    address = data.get("shipping_address") or cart.shipping_address or None
    products_data = data.get("products") or []
    is_confirmed = data.get("is_confirmed", False)
    cart_cleared = bool(data.get("cart_cleared"))

    _save_cart_details(cart, name, phone, secondary_phone, address)

    if cart_cleared:
        # The customer emptied the cart on purpose, so the restore below must not
        # put it back. Without this flag an empty product list was always read as
        # "the extractor lost track", and removing your only perfume re-added it.
        removed = cart.items.count()
        cart.items.all().delete()
        products_data = []
        if removed:
            logger.info(
                f"Cart cleared on request for conversation #{conversation.id} "
                f"({removed} item(s) removed)."
            )
            return "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟", ""

    # A single bad extraction must not empty a cart the customer already built.
    if not products_data and cart.items.exists():
        products_data = _cart_items_as_products_data(cart)
        logger.warning(
            f"Extractor returned no products for conversation #{conversation.id}; "
            f"falling back to the {len(products_data)} saved cart item(s)."
        )

    # Same protection for a perfume that was chosen but never sized. It has no CartItem
    # to fall back on, so without this the turn spent asking for the size also loses the
    # perfume — which is exactly what "هاخد اودورا" then "خليها 2 بدل واحدة" did.
    if not products_data and cart.pending_product_id:
        products_data = [{
            "name": cart.pending_product.name,
            "quantity": None,
            "volume": None,
            "bottle_type": None,
        }]
        logger.info(
            f"Extractor returned no products for conversation #{conversation.id}; "
            f"restoring the pending selection '{cart.pending_product.name}'."
        )

    if not products_data:
        return _ask_which_perfume(conversation, store), ""

    # 1. Resolve products first to check stock and prices BEFORE asking for user info
    total_price = 0
    items_to_create = []
    context_data = []
    
    for p_data in products_data:
        if not isinstance(p_data, dict): continue
        p_name = p_data.get("name")
        qty = p_data.get("quantity", 1)
        req_volume = p_data.get("volume")
        
        if not p_name or not isinstance(p_name, str): continue
        try:
            qty = int(qty)
        except (ValueError, TypeError):
            qty = 1
            
        product = Product.objects.prefetch_related('variants').filter(store=store, name__iexact=p_name, is_active=True).first()
        if not product:
            product = resolve_product(message=p_name, store=store)
            
        if product:
            p_data["name"] = product.name
            variants = list(product.variants.all())
            if not variants:
                continue # Edge case: product has no variants
                
            bottle_type = p_data.get("bottle_type")
            
            # Filter available variants based on actual stock
            # Brand bottles are compounded to order, so every one of them is available.
            available_normal = [v for v in variants if v.bottle_type == "normal"]
            available_original = [v for v in variants if v.bottle_type == "original" and (v.stock or 0) > 0]
            
            # If req_volume is present, try to infer bottle_type if it uniquely belongs to one type
            if req_volume and not bottle_type:
                try:
                    vol = int(req_volume)
                    has_normal_vol = any(v.volume == vol for v in available_normal)
                    has_original_vol = any(v.volume == vol for v in available_original)
                    
                    if has_normal_vol and not has_original_vol:
                        bottle_type = "normal"
                        p_data["bottle_type"] = "normal"
                    elif has_original_vol and not has_normal_vol:
                        bottle_type = "original"
                        p_data["bottle_type"] = "original"
                except (ValueError, TypeError):
                    pass

            if not bottle_type:
                if product.brand.name.lower() == store.name.lower():
                    bottle_type = "normal"
                    p_data["bottle_type"] = "normal"
                elif not available_original and available_normal:
                    bottle_type = "normal"
                    p_data["bottle_type"] = "normal"
                elif not available_normal and available_original:
                    bottle_type = "original"
                    p_data["bottle_type"] = "original"
                elif not available_normal and not available_original:
                    return f"للأسف عطر {product.name} نفد من المخزون بجميع أحجامه حالياً 😔", ""
                else:
                    p_data["product_obj"] = product
                    p_data["missing_bottle_type"] = True
                    continue
            else:
                if bottle_type == "original":
                    has_original = any(v.bottle_type == "original" for v in variants)
                    if not has_original:
                        is_custom_blend = bool(product.store and product.brand.name.lower() == product.store.name.lower())
                        if available_normal:
                            if is_custom_blend:
                                return f"عذراً يا فندم، عطر {product.name} من تصميمنا وابتكارنا ولا يوجد منه زجاجة أوريجينال. متوفر فقط في زجاجة البراند الخاصة بينا. تحب تطلبه؟", ""
                            else:
                                return f"عذراً يا فندم، غير متوفر زجاجات أوريجينال لعطر {product.name} حالياً. متوفر منه فقط زجاجة البراند التركيب بتاعتنا. تحب تطلبه؟", ""
                        else:
                            return f"عذراً يا فندم، عطر {product.name} نفد من المخزون حالياً 😔.", ""
                    elif not available_original:
                        if available_normal:
                            return f"عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} نفدت من المخزون حالياً 😔. متوفر منه زجاجات البراند التركيب. تحب تطلب زجاجة البراند؟", ""
                        else:
                            return f"عذراً يا فندم، عطر {product.name} نفد من المخزون تماماً 😔.", ""
                elif bottle_type == "normal" and not available_normal:
                    if available_original:
                        return f"عذراً يا فندم، زجاجات البراند التركيب لعطر {product.name} غير متوفرة حالياً 😔. متوفر منه الزجاجة الأوريجينال. تحب تطلبها؟", ""


            # Now filter to only AVAILABLE variants of the selected bottle_type
            if bottle_type == "normal":
                filtered_variants = available_normal
            elif bottle_type == "original":
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
                    return f"للأسف عطر {product.name} نفد من المخزون حالياً 😔", ""
                
                # If the user explicitly asked for a volume but it wasn't found in the filtered variants
                if req_volume:
                    if bottle_type == "original":
                        has_normal_vol = any(v.volume == req_volume for v in available_normal)
                        avail_orig_vols = "، ".join([f"{v.volume} ملي" for v in filtered_variants])
                        if has_normal_vol:
                            return f"عذراً يا فندم، الـ {req_volume} ملي من عطر {product.name} متاح في زجاجات البراند الخاصة بينا فقط وليس الأوريجينال. (الأوريجينال متاح منه: {avail_orig_vols}). تحب تطلب زجاجة البراند؟", ""
                        else:
                            return f"عذراً يا فندم، حجم {req_volume} ملي غير متوفر من الزجاجات الأوريجينال لعطر {product.name}. (المتاح: {avail_orig_vols}). تحب تطلب حجم تاني؟", ""
                    elif bottle_type == "normal":
                        has_orig_vol = any(v.volume == req_volume for v in available_original)
                        avail_normal_vols = "، ".join([f"{v.volume} ملي" for v in filtered_variants])
                        if has_orig_vol:
                            return f"عذراً يا فندم، الـ {req_volume} ملي من عطر {product.name} متاح كزجاجة أوريجينال فقط حالياً. (زجاجات البراند المتاح منها: {avail_normal_vols}). تحب تطلب الأوريجينال؟", ""
                        else:
                            return f"عذراً يا فندم، حجم {req_volume} ملي غير متوفر من زجاجات البراند لعطر {product.name}. (المتاح: {avail_normal_vols}). تحب تطلب حجم تاني؟", ""
                    else:
                        avail_vols_display = []
                        for v in filtered_variants:
                            if v.bottle_type == "original":
                                avail_vols_display.append(f"{v.volume} ملي (زجاجة أوريجينال)")
                            else:
                                avail_vols_display.append(f"{v.volume} ملي (زجاجة البراند)")
                        vols_str = "، ".join(avail_vols_display)
                        return f"عذراً يا فندم، حجم {req_volume} ملي غير متوفر حالياً لعطر {product.name}. المتاح: {vols_str}. تحب تطلب حاجة منهم؟", ""

                # Only auto-select if no explicit volume was requested AND there's truly one variant total for this type.
                # If other sizes exist but are out of stock, always ask the customer.
                all_type_variants = [v for v in variants if v.bottle_type == bottle_type] if bottle_type else variants
                if len(filtered_variants) == 1 and len(all_type_variants) == 1:
                    selected_variant = filtered_variants[0]
                else:
                    p_data["product_obj"] = product
                    avail_vols_display = []
                    for v in filtered_variants:
                        if v.bottle_type == "original":
                            avail_vols_display.append(f"{v.volume} ملي (زجاجة أوريجينال)")
                        else:
                            avail_vols_display.append(f"{v.volume} ملي")
                    p_data["available_volumes_display"] = avail_vols_display
                    continue
            
            # Check stock availability for the selected variant. Brand bottles have no
            # stock to check — they are compounded to order, so the oil-insufficient and
            # max-quantity paths that used to live here are gone with the oil ledger.
            if bottle_type == "original":
                stock = selected_variant.stock or 0
                if stock == 0:
                    return f"عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} حجم {selected_variant.volume} ملي نفدت تماماً.", ""
                elif stock < qty:
                    return f"عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} المتوفرة حالياً {stock} زجاجة فقط من حجم {selected_variant.volume} ملي. تحب تطلب {stock} بس؟", ""

            price = selected_variant.price
            total_price += price * qty
            items_to_create.append({
                "variant": selected_variant,
                "quantity": qty,
                "price": price,
                "bottle_type": bottle_type
            })
            p_data["name"] = product.name
            bottle_text = " (زجاجة أوريجينال)" if bottle_type == "original" else " (زجاجة البراند)"
            context_data.append(f"{product.name} ({selected_variant.volume} ملي){bottle_text} x {qty} ({price * qty} EGP)")
            
    context_str = ", ".join(context_data) if context_data else "No products found"

    # Persist whatever resolved cleanly, before any of the early returns below.
    # Partial progress counts: if one perfume is settled and another still needs a
    # size, the settled one must survive the turn spent asking about the other — and so
    # must the unsettled one, which is what `pending` carries.
    pending = next(
        (
            item["product_obj"]
            for item in products_data
            if isinstance(item, dict) and item.get("product_obj") is not None
        ),
        None,
    )
    _save_cart_items(cart, items_to_create, pending_product=pending)

    # If the user asked for products but NONE were found in our store, stop immediately and suggest alternatives.
    if not items_to_create and all("product_obj" not in p for p in products_data if isinstance(p, dict)):
        # Check if the AI returned null for the name, which means the user's request was ambiguous (e.g. "عايز واحد")
        if any(not p.get("name") for p in products_data if isinstance(p, dict)):
            return "عذراً يا فندم، تقصد أنهي عطر فيهم بالظبط عشان أقدر أسجلهولك في الطلب؟", context_str
            
        alternatives = suggest_alternatives(store)
        if alternatives:
            alts_text = []
            for alt in alternatives:
                alts_text.append(f"• {alt.name} ({alt.brand.name})")
            alts_str = "\n".join(alts_text)
            return f"للأسف العطر ده مش متوفر عندنا يا فندم 😔\n\nبس عندنا عطور تانية مميزة ممكن تعجبك زي:\n{alts_str}\n\nتحب تعرف تفاصيل أكتر عن أي واحد فيهم؟", context_str
        
        return "مش لاقي العطر ده عندنا يا فندم. ممكن تقولي اسمه تاني أو تسأل عن عطر تاني؟", context_str

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
                return f"تمام 👌 {count}{pending.get('name')} — تحب {sizes}؟", context_str
            return f"تمام 👌 بس محتاج أعرف {missing_text}؟", context_str
        return f"تمام 👌 بس محتاج أعرف {missing_text}؟", context_str

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
    if not is_confirmed or not _summary_was_shown(conversation, cart):
        # Generate Summary
        summary = "تمام، راجع معايا تفاصيل الطلب كده:\n\n"
        summary += f"👤 الاسم: {name}\n"
        summary += f"📱 الموبايل: {phone}\n"
        if secondary_phone:
            summary += f"📞 موبايل بديل: {secondary_phone}\n"
        summary += f"📍 العنوان: {address}\n\n"
        summary += "🛍️ الطلب:\n"
        for item in items_to_create:
            bottle_disp = "أوريجينال" if item['bottle_type'] == "original" else "البراند"
            summary += f"- {item['quantity']} × {item['variant'].product.name} ({item['variant'].volume}ml) - زجاجة {bottle_disp} (السعر: {item['price'] * item['quantity']} جنيه)\n"
        summary += f"\n💰 الإجمالي: {total_price} جنيه.\n"
        # Between the total and the confirmation question on purpose: this is the moment the
        # customer is being asked to check, and the caveat is worthless after they have agreed.
        # It goes after the 💰 marker so _summary_was_shown still finds it.
        summary += _over_budget_warning(conversation, items_to_create)
        summary += "\nكل البيانات كده تمام ونأكد الطلب، ولا تحب تعدل حاجة؟"
        return summary, context_str

    # All details collected and confirmed! Let's process the order.
    return create_order_in_db(store, name, phone, secondary_phone, address, total_price, items_to_create, context_str, conversation)


def create_order_in_db(store, name, phone, secondary_phone, address, total_price, items_to_create, context_str, conversation):
    try:
        with transaction.atomic():
            # Re-validate stock inside the transaction with select_for_update to prevent
            # race conditions. Only originals need it: they are the one finite thing an
            # order consumes, so they are the one place two concurrent orders can oversell.
            for item in items_to_create:
                if item["bottle_type"] == "original":
                    locked_variant = ProductVariant.objects.select_for_update().get(id=item["variant"].id)
                    if (locked_variant.stock or 0) < item["quantity"]:
                        return f"للأسف الزجاجة الأوريجينال لعطر {item['variant'].product.name} نفدت أثناء تأكيد الطلب 😔 ممكن تجرب تاني.", ""

            order = Order.objects.create(
                store=store,
                customer_name=name,
                customer_phone=phone,
                secondary_phone=secondary_phone,
                shipping_address=address,
                total_price=total_price,
                status="pending",
                conversation=conversation
            )
            
            for item in items_to_create:
                OrderItem.objects.create(
                    order=order,
                    variant=item["variant"],
                    quantity=item["quantity"],
                    bottle_type=item["bottle_type"],
                    price_at_time_of_order=item["price"]
                )
                
                # Decrement stock atomically using F(). Originals only — a brand bottle is
                # compounded to order and consumes no tracked inventory.
                if item["bottle_type"] == "original":
                    ProductVariant.objects.filter(id=item["variant"].id).update(
                        stock=F('stock') - item["quantity"]
                    )
                
        logger.info(f"Order #{order.id} created successfully for store '{store.name}'")
        # The cart became an Order; drop it so a second order in this same
        # conversation starts from empty instead of re-ordering the first one.
        clear_cart(conversation)
        notify_new_order(order)  # Notify store owner in dashboard
        final_message = (
            f"تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #{order.id}.\n"
            f"سيقوم فريق المبيعات بالتواصل معك قريباً.\n\n"
            f"{_payment_instructions(store)}"
        )
        return final_message, context_str
    except Exception as e:
        logger.exception(f"Failed to create order for store '{store.name}': {e}")
        return "حصل مشكلة في تسجيل الطلب يا فندم. ممكن تجرب تاني ولو المشكلة استمرت هحولك لحد من الفريق يساعدك.", ""
