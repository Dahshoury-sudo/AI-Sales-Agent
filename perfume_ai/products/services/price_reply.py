"""Compose text answers beneath one catalogue heading per quoted perfume.

Only leading grammatical subjects are moved into headings. Ambiguous comparisons
stay untouched and ask the existing grounding retry to repair their attribution.
"""

import json
import re
from decimal import Decimal

from .product_formatting import ProductQuote, QUOTE_MARKER, quote_evidence, render_quotes
from .sales import naming


def _quotes(evidence):
    return [ProductQuote(**{**record, "price": Decimal(record["price"])})
            for record in evidence.get("quotes", [])]


def _name_pattern(names):
    # Arabic conjunctions can touch a Latin name; another Latin word cannot.
    return re.compile(r"(?<![a-z0-9_])(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
                      + r")(?![a-z0-9_])", re.I)


def _price_only(message, names, additional_words=(), alias_products=()):
    text = naming.normalize_product_name(message)
    normalized_names = [naming.normalize_product_name(name) for name in names]
    if normalized_names:
        text = _name_pattern(normalized_names).sub(" ", text)
    if (re.search(r"اوريج|اورجين|original", text) and "تركيب" in text
            and re.search(r"ولا|\bاو\b|\bor\b", text)):
        return False
    text = re.sub(r"\d+(?:[.,]\d+)?", " ", text)
    text = re.sub(r"زجاجه\s+البراند\b", "زجاجه", text)
    allowed = {
        "بكام", "سعر", "سعره", "اسعار", "الاسعار", "السعر", "ثمن", "احجام", "الاحجام",
        "حجم", "الحجم", "ملي", "مل", "ml", "price", "prices", "sizes", "size",
        "زجاجه", "زجاجة", "الزجاجه", "الستور", "الخاصة", "الخاصه", "بينا", "عاديه",
        "تركيب", "اوريجنال", "اوريجينال", "اورجينال", "original", "normal",
        "ايه", "هو", "هي", "ده", "دي", "دول", "الاتنين", "لااتنين", "كلهم", "كل", "و", "ولا",
        "لو", "سمحت", "ممكن", "عايز", "عاوزه", "اعرف", "قوللي", "قولي", "عندكم",
        "تمام", "ماشي", "اه", "ايوه", "نعم", "اوكي", "اوكى", "ok", "yes", "طب", "طيب", "بقي",
    }
    allowed.update(additional_words)
    words = re.findall(r"[^\W\d_]+", text)
    unknown = [word for word in words if word not in allowed and not (word.startswith("و") and word[1:] in allowed)]
    if len(unknown) == 1 and alias_products:
        # Resolve only a single remaining alias token, never a span containing
        # extra requested dimensions. This preserves established Arabic subjects.
        matched = naming.match_product(unknown[0], alias_products[0].store, products=alias_products)
        if matched and matched.pk in {p.pk for p in alias_products}:
            unknown = []
    return bool(words) and not unknown


def supported_facts_question(message, products=()):
    """Narrow deterministic comparisons must not swallow other requested details."""
    # Only recognised request words may remain after exact catalogue names.
    # An unrecognised alias safely stays on the normal generation path.
    text = naming.normalize_product_name(message).strip(" ؟?.!")
    if len(products) == 1 and re.fullmatch(r"(?:ثبات|فوحان)\s+[^\W\d_][\w]*\s+قد\s+ايه", text):
        # A single resolved subject between the performance axis and duration
        # question can be an Arabic alias. A conjunction starts another request.
        subject = text.split()[1]
        if not subject.startswith("و"):
            return True
    return _price_only(message, [p.name for p in products], {
        "قارن", "مقارنه", "بين", "في", "من", "عطر", "برفان",
        "ثبات", "ثباته", "الثبات", "يثبت", "فوحان", "فوحانه", "الفوحان", "longevity", "projection",
        "اوفر", "الاوفر", "اقل", "اكتر", "احسن", "افضل", "انهي", "حلاوه", "حلو", "مسكر",
        "sweet", "sweetness", "value", "per", "ml", "compare", "comparison", "and", "or",
        "الملي", "المل", "كام", "قد", "ساعه", "ساعات", "مده", "المده", "hours", "hour", "رقم", "بالارقام",
        "متر", "امتار", "meters", "metres",
    }, alias_products=products)


