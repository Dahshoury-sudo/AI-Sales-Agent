import json
from django.db import transaction
from products.models import Order, OrderItem, Product
from .ai.client import chat
from .product_resolver import resolve_product

def handle_order(message, history, store, conversation):
    """
    Handles the order collection flow. Extracts Name, Phone, Address, Products, Quantities, and Confirmation.
    """
    
    prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided in the history.
2. "customer_phone": The customer's phone number if provided in the history.
3. "shipping_address": The customer's full delivery address if provided in the history.
4. "products": A comprehensive list of ALL products the user wants to buy right now. You MUST read the entire history and maintain a running list of the shopping cart. 🚨 CRITICAL 🚨: If the assistant has ALREADY confirmed an order in the history (e.g. saying "تم تأكيد طلبك بنجاح"), you MUST EMPTY your cart of any products mentioned BEFORE that confirmation. Only extract NEW products requested AFTER the last order was confirmed. If no new products were requested, return an empty list [].
5. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). ALSO, if the assistant asked "ولا في حاجة حابب تعدلها؟" and the user replies with "لا", "لا شكرا", or "لا تمام" (meaning they don't want to modify), this is a confirmation to proceed, so return true. Otherwise, return false.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer}
    ],
    "is_confirmed": false
}
"""

    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})

    try:
        response = chat(messages, response_format={"type": "json_object"})
        data = json.loads(response)
    except Exception:
        return "مش فاهم تفاصيل الطلب كويس يا فندم. ممكن تقولي تاني عايز تطلب ايه بالظبط؟", ""

    name = data.get("customer_name")
    phone = data.get("customer_phone")
    address = data.get("shipping_address")
    products_data = data.get("products", [])
    is_confirmed = data.get("is_confirmed", False)

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
            variants = list(product.variants.all())
            if not variants:
                continue # Edge case: product has no variants
                
            selected_variant = None
            if req_volume:
                try:
                    req_volume = int(req_volume)
                    selected_variant = next((v for v in variants if v.volume == req_volume), None)
                except (ValueError, TypeError):
                    pass
            
            if not selected_variant:
                # If there is only one variant, auto-select it. Otherwise, mark for missing field.
                if len(variants) == 1:
                    selected_variant = variants[0]
                else:
                    # Save the product in p_data to use for missing fields
                    p_data["product_obj"] = product
                    p_data["available_volumes"] = [v.volume for v in variants]
                    continue
            
            # Check stock availability
            if selected_variant.stock == 0:
                # Check if other sizes of the same product are in stock
                in_stock_variants = [v for v in variants if v.stock > 0]
                if in_stock_variants:
                    sizes_available = ", ".join([f"{v.volume}ml" for v in in_stock_variants])
                    return f"للأسف عطر {product.name} حجم {selected_variant.volume}ml نفد من المخزون حالياً 😔 لكن متوفر منه أحجام تانية: {sizes_available}. تحب تطلب حجم تاني؟", ""
                else:
                    # All sizes out of stock — suggest similar products from store
                    from products.models import ProductVariant
                    similar = Product.objects.filter(
                        store=store, is_active=True, gender=product.gender
                    ).exclude(id=product.id).prefetch_related('variants')
                    alternatives = []
                    for alt in similar[:5]:
                        alt_variants = [v for v in alt.variants.all() if v.stock > 0]
                        if alt_variants:
                            prices = ", ".join([f"{v.volume}ml بـ {v.price} جنيه" for v in alt_variants])
                            alternatives.append(f"• {alt.name} ({alt.brand.name}) - {prices}")
                    if alternatives:
                        alts_text = "\n".join(alternatives)
                        return f"للأسف عطر {product.name} نفد من المخزون حالياً بجميع أحجامه 😔\n\nبس عندنا عطور تانية ممتازة ممكن تعجبك:\n{alts_text}\n\nتحب تعرف تفاصيل أكتر عن أي واحد فيهم؟", ""
                    else:
                        return f"للأسف عطر {product.name} نفد من المخزون حالياً 😔 ممكن تسأل عن عطر تاني وهنساعدك!", ""
            
            if selected_variant.stock < qty:
                return f"للأسف الكمية المطلوبة من عطر {product.name} ({selected_variant.volume}ml) أكبر من المتوفر في المخزون. المتوفر حالياً {selected_variant.stock} قطعة فقط. تحب تطلب {selected_variant.stock} بدل {qty}؟", ""

            price = selected_variant.price
            total_price += price * qty
            items_to_create.append({
                "variant": selected_variant,
                "quantity": qty,
                "price": price
            })
            p_data["name"] = product.name
            context_data.append(f"{product.name} ({selected_variant.volume}ml) x {qty} ({price * qty} EGP)")
            
    context_str = ", ".join(context_data) if context_data else "No products found"

    # If the user asked for products but NONE were found in our store, stop immediately and suggest alternatives.
    if not items_to_create and all("product_obj" not in p for p in products_data if isinstance(p, dict)):
        from products.models import Product
        alternatives = Product.objects.filter(store=store, is_active=True, variants__stock__gt=0).distinct().order_by('?')[:3]
        if alternatives.exists():
            alts_text = []
            for alt in alternatives:
                alts_text.append(f"• {alt.name} ({alt.brand.name})")
            alts_str = "\n".join(alts_text)
            return f"للأسف العطر ده مش متوفر عندنا يا فندم 😔\n\nبس عندنا عطور تانية مميزة ممكن تعجبك زي:\n{alts_str}\n\nتحب تعرف تفاصيل أكتر عن أي واحد فيهم؟", context_str
        
        return "مش لاقي العطر ده عندنا يا فندم. ممكن تقولي اسمه تاني أو تسأل عن عطر تاني؟", context_str

    # 2. Check for missing basic details now that we know we have the products
    missing_fields = []
    if not name:
        missing_fields.append("الاسم الكريم")
    if not phone:
        missing_fields.append("رقم الهاتف")
    if not address:
        missing_fields.append("عنوان التوصيل بالتفصيل")

    # Check for missing quantities and sizes using the original data that passed resolution
    for p in products_data:
        if not isinstance(p, dict): continue
        
        if "product_obj" in p:
            vols = ", ".join(map(str, p["available_volumes"]))
            missing_fields.append(f"الحجم المطلوب من عطر {p.get('name')} (متاح أحجام: {vols} مل)")
            
        # Only check quantities for products we actually found
        elif not p.get("quantity"):
            missing_fields.append(f"الكمية المطلوبة من عطر {p.get('name')}")

    if missing_fields:
        missing_text = " و ".join(missing_fields)
        return f"ممتاز! لتأكيد طلبك، أحتاج فقط إلى {missing_text}.", ""

    if not is_confirmed:
        # Generate Summary
        summary = "تفاصيل طلبك كالتالي:\n\n"
        summary += f"👤 الاسم: {name}\n"
        summary += f"📱 الهاتف: {phone}\n"
        summary += f"📍 العنوان: {address}\n\n"
        summary += "🛍️ المنتجات:\n"
        for item in items_to_create:
            summary += f"- {item['quantity']} × {item['variant'].product.name} ({item['variant'].volume}ml) (السعر: {item['price'] * item['quantity']} جنيه)\n"
        summary += f"\n💰 الإجمالي: {total_price} جنيه.\n"
        summary += "\nهل تحب نأكد الطلب على كده ولا في حاجة حابب تعدلها؟"
        return summary, context_str

    # All details collected and confirmed! Let's process the order.
    return create_order_in_db(store, name, phone, address, total_price, items_to_create, context_str, conversation)


def create_order_in_db(store, name, phone, address, total_price, items_to_create, context_str, conversation):
    try:
        with transaction.atomic():
            order = Order.objects.create(
                store=store,
                customer_name=name,
                customer_phone=phone,
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
                    price_at_time_of_order=item["price"]
                )
                
        return f"تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #{order.id}.\nسيقوم فريق المبيعات بالتواصل معك قريباً لتحديد موعد التسليم.", context_str
    except Exception as e:
        return "حصل مشكلة في تسجيل الطلب يا فندم. ممكن تجرب تاني ولو المشكلة استمرت هحولك لحد من الفريق يساعدك.", ""
