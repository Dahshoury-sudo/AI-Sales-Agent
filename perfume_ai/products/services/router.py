from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order


def route(message, history=None, store=None, conversation=None):
    if history is None:
        history = []

    request_type = classify(message, history)

    if request_type == "recommendation":
        intent = extract_intent(message, history)
        results = search_products(intent, store)
        return recommend(message, results["products"], history, alternatives=results["alternatives"], store=store)

    elif request_type == "product_info":
        return get_product_info(message, history, store)

    elif request_type == "comparison":
        return compare_products(message, history, store)

    elif request_type in ["greeting", "faq"]:
        from .general_service import handle_general
        return handle_general(message, history, store)
        
        
    elif request_type == "order":
        if not conversation:
            return "عذراً، لا يمكن معالجة الطلب بدون محادثة نشطة.", ""
        return handle_order(message, history, store, conversation)
        
    elif request_type == "handoff":
        if conversation:
            conversation.needs_human = True
            conversation.save()
        return "تم تحويل المحادثة لأحد ممثلي خدمة العملاء. سيتم الرد عليك في أقرب وقت ممكن. شكراً لانتظارك!", ""
        
    elif request_type == "out_of_domain":
        return "أنا هنا لمساعدتك في كل ما يخص العطور والطلبات من متجرنا فقط. كيف يمكنني مساعدتك في اختيار عطر اليوم؟", ""

    # Fallback for anything not explicitly matched
    from .general_service import handle_general
    return handle_general(message, history, store)