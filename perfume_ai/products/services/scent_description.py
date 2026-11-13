"""Short catalog descriptions and bounded checks for customer-facing scent prose.

Character comes only from the saved labels. Ingredients remain a separate answer
scope, even when an ingredient happens to be recorded in the catalog.
"""

import re

from products import occasions
from products.scent_tags import SCENT_TAG_LABELS
from .sales.scent_preferences import TAG_ALIASES


_PHRASES = {
    "fresh": "فريش ومنعش", "sweet": "مسكر", "oud": "عودي", "woody": "خشبي",
    "citrus": "فيه ريحة حمضيات", "floral": "فيه ريحة زهور",
    "fruity": "فيه ريحة فواكه", "spicy": "فيه ريحة توابل", "musky": "مسكي",
    "clean": "نضيف", "powdery": "فيه ريحة بودرة", "smoky": "فيه ريحة دخان",
    "leather": "ريحته جلدية", "aquatic": "ريحته بحرية", "light": "خفيف",
    "heavy": "تقيل",
}
_FALLBACK = "وصف ريحته محتاج تأكيد"
_NORMALIZE = occasions.normalize_text
_BY_TAG_ALIAS = {
    _NORMALIZE(alias): tag for tag, aliases in TAG_ALIASES.items() for alias in aliases
}
_TAG_PATTERN = re.compile(
    # "ف" joins a contrast clause ("أما Alexandria II فخشبي"); without it the label went unread and
    # a correct description was rejected (simulation S05 replay t4).
    r"(?<!\w)(?:[وبلف]?ال|[وبف])?(?P<alias>"
    + "|".join(re.escape(alias) for alias in sorted(_BY_TAG_ALIAS, key=len, reverse=True))
    + r")(?!\w)"
)
_NOTE_SCOPE = re.compile(
    r"(?<!\w)(?:[وبل]?ال|[وب])?(?:نوت(?:ه|ات|س)?(?:ه|ها|هم|هن)?|مكونات(?:ه|ها|هم|هن)?|"
    r"تركيب(?:ه|ته|تها)|افتتاحي(?:ه|ته)|قلب|قاعد(?:ه|ته)|طبقات|"
    r"notes?|ingredients?|composition|opening|drydown|layers?)(?!\w)|"
    r"(?<![a-z])(?:top|middle|base|heart)\s+notes?\b|\bcontains?\b|بيحتوي\s+علي|"
    # "الـ top/middle/base بتاعته إيه؟" and "الـ dry down" name the note layers without the word
    # "notes"; the stored notes they ask for were rejected as unsolicited (simulation S02 t4).
    # Normalising drops the tatweel, so "الـdry down" arrives as "الdry down": a Latin layer word may
    # follow an Arabic article with no word boundary (S02 round 2, t7).
    r"(?<![a-z])(?:top|middle|base|heart)\s*(?:/|,|و|and)?\s*(?:top|middle|base|heart)\b|(?<![a-z])dry\s*-?\s*down\b"
)
_SCENT_SCOPE = re.compile(
    r"(?:ريح(?:ه|ته|تها|تهم)|ريحتهم|ريحتها|الريحه)\s*(?:عامل(?:ه)?\s*)?(?:ايه|ازاي|كيف)|"
    r"(?:ايه|ازاي|كيف)\s+(?:هي\s+|تكون\s+)?(?:ريحته|ريحتها|ريحتهم|الريحه)|"
    r"اوصف(?:ه|ها|هم)?(?:ولي|لي)?|وصف(?:ه|ها|هم)?|رايك|رأيك|"
    r"تفاصيل(?:ه|ها|هم)?|كلمني\s+عن|احكيلي\s+عن|قول(?:لي|ي)\s+عن|"
    r"\b(?:describe|description|opinion|review)\b|"
    r"\b(?:tell\s+me|what\s+do\s+you\s+think|how\s+is\s+it|smells?\s+like)\b|"
    r"\bwhat\s+(?:does|is).{0,60}\b(?:smell|scent|fragrance)\b|"
    r"(?:قارن|مقارنه|compare).{0,40}(?:ريح|scent|smell)|"
    r"(?<!\w)(?:و)?(?:ريحته|ريحتها|ريحتهم|الريحه)\s*$|"
    r"\b(?:price|cost)\b.{0,30}\b(?:scent|smell|fragrance)\b"
)
_SCENT_PROSE = re.compile(r"ريحته|ريحتها|ريحتهم|طابعه|طابعها|ستايله|ستايلها|\b(?:scent|smells?|character)\b")
_NEGATIVE = re.compile(
    r"(?<!\w)(?:و)?(?:مش|غير|لا|مفيش|مفيهوش|مافيش|بدون|not|no|without)"
    r"(?:\s+(?:جدا|اوي|قوي|يكون|تكون|ريحه|حاجه|too|very|really|it|a|be)){0,3}\s*$"
)
_NOTE_OPT_OUT = re.compile(
    r"(?<!\w)(?:و)?(?:مش|بلاش|بدون|من غير|لا|without|no|don't|dont|do not)"
    r"(?:\s+(?:عايز(?:ه)?|عاوز(?:ه)?|تذكر|تقول|تفاصيل|شرح|وصف|want|mention|"
    r"tell|explain|details|about|the|any|a|scent|smell|fragrance)){0,5}\s*$"
)
_UNCERTAIN = re.compile(r"محتاج(?:ه)?\s+تاكيد|مش\s+(?:مسجل|متسجل|متاكد|مؤكد|موكد|معروف)|غير\s+(?:مسجل|متوفر)|ماقدرش|مقدرش|لا\s+توجد|not\s+(?:recorded|known)|needs?\s+confirmation")
# Owner decision (2026-11-12): a trait missing from the saved labels is unknown, not absent, so
# "لا، مش مسكر" about a perfume with no sweet label is an inference (simulation S07 t5). The one
# exception is a recorded opposite: a perfume labelled light is not heavy.
_OPPOSITE_TAGS = {"heavy": {"light"}, "light": {"heavy"}}
# Character words no saved label can support: warm, sharp, soft, luxurious, cool. "Body… ممكن يكون
# أدفى في طابعه" (simulation S07 t3) and "التوابل فيه دافية، مش حادة" (S14 t5) were the judge's
# critical invented claims; the checker had no word for them, so they passed.
_UNLABELLED_CHARACTER = re.compile(
    r"(?<!\w)[وف]?(?:ال)?(?:دافي|دافيه|دافئ|دافئه|ادفي|ادفا|ادفئ|دفا|دفء|حاد|حاده|ناعم|ناعمه|انعم|"
    r"فخم|فخمه|افخم|بارد|بارده|ابرد|warm|warmer|sharp|soft|softer|cozy|luxurious)(?!\w)")
