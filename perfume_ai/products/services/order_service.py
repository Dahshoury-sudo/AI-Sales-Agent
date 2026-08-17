import json
import logging
from django.db import transaction
from django.db.models import F, Q
from products.models import Cart, CartItem, Order, OrderItem, Product, ProductVariant
from .ai.client import chat
from .product_resolver import resolve_product
from .notification_service import notify_new_order

logger = logging.getLogger(__name__)


def get_cart(conversation):
    """The conversation's in-progress cart, created on first use."""
    cart, _ = Cart.objects.get_or_create(conversation=conversation)
    return cart


def clear_cart(conversation):
    """Drop the in-progress cart. No stock to restore — none was ever taken."""
    Cart.objects.filter(conversation=conversation).delete()


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


def _save_cart_items(cart, items_to_create):
    """Replace the cart's items with the freshly resolved ones.

    Only fully resolved items can be stored, since CartItem requires a variant. A
    perfume the customer named but hasn't picked a size for yet is still carried
    by the conversation for the one turn it takes to ask.
    """
    cart.items.all().delete()
    for item in items_to_create:
        CartItem.objects.update_or_create(
            cart=cart,
            variant=item["variant"],
            bottle_type=item["bottle_type"],
            defaults={"quantity": item["quantity"]},
        )


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
    """
    Restore stock for all items in a cancelled order.
    Should be called inside a transaction.
    """
    for item in order.items.select_related('variant__product').all():
        if item.bottle_type == "original":
            ProductVariant.objects.filter(id=item.variant_id).update(
                stock=F('stock') + item.quantity
            )
        elif item.bottle_type == "normal":
            product = item.variant.product
            req_oil = int((item.variant.volume * product.concentration_percentage) / 100 * item.quantity)
            Product.objects.filter(id=product.id).update(
                oil_stock_grams=F('oil_stock_grams') + req_oil
            )
    logger.info(f"Stock restored for cancelled order #{order.id}")

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
   🚨 CRITICAL: an empty saved cart means any order visible in the history is ALREADY CLOSED and paid for. Do NOT pull products out of the history to refill it. Return [] unless the customer names a perfume in their LATEST message.
   For each product extract "bottle_type" ("original" for أوريجينال, "normal" for زجاجة البراند/تركيب/زجاجة الاستور/زجاجة المحل).
   CRITICAL: if the customer has not chosen a bottle type and none is saved, return null for it.
6. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). ALSO, if the assistant asked "ولا في حاجة حابب تعدلها؟" and the user replies with "لا", "لا شكرا", or "لا تمام" (meaning they don't want to modify), this is a confirmation to proceed, so return true. Otherwise, return false.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "customer_secondary_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer, "bottle_type": null or "normal" or "original"}
    ],
    "is_confirmed": false
}
""" + _cart_context(cart)

    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    try:
        response = chat(messages, response_format={"type": "json_object"})
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

    _save_cart_details(cart, name, phone, secondary_phone, address)

    # A single bad extraction must not empty a cart the customer already built.
    if not products_data and cart.items.exists():
        products_data = _cart_items_as_products_data(cart)
        logger.warning(
            f"Extractor returned no products for conversation #{conversation.id}; "
            f"falling back to the {len(products_data)} saved cart item(s)."
        )

    if not products_data:
        return "تمام، بس مش واضحلي عايز تطلب أنهي عطر. ممكن تقولي اسم العطر اللي عايزه؟", ""

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
            available_normal = [v for v in variants if v.bottle_type == "normal" and product.oil_stock_grams >= (v.volume * product.concentration_percentage) / 100]
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
            
            # Check stock availability for the selected variant
            if bottle_type == "normal":
                req_oil = (selected_variant.volume * product.concentration_percentage) / 100
                if product.oil_stock_grams < req_oil:
                    if available_normal:
                        sizes_available = ", ".join([f"{v.volume} ملي" for v in available_normal])
                        return f"للأسف الزيت العطري لـ {product.name} لا يكفي لحجم {selected_variant.volume} ملي حالياً 😔 لكن متوفر منه أحجام تانية: {sizes_available}. تحب تطلب حجم تاني؟", ""
                    else:
                        return f"للأسف عطر {product.name} (تركيب) نفد من المخزون حالياً 😔", ""
                elif product.oil_stock_grams < req_oil * qty:
                    max_qty = int(product.oil_stock_grams / req_oil)
                    return f"للأسف كمية الزيت المطلوبة من عطر {product.name} ({selected_variant.volume} ملي) أكبر من المتوفر في المخزون. المتوفر حالياً يكفي لـ {max_qty} زجاجة فقط. تحب تطلب {max_qty} بدل {qty}؟", ""
            
            elif bottle_type == "original":
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
    # size, the settled one must survive the turn spent asking about the other.
    _save_cart_items(cart, items_to_create)

    # If the user asked for products but NONE were found in our store, stop immediately and suggest alternatives.
    if not items_to_create and all("product_obj" not in p for p in products_data if isinstance(p, dict)):
        # Check if the AI returned null for the name, which means the user's request was ambiguous (e.g. "عايز واحد")
        if any(not p.get("name") for p in products_data if isinstance(p, dict)):
            return "عذراً يا فندم، تقصد أنهي عطر فيهم بالظبط عشان أقدر أسجلهولك في الطلب؟", context_str
            
        alternatives = Product.objects.filter(store=store, is_active=True).filter(Q(oil_stock_grams__gt=0) | Q(variants__stock__gt=0)).distinct().order_by('?')[:3]
        if alternatives.exists():
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
        if not p.get("quantity"):
            missing_for_this_product.append("كمية الزجاجات المطلوبة")
            
        if missing_for_this_product:
            joined_missing = " و ".join(missing_for_this_product)
            product_missing_fields.append(f"{joined_missing} من عطر {p.get('name')}")

    if product_missing_fields:
        missing_text = " ولا ".join(product_missing_fields) if len(product_missing_fields) == 1 else " و ".join(product_missing_fields)
        # If it's just one product and they're missing size, make it sound like a natural question
        if len(product_missing_fields) == 1 and "الحجم" in product_missing_fields[0]:
            return f"تمام 👌 تحب الـ50 ملي ولا الـ90 ملي؟", ""
        return f"تمام 👌 بس محتاج أعرف {missing_text}؟", ""

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
        return f"تمام، عشان أأكدلك الطلب ناقصني بس {missing_text}.", ""

    if not is_confirmed:
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
        summary += "\nكل البيانات كده تمام ونأكد الطلب، ولا تحب تعدل حاجة؟"
        return summary, context_str

    # All details collected and confirmed! Let's process the order.
    return create_order_in_db(store, name, phone, secondary_phone, address, total_price, items_to_create, context_str, conversation)


def create_order_in_db(store, name, phone, secondary_phone, address, total_price, items_to_create, context_str, conversation):
    try:
        with transaction.atomic():
            # Re-validate stock inside the transaction with select_for_update to prevent race conditions
            for item in items_to_create:
                product = item["variant"].product
                if item["bottle_type"] == "normal":
                    # Lock the product row to prevent concurrent stock modifications
                    locked_product = Product.objects.select_for_update().get(id=product.id)
                    req_oil = (item["variant"].volume * locked_product.concentration_percentage) / 100 * item["quantity"]
                    if locked_product.oil_stock_grams < req_oil:
                        return f"للأسف عطر {locked_product.name} نفد من المخزون أثناء تأكيد الطلب 😔 ممكن تجرب تاني.", ""
                elif item["bottle_type"] == "original":
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
                
                # Decrement stock atomically using F()
                product = item["variant"].product
                if item["bottle_type"] == "original":
                    ProductVariant.objects.filter(id=item["variant"].id).update(
                        stock=F('stock') - item["quantity"]
                    )
                elif item["bottle_type"] == "normal":
                    req_oil = int((item["variant"].volume * product.concentration_percentage) / 100 * item["quantity"])
                    Product.objects.filter(id=product.id).update(
                        oil_stock_grams=F('oil_stock_grams') - req_oil
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
