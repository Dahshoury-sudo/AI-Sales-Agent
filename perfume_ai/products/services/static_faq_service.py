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
        for keyword_group in keywords:
            if '+' in keyword_group:
                required_words = [w.strip() for w in keyword_group.split('+') if w.strip()]
                if required_words and all(req_word in normalized_msg for req_word in required_words):
                    return {"answer": faq.answer}
            else:
                # Single keyword — only match on short messages (likely direct FAQ questions)
                # to avoid false positives in longer conversational messages
                word_count = len(normalized_msg.split())
                if keyword_group and keyword_group in normalized_msg and word_count <= 5:
                    return {"answer": faq.answer}

    return None