_OCCASION_NEGATIVE = re.compile(
    r"(?<!\w)(?:و)?(?:مش|غير|لا|بدون|من غير|not|no|without)"
    r"(?:\s+(?:مناسب(?:ه)?|بيناسب|ينفع|صالح|suitable|suited|appropriate|good|for|the)){0,3}\s*$"
)
_OCCASION_SCOPE = re.compile(
    r"(?<!\w)(?:[وبل]?ال|[وب])?(?:مناسب(?:ه|ته|تها|تهم|ات(?:ه|ها|هم)?)?|"
    r"استخدام(?:ه|ها|هم)?|ينفع|بينفع|يناسب|بيناسب|انسب|البس(?:ه|ها)?|استخدم(?:ه|ها)|"
    r"occasions?|suitability|suitable|suited|appropriate|wear|use|good\s+for)(?!\w)"
)
_OCCASION_QUERY = re.compile(
    r"(?<!\w)(?:و)?(?:هل|ايه|لايه|لاي|امتي|فين|انهي|"
    r"قول(?:لي|ي)|وضح(?:لي)?|اعرف|ممكن|what|which|when|where|tell\s+me|"
    r"describe|explain|is|are|can|could|would|does)(?!\w)"
)
_OCCASION_DETAIL_REQUEST = re.compile(
    r"(?<!\w)(?:و)?(?:قول(?:لي|ي)|وضح(?:لي)?|اعرف|هل|tell\s+me|"
    r"(?:and|و)\s+(?:what|which|is|are|would|can)|"
    r"(?:is|are|can|could|would|does)\s+(?:it|this|that|they|these|i))(?!\w)"
)
_SELECTION_REQUEST = re.compile(
    r"(?<!\w)(?:و)?(?:رشح\w*|اقترح\w*|عايز(?:ه)?|عاوز(?:ه)?|"
    r"recommend|suggest|find|want|need|looking\s+for|"
    r"(?:what|which)\s+(?:perfumes?|fragrances?)|"
    r"(?:ايه|انهي|اي|احسن|افضل|قوللي|قولي|هاتلي|وريني)\s+"
    r"(?:(?:هو|هي|احسن|افضل)\s+)?(?:ال)?(?:عطر|عطور|برفان|برفانات|حاجه))(?!\w)"
)
_SEASON_OCCASION_ALIAS = re.compile(r"(?:[وبل]?ال|ولل|لل|[وبل])?صيف(?:ي)?|summer")
_OCCASION_OPT_OUT = re.compile(
    r"(?<!\w)(?:و)?(?:بلاش|بدون|من غير|(?:مش|لا)\s+(?:عايز(?:ه)?|عاوز(?:ه)?|تذكر|تقول)|"
    r"without|no|don't|dont|do not)"
    r"(?:\s+(?:تذكر|تقول|شرح|تفاصيل|ذكر|وصف|عن|mention(?:ing)?|describe|"
    r"describing|details|about|the|any|its)){0,5}\s*$"
)


