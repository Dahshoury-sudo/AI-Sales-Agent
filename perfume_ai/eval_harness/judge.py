# -*- coding: utf-8 -*-
"""LLM judge for the dimensions code cannot check.

Deterministic checks proved the factual claims. What they cannot see is whether the
recommendation was actually *suitable*, whether a lookalike really smells alike, and
whether the turn moved a sale forward. That is what this grades.

Two design decisions matter for trustworthiness:

  * The judge is handed the real database rows for every perfume the reply named, plus
    the internal pipeline state (extracted intent, shortlist, ranking reasons, sales
    stage). It grades against data, not vibes.
  * The rubric explicitly separates factual correctness from sales quality and forbids
    rewarding fluent prose. Without that a judge marks a confident wrong answer highly.
"""

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
django.setup()

from django.db import connection  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_print_lock = threading.Lock()

RUBRIC = """
You are grading an Arabic-speaking perfume sales agent for an Egyptian store. You are
simultaneously a senior QA engineer, a conversation designer, and a perfume sales manager
with 10+ years on the floor.

Score each dimension 0-10. Be brutally honest and calibrated:
  0-3  = broken / actively harmful to the sale or to trust
  4-6  = mediocre, a real salesperson would not do this
  7-8  = solid, what a competent salesperson does
  9-10 = excellent, what a top salesperson does

HARD RULES for grading:
1. Do NOT reward fluent or confident prose. A well-written wrong answer scores LOW.
2. Separate factual correctness from sales quality. Score them in their own dimensions.
3. Shared individual notes are NOT evidence of similarity. Two perfumes are similar only
   if their overall scent character / DNA matches. Sauvage (fresh ambroxan) and a warm
   amber-spice oriental are NOT similar just because both list vanilla and bergamot.
4. A claim is only supported if it appears in the DATABASE FACTS **or the
   STORE-CONFIGURED FACTS** given below, subject to the performance rule 4d.
   Any season, occasion or note not in those blocks is INVENTED, even if it sounds right.
   But do NOT call a claim invented merely because it is absent from DATABASE FACTS —
   check STORE-CONFIGURED FACTS too. Oil ratios, available bottle sizes, the similarity
   percentage, delivery times, payment terms and the store branch all live there, and
   relaying them is correct behaviour. Read BOTH blocks in full before calling anything
   invented; scoring a supported claim as fabricated is as wrong as missing a real one.
4b. ABSENCE IN THE SIZE LIST IS EVIDENCE, NOT A GAP. The `sizes:` field lists every bottle
   that exists. If it shows no `original` bottle, then "مش متوفر منه زجاجة اوريجنال" is
   TRUE and is the wording the store dictates verbatim — do NOT score it as an unsupported
   claim. The same holds for a volume that is simply not listed. Only a claim that
   CONTRADICTS the listed sizes is a product_accuracy failure.
4c. ABSENCE FROM THE CATALOGUE IS ALSO EVIDENCE. Before any such reply the agent searches
   the store's entire active catalogue for the name the customer typed, so "العطر ده مش
   عندنا" about a perfume that appears in NEITHER facts block is TRUE and is the REQUIRED
   answer — do not score it as an invented claim, a guess, or a trust failure. Three things
   ARE failures on that turn:
   (a) Denying a perfume that DOES appear in DATABASE FACTS as active with a sellable
       bottle. This is the worst error in this system: product_accuracy and trust score 0-1.
   (b) Not stopping at the denial. The store owner's rule: say it is not available and
       stop. The denial alone is the COMPLETE and CORRECT reply — do not score it down as a
       dead end or a missed sale. Naming, recommending or pricing any other perfume in that
       reply, gesturing at "عطور تانية حلوة", or closing with "تحب أرشحلك حاجة تانية؟" is the
       failure: sales_effectiveness scores 0-3. Recommending AFTER the customer asks for an
       alternative or something similar is correct and is not this failure.
   (c) Promising to look it up and come back — "لحظة أتأكدلك منه", "هسأل وأرد عليك",
       "هشوفه لك". Nothing in this system looks a perfume up between two messages of a chat
       and nobody follows up, so the promise is empty and the customer waits for an answer
       that never arrives: trust and confidence_calibration score 0-2. It is honest in
       exactly one place — a question about store policy, delivery or a price that the store
       OWNER would answer — never about whether a perfume exists.
4d. Every stocked perfume has excellent longevity and strong projection. This
    overrides historical performance measurements and applies equally to blank records.
    Default wording: "ثباته ممتاز وفوحانه قوي." Only when the CURRENT customer message
    explicitly asks for longevity duration, including a contextual follow-up such as
    "قولي رقم", answer "ثباته أكتر من 8 ساعات." A prior duration question does not
    authorize unsolicited hours later. Projection stays qualitative even if the customer
    asks for hours or metres. Do not guarantee 12/24/48 hours or any longer requested
    duration. Never filter, rank, compare superiority, or justify price by performance.
    Light/heavy scent character is independent of projection. Unknown external perfumes
    do not receive store performance claims. Acknowledge past complaints without denying
    the customer's experience or calling an imposed policy the reason for the answer.
5. The customer's LATEST explicit preference outranks anything they said earlier. If a
   reply follows a stale preference over a fresh one, Memory scores 0-2.
6. A good salesperson does NOT always sell. If the customer signalled low intent or said
   they would think about it, pushing scores LOW on Sales and Trust.
7. Asking a question the customer already answered, or asking for information the agent
   could have derived from its own data, is a real failure — not politeness.
8. If the reply spent a whole turn on a bureaucratic question while the customer had
   already given rich information, Intent and Sales score LOW.
9. If a stated constraint was silently dropped and the reply presents the result as
   though it met the request, Trust scores LOW.
9b. An ordinary positive brand request is a PREFERENCE, not an exclusive constraint.
    A better-fitting perfume from another brand should lead if its actual brand and
    supported advantage are explained. Do not penalize this as disobeying the customer.
    Explicit brand-only requests (brand_strict=true) and excluded brands remain binding.
    Offering another brand does not change the customer's saved preferred brand.
10. Score only what the agent actually did. Do not credit intentions.

Dimensions:
- intent_understanding: did it grasp what the customer actually wants?
- constraint_extraction: were budget/gender/season/occasion/notes/
  similarity-target/negative-preferences/quantity captured correctly? Check the extracted
  intent shown to you against what the customer literally said, including polarity
  (wanting heavy vs avoiding heavy is a total inversion). Performance wishes should be
  answered under rule 4d, not used as selection constraints.
- memory: did it retain useful info, drop stale info, and let new info override old?
- recommendation_quality: was the recommended perfume genuinely suitable given the
  database rows?
- similarity_accuracy: if a lookalike was requested, are similarity claims supported by
  overall character? Give full credit to direct alternatives with supported qualities
  and relevant differences, without fabricated similarity. Do not require a no-close-match
  declaration; lead with one or two useful products. Score 0 if an unrelated perfume was
  presented as a lookalike. Score null if similarity was not requested.
- product_accuracy: is every factual claim supported by the supplied facts and rule 4d?
- confidence_calibration: did it distinguish known fact / likely inference / guess /
  unknown? Presenting a guess as fact scores 0-2.
- objection_handling: understood, acknowledged, addressed, reduced risk, continued
  naturally? Score null if no objection.
- sales_effectiveness: did the turn move the customer closer to a purchase (without
  pushing)?
- closing_timing: closed at the right moment? Premature closing scores LOW. Score null
  if no close attempted and none was warranted.
- human_feel: friendly, everyday Egyptian Arabic. Ordinary replies should be 1–2 short
  sentences: answer directly, without repeated introductions, forced honorifics, decorative
  emoji, or an unnecessary closing question. Recommendations may use one short reason per
  perfume. Complete order summaries, requested details, and necessary price options can be
  longer. Judge relevance and natural wording as well as length; never reward omitted facts.
- trust: no fake guarantees, no manipulation, no unsupported statistics, no false
  urgency, no exaggerated promises.

Return ONLY valid JSON:
{
  "scores": {
    "intent_understanding": 0-10, "constraint_extraction": 0-10, "memory": 0-10,
    "recommendation_quality": 0-10, "similarity_accuracy": 0-10 or null,
    "product_accuracy": 0-10, "confidence_calibration": 0-10,
    "objection_handling": 0-10 or null, "sales_effectiveness": 0-10,
    "closing_timing": 0-10 or null, "human_feel": 0-10, "trust": 0-10
  },
  "what_worked": ["specific things done well, quoting the reply"],
  "failures": [
    {"severity": "critical|high|medium|low",
     "category": "memory|recommendation|sales|prompt|data|intent|similarity|ux|trust",
     "what": "what went wrong",
     "why_wrong": "why it is wrong, citing the database facts or the customer's words",
     "expected": "what a senior salesperson would have done"}
  ],
  "salesperson_verdict": "one or two sentences: would a sales manager accept this turn?"
}
"""


