"""Customer gender choices and the pending recommendation question.

Recommendations require a customer choice, supplied now or saved for the same
recipient. The legacy catalog resolver remains available to product information.
"""

import re

from ..static_faq_service import normalize_arabic

QUESTION = "محتاج برفان رجالي ولا حريمي؟"
_LEGACY_QUESTION = "حضرتك محتاج برفان رجالي ولا حريمي"
REQUIRED_MARKER = "GENDER_REQUIRED"
MULTIPLE_QUESTION = "نختار واحد واحد. نبدأ بالرجالي ولا الحريمي؟"
_LEGACY_MULTIPLE_QUESTION = "خلينا نختارهم واحد واحد عشان أقدر أركز معاك. تحب نبدأ بالرجالي ولا الحريمي؟"
VALID_GENDERS = frozenset({"male", "female", "unisex"})

# The single copy of the vocabulary the router used inline. Relationship words
# ("لمراتي") are simultaneously gender clues and gift clues, which is why
# sales.constraints reuses the same idea for gift detection.
#
# Choice parsing uses word boundaries and common Arabic prefixes. Ambiguous forms
# such as "ست" and "الست" remain excluded because they can mean the number six;
# "للست" identifies a recipient without that ambiguity.
MALE_WORDS = (
    # Bare, because a direct question invites a bare answer. "راجل" is the most natural
    # one-word reply and was the wording the *old* prompt invited with "العطر لراجل ولا لست؟",
    # yet it matched nothing here — so a customer who answered the question was treated as
    # never having answered it. Prefix-aware matching also handles "لراجل", while
    # word boundaries recognize "راجل" in "انا راجل" and "أنا راجل".
    "راجل",
    "رجالي", "رجالى", "للرجال", "male", "رجاليه", "ولادي", "شبابي", "شاب",
    "رجاله", "عريس", "لصاحبي", "لاخويا", "لأخويا", "لابويا", "لأبويا",
    "لخطيبي", "لجوزي", "لابني", "لعمي", "لخالي", "انا ولد",
    # "عايز واحد لماما وواحد لبابا" parsed as female alone and the father was dropped. Only the
    # ل-forms: a bare "بابا" turns up in sentences that are not about who the perfume is for.
    "لبابا", "لوالدي",
)
FEMALE_WORDS = (
    "للست",
    "حريمي", "حريمى", "للبنات", "للستات", "female", "نسائي", "بناتي", "بنت",
    "بنات", "عروسة", "عروسه", "لصاحبتي", "لاختي", "لأختي", "لماما",
    "لخطيبتي", "لمراتي", "لبنتي", "لطنطي", "لخالتي", "انا بنت", "أنا بنت",
    "لواحده", "لمرات", "لمامتي", "لوالدتي",
)
UNISEX_WORDS = (
    "يونيسيكس", "unisex", "للجنسين", "bisexual", "بايسكشوال", "بايسيكشوال",
    # "يونيسكس" is how customers actually spell it: 8 of the 3,701 stored customer messages, and
    # none of them parsed. Conversation 1314 said it three times ("منا قولت يونيسكس") and got the
    # binary question three times, until the customer gave up and said "رجالي". "يونيكس" is the
    # spelling the bot itself used in conversation 365, so customers will echo it.
    "يونيسكس", "يونسكس", "يوني سكس", "يونيكس",
    "uni sex", "unisix", "gender neutral", "genderless",
)

# Any of these means the customer told us who it is for, even if we cannot tell which
# — the router uses this to decide whether asking is still worth a turn.
ALL_WORDS = MALE_WORDS + FEMALE_WORDS + UNISEX_WORDS