def _all_tags(tags):
    if not isinstance(tags, (list, tuple)):
        return []
    result = []
    for tag in tags:
        if isinstance(tag, str):
            tag = tag.strip().casefold()
            if tag in SCENT_TAG_LABELS and tag not in result:
                result.append(tag)
    return result


def selected_tags(tags):
    """First three recognized, unique character labels in saved order."""
    return _all_tags(tags)[:3]


def _occasion_mentions(value):
    # Reuse the longest-phrase parser: its public set intentionally loses order.
    text = _NORMALIZE(value)
    return [(occasions._BY_ALIAS[match.group()], match.start(), match.end())
            for match in occasions._MENTION.finditer(text)]


def selected_occasion(recorded, preferred=None):
    """A matching requested category, or the first category actually recorded."""
    saved = list(dict.fromkeys(category for category, _, _ in _occasion_mentions(recorded)))
    if not saved:
        return ""
    positive_preferences = occasions.occasion_groups(preferred, positive_only=True)
    for category, _, _ in _occasion_mentions(preferred):
        if category in saved and category in positive_preferences:
            return category
    return saved[0]


def describe_scent(tags, occasion="", *, preferred_occasion=None):
    """Scent labels only; occasion arguments remain for caller compatibility."""
    phrases = []
    for tag in selected_tags(tags):
        phrase = _PHRASES[tag]
        if phrase.startswith("فيه ريحة ") and phrases and phrases[-1].startswith("فيه ريحة "):
            phrases[-1] += " و" + phrase.removeprefix("فيه ريحة ")
        else:
            phrases.append(phrase)
    if phrases:
        description = " و".join(phrases)
        if not description.startswith(("فيه ريحة ", "ريحته ")):
            description = "ريحته " + description
    else:
        description = _FALLBACK
    return description


def _without_names(message, product_names):
    text = _NORMALIZE(message)
    names = sorted({_NORMALIZE(name) for name in product_names if name}, key=len, reverse=True)
    if names:
        text = re.sub(r"(?<!\w)(?:[وبل]?ال|[وب])?(?:" + "|".join(map(re.escape, names)) + r")(?!\w)", " ", text)
    return text


def occasion_preference(message, *, product_names=(), saved=None):
    """Current positive occasion, excluding names, then the saved preference."""
    text = _without_names(message, product_names)
    positive = occasions.occasion_groups(text, positive_only=True)
    for category, _, _ in _occasion_mentions(text):
        if category in positive:
            return category
    return selected_occasion(saved)


def requested_occasions(message, *, product_names=()):
    """Specific uses named in a question, excluding generic fields and seasons."""
    text = _without_names(message, product_names)
    categories = []
    for category, start, end in _occasion_mentions(text):
        phrase = text[start:end]
        if _SEASON_OCCASION_ALIAS.fullmatch(phrase):
            continue
        if re.fullmatch(r"(?:و?ال|و)?مناسبات", phrase) and not re.search(
            r"(?:مناسب(?:ه)?|ينفع|بينفع|يناسب|بيناسب)\s*$", text[:start],
        ):
            continue
        if category not in categories:
            categories.append(category)
    return categories


def occasion_requested(message, *, product_names=()):
    """An explicit current question about use, not a selection preference."""
    text = _without_names(message, product_names)
    for match in _OCCASION_SCOPE.finditer(text):
        before, after = text[:match.start()], text[match.end():]
        if _OCCASION_OPT_OUT.search(before):
            continue
        selections = list(_SELECTION_REQUEST.finditer(before))
        if selections and not _OCCASION_DETAIL_REQUEST.search(before[selections[-1].end():]):
            continue
        uses = requested_occasions(after)
        if not uses and re.search(r"صيف|شتا|شتوي|ربيع|خريف|\b(?:summer|winter|spring|fall|autumn)\b", after) and not re.search(
            r"مناسبات|occasions?", match.group(),
        ):
            continue
        # "Suitable for men" or "good for you" is not an occasion question.
        topic = bool(uses or _OCCASION_QUERY.search(after) or re.search(
            r"مناسب(?:ته|تها|تهم|ات)|استخدام|occasions?|suitability|wear|البس", match.group(),
        ))
        if not topic:
            continue
        if _OCCASION_QUERY.search(text) or re.fullmatch(
            r"\s*(?:(?:و|هو|هي|ده|دي|طب|طيب|هل|is|are|it|its|this|that|the)\s*)*", before,
        ) or re.search(r"[؟?]", str(message or "")):
            # A suitability phrase inside a question sentence is a question whatever precedes it.
            # "طب وإيروس ينفع للشغل ولا تقيل؟" names the perfume in Arabic, which `_without_names`
            # cannot strip, and the answer was rejected as unrequested (simulation S08 t3).
            return True
    return False


