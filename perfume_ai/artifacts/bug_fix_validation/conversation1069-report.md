Conversation 1069 — completed 2026-10-14

The recommendation → first-product price → “طب والتاني” regression selects Afnan 9PM and quotes its verified variants. The incident fixture uses 508 EGP for 50 ml, 1,077 EGP for 90 ml, and 881 EGP for the original 100 ml bottle. Unsupported 642/990 EGP drafts are rejected, and a recorded incorrect quote is acknowledged without changing checkout prices. Runtime quotations continue to use the current catalogue.

The resumed changes complete the numeric-name checks:

- Price wording such as “غالي” and packaging phrases such as “زجاجة البراند” no longer become part of the perfume name.
- The full message “عايز 9 بي ام ٩٠ ملي زجاجة البراند” resolves the 90 ml variant even when extraction omits the literal source span. Verification retains the original text and does not confirm an order.
- Price objections and disputes ask for clarification when a numeric name is ambiguous. A model guess cannot select a flanker.
- An inactive base perfume remains unavailable even when a related flanker is active.
- The handoff regression now simulates its model response explicitly.

| Verification | Result |
| --- | --- |
| Focused conversation 1069 regressions | 20 passed |
| Full Django suite on local PostgreSQL | 1,448 passed in 27.182 seconds |
| Unmocked external HTTP attempts | 0 in both final runs |
| Django system checks | Passed |
| Evaluation scenario | CONV1069 registered with five turns; IDs unique |
| git diff --check | Passed |

Both test runs used unique disposable PostgreSQL databases with external HTTP blocked; Django removed those databases afterward. Evidence: [focused log](conversation1069-resumed-focused-alias-fix.log) and [full log](conversation1069-resumed-full.log). These are offline regressions with simulated providers, not a paid live-model evaluation.

The original implementation is in commit `784d0a6`. The resumed changes are in the existing naming, product-information and objection services and regression tests. No migrations, dependencies, directories or public interfaces were added. Source changes remain local and uncommitted as of this verification.