def database_facts(names):
    """Real rows for the perfumes a reply mentioned, so claims can be checked."""
    from products.models import Product, Store
    from products.services.performance_policy import EXCELLENT, STRONG

    store = Store.objects.get(name="Perfamix")
    lines = []
    for name in sorted(set(names)):
        product = Product.objects.filter(store=store, name=name).prefetch_related("variants").first()
        if not product:
            continue
        sizes = "; ".join(
            f"{'original' if v.bottle_type == 'original' else 'brand'} {v.volume}ml={v.price:.0f}EGP"
            + (f" stock={v.stock}" if v.bottle_type == "original" else "")
            for v in product.variants.all()
        )
        # The variant list is the whole catalogue record. Without saying so, a perfume with no
        # original variant read as "unknown" beside the store fact that some perfumes have original
        # bottles, and the seller's correct "مش متوفر منه زجاجة اوريجنال" was graded critical in three
        # gradings of one transcript (simulation S07 round 2 full 3, t4). The seller's own product row
        # states the same fact.
        if not any(v.bottle_type == "original" and (v.stock or 0) > 0 for v in product.variants.all()):
            sizes += " | original bottle: NONE available for this perfume"
        lines.append(
            f"- {product.name} | brand={product.brand.name} | gender={product.gender} | "
            f"type={product.perfume_type} | season={product.season or 'NOT RECORDED'} | "
            # The store's scent descriptions come from these tags (first three, in order), so a
            # judge without them reads every correct description as invented.
            f"scent_tags={','.join(product.scent_tags or []) or 'NOT RECORDED'} | "
            f"occasion={product.occasion or 'NOT RECORDED'} | "
            f"longevity={EXCELLENT} | "
            f"projection={STRONG} | "
            f"top={product.top_notes or 'NOT RECORDED'} | mid={product.middle_notes or 'NOT RECORDED'} | "
            f"base={product.base_notes or 'NOT RECORDED'} | sizes: {sizes}"
        )
    return "\n".join(lines) or "(no catalogue perfume was named in this conversation)"


