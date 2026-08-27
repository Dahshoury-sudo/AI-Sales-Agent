import json
from django.db.models import Q
from products.models import Product
from .ai.client import chat
from .sales import naming


def resolve_products(message: str, history=None, store=None, conversation=None):
    """
    Try to resolve multiple products from the user's message using AI extraction.

    `conversation` is optional and used only to anchor pronoun resolution: it supplies the
    perfumes we most recently offered, derived from `Message.internal_context`. Without it the
    only reference guidance is one sentence below plus a rule scoped to short confirmations, and
    a doubt utterance matches neither — "مش متوفر متأكد ؟" about Versace Eros resolved to two
    perfumes from two turns earlier (conversation 1099) because nothing pointed at the newest.
    """
    products = Product.objects.filter(is_active=True)
    if store:
        products = products.filter(store=store)
    product_names = list(products.values_list('name', flat=True))

    from .sales import described as sales_described

    offered_block = sales_described.offered_context_block(conversation, store)

    prompt = f"""
Extract the exact perfume names the user is inquiring about.
Look at the conversation history if the user is using pronouns or referring to something previously mentioned (like "بكام ده" or "عامل كام" or "الاتنين").

Available Perfumes in Database:
{product_names}
{offered_block}
Rules:
1. Translate Arabic names to English and fix spelling mistakes to match the exact names in the database.
2. Be HIGHLY tolerant of phonetic Arabic transliterations and typos (e.g., 'فريساتشي يورس' or 'ايروس' -> 'Versace Eros', 'ديور سيفاج' -> 'Dior Sauvage', 'امبيرو' -> 'Ambero').
3. Check if the requested perfumes exist in the Available Perfumes list.
4. If the perfumes exist in the list, return their exact names from the list.
5. CRITICAL: If a requested perfume is absolutely NOT in the list, ignore it and DO NOT include it in the output. ❌ NEVER hallucinate or return a random/different perfume from the list just to fill the output. If you can't confidently map the user's word to a perfume in the list, return an empty list.
6. CRITICAL: If the user's message is a short confirmation (e.g. "ماشي", "تمام", "ايوة", "اه", "قول سعرهم") in response to the assistant's offer to show prices or details, you MUST extract ALL the perfume names that the assistant explicitly recommended or mentioned in its IMMEDIATELY PRECEDING message.
7. 🔴 CRITICAL — THE NEWEST TURN WINS. If the user names no perfume and is instead reacting to what you just said — doubting it ("مش متوفر متأكد ؟", "متأكد؟", "بجد؟"), asking about it ("بكام؟", "ثباته ايه؟", "فيه أحجام تانية؟"), or pointing at it ("ده", "دي") — the subject is the perfume in the "PERFUMES YOU JUST OFFERED" block above, entry 1 unless they say otherwise. ❌ NEVER reach further back in the history for a perfume you offered on an earlier turn: a customer who questions what you just said is talking about what you just said, not about something two turns ago.
"""
    prompt += """
Output format MUST be valid JSON:
{"perfumes": ["Exact Name 1", "Exact Name 2"]}  (Return an empty list if none found)
"""
    try:
        messages = [{"role": "system", "content": prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})
        
        response = chat(messages, profile="extract", response_format={"type": "json_object"})
        
        data = json.loads(response)
        p_names = data.get("perfumes", [])
    except Exception:
        p_names = []

    if not isinstance(p_names, list) or not p_names:
        return []

    resolved = []
    for p_name in p_names:
        if not p_name: continue
        # First try exact match from the list
        exact_match = products.filter(name__iexact=p_name).first()
        if exact_match:
            resolved.append(exact_match)
            continue

        # Then the deterministic token matcher, which handles reordering ("9pm by Afnan"
        # for "Afnan 9PM") and a one-character slip ("Ambiro" for "Ambero"), and returns
        # nothing when a name is ambiguous.
        #
        # This replaces a loose AND of `icontains` over each word, which was actively
        # dangerous: it resolved a mis-transliterated "اوداورا" to *Dark Aura* — a
        # different real perfume — and the bot then confidently compared the wrong one.
        # The prompt above tells the model never to substitute a different perfume; the
        # Python fallback was doing exactly that behind its back.
        match = naming.match_product(p_name, store, products=products)
        if match and match not in resolved:
            resolved.append(match)

    return resolved


def resolve_product(message: str, history=None, store=None, conversation=None):
    """
    Try to resolve a single product. Returns the first matched product or None.
    """
    resolved = resolve_products(message, history, store, conversation)
    return resolved[0] if resolved else None