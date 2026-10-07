# Conversation bug fixes

Implemented in the existing Django apps, services, tasks and web clients. No project restructuring. Original evidence in `artifacts/bug_reproductions` and the original diagnostic scripts was preserved.

**AIAgent status, 2026-10-07:** Ran Django's migration command against local `AIAgent`. `0037_conversation_integrity` was already recorded as applied at 02:05:34 UTC; no migrations remained. Verified model tables/columns, required unique constraints, cart-line IDs and pending-product backfill. System checks passed. The earlier migration mismatch is no longer present in the current database. See [database verification](aiagent_migration.json).

All 21 reproduced cases have regression coverage. L08 and L13 remain controls, rather than being counted as reproduced defects.

| Cases | Implemented behavior |
|---|---|
| L01, L02 | Persist every cart line, including unknown products and unsized quantities. Omitted lines survive extraction; explicit line IDs remove items. Unresolved lines prevent checkout. |
| L03, C01, C02 | Require a delivered summary of the current cart, contacts and prices. An edit or price change produces a revised quote requiring another approval. Only JSON `true` confirms. |
| C03 | Serialize confirmations and commit the order, stock change and cart clearing atomically. A unique checkout token prevents duplicate orders. |
| C04 | Report success once the order commits. Failed dashboard notifications remain pending and retry independently, with deduplication. |
| C05 | Cancellation restores stock once. Cancelled orders cannot reopen through the bot, API or admin. |
| L04, L05 | Apply requested size, bottle type, availability and price to the same variant. Combined budgets use the aggregate price; exact budgets stay strict. Approximate budgets retain disclosed 20% flexibility. A literal combined amount cannot be divided by the extractor. |
| L06, L07 | Cover each requested product and factual comparison dimension. Calculate price per ml from variant data; acknowledge uncertainty about comparative sweetness. |
| L09, L10 | Retain explicit false preferences and clear previous requirements when the recipient changes. |
| L11 | Preserve the ordered list of delivered recommendations across FAQs. Failed or uncertain replies cannot replace that list. |
| L12 | Save checkout contact details before answering a supplemental FAQ. The combined response remains a confirmable quote. |
| D01, D02 | Persist inbound receipts, reuse replies for repeated source IDs, and process accepted messages in order under a conversation lock. Generation failure rolls back the turn and its state changes together. |
| D03 | Persist delivery outcomes, including separate image/text parts. An ambiguous acknowledgement or interrupted send flags staff and is not automatically resent. Exclude undelivered assistant replies from model history. |
| D04 | Provide an authenticated, signed-conversation message cursor and poll it from the widget and both demo clients. Staff replies and attachments appear once. |
| E01 | Bind quoted prices to the named product, size and bottle type, rather than accepting any catalogue price. |
| L08, L13 controls | Use the store's configured formulation facts and acknowledge unknown oil-manufacturer information. |

Validation:

- Full suite: **1,401 tests passed on disposable PostgreSQL**, with external HTTP blocked. Includes real concurrent confirmations, cancellations and web submissions, interrupted/partial delivery, generation rollback and commit-time repricing. See [test_summary.json](test_summary.json) and [regression tests](../../products/test_bug_regressions.py).
- Live replay: the initial three passes covered L01–L13 (39 conversations). A further complete pass covered all 13 after the quote and preference changes; exact ordinal selection and stored contacts were checked. See [full-pass results](final/live_summary.json).
- Stronger state checks then exposed an intermittent halving of the combined budget. The literal-budget fix and bottle-type guard passed three further L04/L05 passes (six conversations): [final constraint results](constraint_followups_fixed/live_summary.json). Earlier intermediate evidence is retained in `constraint_followups`; use `constraint_followups_fixed` for the final result.
- Live runs copied the real catalogue/public store facts in a read-only transaction into disposable databases. Customer transport was disabled, synthetic orders were rolled back, and test databases were removed. Live model responses remain variable; the saved transcripts and assertions document the exercised behavior.
- All three shipped chat scripts passed syntax and offline interaction checks for retry identity, overlapping submissions, cursor polling, staff attachments, echo deduplication and stale responses after reset. Run `node scripts/verify_chat_clients.js`. This was an offline DOM simulation, not a visual browser test.
- Django system checks, migration consistency and `git diff --check` passed. A migration rehearsal preserved a legacy pending product, existing quantities and stable line IDs: [migration results](migration_summary.json).

Rollout prerequisites:

1. **Local AIAgent is current.** The earlier database inspection showed migrations through `0041` from another checkout. The database inspected on 2026-10-07 instead matches this checkout through [0037](../../products/migrations/0037_conversation_integrity.py), including the actual schema. No migration-history rewrite or data removal was needed in this verification. Inspect any separate deployment database before migrating it; do not blindly fake migrations to bypass a mismatch.
2. Deploy the app/worker changes together with the additive migration on any other target. Existing carts survive; legacy summaries require renewed review because they have no versioned quote.
3. Run one Celery Beat scheduler alongside the worker. `Procfile` and `StartAiAgent.bat` include it; it retries pending order notifications every 60 seconds. This task did not deploy or launch services.
4. Use direct PostgreSQL connections or session pooling. Conversation advisory locks require session affinity and are incompatible with transaction-mode PgBouncer. The SQLite lock fallback is for single-process tests.

The work remains local and uncommitted. No customer messages were sent by validation.