def _redundant_quotation_clause(clause):
    """Drop only bottle/availability/size wording already supplied by code."""
    from .product_formatting import monetary_claims
    if monetary_claims(clause):
        return False
    text = naming.normalize_product_name(clause)
    if not re.search(r"زجاج|ملي|\bمل\b|متاح|متوفر|موجود", text):
        return False
    allowed = {
        "زجاجه", "الزجاجه", "الستور", "الاستور", "الخاصة", "الخاصه", "بينا", "البراند",
        "اوريجنال", "اوريجينال", "اورجينال", "original", "normal",
        "متاح", "متاحه", "متوفر", "متوفره", "موجود", "موجوده", "عندنا",
        "ملي", "مل", "ml", "احجام", "الاحجام", "حجم", "الحجم", "حجمين", "بحجم", "بحجمين", "باحجام",
        "واحد", "واحده", "اتنين", "اثنين", "تلاته", "ثلاثه",
        "و", "او", "كمان", "بس", "في", "فيه",
    }
    return all(word in allowed or (word.startswith("و") and word[1:] in allowed)
               for word in re.findall(r"[^\W\d_]+", text))


def quotation_prose(reply, evidence):
    """Remove only final quotation rows from their own product sections.

    A duplicate row generated earlier in the same section, or under another
    perfume heading, remains untrusted and is still checked as a price claim.
    """
    sections = evidence.get("sections")
    if not isinstance(sections, list):
        block = evidence.get("text", "")
        return reply.replace(block, "") if block else reply
    footer = evidence.get("footer", "")
    if footer and reply.rstrip().endswith(footer):
        reply = reply.rstrip()[:-len(footer)].rstrip()
    names = [section.get("name", "") for section in sections]
    headings = list(re.finditer(r"(?m)^(?:" + "|".join(re.escape(name) for name in names if name)
                               + r")\s*$", reply))
    removals = []
    for section in sections:
        matches = [h for h in headings if h.group().strip() == section.get("name")]
        if len(matches) != 1 or not section.get("text"):
            continue
        heading = matches[0]
        end = next((h.start() for h in headings if h.start() > heading.start()), len(reply))
        segment = reply[heading.end():end].rstrip()
        block = section["text"]
        if segment.endswith(block):
            stop = heading.end() + len(segment)
            removals.append((stop - len(block), stop))
    for start, end in sorted(removals, reverse=True):
        reply = reply[:start] + reply[end:]
    return reply


def composition_issues(reply, context):
    evidence = quote_evidence(context)
    if not evidence.get("sections"):
        return []
    names = sorted({q.get("name", "") for q in evidence.get("quotes", [])}, key=len, reverse=True)
    pattern = _name_pattern([name for name in names if name])
    actual = list(pattern.finditer(reply))
    counts = {name: sum(match.group().lower() == name.lower() for match in actual) for name in names}
    issues = []
    if any(count != 1 for count in counts.values()) or evidence.get("ambiguous_prose"):
        issues.append("price_layout: اكتب لكل عطر عنوان واحد باسمه، ثم تفاصيله بدون تكرار الاسم؛ لا تخلط صفات عطرين")
    return issues


