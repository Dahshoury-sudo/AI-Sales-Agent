# Conversation 1075 — implementation and validation

The contextual purchase “هات واحده اوريجنال 100 ملي” now selects the single verified Sauvage offer and saves one original 100 ml bottle. Its price comes from the catalogue (815 EGP in the incident fixture). Repeating the perfume name or purchase retains that line rather than adding a duplicate, losing its identity, or inventing customer details.

The original regression began in `f0ba894`: the new identity guard tried resolving the purchase sentence as a perfume name. This fix retains the guard for explicit names and resolves unnamed purchases before inspecting model-provided `source_text`.

## Changes

- Contextual selection uses a unique existing cart line or the latest supported offer within two delivered assistant replies. It excludes stale, undelivered, foreign-store and closed-checkout offers. Multiple choices require clarification.
- Clear unnamed purchase follow-ups have a deterministic checkout route, preventing classifier drift into recommendations or product information.
- A perfume name answering an outstanding order question repairs the existing pending line and preserves its quantity, volume, bottle type and line ID. Existing malformed entries can recover on the next relevant customer message; historical records are not rewritten.
- Arabic shorthand already answered with a single supported product can reuse that exact user-name mapping. This does not relax the global phonetic thresholds or bypass numeric-name/flanker ambiguity. Confirmations cannot create name mappings.
- Selection-only messages cannot supply personal details. Explicit name/address/phone updates keep their existing behavior. Repeated purchases acknowledge the saved bottle, ask briefly for missing information and retain budget warnings.
- Checkout prices, stock validation, confirmation fingerprints, duplicate-event handling and cancellation protections remain in place.

No migrations, dependencies or public API changes were introduced. The changes remain local and uncommitted; no deployment or customer messages were sent.

## Offline verification

| Check | Result |
| --- | --- |
| Baseline full PostgreSQL suite | 1,448 passed |
| New incident/regression cases | 32 passed |
| Final full PostgreSQL suite | 1,480 passed in 28.328 seconds |
| Unexpected HTTP attempts in final runs | 0 |
| Django system checks | Passed |
| Python syntax checks and `git diff --check` | Passed |
| Disposable databases checked after cleanup | 26; none remain |

The first new tests reproduced the failure before the implementation changed. Coverage includes independent customer/model inputs, wrong or missing extracted products, source-span variations, Arabic digits, recovery of the recorded bad pending line, routing, ambiguity, explicit unavailable/unknown names, contact updates, stock, current prices, confirmation, budget warnings and duplicate web events.

Older persistence tests sometimes used only a perfume name as the customer message while their model stubs supplied a quantity edit, contact details or approval. Those inputs now explicitly express the intended action; their assertions were retained. The existing unavailable-product contact test also now asserts that the pending perfume identity survives.

The reusable offline runner enforces local PostgreSQL, unique disposable databases and blocked HTTP, including a counter that detects attempted calls even if application fallbacks swallow the exception.

- [Final focused log](conversation1075-complete-focused.log)
- [Final full-suite log](conversation1075-complete-full.log)
- [Initial failing regressions](conversation1075-before.log)
- [Source hashes, results and cleanup evidence](conversation1075-verification.json)

## Live verification

Conversation 1075 passed three consecutive complete replays: [run 5](conversation1075-live-5/CONV1075.json), [run 6](conversation1075-live-6/CONV1075.json), and [the final scenario batch](conversation1075-live-final/CONV1075.json). Each retained one resolved original 100 ml Sauvage after turns 2–4, with no pending identity, invented contact details or confirmed order. Purchase replies followed checkout and the repeated request received an acknowledgement.

Earlier runs exposed additional failures that cart-only assertions missed: off-topic classifier routing, repeated contact requests and the perfume name being stored as the customer's name. Those were fixed and regression-tested. The harness now checks reply type and contact-field presence in addition to exact cart variants, pending lines and order count.

The final batch also ran `CONV1069`, `F1`, `P2`, `P3` and `F4`, with no automated findings or transport errors. Budget warnings, unavailable-product preservation, quantity updates and original-liquid preferences behaved as expected. Transcripts were inspected in addition to automated grading.

**Existing limitation found during transcript review:** the opening Ambero/9PM comparison in `CONV1069` can return `NUMERIC_NAME_UNRESOLVED`, leaving the following ordinals without an offered list. The baseline committed product-information code and current code produce the identical response in a controlled read-only comparison without model calls. This is a pre-existing comparison issue, not a regression from this change. Its later explicit 9PM checkout and price-dispute turns passed. Zero automated findings should not be interpreted as proof that every conversational response in that scenario was ideal.

The final offline suite also includes the last audit guards for empty name mappings and budget warnings on repeated selections. Those guards were added after the three incident live replays; their triggers are covered by deterministic regressions.

Total live spending, including unsuccessful development iterations: **$0.11475965 of the authorized $1.00**, across 93 requests, with no outstanding reservations. All live scenarios used copied public catalogue/store facts in disposable local databases, rolled back their data, and disabled customer delivery. No paid judge was used.

- [Latest scenario transcripts](conversation1075-live-final/runs.json)
- [Cumulative spending ledger](conversation1075-live-final/usage.json)
- [Live environment and model profiles](conversation1075-live-final/manifest.json)

## Reproduce offline checks

```powershell
.\.venv\Scripts\python.exe scripts/run_offline_tests.py products.test_conversation1075 --log artifacts/bug_fix_validation/conversation1075-recheck-focused.log
.\.venv\Scripts\python.exe scripts/run_offline_tests.py --log artifacts/bug_fix_validation/conversation1075-recheck-full.log
```

`TEST_DATABASE_URL` must point to local PostgreSQL. Both commands create and remove their own test databases. Live replays should use the existing budgeted runner and carry the prior ledger forward with `--resume-from` so repeated runs cannot reset cumulative spending.
