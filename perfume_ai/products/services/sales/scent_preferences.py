"""Canonical scent requests and conversation updates; product labels are never inferred."""

import re
from dataclasses import dataclass, replace

from django.core.exceptions import ValidationError
from products.scent_tags import normalize_scent_tags
from ..static_faq_service import normalize_arabic


SCENT_KEYS = ("scent_tags", "excluded_scent_tags", "scent_match", "scent_open")
# The English words in Arabic letters are aliases too: "فيها سموكي وفاكهة" kept fruit and lost smoky,
# and both recommendations ignored it (simulation S09 round 2 full 4, t4).
TAG_ALIASES = {
    "fresh": ("fresh", "فريش", "منعش", "منعشه"),
    "sweet": ("sweet", "sugary", "gourmand", "سويت", "شوجري", "مسكر", "مسكره"),
    "oud": ("oud", "عود", "عودي"),
    "woody": ("woody", "خشبي", "خشبيه", "وودي", "خشب"),
    "citrus": ("citrus", "citrusy", "حمضي", "حمضيه", "حمضيات"),
    "floral": ("floral", "زهري", "زهريه", "زهور", "فلورال"),
    "fruity": ("fruity", "فاكهي", "فاكهيه", "فاكهه", "فواكه", "فروتي"),
    "spicy": ("spicy", "متبل", "متبله", "توابل", "سبايسي"),
    "musky": ("musky", "مسكي", "مسكيه"),
    "clean": ("clean", "نضيف", "نضيفه", "نظيف", "نظيفه", "نظافه", "صابوني", "صابونيه", "soapy"),
    "powdery": ("powdery", "بودري", "بودريه", "بودره"),
    "smoky": ("smoky", "smokey", "دخاني", "دخانيه", "دخان", "تبغي", "تبغيه", "سموكي"),
    "leather": ("leather", "leathery", "جلدي", "جلديه", "ليذر"),
    "aquatic": ("aquatic", "مائي", "مائيه", "بحري", "بحريه", "اكواتيك"),
    "light": ("light", "خفيف", "خفيفه"),
    "heavy": ("heavy", "تقيل", "تقيله"),
}
_ALIASES = {normalize_arabic(alias): tag for tag, aliases in TAG_ALIASES.items() for alias in aliases}
# These material names describe a style only in a customer's preference. They
# must not make a product's recorded vanilla/tobacco ingredient prove a tag.
_MATERIALS = {"vanilla": ("vanilla", "فانيليا", "فانيلا"),
              "tobacco": ("tobacco", "تبغ", "توباكو")}
_MATERIAL_NAMES = {normalize_arabic(alias): note for note, aliases in _MATERIALS.items() for alias in aliases}
_REQUEST_ALIASES = {**_ALIASES, **{alias: "sweet" if note == "vanilla" else "smoky"
                                for alias, note in _MATERIAL_NAMES.items()}}
_NEGATIVE = re.compile(
    r"(?<!\w)(?:و)?(?:مش|بلاش|بدون|من غير|غير|لا|ولا|مبحبش|ما\s+بحبش|بكره|"
    r"not|no|without|avoid|except|don't|dont|do\s+not|dislike|hate)"
    r"(?:\s+(?:عايز(?:ه|ها)?|عاوز(?:ه|ها)?|بحب|احب|بتحب|بيحب|تحب|يحب|حابه|بفضل|بتفضل|بيفضل|"
    r"(?:ال)?حاج(?:ه|ات)|يكون|تكون|ريحه|اي|"
    r"want|like|prefer|something|anything|any|a|an|it|to|be|too|very|overly)){0,6}\s*$"
)
# A seller's description negates within its clause in many more ways than a customer request
# ("من غير تاج دخاني", "ومش مسجل إنه تقيل"). Reading those as positive claims rejected correct
# drafts until the reply fell back to a non-answer (simulation S02 t1, S08 t3).
_CLAIM_NEGATOR = re.compile(r"(?<!\w)[وف]?(?:مش|مفيش|من غير|بدون|غير|ملوش|مالوش|مفيهوش|مفيهاش|"
                            r"not|no|without|isn't|never)(?!\w)")
