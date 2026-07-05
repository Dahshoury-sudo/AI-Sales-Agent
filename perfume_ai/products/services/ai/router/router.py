from .ai.classifier import classify
from .ai.intent import extract_intent
from .ai.recommendation import recommend
from .search_service import search_products


def route(message):

    request_type = classify(message)

    if request_type == "recommendation":

        intent = extract_intent(message)

        products = search_products(intent)

        return recommend(message, products)

    return "هذه الميزة لم يتم تنفيذها بعد."