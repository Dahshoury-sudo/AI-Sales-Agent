# Price-name-once independent review

Status: final review complete. Ready to integrate; no remaining Critical or Important findings in the reviewed scope.

Scope: actual uncommitted changes from HEAD `c12314a`, including `products/services/price_reply.py` and `products/test_price_name_once.py`. Read-only source review; reviewer writes are limited to this report and the approved verification log. Unrelated `../.gitignore` excluded.

## Strengths

- `render_quotes` groups by product identity in first-seen order and preserves each product's variant order, bottle labels, decimal totals, quantities, and per-size budget warnings. The interleaved A/B/A tests cover the intended behavior directly.
- Composition keeps descriptive prose outside quotation evidence. `quotation_prose` removes only the final exact rows under their corresponding unique product heading, so a generated copy under another product remains a price claim. The new adversarial regression exercises that trust boundary.
- Product-info and comparison composition reuse the existing grounding retry and retain the `(reply, context)` interface. The implementation does not add a rewriting API call.
- Test coverage includes overlapping names, numeric names, mixed notes, exact quotation evidence, reappend behavior, sanitizing, successful retries, failed retries, and recommendation totals.

## Findings reported during review

All four findings below were reproduced or verified through the cited caller path, accepted, fixed, and re-reviewed. The original line references document where each issue was found; resolution references point to the final code. There are no open Critical, Important, or Minor findings.

### Important, resolved: unsupported mixed questions enter the narrow facts handler

Files: `products/services/price_reply.py:40`, `products/services/comparison_service.py:133`, `products/services/product_info.py:1220`.

`supported_facts_question` is a blacklist. Both `قارن Alpha وBeta في السعر وأنهي رجالي وأنهي حريمي` and `قارن Alpha وBeta في السعر والتقييمات` return true, while `requested_dimensions` returns only `{'price'}`. Both callers can therefore return `requested_facts` immediately, omitting the other requested answer.

Fix: accept only supported questions after removing recognized product names and supported request vocabulary; route remaining unknown details through the normal response path. Retain a regression covering gender and a previously unlisted detail.

Resolution: `price_reply.py:57` now uses a positive residual-word gate, supplied with the resolved products by both callers. Unsupported gender/review requests remain on the generation path. Narrow duration grammar, a single matched alias token, and the established plural typo preserve the prior deterministic cases without removing arbitrary mixed-detail spans. The original reproductions and the three compatibility regressions pass.

### Important, resolved: authenticity answers disappear in composed price replies

Files: `products/services/router.py:205`, `products/services/price_reply.py:157`, `products/services/price_reply.py:20`.

Failed-retry reproduction: request `Alpha بكام وهل أصلي ولا تركيب؟`, generated draft `Alpha عطر أصلي.`. The claim validator correctly reports authenticity. The new composed fallback executes before the pre-existing formulation recovery and returns only the heading and prices because its facts builder handles notes, performance, occasion, and season. The requested authenticity answer is lost.

Normal-path reproduction: `_price_only('Alpha بكام واوريجنال ولا تركيب؟', ['Alpha'])` returns true. Composing the correct answer `كل العطور تركيب من عندنا.` discards that answer and displays prices alone because the classifier allows all of `اوريجنال`, `ولا`, and `تركيب`.

Fix: preserve formulation/bottle explanations in fallback, and distinguish a price request for a specified bottle type from a question contrasting original versus compounded liquid. Keep ordinary price-only requests concise.

Resolution: `_price_only` preserves original-versus-compounded questions; `fallback_price_reply` at `price_reply.py:212` retains the formulation answer along with requested catalogue facts. Final redundant-clause filtering at `price_reply.py:77` excludes monetary and formulation prose. The exact late regression `زجاجة اوريجنال بس تركيب` remains in the response, while observed availability/size-only forms such as `بحجمين` and `بحجم واحد` are removed. Both normal and failed-retry authenticity regressions pass.

### Important, resolved: unbounded name matches break legitimate descriptions

Files: `products/services/price_reply.py:84`, `products/services/price_reply.py:106`, `products/services/router.py:209`.

