from .product_resolver import resolve_products
from .ai.client import chat
from .ai.prompts import get_system_prompt
from products.models import Product

def get_product_info(message, history=None, store=None):

    products = resolve_products(message, history, store)

    if products:
        context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        for product in products:
            variants = list(product.variants.all())
            variants_str = "\n".join([f"- {v.volume}ml: {v.price} EGP ({'متوفر - المخزون: ' + str(v.stock) if v.stock > 0 else '❌ نفد من المخزون'})" for v in variants]) if variants else "غير متوفر أسعار/أحجام حالياً"
            all_out_of_stock = all(v.stock == 0 for v in variants) if variants else True
            stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"
            context += f"""
Name: {product.name}
Brand: {product.brand.name}
Stock Status: {stock_status}
Available Sizes & Prices:
{variants_str}
Gender: {product.gender}
Season: {product.season}
Occasion: {product.occasion}
Longevity: {product.longevity}
Projection: {product.projection}
Top Notes: {product.top_notes}
Middle Notes: {product.middle_notes}
Base Notes: {product.base_notes}
Description: {product.description}
-----------------------
"""
        instructions = """
═══ تعليمات صارمة ═══
1. رد على سؤال العميل باستخدام البيانات أعلاه فقط.
2. لو العميل سأل عن السعر، اذكر السعر كما هو مكتوب بالظبط — ❌ ممنوع تغييره أو تقريبه.
3. لو العميل سأل عن الحجم أو الملي، اذكر الحجم كما هو مكتوب بالظبط.
4. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة).
5. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
6. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌) أو حجم معين نفد، أخبر العميل بذلك بشكل لطيف واقترح عليه إنه يسأل عن عطور تانية متوفرة أو اعرض عليه الأحجام المتوفرة إن وجدت.
"""
    else:
        # Product not found, let's get some alternatives
        alternatives = Product.objects.filter(store=store, is_active=True, variants__stock__gt=0).distinct().order_by('?')[:3]
        
        context = "═══ تنبيه للنظام ═══\nلم يتم التعرف على اسم منتج محدد في رسالة العميل الأخيرة.\n\n"
        if alternatives.exists():
            context += "═══ بدائل مقترحة متوفرة في المتجر ═══\n"
            for alt in alternatives:
                variants = list(alt.variants.filter(stock__gt=0))
                variants_str = "\n".join([f"- {v.volume}ml: {v.price} EGP" for v in variants])
                context += f"""
Name: {alt.name}
Brand: {alt.brand.name}
Available Sizes & Prices:
{variants_str}
Gender: {alt.gender}
Description: {alt.description}
-----------------------
"""
        
        instructions = """
═══ تعليمات ═══
1. اقرأ سجل المحادثة جيداً. لو كان العميل يسأل سؤالاً عاماً (مثل "بتحط كام جرام") أو يستفسر عن منتج تم التحدث عنه بالفعل في المحادثة، أجب من سياق المحادثة وتجاهل قائمة البدائل تماماً.
2. ❌ إياك أن تقول أن المنتج "غير متوفر" إذا كان قد تم إخباره بأنه متوفر في الرسائل السابقة. النظام هنا لم يتعرف على اسم منتج جديد فقط.
3. لو العميل سأل عن عطر جديد تماماً واسمه واضح ولكنه غير متوفر لدينا، اعتذر بلباقة وأخبره أنه غير متوفر.
4. إذا كان العطر غير متوفر، رشح له 1-2 من "البدائل المقترحة" أعلاه بشكل جذاب.
5. ❌ ممنوع تخترع أي معلومة أو عطر غير موجود في القائمة المقترحة.
"""

    messages = [
        {
            "role": "system",
            "content": get_system_prompt(store),
        }
    ]
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": f"""
═══ سؤال العميل ═══
{message}

{context}
{instructions}
"""
    })

    response = chat(messages)
    return response, context