_CONNECTOR = re.compile(r"(?:(?:[,،]\s*)?(?:و|او|ولا|and|or|nor)|[,،])\s*")
_LITERAL = re.compile(r"(?:فيه|فيها|مفيهوش|مفيهاش|يحتوي\s+علي|نوت(?:ه|ات)(?:ه)?|مكونات(?:ه)?|"
                      r"contains?|ingredients?|notes?(?:\s+of|\s*:))\s*$")
# "حاجة فيها فواكه", "عطر فيه توابل", "الريحة اللي فيها سموكي وفاكهة": a wanted kind of perfume said
# with "فيه". For a label with no tracked ingredient behind it the wish was dropped, the model's own
# tag with it, and "بحب الريحة اللي فيها سموكي وفاكهة" was asked the scent question again (simulation
# S09 round 2 batch 4, t3). A generic noun must lead; "Aventus فيه سموكي؟" asks about a perfume.
_WISH_LITERAL = re.compile(r"(?<!\w)(?:(?:ال)?حاج(?:ه|ات)|(?:ال)?عطر|(?:ال)?برفان|(?:ال)?ريحه|اللي|يكون|تكون)\s+(?:فيه|فيها)\s*$")
_ALTERNATIVE = re.compile(r"(?<!\w)(?:او|ولا|or)(?!\w)")
_EITHER = re.compile(r"(?<!\w)يا\s+(?:ال)?$")
_VAGUE_SCENT_WORDS = frozenset({
    "حاجه", "حاجات", "الحاجات", "حلو", "حلوه", "الحلو", "الحلوه", "كويس", "كويسه",
    "جميل", "جميله", "عطر", "برفان", "ريحه", "عايز", "عايزه", "عاوز", "عاوزه", "محتاج", "بحب",
    "nice", "good", "something", "a", "an", "perfume", "fragrance", "scent", "i", "want", "like",
})
_REPLACEMENT = re.compile(r"(?<!\w)(?:بدل|instead of)(?!\w)")
_REPLACED = re.compile(r"(?:بدل|instead of)(?:\s+(?:طابع|ريحه|ستايل|the|a|an|scent|style)){0,3}\s*$")
_REFERENCE_FOLLOWUP = (
    r"(?:بس|لكن|but|و\s*(?:مش|بلاش|بدون|عايز(?:ه)?|عاوز(?:ه)?|محتاج(?:ه)?|"
    r"يكون|تكون|فيه|فيها|مفيهوش|مفيهاش)|and\s+(?:not|without|with))\b|"
    r"(?:و\s*|and\s+)(?:ال)?(?:" + "|".join(re.escape(alias) for alias in sorted(_ALIASES, key=len, reverse=True)) + r")(?!\w)"
)
_OPEN = re.compile(r"اي\s+(?:ريحه|ستايل|طابع)|(?:الريحه|الستايل|الطابع)\s+مش\s+(?:مهم|فارقه)|مش\s+فارقه\s+(?:الريحه|الستايل)|any\s+(?:scent|style)|no\s+scent\s+preference", re.I)
_STYLE_TERMS = {"fresh", "sweet", "gourmand", "clean", "woody", "floral", "fruity", "spicy", "powdery", "aquatic", "light", "heavy",
                "فريش", "منعش", "مسكر", "سويت", "نظافه", "خشبي", "زهري", "فاكهي", "متبل", "بودري", "مائي", "خفيف", "تقيل"}
_STYLE_TERMS.update(alias for alias, tag in _ALIASES.items()
                    if tag in {"fresh", "sweet", "clean", "woody", "floral", "fruity", "spicy", "powdery", "aquatic", "light", "heavy"})


