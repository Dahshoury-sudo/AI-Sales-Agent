import json
import logging
from django.db import transaction
from django.db.models import F, Q
from products.models import Order, OrderItem, Product, ProductVariant
from .ai.client import chat
from .product_resolver import resolve_product

logger = logging.getLogger(__name__)


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
    
    prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided in the history.
2. "customer_phone": The customer's phone number if provided in the history.
3. "shipping_address": The customer's full delivery address if provided in the history.
4. "products": A comprehensive list of ALL products the user wants to buy right now. For each product, extract "bottle_type" ("original", "normal", or null if they didn't specify). CRITICAL: If the user hasn't explicitly chosen the bottle type yet, YOU MUST return null. You MUST read the entire history and maintain a running list of the shopping cart. 🚨 CRITICAL 🚨: If the assistant has ALREADY confirmed an order in the history (e.g. saying "تم تأكيد طلبك بنجاح"), you MUST EMPTY your cart of any products mentioned BEFORE that confirmation. Only extract NEW products requested AFTER the last order was confirmed. If no new products were requested, return an empty list [].
5. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). ALSO, if the assistant asked "ولا في حاجة حابب تعدلها؟" and the user replies with "لا", "لا شكرا", or "لا تمام" (meaning they don't want to modify), this is a confirmation to proceed, so return true. Otherwise, return false.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer, "volume": null or integer, "bottle_type": null or "normal" or "original"}
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
                
            bottle_type = p_data.get("bottle_type")
            
            # Filter available variants based on actual stock
            available_normal = [v for v in variants if v.bottle_type == "normal" and product.oil_stock_grams >= (v.volume * product.concentration_percentage) / 100]
            available_original = [v for v in variants if v.bottle_type == "original" and (v.stock or 0) > 0]
            
            if not bottle_type:
                if product.brand.name.lower() == "perfamix":
                    bottle_type = "normal"
                    p_data["bottle_type"] = "normal"
                elif not available_original and available_normal:
                    bottle_type = "normal"
                    p_data["bottle_type"] = "normal"
                elif not available_normal and available_original:
                    bottle_type = "original"
                    p_data["bottle_type"] = "original"
                elif not available_normal and not available_original:
                    # Everything is out of stock, let it fall through to error handling
                    pass
                else:
                    p_data["product_obj"] = product
                    p_data["missing_bottle_type"] = True
                    continue
            else:
                if bottle_type == "original":
                    has_original = any(v.bottle_type == "original" for v in variants)
                    if not has_original:
                        if available_normal:
                            return f"عذراً يا فندم، عطر {product.name} حصري للمتجر ولا يوجد منه زجاجات أوريجينال. متوفر فقط في زجاجة المحل. تحب تطلبه؟", ""
                        else:
                            return f"عذراً يا فندم، عطر {product.name} نفد من المخزون حالياً 😔.", ""
                    elif not available_original:
                        if available_normal:
                            return f"عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} نفدت من المخزون حالياً 😔. متوفر منه زجاجات المحل التعبئة. تحب تطلب زجاجة المحل؟", ""
                        else:
                            return f"عذراً يا فندم، عطر {product.name} نفد من المخزون تماماً 😔.", ""
                elif bottle_type == "normal" and not available_normal:
                    if available_original:
                        return f"عذراً يا فندم، زجاجات المحل التعبئة لعطر {product.name} غير متوفرة حالياً 😔. متوفر منه الزجاجة الأوريجينال. تحب تطلبها؟", ""


            # Now filter the variants to only the selected bottle_type
            filtered_variants = [v for v in variants if v.bottle_type == bottle_type] if bottle_type else variants
            
            if req_volume:
                try:
                    req_volume = int(req_volume)
                    selected_variant = next((v for v in filtered_variants if v.volume == req_volume), None)
                except (ValueError, TypeError):
                    selected_variant = None
            else:
                selected_variant = None

            if not selected_variant:
                # If there is only one variant of this type, auto-select it. Otherwise, mark for missing field.
                if len(filtered_variants) == 1:
                    selected_variant = filtered_variants[0]
                else:
                    p_data["product_obj"] = product
                    p_data["available_volumes"] = [v.volume for v in filtered_variants]
                    continue
            
            # Check stock availability for the selected variant
            if bottle_type == "normal":
                req_oil = (selected_variant.volume * product.concentration_percentage) / 100
                if product.oil_stock_grams < req_oil:
                    if available_normal:
                        sizes_available = ", ".join([f"{v.volume}ml" for v in available_normal])
                        return f"للأسف الزيت العطري لـ {product.name} لا يكفي لحجم {selected_variant.volume}ml حالياً 😔 لكن متوفر منه أحجام تانية: {sizes_available}. تحب تطلب حجم تاني؟", ""
                    else:
                        return f"للأسف عطر {product.name} (تعبئة) نفد من المخزون حالياً 😔", ""
                elif product.oil_stock_grams < req_oil * qty:
                    max_qty = int(product.oil_stock_grams / req_oil)
                    return f"للأسف كمية الزيت المطلوبة من عطر {product.name} ({selected_variant.volume}ml) أكبر من المتوفر في المخزون. المتوفر حالياً يكفي لـ {max_qty} زجاجة فقط. تحب تطلب {max_qty} بدل {qty}؟", ""
            
            elif bottle_type == "original":
                stock = selected_variant.stock or 0
                if stock == 0:
                    return f"عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} حجم {selected_variant.volume}ml نفدت تماماً.", ""
                elif stock < qty:
                    return f"عذراً يا فندم، الزجاجات الأوريجينال لعطر {product.name} المتوفرة حالياً {stock} زجاجة فقط من حجم {selected_variant.volume}ml. تحب تطلب {stock} بس؟", ""

            price = selected_variant.price
            total_price += price * qty
            items_to_create.append({
                "variant": selected_variant,
                "quantity": qty,
                "price": price,
                "bottle_type": bottle_type
            })
            p_data["name"] = product.name
            bottle_text = " (زجاجة أوريجينال)" if bottle_type == "original" else " (زجاجة المحل)"
            context_data.append(f"{product.name} ({selected_variant.volume}ml){bottle_text} x {qty} ({price * qty} EGP)")
            
    context_str = ", ".join(context_data) if context_data else "No products found"

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

    # 2. Check for missing basic details now that we know we have the products
    missing_fields = []
    if not name:
        missing_fields.append("الاسم الكريم")
    if not phone:
        missing_fields.append("رقم الهاتف")
    if not address:
        missing_fields.append("عنوان التوصيل بالتفصيل")

    # Check for missing quantities, sizes, and bottle types using the original data that passed resolution
    for p in products_data:
        if not isinstance(p, dict): continue
        
        if "product_obj" in p:
            if "available_volumes" in p:
                vols = ", ".join(map(str, p["available_volumes"]))
                missing_fields.append(f"الحجم المطلوب من عطر {p.get('name')} (متاح أحجام: {vols} مل)")
            if p.get("missing_bottle_type"):
                missing_fields.append(f"نوع الزجاجة لعطر {p.get('name')} (هل تفضل تعبئته في زجاجة أوريجينال أم زجاجة المحل؟)")
            
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
            bottle_disp = "أوريجينال" if item['bottle_type'] == "original" else "عادية"
            summary += f"- {item['quantity']} × {item['variant'].product.name} ({item['variant'].volume}ml) - زجاجة {bottle_disp} (السعر: {item['price'] * item['quantity']} جنيه)\n"
        summary += f"\n💰 الإجمالي: {total_price} جنيه.\n"
        summary += "\nهل تحب نأكد الطلب على كده ولا في حاجة حابب تعدلها؟"
        return summary, context_str

    # All details collected and confirmed! Let's process the order.
    return create_order_in_db(store, name, phone, address, total_price, items_to_create, context_str, conversation)


def create_order_in_db(store, name, phone, address, total_price, items_to_create, context_str, conversation):
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
        return f"تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #{order.id}.\nسيقوم فريق المبيعات بالتواصل معك قريباً لتحديد موعد التسليم.", context_str
    except Exception as e:
        logger.exception(f"Failed to create order for store '{store.name}': {e}")
        return "حصل مشكلة في تسجيل الطلب يا فندم. ممكن تجرب تاني ولو المشكلة استمرت هحولك لحد من الفريق يساعدك.", ""
