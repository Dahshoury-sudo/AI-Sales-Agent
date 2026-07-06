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
1. "customer_name": The customer's full name if provided.
2. "customer_phone": The customer's phone number.
3. "shipping_address": The customer's full delivery address.
4. "products": A list of objects containing "name" (exact name from history) and "quantity" (an integer, return null if the user didn't explicitly mention the number of bottles). If no products were mentioned, return an empty list.
5. "is_confirmed": true ONLY IF the assistant in the previous message summarized the full order (including total price) AND the user explicitly agreed/confirmed in their latest message (e.g. "تمام", "اكد الطلب", "توكلنا على الله", "ايوة"). Otherwise, return false.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": null or integer}
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
        return "عذراً، لم أتمكن من فهم تفاصيل الطلب. هل يمكنك إعادة كتابة طلبك بوضوح؟", ""

    missing_fields = []
    
    name = data.get("customer_name")
    phone = data.get("customer_phone")
    address = data.get("shipping_address")
    products_data = data.get("products", [])
    is_confirmed = data.get("is_confirmed", False)

    if not products_data:
        return "لم تحدد العطر الذي ترغب في شرائه. أي عطر تود طلبه؟", ""

    # Check for missing basic details
    if not name:
        missing_fields.append("الاسم الكريم")
    if not phone:
        missing_fields.append("رقم الهاتف")
    if not address:
        missing_fields.append("عنوان التوصيل بالتفصيل")

    # Check for missing quantities
    for p in products_data:
        if not p.get("quantity"):
            missing_fields.append(f"الكمية المطلوبة من عطر {p.get('name')}")

    if missing_fields:
        missing_text = " و ".join(missing_fields)
        return f"ممتاز! لتأكيد طلبك، أحتاج فقط إلى {missing_text}.", ""

    # Resolve products to check stock and prices
    total_price = 0
    items_to_create = []
    context_data = []
    
    for p_data in products_data:
        if not isinstance(p_data, dict): continue
        p_name = p_data.get("name")
        qty = p_data.get("quantity", 1)
        
        if not p_name or not isinstance(p_name, str): continue
        try:
            qty = int(qty)
        except (ValueError, TypeError):
            qty = 1
            
        product = Product.objects.filter(store=store, name__iexact=p_name, is_active=True).first()
        if not product:
            product = resolve_product(p_name, store)
            
        if product:
            price = product.price
            total_price += price * qty
            items_to_create.append({
                "product": product,
                "quantity": qty,
                "price": price
            })
            context_data.append(f"{product.name} x {qty} ({price * qty} EGP)")
            
    context_str = ", ".join(context_data) if context_data else "No products found"

    if not items_to_create:
        return "لم أتمكن من العثور على العطور المذكورة في متجرنا. يرجى التأكد من الأسماء.", context_str

    if not is_confirmed:
        # Generate Summary
        summary = "تفاصيل طلبك كالتالي:\n\n"
        summary += f"👤 الاسم: {name}\n"
        summary += f"📱 الهاتف: {phone}\n"
        summary += f"📍 العنوان: {address}\n\n"
        summary += "🛍️ المنتجات:\n"
        for item in items_to_create:
            summary += f"- {item['quantity']} × {item['product'].name} (السعر: {item['price'] * item['quantity']} جنيه)\n"
        summary += f"\n💰 الإجمالي: {total_price} جنيه.\n"
        summary += "\nهل تحب نأكد الطلب على كده ولا في حاجة حابب تعدلها؟"
        return summary, context_str

    # All details collected and confirmed! Let's process the order.
    return create_order_in_db(store, name, phone, address, total_price, items_to_create, context_str)


def create_order_in_db(store, name, phone, address, total_price, items_to_create, context_str):
    try:
        with transaction.atomic():
            order = Order.objects.create(
                store=store,
                customer_name=name,
                customer_phone=phone,
                shipping_address=address,
                total_price=total_price,
                status="pending"
            )
            
            for item in items_to_create:
                OrderItem.objects.create(
                    order=order,
                    product=item["product"],
                    quantity=item["quantity"],
                    price_at_time_of_order=item["price"]
                )
                
        return f"تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #{order.id}.\nسيقوم فريق المبيعات بالتواصل معك قريباً لتحديد موعد التسليم.", context_str
    except Exception as e:
        return "عذراً، حدث خطأ أثناء تسجيل الطلب. يرجى المحاولة مرة أخرى لاحقاً.", ""
