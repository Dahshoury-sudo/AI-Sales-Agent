"""Choose catalog IDs from direct label matches and explain the selected perfumes."""

import json
import re

from products.models import Product
from products.occasions import OCCASIONS, occasion_groups
from .prompts import get_system_prompt
from ..product_formatting import (
    append_quotation, format_products,
    monetary_claims, product_claim_facts, product_quotes, quote_context, render_quotes,
)
from ..reply_sanitizer import product_claim_issues
from ..performance_policy import EXCELLENT, STRONG, governed_reply, selection_preferences
from ..search_service import product_matches_request
from ..sales import brand_response, described, naming, repetition
from ..sales.scent_preferences import normalize_scent_intent
from ..sales.scent_matching import fit_key, match_facts, order_candidates
from ..sales.scent_selection import combined_budget_quotes, nearest_selection
from ..sales.value import as_budget, eligible_variants
from ..scent_description import describe_scent, selected_tags
from ..static_faq_service import normalize_arabic


def no_match_reply(search=None):
    key = (search or {}).get("relax_key")
    questions = {
        "max_price": "مش لاقي اختيار بنفس طابع الريحة في الميزانية دي. تحب تحدد ميزانية تانية؟",
        "brand": "مش لاقي اختيار بنفس طابع الريحة من البراند المطلوب. تحب نشوف براند تاني؟",
    }
    if key in questions:
        context = f"{described.PENDING_RELAX_MARKER} {key}=offered\n" if key == "brand" else "NO_BUDGET_MATCH"
        return questions[key], context
    reason = (search or {}).get("no_match_reason")
    blockers = {
        "requested_volume": "الحجم المطلوب مش متاح مع طابع الريحة ده. تحب أنهي حجم تاني؟",
        "bottle_type": "نوع الزجاجة المطلوب مش متاح مع طابع الريحة ده. تحب أنهي نوع زجاجة تاني؟",
        "gender": "مش لاقي اختيار بنفس طابع الريحة للنوع اللي طلبته. فيه طابع ريحة تاني بتحبه؟",
        "perfume_type": "مش لاقي اختيار بنفس طابع الريحة من الفئة المطلوبة. تحب أنهي فئة تانية؟",
        "notes": "مش لاقي اختيار بنفس طابع الريحة بالمكونات اللي طلبتها. تحب تغيّر أنهي مكوّن مطلوب؟",
        "purchase_quantity": "مش متاح العدد المطلوب من الاختيارات المناسبة. تحب كام زجاجة بدل كده؟",
        "requirements": "مش لاقي اختيار متاح بنفس طابع الريحة بالمواصفات اللي حددتها. تحب تغيّر إيه في الطلب؟",
    }
    if reason in blockers:
        return blockers[reason], "NO_ELIGIBLE_SCENT_MATCH"
    return "مش لاقي عطر متاح بنفس طابع الريحة المطلوب. فيه طابع ريحة تاني بتحبه؟", "NO_SCENT_MATCH"


def _record(product, intent):
    return {
        "id": product.pk, "name": product.name, "brand": product.brand.name,
        "scent_tags": product.scent_tags, "gender": product.gender,
        "match": match_facts(product, intent),
        "description_tags": selected_tags(product.scent_tags),
        "scent_description": describe_scent(product.scent_tags, product.occasion,
                                            preferred_occasion=intent.get("occasion")),
        "occasion": product.occasion, "season": product.season,
        "occasion_categories": [category for category in OCCASIONS if category in occasion_groups(product.occasion)],
        "longevity": EXCELLENT, "projection": STRONG,
        "notes": [product.top_notes, product.middle_notes, product.base_notes],
        "variants": [{"volume": v.volume, "bottle_type": v.bottle_type, "price": str(v.price)}
                     for v in eligible_variants(product, intent) if v.volume > 0],
    }


