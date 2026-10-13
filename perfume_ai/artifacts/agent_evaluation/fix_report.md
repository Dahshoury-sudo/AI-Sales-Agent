# Agent evaluation fixes

Implemented within the existing project structure. No application directories, services, models, migrations, dependencies, or public HTTP interfaces were added or reorganized. Existing JSON preference/cart state gained optional fields, and the existing evaluation runner gained ordered scenario selection and budget-preserving reruns.

## Results

| Check | Result |
| --- | --- |
| Full Django suite, local PostgreSQL | **1,428 passed**, 26.475 seconds |
| Main scenarios represented by their latest replay | **40 scenarios / 67 turns** |
| Automated findings in those latest results | **0** |
| Additional check of all latest replies using the final claim validator | **0 findings** |
| Live executions across implementation iterations | **50 scenarios / 91 turns**, including reruns |
| Completed model requests across all iterations | **241** |
| Total live cost, including all iterations | **$0.27696325 / $0.30** |
| Unresolved request reservations | **$0** |

The final regression suite tests the final implementation. Live results combine the latest observations for each scenario: the original 40-case fix run followed by focused reruns when a relevant change was made. This is not a single fresh 40-case run of the final source, a statistical reliability estimate, or a new holistic conversational score. No paid judge was used.

## Behavior changed

- **P2:** checkout checks the customer's original wording independently of the order extractor's proposed name, including inactive identities. Ambero stays pending as unavailable; no other perfume enters the cart. New contact details receive distinct acknowledgements.
- **P3/F1:** pending quantities and sizes survive follow-ups. Demonstratives and ordinals resolve against the current offered list, including when the extractor omits its source span. F1 correctly preserves two 90ml bottles, calculates 1,400 EGP, and warns about the 900 EGP budget.
- **F4:** an original-liquid requirement persists separately from packaging and brand exclusions. The agent explains that the store sells blends, stops recommendations that contradict that requirement, and resumes only after an explicit change of preference.
- **F2/R3/R4/R5:** product-specific note and performance checks can trigger one correction shared with repetition handling. A second unsupported draft falls back to recorded facts. Rarity is not inferred from the niche category, and partial requested-note matches are disclosed.
- **D1/S4:** ordinary Egyptian “حلو” no longer creates a sweetness preference. Vague requests get a discovery question. An unknown similarity profile gets one targeted clarification; the answer can supply the profile on the following turn.
- **M2/M4:** explicit preference reversals remove continuity preference for earlier recommendations. Inactive products can still supply recorded scent and gender reference information.
- **O4/O5/X3:** price-gap responses compare real offers and recorded performance; deferrals receive a brief acknowledgement; unsupported two-day longevity is not presented as attainable by increasing the budget.
- **Follow-up state:** assistant-suggested sizes cannot become customer choices, and a bare budget following recommendations does not start an empty checkout.

## Evaluation changes

The harness now records cart contents/pending lines, checks scenario-specific checkout identity and quantity, and retains product-specific claim evidence. Price checks distinguish unit prices from quantity subtotals and accept correctly rounded per-millilitre values. Guarantee checks account for negation. Stale availability assumptions in C2/P2/P3/M4 were corrected.

The runner supports `--scenario-ids` for ordered selection and `--rerun-ids` with `--resume-from`. Reruns replace the selected observations in a fresh output directory while retaining all prior spend. Customer delivery remained disabled, scenario transactions rolled back, and disposable local evaluation databases were cleaned up.

## Evidence

- [Final regression log](fix_offline_tests.log)
- [Latest 40 scenario transcripts and state](fix_live_complete/runs.json)
- [Complete cumulative usage ledger](fix_live_complete/usage.json)
- [Final claim recheck](fix_live_complete/claim_recheck.json)
- Earlier iterations remain in `fix_live`, `fix_live_verified`, and `fix_live_final`, including the failures that led to additional fixes.

The claim validator covers explicit claims in a bounded vocabulary; arbitrary prose and recommendation quality still need conversational review. Its factual fallback deliberately supplies less descriptive detail when the model cannot produce a supported answer. Existing disagreements in store-configured payment/similarity wording were left unchanged.