def _occasion_claims(segment, recorded_notes=()):
    """Attributable use claims, excluding ingredients, seasons and questions."""
    prose = _normalize_prose(segment)
    # Mask just the ingredient names: masking a whole notes answer could hide
    # an appended suitability claim in "نوتاته Vanilla ومناسب للشغل".
    for mention in reversed(list(_ingredient_mentions(prose, recorded_notes))):
        prose = prose[:mention.start()] + " " * (mention.end() - mention.start()) + prose[mention.end():]
    for clause in re.finditer(r"[^.!؟?\n؛;،,]+(?:[.!؟?\n؛;،,]|$)", prose):
        text = _NORMALIZE(clause.group())
        if clause.group().rstrip().endswith(("?", "؟")) and (
            re.search(r"تحب|تفضل|عايز|عاوز|\b(?:want|prefer|looking)\b", text)
            or not re.search(r"مناسب|ينفع|suitable|suited|good\s+for", text)
        ):
            continue
        mentions = _occasion_mentions(text)
        if not mentions and re.search(r"مناسب(?:ته|تها|اتهم)|المناسبه|\boccasions?\b", text):
            yield "", False, True
        for category, start, end in mentions:
            # Summer is also an occasion alias, but seasonal claims belong to
            # the existing season validator and the product's season field.
            if _SEASON_OCCASION_ALIAS.fullmatch(text[start:end]):
                continue
            negative = bool(_OCCASION_NEGATIVE.search(text[max(0, start - 50):start]))
            # A hedge later in the same clause covers every use named before it: "للاستخدام اليومي
            # والكاجوال محتاجة تأكيد" names two aliases before its "محتاجة تأكيد".
            uncertain = bool(_UNCERTAIN.search(text[end:]))
            yield category, negative, uncertain


def _note_aliases():
    # The sanitizer consumes this module too, so import its vocabulary lazily.
    from .reply_sanitizer import _NOTE_ALIASES
    from .sales.notes import NOTE_FAMILIES

    aliases = {_NORMALIZE(alias) for values in _NOTE_ALIASES.values() for alias in values}
    aliases.update(_NORMALIZE(note) for note in NOTE_FAMILIES)
    aliases.update(("برغموت", "برغموط", "برجاموت"))
    # These are scent families/adjectives, rather than named ingredient questions.
    families = set(_BY_TAG_ALIAS) | {"aromatic", "green", "marine", "wood", "خشب", "flowers"}
    aliases.difference_update(families - {"oud", "عود", "leather"})
    return aliases


def _ingredient_mentions(text, recorded=()):
    aliases = _note_aliases()
    if recorded:
        from .sales.notes import parse_notes
        aliases.update(
            normalized for field in recorded for note in parse_notes(field)
            if (normalized := _NORMALIZE(note)) and not _TAG_PATTERN.fullmatch(normalized)
        )
    pattern = r"(?<!\w)(?:[وبل]?ال|[وب])?(?:" + "|".join(map(re.escape, sorted(aliases, key=len, reverse=True))) + r")(?!\w)"
    return list(re.finditer(pattern, text))


# "لسه بتكرر نفس النوتات من غير مصدر 😅 خلينا في اللي متأكدين منه—الـ90 مل من Layton بكام؟" complains
# that the notes keep coming back; its "النوتات" was read as a request and the whole list went out
# again above the price (simulation S02 round 3 replay 2, t6, high).
_NOTE_COMPLAINT = re.compile(r"(?:بتكرر|بتعيد|كررت|عيدت|تكرار)\w*\s+(?:\S+\s+){0,2}$")


def _notes_authorized(text, recorded=()):
    mentions = [*_NOTE_SCOPE.finditer(text), *_ingredient_mentions(text, recorded)]
    return any(not _NOTE_OPT_OUT.search(text[max(0, match.start() - 70):match.start()])
               and not _NOTE_COMPLAINT.search(text[max(0, match.start() - 40):match.start()])
               for match in mentions)


