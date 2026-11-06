# Response style update

Customer replies now default to one or two short sentences in friendly Egyptian Arabic. Prompts no longer require sales questions, repeated introductions, or unsolicited size-value explanations. Fixed replies use the same tone, and price options share repeated product/bottle labels.

Order summaries and explicitly requested details remain complete. Existing and revised question wording both work in ongoing conversations. Store-authored FAQ answers and payment instructions retain their saved text. No model, API, schema, price calculation, order-confirmation rule, or delivery behavior was changed.

## Verification

- Full product suite: **1,981 tests passed**, zero unexpected HTTP attempts; `full-tests-final.log`.
- Added behavioral coverage for compact quotations, precise prices, per-option budget warnings, quotation evidence, sanitizer compatibility, old/new pending questions, handoff refusal, checkout details, and explicit recommendation counts.
- Nine isolated before/after model scenarios completed without API errors or crashes. Across 12 comparable replies, word count fell from 221 to 164 (about 26%); see `comparison.json`.
- Price example: 42 to 26 words, keeping all three price options. Comparison example: 44 to 20 words. Deferral: «تمام، خد وقتك.»

## Evaluation limit

The legacy F1 checkout script assumes a recommendation before answering the required scent question. It failed to reach checkout before this change and still does afterward. It is excluded from the word-count comparison. Full checkout summaries, required fields and explicit confirmation are covered by the passing offline suite; this replay is not counted as a successful checkout test.

Independent code review passed with no actionable findings. Changes are in the current working branch and have not been committed or deployed.
