# Renderer implementation report

Implemented the approved layout in `products/services/product_formatting.py`, changing only `render_quotes`. Quotes first collect into first-seen `(product_id, name)` sections, preserving their order within each product. Every section has one standalone catalogue-name heading followed by adjacent bottle groups. Blank lines separate products. `include_name=False` omits headings while retaining the product and bottle boundaries.

Decimal prices and quantity totals keep full precision. Near-budget warnings still attach to their own size and use the existing per-unit budget tier. Evidence records retain their original input order and unit prices; the canonical displayed evidence text now reflects the new heading layout. No changes to checkout summaries, selection, append logic, context serialization, or prose instruction were made by this renderer task.

Updated and expanded `products/test_compact_quotes.py` to cover interleaved A/B/A selections, joined adjacent bottle groups within a product, normal/original/normal order, overlapping names, distinct catalogue IDs sharing a name, name omission, all selected sizes, quantities, exact decimals, warnings, evidence, sanitizing and append idempotence.

Validation used disposable local PostgreSQL with external HTTP blocked:

- Red: `.venv/Scripts/python.exe scripts/run_offline_tests.py products.test_compact_quotes --log artifacts/price_name_once/renderer-red.log` — 16 tests, 12 expected layout failures, 0 HTTP attempts. New expectations fail because the old renderer repeats product names and preserves separate A/B/A sections.
- Green: `.venv/Scripts/python.exe scripts/run_offline_tests.py products.test_compact_quotes --log artifacts/price_name_once/renderer-tests.log` — 16 tests, 0 failures, 0 HTTP attempts.
- Related: `.venv/Scripts/python.exe scripts/run_offline_tests.py products.test_bug_regressions products.tests --log artifacts/price_name_once/renderer-related-tests.log` — 1,447 tests, 0 failures, 0 HTTP attempts.
- `git diff --check -- products/services/product_formatting.py products/test_compact_quotes.py` — exit 0. Git reports only its existing LF-to-CRLF normalization notice for the test file.

No outstanding renderer concerns. The composition task will integrate model prose with these headings; its additive evidence work and the final full-products run remain the responsibility of the composition/root agents. Renderer tests use the canonical quotation text so old context/evidence APIs remain exercised.
