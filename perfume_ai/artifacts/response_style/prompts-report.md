# Task 1: prompts and model-written replies

Implemented the approved natural Egyptian voice: ordinary prose defaults to one or two short sentences, recommendations default to at most two perfumes with one grounded reason each, and full requested details and checkout summaries remain exceptions. Follow-up questions now depend on a missing useful detail or purchasing intent. Availability answers, comparison and identification replies, appreciation, and repeated greetings no longer require a sales closer, recap, or decorative bullets. Size-value explanation is reserved for explicit value or size-choice questions.

Files changed:

- `products/services/ai/prompts.py`
- `products/services/product_info.py`
- `products/services/comparison_service.py`
- `products/services/general_service.py`
- `products/services/identification_service.py`
- `products/services/ai/scent_recommendation.py`
- `products/services/ai/recommendation.py`
- `products/services/sales/recommendation_response.py`
- `products/services/sales/appreciation.py`
- `products/services/sales/brand_response.py` (root-approved scope expansion)
- `products/tests.py`

Fallbacks retain catalog scent descriptions and selected names, omit unrequested shared performance, and state requested performance once for all choices. Recommendation selection, validation and brand disclosures share the existing count semantics: explicit recommendation count, or purchase quantity for a combined budget, with two as default. This fixes validators and disclosures that previously capped explicit larger requests at two. Prices, variants, quotation evidence, budgets, consent and checkout fields remain unchanged.

Added five output/validation regression cases for fallback scope and requested counts; watched the changed behaviors fail before implementation. Updated obsolete existing persona, size-value, scent-question, confirmation, cancellation and goodbye assertions. No new source-mirroring prompt tests.

Verification:

- `prompts-red.log`: expected failures for repeated/unrequested performance, explicit larger-count validation, and old persona wording; zero external HTTP.
- `brand-count-red.log`: expected cross-brand larger-count failure; zero external HTTP.
- `prompts-tests.log`: 277 prompt, grounding, repetition, scent, performance, appreciation, checkout and fallback tests passed; zero external HTTP.
- `prompts-brand-tests.log`: 62 brand preference and fallback/count tests passed; zero external HTTP.
- `prompts-final-tests.log`: 39 value-language, persona and fallback/count tests passed after quotation-note coordination; zero external HTTP.
- `git diff --check` on all owned files passed.

Coordinated the remaining mandatory size-value note instructions with the quotation agent, who updated `sales/value.py`; obsolete corresponding assertions are updated here. Root handles the combined full-suite check and isolated live model evaluation. Model voice remains prompt-guided; no hard truncation, model change, new rewrite call, saved payment/FAQ edit, migration, or commit was introduced.
