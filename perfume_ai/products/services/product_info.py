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
            available_variants = []
            out_of_stock_variants = []
            all_out_of_stock = True
            for v in variants:
                if v.bottle_type == 'normal':
                    req_oil = (v.volume * product.concentration_percentage) / 100
                    is_available = product.oil_stock_grams >= req_oil
                    if is_available:
                        available_variants.append(f"- الـ {v.volume} ملي: {v.price} EGP")
                        all_out_of_stock = False
                    else:
                        out_of_stock_variants.append(f"الـ {v.volume} ملي")
                elif v.bottle_type == 'original':
                    stock_num = v.stock or 0
                    is_available = stock_num > 0
                    if is_available:
                        status = f" ({stock_num} زجاجة فقط)" if stock_num <= 3 else ""
                        available_variants.append(f"- زجاجة أوريجينال {v.volume} ملي: {v.price} EGP{status}")
                        all_out_of_stock = False
                    else:
                        out_of_stock_variants.append(f"زجاجة أوريجينال {v.volume} ملي")
            
            avail_str = "\n".join(available_variants) if available_variants else "لا توجد أحجام متوفرة حالياً"
            oos_str = "، ".join(out_of_stock_variants) if out_of_stock_variants else "لا يوجد"
            
            stock_status = "❌ هذا المنتج غير متوفر حالياً بجميع أحجامه" if all_out_of_stock else "✅ متوفر"
            is_custom_blend = bool(product.store and product.brand.name.lower() == product.store.name.lower())
            brand_display = "⭐ عطر تركيب حصري خاص بالمتجر" if is_custom_blend else product.brand.name

            has_original_bottle = any(v.bottle_type == 'original' for v in variants)
            original_bottle_status = "Available (see sizes below)" if has_original_bottle else "NOT AVAILABLE (DO NOT OFFER IT)"

            context += f"""
Name (الاسم الصحيح): {product.name}
Brand: {brand_display}
Stock Status: {stock_status}
Original Bottle: {original_bottle_status}
Available Sizes & Prices:
{avail_str}
Out of Stock Sizes (DO NOT OFFER unless explicitly asked):
{oos_str}
Gender: {product.gender}
Perfume Type: {product.get_perfume_type_display() if product.perfume_type else 'غير محدد'}
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
1. 🔴 استخدم دائماً الاسم الصحيح للعطر الموجود في البيانات حتى لو أخطأ العميل في كتابته.
1. 🔴 فرّق بين نوع السؤال:
   • لو العميل بيسأل عن التوافر بس (زي "عندكم سوفاج؟" أو "فيه بلو دي شانيل؟" أو "موجود عندكم X؟") → رد بإجابة قصيرة جداً: "أيوه يا فندم عندنا" أو "أه متوفر عندنا". ممكن تعرض عليه يعرف الأسعار والأحجام بس متقولهمش من نفسك. متلقيش كل التفاصيل والأسعار إلا لو طلبها.
   • لو العميل سأل عن السعر أو الحجم صراحة (زي "بكام؟" أو "الأحجام إيه؟") → هنا اذكر الأسعار والأحجام.
   • لو العميل سأل سؤال تفصيلي (زي "إيه مكوناته؟" أو "ثباته إيه؟") → جاوب على اللي سأله بس.
2. لو العميل سأل عن السعر، اذكر **كل الأسعار والأحجام المتاحة** كما هي مكتوبة بالظبط في سطر واحد مفصولين بفاصلة (مثال: "الـ 50 ملي بـ 400 جنيه، والـ 90 ملي بـ 600 جنيه") — ❌ ممنوع تغيير السعر أو تذكر حجم وتسيب الباقي.
3. لو العميل سأل عن الحجم أو الملي، اذكر كل الأحجام المتاحة كما هي مكتوبة بالظبط.
4. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة).
5. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
6. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌) أو حجم معين نفد، أخبر العميل بذلك بشكل لطيف واقترح عليه إنه يسأل عن عطور تانية متوفرة أو اعرض عليه الأحجام المتوفرة إن وجدت.
7. 🔴 ممنوع أسئلة فاضية (زي "عايز حاجة تانية؟"). مسموح بـ CTA بيعي بس مش كل مرة (زي "تحب تطلب؟" أو "تنورنا في الستور تجرب؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
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
                        available_variants.append(f"- الـ {v.volume} ملي: {v.price} EGP")
                    elif v.bottle_type == 'original' and (v.stock or 0) > 0:
                        available_variants.append(f"- زجاجة أوريجينال {v.volume} ملي: {v.price} EGP")
                
                variants_str = "\n".join(available_variants)
                is_custom_blend = bool(alt.store and alt.brand.name.lower() == alt.store.name.lower())
                brand_display = "⭐ عطر تركيب حصري خاص بالمتجر" if is_custom_blend else alt.brand.name

                has_original_bottle = any(v.bottle_type == 'original' for v in variants)
                original_bottle_status = "Available (see sizes below)" if has_original_bottle else "NOT AVAILABLE (DO NOT OFFER IT)"

                context += f"""
Name (الاسم الصحيح): {alt.name}
Brand: {brand_display}
Original Bottle: {original_bottle_status}
Available Sizes & Prices:
{variants_str}
Gender: {alt.gender}
Perfume Type: {alt.get_perfume_type_display() if alt.perfume_type else 'غير محدد'}
Description: {alt.description}
-----------------------
"""
        
        instructions = """
═══ تعليمات ═══
1. اقرأ سجل المحادثة جيداً. لو كان العميل يسأل سؤالاً عاماً (مثل "بتحط كام جرام") أو يستفسر عن منتج تم التحدث عنه بالفعل في المحادثة، أجب من سياق المحادثة وتجاهل قائمة البدائل تماماً.
2. ❌ إياك أن تقول أن المنتج "غير متوفر" إذا كان قد تم إخباره بأنه متوفر في الرسائل السابقة. النظام هنا لم يتعرف على اسم منتج جديد فقط.
3. 🔴🔴 قانون مهم جداً — فرّق بين الحالات دي:
   • لو العميل سمّى عطر معين باسمه بوضوح (مثل "عندكو ديور هوم" أو "سعر أمبريو أرماني") والعطر مش موجود عندنا → اعتذر بلباقة وقوله إن العطر ده مش متوفر عندنا حالياً، ورشحله 1-2 بديل من القائمة أدناه.
   • لو العميل بيسأل عن براند أو نوع عام أو حاجة مش واضحة (مثل "عندكو حاجة من شانيل" أو "عايز حاجة حلوة" أو رسالة غامضة) → ❌ ممنوع تماماً تقول "مش متوفر"! بدل كده اسأله يوضحلك أكتر: "تقصد أنهي عطر بالظبط يا فندم؟" أو "ممكن توضحلي أكتر عشان أقدر أساعدك؟".
   • لو الرسالة مش واضحة أو مش فاهمها → ❌ ممنوع تقول "مش متوفر"! اسأله يوضح: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر؟".
4. بعد الاعتذار (في حالة عطر محدد مش موجود فقط)، رشح له 1-2 من "البدائل المقترحة" أعلاه بشكل جذاب. ❌ إياك أن تتظاهر أو توحي بأن العطر البديل هو نفسه العطر الذي سأل عنه العميل!
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