Predeployment review — 2026-11-03

**Local validation passes after the fixes below. Production activation still requires verifying the hosted configuration and a controlled delivery check.** This review did not deploy the application or send customer messages.

| Check | Result | Evidence |
|---|---|---|
| Full Django regression suite | 1,821 tests passed; zero unexpected HTTP attempts | [offline_final.log](offline_final.log) |
| Browser checks | All eight passed: reply controls, workflows, layout, requests, handoff, analytics, accessibility, real Django analytics API | [ui/summary.json](ui/summary.json) |
| Chat-client JavaScript checks | All three passed | `node scripts/verify_chat_clients.js` |
| Dependencies | Installed versions match requirements; `pip check` passed | Python 3.14.3, OpenAI SDK 2.44.0 |
| Startup and configuration | WSGI import, Celery task/beat registration, and simulated production checks passed with the advisories below | [production_checks.json](production_checks.json) |
| Database and static assets | No missing model migrations; 179 static files collected, 861 postprocessed, manifest verified | [production_checks.json](production_checks.json) |
| Live model behavior | 45 distinct Luna scenarios, 84 customer turns in the latest records, zero scenario crashes; one manually reviewed finding retained | [live_final/runs.json](live_final/runs.json) |
| API accounting | 513 cumulative completed calls; zero invalid responses or unresolved reservations; $0.153685335 of the $0.30 cap | [live_final/usage.json](live_final/usage.json) |

The API total includes the earlier model comparison and every superseded evaluation attempt. This predeployment review added $0.087407760. The 56 records in `runs.json` include 11 carried records outside the 45-scenario expanded review; they are not 56 fresh scenarios. The last invocation reran six affected cases and carried the latest results from previous invocations.

The review fixed these verified problems:

- **Checkout follow-ups:** unresolved names no longer hide other selected perfumes needing a size or bottle choice. Quantity changes are acknowledged, explicitly supplied names are preserved, and repeated quantity requests acknowledge the saved choice while asking only for missing contact details.
- **Price follow-ups:** saved, unsized selections can be recovered from an older incomplete checkout response. Later product discussions take precedence, and another store's conversation cannot supply a reference.
- **Insufficient budgets:** the reply explains the actual minimum among searched candidates and preserves the customer's budget and exclusions. The explanation carries validated variant and quantity evidence through the router's price guard. It does not offer a far-above-budget perfume as a recommendation.
- **Similarity requests:** ingredient analogies such as “sweet like desserts” no longer trigger a perfume-reference question. An Arabic reference can use the existing confirmation resolver, and a failed or mismatched confirmation keeps the request unresolved.
- **Name collisions:** “Baccarat Rouge” resolves to Baccarat Rouge 540, not the unrelated Rouge by Narciso Rodriguez. A shorter product name must account for the remaining typed words through its brand. Multiple possible editions remain ambiguous.
- **Checkout editions:** a name matching several editions stays pending instead of sending the first guessed edition into the cart. The final F3 replay preserves the unspecified Stronger With You separately and quotes only the explicitly identified Intensely. No order is confirmed while those choices remain unresolved.
- **Customer replies:** explicit drafting commentary and thought tags trigger the existing bounded retry; another invalid draft falls back to catalogue facts. The comparison prompt no longer supplies an unrelated named perfume as an example.
- **HTTPS defaults:** session and CSRF cookies become secure by default when `DEBUG=False`. Redirects, HSTS, and trusted proxy headers are configurable, with regression checks for redirect loops and untrusted forwarded headers.

The full checkout replay verifies Eros 50 ml at 750 EGP, quantity changes from one to two and back to one, saved contact details, a revised total before confirmation, and exactly one order after the next explicit confirmation. All orders existed only in disposable local test databases and were rolled back.

