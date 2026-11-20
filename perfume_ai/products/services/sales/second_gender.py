"""A men's and a women's perfume asked for in one message, chosen one at a time.

"عايز رجالي خشبي وحريمي فلورال" gets `gender.MULTIPLE_QUESTION` ("نبدأ بالرجالي ولا الحريمي؟"). Before
this module the request ended with the first perfume: `multiple` is never saved, so once the customer
picked a side nothing recorded the other one (conversation 649 got men's picks and the sister's
perfume never came up again). Both scents were also searched together: the extractor returns
"رجالي خشبي وحريمي فلورال" as one profile, woody AND floral, for each perfume.

State is `conversation.sales_state[STATE_KEY]` (preferences are rewritten to the persisted keys
every turn, so nothing parked there survives):

    {"scents": {"male": [...], "female": [...]} or {},  # each side's own scent, when it said one
     "shared": {...},                                   # the rest of the request, for both
     "current": "male" | "female",                      # absent until the customer picks a side
     "profiles": {"male": {...}},                       # the side left behind, to come back to
     "offered": bool}                                   # "نكمل بالحريمي؟" already asked

Owner decision (2026-11-20): the second perfume is brought up once, after the first is in the
cart, as "تمام، نكمل بالحريمي؟", and nothing is pitched for it until the customer says yes.
"""

import re

from . import gender as sales_gender

STATE_KEY = "gender_queue"
_OTHER = {"male": "female", "female": "male"}
_LABEL = {"male": "الرجالي", "female": "الحريمي"}
# The clause split `router._per_recipient_scents` uses for "الفريش ليا، والسويت لأخويا".
_SEGMENT = re.compile(r"[،,.!؟?\n]|\s+و(?=\S)")
_TOTAL_BUDGET_KEYS = ("max_price", "budget_scope", "budget_strict", "purchase_quantity")


def split_scents(message, fresh=None):
    """{"male": [tags], "female": [tags]} when each side was given its own scent, else {}.

    Tags come from the extractor's evidence where it quoted the words (it reads فانيليا as sweet,
    which the keyword table does not), and from the keyword table otherwise. A scent said once for
    both ("عايز حاجه فريش رجالي وحريمي") is shared, not split: like `_per_recipient_scents`, a split
    needs both sides to have tags and the two sets to differ.
    """
    from ..scent_description import _tag_mentions
    from ..static_faq_service import normalize_arabic

    fresh = fresh or {}
    known = set(fresh.get("scent_tags") or ())
    evidence = []
    for entry in fresh.get("scent_evidence") or ():
        if not isinstance(entry, dict) or entry.get("polarity", "positive") != "positive":
            continue
        tags = [tag for tag in entry.get("tags") or () if isinstance(tag, str) and (not known or tag in known)]
        if isinstance(entry.get("text"), str) and entry["text"].strip() and tags:
            evidence.append((normalize_arabic(entry["text"]), tags))
    found = {}
    for segment in _SEGMENT.split(normalize_arabic(message or "")):
        side = sales_gender._choice(sales_gender._text(segment))
        if side not in _OTHER:
            continue
        tags = [tag for text, tags in evidence if text in segment for tag in tags]
        if not tags:
            tags = [tag for tag, _, _, negative in _tag_mentions(segment) if not negative]
        if tags:
            bucket = found.setdefault(side, [])
            bucket.extend(tag for tag in tags if tag not in bucket)
    if len(found) == 2 and set(found["male"]) != set(found["female"]):
        return found
    return {}


def start(conversation, message, fresh):
    """Record both sides on the turn that asks which to start with."""
    if conversation is None or getattr(conversation, "pk", None) is None:
        return
    scents = split_scents(message, fresh)
    preferences = dict(conversation.preferences or {})
    if scents:
        # The merged profile holds both sides' scents; only what they share stays on it.
        own = set(scents["male"]) ^ set(scents["female"])
        kept = [tag for tag in preferences.get("scent_tags") or () if tag not in own]
        if kept:
            preferences["scent_tags"] = kept
        else:
            preferences.pop("scent_tags", None)
            preferences.pop("scent_match", None)
    state = dict(conversation.sales_state or {})
    state[STATE_KEY] = {"scents": scents,
                        "shared": {key: value for key, value in preferences.items() if key != "gender"}}
    conversation.preferences = preferences
    conversation.sales_state = state
    conversation.save(update_fields=["preferences", "sales_state"])


