from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products
from .product_info import get_product_info
from .comparison_service import compare_products


def route(message):
    request_type = classify(message)

    if request_type == "recommendation":
        intent = extract_intent(message)
        products = search_products(intent)
        return recommend(message, products)

    elif request_type == "product_info":
        return get_product_info(message)

    elif request_type == "comparison":
        return compare_products(message)

    return "هذه الميزة لم يتم تنفيذها بعد."