def notes_requested(message, history=None, *, product_names=()):
    """Explicit current-turn notes/ingredient scope; old permission never leaks."""
    return _notes_authorized(_without_names(message, product_names))


def scent_requested(message, *, product_names=()):
    """Open character descriptions/opinions, rather than single trait answers."""
    text = _without_names(message, product_names)
    if _notes_authorized(text) and not re.search(
        # "مش بحب العود خالص. ريحة K By Dolce عاملة إزاي بالظبط؟" asks how it smells in so many words;
        # its oud word made it a notes question only, and the reply was "مفيش عود ضمن نوتاته المسجلة"
        # with no description (simulation S14 round 4 replay, t2).
        r"ريحته|ريحتها|ريحتهم|الريحه|رايك|ريحه\s+(?:عامله?\s+)?(?:ايه|ازاي|كيف)|"
        r"\b(?:scent|smells?|fragrance|opinion)\b", text,
    ):
        return False
    if occasion_requested(text) and not re.search(
        r"ريحته|ريحتها|ريحتهم|الريحه|رايك|تفاصيل|اوصف(?:ه|ها|هم)(?:ولي|لي)?|"
        r"\b(?:scent|smells?|fragrance|opinion)\b|\bdescribe\s+and\b", text,
    ):
        return False
    return any(not _NOTE_OPT_OUT.search(text[max(0, match.start() - 70):match.start()])
               for match in _SCENT_SCOPE.finditer(text))


def scent_excluded(message):
    """An explicit description opt-out, with no positive description request."""
    text = _NORMALIZE(message)
    return not scent_requested(text) and any(
        _NOTE_OPT_OUT.search(text[max(0, match.start() - 70):match.start()])
        for match in _SCENT_SCOPE.finditer(text)
    )


def _tag_mentions(text):
    result = []
    for match in _TAG_PATTERN.finditer(text):
        negative = bool(_NEGATIVE.search(text[max(0, match.start() - 35):match.start()]))
        result.append((_BY_TAG_ALIAS[match.group("alias")], match.start(), match.end(), negative))
    return result


def _name_pattern(product_names, *, normalize=_NORMALIZE):
    names = sorted({normalize(name) for name in product_names if name}, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:[وبل]?ال|[وب])?(?P<name>"
                      + "|".join(map(re.escape, names)) + r")(?!\w)") if names else None


def request_texts(message, product_names):
    """Keep each resolved subject's question, sharing consecutive named groups."""
    names = {_NORMALIZE(name): name for name in product_names if name}
    text = _NORMALIZE(message)
    pattern = _name_pattern(product_names)
    matches = list(pattern.finditer(text)) if pattern else []
    if not matches:
        return {name: text for name in product_names}
    groups = []
    for match in matches:
        if groups and re.fullmatch(r"\s*(?:و|او|and|or|with|مع)?\s*", text[groups[-1][-1].end():match.start()]):
            groups[-1].append(match)
        else:
            groups.append([match])
    scoped = {name: [] for name in product_names}
    starts = [group[0].start() for group in groups]
    for index in range(1, len(groups)):
        previous_end = groups[index - 1][-1].end()
        between = text[previous_end:starts[index]]
        dimensions = [*_NOTE_SCOPE.finditer(between), *_OCCASION_SCOPE.finditer(between), *_SCENT_PROSE.finditer(between),
                      *_SCENT_SCOPE.finditer(between), *re.finditer(
                          r"(?<!\w)(?:و)?(?:ريحه|سعر|اسعار|ثبات|فوحان|اداء|تركيز|موسم|"
                          r"price|cost|longevity|projection|performance|availability|concentration|season)(?!\w)",
                          between,
                      )]
        for dimension in sorted(dimensions, key=lambda match: match.start(), reverse=True):
            if re.fullmatch(r"\s*(?:(?:بتاع|بتاعت|عن|of|for)\s*)?", between[dimension.end():]):
                # Move only a trailing subject dimension to the following name,
                # leaving the earlier subject's suffix question in its own scope.
                opt_out = _NOTE_OPT_OUT.search(between[:dimension.start()])
                starts[index] = previous_end + (opt_out.start() if opt_out else dimension.start())
                break
    for index, group in enumerate(groups):
        end = starts[index + 1] if index + 1 < len(groups) else len(text)
        prefix = text[:group[0].start()] if index == 0 else text[starts[index]:group[0].start()]
        question = prefix + " " + text[group[-1].end():end]
        for match in group:
            scoped[names[match.group("name")]].append(question)
    return {name: " ".join(parts) for name, parts in scoped.items()}