def _current_candidates(candidates, intent, search, store):
    current = {p.pk: p for p in Product.objects.filter(store=store, pk__in=[p.pk for p in candidates])
               .select_related("brand", "store").prefetch_related("variants")}
    reference = search.get("reference_product")
    eligible = [current[p.pk] for p in candidates if p.pk in current and product_matches_request(
        current[p.pk], intent, store, blocked_brands=search.get("blocked_brands", ()),
        excluded_names=search.get("excluded_names", ()), reference_id=reference.pk if reference else None,
    )]
    return order_candidates(eligible, intent, keep=search.get("keeping", ()), offered=search.get("offered", ()))


def _quotes(selected, intent, message):
    budget = as_budget(intent.get("max_price"))
    if intent.get("budget_scope") == "total":
        return combined_budget_quotes(selected, intent)
    allowed = {v.pk for p in selected for v in eligible_variants(p, intent) if v.volume > 0}
    # The merged request includes saved size and bottle choices. Re-parsing only
    # this message can mistake a reference name for packaging or lose those choices.
    quotes = [q for q in product_quotes(selected, max_price=budget) if q.variant_id in allowed]
    if {q.product_id for q in quotes} != {p.pk for p in selected}:
        raise ValueError("Every selected perfume needs an eligible quotation.")
    return quotes


def _fallback(candidates, intent, message=None, history=None, search=None):
    from ..sales.recommendation_response import _choice_limit
    limit = _choice_limit(intent)
    search = search or {}
    selected = nearest_selection(candidates, intent, limit, keep=search.get("keeping", ()),
                                 offered=search.get("offered", ()))
    from .recommendation import recommendation_fit
    return selected, recommendation_fit(selected, intent, message=message, history=history)


def _compose_selection(reply, selected, intent, message, store):
    """Keep the same selection evidence while checking the final price layout."""
    reply = brand_response.disclose_brands(reply, selected, intent, store)
    context = format_products(selected, show_prices=False, show_value_pick=False,
                              preferred_occasion=intent.get("occasion"))
    context += described.moved_past_block(intent, store)
    budget = as_budget(intent.get("max_price"))
    if budget is not None or intent.get("requested_volume") or intent.get("bottle_type"):
        quotes = _quotes(selected, intent, message)
        rendered = render_quotes(quotes, budget)
        footer = None
        if intent.get("budget_scope") == "total":
            footer = f"إجمالي الـ{len(quotes)} زجاجات: {sum(q.price for q in quotes):f} جنيه."
            rendered += "\n" + footer
        context = quote_context(context, quotes, append=True, max_price=budget, rendered=rendered,
                                request=message, footer=footer)
        from ..price_reply import compose_price_reply
        return compose_price_reply(reply, context)
    return reply, context


