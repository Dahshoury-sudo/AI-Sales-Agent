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
   STORE-CONFIGURED FACTS** given below. Any longevity in hours, projection level,
   season, occasion or note not in those blocks is INVENTED, even if it sounds right.
   But do NOT call a claim invented merely because it is absent from DATABASE FACTS —
   check STORE-CONFIGURED FACTS too. Oil ratios, available bottle sizes, the similarity
   percentage, delivery times, payment terms and the store branch all live there, and
   relaying them is correct behaviour.
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
10. Score only what the agent actually did. Do not credit intentions.

Dimensions:
- intent_understanding: did it grasp what the customer actually wants?
- constraint_extraction: were budget/gender/season/occasion/longevity/projection/notes/
  similarity-target/negative-preferences/quantity captured correctly? Check the extracted
  intent shown to you against what the customer literally said, including polarity
  (wanting heavy vs avoiding heavy is a total inversion).
- memory: did it retain useful info, drop stale info, and let new info override old?
- recommendation_quality: was the recommended perfume genuinely suitable given the
  database rows?
- similarity_accuracy: if a lookalike was requested, is the offer actually similar in
  overall character? 10 if honestly admitted no close match exists; 0 if an unrelated
  perfume was presented as a lookalike. Score null if similarity was not requested.
- product_accuracy: is every factual claim traceable to the database rows?
- confidence_calibration: did it distinguish known fact / likely inference / guess /
  unknown? Presenting a guess as fact scores 0-2.
- objection_handling: understood, acknowledged, addressed, reduced risk, continued
  naturally? Score null if no objection.
- sales_effectiveness: did the turn move the customer closer to a purchase (without
  pushing)?
- closing_timing: closed at the right moment? Premature closing scores LOW. Score null
  if no close attempted and none was warranted.
- human_feel: natural Egyptian, not robotic, not repetitive, not a product catalogue.
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
        fillable = product.oil_stock_grams // max(1, (50 * product.concentration_percentage) // 100)
        lines.append(
            f"- {product.name} | brand={product.brand.name} | gender={product.gender} | "
            f"type={product.perfume_type} | season={product.season or 'NOT RECORDED'} | "
            f"occasion={product.occasion or 'NOT RECORDED'} | "
            f"longevity={product.longevity or 'NOT RECORDED'} | "
            f"projection={product.projection or 'NOT RECORDED'} | "
            f"top={product.top_notes or 'NOT RECORDED'} | mid={product.middle_notes or 'NOT RECORDED'} | "
            f"base={product.base_notes or 'NOT RECORDED'} | sizes: {sizes} | "
            f"oil={product.oil_stock_grams}g conc={product.concentration_percentage}% "
            f"(50ml bottles fillable≈{fillable})"
        )
    return "\n".join(lines) or "(no catalogue perfume was named in this conversation)"


def store_facts():
    """Claims the store itself configured, which the agent is entitled to relay.

    Without this the judge marked legitimate answers as invented: the oil ratios, the
    available bottle sizes and the ~90% similarity figure all live in StoreSettings and
    StaticFAQ, not on a product row, so a facts block built only from named perfumes made
    them look fabricated. Three of seven "critical" findings in the first pass were this
    mistake.
    """
    from products.models import Store

    store = Store.objects.get(name="Perfamix")
    parts = []
    try:
        settings_row = store.settings
        for label, value in (
            ("business_facts", settings_row.business_facts),
            ("custom instructions", settings_row.system_prompt),
            ("payment instructions", settings_row.payment_instructions),
        ):
            if (value or "").strip():
                parts.append(f"[{label}]\n{value.strip()}")
    except Exception:
        pass
    for faq in store.static_faqs.filter(is_active=True):
        parts.append(f"[StaticFAQ: {faq.question}]\n{faq.answer.strip()}")
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