def _normalize_prose(value):
    """Normalize prose and catalog names while retaining sentence boundaries."""
    return " ".join(part if re.fullmatch(r"[.!؟?\n؛;،,]", part) else _NORMALIZE(part)
                    for part in re.split(r"([.!؟?\n؛;،,])", str(value or ""))).strip()


def _attributed_segments(reply, facts):
    # Use the same punctuation normalization for prose, names and lookups.
    text = _normalize_prose(reply)
    names = {_normalize_prose(name): name for name in facts}
    names.pop("", None)
    if not names:
        return {}
    pattern = _name_pattern(facts, normalize=_normalize_prose)
    matches = list(pattern.finditer(text))
    if not matches and len(names) == 1:
        return {next(iter(names.values())): text}
    segments = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        name = names[match.group("name")]
        segments.setdefault(name, []).append(text[match.end():end])
    return {name: " ".join(parts) for name, parts in segments.items()}


def _character_text(segment, recorded=()):
    """Mask ingredient answers while retaining character/occasion positions."""
    spans = []
    for mention in _NOTE_SCOPE.finditer(segment):
        following = segment[mention.end():]
        boundary = re.search(r"[.!؟?\n؛;،,]", following)
        character = _SCENT_PROSE.search(following)
        ends = [match.start() for match in (boundary, character) if match]
        spans.append((mention.start(), mention.end() + min(ends) if ends else len(segment)))
    for mention in _ingredient_mentions(segment, recorded):
        # A bare material-like style (oud, leather) remains ordinary character.
        if not _TAG_PATTERN.fullmatch(mention.group()) or re.search(
            r"فيه\s*$|فيها\s*$|يحتوي|\bcontains?\s*$", segment[max(0, mention.start() - 25):mention.start()],
        ):
            spans.append((mention.start(), mention.end()))
    masked = list(segment)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    return "".join(masked)


# Labels that are also a material. "مفيهوش عود" is a statement about the stored notes, so it is
# supported when notes are recorded and none of them is that material.
_MATERIAL_NOTES = {"oud": ("oud", "agarwood", "عود"), "musky": ("musk", "مسك"), "leather": ("leather", "جلد")}


def _absence_in_notes(row, tag):
    words = _MATERIAL_NOTES.get(tag)
    notes = " ".join(str(note) for note in (row.get("notes") or ()) if note).lower()
    return bool(words and notes.strip()) and not any(word in notes for word in words)


_PROMINENCE = re.compile(r"(?:هو\s+|هي\s+)?(?:اوضح|اقوي|الغالب|غالب|طاغي|طاغيه|ابرز|باين\s+اكتر|واضح\s+اكتر)(?!\w)")


# A question that names a note layer: "الـtop", "base", "الـdry down", "الافتتاحية", "القاعدة".
LAYER_QUESTION = re.compile(r"(?<![a-z])(?:top|middle|heart|base)(?![a-z])|dry\s*-?\s*down|دراي\s*داون|افتتاحي|قاعد")


# A trait asked about by name gets its noun in the answer only when that noun is not itself a
# label alias: "التوابل مش مؤكدة" would read back to the claim checker as a spicy claim.
_TRAIT_NOUNS = {"sweet": "الحلاوة", "fresh": "الانتعاش", "woody": "ريحة الخشب", "heavy": "التقل",
                "light": "الخفة"}
# A trait the customer is asking for ("وعايز حاجة مسكرة") is a wish, not a question about this perfume.
_TRAIT_WISH = re.compile(r"(?<!\w)و?(?:عايز|عاوز|عايزه|عاوزه|محتاج|محتاجه|بحب|بدور|نفسي|رشح\w*|اقترح\w*|"
                         r"want|need|looking|recommend|suggest)(?!\w)[^.،,]*$")


