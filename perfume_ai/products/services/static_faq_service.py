import re
from products.models import StaticFAQ


def normalize_arabic(text: str) -> str:
    """تطبيع النص العربي لتوحيد الحروف وإزالة التشكيل"""
    # Normalize alef variants
    text = re.sub(r'[إأآٱ]', 'ا', text)
    # Normalize ya
    text = re.sub(r'ى', 'ي', text)
    # Normalize ta marbuta
    text = re.sub(r'ة', 'ه', text)
    # Remove tashkeel (diacritics)
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    # Lowercase for English keywords
    text = text.strip().lower()
    return text


def match_static_faq(message: str, store) -> dict | None:
    """
    يبحث في الأسئلة الثابتة للـ Store.
    يرجع dict فيه {answer} لو لقى مطابقة.
    يرجع None لو ملقاش.
    
    الخوارزمية:
    1. يجيب كل الـ StaticFAQ الخاصة بالـ Store (active فقط)، مرتبة بالأولوية
    2. لكل FAQ، يقسم الـ keywords لقائمة كلمات
    3. يشيك لو أي keyword موجود كـ substring في رسالة العميل (OR logic)
    4. لو لقى مطابقة → يرجع الرد
    5. لو ملقاش → يرجع None
    """
    if not store:
        return None

    normalized_msg = normalize_arabic(message)

    if not normalized_msg:
        return None

    faqs = StaticFAQ.objects.filter(store=store, is_active=True).order_by('-priority', 'id')

    for faq in faqs:
        keywords = [
            normalize_arabic(kw.strip())
            for kw in faq.keywords.split(',')
            if kw.strip()
        ]
        for keyword in keywords:
            if keyword and keyword in normalized_msg:
                return {"answer": faq.answer}

    return None
