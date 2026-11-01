# Conversation 1232 validation

Implemented and verified on 2026-11-01. No deployment or database migration.

## Corrected behavior

- Affirmative similarity and new-use requests reach recommendations despite misleading classifier labels. Product questions, daily pointers, explicit orders and genuine refinements retain their handling.
- `شبهو عشان عندي منو` resolves to the latest relevant Terre d'Hermes reference, including accented spelling. Unrelated older suggestions cannot supply the reference; ambiguous offers require clarification.
- A standalone new scent target or purpose replaces the preceding positive scent/use/performance goal. Explicitly connected refinements retain it. Budget, recipient, brand restrictions and explicit exclusions remain available.
- Mosque use does not imply morning. The final mosque/casual request becomes `occasion=casual`, `notes=[clean]`, `projection=moderate`, without the previous similarity reference. `multiple` is never saved as an occasion.
- Current suitability outranks previous-offer effects on a new goal. Excessive projection conflicts with subdued use; stronger projection no longer gets full matching credit for a moderate request.
- Replies offer grounded alternatives directly. Checks cover incorrect occasion claims, equal-performance comparisons, unsupported similarity, and accidentally offering the reference itself during factual recovery.

## Verification

- **1,746 offline tests passed**, using a disposable local PostgreSQL test database; zero unexpected HTTP attempts. Log: `conversation1232-offline.log`.
- **21 conversation-1232 replay turns passed**: three independent sequential runs of all six customer turns, plus one original-history replay each for messages 7601, 7603 and 7605.
- **Five compatibility replay turns passed**: two for conversation 1230 and three for conversation 1233, preserving cross-brand recommendations, morning ranking and daily-reference resolution.
- Additional factual audit of the 21 final 1232 replies found no supported-vocabulary note, duration, projection, occasion or comparison violations.
- Django system checks and `git diff --check` passed.

### Observed selections

| Request | Sequential runs 1 / 2 / 3 |
| --- | --- |
| Fresh/light for mosque (7597) | Guilty Black + Zlatan in all three |
| Terre comparison (7601) | Bleu de Chanel + Allure Homme Sport / Pure XS / Allure Homme Sport |
| “Something like it; I own it” (7603) | Bleu de Chanel + Wanted by Night in all three |
| New mosque/casual purpose (7605) | For Him + Bmw in all three |

The original-history replays led with Bleu de Chanel on both comparison turns and Bonded on the final casual-use turn, with For Him as the second option. These are observed results, not hardcoded product rules. Guilty Black remains eligible for requests it fits; it is not globally banned.

## Evidence and isolation

- Final 1232 trace: `conversation1232-verified-replays.json`. Includes classifications, extracted and merged intent, saved preferences, ranked candidates, selected products, reply text and checks for every turn.
- Compatibility traces: `conversation1230-goal-regression-replays.json` and `conversation1233-goal-regression-replays.json`.
- Source: `conversation1232-source.json`; complete saved store catalogue: `conversation1232-catalogue.json` (251 products). Captured from local PostgreSQL inside a read-only transaction, with only public store prompt settings copied.
- Replay storage was in-memory SQLite. HTTP was allowed only to the configured model endpoint; customer messaging, SMTP and background task submission were blocked. No blocked delivery attempts occurred.
- Profiles: `gpt-4.1-mini`, with `gpt-5-mini` configured for smart/resolver calls. Historical preference seeds are reconstructed test inputs, not historical database snapshots.
- A read-only comparison after validation confirmed that conversation 1232's original messages, preferences and sales state exactly match the capture.
- Earlier exploratory traces are retained separately. Manual review of those runs identified the equal-projection comparison and reference-in-recovery gaps; the verified trace above includes both corrections.

Reproduce with `scripts/run_offline_tests.py --log artifacts/bug_fix_validation/conversation1232-offline.log` and `scripts/replay_conversation1232.py --runs 3 --output artifacts/bug_fix_validation/conversation1232-verified-replays.json`, using the project's Python environment.
