# Cross-brand recommendations — conversation 1230

Implemented and validated on 2026-11-01. Ordinary brand requests now express a preference; explicit brand-only requests remain strict. Public APIs and database schemas are unchanged.

## Behavior

- Search considers all eligible brands for soft preferences. Suitability precedes the preferred brand and previous recommendations; equal-fit ties favor the requested brand.
- `brand_strict` lives in existing preference JSON. Explicit restrictions persist, brand changes/reset clear them, and legacy restrictions are recovered from customer history. Offering another brand does not overwrite the preferred brand.
- “من اختيار الستور” does not imply Perfamix products. Explicit daytime/performance wording is retained when the model omits those fields.
- Generated recommendations, constrained replies and factual/price recoveries disclose the alternative's real brand. A bounded correction/factual fallback prevents weaker-fit leads, wrong brand attribution, unsupported equal-performance comparisons, internal warning labels and excessive recommendation lists.
- Hard exclusions, availability, variant/budget constraints, product questions and orders retain their handling. Soft preferences no longer appear as hard no-match conditions.

## Verification

- **1,722 offline tests passed**, with **zero unexpected HTTP attempts**. [Full log](conversation1230-offline.log).
- Django checks: no issues, using isolated test settings.
- `git diff --check`: clean.
- New coverage includes soft/strict preferences, legacy history, openness and negation, own-brand wording, ranking ties, availability and store isolation, continuity, both generated branches, constrained quotes, factual recovery and brand disclosure.
- Older tests that intentionally exercise hard brand constraints now explicitly set `brand_strict=True`; their behavioral assertions remain in place.

## Final model replays

Three independent runs of each case, **15 turns total**, all passing their recorded checks. Replies were also inspected for brand attribution, recommended product count and performance comparisons.

| Conversation / turn | Outcome in all three runs |
| --- | --- |
| 1230 similarity | Bleu de Chanel / Chanel leads, with Wanted by Night / Azzaro as the other option. Different brands are disclosed without claiming the same scent as Terre d’Hermès. |
| 1230 daytime | Invictus Legend / Paco Rabanne leads, followed by Paradise Garden / Jean Paul Gaultier. Both have recorded strong projection and eight-hour longevity. |
| 1233 similarity regression | Bleu de Chanel and Wanted by Night replace the former brand-limited shortlist; the requested catalogue reference is retained. |
| 1233 daytime regression | Wanted / Azzaro and Invictus Legend / Paco Rabanne lead with their recorded daytime suitability, projection and longevity. |
| 1233 daily reference | The original historical offer still resolves to Bonded and Showmix, prompting clarification between them. |

Full selected products, brands, merged preferences, search results and replies:

- [Conversation 1230 final replays](conversation1230-final-replays.json)
- [Conversation 1233 final regression replays](conversation1233-final-brand-replays.json)

Reproduction:

```text
python scripts/run_offline_tests.py --log artifacts/bug_fix_validation/conversation1230-offline.log
python scripts/replay_conversation1230.py --runs 3 --output artifacts/bug_fix_validation/conversation1230-final-replays.json
python scripts/replay_conversation1233.py --runs 3 --output artifacts/bug_fix_validation/conversation1233-final-brand-replays.json
```

Isolation: replays imported the saved 251-product catalogue into in-memory SQLite and blocked customer transports/background tasks. Offline tests used disposable local PostgreSQL with HTTP blocked. No customer messages were sent, live conversations rewritten or deployment performed.

Limits: preference seeds reconstruct earlier explicit requests rather than historical preference snapshots. Conversation 1230 uses saved public store settings; the existing 1233 runner uses the default store prompt. These are affected-turn replays, not an end-to-end checkout validation, and the bounded wording guards do not validate arbitrary natural-language claims.