# Franco-Arabic spellings are category words too: "rigali" went unrecognised and the scripted gender
# question was sent seven times in one conversation (simulation S13).
_FRANCO_MALE = ("rigali", "regali", "rgali", "rigaly", "regaly", "ragali", "rigale", "regale", "rigalii")
_FRANCO_FEMALE = ("7arimi", "harimi", "7arimy", "harimy", "7areemy", "7aremy", "7arime", "harime", "banati")
_CATEGORIES = {
    "male": ("male", "men", "mens", "men's", "رجالي", "رجاليه", "للرجال", "رجاله", "ولادي", "شبابي", *_FRANCO_MALE),
    "female": ("female", "women", "womens", "women's", "ladies", "حريمي", "حريميه", "نسائي", "نسائيه", "بناتي",
               *_FRANCO_FEMALE),
    "unisex": UNISEX_WORDS,
}
_RECIPIENTS = {
    "male": (*MALE_WORDS, "man", "boy", "for him", "for my husband", "for my boyfriend", "for my fiance",
             "for my father", "for my dad", "for my brother", "for my son", "لزوجي", "لولد"),
    "female": (*FEMALE_WORDS, "woman", "girl", "for her", "for my wife", "for my girlfriend", "for my fiancee",
               "for my mother", "for my mom", "for my sister", "for my daughter", "لزوجتي", "لامي"),
}
_NEGATIVE = re.compile(
    r"(?<!\w)(?:مش|لا|بلاش|بدون|غير|not|no|without|don't|dont)"
    r"(?:\s+(?:عايز(?:ه)?|عاوز(?:ه)?|محتاج(?:ه)?|want|need|a|an|the|for)){0,4}\s*$"
)
_CONNECTOR = re.compile(r"\s*(?:و|او|ولا|and|or|nor)\s*")
_ALTERNATIVE = re.compile(r"(?<!\w)(?:او|ولا|or)(?!\w)")
# One perfume for both sexes, said without the word "unisex". Matched on normalised text.
#
# "ينفع للراجل والست" parsed as *male*: "والست" is deliberately not a female word (see above), so
# only "للراجل" matched. "عايز حاجه رجالي وحريمي فنفس الوقت" (conversation 23) parsed as
# `multiple` and got the two-recipients question. Each form here is narrow on purpose: a "suits"
# verb, or the two category words joined and followed by "at the same time". The plain
# "عايز رجالي وحريمي" and "عطر رجالي وعطر حريمي" still mean two perfumes, and "الاتنين" alone
# almost always means "both of those perfumes" ("بكام الاتنين").
_SUITS = r"(?:[يت](?:نفع|صلح|ناسب|ليق))"
_MEN = r"(?:لل|ل|ال)?(?:راجل|رجاله|رجال|رجل|ولاد|شباب)"
_WOMEN = r"(?:لل|ل|ال)?(?:ست|ستات|سيدات|بنات|بنت)"
_MALE_CATEGORY = r"(?:رجالي|رجاليه|للرجال|رجاله)"
_FEMALE_CATEGORY = r"(?:حريمي|حريميه|نسائي|نسائيه|للستات|للبنات)"
_ONE_FOR_BOTH = re.compile(
    r"(?<!\w)(?:"
    rf"{_SUITS}\s+(?:لل|ل|ال)?(?:اتنين|اثنين)"
    rf"|{_SUITS}\s+(?:{_MEN}\s+و\s*{_WOMEN}|{_WOMEN}\s+و\s*{_MEN})"
    rf"|(?:{_MALE_CATEGORY}\s+و\s*{_FEMALE_CATEGORY}|{_FEMALE_CATEGORY}\s+و\s*{_MALE_CATEGORY})\s*(?:في|ف)\s*نفس\s*الوقت"
    r"|for\s+(?:both\s+)?men\s+and\s+women|for\s+both\s+(?:genders|sexes)"
    r")(?!\w)"
)
_UNISEX_STOP = "|".join(re.escape(" ".join(normalize_arabic(word).split()))
                        for word in sorted(UNISEX_WORDS, key=len, reverse=True))
_REFERENCE_SPAN = re.compile(
    r"(?<!\w)(?:similar\s+to|something\s+like|perfume\s+like|fragrance\s+like|شبه|زي|بديل)\s+"
    r".+?(?=\s+(?:but|بس|لكن|for\s+(?:my|him|her)|ل(?:مراتي|خطيبتي|جوزي|اخويا|اختي|زوجتي)|"
    rf"رجالي|حريمي|{_SUITS}|{_UNISEX_STOP})(?!\w)|[.!؟?،;]|$)"
)
_UNRESOLVED_ANSWER = re.compile(
    r"(?:(?:طيب|طب)\s+)?(?:تمام|اه|ايوه|ماشي|ليا|لنفسي|هديه|لحد عزيز|مش عارف(?:ه)?|"
    r"انت اختار|اختار(?:لي)?(?: انت)?|علي ذوقك|اي حاجه|ok|okay|yes|sure|for me|"
    r"a gift|don't know|not sure|you choose|choose for me|surprise me)[.!؟?]*"
)
# The speaker describing themselves. Matched on normalised text, so "أنا"/"عروسة" arrive as انا/عروسه.
_SPEAKER = re.compile(r"(?<!\w)انا\s+(?:راجل|عريس|عروسه|بنت|ولد|شاب)(?!\w)")
# Asked once the first of two perfumes is in the cart (sales.second_gender). "نكمل بالحريمي؟" is the
# tail of an order reply, so it is recognised by its ending and, with a conversation, by its marker.
OFFER_MARKER = "SECOND_GENDER_OFFER"
_OFFER_TAIL = re.compile(r"نكمل\s+بال(?:رجالي|حريمي)\s*[؟?]\s*$")
_ACCEPTS_OFFER = re.compile(
    r"(?:(?:طيب|طب|ايوه|ايوا|اه|تمام|ماشي|يلا|اوك|اكيد|ياريت|نكمل|ok|okay|yes|sure)[\s،,.!]*)+")
