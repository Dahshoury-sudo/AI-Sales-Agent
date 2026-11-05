"""Choose catalog IDs from direct label matches and explain the selected perfumes."""

import json

from products.models import Product
from products.occasions import OCCASIONS, occasion_groups
from products.scent_tags import describe_scent_tags
from .prompts import get_system_prompt
from ..product_formatting import (
    ProductQuote, append_quotation, factual_description, format_products,
    monetary_claims, product_claim_facts, product_quotes, quote_context, render_quotes,
)
from ..reply_sanitizer import product_claim_issues
from ..performance_policy import EXCELLENT, governed_reply, selection_preferences
from ..search_service import product_matches_request
from ..sales import brand_response, described, naming, repetition
from ..sales.scent_preferences import normalize_scent_intent
from ..sales.value import as_budget, eligible_variants


def no_match_reply(search=None):
    if (search or {}).get("needs_occasion"):
        from ..sales.occasion_preferences import CLARIFICATION
        return CLARIFICATION, "OCCASION_REQUIRED"
    key = (search or {}).get("relax_key")
    questions = {
        "scent_tags": "تحب نوسّع الاختيارات من غير الالتزام بطابع الريحة ده؟",
        "excluded_scent_tags": "تحب نوسّع الاختيارات ونشيل شرط استبعاد طابع الريحة؟",
        "occasion": "تحب نشوف نفس طابع الريحة من غير تقييد المناسبة؟",
        "max_price": "تحب نوسّع البحث من غير حد للميزانية؟",
        "brand": "تحب نشوف نفس المواصفات من براند تاني؟",
    }
    question = questions.get(key, "تحب تغيّر أنهي شرط في طلبك؟")
    context = f"{described.PENDING_RELAX_MARKER} {key}=offered\n" if key else "NO_SCENT_MATCH"
    return "مش لاقي ترشيح يطابق كل الشروط دي مع بعض. " + question, context


def _record(product, intent):
    return {
        "id": product.pk, "name": product.name, "brand": product.brand.name,
        "scent_tags": product.scent_tags, "gender": product.gender,
        "occasion": product.occasion, "season": product.season,
        "occasion_categories": [category for category in OCCASIONS if category in occasion_groups(product.occasion)],
        "longevity": EXCELLENT, "projection": EXCELLENT,
        "notes": [product.top_notes, product.middle_notes, product.base_notes],
        "variants": [{"volume": v.volume, "bottle_type": v.bottle_type, "price": str(v.price)}
                     for v in eligible_variants(product, intent) if v.volume > 0],
    }


def _current_candidates(candidates, intent, search, store):
    current = {p.pk: p for p in Product.objects.filter(store=store, pk__in=[p.pk for p in candidates])
               .select_related("brand", "store").prefetch_related("variants")}
    reference = search.get("reference_product")
    return [current[p.pk] for p in candidates if p.pk in current and product_matches_request(
        current[p.pk], intent, store, blocked_brands=search.get("blocked_brands", ()),
        excluded_names=search.get("excluded_names", ()), reference_id=reference.pk if reference else None,
    )]


def _quotes(selected, intent, message):
    budget = as_budget(intent.get("max_price"))
    if intent.get("budget_scope") == "total":
        count = intent.get("purchase_quantity") or 2
        if budget is None or len(selected) != count:
            raise ValueError("Choose the requested bottle count within the combined budget.")
        quotes = [ProductQuote.from_variant(min(eligible_variants(p, intent), key=lambda v: (v.price, v.pk)))
                  for p in selected]
        if sum(q.price for q in quotes) > budget:
            raise ValueError("The selected bottles exceed the combined budget.")
        return quotes
    allowed = {v.pk for p in selected for v in eligible_variants(p, intent) if v.volume > 0}
    # The merged request includes saved size and bottle choices. Re-parsing only
    # this message can mistake a reference name for packaging or lose those choices.
    quotes = [q for q in product_quotes(selected, max_price=budget) if q.variant_id in allowed]
    if {q.product_id for q in quotes} != {p.pk for p in selected}:
        raise ValueError("Every selected perfume needs an eligible quotation.")
    return quotes


def _fallback(candidates, intent):
    limit = intent.get("recommendation_count") or 2
    if intent.get("budget_scope") == "total":
        limit = intent.get("purchase_quantity") or 2
        candidates = sorted(candidates, key=lambda p: min(v.price for v in eligible_variants(p, intent)))
    limit = limit if type(limit) is int and limit > 0 else 2
    selected = candidates[:limit]
    lines = []
    for product in selected:
        profile = describe_scent_tags(product.scent_tags)
        facts = factual_description(product)
        lines.append(f"{product.name}: طابعه {profile}." + (" " + facts + "." if facts else ""))
    return selected, "\n".join(lines)