# A trait word whose clause opens with a perfume or a pointer to one describes that perfume; it is
# not a wish. "و212 VIB تقيل وتوابله كتير.. مش ده اللي طلبته" saved heavy as a WANTED label over the
# customer's own "مش تقيلة" (simulation S10 final 1, t6), and "Pure XS فيه جلد؟" saved leather
# (S14 final 2, t7). The subject is a short Latin name (Franco words mix letters and digits and are
# not names) or a pointer, followed by at most two words before the trait.
_LATIN_SUBJECT = r"[A-Za-z0-9][A-Za-z0-9'’&.-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'’&.-]*){0,3}"
_POINTER_SUBJECT = r"(?:ده|دي|دا|ديه|هو|هي|الاولاني|التاني|ريحته|ريحتها)"
# "يعني ريحته حمضيات وخشب أكتر؟" is a question about the perfume just described; without يعني as a
# lead-in it saved citrus as a wish (simulation S14 round 2, t3).
# "طب والـ Black XS حلاوته واضحة ولا التوابل…" puts an article before the name (S14 round 2 batch 2, t5).
# "وريحة Pure XS الجلدية دي عاملة إزاي؟" names the smell of a perfume; read as a wish once the turn
# became a recommendation, it saved leather (S14 round 2 batch 2, t7).
_DESCRIBED = re.compile(r"^\s*(?:(?:و|بس|طب|طيب|اما|لكن|يعني|هل)\s*)?(?:(?:ريحه|ريحة|طابع|ستايل)\s+(?=[A-Za-z0-9]))?"
                        r"(?:و?الـ?\s*(?=[A-Za-z0-9]))?"
                        r"(?:(?P<latin>" + _LATIN_SUBJECT + r")|" + _POINTER_SUBJECT
                        + r")(?P<rest>(?:\s+\S+)*)\s*$")
# The whole clause after such a subject describes it ("طب Black XS ريحته مسكرة أكتر ولا التوابل
# باينة؟" excluded spicy through its "ولا", S14 final 2 t5), until a wish or a contrast starts.
# "هي بتحب الحاجات الخفيفة ومش المسكرة أوي" is the recipient's taste, not a perfume described through
# a pointer: without بتحب/بيحب the fiancée's light and not-sweet were dropped (S07 round 2 batch 2, t2).
_WISH = re.compile(r"(?<!\w)(?:عايز|عاوز|عايزه|عاوزه|بحب|بتحب|بيحب|تحب|يحب|بتفضل|بيفضل|محتاج|محتاجه|نفسي|بدور|"
                   r"بفضل|بس|لكن|بدل|حاجه|حاجات)(?!\w)")


# English requests are Latin too ("no oud or sweet", "I want a sugary perfume"): a Latin subject
# counts only when it holds a word that is not request grammar or a scent word.
_SUBJECT_STOPWORDS = frozenset({
    "a", "an", "and", "any", "anything", "avoid", "bit", "but", "do", "dont", "don't", "except", "for",
    "fragrance", "hate", "dislike", "i", "i'm", "im", "is", "it", "it's", "its", "just", "less", "like",
    "little", "love", "more", "need", "no", "not", "one", "only", "or", "perfume", "prefer", "really",
    "scent", "smell", "smells", "so", "something", "that", "the", "this", "too", "very", "want",
    "with", "without", "would", "me", "my", "please", "also",
})


def _describes_a_perfume(before):
    clause = re.split(r"[.!؟?\n؛;،,]", before)[-1]
    match = _DESCRIBED.match(clause)
    if not match or _WISH.search(match.group("rest") or ""):
        return False
    subject = match.group("latin")
    if subject is None:
        return True  # a pointer: ده / هو / ريحته
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’&.-]*", subject)]
    if any(re.search(r"[a-z]\d|\d[a-z]", word) for word in words):
        return False  # Franco Arabic ("7aga fresh"), not a perfume name
    return any(word not in _SUBJECT_STOPWORDS and word not in _ALIASES for word in words)


@dataclass(frozen=True)
class _Mention:
    start: int
    end: int
    tags: tuple
    polarity: str
    note: str | None = None
    # The model quoted this whole phrase as evidence and stated its polarity, so a neighbouring
    # phrase's negation must not spread into it ("مش عايزها تقيلة، حاجة هادية").
    stated: bool = False