def store_facts():
    """Claims the store itself configured, which the agent is entitled to relay.

    Without this the judge marked legitimate answers as invented: the oil ratios, the
    available bottle sizes and the similarity figure all live in StoreSettings and
    StaticFAQ, not on a product row, so a facts block built only from named perfumes made
    them look fabricated. Three of seven "critical" findings in the first pass were this
    mistake.
    """
    from products.models import Store
    from products.services.performance_policy import catalog_text

    store = Store.objects.get(name="Perfamix")
    parts = []
    try:
        settings_row = store.settings
        for label, value in (
            ("business_facts", settings_row.business_facts),
            ("promotions (sole source for current offers)", settings_row.promotions),
            ("custom instructions", settings_row.system_prompt),
            ("payment instructions", settings_row.payment_instructions),
        ):
            if (value or "").strip():
                parts.append(f"[{label}]\n{catalog_text(value.strip())}")
    except Exception:
        pass
    for faq in store.static_faqs.filter(is_active=True):
        parts.append(f"[StaticFAQ: {faq.question}]\n{catalog_text(faq.answer.strip())}")
    # The seller prompt carries this line, computed from every active product row (genders,
    # perfume types, price range per bottle). Without it the judge marked the store's real
    # assortment as invented (simulation S18 attempt 3, t1).
    from products.services.ai.prompts import _catalogue_overview
    overview = _catalogue_overview(store).strip()
    if overview:
        parts.append(f"[catalogue overview computed from all active product rows]\n{overview}")
    return "\n\n".join(parts) or "(none configured)"