def compose_price_reply(reply, context):
    evidence = quote_evidence(context)
    if "request" not in evidence or not evidence.get("append") or not evidence.get("quotes"):
        from .product_formatting import append_quotation
        return append_quotation(reply, context), context
    # Already composed replies are safe to append again without duplicating rows.
    if evidence.get("sections") and quotation_prose(reply, evidence) != reply:
        return reply, context
    quotes = _quotes(evidence)
    groups = {}
    for quote in quotes:
        groups.setdefault((quote.product_id, quote.name), []).append(quote)
    names = [name for _, name in groups]
    pattern = _name_pattern(names)
    details = {name: [] for name in names}
    subject = names[0] if len(names) == 1 else None
    ambiguous, outside = False, []
    pure_price = _price_only(evidence["request"], names)
    # "طب سعر الـ90 من Aventus كام؟" after a notes discussion got the whole notes list above the price,
    # and the price question counted as not answered directly (simulation S02 round 2 full 2, t7).
    from .scent_description import _NORMALIZE, _notes_authorized
    notes_unrequested = not _notes_authorized(_NORMALIZE(evidence["request"]))
    if not pure_price:
        # Split whole sentences (never decimal prices), and explicit name clauses.
        separator = r"\n+|(?<!\d)[.!?؟;]+\s*|[.!?؟;]+(?!\d)\s*"
        for clause in re.split(separator, reply or ""):
            clause = clause.strip().strip("* ")
            if not clause:
                continue
            matches = list(pattern.finditer(clause))
            if len(matches) == 1:
                match = matches[0]
                prefix, suffix = clause[:match.start()].strip(), clause[match.end():].strip(" :—-* ")
                lead = naming.normalize_product_name(prefix)
                if re.fullmatch(r"(?:و|اما|عطر|عندنا|عندنا عطر|ارشحلك|انصحك ب|انصحك بـ|بالنسبه ل|بالنسبه لـ|بالنسبه|بالنسبة ل)?", lead):
                    subject = next(name for name in names if name.lower() == match.group().lower())
                    clause = suffix
                elif lead in ("ثبات", "فوحان", "مكونات", "ريحه", "ريحة"):
                    subject = next(name for name in names if name.lower() == match.group().lower())
                    clause = prefix + " " + suffix
                else:
                    ambiguous = True
            elif matches:
                ambiguous = True
            if naming.normalize_product_name(clause) in ("متوفر", "موجود", "متاح", "متوفر عندنا", "موجود عندنا", "عندنا"):
                continue
            if _redundant_quotation_clause(clause):
                continue
            if notes_unrequested and re.match(r"(?:و)?(?:ال)?(?:نوتات(?:ه|ها)?|نوتس)(?!\w)|(?:top|base|middle)?\s*notes?\b",
                                              naming.normalize_product_name(clause), re.I):
                continue
            if clause:
                if matches and ambiguous and pattern.search(clause):
                    outside.append(clause)
                elif subject is None:
                    outside.append(clause)
                    ambiguous = True
                else:
                    details[subject].append(clause.rstrip(".") + ".")
    budget = Decimal(evidence["budget"]) if evidence.get("budget") is not None else None
    # "سوفاج 90 بكام؟ ومتوفر؟" asks two things, and the bare "متوفر" clause is dropped above as a
    # repeat of the quotation; when it was all the prose said, the second answer vanished
    # (simulation S04 final t1). A quoted variant is in stock and matches the requested size and
    # bottle by construction (`product_quotes`), so the code states it.
    asks_available = re.search(r"متوفر|موجود|متاح|عندك|available|in stock",
                                naming.normalize_product_name(evidence["request"]), re.I)
    sections, display = [], []
    for (product_id, name), current in groups.items():
        rows = render_quotes(current, budget, include_name=False)
        lines = details[name]
        if asks_available and not any(re.search(r"متوفر|متاح|موجود", line) for line in lines):
            lines = ["متوفر.", *lines]
        sections.append({"product_id": product_id, "name": name, "text": rows})
        display.append("\n".join([name, *lines, rows]))
    if outside:
        display.append("\n".join(outside))
    if evidence.get("footer"):
        display.append(evidence["footer"])
    evidence = {**evidence, "sections": sections, "text": "\n\n".join(s["text"] for s in sections),
                "ambiguous_prose": ambiguous}
    context += "\n" + QUOTE_MARKER + json.dumps(evidence, ensure_ascii=False)
    return "\n\n".join(display), context


