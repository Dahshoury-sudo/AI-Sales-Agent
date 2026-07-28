from .product_resolver import resolve_products
from .ai.client import chat
from .ai.prompts import get_system_prompt
from django.db.models import Q
from products.models import Product

def get_product_info(message, history=None, store=None):

    products = resolve_products(message, history, store)

    if products:
        context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        for product in products:
            variants = list(product.variants.all())
            variants_str_list = []
            all_out_of_stock = True
            for v in variants:
                if v.bottle_type == 'normal':
                    req_oil = (v.volume * product.concentration_percentage) / 100
                    is_available = product.oil_stock_grams >= req_oil
                    status = 'متوفر' if is_available else '❌ نفد من المخزون'
                    variants_str_list.append(f"- زجاجة تعبئة {v.volume} ملي: {v.price} EGP ({status})")
                    if is_available:
                        all_out_of_stock = False
                elif v.bottle_type == 'original':
                    stock_num = v.stock or 0
                    is_available = stock_num > 0
                    if is_available:
                        status = f"متوفر ({stock_num} زجاجة فقط)" if stock_num <= 3 else "متوفر"
                    else:
                        status = '❌ نفد من المخزون'
                    variants_str_list.append(f"- زجاجة أوريجينال {v.volume} ملي: {v.price} EGP ({status})")
                    if is_available:
                        all_out_of_stock = False
            
            variants_str = "\n".join(variants_str_list) if variants else "غير متوفر أسعار/أحجام حالياً"
            
            stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"
            is_custom_blend = product.brand.name.lower() == "perfamix"
            brand_display = "⭐ عطر تركيب حصري خاص بالمتجر" if is_custom_blend else product.brand.name

            context += f"""
Name: {product.name}
Brand: {brand_display}
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
7. 🔴🔴 ممنوع تسأل سؤال متابعة في آخر ردك (زي "عايز حاجة تانية؟" أو "محتاج مساعدة؟" أو "تحب أرشحلك حاجة تانية؟"). جاوب على السؤال وخلاص.
"""
    else:
        # Product not found, let's get some alternatives
        alternatives = Product.objects.filter(store=store, is_active=True).filter(Q(oil_stock_grams__gt=0) | Q(variants__stock__gt=0)).distinct().order_by('?')[:3]
        
        context = "═══ تنبيه للنظام ═══\nلم يتم التعرف على اسم منتج محدد في رسالة العميل الأخيرة.\n\n"
        if alternatives.exists():
            context += "═══ بدائل مقترحة متوفرة في المتجر ═══\n"
            for alt in alternatives:
                variants = list(alt.variants.all())
                available_variants = []
                for v in variants:
                    if v.bottle_type == 'normal' and alt.oil_stock_grams >= (v.volume * alt.concentration_percentage) / 100:
                        available_variants.append(f"- زجاجة تعبئة {v.volume} ملي: {v.price} EGP")
                    elif v.bottle_type == 'original' and (v.stock or 0) > 0:
                        available_variants.append(f"- زجاجة أوريجينال {v.volume} ملي: {v.price} EGP")
                
                variants_str = "\n".join(available_variants)
                is_custom_blend = alt.brand.name.lower() == "perfamix"
                brand_display = "⭐ عطر تركيب حصري خاص بالمتجر" if is_custom_blend else alt.brand.name

                context += f"""
Name: {alt.name}
Brand: {brand_display}
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