@governed_reply
def recommend_from_labels(message, products, history=None, alternatives=None, store=None, intent=None,
                          search=None, gender_unknown=False, repeat_hint="", *, chat):
    search = search or {}
    intent = selection_preferences(normalize_scent_intent(search.get("effective_intent") or intent))
    candidates = _current_candidates(list(products), intent, search, store)
    if not candidates:
        return no_match_reply(search)
    if intent.get("budget_scope") == "total" and as_budget(intent.get("max_price")) is None:
        return "ميزانيتك الإجمالية للزجاجات في حدود كام؟", "TOTAL_BUDGET_REQUIRED"
    count = intent.get("purchase_quantity") if intent.get("budget_scope") == "total" else intent.get("recommendation_count")
    count = count if type(count) is int and count > 0 else 2
    if intent.get("budget_scope") == "total":
        minimum_prices = sorted(min(v.price for v in eligible_variants(p, intent) if v.volume > 0)
                                for p in candidates)
        if len(minimum_prices) < count:
            return no_match_reply(search)
        if sum(minimum_prices[:count]) > as_budget(intent.get("max_price")):
            return no_match_reply({**search, "relax_key": "max_price"})
    instructions = (
        "\nFor this recommendation return ONLY JSON: {\"product_ids\": [integer IDs], \"reply\": \"Egyptian Arabic\"}. "
        f"Choose between one and {count} perfumes from eligible_products; use their exact catalog names in reply. "
        "For a total purchase budget choose exactly the purchase_quantity of bottles within that combined budget. "
        "Choose according to the customer's current preferences and the supplied facts. The list is in ID order, not relevance order. "
        "Explain briefly why the choices suit the stated scent style and occasion. If presenting two, help choose between them. "
        "scent_tags are the owner's descriptions of scent character. They do not prove ingredients, performance, or authenticity. "
        "Use notes for ingredient claims. Every store perfume has excellent longevity and projection under the shared store policy. "
        "Performance preferences do not distinguish candidates, establish a quiet projection, or guarantee a requested duration. "
        "A shared scent style is not evidence that perfumes smell closely alike. Do not claim close similarity or a similarity percentage. "
        "Never invent popularity, guaranteed appeal, performance differences, product names or facts. "
        "Describe occasion suitability only using each product's occasion_categories. "
        "Respect preferred brands where possible and identify a different brand when choosing it. "
        "Use previous offers and current discussion for continuity; when asked for other choices, prefer an unoffered match. "
        "Do not repeat earlier sales sentences, ask for an occasion, or ask for a scent preference already supplied. "
        "The recommendation flow requires a customer gender choice before selection. "
        "Honor that choice, do not infer it from perfume names, and do not ask for it again. "
        "Do NOT write prices, bottle volumes, or totals in reply: code will append actual eligible quotations. "
        "Write naturally and briefly, with no internal IDs, scoring, labels about system behavior, or premature checkout request."
    )
    reference = search.get("reference_product")
    payload = {
        "message": message, "preferences": intent, "eligible_products": [_record(p, intent) for p in candidates],
        "previously_offered": search.get("offered", []), "under_discussion": search.get("keeping", []),
        "no_longer_matching": search.get("dropped", {}),
        "gender_unknown": gender_unknown,
        "reference": {"name": reference.name, "scent_tags": reference.scent_tags} if reference else None,
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
            draft = data.get("reply")
            if not isinstance(draft, str) or not draft.strip():
                raise ValueError("Write a brief explanation naming the selected perfumes.")
            chosen_names = {p.name for p in proposed}
            mentioned = set(naming.names_in(draft, catalogue_names))
            allowed_names = chosen_names | ({reference.name} if reference else set())
            if not chosen_names <= mentioned or mentioned - allowed_names:
                raise ValueError("Name every selected perfume and do not offer another perfume in the reply.")
            issues = product_claim_issues(draft, product_claim_facts(proposed))
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
        selected, reply = _fallback(candidates, intent)
    try:
        quotes = _quotes(selected, intent, message)
    except ValueError:
        return no_match_reply(search)
    reply = brand_response.disclose_brands(reply, selected, intent, store)
    # Only actual selections become row evidence for references and offer history.
    context = format_products(selected, show_prices=False, show_value_pick=False)
    context += described.moved_past_block(intent, store)
    budget = as_budget(intent.get("max_price"))
    if budget is not None or intent.get("requested_volume") or intent.get("bottle_type"):
        rendered = render_quotes(quotes, budget)
        if intent.get("budget_scope") == "total":
            rendered += f"\nإجمالي الـ{len(quotes)} زجاجات: {sum(q.price for q in quotes):f} جنيه."
        context = quote_context(context, quotes, append=True, max_price=budget, rendered=rendered)
    return append_quotation(reply, context), context