# "تمام كده" / "ماشي خلاص" decline: the leading word agrees with the order, the rest says nothing more.
_DECLINES_OFFER = re.compile(
    r"(?:(?:تمام|ماشي|اوك|ok)[\s،,.!]*)?"
    r"(?:(?:لا|لاء|خلاص|كده|كدا|بس|مش\s+(?:عايز|عاوز|محتاج)(?:ه)?|شكرا|no|nope|thanks)[\s،,.!]*)+")
_OTHER_REQUEST = re.compile(
    r"(?<!\w)(?:ضيف|هاخد|هشتري|اطلب|الغي|الغاء|كنسل|موظف|حد اكلمه|بكام|سعر|ثبات|فوحان|"
    r"شحن|توصيل|عنوان|مواعيد|order|buy|add|cancel|human|price|cost|delivery|shipping)(?!\w)|"
    r"^(?:هل|هو|هي|عندك|how|what|why|is|does)\b"
)


def _text(message):
    return " ".join(normalize_arabic(message or "").replace("’", "'").split())


def _compile_words(table):
    aliases = {}
    for gender, words in table.items():
        for word in words:
            word = _text(word)
            aliases[word] = gender
            if re.match(r"[\u0600-\u06ff]", word):
                # ب/بال: "نبدأ بالرجالي" is the natural answer to MULTIPLE_QUESTION, and conversation 649
                # answered exactly that ("ماشي نبدأ بالرجالي"); it parsed as no choice at all.
                for prefix in ("و", "ل", "ال", "لل", "وال", "ول", "ب", "بال"):
                    aliases[prefix + word] = gender
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(
        re.escape(word) for word in sorted(aliases, key=lambda word: (-len(word), word))
    ) + r")(?!\w)")
    return pattern, aliases


_CATEGORY_WORDS = _compile_words(_CATEGORIES)
_RECIPIENT_WORDS = _compile_words(_RECIPIENTS)


def _groups(text, words):
    pattern, aliases = words
    groups = set()
    previous_end, previous_negative = 0, False
    for match in pattern.finditer(text):
        negative = bool(_NEGATIVE.search(text[:match.start()])) or bool(
            previous_negative and _CONNECTOR.fullmatch(text[previous_end:match.start()])
        )
        if not negative:
            groups.add(aliases[match.group()])
        previous_end, previous_negative = match.end(), negative
    return groups


def _one_for_both(text):
    match = _ONE_FOR_BOTH.search(text)
    return bool(match) and not _NEGATIVE.search(text[:match.start()])


def _without_speaker(text):
    """The text minus "انا راجل"-style self-descriptions when someone else is named.

    "انا عريس جديد وعايز حاجه لمراتي" (conversation 356) and "انا عروسه وعايزه حاجه لصاحبي" (357)
    describe the speaker and then name the recipient; both parsed as `multiple` and would have been
    asked which perfume to start with. The description counts only when the message also asks for
    something for the speaker: "عايز برفان ليا انا راجل وواحد لاختي" (649) is two perfumes.
    """
    masked = _SPEAKER.sub(" ", text)
    if masked == text or not (_groups(masked, _CATEGORY_WORDS) or _groups(masked, _RECIPIENT_WORDS)):
        return text
    from ..conversation_service import _SELF_RECIPIENT
    return text if _SELF_RECIPIENT.search(text) else masked


def _choice(text):
    # Before the category groups: "رجالي وحريمي في نفس الوقت" holds both groups, which alone
    # would read as two recipients.
    if _one_for_both(text):
        return "unisex"
    text = _without_speaker(text)
    groups = _groups(text, _CATEGORY_WORDS) or _groups(text, _RECIPIENT_WORDS)
    if "unisex" in groups:
        return "unisex"
    if len(groups) == 1:
        return next(iter(groups))
    if len(groups) > 1 and not _ALTERNATIVE.search(text):
        return "multiple"
    return None


def canonical(value):
    return value.strip().lower() if isinstance(value, str) and value.strip().lower() in VALID_GENDERS else None


