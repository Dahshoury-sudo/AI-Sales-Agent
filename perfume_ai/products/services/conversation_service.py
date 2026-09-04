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
#
# avoid_notes, avoid_traits and similar_to are durable for the same reason a budget is: a
# customer who said "مش عايز حاجة تقيلة" five turns ago still does not want one, and
# re-deriving intent from an 8-message window loses that. Losing an exclusion is worse
# than losing a preference — it means recommending the exact thing they rejected.
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
    "avoid_notes",
    "avoid_traits",
    "similar_to",
    # similar_to_notes travels with similar_to or the pair is useless: keeping the name
    # while dropping the notes meant that on any later turn a reference we do not stock
    # resolved to no catalogue product and no fallback notes, so _resolve_reference
    # returned None and similarity silently switched itself off mid-conversation.
    "similar_to_notes",
    # "مش منتشرة" is a taste, not a passing remark, and losing it dropped the one signal
    # that favours the store's own exclusive blends — its highest-margin stock.
    "wants_uncommon",
)

# "multiple" is a transient signal, not a taste: it means the customer wants a men's and
# a women's perfume and the router must ask which to start with. Persisting it would
# restore that question on every later turn that happens to omit a gender.
_TRANSIENT_GENDER = "multiple"

# Axes that a single sentence can flip wholesale. When the customer reverses one of
# these, every key on the same axis has to be dropped rather than gap-filled — see
# _contradicted_keys.
_AXES = (
    ("scent", ("notes", "avoid_notes", "avoid_traits", "perfume_type")),
    ("season", ("season",)),
    ("occasion", ("occasion",)),
    ("performance", ("longevity", "projection")),
    ("reference", ("similar_to", "similar_to_notes")),
)

# How a customer says "ignore what I just told you". Kept narrow on purpose: a false
# positive here throws away a preference the customer still holds.
_REVERSAL_MARKERS = (
    "غيرت رايي", "غيرت رأيي", "بدلت رايي", "بدلت رأيي",
    "لا مش كده", "لا مش ده", "لا مش دي", "بلاش", "الغي اللي قلته",
    "انسى اللي قلته", "انسي اللي قلته", "مش عايز اللي قلته",
    "عدلت عن", "رجعت في كلامي",
)


def _is_reversal(message):
    """Did the customer explicitly retract what they said earlier?"""
    if not message:
        return False
    from .static_faq_service import normalize_arabic

    normalized = normalize_arabic(message)
    return any(normalize_arabic(marker) in normalized for marker in _REVERSAL_MARKERS)


# How a customer accepts an offer to drop a constraint, as opposed to retracting a preference
# unprompted. `_REVERSAL_MARKERS` above is about the customer changing their own mind; this is
# about them answering a question we asked. Conversation 932 is the failure: the reply offered
# "نفس البراند بس رجالي، ولا براند تاني حريمي", and "من براند تاني حريمي" matched no reversal
# marker, named no replacement brand, and so left `brand: "Versace"` to be gap-filled back in
# three turns running.
#
# Written in the form `normalize_arabic` produces (ة→ه, أ/إ→ا, ى→ي), since both sides are
# normalized before comparison — so one spelling per phrase is enough.
_BRAND_RELAX_MARKERS = (
    "براند تاني", "ماركه تانيه", "براند غير", "براند مختلف", "ماركه مختلفه",
    "اي براند", "اي ماركه", "براند تانيه", "من غير براند",
)

# The same acceptance, said in words that only mean it because of what was just offered. These
# fire only when `pending` says a brand relaxation was on the table — "حاجه تانيه" on any other
# turn is a request for a different *perfume*, which `ai/intent.py` already routes to
# `exclude_names`, and treating it as a brand withdrawal there would throw away a filter the
# customer still wants.
_RELAX_ACCEPT_PHRASES = (
    "حاجه تانيه", "اي حاجه", "التانيه", "التاني", "الثانيه", "الخيار التاني",
)

# Bare agreement. Matched against the whole message rather than as a substring: "اه" is two
# letters that sit inside ordinary words — مياه, معاه, and anything ending ـاة once
# `normalize_arabic` has folded ة to ه — so a substring test here would read agreement into
# sentences that contain none. A customer who agrees in one word writes only that word.
_RELAX_ACCEPT_EXACT = (
    "اه", "اها", "ايوه", "ايوا", "تمام", "ماشي", "اوك", "ok", "okay", "yes", "yep",
    "اه صح", "تمام كده", "ماشي كده", "اه تمام", "حاضر", "طيب",
)

_ACCEPT_STRIP = " \t\n.،,!؟?:؛;\"'()"


