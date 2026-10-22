# Store analytics

Open `/dashboard/?section=analytics` after signing in as a store owner. The initial
range is the last 30 calendar days, including today. Change the period or chart
aggregation, search/sort/page the product table, or choose a report and download CSV.
Custom dates require **Apply**. Filters survive reload and browser back/forward.
Refresh retrieves current database results; the timestamp shows the reporting cutoff.

## Deployment

This release includes migration `products.0040_analytics_history`. Run the normal
deployment migration and static collection steps before serving the updated app:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

These commands target the database configured for that deployment. Development
verification uses isolated settings instead; no live database was migrated or seeded
while implementing this feature.

The migration snapshots surviving order-item product IDs, names and volumes, and
changes variant deletion to `SET_NULL`. Existing stored prices remain unchanged.
Checkout and admin creation capture snapshots for new items. Historical names in
the backfill are the names available at migration time. Once deleted variants exist,
do not reverse this migration to the old non-null foreign key; roll forward instead.
New indexes support store/date filtering on orders and conversations.

## Definitions

| Metric | Definition |
| --- | --- |
| Revenue | Sum of `Order.total_price` for currently confirmed/delivered orders created in the selected period. Not cash receipts. |
| Orders | All orders created in the period, including pending/cancelled. |
| Average order value | Revenue divided by confirmed/delivered order count; unavailable with no qualifying orders. |
| Unique customers | Distinct nonblank `customer_phone_hash` values on period orders; missing identifiers are counted separately. No identifiers are returned. |
| New conversations | Conversations created in the period. This is not a count of messages or returning active conversations. |
| Conversion | Distinct new conversations with at least one linked same-store qualifying order created at/after the conversation and within the same period, divided by all new conversations. Empty denominator is unavailable. |
| Product units/revenue | Qualifying order items; revenue uses `quantity × price_at_time_of_order`. Variants and renamed products share a stable product identity. |
| Channel orders/revenue | Orders created in the period attributed through their same-store conversation, even when that conversation is older. Counts include all statuses; revenue only qualifying statuses. Missing links remain unattributed. |

All monetary computation uses Decimal until serialization. Current catalogue prices
never determine historical revenue. Product revenue need not equal order totals:
the dashboard exposes the difference and orders without retained items. It does not
allocate a difference to invented products. Hard-deleted items from before this
migration cannot be recovered. The operational Overview retains all-time totals.

Calendar boundaries use `settings.TIME_ZONE` (**UTC** in this project), never the
browser timezone. Input dates are inclusive; database predicates use an exclusive
end. Today is clipped to the request's cutoff. The comparison shifts both endpoints
back by the number of selected calendar days, preserving the local time of day.
For example, today through noon compares with yesterday through noon; month-to-date
compares with the immediately preceding equally sized date window, not a full month.
Weeks start Monday. Leading/trailing week/month buckets are clipped to the selected
dates, and previous data aligns by relative calendar day. Tooltips and tables show
both bucket date ranges. DST-aware calendar construction keeps local boundaries intact.

Percentage changes are unavailable when the previous value is zero or missing;
zero activity in both periods is labelled separately. Statuses reflect their current
values, so historical metrics can change after an order status update. `as_of` freezes
creation-time boundaries, not a historical database snapshot of mutable statuses.

## API

All endpoints use the existing store-owner JWT authentication. Store selection comes
from the authenticated request; a caller cannot override it with a query parameter.
Unparameterized `/dashboard/api/analytics/` retains its legacy JSON contract and
all-time semantics. The new UI explicitly requests `version=2`.

```text
GET /dashboard/api/analytics/?version=2&preset=30d&aggregation=daily
GET /dashboard/api/analytics/?version=2&preset=custom&start=2026-09-01&end=2026-09-30
GET /dashboard/api/analytics/products/?preset=30d&search=oud&sort=-revenue&page=1
GET /dashboard/api/analytics/export/?preset=30d&report=products&sort=-revenue
```

- Presets: `today`, `7d`, `30d` (default), `month`, `custom`. Explicit dates require `custom`.
- Aggregation: `daily` (default), `weekly`, `monthly`. Maximum date range: 366 days; future/reversed dates fail validation.
- Product search: up to 200 characters. Sort: `name`, `units`, `orders`, `revenue`, optionally prefixed with `-`; default `-revenue`.
- Pagination: `page` starts at 1; `page_size` defaults to 20, maximum 100. Pages beyond the last page clamp to the last page.
- CSV reports: `overview`, `trends`, `statuses`, `products`, `channels`. Product exports include all matching rows, not only the visible page, with a 10,000-row limit. Oversized exports return a validation error, never a truncated file.
- Optional `as_of`: timezone-aware cutoff no later than now, used by the UI to keep product requests and exports on the displayed period. Manual refresh creates a new cutoff.
- Monetary values in v2 JSON are decimal strings; unavailable ratios/changes are null.
- CSV uses UTF-8 BOM and formula-safe text cells. Date/filter metadata accompanies every row. Empty results contain a metadata-only row with blank report columns. No customer contact details or hashes are exported.

The UI has no synthetic-data fallback. Preview fixtures exist only under
`scripts/dashboard_ui`. Substantial browser behavior is in `static/dashboard/analytics.js`;
aggregation and validated windows are in `analytics.py`.

## Verification

```powershell
$env:TEST_DATABASE_URL='sqlite:///:memory:'
.\.venv\Scripts\python.exe manage.py test dashboard --settings=perfume_ai.settings_test --noinput
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=perfume_ai.settings_test
```

For PostgreSQL, use `scripts/run_offline_tests.py` with `TEST_DATABASE_URL` pointing
to disposable **local** PostgreSQL. It creates/drops a separate test database and
blocks external HTTP. See `scripts/dashboard_ui/README.md` for browser checks,
including the real-API smoke test backed by a temporary SQLite database.

Implementation verification: all 26 dashboard tests passed on SQLite and the final
55-test focused PostgreSQL run passed with no external HTTP attempts. The broader
PostgreSQL run completed 1,549 tests with one unrelated existing failure:
`Conversation1163RecommendationTests.test_price_recovery_keeps_the_recommendation_reason_and_budget_warning`.
That same wording assertion fails on untouched commit `47607e9`.
Browser layout, workflow, handoff, accessibility, analytics interaction, real-API
smoke and chat-client checks passed. Logs and captured screenshots are in
`artifacts/analytics` in the working environment.
