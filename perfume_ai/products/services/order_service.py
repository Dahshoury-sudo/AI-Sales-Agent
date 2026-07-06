import json
from django.db import transaction
from products.models import Order, OrderItem, Product
from .ai.client import chat

def handle_order(message, history, store, conversation):
    """
    Handles the order collection flow. Extracts Name, Phone, Address, and Products.
    """
    
    prompt = """
You are an order detail extractor for an Arabic perfume store.
Look at the conversation history and the latest message to extract the customer's order details.
If a detail is not provided, return null for it.

Rules:
1. "customer_name": The customer's full name if provided.
2. "customer_phone": The customer's phone number.
3. "shipping_address": The customer's full delivery address.
4. "products": A list of objects containing "name" (exact name from history) and "quantity" (default 1). If no products were mentioned, return an empty list.

Return valid JSON in this exact format:
{
    "customer_name": "...",
    "customer_phone": "...",
    "shipping_address": "...",
    "products": [
        {"name": "...", "quantity": 1}
    ]
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
        return "عذراً، لم أتمكن من فهم تفاصيل الطلب. هل يمكنك إعادة كتابة طلبك بوضوح؟"

    missing_fields = []
    
    name = data.get("customer_name")
    phone = data.get("customer_phone")
    address = data.get("shipping_address")
    products_data = data.get("products", [])

    if not products_data:
        return "لم تحدد العطر الذي ترغب في شرائه. أي عطر تود طلبه؟", ""

    if not name:
        missing_fields.append("الاسم الكريم")
    if not phone:
        missing_fields.append("رقم الهاتف")
    if not address:
        missing_fields.append("عنوان التوصيل بالتفصيل")

    if missing_fields:
        missing_text = " و ".join(missing_fields)
        return f"ممتاز! لتأكيد طلبك، أحتاج فقط إلى {missing_text}.", ""

    # All details collected! Let's process the order.
    return create_order_in_db(name, phone, address, products_data, store)


def create_order_in_db(name, phone, address, products_data, store):
    total_price = 0
    items_to_create = []
    
    # Verify products
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
        
        # Search for exact product in store
        product = Product.objects.filter(store=store, name__iexact=p_name, is_active=True).first()
        
        # Fallback partial search if exact fails
        if not product:
            product = Product.objects.filter(store=store, name__icontains=p_name, is_active=True).first()
            
        if product:
            price = product.price
            total_price += price * qty
            items_to_create.append({
                "product": product,
                "quantity": qty,
                "price": price
            })
            context_data.append(f"{product.name} ({price} EGP)")
            
    context_str = ", ".join(context_data) if context_data else "No products found"

    if not items_to_create:
        return "لم أتمكن من العثور على العطور المذكورة في متجرنا. يرجى التأكد من الأسماء.", context_str

    # Create DB records
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
                
        return f"تم تأكيد طلبك بنجاح! رقم الطلب هو #{order.id}. إجمالي المبلغ: {total_price} جنيه. سيقوم فريق المبيعات بالتواصل معك قريباً.", context_str
    except Exception as e:
        return "عذراً، حدث خطأ أثناء تسجيل الطلب. يرجى المحاولة مرة أخرى لاحقاً.", ""
