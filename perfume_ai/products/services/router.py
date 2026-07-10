from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products
from .order_service import handle_order
from .general_service import handle_general
from products.models import Order


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
        return handle_general(message, history, store)
        
        
    elif request_type == "order":
        if not conversation:
            return "محتاج الأول تبدأ محادثة جديدة عشان أقدر أسجللك الطلب يا فندم.", ""
        return handle_order(message, history, store, conversation)
        
    elif request_type == "order_cancel":
        if conversation:
            latest_order = Order.objects.filter(conversation=conversation, status="pending").order_by('-created_at').first()
            if latest_order:
                latest_order.status = "cancelled"
                latest_order.bot_notes = "تم إلغاء الطلب بواسطة البوت بناءً على طلب العميل."
                latest_order.save()
                return "تم الغاء الطلب اللي تم تسجيله بنجاح يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""
        return "ولا يهمك يا فندم، نورتنا في أي وقت! ولو احتجت أي مساعدة إحنا موجودين 24 ساعة تحت أمرك.", ""
        
    elif request_type == "handoff":
        if conversation:
            conversation.needs_human = True
            conversation.save()
        return "تم تحويل المحادثة لأحد ممثلي خدمة العملاء. سيتم الرد عليك في أقرب وقت ممكن. شكراً لانتظارك!", ""
        
    elif request_type == "out_of_domain":
        return "أنا هنا عشان أساعدك تختار عطر أو تطلب من متجرنا. قولي عايز ايه وهساعدك!", ""

    # Fallback for anything not explicitly matched
    return handle_general(message, history, store)