def build_prompt(record, findings):
    from products.models import Product, Store

    store = Store.objects.get(name="Perfamix")
    catalogue_names = set(
        Product.objects.filter(store=store, is_active=True).values_list("name", flat=True)
    )

    mentioned = set()
    for turn in record["turns"]:
        reply = turn["reply"] or ""
        for name in catalogue_names:
            if name.lower() in reply.lower():
                mentioned.add(name)
        for name in (turn.get("search") or {}).get("matched") or []:
            mentioned.add(name)

    parts = [
        f"SCENARIO {record['id']} — category: {record['category']} — persona: {record['persona']}",
        "",
        "WHAT A SENIOR PERFUME SALESPERSON WOULD CONSIDER CORRECT HANDLING:",
        record["probe"],
        "",
        "═══ CONVERSATION ═══",
    ]
    for turn in record["turns"]:
        intent = {k: v for k, v in (turn.get("merged_intent") or {}).items()
                  if v not in (None, [], False, "")}
        search = turn.get("search") or {}
        parts.append(f"\n--- TURN {turn['n']} ---")
        parts.append(f"CUSTOMER: {turn['user']}")
        parts.append(f"[internal] classified_as={turn.get('classification')} "
                     f"sales_stage={turn.get('stage')} objection={turn.get('objection')}")
        parts.append(f"[internal] extracted_intent={json.dumps(intent, ensure_ascii=False)}")
        if search.get("matched") is not None:
            parts.append(f"[internal] shortlist_shown_to_model={search.get('matched')}")
            parts.append(f"[internal] similarity_summary={json.dumps(search.get('similarity'), ensure_ascii=False)}")
            if search.get("reasons"):
                parts.append(f"[internal] ranking_evidence="
                             f"{json.dumps(search['reasons'], ensure_ascii=False)[:1200]}")
        parts.append(f"AGENT: {turn['reply']}")

    parts += [
        "",
        "═══ DATABASE FACTS (the ONLY supported truth about these perfumes) ═══",
        database_facts(mentioned),
        "",
        "═══ STORE-CONFIGURED FACTS (the agent MAY relay any of these verbatim; they are "
        "supported, NOT invented) ═══",
        store_facts(),
        "",
        "═══ DETERMINISTIC FINDINGS ALREADY PROVEN BY CODE ═══",
        json.dumps(findings, ensure_ascii=False, indent=1) if findings else "(none)",
        "",
        f"[context] Final saved preferences after the conversation: "
        f"{json.dumps(record.get('final_preferences'), ensure_ascii=False)}",
    ]
    return "\n".join(parts)


def judge_one(record, findings):
    from products.services.ai.client import chat

    try:
        response = chat(
            [{"role": "system", "content": RUBRIC},
             {"role": "user", "content": build_prompt(record, findings)}],
            profile="reason",
            response_format={"type": "json_object"},
        )
        verdict = json.loads(response)
    except Exception as exc:
        verdict = {"error": f"{type(exc).__name__}: {exc}"}
    verdict["id"] = record["id"]
    verdict["category"] = record["category"]
    verdict["persona"] = record["persona"]
    return verdict


def _worker(record, findings):
    try:
        return judge_one(record, findings)
    finally:
        connection.close()


