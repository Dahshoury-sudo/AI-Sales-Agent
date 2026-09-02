"""Resolve who a perfume is for, preferring data over asking.

The failure this exists for: "عندك حاجه زي 9pm بتاع افنان؟" was answered with
"بتدور على عطر رجالي ولا حريمي؟". 9PM is in the catalogue with gender=male, so the
router asked a question its own database already answered — and spent the customer's
most information-dense turn on protocol.

The old gate scanned for literal Arabic gender words and nothing else, so any request
carrying its gender implicitly (a named perfume, a similarity target) read as "gender
unknown". Worse, the gate ran before the taste-information check, which meant the
`similar_to` clause added to that check specifically to protect lookalike requests was
unreachable whenever a gender was absent.

Resolution order, strongest evidence first:

  1. what the extractor already put in the intent;
  2. explicit gender words in the message or history;
  3. the recorded gender of any catalogue perfume named in the conversation —
     including the similarity target, which is the whole point;
  4. unresolved.

Step 3 deliberately ignores `unisex` products as evidence: a unisex perfume tells us
nothing about who is shopping, so inferring from it would be inventing a fact.
"""

from ..static_faq_service import normalize_arabic

# The single copy of the vocabulary the router used inline. Relationship words
# ("لمراتي") are simultaneously gender clues and gift clues, which is why
# sales.constraints reuses the same idea for gift detection.
#
# Matching is substring-based, so an entry earns its place only if it cannot appear inside an
# unrelated word — and on the female side that rules out almost every short form, because in
# Egyptian "ست" is also the number six. Rejected after testing against real phrasings:
#
#   * bare "ست" — inside "ستة", "مستحضر", and "لستة" (which normalize_arabic delivers as
#     "لسته", since it maps ة→ه);
#   * "الست" — "بكام الست حجات دي" is six items, not a woman, and it resolved female;
#   * "ستي" — inside "ستين", so "حاجة بستين جنيه" resolved female.
#
# "للست" survives because the doubled lam does not occur in the number forms. The asymmetry
# with "راجل" below is therefore a property of the language, not an oversight: there is no
# equally safe bare female word. It is also why the recommendation prompt asks "رجالي ولا
# حريمي" — those two are the least collision-prone words in this file, and a question whose
# answer this vocabulary cannot read is not a question at all (conversation 910).
MALE_WORDS = (
    # Bare, because a direct question invites a bare answer. "راجل" is the most natural
    # one-word reply and was the wording the *old* prompt invited with "العطر لراجل ولا لست؟",
    # yet it matched nothing here — so a customer who answered the question was treated as
    # never having answered it. Subsumes "لراجل", "انا راجل" and "أنا راجل", which is why
    # those are gone rather than kept alongside it.
    "راجل",
    "رجالي", "رجالى", "للرجال", "male", "رجاليه", "ولادي", "شبابي", "شاب",
    "رجاله", "عريس", "لصاحبي", "لاخويا", "لأخويا", "لابويا", "لأبويا",
    "لخطيبي", "لجوزي", "لابني", "لعمي", "لخالي", "انا ولد",
)
FEMALE_WORDS = (
    "للست",
    "حريمي", "حريمى", "للبنات", "للستات", "female", "نسائي", "بناتي", "بنت",
    "بنات", "عروسة", "عروسه", "لصاحبتي", "لاختي", "لأختي", "لماما",
    "لخطيبتي", "لمراتي", "لبنتي", "لطنطي", "لخالتي", "انا بنت", "أنا بنت",
    "لواحده", "لمرات",
)
UNISEX_WORDS = (
    "يونيسيكس", "unisex", "للجنسين", "bisexual", "بايسكشوال", "بايسيكشوال",
)

# Any of these means the customer told us who it is for, even if we cannot tell which
# — the router uses this to decide whether asking is still worth a turn.
ALL_WORDS = MALE_WORDS + FEMALE_WORDS + UNISEX_WORDS


def _conversation_text(message, history=None):
    """The customer's own words, normalised.

    Bot replies are excluded on purpose: scanning them would match the gender words
    the bot itself uses when asking the question, so every follow-up would look as
    though the customer had answered.
    """
    parts = [normalize_arabic(message or "")]
    for entry in history or ():
        if entry.get("role") == "user":
            parts.append(normalize_arabic(entry.get("content", "")))
    return " ".join(parts)


def mentioned_in_words(message, history=None):
    """Did the customer name a gender at all, in any form?"""
    text = _conversation_text(message, history)
    return any(normalize_arabic(word) in text for word in ALL_WORDS)


def from_words(message, history=None):
    """Gender stated outright, or implied by a relationship word."""
    text = _conversation_text(message, history)

    if any(normalize_arabic(word) in text for word in UNISEX_WORDS):
        return "unisex"

    male = any(normalize_arabic(word) in text for word in MALE_WORDS)
    female = any(normalize_arabic(word) in text for word in FEMALE_WORDS)
    if male and not female:
        return "male"
    if female and not male:
        return "female"
    # Both present is the "واحد ليا وواحد لمراتي" case. The extractor owns that
    # decision (unisex, or 'multiple' when the customer rejects unisex); guessing
    # here would override it.
    return None


def _catalogue_gender(name, store):
    """The recorded gender of a catalogue perfume named by the customer.

    Matching is delegated to sales.naming so this cannot drift from how the similarity
    reference and the exclusion list resolve the same strings.
    """
    if not name or store is None:
        return None

    from . import naming

    product = naming.match_product(name, store)
    if product is None:
        return None
    # A unisex perfume is not evidence about who is shopping.
    return product.gender if product.gender in ("male", "female") else None


def from_named_products(intent, message, history=None, store=None):
    """Gender inferred from perfumes the customer actually named.

    The similarity target is checked first: "حاجة شبه سوفاج" is a stronger statement
    about who the customer is than anything else in the message.
    """
    gender = _catalogue_gender((intent or {}).get("similar_to"), store)
    if gender:
        return gender

    if store is None:
        return None

    from products.models import Product

    text = _conversation_text(message, history)
    found = set()
    for product in Product.objects.filter(store=store, is_active=True).only(
        "name", "gender"
    ):
        normalized = normalize_arabic(product.name)
        if len(normalized) > 3 and normalized in text and product.gender in ("male", "female"):
            found.add(product.gender)

    # Two perfumes of opposite gender named together tells us nothing.
    return found.pop() if len(found) == 1 else None


def resolve(intent, message, history=None, store=None):
    """The customer's gender requirement, or None if genuinely unknowable.

    Returns the value the caller should put in `intent["gender"]`. Never returns
    'multiple' — that is a routing signal the extractor owns.
    """
    existing = (intent or {}).get("gender")
    if existing:
        return existing

    return from_words(message, history) or from_named_products(
        intent, message, history, store
    )
