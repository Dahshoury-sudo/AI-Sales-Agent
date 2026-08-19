from datetime import timedelta
from django.utils import timezone
from products.models import Conversation, Message


def create_conversation(store=None, platform="web", platform_sender_id=""):
    return Conversation.objects.create(store=store, platform=platform, platform_sender_id=platform_sender_id)

def get_or_create_platform_conversation(store, platform, sender_id):
    # Get the latest conversation for this user
    conversation = Conversation.objects.filter(
        store=store,
        platform=platform,
        platform_sender_id=sender_id
    ).order_by('-created_at').first()

    now = timezone.now()
    created = False

    if conversation:
        # Check the last message in this conversation
        last_message = conversation.messages.order_by('-created_at').first()
        
        # If there's a last message and it's older than 24 hours, create a new conversation
        if last_message and (now - last_message.created_at) > timedelta(hours=24):
            conversation = create_conversation(store, platform, sender_id)
            created = True
        # If there are no messages yet (edge case) or last message is recent, use the existing one
    else:
        # No previous conversation found, create a new one
        conversation = create_conversation(store, platform, sender_id)
        created = True

    return conversation, created


def get_conversation(conversation_id, store=None):
    try:
        if store:
            return Conversation.objects.get(id=conversation_id, store=store)
        return Conversation.objects.get(id=conversation_id)
    except (Conversation.DoesNotExist, ValueError, TypeError):
        return None


def save_message(conversation, role, content, internal_context=""):
    return Message.objects.create(
        conversation=conversation,
        role=role,
        content=content,
        internal_context=internal_context,
    )


def get_conversation_messages(conversation, limit=8):
    messages = conversation.messages.order_by("-created_at")[:limit]
    return reversed(messages)


# Roles the Chat Completions API accepts. A human agent's "agent" row is dropped
# rather than mapped onto "assistant": the bot must not adopt a colleague's voice or
# inherit promises it cannot keep. The cost is that the bot loses sight of what the
# human said, which is the safer of the two failures.
LLM_ROLES = ("user", "assistant")


def build_llm_history(conversation, limit=8):
    """History for a model call, as role/content dicts.

    Both callers built this inline and identically (products/tasks.py and
    products/views.py), so neither would have picked up the agent-role filter.
    """
    return [
        {"role": message.role, "content": message.content}
        for message in get_conversation_messages(conversation, limit=limit)
        if message.role in LLM_ROLES
    ]


# The durable half of the intent schema (products/services/ai/intent.py). exclude_names
# is deliberately absent: it is per-request by design — intent.py only fills it when the
# customer asks for an alternative — and persisting it would permanently blacklist
# perfumes the customer merely mentioned once.
PERSISTED_PREFERENCE_KEYS = (
    "gender",
    "max_price",
    "perfume_type",
    "brand",
    "season",
    "occasion",
    "notes",
    "longevity",
    "projection",
)

# "multiple" is a transient signal, not a taste: it means the customer wants a men's and
# a women's perfume and the router must ask which to start with. Persisting it would
# restore that question on every later turn that happens to omit a gender.
_TRANSIENT_GENDER = "multiple"


def _is_set(value):
    """A preference the customer actually expressed, as opposed to an empty slot."""
    return value not in (None, "", [], {})


def merge_preferences(conversation, intent):
    """Fill gaps in a freshly extracted intent from what the customer said earlier.

    extract_intent re-derives every criterion from the last 8 messages alone, so a
    budget or gender given five turns back is simply gone. The consequences are not
    subtle: with max_price missing, recommendation.py switches price_instruction to
    "ممنوع تذكر الأسعار", so a bot that was quoting prices stops, search_products drops
    its price filter and starts offering perfumes over budget, and the router asks for
    a budget the customer already gave.

    Freshly extracted values always win over saved ones, matching the override rule the
    extractor prompt already states — a customer who changes their mind must not be
    contradicted by their own history.
    """
    merged = dict(intent or {})
    if conversation is None:
        return merged

    saved = conversation.preferences or {}

    for key in PERSISTED_PREFERENCE_KEYS:
        if not _is_set(merged.get(key)) and _is_set(saved.get(key)):
            merged[key] = saved[key]

    to_save = {
        key: merged[key] for key in PERSISTED_PREFERENCE_KEYS if _is_set(merged.get(key))
    }
    if to_save.get("gender") == _TRANSIENT_GENDER:
        to_save.pop("gender")

    if to_save != saved:
        conversation.preferences = to_save
        conversation.save(update_fields=["preferences"])

    return merged