def _mentions(text, *, for_claims=False):
    aliases = _ALIASES if for_claims else _REQUEST_ALIASES
    found = []
    previous = None
    pattern = r"(?<!\w)(?:[وب]?ال|[وب])?(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + r")(?!\w)"
    for match in re.finditer(pattern, text):
        before = text[:match.start()]
        connector = re.sub(r"ال$", "", text[previous.end:match.start(1)].strip()).strip() if previous else ""
        # An adjective that directly follows another trait shares its negation: "ومش خشبية تقيلة"
        # saved heavy as WANTED next to an excluded woody (simulation S10 round 2, t3).
        connected = bool(previous and (_CONNECTOR.fullmatch(connector)
                                       or (not connector and previous.polarity == "negative")))
        tag = aliases[match[1]]
        literal = _LITERAL.search(before)
        negative = bool(_NEGATIVE.search(before) or (connected and previous.polarity == "negative"))
        if for_claims and not negative:
            negative = bool(_CLAIM_NEGATOR.search(re.split(r"[.!؟?\n؛;،,]", before)[-1]))
        polarity = "negative" if negative else "positive"
        if literal or (connected and previous.polarity.startswith("literal_")):
            negative = bool((literal and (literal[0].strip() in ("مفيهوش", "مفيهاش")
                                          or _NEGATIVE.search(before[:literal.start()])))
                            or (connected and previous.polarity == "literal_negative"))
            polarity = "literal_negative" if negative else "literal_positive"
            # Oud, leather and musk are ingredients as well as labels: "عايز عطر فيه عود" stays an
            # ingredient requirement (`test_literal_ingredients_do_not_add_a_style_requirement`).
            if (not for_claims and not negative and literal and _MATERIAL_NAMES.get(match[1]) is None
                    and tag not in ("oud", "leather", "musky") and _WISH_LITERAL.search(before)):
                polarity = "positive"
        elif (_REPLACED.search(before) or re.search(r"فوحان(?:ه)?\s*$|projection\s*$", before)
              # "أنا مش بسأل على العود بس، قلتلك مش عايز حاجة مسكرة!" names oud as a topic; read as a
              # wish it moved oud from excluded to wanted (simulation S10 round 2 full 2, t4).
              or re.search(r"(?:بسال|بتسال|اسال|سالت|سالتك|هسال|بسالك|بكلم|بتكلم)\s+(?:علي|عن)\s*(?:ال)?$", before)
              or (tag in ("light", "heavy") and re.search(r"(?:ال)?خروجات\s*$|(?:workouts?|outings?)\s*$", before))):
            polarity = "ignored"
        # Only an Arabic trait word after the subject: the real shape is mixed script ("212 VIB
        # تقيل"), while an English request ("recommend a fresh unisex perfume") is Latin throughout.
        if not for_claims and re.match(r"[؀-ۿ]", match[1]) and _describes_a_perfume(before):
            polarity = "ignored"
        # Keep the conjunction outside the span so phrase-level reconciliation
        # can join this alias to a preceding phrase understood by the model.
        previous = _Mention(match.start(1), match.end(), (tag,), polarity, _MATERIAL_NAMES.get(match[1]))
        found.append(previous)
    return found


def _summarize(text, mentions, *, literal_exclusions=False):
    # Source order resolves a customer's correction within the same message.
    stances = {}
    ingredients = {}
    previous = None
    for mention in sorted(mentions, key=lambda item: (item.start, item.end)):
        connector = re.sub(r"ال$", "", text[previous.end:mention.start].strip()).strip() if previous else ""
        if (previous and previous.polarity == "negative" and mention.polarity == "positive"
                and not mention.stated
                and previous.end <= mention.start and _CONNECTOR.fullmatch(connector)):
            mention = replace(mention, polarity="negative")
        if mention.polarity in ("positive", "negative"):
            for tag in mention.tags:
                stances[tag] = mention
        elif mention.note and mention.polarity.startswith("literal_"):
            ingredients[mention.note] = mention.polarity
        elif literal_exclusions and mention.polarity == "literal_negative":
            # "ولا فيها عود": a refused scent label with no tracked ingredient behind it. Dropping it
            # also discarded the model's own exclusion, so oud vanished from simulation S10 turn 1.
            for tag in mention.tags:
                stances[tag] = replace(mention, polarity="negative")
        previous = mention
    positive = [tag for tag, mention in stances.items() if mention.polarity == "positive"]
    negative = [tag for tag, mention in stances.items() if mention.polarity == "negative"]
    opening = _OPEN.search(text)
    result = {}
    if opening and not _NEGATIVE.search(text[:opening.start()]):
        result.update(scent_open=True, scent_tags=[])
    if positive:
        result["scent_tags"] = positive
        result["scent_open"] = False
        spans = sorted({(stances[tag].start, stances[tag].end) for tag in positive})
        result["scent_match"] = "any" if any(_ALTERNATIVE.search(text[left[1]:right[0]])
                                              or (_EITHER.search(text[:left[0]])
                                                  and _EITHER.search(text[left[1]:right[0]]))
                                              for left, right in zip(spans, spans[1:])) else "all"
    if negative:
        result["excluded_scent_tags"] = negative
    for key, polarity in (("notes", "literal_positive"), ("avoid_notes", "literal_negative")):
        values = [note for note, stance in ingredients.items() if stance == polarity]
        if values:
            result[key] = values
    return result


