# Price replies: one perfume name

Implemented the approved layout: one catalogue-name heading per perfume, requested details beneath it, then all selected bottle/size/price rows without repeating the name. Products follow first-seen order; each product retains its original variant order. The existing concise Egyptian Arabic style remains in place.

The shared renderer, generated product answers, comparisons, price objections, recommendation prices, and retry/fallback paths use the layout. Checkout summaries are unchanged. Unsupported mixed questions stay on the normal generation path; supported deterministic performance and price follow-ups retain their established behavior.

Quotation records preserve exact Decimal prices, quantities, bottle types, budget warnings, and selected variants. Generated prose stays outside trusted quotation rows. Section-specific stripping keeps invented or wrongly attributed prices subject to validation. Repeated quotation appending is idempotent. Ambiguous product attribution uses the existing retry/fallback rather than deleting names blindly.

## Verification

- Final full products suite: **2,005 tests passed**, zero unexpected HTTP, in `full-tests-release.log`.
- Final focused composition/regression run: **233 tests passed**, zero unexpected HTTP, in `composition-tests.log`.
- Independent reviewer verified the implementation and the fixes for mixed-detail routing, authenticity, short-name boundaries, and recommendation validation; see `final-review.md`.
- `git diff --check` passed; only Git's existing LF/CRLF conversion notice was printed.
- Isolated live evaluation covered five scenarios and six turns: price, notes with price, performance with price, two perfumes, and a size follow-up. Every quoted perfume name occurred exactly once; prices matched the copied catalogue, requested facts remained, and the 50 ml follow-up selected only that size.

Live samples exposed redundant availability wording, now covered by deterministic regressions including the exact observed phrases. Early evaluator `wrong_variant_price` flags came from carrying an earlier original-bottle mention into subsequent normal-bottle lines; manual comparison with `ground_truth.json` confirmed correct prices. No crashes, invalid model responses, or customer delivery occurred. Total live evaluation cost across the recorded runs was USD 0.009103900.

Example from the live follow-up scenario:

```text
Sauvage
زجاجة الستور الخاصة بينا 90 ملي بـ1000.00 جنيه، و50 ملي بـ750.00 جنيه.
زجاجة اوريجنال 100 ملي بـ1500.00 جنيه.
```

Changes remain in the working tree. No commit, deployment, migration, or change to the user's `../.gitignore` was made.