One automatic finding remains in the latest 45-scenario records: M4 flags `Ambero`, transliterated from the customer's statement about a past purchase. The reply thanks the customer and makes no availability, price, or scent claim about that name. It is retained for inspection in [live_verified/LUNA_M4.json](live_verified/LUNA_M4.json). Missing catalogue names can still lead to clarification, and some discovery replies ask unnecessary follow-up questions; the sample does not establish perfect conversational quality.

Earlier failures remain in their original folders for traceability. In particular, the repeated budget replies, wrong Baccarat reference, arbitrary checkout edition, and S2 drafting commentary are superseded by the verified results in `live_release` and `live_final`. The guarantee check was corrected to distinguish “without guaranteeing it” from an actual guarantee. Older scenario prose also assumes products that are absent from this catalogue; results were reviewed against the recorded catalogue. The existing non-strict budget tolerance is 20%; it was not changed by this review.

**Hosted configuration to verify before activation**

Apply the same model settings to the web, Celery worker, and scheduler services:

```env
DEBUG=False
OPENAI_MODEL=gpt-6-luna
OPENAI_SMART_MODEL=gpt-6-luna
OPENAI_RESOLVER_MODEL=gpt-6-luna
OPENAI_TEMPERATURE=0.3
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
TRUST_PROXY_HTTPS=True
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600
```

`TRUST_PROXY_HTTPS=True` is appropriate only behind an ingress that overwrites `X-Forwarded-Proto` and prevents clients from bypassing it. Verify that condition for the deployed service. The middleware tests cover the corresponding HTTPS request and redirect behavior.

Use the production `SECRET_KEY`, `OPENAI_API_KEY`, `DATABASE_URL`, and `REDIS_URL`; confirm exact `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and required CORS origins. Preserve the existing `FIELD_ENCRYPTION_KEY` consistently across services so stored customer details remain readable. The local `.env` is configured for development and is not evidence of the hosted configuration.

The existing [Procfile](../../../Procfile) defines the web process, Celery worker, scheduler, and release commands. Confirm all required services run, with only one beat scheduler for this schedule. Run static collection and pending migrations through the release process; no new schema migration was needed for these fixes. Deploy the backend, templates, and static assets together.

Production-mode checks were simulated locally using a temporary strong secret. Three advisories were reviewed: `SAMEORIGIN` supports the embedded dashboard; HSTS does not cover unaudited subdomains; browser preload remains disabled during the initial one-hour HSTS rollout. Actual hosting variables were not read or changed.

After activation, verify HTTPS login, a signed inbound webhook, worker processing, a test-account reply, and an order/notification flow using controlled test data. Delivery, live Redis connectivity, hosting health checks, and external provider credentials were not exercised by the offline/browser suites. The live evaluations exercised OpenAI but disabled customer delivery. No production throughput or peak-concurrency load test was performed; capacity for 300 customers per day under your actual traffic peaks remains unverified.

**Repeatable checks**

Use fresh log/output paths. Never run the regression suite with the default production `DATABASE_URL`; this wrapper requires local `TEST_DATABASE_URL`, creates a disposable PostgreSQL database, and blocks HTTP:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\run_offline_tests.py products dashboard --log artifacts/luna_migration/predeploy/offline_repeat.log
```

For a paid targeted replay, retain cumulative spending by resuming from the latest ledger:

```powershell
.\.venv\Scripts\python.exe -X utf8 artifacts\luna_migration\replay_validation.py --model-set luna --extended --workers 2 --scenario-ids CHECKOUT,R1,M3,S1,S2,S3,F3 --resume-from artifacts/luna_migration/predeploy/live_final --output artifacts/luna_migration/predeploy/live_repeat --budget 0.30
```

The live runner uses a read-only catalogue snapshot, synthetic contact details, disposable local PostgreSQL, rollback per scenario, blocked customer delivery, and no automatic API retries. The browser preview used for this review has been stopped.

Rollback remains available by restoring `OPENAI_MODEL=gpt-4.1-mini` and both smart/resolver variables to `gpt-5-mini`, keeping temperature `0.3`, and restarting the services. That is the earlier measured configuration, not a guarantee of identical answers.