def _relaxed_keys(message, pending=None):
    """Constraints the customer has just agreed to drop, from their words alone.

    Returns candidates, not conclusions: `merge_preferences` still refuses to relax a key the
    customer overrode with a real value in the same breath. Brand only, for now — the other
    half of 932's offer ("نفس البراند بس رجالي") already works, because `sales/gender.py`
    reads رجالي out of the message and flips the gender itself. The marker tables are keyed by
    constraint so `perfume_type` and `season` can join without reshaping anything.
    """
    if not message:
        return frozenset()
    from .static_faq_service import normalize_arabic

    normalized = normalize_arabic(message)
    if any(normalize_arabic(marker) in normalized for marker in _BRAND_RELAX_MARKERS):
        return frozenset({"brand"})

    if "brand" in (pending or {}):
        if any(normalize_arabic(phrase) in normalized for phrase in _RELAX_ACCEPT_PHRASES):
            return frozenset({"brand"})
        bare = normalized.strip(_ACCEPT_STRIP)
        if any(bare == normalize_arabic(word) for word in _RELAX_ACCEPT_EXACT):
            return frozenset({"brand"})

    return frozenset()


def _contradicted_keys(intent, message):
    """Saved keys that must NOT be restored on this turn.

    The failure this exists for: "لا غيرت رايي، عايزه حاجه تقيله للشتا" arrived with a
    fresh intent carrying season=winter but no `notes`, so the gap-filler dutifully
    restored notes=["fresh"] from the summer request the customer had just retracted —
    and the active requirement set became "heavy winter AND fresh". Per-key freshness is
    not enough, because a reversal expresses itself on a *different key* from the one it
    contradicts.

    So on an explicit reversal, any axis the new intent speaks to at all is cleared of
    its saved values entirely; axes the customer did not touch are still carried, since
    changing your mind about the season says nothing about your budget.
    """
    if not _is_reversal(message):
        return frozenset()

    contradicted = set()
    for _, keys in _AXES:
        if any(_is_set((intent or {}).get(key)) for key in keys):
            contradicted.update(keys)
    return frozenset(contradicted)


def _is_set(value):
    """A preference the customer actually expressed, as opposed to an empty slot.

    `False` counts as unset, matching sales.constraints._is_set. `wants_uncommon: false` is
    the extractor reporting the *absence* of a preference, and a plain membership test
    against (None, "", [], {}) treats it as present — so once wants_uncommon joined
    PERSISTED_PREFERENCE_KEYS every conversation began saving `wants_uncommon: False` as
    though the customer had asked for something.
    """
    if value is False:
        return False
    return value not in (None, "", [], {})


def merge_preferences(conversation, intent, message=None, pending=None):
    """Fill gaps in a freshly extracted intent from what the customer said earlier.

    extract_intent re-derives every criterion from the last 8 messages alone, so a
    budget or gender given five turns back is simply gone. The consequences are not
    subtle: with max_price missing, recommendation.py switches price_instruction to
    "ممنوع تذكر الأسعار", so a bot that was quoting prices stops, search_products drops
    its price filter and starts offering perfumes over budget, and the router asks for
    a budget the customer already gave.

    Freshly extracted values always win over saved ones, matching the override rule the
    extractor prompt already states — a customer who changes their mind must not be
    contradicted by their own history. `message` is used to detect an explicit reversal,
    where gap-filling itself is the wrong behaviour rather than merely a stale one.

    `pending` is what the previous reply offered to relax, from
    `sales.described.pending_relaxations`. It is what lets a terse "التانية" be read as an
    answer; the unambiguous phrasings need no such help.
    """
    merged = dict(intent or {})
    if conversation is None:
        return merged

    saved = conversation.preferences or {}
    contradicted = _contradicted_keys(intent, message)

    # Deleted, not merely left un-gap-filled. `ai/intent.py` tells the extractor to accumulate
    # preferences out of the history, and it does: on conversation 931's turn 9 `raw_intent` came
    # back carrying brand "Versace" for a message whose entire text was "1200". So the stale
    # value arrives on the fresh intent too, and a fix that only declined to restore it from
    # `saved` would have left 932's loop running exactly as it was.
    relaxed = set(_relaxed_keys(message, pending))
    for key in tuple(relaxed):
        # An override is not a withdrawal. "براند تاني زي ديور" both accepts the offer and names
        # the replacement, and the named brand has to survive — so relax only when the extractor
        # brought back nothing, or brought back the very value being dropped.
        fresh, previous = merged.get(key), saved.get(key)
        if _is_set(fresh) and _is_set(previous) and str(fresh).strip().lower() != str(previous).strip().lower():
            relaxed.discard(key)
            continue
        if _is_set(fresh) and not _is_set(previous):
            relaxed.discard(key)
            continue
        merged.pop(key, None)

    for key in PERSISTED_PREFERENCE_KEYS:
        if key in contradicted or key in relaxed:
            continue
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