def explicit_scent_updates(message, *, for_claims=False):
    text = normalize_arabic(message or "")
    return _summarize(text, _mentions(text, for_claims=for_claims), literal_exclusions=not for_claims)


def _valid_tags(value):
    if not isinstance(value, list):
        return []
    tags = []
    for entry in value:
        try:
            for tag in normalize_scent_tags([entry]):
                if tag not in tags:
                    tags.append(tag)
        except ValidationError:
            continue
    return tags


def normalize_scent_intent(intent):
    """Validate model fields and migrate old broad note terms into explicit labels."""
    result = dict(intent) if isinstance(intent, dict) else {}
    for key in ("scent_tags", "excluded_scent_tags"):
        if key not in result or result[key] is None:
            result.pop(key, None)
            continue
        if not isinstance(result[key], list):
            result.pop(key, None)
        else:
            result[key] = _valid_tags(result[key])
    for note_key, tag_key in (("notes", "scent_tags"), ("avoid_notes", "excluded_scent_tags")):
        literal, tags = [], list(result.get(tag_key) or [])
        terms = result.get(note_key)
        for term in terms if isinstance(terms, (list, tuple)) else ():
            if not isinstance(term, str) or not term.strip():
                continue
            normalized = normalize_arabic(str(term))
            if normalized in _STYLE_TERMS:
                tag = _ALIASES.get(normalized)
                if tag and tag not in tags:
                    tags.append(tag)
            else:
                literal.append(term)
        if note_key in result:
            result[note_key] = literal
        if tags:
            result[tag_key] = tags
    for trait in result.get("avoid_traits") or ():
        if trait in ("heavy", "sweet"):
            result["excluded_scent_tags"] = list(dict.fromkeys([*result.get("excluded_scent_tags", []), trait]))
    if result.get("scent_match") not in ("all", "any"):
        result.pop("scent_match", None)
    if type(result.get("scent_open")) is not bool:
        result.pop("scent_open", None)
    return result


