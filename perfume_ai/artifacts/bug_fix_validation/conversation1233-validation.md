# Conversation 1233 validation

Implemented and validated on 2026-11-01. No schema changes or deployment.

- Same-notes requests retain the requested Terre d’Hermès reference and use its catalogue notes. Unrelated targets, different editions, ambiguous editions and negated comparisons are rejected.
- Occasion conflicts and insufficient scent similarity remove continuity bonuses. Daytime suitability outranks evening projection; unknown occasion data remains unknown.
- Both generated recommendation branches lead with alternatives and explain supported differences. Weak similarity evidence no longer says “فيه شبه”. Unsupported similarity or a no-match declaration triggers one correction attempt, then a catalogue-fact fallback if necessary.
- Bounded daily/evening references resolve the latest delivered suitable offer across the full stored history. Multiple matches prompt clarification. Named products, fresh requests and orders with products, sizes or quantities keep their routes.
- The full suite also exposed an existing duplicated “مناسب مناسب” recovery phrase; the factual price-recovery explanation now uses the wording required by its regression test.

## Verification

- Offline regressions: **1,665 passed**, zero unexpected HTTP attempts. [Full log](conversation1233-offline.log).
- Django checks: no issues, using isolated test settings.
- `git diff --check`: clean.
- Coverage includes classifier mistakes, active carts, older offers outside the model history window, failed/pending delivery, unavailable products, store isolation, negation, editions, unknown occasions, continuity conflicts, both recommendation branches and bounded reply correction.

## Model replays

Three independent runs of each affected turn, nine turns total, using the saved 251-product catalogue and original history before each target. All final checks passed; replies were also read for scent and occasion claims.

| Turn | Outcome in all three runs | Merged preferences |
| --- | --- | --- |
| Same notes as Terre d’Hermès | Dark Aura and Showmix offered as alternatives; no same-scent claim or no-match declaration. Search uses Terre d’Hermes’s recorded citrus, pepper/flint and vetiver/cedar/patchouli/benzoin notes. | Male, store brand, Terre d’Hermès comparison retained. |
| Daytime, projecting and lasting | Bonded and Showmix; recorded eight-hour longevity and moderate projection. Bonded leads instead of the stronger evening products. | Male, store brand, daytime (`day`/`daytime`), strong projection, long-lasting; earlier clean preference retained. |
| “واعطيني العطر اليومي” | “للنهار كنا اتكلمنا عن Bonded، Showmix. تقصد أنهي واحد؟” | Existing preferences preserved. |

Full selections, extracted/merged intent, catalogue reference, replies and validation results: [conversation1233-replays.json](conversation1233-replays.json). Reproduce with `python scripts/replay_conversation1233.py --runs 3`.

Isolation: model calls used the configured `gpt-4.1-mini`/`gpt-5-mini` profiles; all storage was in-memory SQLite. Customer transports and background tasks were blocked. No customer messages were sent or live conversations changed. Offline regressions used disposable local PostgreSQL with HTTP blocked.

Limits: preference seeds were reconstructed from preceding explicit requests, not historical preference snapshots. No saved custom store-settings snapshot was available, so replays used the default store prompt. These are isolated turn replays, not a checkout or deployment test.