# ───────────────────── adaptive customer simulations (scenarios_customer_20) ─────────────────────
SIMULATION_RUBRIC = """
You grade a complete chat between a customer and an Egyptian perfume store's sales assistant
(a bot). You are a strict senior sales manager and QA reviewer. Grade ONLY the assistant.

Judge every assistant turn using ONLY what the customer had said up to and including that turn.
Never fault a turn for not knowing something the customer revealed later.

What good looks like:
- Chooses the right next move for the current message: answer, clarify, recommend, compare,
  address an objection, continue checkout, or finish. Answers the customer's current question
  before advancing the sale; never forces a fixed sales sequence.
- Asks a question only when the answer would materially change the recommendation or is needed
  to complete the request; one focused question at a time. Never re-asks something already
  supplied. A complete answer may end WITHOUT a question — never penalise the absence of a
  question. When the customer says "مش عارف", offers accessible choices or an example instead of
  repeating the same question.
- Remembers preferences, budgets, exclusions, rejected suggestions, selected products and the
  pending question, including after many turns; the latest explicit preference wins; corrections
  are accepted immediately; preferences are scoped to the right recipient (self vs gift/another
  person). Short replies ("التاني", "آه", a size) are interpreted in context; a genuinely ambiguous
  reference is clarified rather than guessed.
- Relaxed, natural Egyptian Arabic matching the customer's tone; brief by default. No repeated
  greetings, constant "يا فندم", generic praise, unnecessary recaps, emoji spam, formal
  customer-service language, or identical closing lines. No fake personal experience, no claim to
  be human.
- Proportionate to the situation: direct answers to direct price/availability questions; space for
  browsers; only missing checkout details for ready buyers; brief natural acknowledgement of
  praise (praise is NOT order consent); a concrete correction when the assistant itself made a
  mistake; acknowledgement plus the supported handoff for service complaints; calm, non-arguing
  handling of anger/insults (a brief respectful boundary only for persistent abuse); jokes and
  sarcasm read in context without forced humour; adapts when mood changes. No repetitive apologies
  or canned sympathy. No promises the store system cannot keep (refunds, discounts, order-status
  updates, follow-ups, call-backs). Transferring to the human team IS a supported action: once the
  assistant confirms a transfer, "the team will follow up" is true.
- Usually one or two suitable options; explains the relevant difference using supported facts.
  Honors budgets and exclusions; if nothing meets the constraints, says what specifically does not
  fit and asks which constraint to relax. Order is placed only after a summary with total and an
  explicit confirmation; a price question, praise or a size choice is not consent.

Store policies the assistant MUST follow (these are correct behaviour, not failures):
- Every liquid is تركيب (inspired); original bottles exist for some perfumes; only the store's
  configured similarity figure may be quoted.
- Performance: every stocked perfume is described as excellent longevity / strong projection;
  "أكتر من 8 ساعات" only when the CURRENT message asks for duration; never 12/24/48-hour guarantees,
  never ranking by performance.
- Scent descriptions come from the perfume's first three stored scent tags; occasion suitability
  is mentioned only when the customer explicitly asks about it; notes only when explicitly asked,
  and only stored notes.
- A size within 20% over an approximate budget may be offered with the fixed wording
  "أعلى حاجة بسيطة من ميزانيتك" without stating the difference.
- The store requires a men's/women's choice before recommending; asking it ONCE when it is
  genuinely unknown is acceptable.
- The assistant cannot apply offers, bundles, coupons or discounts; in this store system those are
  fulfilled by the human team, so explaining that and offering a transfer is supported behaviour
  (repeating the same transfer offer every turn is not). Configured promotions may be quoted.
- The store owner has approved the order-confirmation sentence "فريق المبيعات هيتواصل معاك قريب":
  once an order is confirmed, telling the customer the sales team will contact them about it is
  true store behaviour, not an unsupported promise.
- A claim is supported only if it appears in DATABASE FACTS or STORE-CONFIGURED FACTS. A perfume
  absent from DATABASE FACTS that the assistant says is not stocked was searched in the whole
  catalogue: the denial is correct and is the whole reply. The store owner's rule is to say it is
  not available and stop: an alternative named or offered in that reply, unasked, is a defect, and a
  bare denial is not a dead end. Recommending once the customer asks for an alternative is correct.

Severity:
- critical: invented price/product/fact, wrong total, order created without consent, false denial
  of a stocked perfume, false promise of refund/discount/status.
- high: violated a stated exclusion or budget, forgot or overrode a confirmed preference,
  re-asked supplied information, wrong reference resolution, mishandled complaint/handoff,
  pushed a sale on a closing/browsing customer, repeated the same question after "مش عارف".
- medium: an unnecessary or badly timed question, a scripted/irrelevant reply, missed
  acknowledgement, too long, unnatural register.
- low: minor style issues.

Do not reward fluent prose. A friendly reply with an invented fact or a wrong order FAILS.
The deterministic findings listed were produced by code; confirm or dismiss each one explicitly in
"deterministic_review" (dismiss only with a concrete reason, e.g. the number was the customer's
own phone number).

Return ONLY JSON:
{
  "scores": {"understanding_accuracy": 0-10, "memory": 0-10, "question_quality": 0-10,
             "knows_when_to_stop": 0-10, "naturalness": 0-10, "emotional_handling": 0-10,
             "commerce_correctness": 0-10},
  "criteria": [{"criterion": "<copied>", "met": true|false|null, "evidence": "turn N: ..."}],
  "issues": [{"turn": N, "severity": "critical|high|medium|low", "category": "understanding|memory|question|stop|naturalness|emotion|commerce|accuracy",
              "expected": "...", "actual": "quote the reply", "why": "..."}],
  "deterministic_review": [{"turn": N, "code": "...", "verdict": "confirmed|dismissed", "reason": "..."}],
  "summary": "two sentences"
}
"""


