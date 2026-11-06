# Task 2: fixed replies and historical compatibility

Implemented short natural Egyptian wording for gender/scent/occasion questions, product and contact clarification, stock/size/bottle issues, order confirmation/cancellation, goodbye, and deferred decisions. Preserved all displayed variant facts, quantities, required contact fields, budget warnings, consent checks, checkout fingerprints and configured payment instructions. Full checkout summaries remain complete, including the existing total marker. The existing handoff confirmation was already concise and remains unchanged.

Historical compatibility accepts both old and revised gender questions (including multiple recipients), scent questions, unresolved-product questions and contact-detail requests. The four scent options retain their fresh/woody/oud/sweet order. Short answers and delegated scent choices cannot become occasion choices or transfer approvals. Delivered-message checks and newer question/human-message boundaries remain intact.

Root additionally authorized promotion_service.py: shortened the limitation while retaining explicit inability to execute promotional orders. Both historical and revised limitations preserve declined transfers. Objection instructions now combine acknowledgment and useful answer in 1–2 short sentences, with optional selling/risk reduction only when necessary. Recommendation recovery uses the shared short scent-reason helper and honors requested counts; performance appears once when requested.

Files changed:

- products/services/order_service.py
- products/services/router.py
- products/services/objection_service.py
- products/services/promotion_service.py (scope added by root)
- products/services/sales/gender.py
- products/services/sales/scent_answers.py
- products/services/sales/occasion_preferences.py
- products/test_response_style.py (15 behavior/compatibility tests)

Verification:

- Test-first compatibility red: 12 tests, 25 expected failed subtests, zero HTTP; fixed-red-tests.log.
- Promotion-history red: one test failed for the revised limitation/decline history, zero HTTP; fixed-promotion-red-tests.log.
- Recommendation recovery red: two expected failing tests, zero HTTP; fixed-fallback-red-tests.log. Green run: 18 tests passed (15 new + existing reply-integrity tests), zero HTTP; fixed-fallback-tests.log.
- Final affected run: **374 tests passed, zero unexpected HTTP**, disposable local PostgreSQL; fixed-tests.log. Coverage includes new tests, gender, scent interpretation, occasions, bottle wording, handoff/attention, promotions, predeploy and bug regressions, plus existing cart cancellation/order guards/payment/summary/budget/order-edit classes.
- git diff --check passed for all changed production files; only Git's existing LF/CRLF warning was printed.

Final command:

```powershell
.venv/Scripts/python.exe scripts/run_offline_tests.py products.test_response_style products.test_gender_first products.test_scent_interpretation products.test_occasions products.test_bottle_wording products.test_handoff products.test_handoff_attention products.test_promotions products.test_predeploy products.test_bug_regressions products.tests.CartCancellationTests products.tests.OrderGuardTests products.tests.PerStorePaymentInstructionsTests products.tests.ScriptedRepliesSurviveSanitizingTests products.tests.BudgetWarningReachesTheCustomerTests products.tests.OverBudgetLineTests products.tests.OrderEditNotCancelTests --log artifacts/response_style/fixed-tests.log
```

No outstanding failures in this affected run. Earlier runs exposed stale copy assertions in other agents' test files; root/prompt agent updated them, and the final combined run passed. A temporary indentation error in the unavailable-variant reply was caught by the checkout test and corrected before the final run. Full products-suite verification and live evaluation remain with root. No commits, migrations, saved FAQ/payment edits or ../.gitignore edits were made by this task.
