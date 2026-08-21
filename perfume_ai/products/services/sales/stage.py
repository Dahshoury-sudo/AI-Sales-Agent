"""Which stage of the sale this turn is in, and whether closing is allowed yet.

The bot closed with "تحب أساعدك في الطلب؟" while the customer was still comparing, still
objecting, or still trying to remember a perfume's name. That is not a prompt problem:
the router knew what *kind* of message had arrived (`recommendation`, `faq`, `handoff`)
but had no notion of where the customer stood, so every branch was equally entitled to
ask for the order.

Stage is derived fresh each turn from data already in hand — the classification, the
extracted intent, any detected objection, and the message itself. Nothing is persisted:
a stored stage would need a migration and would go stale the moment a customer changed
the subject, and every behaviour required here is answerable from the current turn.
"""

from . import constraints
from ..static_faq_service import normalize_arabic

DISCOVERY = "discovery"
RECOMMENDATION = "recommendation"
COMPARISON = "comparison"
OBJECTION = "objection"
IDENTIFICATION = "identification"
PURCHASE_INTENT = "purchase_intent"
ORDER_COLLECTION = "order_collection"
COMPLAINT = "complaint"

STAGES = (
    DISCOVERY, RECOMMENDATION, COMPARISON, OBJECTION,
    IDENTIFICATION, PURCHASE_INTENT, ORDER_COLLECTION, COMPLAINT,
)

# The only stages where asking for the order is earned. Everything else is a customer who
# has not chosen yet, and pushing them there is what made the bot read as a checkout bot.
CLOSING_STAGES = frozenset({PURCHASE_INTENT, ORDER_COLLECTION})

# Asking a price or a size is a buying signal — the persona explicitly wants the next step
# offered there. Asking what a perfume smells like is not.
_BUYING_SIGNALS = (
    "بكام", "سعره", "سعرها", "الاسعار", "كام", "بقى كام", "عامل كام",
    "الاحجام", "حجم", "ملي", "ml", "اطلب", "هاخد", "خدلي", "عايز اشتري",
    "عايزه اشتري", "هشتري", "اشتريه",
)

# Explicit "answer my question" markers that mean a factual question, not a purchase.
_FACTUAL_SIGNALS = (
    "ريحته ايه", "ريحتها ايه", "مكوناته", "نوتاته", "فيه ايه", "ثباته",
    "فوحانه", "مناسب لايه", "ينفع ل", "ايه الفرق", "يعني ايه",
)


def derive(request_type, message=None, intent=None, objection=None, history=None):
    """The sales stage for this turn.

    `objection` outranks the classification: a customer objecting to a price has usually
    been classified `faq` or `product_info`, and answering that as a product question is
    exactly the defend-instead-of-address failure.
    """
    if objection is not None:
        return COMPLAINT if objection.is_complaint else OBJECTION

    if request_type == "order":
        return ORDER_COLLECTION
    if request_type == "order_cancel":
        return ORDER_COLLECTION
    if request_type == "identification":
        return IDENTIFICATION
    if request_type == "comparison":
        return COMPARISON
    if request_type == "handoff":
        return COMPLAINT

    normalized = normalize_arabic(message or "")

    if request_type == "product_info":
        # A price or size question is purchase-adjacent; a "what does it smell like"
        # question is not, and closing on it is premature.
        if any(signal in normalized for signal in _FACTUAL_SIGNALS):
            return RECOMMENDATION
        if any(signal in normalized for signal in _BUYING_SIGNALS):
            return PURCHASE_INTENT
        return RECOMMENDATION

    if request_type == "recommendation":
        if constraints.taste_constraint_count(intent) > 0:
            return RECOMMENDATION
        return DISCOVERY

    return DISCOVERY


def closing_allowed(stage):
    """Whether this turn has earned a "shall I place the order" close."""
    return stage in CLOSING_STAGES