def reconcile_scent_intent(intent, message, *, require_evidence=False):
    """Reconcile current-turn phrases, without changing saved preference semantics.

    Raw model output and new goals require evidence. Other callers can supply
    canonical updates, including older conversations without phrase metadata.
    """
    result = normalize_scent_intent(intent)
    text = normalize_arabic(message or "")
    reference = normalize_arabic(str(result.get("similar_to") or ""))
    reference_spans = [(match.start(), match.end()) for match in re.finditer(re.escape(reference), text)] if reference else []
    if reference and not reference_spans:
        # A translated catalog name need not occur verbatim in Arabic. Limit the
        # protected span to the reference clause, retaining following preferences
        # even when their Arabic conjunction is attached (e.g. "وفريش").
        reference_spans = [(match.start(1), match.end(1)) for match in re.finditer(
            r"(?:شبه|زي|similar\s+to|same\s+vibe\s+as)\s+(.+?)(?=\s+(?:" + _REFERENCE_FOLLOWUP + r")|[,،;.!؟?]|$)", text,
        )]

    def in_reference(start, end):
        return any(start < right and left < end for left, right in reference_spans)

    known = _mentions(text)
    known = [_Mention(item.start, item.end, item.tags, "ignored", item.note)
             if in_reference(item.start, item.end) else item for item in known]
    scoped_known = list(known)
    evidence = result.get("scent_evidence")
    model_mentions, replaced_tags = [], set()
    next_start = {}
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            phrase = normalize_arabic(item["text"])
            tags = _valid_tags(item.get("tags"))
            polarity = item.get("polarity")
            if not phrase or not tags or polarity not in ("positive", "negative"):
                continue
            start = text.find(phrase, next_start.get(phrase, 0))
            if start < 0:
                continue
            end = start + len(phrase)
            prefix = re.search(r"\w*$", text[:start])[0]
            arabic_prefix = bool(re.match(r"[\u0600-\u06ff]", phrase)
                                 and prefix in ("و", "ب", "ل", "ف", "ال", "وال", "بال", "لل", "فال", "وبال", "فبال"))
            if (prefix and not arabic_prefix) or (end < len(text) and text[end].isalnum()):
                continue
            next_start[phrase] = end
            words = set(re.findall(r"\w+", phrase))
            if words and words <= _VAGUE_SCENT_WORDS:
                # "حاجة حلوة" asks for something nice; a source quote alone
                # does not make it a sweetness or other scent-style request.
                replaced_tags.update(tags)
                continue
            if in_reference(start, end):
                replaced_tags.update(tags)
                continue
            overlaps = [(index, mention) for index, mention in enumerate(known)
                        if start < mention.end and mention.start < end]
            if overlaps:
                replaced_tags.update(tags)
                if len(overlaps) == 1:
                    index, mention = overlaps[0]
                    if set(tags) == set(mention.tags) and mention.polarity in ("positive", "negative"):
                        # A known word can be part of a longer scent phrase, such
                        # as "زهور بيضا". Keep its meaning, but use the complete
                        # source span when carrying negation into a joined scent.
                        # The model's negative stands when its own quoted phrase carries the
                        # negator before the word ("مش عايز أي حاجة مسكرة"); the local
                        # grammar is a fixed filler list and cannot read every such phrase.
                        stated_negative = polarity == "negative" and _CLAIM_NEGATOR.search(text[start:mention.start])
                        polarity = ("negative" if _NEGATIVE.search(text[:start]) or stated_negative
                                    else mention.polarity)
                        scoped_known[index] = replace(scoped_known[index], end=max(scoped_known[index].end, end),
                                                      polarity=polarity)
                continue
            if _NEGATIVE.search(text[:start]):
                polarity = "negative"
            if _LITERAL.search(text[:start]) or _REPLACED.search(text[:start]):
                replaced_tags.update(tags)
                continue
            model_mentions.append(_Mention(start, end, tuple(tags), polarity, stated=True))
    updates = _summarize(text, [*model_mentions, *scoped_known], literal_exclusions=True)
    touched = replaced_tags | {tag for mention in known for tag in mention.tags}
    for key in ("scent_tags", "excluded_scent_tags"):
        base = [] if require_evidence else [tag for tag in result.get(key, []) if tag not in touched]
        values = list(dict.fromkeys([*updates.get(key, []), *base]))
        if values or key in result or key in updates:
            result[key] = values
    # Positive/negative conflicts without source evidence conservatively exclude;
    # a known later positive has already removed the old negative above.
    negatives = set(result.get("excluded_scent_tags", []))
    if result.get("scent_tags"):
        result["scent_tags"] = [tag for tag in result["scent_tags"] if tag not in negatives]
    material_mentions = {mention.note for mention in known if mention.note}
    for key in ("notes", "avoid_notes"):
        values = [note for note in result.get(key, [])
                  if _MATERIAL_NAMES.get(normalize_arabic(note)) not in material_mentions]
        values = list(dict.fromkeys([*values, *updates.get(key, [])]))
        if values or key in result:
            result[key] = values
    if reference and require_evidence:
        outside_reference = text
        for left, right in reversed(reference_spans):
            outside_reference = outside_reference[:left] + " " * (right - left) + outside_reference[right:]
        if not any(_LITERAL.search(outside_reference[:match.start()])
                   for match in re.finditer(r"\S+", outside_reference)):
            # Reference notes are catalog facts, never guesses from its name.
            result.pop("notes", None)
            result.pop("avoid_notes", None)
    if updates.get("scent_open") is True:
        result.update(scent_open=True, scent_tags=[])
    elif result.get("scent_tags") or result.get("excluded_scent_tags"):
        result["scent_open"] = False
    if result.get("scent_tags"):
        # A single known tag must not overwrite an AI-evidenced OR relationship
        # with another phrase; all surviving evidence participates in the summary.
        result["scent_match"] = updates.get("scent_match", result.get("scent_match", "all"))
    else:
        result.pop("scent_match", None)
    cleared = set(result.get("clear_preferences") or ())
    if updates.get("scent_tags") and _REPLACEMENT.search(text):
        # The model can clear the old array while also giving its replacement.
        # Sourced replacement tags already replace the saved positive styles.
        cleared.difference_update(("scent_tags", "scent_match", "scent_open"))
        if result.get("clear_preferences"):
            result["clear_preferences"] = [key for key in result["clear_preferences"] if key in cleared]
    for key in SCENT_KEYS + ("notes", "avoid_notes"):
        if key in cleared:
            result.pop(key, None)
    if "scent_tags" in cleared:
        result.pop("scent_match", None)
    # A legacy heavy/sweet exclusion must not undo a sourced positive later.
    if result.get("avoid_traits"):
        result["avoid_traits"] = [trait for trait in result["avoid_traits"] if trait not in result.get("scent_tags", [])]
    return result