def customer_choice(message, intent=None, store=None):
    """A choice grounded in customer wording, with perfume names masked."""
    text = _text(message)
    if _CATEGORY_WORDS[0].fullmatch(text) or _RECIPIENT_WORDS[0].fullmatch(text):
        return _choice(text)
    if not (_CATEGORY_WORDS[0].search(text) or _RECIPIENT_WORDS[0].search(text) or _ONE_FOR_BOTH.search(text)):
        return None
    names = [(intent or {}).get("similar_to")]
    if store:
        from products.models import Product
        names.extend(Product.objects.filter(store=store).values_list("name", flat=True))
    for name in sorted((_text(name) for name in names if isinstance(name, str) and name), key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " perfume_reference ", text)
    text = _REFERENCE_SPAN.sub(" ", text)
    return _choice(text)


def sanitize_updates(intent, message=None, store=None):
    """Never persist a model-inferred gender from a perfume or customer name."""
    result = dict(intent or {})
    previous = result.pop("gender", None)
    gender = canonical(previous) if message is None else customer_choice(message, result, store)
    if gender:
        result["gender"] = gender
        result["clear_preferences"] = [key for key in (result.get("clear_preferences") or []) if key != "gender"]
        if not result["clear_preferences"]:
            result.pop("clear_preferences")
    return result


def from_customer_history(history, store=None):
    from .reference import _RESET, _normalized
    for row in reversed(history or []):
        if row.get("role") != "user":
            continue
        message = row.get("content", "")
        choice = customer_choice(message, store=store)
        if choice or _RESET.search(_normalized(message)):
            return choice
    return None


def _latest_reply(history=None, conversation=None):
    """(content, internal_context) of the last assistant reply, or None when staff spoke last."""
    if conversation is not None:
        latest = conversation.messages.filter(role__in=("assistant", "agent"), delivery_status="sent").order_by("-created_at", "-pk").first()
        if latest:
            return (latest.content or "", latest.internal_context or "") if latest.role == "assistant" else None
    latest = next((row for row in reversed(history or []) if row.get("role") in ("assistant", "agent")), {})
    return (latest.get("content", ""), "") if latest.get("role") == "assistant" else None


def offer_pending(history=None, conversation=None):
    """Did our last reply offer to move on to the second of two perfumes?"""
    latest = _latest_reply(history, conversation)
    return bool(latest) and (OFFER_MARKER in latest[1] or bool(_OFFER_TAIL.search(_text(latest[0]))))


def accepts_offer(message):
    text = _text(message)
    return len(text.split()) <= 4 and bool(_ACCEPTS_OFFER.fullmatch(text))


def declines_offer(message):
    text = _text(message)
    return len(text.split()) <= 5 and bool(_DECLINES_OFFER.fullmatch(text))


def question_pending(history=None, conversation=None):
    questions = {_text(question) for question in (
        QUESTION, _LEGACY_QUESTION, MULTIPLE_QUESTION, _LEGACY_MULTIPLE_QUESTION,
    )}
    latest = _latest_reply(history, conversation)
    if not latest:
        return False
    content, context = latest
    return (REQUIRED_MARKER in context or "MULTIPLE_RECIPIENTS" in context or _text(content) in questions
            or OFFER_MARKER in context or bool(_OFFER_TAIL.search(_text(content))))


def pending_answer(message, history=None, conversation=None, store=None):
    if not question_pending(history, conversation):
        return False
    text = _text(message)
    if len(text.split()) > 16 or _OTHER_REQUEST.search(text):
        return False
    if offer_pending(history, conversation):
        # "نكمل بالحريمي؟" is answered yes or with the gender; a "تمام" there is a yes, not the
        # undecided answer it would be to the bare gender question.
        return bool(accepts_offer(message) or customer_choice(message, store=store) in ("male", "female"))
    return bool(customer_choice(message, store=store) or _UNRESOLVED_ANSWER.fullmatch(text)
                or _groups(text, _CATEGORY_WORDS))


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
    return any(normalize_arabic(word) in text for word in ALL_WORDS) or bool(_ONE_FOR_BOTH.search(_text(text)))


def from_words(message, history=None):
    """Gender stated outright, or implied by a relationship word."""
    texts = [message] + [row.get("content", "") for row in reversed(history or []) if row.get("role") == "user"]
    for text in texts:
        choice = _choice(_text(text))
        if choice:
            return choice if choice in VALID_GENDERS else None
    return None


def _catalogue_gender(name, store):
    """The recorded gender of a catalogue perfume named by the customer.

    Matching is delegated to sales.naming so this cannot drift from how the similarity
    reference and the exclusion list resolve the same strings.
    """
    if not name or store is None:
        return None

    from . import naming

    from products.models import Product
    product = naming.match_product(name, store, products=Product.objects.filter(store=store).select_related("brand"))
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
