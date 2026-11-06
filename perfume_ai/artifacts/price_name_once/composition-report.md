# Composition task

Implemented final price composition with one catalogue-name heading per quoted product, requested details beneath that heading, and name-free quotation rows. Existing `(reply, context)` service results and the plain-text generation contract remain unchanged; there is no added rewriting call.

## Files and behavior

- `products/services/price_reply.py`: bounded, longest-first name matching; conservative supported-question residual check; controlled relocation of leading product subjects; per-product composition; exact section-scoped quotation stripping; layout validation; catalogue-based detail fallback. Pure Arabic-alias price drafts also discard only redundant bottle/availability/size clauses through a positive vocabulary; monetary claims always remain untrusted.
- `product_info.py`, `comparison_service.py`, and `objection_service.py`: use the shared composition for price-bearing generated replies. Unsupported mixed dimensions stay on generation instead of being reduced to the narrow deterministic facts handler.
- `product_formatting.py`: additive request/footer/section evidence support, idempotent appending, exact final-row stripping for price validation, and prompt instructions for a single leading name heading. Renderer changes belong to the renderer agent.
- `router.py`: existing grounding retry also checks composition. Recovery preserves requested catalogue notes, performance, gender, authenticity, occasion, season, perfume type, and explicit absence of ratings. Root added the final fallback claim/price/layout verification guard.
- `ai/scent_recommendation.py`: composition checked inside the existing two-attempt selection loop before a direct return; existing deterministic fallback handles an unsafe embedded opener. Preserves brand disclosure, selection evidence, and verified total footer.
- `comparison_service.requested_facts`: uses section references in the value verdict to avoid repeating the name and retains budget warnings with unchanged selected quotations.

## Evidence and safety

Quote records retain exact prices, variant IDs, quantities, bottle types, and ordering. Additive `sections` store each product's exact displayed rows; generated descriptions remain outside those trusted rows. A trusted row is removed from validator prose only at the end of its uniquely matching product section. Generated duplicates earlier in a section or under another heading remain untrusted, including rows whose numbers match another real quotation. Old evidence remains readable. Redundant-clause filtering excludes monetary and liquid-formulation prose, preserving `زجاجة اوريجنال بس تركيب`; it recognises the verified live availability forms `بحجمين` and `بحجم واحد`.

Ambiguous name-bearing sentences are retained and flagged for the existing retry/fallback instead of globally deleting names or assigning their descriptions to an arbitrary product. Latin word boundaries prevent `Pi` from matching inside `Pink Pepper`.

## Verification

Final focused command:

`.venv/Scripts/python.exe scripts/run_offline_tests.py products.test_price_name_once products.test_scent_recommendations products.test_brand_preference_replies products.test_bug_regressions products.test_compact_quotes products.test_performance_policy products.test_response_style products.tests.SalesScenarioTests.test_i_a_duration_question_gets_the_store_answer_without_generation products.tests.SalesScenarioTests.test_i_blank_performance_fields_use_the_store_policy products.tests.AnswerEveryRowTests.test_each_requested_price_is_answered_once --log artifacts/price_name_once/composition-tests.log`

Result: **233 tests passed; zero unexpected HTTP attempts**. `git diff --check` passed (Git reported only its existing LF/CRLF warning for the renderer's test file).

New tests cover duplicate pure-price and mixed openers, all bottle types, overlapping and numeric names, invalid generated notes/prices, retry failure recovery, wrongly placed identical quotation rows, ambiguous embedded subjects, mixed gender/reviews, authenticity and liquid contrast, short names within notes, recommendation openers, total footer, value verdict naming, and Arabic-alias redundant bottle/size prose including `متوفرة بحجمين` and `بحجم واحد`. Red runs are recorded in `composition-red.log`, `composition-adversarial-red.log`, `composition-gate-red.log`, `composition-review-red.log`, `composition-original-red.log`, `composition-selection-red.log`, `composition-alias-red.log`, `composition-alias-morphology-red.log`, `composition-formulation-red.log`, and `composition-count-red.log`.

Review/live findings also preserved existing deterministic behavior for the narrowly formed Arabic duration request and the established plural price typo `لااتنين`. Unsupported mixed dimensions still require normal generation; no arbitrary request words are removed as name aliases.

Legacy recommendation assertions were adjusted only where they required the old contiguous name-bearing prose; preserved reason, brand disclosure, exact prices, selection count, and model-call checks remain. The joint-selection fixture now supplies separate attributed product clauses under the approved generation contract.

Root owns the complete products suite, live samples, and independent final review. No commit, subagent, migration, external message, or change to `../.gitignore` was performed by this task.