def merge_scent_preferences(merged, fresh, saved, message):
    """Apply a stated replacement, addition, exclusion or withdrawal to durable labels."""
    fresh = reconcile_scent_intent(fresh, message)
    cleared = set(merged.get("clear_preferences") or ())
    for key in SCENT_KEYS + ("notes", "avoid_notes"):
        if key in cleared:
            fresh.pop(key, None)
    merged.pop("scent_evidence", None)
    # Empty/null extraction fields do not withdraw saved ingredient constraints.
    # Reinterpret only materials actually mentioned in this message, after the
    # conversation layer has merged literal notes (e.g. vanilla style clears a
    # saved vanilla ingredient requirement while retaining a rose requirement).
    note_updates = reconcile_scent_intent(
        {key: merged[key] for key in ("notes", "avoid_notes", "similar_to", "clear_preferences") if key in merged},
        message,
    )
    for key in ("notes", "avoid_notes"):
        if key in note_updates:
            merged[key] = note_updates[key]
    resetting = merged.get("reset_preferences") is True
    old = {} if resetting else normalize_scent_intent(saved)
    if fresh.get("scent_open") is True:
        excluded = [] if "excluded_scent_tags" in cleared else old.get("excluded_scent_tags", [])
        merged.update(scent_open=True, scent_tags=[], notes=[],
                      excluded_scent_tags=list(dict.fromkeys([*excluded, *fresh.get("excluded_scent_tags", [])])))
        merged.pop("similar_to", None)
        merged.pop("similar_to_notes", None)
        return merged
    positive = list(fresh.get("scent_tags") or ([] if "scent_tags" in cleared else old.get("scent_tags")) or [])
    if fresh.get("scent_tags") and re.search(r"كمان|بالاضافه|\balso\b|\bas well\b", normalize_arabic(message or "")):
        positive = list(dict.fromkeys([*old.get("scent_tags", []), *positive]))
    negative = list(dict.fromkeys([*([] if "excluded_scent_tags" in cleared else old.get("excluded_scent_tags", [])),
                                   *fresh.get("excluded_scent_tags", [])]))
    negative = [tag for tag in negative if tag not in (fresh.get("scent_tags") or [])
                or tag in fresh.get("excluded_scent_tags", [])]
    positive = [tag for tag in positive if tag not in negative]
    if positive or "scent_tags" in merged or old.get("scent_tags"):
        merged["scent_tags"] = positive
    if negative or "excluded_scent_tags" in merged or old.get("excluded_scent_tags"):
        merged["excluded_scent_tags"] = negative
    if positive:
        merged["scent_match"] = fresh.get("scent_match") or old.get("scent_match") or "all"
    else:
        merged.pop("scent_match", None)
    if positive or negative or fresh.get("scent_open") is False:
        merged["scent_open"] = False
    if merged.get("avoid_traits"):
        merged["avoid_traits"] = [trait for trait in merged["avoid_traits"] if trait not in positive]
    return merged


def has_scent_preference(intent):
    return bool(intent.get("scent_tags") or intent.get("excluded_scent_tags") or intent.get("notes")
                or intent.get("avoid_notes") or intent.get("similar_to") or intent.get("scent_open"))