def asked_attribute_answers(text, product, *, traits=True):
    """Direct answers for the ingredients and traits a yes/no question names, or `[]`.

    "212 VIB Man فيه عود أو ريحته مسكرة؟" got every recorded note and no answer to either question,
    so the customer had to ask again (simulation S10 round 2, t4). An ingredient is answered from
    the stored notes. A trait is answered from the saved labels, and one the labels do not record
    is unconfirmed, never absent (owner decision 4, 2026-11-12).
    """
    text = _NORMALIZE(text or "")
    from .reply_sanitizer import _NOTE_ALIASES
    recorded = [field for field in (product.top_notes, product.middle_notes, product.base_notes) if field]
    tags = _all_tags(product.scent_tags)
    answers, materials = [], set()
    if recorded and not _NOTE_SCOPE.search(text):
        known = _note_aliases()
        for match in _ingredient_mentions(text, recorded):
            # "ورد" (rose) begins with the letter of the conjunction: stripped blindly it became "رد",
            # and the reply said "مفيش رد ضمن نوتاته" (simulation S15 round 2 full 2, t3).
            raw = match.group()
            stripped = re.sub(r"^(?:[وبل]?ال|[وب])", "", raw)
            word = raw if raw in known and stripped not in known else stripped
            if _NOTE_OPT_OUT.search(text[max(0, match.start() - 40):match.start()]):
                continue
            aliases = next((values for values in _NOTE_ALIASES.values()
                            if word in {_NORMALIZE(value) for value in values}), (word,))
            materials.update(tag for tag, names in _MATERIAL_NOTES.items()
                             if any(_NORMALIZE(name) in {_NORMALIZE(a) for a in aliases} for name in names))
            homes = [(label, value) for label, value in (("الـTop Notes", product.top_notes),
                                                          ("الـMiddle Notes", product.middle_notes),
                                                          ("الـBase Notes", product.base_notes))
                     if value and any(re.search(r"(?<!\w)" + re.escape(_NORMALIZE(alias)) + r"(?!\w)", _NORMALIZE(value))
                                      for alias in aliases)]
            if not homes:
                answers.append(f"مفيش {word} ضمن نوتاته المسجلة")
            elif LAYER_QUESTION.search(text):
                # "فلفل في الـbase؟ اللي أعرفه إنه في الـtop": the layer it is recorded in is the answer,
                # whatever layer the customer believed (simulation S02 round 2, t4).
                answers.append(f"{word} مسجل عندنا في {homes[0][0]}: {homes[0][1]}")
            else:
                answers.append(f"فيه {word} في نوتاته")
    for tag, start, _end, _negative in (_tag_mentions(text) if traits else ()):
        if tag in materials or _TRAIT_WISH.search(text[max(0, start - 40):start]):
            continue
        if tag in tags:
            answers.append(f"طابعه المسجل {_PHRASES[tag]}")
        elif tag in _MATERIAL_NOTES:
            # A material label ("ريحته عودي؟") the labels lack is answered from the notes, as an
            # ingredient would be.
            word = _MATERIAL_NOTES[tag][-1]
            present = any(re.search(r"(?<!\w)" + re.escape(_NORMALIZE(name)) + r"(?!\w)", _NORMALIZE(" ".join(recorded)))
                          for name in _MATERIAL_NOTES[tag])
            if recorded:
                answers.append(f"فيه {word} في نوتاته" if present else f"مفيش {word} ضمن نوتاته المسجلة")
        else:
            noun = _TRAIT_NOUNS.get(tag, "الصفة دي")
            answers.append(f"{noun} مش مؤكدة في طابعه المسجل")
    answers = list(dict.fromkeys(answers))
    if answers and any("مش مؤكدة" in answer for answer in answers) and tags:
        answers.append("وصفه المسجل: " + describe_scent(tags).replace("ريحته ", ""))
    asked_traits = {tag for tag, _, _, _ in _tag_mentions(text)} if traits else set()
    if len(asked_traits) >= 2 and re.search(r"(?<!\w)(?:ولا|او|اكتر|اوضح|اقوي|غالب\w*)(?!\w)", text):
        # "ريحته سويت قوي ولا التوابل والخشب أوضح؟" asks which trait leads; the labels say which ones
        # are there, not which is stronger (simulation S14 round 2 full 4, t5/t7).
        answers.append("ومقدرش أحدد أنهي أوضح فيهم من البيانات اللي عندي")
    return answers


def _unsolicited_notes(segment, recorded):
    issues = []
    if _NOTE_SCOPE.search(segment) and not _UNCERTAIN.search(segment):
        issues.append("notes require an explicit current question")
    for match in _ingredient_mentions(segment, recorded):
        before = segment[max(0, match.start() - 35):match.start()]
        if _UNCERTAIN.search(before):
            continue
        ingredient = re.sub(r"^(?:[وبل]?ال|[وب])", "", match.group())
        # A style word such as oud/leather is also a literal note name. Permit
        # the character reading unless the surrounding phrase claims a material.
        is_style = any(item[1] == match.start() and item[2] == match.end() for item in _tag_mentions(segment))
        if is_style and not re.search(r"فيه\s*$|فيها\s*$|يحتوي|مكونات|نوت|\bcontains?\s*$", before):
            continue
        issues.append("unsolicited ingredient " + ingredient)
    return issues


