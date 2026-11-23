# Nearest scent recommendations implementation plan

Approved in the conversation on 2026-11-07; this document also records the accepted specification.

## Goal and constraints

Recommend an available perfume sharing at least one requested scent tag. After that minimum, prefer a season match, then additional shared tags, then occasion. A fresh-only summer perfume therefore beats a fresh-and-woody winter perfume for a fresh, woody, summer request.

Gender, budgets (including existing approximate-budget tolerance), size/bottle type, availability, strict brands, perfume categories, literal ingredients, and explicit exclusions remain firm. Preserve public APIs, queryset search results, all eligible candidates, and stored preferences. No migration. Keep factual descriptions and existing any-scent behavior. Unknown or unlabelled references require a scent clarification. Unknown occasions do not block recommendations.

## Task 1: Shared eligibility and ordering

- Add regressions proving any shared tag qualifies, including saved `scent_match=all`, reference tags, and tags beyond the first three.
- Use one shared fit key: season match, tag overlap count, occasion match. Existing brand/discussion preferences may break ties; stable product ID is the final fallback.
- Remove season and occasion rejection, including the router's unknown-occasion gate. Keep real hard-limit reasons and preserve memory.
- Verify recommendation, occasion, brand, and conversation ranking regressions.

## Task 2: Selection and reply recovery

- Add tests for model selections skipping a nearer feasible option, catalog changes during generation, and combined-budget combinations.
- Supply match facts to the model and enforce fit order after selection. Retain one retry and deterministic recovery, using the same ranking and current eligibility.
- Recommend partial matches directly. Only actual missing scent information or unmet firm requirements may produce clarification. Never automatically remove the scent requirement or fabricate suitability.
- Verify reply, budget, exclusion, reference, and state regressions.

## Task 3: Contextual alternatives and documentation

- Route existing alternative suggestions with known preferences through the shared selector without changing factual product lookup behavior.
- Update recommendation guidance and outdated strict-filter tests.
- Run `python scripts/run_offline_tests.py --log <task-workspace>/full-suite.log` with disposable local PostgreSQL and external HTTP blocked.
- Obtain a fresh code review of the working diff, address material findings, and verify the final diff.

## Acceptance coverage

One tag succeeds; zero fails; season outranks extra tags; extra tags outrank occasion; ties honor existing context. Same season/different occasion and unknown occasions still recommend. Saved all-tag requests and similar-to requests use the same policy. Limits, quotes, stock rechecks, total purchase quantity, exclusions, unlabelled references, and truthful descriptions remain protected.

Baseline: 92 targeted tests passed before implementation. The user's unrelated working-tree changes are outside this task.