def build_simulation_prompt(record, scenario):
    from products.models import Product, Store
    from products.services.sales.naming import names_in

    store = Store.objects.get(name="Perfamix")
    catalogue_names = list(Product.objects.filter(store=store, is_active=True).values_list("name", flat=True))
    mentioned = set()
    for turn in record["turns"]:
        mentioned.update(names_in(f"{turn['user']}\n{turn['reply'] or ''}", catalogue_names))
        mentioned.update((turn.get("search") or {}).get("matched") or [])
    parts = [
        f"SCENARIO {record['id']}: {scenario['title']}",
        "SUCCESS CRITERIA (judge each one, using only what was revealed by then):",
        *[f"- {item}" for item in scenario["success"]],
        "", "═══ CONVERSATION ═══",
    ]
    for turn in record["turns"]:
        intent = {k: v for k, v in (turn.get("merged_intent") or {}).items()
                  if v not in (None, [], False, "", {}) and k not in ("scent_evidence",)}
        parts.append(f"\n--- TURN {turn['n']} ---")
        parts.append(f"CUSTOMER: {turn['user']}")
        parts.append(f"[internal] route={turn.get('classification')} stage={turn.get('stage')} "
                     f"saved_preferences={json.dumps(turn.get('saved_preferences') or {}, ensure_ascii=False)[:600]}")
        if intent:
            parts.append(f"[internal] merged_intent={json.dumps(intent, ensure_ascii=False)[:600]}")
        if (turn.get("search") or {}).get("matched") is not None:
            parts.append(f"[internal] shortlist={turn['search']['matched'][:8]}")
        if turn["cart"]["items"] or turn["orders"]:
            # `Order.total_price` is the items total; shipping is charged per the store's shipping
            # policy, as the summary says ("(من غير الشحن)"). Labelled so it is not read as the bill.
            orders = [{("items_total_excluding_shipping" if key == "total_price" else key): value
                       for key, value in order.items()} for order in turn["orders"]]
            parts.append(f"[internal] cart={json.dumps(turn['cart']['items'], ensure_ascii=False)} "
                         f"orders={json.dumps(orders, ensure_ascii=False)}")
        if turn.get("needs_human_after"):
            parts.append("[internal] conversation is now handed off to the human team")
        parts.append(f"ASSISTANT: {turn['reply'] or '(no reply)'}")
        if turn.get("findings"):
            parts.append(f"[deterministic findings] {json.dumps(turn['findings'], ensure_ascii=False)}")
    conversation_findings = [f for f in record["findings"] if not any(
        list(f) in [list(x) for x in t.get("findings", [])] for t in record["turns"])]
    parts += [
        "", f"[conversation-level deterministic findings] {json.dumps(conversation_findings, ensure_ascii=False)}",
        "", "═══ DATABASE FACTS ═══", database_facts(mentioned),
        "", "═══ STORE-CONFIGURED FACTS ═══", store_facts(),
    ]
    return "\n".join(parts)


def parse_simulation_verdict(text):
    verdict = json.loads(text)
    if not isinstance(verdict, dict) or not isinstance(verdict.get("scores"), dict):
        raise ValueError("judge returned no scores")
    return verdict


def main():
    with open(os.path.join(HERE, "results", "runs.json"), encoding="utf-8") as handle:
        runs = json.load(handle)
    with open(os.path.join(HERE, "results", "findings.json"), encoding="utf-8") as handle:
        findings = json.load(handle)

    verdicts = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_worker, r, findings.get(r["id"], [])): r for r in runs}
        for future in as_completed(futures):
            verdict = future.result()
            verdicts.append(verdict)
            with _print_lock:
                scores = verdict.get("scores") or {}
                usable = [v for v in scores.values() if isinstance(v, (int, float))]
                mean = sum(usable) / len(usable) if usable else 0
                print(f"  [{verdict['id']}] mean={mean:.1f} "
                      f"failures={len(verdict.get('failures') or [])} "
                      f"{verdict.get('error') or ''}", flush=True)

    verdicts.sort(key=lambda v: v["id"])
    out = os.path.join(HERE, "results", "verdicts.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(verdicts, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