def description_issues(reply, facts, *, message=None, history=None,
                       preferred_occasion=None, required=False):
    """Check only attributable, bounded scent claims against recorded data.

    Legacy direct validators that omit the current message retain their original
    scope. The caller supplies the resolved facts and recommendation requirement.
    """
    if message is None or not facts:
        return []
    product_names = tuple(facts)
    requests = request_texts(message, product_names)
    segments = _attributed_segments(reply, facts)
    issues = []
    for name, row in facts.items():
        request_text = requests[name]
        wants_scent = scent_requested(request_text)
        excludes_scent = scent_excluded(request_text)
        allows_notes = _notes_authorized(request_text, row.get("notes", ()))
        request_traits = {tag for tag, _, _, _ in _tag_mentions(request_text)}
        trait_only = bool(request_traits and not wants_scent and not allows_notes and not required)
        note_only = allows_notes and not wants_scent and not required
        segment = segments.get(name, "")
        character = _NORMALIZE(_character_text(segment, row.get("notes", ())) if allows_notes else segment)
        mentions = _tag_mentions(character)
        actual_prose = bool(mentions or (_SCENT_PROSE.search(character) and not _UNCERTAIN.search(character)))
        full_description = not excludes_scent and bool(
            required or wants_scent or (actual_prose and not trait_only and not note_only)
        )
        problems = []
        for word in _UNLABELLED_CHARACTER.finditer(character):
            if not _UNCERTAIN.search(character[max(0, word.start() - 40):word.end() + 40]):
                problems.append("character beyond the saved labels is not recorded: " + word.group())
                break
        recorded = set(row.get("scent_tags") or ())
        for tag, start, end, negative in mentions:
            if (negative and tag not in recorded and not (_OPPOSITE_TAGS.get(tag, set()) & recorded)
                    and not _absence_in_notes(row, tag)
                    and not _UNCERTAIN.search(character[max(0, start - 40):end + 40])):
                problems.append("an unrecorded trait is not confirmed absent; say it is not confirmed: " + tag)
            # The other half of owner decision 4: a label that IS recorded is not "unconfirmed". "الجلدية
            # مش مؤكدة في طابعه… والتوابل مش مؤكدة" about Pure XS, labelled leather and spicy
            # (simulation S14 round 2 batch 3, t6).
            if tag in recorded and not negative and _UNCERTAIN.match(character[end:end + 30].lstrip()):
                problems.append("a recorded label is stated as unconfirmed; it is recorded: " + tag)
            # "المسكر أوضح في طابعه، والجلدي موجود كمان": the labels say which traits are there, not
            # which leads (simulation S14 round 2 full 4, t7, critical).
            if _PROMINENCE.match(character[end:end + 20].lstrip()) \
                    and not _UNCERTAIN.search(character[max(0, start - 40):end + 60]):
                problems.append("which trait is stronger is not recorded: " + tag)
        if excludes_scent and actual_prose and not trait_only:
            problems.append("the current question explicitly excludes a scent description")
        if segment and not allows_notes:
            problems.extend(_unsolicited_notes(segment, row.get("notes", ())))
        occasion_claims = list(_occasion_claims(segment, row.get("notes", ())))
        # The scoped request text is normalised without its question mark; when nothing was scoped
        # (one subject, or a name written in Arabic), the raw message still carries it.
        asked_occasion = occasion_requested(request_text) or (
            request_text == _NORMALIZE(message) and occasion_requested(message, product_names=product_names))
        if occasion_claims and not asked_occasion:
            problems.append("occasion suitability requires an explicit current question")
        else:
            saved_occasions = occasions.occasion_groups(row.get("occasion", ""))
            for category, negative, uncertain in occasion_claims:
                if uncertain:
                    continue
                if negative and category in saved_occasions:
                    problems.append("do not deny a saved occasion: " + category)
                elif category not in saved_occasions and not (negative and saved_occasions):
                    # A denial of an unrecorded use ("مناسبته المسجلة الخروجات، مش الشغل") is true,
                    # and rejecting it left the customer with a non-answer (simulation S08 t3).
                    problems.append("occasion is not recorded; do not invent it: " + category)
        if full_description:
            expected_tags = selected_tags(row.get("scent_tags"))
            positive = [(tag, start, end) for tag, start, end, negative in mentions if not negative]
            actual_tags = list(dict.fromkeys(tag for tag, _, _ in positive))
            if expected_tags:
                if set(actual_tags) != set(expected_tags):
                    problems.append("use the first three saved labels: " + ", ".join(expected_tags))
                if any(tag in expected_tags and negative for tag, _, _, negative in mentions):
                    problems.append("do not deny saved character labels")
            elif actual_tags:
                problems.append("character is not recorded; do not invent it")
            elif not _UNCERTAIN.search(segment):
                problems.append("missing character needs an honest confirmation phrase")
        for problem in problems:
            issues.append(f"description:{name}: {problem}")
    return list(dict.fromkeys(issues))
