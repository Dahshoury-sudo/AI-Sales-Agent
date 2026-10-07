# Follow-up bug review — 2026-10-07

Six additional failure cases reproduced against the current implementation. These are controlled handler-level reproductions with injected extractor/provider outcomes, not newly observed live customer incidents. Every scenario ran in a rollback transaction in a disposable PostgreSQL database, with external HTTP blocked. AIAgent and application code were not changed by this review.

Reproduction: `python scripts/review_remaining_bugs.py`. See [recorded results](results.json) and [diagnostic script](../../scripts/review_remaining_bugs.py). All six cases reproduced.

| Priority | Case | Failure and observed result |
|---|---|---|
| High | R02 | A stale admin save reopens a cancelled order; cancelling it again raises stock from the correct 2 bottles to 3. |
| High | R01 | An unchanged cart containing 50ml and 90ml of the same perfume doubles from one of each to two of each when extraction omits line IDs. The quoted total rises from 1,300 to 2,600. |
| High | R04 | A repeated comment event generates and attempts to send two replies during an active human handoff. Failed private delivery is nevertheless saved as `sent` and enters model history. |
| Medium | R03 | Asking to confirm after cancellation produces “تم تأكيد طلبك بنجاح” while the order remains cancelled. No replacement order is created. |
| Medium | R05 | A message containing contact numbers plus “مين المصنع؟” receives only the formulation answer; both numbers remain missing from the cart. |
| Medium | R06 | A message dropped after the maximum rate-limit deferrals stays pending, then is processed ahead of a later message. |

## R02 — stale admin saves bypass final cancellation

An admin request loads a pending order. Another request cancels it and restores stock. The first request then saves a note without changing the status field in its form. `OrderAdmin.save_model` takes the ordinary `super().save_model()` branch and writes the old `pending` status back. A second cancellation restores stock again.

The reproduction executes that exact save interleaving with the real admin and order-status service; it does not require an invalid status submitted by a user. The existing cancellation tests exercise the central service, but this save path bypasses it.

Suggested local fix: lock and reload the order for every admin save, retain the current persisted status unless an explicit valid transition is requested, and prevent the later model save from overwriting that transition. Add a regression for the stale-note interleaving.

## R01 — missing line IDs inflate multi-size carts

`_merge_lines` falls back to matching only the product name. When two saved lines have the same name, neither matches uniquely, so a full extractor response without IDs appends two new lines. The subsequent variant aggregation adds their quantities to the existing lines. The extractor prompt requests IDs, but its example output schema omits them.

Suggested local fix: use an unambiguous name/size/bottle-type match before creating a new line, include IDs in the output schema, and ask for clarification when an edit is genuinely ambiguous. Cover unchanged multi-size carts as well as quantity and size changes.

## R04 — comment processing bypasses message safeguards

`process_comment_task` still generates and sends directly. It neither deduplicates the comment ID nor respects the conversation's human-handoff state. It also saves the assistant reply with the default `sent` status before attempting private delivery, and ignores a `None` provider result.

The reproduction invokes the same comment ID twice with handoff active and a failed private-send result: two public sends, two private attempts, and two assistant messages marked sent. This affects the comment path separately from the previously corrected direct-message path.

Suggested local fix: apply the existing receipt, conversation locking and delivery-state handling to comments, with separate outcomes for public and private sends. Do not generate an automated sales response while a human owns the thread.

## R03 — cancelled orders receive a success message

The duplicate-confirmation shortcut finds the latest order with a checkout token without checking its current status. `_order_success` always claims confirmation, even when the record is cancelled.

Suggested local fix: return the actual saved status for repeat confirmations. A cancelled order must stay cancelled; starting a replacement requires a new cart and reviewed quote.

## R05 — formulation shortcut still intercepts checkout updates

The special `نوع السائل` / `مصنع` branch returns before the checkout-detail check. Consequently, the combined message “موبايلي 01000000000 والبديل 01100000000. ومين المصنع؟” never reaches checkout. This is a remaining variant of the earlier FAQ/contact bug.

Suggested local fix: process recognized checkout updates first and append the factual formulation answer while preserving the quote linkage, as already done for shipping FAQs.

## R06 — rate-limited messages are not actually dropped

`_defer_or_drop` logs a drop and returns at the maximum deferral count without changing the durable event. `pending_through` later selects that still-pending row when a newer event arrives. The old request is then generated and delivered despite the earlier drop decision.

Suggested local fix: give exhausted events a terminal state and exclude it from the pending queue; retain the receipt for diagnostics. Decide whether the customer/staff should be informed of the rejected request.

These fixes can stay in the current service/task/admin structure. Prioritize R02, R01 and R04. The passing original suite and earlier live replays remain useful evidence for their covered cases; they did not cover these additional interleavings and paths.
