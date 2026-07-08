import json
from django.db.models import Q
from products.models import Product


def resolve_product(message: str, history=None, store=None):
    """
    Try to resolve a product from the user's message using AI extraction.
    """
    products = Product.objects.filter(is_active=True)
    if store:
        products = products.filter(store=store)
    product_names = list(products.values_list('name', flat=True))

    prompt = f"""
Extract the exact perfume name the user is inquiring about.
Look at the conversation history if the user is using pronouns or referring to something previously mentioned (like "بكام ده" or "عامل كام").

Available Perfumes in Database:
{product_names}

Rules:
1. Translate Arabic names and fix spelling mistakes (e.g., 'بلو شانيل' -> 'Bleu de Chanel').
2. Check if the requested perfume exists in the Available Perfumes list.
3. If the perfume exists in the list (or is a close match/misspelling of one), return its exact name from the list.
4. CRITICAL: If the requested perfume is NOT in the list, return null. Do NOT return a different perfume just because they share the same brand.

Output format MUST be valid JSON:
{{"perfume": "Exact Name" or null}}
"""
    try:
        from .ai.client import chat
        messages = [{"role": "system", "content": prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})
        
        response = chat(messages, response_format={"type": "json_object"})
        
        data = json.loads(response)
        p_name = data.get("perfume")
    except Exception:
        p_name = None

    if not p_name:
        return None

    # First try exact match from the list
    exact_match = products.filter(name__iexact=p_name).first()
    if exact_match:
        return exact_match

    # Fallback search if LLM returned slightly different name
    query = Q()
    for word in p_name.split():
        if len(word) > 2:
            query &= (Q(name__icontains=word) | Q(brand__name__icontains=word))
            
    if not query:
        return None
        
    return products.filter(query).first()