@governed_reply
def recommend_from_labels(message, products, history=None, alternatives=None, store=None, intent=None,
                          search=None, gender_unknown=False, repeat_hint="", *, chat):
    search = search or {}
    intent = selection_preferences(normalize_scent_intent(search.get("effective_intent") or intent))
    # Preserve the customer's saved words, but expose the recommendation policy
    # unambiguously to the model even for a legacy all-label request.
    if intent.get("scent_tags"):
        intent["scent_match"] = "any"
    intent.pop("occasion_needs_clarification", None)
    candidates = _current_candidates(list(products), intent, search, store)
    if not candidates:
        return no_match_reply(search)
    if intent.get("budget_scope") == "total" and as_budget(intent.get("max_price")) is None:
        return "ميزانيتك الإجمالية للزجاجات في حدود كام؟", "TOTAL_BUDGET_REQUIRED"
    from ..sales.recommendation_response import _choice_limit
    count = _choice_limit(intent)
    if intent.get("budget_scope") == "total":
        minimum_prices = sorted(min(v.price for v in eligible_variants(p, intent) if v.volume > 0)
                                for p in candidates)
        if len(minimum_prices) < count:
            return no_match_reply({**search, "no_match_reason": "purchase_quantity"})
        if sum(minimum_prices[:count]) > as_budget(intent.get("max_price")):
            return no_match_reply({**search, "relax_key": "max_price"})
    instructions = (
        "\nFor this recommendation return ONLY JSON: {\"product_ids\": [integer IDs], \"reply\": \"Egyptian Arabic\"}. "
        f"Choose between one and {count} perfumes from eligible_products; use their exact catalog names in reply. "
        "For a total purchase budget choose exactly the purchase_quantity of bottles within that combined budget. "
        "One shared requested scent tag is sufficient; never require every requested tag, even for an AND/all request. "
        "eligible_products is ordered by nearest fit: matching season FIRST, then more shared scent tags, then matching occasion. "
        "Season and occasion differences or missing values do not disqualify a perfume. "
        "Choose the nearest feasible options in that order; nearest_product_ids is a feasible default selection. "
        "Do not skip a nearer feasible perfume for a cheaper one, a preferred brand, or an earlier suggestion. "
        "Within equal fits, use preferred brands and conversation context. "
        "Recommend directly even when the match is partial; do not say nothing fits or ask to relax scent, season, or occasion. "
        "Describe actual catalog facts, without claiming a different season or occasion matches the request. "
        "Describe every selected perfume in natural Egyptian Arabic using all its description_tags "
        "(the first three saved scent_tags), without appending an occasion or usage claim. "
        "If the customer explicitly excludes a scent description, omit it and keep the other requested facts. "
        "The supplied scent_description demonstrates the simple wording; no extra descriptors or fancy phrases. "
        "Ordinary prose is one or two short sentences; honor requests for more choices or complete details. "
        "No introductory recap, decorative bullets, emoji, repeated performance descriptions, or obligatory closing question. "
        "Ask at most one useful question only when the current request needs missing information. "
        "If presenting two, give a short difference only if it helps the customer choose. "
        "scent_tags are the owner's descriptions of scent character. They do not prove ingredients, performance, or authenticity. "
        "Mention notes only when the current customer explicitly asks about notes or ingredients. "
        "An ordinary smell question does not ask for notes; never infer character from notes. "
        "Every store perfume has excellent longevity and projection under the shared store policy. "
        "Performance preferences do not distinguish candidates, establish a quiet projection, or guarantee a requested duration. "
        "A shared scent style is not evidence that perfumes smell closely alike. Do not claim close similarity or a similarity percentage. "
        "Never invent popularity, guaranteed appeal, performance differences, product names or facts. "
        "Mention occasion suitability only when the current customer explicitly asks about it for that perfume, "
        "using its occasion_categories; unrecorded suitability needs confirmation. "
        "A request to recommend something for work is a selection preference, not permission to describe occasions. "
        "Saved occasion preferences and earlier questions do not authorize occasion wording. "
        "Respect preferred brands where possible and identify a different brand when choosing it. "
        "Use previous offers and current discussion for continuity; when asked for other choices, prefer an unoffered match. "
        "Do not repeat earlier sales sentences, ask for an occasion, or ask for a scent preference already supplied. "
        "The recommendation flow requires a customer gender choice before selection. "
        "Honor that choice, do not infer it from perfume names, and do not ask for it again. "
        "Do NOT write prices, bottle volumes, or totals in reply: code will append actual eligible quotations. "
        "Write naturally and briefly, with no internal IDs, scoring, labels about system behavior, or premature checkout request. "
        "Performance is shared by the choices; mention it once only if relevant to the current request."
    )
    reference = search.get("reference_product")
    payload = {
        "message": message, "preferences": intent, "eligible_products": [_record(p, intent) for p in candidates],
        "previously_offered": search.get("offered", []), "under_discussion": search.get("keeping", []),
        "no_longer_matching": search.get("dropped", {}),
        "gender_unknown": gender_unknown,
        "reference": {"name": reference.name, "scent_tags": reference.scent_tags} if reference else None,
        "nearest_product_ids": [p.pk for p in nearest_selection(
            candidates, intent, count, keep=search.get("keeping", ()), offered=search.get("offered", ()))],
    }
    messages = [{"role": "system", "content": get_system_prompt(store) + instructions}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")) + (repeat_hint or "")})
    catalogue_names = list(Product.objects.filter(store=store).values_list("name", flat=True))
    selected, reply = [], ""
    for attempt in range(2):
        try:
            response = chat(messages, profile="converse", response_format={"type": "json_object"})
            data = json.loads(response)
            ids = data.get("product_ids") if isinstance(data, dict) else None
            if (not isinstance(ids, list) or not 1 <= len(ids) <= count
                    or any(type(pk) is not int for pk in ids) or len(ids) != len(set(ids))):
                raise ValueError("Return a nonempty list of distinct eligible integer product IDs.")
            available = {p.pk: p for p in _current_candidates(candidates, intent, search, store)}
            if any(pk not in available for pk in ids):
                raise ValueError("Some chosen IDs are not currently eligible; choose only available matching products.")
            proposed = [available[pk] for pk in ids]
            closest = nearest_selection(list(available.values()), intent, len(proposed),
                                        keep=search.get("keeping", ()), offered=search.get("offered", ()))
            if not closest or [fit_key(p, intent) for p in proposed] != [fit_key(p, intent) for p in closest]:
                raise ValueError("Choose the nearest feasible perfumes in order: season, shared scent tags, then occasion.")
            draft = data.get("reply")
            if not isinstance(draft, str) or not draft.strip():
                raise ValueError("Write a brief explanation naming the selected perfumes.")
            chosen_names = {p.name for p in proposed}
            mentioned = set(naming.names_in(draft, catalogue_names))
            allowed_names = chosen_names | ({reference.name} if reference else set())
            if not chosen_names <= mentioned or mentioned - allowed_names:
                raise ValueError("Name every selected perfume and do not offer another perfume in the reply.")
            named_order = naming.names_in(draft, [p.name for p in proposed])
            by_name = {p.name: p for p in proposed}
            if [fit_key(by_name[name], intent) for name in named_order] != [fit_key(p, intent) for p in closest]:
                raise ValueError("Present the nearest perfume first, followed by the next nearest choice.")
            text = normalize_arabic(draft)
            if re.search(r"(?:مش لاقي|مفيش|لا يوجد|لم اجد).{0,90}(?:ترشيح|يطابق|يناسب|الشروط)|"
                         r"(?:نغير|تغير|نوسع|نشيل|نخفف).{0,40}(?:المناسبه|الموسم|الشروط|طابع الريحه)|"
                         r"(?:no|cannot find|can't find).{0,40}(?:match|fits)|"
                         r"(?:relax|change).{0,40}(?:occasion|season|scent|preferences)", text):
                raise ValueError("An eligible partial match exists. Recommend it directly without a no-match or relaxation message.")
            issues = product_claim_issues(draft, product_claim_facts(proposed), message=message,
                                          history=history, preferred_occasion=intent.get("occasion"),
                                          description_required=True)
            if monetary_claims(draft):
                issues.append("Do not write prices or totals; quotations are added automatically.")
            from ..sales.similarity import claims_unsupported_similarity
            if claims_unsupported_similarity(draft):
                issues.append("Shared scent labels support the same style, not a claim of close similarity.")
            if reference and reference.name in mentioned:
                from ..sales.recommendation_response import response_issues
                issues.extend(response_issues(draft, proposed, intent, search))
            issues.extend(brand_response.response_issues(draft, proposed, intent, search, store))
            repeats = repetition.repeated_sentences(draft, history, store=store) + repetition.repeated_frames(draft, history, store=store)
            if issues or repeats:
                raise ValueError("; ".join(issues) if issues else repetition.retry_hint(repeats))
            _quotes(proposed, intent, message)
            composed, composed_context = _compose_selection(draft, proposed, intent, message, store)
            from ..price_reply import composition_issues
            layout_issues = composition_issues(composed, composed_context)
            if layout_issues:
                raise ValueError("; ".join(layout_issues))
            selected, reply = proposed, draft.strip()
            break
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            messages.append({"role": "user", "content": "Correct the JSON selection and reply: " + str(exc)})
        except Exception:
            messages.append({"role": "user", "content": "Return a valid JSON selection using the provided catalog facts."})
    if not selected:
        candidates = _current_candidates(candidates, intent, search, store)
        if not candidates:
            return no_match_reply(search)
        selected, reply = _fallback(candidates, intent, message, history, search)
        if not selected:
            return no_match_reply({**search, "relax_key": "max_price"})
    try:
        quotes = _quotes(selected, intent, message)
    except ValueError:
        return no_match_reply(search)
    return _compose_selection(reply, selected, intent, message, store)
