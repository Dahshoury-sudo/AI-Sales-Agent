GPT-6 Luna migration — completed locally, 2026-11-03

**Latest status:** the subsequent [predeployment review](predeploy/README.md) passed
1,821 offline tests and all eight browser checks, and reviewed 45 distinct live
Luna scenarios. It also fixed checkout, budget, name-matching, reply, and HTTPS
issues found during that review. The current cumulative API ledger is
[predeploy/live_final/usage.json](predeploy/live_final/usage.json): **$0.153685335**
of the existing **$0.30** cap. The comparison below records the initial migration
benchmark; consult the predeployment report for current readiness and limitations.

All four profiles now support the requested Luna configuration. The local `.env`
sets all three model variables to `gpt-6-luna`:

```env
OPENAI_MODEL=gpt-6-luna
OPENAI_SMART_MODEL=gpt-6-luna
OPENAI_RESOLVER_MODEL=gpt-6-luna
OPENAI_TEMPERATURE=0.3
```

| Profile | Work | Reasoning effort | Temperature |
|---|---|---|---|
| `extract` | Classification and structured extraction | `none` | `0` |
| `converse` | Customer replies | `none` | `0.3` |
| `reason` | Checkout reasoning | `high` | Omitted |
| `resolve` | Perfume-name matching | `high` | Omitted |

The existing Chat Completions interface and JSON contracts are preserved. Smart
profiles also use high effort when falling back to a Luna base model. Legacy
model configuration remains supported for rollback.

The budgeted evaluator now prices Luna cache reads and writes separately, counts
reasoning tokens once within completion tokens, and reserves the maximum request
cost before sending. Evaluation completion limits are 2,048 tokens for `none` and
25,000 for `high`. Empty, truncated, or invalid JSON responses are recorded as
failures. The replay probes forward promotion and preference context correctly,
and the analytics migration test restores the current schema after its check.

**Measured comparison**

The same three scenarios were run under both configurations against the same
local catalogue. Each configuration made 19 API calls across eight customer turns.

| Scenario | Luna cost (USD) | Old models cost (USD) | Luna time | Old models time |
|---|---:|---:|---:|---:|
| Checkout, six turns | 0.002808225 | 0.017875000 | 50.7 s | 55.0 s |
| Original-bottle inquiry | 0.000494995 | 0.004680900 | 4.9 s | 5.3 s |
| Summer/office recommendation | 0.001263905 | 0.006609600 | 11.1 s | 6.2 s |
| Total | **0.004567125** | **0.029165500** | **66.7 s** | **66.5 s** |

The observed cost reduction was **84.34%**. Luna made 11 requests with `none` and
eight with `high`, using 1,496 reasoning tokens. Both configurations passed all
three scenarios without deterministic quality findings or response errors.
These are small-sample measurements: token use and cache hits differ, so this
does not establish a monthly savings rate or broad quality equivalence.

The checkout assertions verified the exact Eros 50 ml store-bottle variant at
750 EGP, a quantity increase, saved contact details, and a later quantity decrease
combined with a confirmation request. Both models showed the revised total
before creating exactly one order on the next confirmation. The original-bottle
inquiry correctly quoted Eros 100 ml at 1,500 EGP. The recommendation used stocked
perfume data and stayed within the stated 1,200 EGP budget.

**Initial migration validation (superseded by the predeployment review)**

- The full offline suite passed **1,788 tests** with zero unexpected HTTP calls.
  The final focused run passed **27 tests**, including the added probe-context
  regression. Logs: `offline_verified.log` and `offline_final_focused.log`.
- At the end of the initial migration, its ledger contained **104 completed API calls**, zero
  invalid responses, zero unresolved reservations, and **$0.066277575** spent
  against the existing **$0.30** cap. Current scenario records have no crashes.
- At that stage, `P3` and `F3` retained repeated-reply findings for unresolved perfumes
  absent from the current catalogue. Both also reproduced with the old models.
  The later predeployment review fixed these follow-ups and retested them.
- `CONV772` retains the checker's `named_product_without_data` finding. Manual
  review shows a clarification about Eros from the previous turn, with no new
  product claim. The raw finding remains available for inspection.

All replays used disposable local PostgreSQL databases and synthetic checkout
contact details. Each conversation rolled back, customer delivery was disabled,
and automatic API retries were disabled. The public source catalogue was read
inside a read-only transaction.

Machine-readable comparison: [comparison.json](comparison.json).
Initial migration ledger and transcripts: [validation_legacy](validation_legacy).
Current cumulative ledger and records: [predeploy/live_final](predeploy/live_final).
Luna transcripts: [validation_luna](validation_luna).
Reproducible scenarios: [replay_validation.py](replay_validation.py).

To repeat the checks, choose fresh output directories and retain cumulative
spending through `--resume-from`. The helper reruns its selected cases while
preserving earlier charges:

```powershell
.\.venv\Scripts\python.exe -X utf8 artifacts\luna_migration\replay_validation.py --model-set luna --resume-from artifacts\luna_migration\predeploy\live_final --output artifacts\luna_migration\repeat_luna --budget 0.30
.\.venv\Scripts\python.exe -X utf8 artifacts\luna_migration\replay_validation.py --model-set legacy --resume-from artifacts\luna_migration\repeat_luna --output artifacts\luna_migration\repeat_legacy --budget 0.30
```

The checkout fixture intentionally asserts the catalogue's current Eros variant
and price. A future catalogue change requires reviewing those expectations.

**Activation and rollback**

The local `.env` already contains the Luna settings above. Apply the same
environment variables to deployed web and Celery services and restart those
processes to activate the migration there. Hosting environment variables override
`.env`; the ignored local file is not a deployment mechanism. No database schema
migration is required for this model change.

To roll back, set `OPENAI_MODEL=gpt-4.1-mini` and both
`OPENAI_SMART_MODEL` and `OPENAI_RESOLVER_MODEL` to `gpt-5-mini`, then restart the
services. Keep `OPENAI_TEMPERATURE=0.3`.

Official OpenAI documentation verified on 2026-11-03:
[Luna capabilities and pricing](https://developers.openai.com/api/docs/models/gpt-6-luna),
[GPT-6 parameter compatibility](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra#migration-quickstart),
and [cache billing](https://developers.openai.com/api/docs/guides/prompt-caching).
