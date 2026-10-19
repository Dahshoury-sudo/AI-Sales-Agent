# Incremental perfume agent evaluation — 19 October 2026

**Overall score: 6.65/10 (previously 6.59; change +0.06).**

The combined result contains 63 scenarios: 24 newly run and graded, plus 39 unchanged transcripts and grades from [18 October](../evaluation_20261018/report.md). Only the 24 supplied scenario IDs were rerun. Successful cases outside that list generated no new live or grading requests. This is an incremental score combining two source revisions, not a fresh full-suite evaluation.

| Check | Result |
|---|---|
| Updated conversations | 24; 113 turns |
| Retained conversations | 39; transcripts and grades verified identical |
| Combined evidence | 63 conversations; 185 turns |
| Completion | No skipped cases or grades; 0 unhandled scenario errors |
| Caught name-confirmation exceptions in updated cases | 23 |
| New API requests | 339 agent + 24 judge |
| Updated subset score | 4.65 → 4.81 |
| Combined core / historical scores | 7.42 / 5.12 |
| Baseline artifacts | All original file hashes unchanged |
| Disposable database | Cleanup verified |

The live runner resumed from `artifacts/agent_evaluation/evaluation_20261018/live`, using `--rerun-ids` and a live cumulative cap of $1.24258970. The $0.25741030 previously spent on grading is also included in the final $1.50 cumulative cap.

## Updated scores

| Scenario | Previous / 10 | Updated / 10 | Change |
|---|---:|---:|---:|
| C2 | 4.11 | 8.89 | +4.78 |
| CONV726 | 4.20 | 6.30 | +2.10 |
| CONV738 | 7.70 | 9.11 | +1.41 |
| CONV997 | 6.60 | 8.00 | +1.40 |
| CONV932 | 5.10 | 6.40 | +1.30 |
| CONV836 | 2.56 | 3.56 | +1.00 |
| CONV795 | 1.11 | 2.00 | +0.89 |
| CONV1105 | 9.11 | 9.67 | +0.56 |
| M3 | 7.09 | 7.40 | +0.31 |
| G1 | 2.67 | 2.89 | +0.22 |
| CONV973 | 4.30 | 4.36 | +0.06 |
| CONV816 | 2.11 | 2.11 | +0.00 |
| CONV841 | 3.56 | 3.44 | -0.11 |
| CONV798 | 1.44 | 1.33 | -0.11 |
| CONV817 | 2.78 | 2.67 | -0.11 |
| CONV772 | 6.70 | 6.33 | -0.37 |
| P2 | 3.22 | 2.70 | -0.52 |
| CONV799 | 2.67 | 2.11 | -0.56 |
| CONV931 | 5.50 | 4.70 | -0.80 |
| CONV1075 | 4.50 | 3.67 | -0.83 |
| CONV1069 | 7.45 | 6.55 | -0.91 |
| CONV835 | 3.09 | 1.90 | -1.19 |
| CONV1005 | 6.36 | 4.91 | -1.45 |
| CONV768 | 7.60 | 4.50 | -3.10 |

## Lowest scoring updated cases

The following are the judge's assessments; the saved transcripts and verdicts contain the supporting evidence.

- **CONV798: 1.33/10.** Not acceptable. This turn fails core requirements: the agent ignored the customer's explicit answer, repeated the same request for clarification, and did not deny the named perfume or offer stocked alternatives — a senior sales manager would require retraining and a scripted correct reply immediately.
- **CONV835: 1.90/10.** Unacceptable: a senior sales manager would not accept this turn. The agent failed to follow the required immediate denial for a non-stocked perfume, created false expectations of checking, omitted offering the mandated alternative (Dior Homme Sport), and forced the customer to ask prices multiple times.
- **CONV795: 2.00/10.** Unacceptable as-is: a senior sales manager would not accept repeating clarification requests without denying unavailable SKUs and offering the stocked incense alternatives (Dior Homme Sport or Bleu de Chanel) in the same reply. This turn needs correction before continuing the sale.
- **CONV799: 2.11/10.** غير مقبول — هذا الدور لا يلبي توقعات المندوب المتمرس على الأرض. يجب أن يرفض المنتج غير الموجود بصراحة مع اعتذار قصير، ثم يعرض بدائل محددة باسمها.
- **CONV816: 2.11/10.** No — this turn would not be accepted. The agent failed to answer the customer's direct availability question, repeatedly re‑asked for information already given, and missed the required denial + immediate alternatives that would have moved the sale forward.

## Combined rubric scores

| Dimension | Score / 10 | Scenarios |
|---|---:|---:|
| objection handling | 5.43 | 21 |
| sales effectiveness | 5.62 | 63 |
| recommendation quality | 5.98 | 63 |
| closing timing | 6.08 | 13 |
| constraint extraction | 6.29 | 63 |
| trust | 6.48 | 63 |
| memory | 6.81 | 63 |
| intent understanding | 6.90 | 63 |
| confidence calibration | 6.92 | 63 |
| human feel | 7.24 | 63 |
| similarity accuracy | 7.40 | 5 |
| product accuracy | 8.25 | 63 |

Scores use the repository's unmodified 12-dimension rubric with `gpt-5-mini`, low reasoning effort: average non-null dimensions within each scenario, then weight scenarios equally. Retained grades are copied from the baseline's final verdicts. Old manual checker corrections are not automatically reused on changed transcripts; this run's corrections are saved in [finding adjudications](judge/finding_adjudications.json). Raw checker flags are preserved: `{'repeated_reply': 12, 'too_long': 2, 'filler_question': 1, 'wrong_variant_price': 2}`. These flags are diagnostics, not independent defect counts. Scores reflect one model-judged sample and can vary between runs.

The live log contains 23 caught `ValueError: Invalid format specifier ' true'` exceptions from the name-confirmation path in `products/services/product_resolver.py`. These are distinct from unhandled scenario errors: the agent falls back and continues. See [runtime findings](runtime_findings.json). Catalogue ground truth matches the baseline: True.

## Cost and provenance

| Cost component | USD |
|---|---:|
| Baseline live and grading | 0.85287455 |
| New live calls | 0.39862390 |
| New grading calls | 0.10134740 |
| Incremental total | 0.49997130 |
| Cumulative total | 1.35284585 |
| Cumulative cap | 1.50 |
| Remaining | 0.14715415 |

The ledger includes all baseline attempts and every new request, with cached-input discounts. Taxes and credits are excluded. No unresolved reservations remain. New cases used source revision `3fd7b326056c5ea74d439612c6da8ee92d7cb52a` and a read-only snapshot of the 46-product catalogue in a disposable local PostgreSQL database. Customer delivery was disabled. Retained evidence comes from baseline revision `aa7df41fd548357597d5582703c258268cb18284`.

Offline tests and historical backtests were not rerun. Their previous results remain in the [baseline report](../evaluation_20261018/report.md) and do not count toward this quality score or establish current regression coverage.

Evidence: [summary](summary.json), [all scenario scores](scenario_scores.csv), [live transcripts](live/runs.json), [judge verdicts](judge/verdicts.json), [cumulative usage](judge/usage.json), [live log](live_console.log), [judge log](judge_console.log), [request and baseline hashes](incremental_request.json), [database cleanup](database_cleanup.json).