def switch(conversation, message, state, store=None):
    """(profile, foreign scent tags) when this message moves to a side, else None.

    A sibling of `conversation_service._switch_recipient`: the caller uses the profile as the saved
    preferences and drops the foreign tags from what the extractor re-read out of the history.
    `state` is the sales state the caller persists; it is updated in place.
    """
    queue = state.get(STATE_KEY)
    if conversation is None or not message or not isinstance(queue, dict):
        return None
    choice = sales_gender.customer_choice(message, store=store)
    current = queue.get("current")
    if current not in _OTHER:
        # The answer to "نبدأ بالرجالي ولا الحريمي؟" (or to the gender question asked after it).
        if choice not in _OTHER:
            return None
        if not sales_gender.question_pending(conversation=conversation):
            # The question is behind us and the customer has moved on to something else.
            state.pop(STATE_KEY, None)
            return None
        target = choice
    else:
        target = _OTHER[current]
        accepted = choice is None and sales_gender.accepts_offer(message) \
            and sales_gender.offer_pending(conversation=conversation)
        if choice != target and not accepted:
            return None

    preferences = dict(conversation.preferences or {})
    scents = queue.get("scents") or {}
    profiles = dict(queue.get("profiles") or {})
    if current in _OTHER:
        profiles[current] = preferences
        restored = dict(profiles.pop(target, None) or queue.get("shared") or {})
        if target not in (queue.get("profiles") or {}) and scents.get(target):
            restored["scent_tags"] = list(scents[target])
        if preferences.get("budget_scope") == "total":
            # A combined budget covers both bottles, as it does across recipients.
            for key in _TOTAL_BUDGET_KEYS:
                if key in preferences and key not in restored:
                    restored[key] = preferences[key]
    else:
        restored = preferences
        if scents.get(target):
            restored["scent_tags"] = list(scents[target])
    restored["gender"] = target
    state[STATE_KEY] = {**queue, "current": target, "profiles": profiles, "offered": False}
    foreign = {tag for side, tags in scents.items() if side != target for tag in tags}
    return restored, foreign - set(restored.get("scent_tags") or ())


def _save(conversation, state):
    from products.models import Conversation
    Conversation.objects.filter(pk=conversation.pk).update(sales_state=state)
    conversation.sales_state = state


def offer_line(conversation, items):
    """"تمام، نكمل بالحريمي؟" once the first side's perfume is in the cart, else "".

    Asked once. The order turn after it (the customer sent their details instead of answering)
    closes the queue, and so does a cart that already holds the other side's perfume.
    """
    if conversation is None or getattr(conversation, "pk", None) is None or not items:
        return ""
    state = dict(conversation.sales_state or {})
    queue = state.get(STATE_KEY)
    if not isinstance(queue, dict) or queue.get("current") not in _OTHER:
        return ""
    target = _OTHER[queue["current"]]
    if queue.get("offered") or any(item["variant"].product.gender == target for item in items):
        state.pop(STATE_KEY)
        _save(conversation, state)
        return ""
    state[STATE_KEY] = {**queue, "offered": True}
    _save(conversation, state)
    return f"تمام، نكمل ب{_LABEL[target]}؟"


def decline(conversation, message):
    """Close the queue when the offer is answered no; True if it was."""
    if conversation is None or getattr(conversation, "pk", None) is None:
        return False
    state = dict(conversation.sales_state or {})
    if STATE_KEY not in state or not sales_gender.declines_offer(message) \
            or not sales_gender.offer_pending(conversation=conversation):
        return False
    state.pop(STATE_KEY)
    _save(conversation, state)
    return True