Reproduction: perfume `Pi`, request `Pi بكام ومكوناته إيه؟`, grounded draft `Pi فيه Pink Pepper.`. The regex matches `Pi` both as the name and inside `Pink`, marks the sentence ambiguous, and produces a heading/price section followed by the unchanged name-bearing sentence. Catalogue fallback with `top_notes='Pink Pepper'` produces the same duplicate layout. At the time found, the router accepted this fallback after checking prices alone.

Fix: use boundary-aware name matching while preserving longest-name precedence and Arabic conjunction handling. Validate fallback composition and descriptive claims as well as prices.

Resolution: `_name_pattern` at `price_reply.py:20` prevents Latin substring matches while retaining Arabic conjunctions and longest-name precedence. `Pi فيه Pink Pepper` now appears below a single `Pi` heading. `router.py:207` checks claims, quotation prices, and composition before accepting fallback. Numeric, overlapping-name, short-name, and fallback regressions pass.

### Important, resolved: recommendation composition bypasses layout validation

Files: `products/services/ai/scent_recommendation.py:207`, `products/services/router.py:886`.

Composition runs after the recommendation validator/retry loop. The router's recommendation branch returns that result directly, without `_rephrased`. Valid drafts such as `أنا أرشحلك Alpha طابعه فريش.` or `أقرب اختيار لطلبك هو Alpha لأنه فريش.` use prefixes outside the composer's accepted grammar. They become an `Alpha` heading and prices followed by the unchanged sentence naming `Alpha` again, with `ambiguous_prose=True`. No layout check runs afterward. Multi-name sentences expose the same path.

Fix: validate composition inside the existing recommendation attempts, or apply a deterministic grounded fallback afterward. Preserve selected IDs, reference handling, brand disclosure, eligibility, and total-budget evidence. No additional rewriting call is necessary.

Resolution: `_compose_selection` at `scent_recommendation.py:90` is checked inside the existing two-attempt loop at line 203. Unsupported embedded-name grammar reaches the established deterministic fallback instead of shipping duplicate names. The regression verifies one heading, preserved recommendation reason, grounding, and at most two calls. Brand-disclosure and exact-total coverage also pass.

## Final validation

- Read the requirements, core changed modules, relevant callers/validators, and focused tests.
- Ran isolated Python reproductions of the unsupported-dimension gate, authenticity fallback, authenticity price-only classification, and `Pi`/`Pink Pepper` composition. These used Django test settings without database operations or external HTTP.
- Reviewer-run targeted suite: **48 tests passed, zero unexpected HTTP**, recorded in `reviewer-tests.log`.
- Final implementation-focused suite: **233 tests passed, zero unexpected HTTP**, confirmed from `composition-tests.log`.
- Final root suite after the last formulation/count edit: **2,005 tests passed, zero unexpected HTTP**, confirmed from `full-tests-release.log` (41.270 seconds, disposable local PostgreSQL).
- Inspected updated legacy assertions: they retain reasons, brand disclosure, exact prices, selected-product checks, and model-call expectations while accommodating the approved heading layout.
- Inspected the five recorded live scenarios, including the two-turn size follow-up: names appear once, requested notes/performance survive, and displayed prices match stored quotation evidence. Earlier evaluator price flags came from bottle-label inference across prose; the actual quotation values were correct. The last observed redundant availability wording is covered by the exact final regression. No additional live run was needed for that bounded wording change.
- Root reported `git diff --check` passing, with only the existing CRLF notice. No schema, checkout-flow, commit, or deployment change is part of this implementation.

## Declined to judge

- Unrelated parent-directory `.gitignore` change: outside the authorized review scope.
- The pre-existing bounded-vocabulary claim validator's general semantic limits, including its own unbounded `Pi`/`Pink Pepper` matches: not introduced by this change; the new composition defect above is reviewed separately.
- Production deployment and actual customer delivery: not exercised by this read-only review. The recorded isolated live samples were inspected as described above.
- Broad renderer or conversation architecture redesign: unnecessary to assess the specified heading and completeness requirements.

## Readiness

**Ready to integrate: Yes.** All four Important findings and the related compatibility/filter regressions are resolved. The final implementation preserves product attribution and exact quotation evidence, and the final focused and full suites are green. No remaining blocker was found within the approved scope.
