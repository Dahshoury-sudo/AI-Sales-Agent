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
    r"(?<!\w)(?:[وبل]?ال|[وب])?(?P<alias>"
    + "|".join(re.escape(alias) for alias in sorted(_BY_TAG_ALIAS, key=len, reverse=True))
    + r")(?!\w)"
)
_NOTE_SCOPE = re.compile(
    r"(?<!\w)(?:[وبل]?ال|[وب])?(?:نوت(?:ه|ات|س)?(?:ه|ها|هم|هن)?|مكونات(?:ه|ها|هم|هن)?|"
    r"تركيب(?:ه|ته|تها)|افتتاحي(?:ه|ته)|قلب|قاعد(?:ه|ته)|طبقات|"
    r"notes?|ingredients?|composition|opening|drydown|layers?)(?!\w)|"
    r"\b(?:top|middle|base|heart)\s+notes?\b|\bcontains?\b|بيحتوي\s+علي"
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
_UNCERTAIN = re.compile(r"محتاج(?:ه)?\s+تاكيد|مش\s+(?:مسجل|متاكد|معروف)|غير\s+(?:مسجل|متوفر)|ماقدرش|مقدرش|لا\s+توجد|not\s+(?:recorded|known)|needs?\s+confirmation")
_OCCASION_NEGATIVE = re.compile(
    r"(?<!\w)(?:و)?(?:مش|غير|لا|بدون|من غير|not|no|without)"
    r"(?:\s+(?:مناسب(?:ه)?|بيناسب|ينفع|صالح|suitable|suited|appropriate|good|for|the)){0,3}\s*$"
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
    """An unnamed Egyptian description, with one saved use and no full stop."""
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
    chosen = selected_occasion(occasion, preferred_occasion)
    if chosen:
        wording = "للاستخدام اليومي" if chosen == occasions.DAILY else occasions.OCCASION_DESCRIPTIONS[chosen]
        description += "، ومناسب " + wording
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


def _notes_authorized(text, recorded=()):
    mentions = [*_NOTE_SCOPE.finditer(text), *_ingredient_mentions(text, recorded)]
    return any(not _NOTE_OPT_OUT.search(text[max(0, match.start() - 70):match.start()])
               for match in mentions)


def notes_requested(message, history=None, *, product_names=()):
    """Explicit current-turn notes/ingredient scope; old permission never leaks."""
    return _notes_authorized(_without_names(message, product_names))


def scent_requested(message, *, product_names=()):
    """Open character descriptions/opinions, rather than single trait answers."""
    text = _without_names(message, product_names)
    if _notes_authorized(text) and not re.search(
        r"ريحته|ريحتها|ريحتهم|الريحه|رايك|\b(?:scent|smells?|fragrance|opinion)\b", text,
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
        dimensions = [*_NOTE_SCOPE.finditer(between), *_SCENT_PROSE.finditer(between),
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
        if excludes_scent and actual_prose and not trait_only:
            problems.append("the current question explicitly excludes a scent description")
        if segment and not allows_notes:
            problems.extend(_unsolicited_notes(segment, row.get("notes", ())))
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
            occasion = selected_occasion(row.get("occasion", ""), preferred_occasion)
            occasion_mentions = _occasion_mentions(character)
            actual_occasions = list(dict.fromkeys(category for category, _, _ in occasion_mentions))
            denied_occasions = {category for category, start, _ in occasion_mentions
                                if _OCCASION_NEGATIVE.search(character[max(0, start - 50):start])}
            if occasion:
                if actual_occasions != [occasion] or occasion in denied_occasions:
                    problems.append("use one saved occasion: " + occasion)
                elif positive and occasion_mentions[0][1] < max(end for _, _, end in positive):
                    problems.append("place the occasion after the character labels")
            elif actual_occasions:
                problems.append("occasion is not recorded; do not invent it")
        for problem in problems:
            issues.append(f"description:{name}: {problem}")
    return list(dict.fromkeys(issues))
