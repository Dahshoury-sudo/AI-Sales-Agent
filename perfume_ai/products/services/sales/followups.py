"""Resolve short replies against the question and products actually shown."""
from dataclasses import dataclass
import re

from . import naming


def latest_reply(history=None, conversation=None):
    if conversation is not None:
        row = conversation.messages.filter(
            role__in=("assistant", "agent"), delivery_status="sent",
        ).order_by("-created_at", "-pk").first()
        if row is not None:
            return {"role": row.role, "content": row.content,
                    "internal_context": row.internal_context}
    return next((row for row in reversed(history or [])
                 if row.get("role") in ("assistant", "agent")), {})


def _last_sentence(text):
    sentences = re.split(r"[.!?؟\n]+", naming.normalize_product_name(text))
    return next((part.strip() for part in reversed(sentences) if part.strip()), "")


def budget_answer_expected(history=None, conversation=None):
    row = latest_reply(history, conversation)
    if row.get("role") != "assistant":
        return False
    question = _last_sentence(row.get("content", ""))
    return bool(re.search(r"ميزاني|\bbudget\b|(?:حدود|رينج)\s+كام", question)
                and re.search(r"كام|ايه|قد ايه|\b(?:what|how much)\b", question))


def accepts_price_offer(message, history=None, conversation=None):
    """A short yes accepts only the latest invitation to give product prices."""
    answer = re.sub(r"[^\w\s]", " ", naming.normalize_product_name(message))
    answer = " ".join(answer.split())
    if not re.fullmatch(
        r"(?:اه|اها|ايوه|ايوا|نعم|تمام|ماشي|اوك|اوكي|حاضر|موافق|اشطا|قول|"
        r"yes|yeah|yep|ok|okay|sure|please do)"
        r"(?:\s+(?:قول|قولي|قوللي|يا فندم|شكرا|please))?", answer,
    ):
        return False
    row = latest_reply(history, conversation)
    if row.get("role") != "assistant":
        return False
    question = _last_sentence(row.get("content", ""))
    if re.search(r"(?<!\w)(?:اطلب|اطلبه|تطلب|تطلبي|نطلب|نضيف|اضيف|احجز|نحجز|نكمل|"
                 r"احولك|اوصلك|buy|order|checkout|add|reserve)(?!\w)", question):
        return False
    invitation = re.search(r"تحب|حابب|حابه|عايز|عاوزه|\b(?:would you like|shall i|want me to)\b", question)
    information = re.search(r"اقول|تعرف|اعرف|اوضح|اوريلك|اشرح|تشوف|اعرض|\b(?:tell|show|explain|know|see)\b", question)
    direct_offer = re.search(r"(?:تحب|حابب|حابه|عايز|عاوزه)\s+(?:الاسعار|اسعارهم|الاحجام)|"
                             r"\bwould you like (?:the )?(?:prices?|sizes?)\b", question)
    return bool(invitation and (information or direct_offer)
                and re.search(r"اسعار|سعر|بكام|احجام|\b(?:prices?|sizes?)\b", question))


@dataclass(frozen=True)
class NumericReference:
    status: str = "no_match"
    candidates: tuple = ()

    @property
    def product(self):
        return self.candidates[0] if self.status == "resolved" else None


def numeric_reference(message, store, conversation=None, history=None, *, respect_budget=True):
    """Match a bare number as a catalogue name, without fuzzy or global aliases."""
    token = naming.normalize_product_name(message).strip()
    if store is None or not re.fullmatch(r"[0-9]{2,6}", token):
        return NumericReference()
    if conversation is not None and conversation.store_id != store.pk:
        return NumericReference()
    if respect_budget and budget_answer_expected(history, conversation):
        return NumericReference()

    from products.models import Product
    from .described import supported_offered_names

    candidates = tuple(
        product for product in Product.objects.filter(store=store)
        .select_related("brand").prefetch_related("variants").order_by("pk")
        if token in naming.tokens(product.name)
    )
    if not candidates:
        return NumericReference()

    row = latest_reply(history, conversation)
    if row.get("role") == "assistant":
        names = [product.name for product in candidates]
        if conversation is not None or row.get("internal_context"):
            offered = supported_offered_names(
                row.get("content", ""), row.get("internal_context", ""), names,
            )
        else:
            # History-only callers have no injected rows. Still require an exact
            # catalogue name in the assistant's text, never a guessed alias.
            offered = naming.names_in(row.get("content", ""), names)
        recent = tuple(product for product in candidates if product.name in offered)
        if recent:
            candidates = recent
    return NumericReference("resolved" if len(candidates) == 1 else "ambiguous", candidates)