def requested_product_details(products, message, *, preferred_occasion=None, history=None,
                              description_required=False, include_performance=True):
    """Render the independently requested facts for each resolved product."""
    products = list(products)
    lines = []
    from . import performance_policy
    from .scent_description import (describe_scent, scent_requested, scent_excluded, selected_occasion,
                                    occasion_preference, occasion_requested, requested_occasions,
                                    request_texts, _notes_authorized, asked_attribute_answers, LAYER_QUESTION)
    from .product_formatting import is_variant_available
    from products.occasions import OCCASION_DESCRIPTIONS
    product_names = [p.name for p in products]
    requests = request_texts(message, product_names)
    fits_occasion = {}
    for product in products:
        text = requests[product.name]
        preferred = occasion_preference(text, saved=preferred_occasion)
        wants_notes = _notes_authorized(text, (product.top_notes, product.middle_notes, product.base_notes))
        wants_scent = not scent_excluded(text) and (description_required or scent_requested(text))
        request = performance_policy.request_for(text, history)
        facts = []
        # With a description already given, only the ingredients asked about are answered: "رشحلي حاجة
        # مش مسكرة ولا فيها عود" got each perfume's full notes list (simulation S10 round 2, t4/t8).
        direct = [] if scent_excluded(text) else asked_attribute_answers(text, product, traits=not wants_scent)
        if wants_scent:
            facts.append(describe_scent(product.scent_tags, product.occasion, preferred_occasion=preferred))
        if direct:
            facts.extend(direct)
        elif wants_notes:
            notes = [n for n in (product.top_notes, product.middle_notes, product.base_notes) if n]
            layers = [(label, value) for label, value in (("الـTop Notes", product.top_notes),
                                                           ("الـMiddle Notes", product.middle_notes),
                                                           ("الـBase Notes", product.base_notes)) if value]
            if LAYER_QUESTION.search(text) and len(layers) == 3:
                # "فلفل في الـbase؟ اللي أعرفه إنه في الـtop" asks which layer; one unlabelled list of
                # every note could not answer it (simulation S02 round 2, t4).
                facts.append(". ".join(f"{label}: {value}" for label, value in layers))
            else:
                facts.append("نوتاته " + "، ".join(notes) if notes else "نوتاته محتاجة تأكيد")
        if include_performance and (request.longevity or request.projection):
            facts.append(performance_policy.description(request, longevity=request.longevity, projection=request.projection))
        if re.search(r"متوفر|موجود|متاح|عندك|available|availability|in stock", text, re.I):
            available = product.is_active and any(v.volume > 0 and is_variant_available(v)
                                                 for v in product.variants.all())
            facts.append("متوفر" if available else "مش متوفر حاليا")
        if re.search(r"رجال|حريم|للجنسين|gender|male|female|unisex", text, re.I):
            facts.append({"male": "رجالي", "female": "حريمي", "unisex": "للجنسين"}.get(product.gender, "نوعه مش مسجل عندي"))
        if re.search(r"اصلي|اصال|تركيب|سائل|تصنيع|مصنع|authentic|original", text, re.I):
            from .sales.constraints import formulation_reply
            facts.append(formulation_reply().split(" لو مناسب ليك")[0])
        if re.search(r"فرق.*زجاج|فرق.*عبو|bottle.*difference", text, re.I):
            from .bottle_wording import BOTTLE_DIFFERENCE
            facts.append(BOTTLE_DIFFERENCE)
        if re.search(r"تركيز|perfume type|concentration|edp|edt|extrait", text, re.I):
            facts.append("تركيزه " + product.perfume_type if product.perfume_type else "تركيزه مش مسجل عندي")
        if re.search(r"تقييم|reviews?|ratings?", text, re.I):
            facts.append("مش عندي تقييمات عملاء مسجلة")
        # The scoped text has lost its question mark; with one perfume the raw message is the same scope.
        # "Wanted by Night تقيل أوي ولا ينفع للسهرات؟" got the heaviness and not the evening answer
        # (simulation S14 round 2 full 3, t7). `description_issues` reads it the same way.
        if occasion_requested(text) or (len(products) == 1 and message
                                        and occasion_requested(message, product_names=product_names)):
            requested = requested_occasions(text)
            occasion = selected_occasion(product.occasion, "; ".join(requested) or preferred_occasion)
            if requested and occasion not in requested:
                occasion = ""
            if occasion:
                facts.append("مناسب " + OCCASION_DESCRIPTIONS[occasion])
                fits_occasion.setdefault(occasion, []).append(product.name)
            elif len(products) > 1:
                # Choosing among several for one occasion: each one's recorded occasion is the answer.
                # "ومناسبته للشغل والمكتب محتاجة تأكيد" for Sauvage and Eros read as no answer (simulation
                # S08 round 2 full 4, t2).
                from products.occasions import OCCASIONS, occasion_groups
                recorded = [c for c in OCCASIONS if c in occasion_groups(product.occasion or "")]
                if recorded:
                    facts.append("مناسبته المسجلة " + " و".join(OCCASION_DESCRIPTIONS[c] for c in recorded))
            else:
                # Asked about an occasion the perfume is not recorded for, the recorded one is the
                # honest answer; "مناسبته محتاجة تأكيد" read as if nothing were known (S06 replay t5).
                from products.occasions import OCCASIONS, occasion_groups
                recorded = [c for c in OCCASIONS if c in occasion_groups(product.occasion or "")]
                asked = " و".join(OCCASION_DESCRIPTIONS[c] for c in requested if c in OCCASION_DESCRIPTIONS)
                if recorded:
                    # The store rule stands (an unrecorded use needs confirmation), and the recorded
                    # use is still worth saying.
                    facts.append("مناسبته المسجلة " + " و".join(OCCASION_DESCRIPTIONS[c] for c in recorded)
                                 + (f"، ومناسبته {asked} محتاجة تأكيد" if asked else ""))
                else:
                    facts.append("مناسبته محتاجة تأكيد")
        if re.search(r"موسم|صيف|شتا|season", text, re.I) and product.season:
            facts.append("موسمه " + product.season)
        if facts:
            lines.append(product.name + ": " + ". ".join(facts) + ".")
        elif description_required:
            lines.append(product.name + ".")
    if len(products) > 1 and len(fits_occasion) == 1:
        # "أنهي فيهم أنسب للشغل؟" asks for the one that fits; it opens the answer (S08 round 2 full 4, t2).
        occasion, names = next(iter(fits_occasion.items()))
        if len(names) < len(products):
            lines.insert(0, f"الأنسب {OCCASION_DESCRIPTIONS[occasion]}: " + " و".join(names) + ".")
    return lines


def fallback_price_reply(context, products, recommendation_intent=None):
    """Use catalogue facts for requested detail after an unsuccessful grounding retry."""
    evidence = quote_evidence(context)
    lines = requested_product_details(products, evidence.get("request", ""),
                                      preferred_occasion=(recommendation_intent or {}).get("occasion"))
    clean = {key: value for key, value in evidence.items() if key not in ("sections", "ambiguous_prose")}
    clean["append"] = True
    context += "\n" + QUOTE_MARKER + json.dumps(clean, ensure_ascii=False)
    if recommendation_intent is not None:
        from .ai.recommendation import recommendation_fit
        lines = [recommendation_fit(products, recommendation_intent, message=evidence.get("request", ""))]
    return compose_price_reply("\n".join(lines), context)
