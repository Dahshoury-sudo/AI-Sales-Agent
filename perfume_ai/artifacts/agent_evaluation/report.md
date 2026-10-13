**Agent evaluation: 6.8/10. Main suite completed within the $0.30 model budget.**

The agent usually handles prices, simple recommendations, misspelled names and checkout follow-ups competently. It still makes serious product-identity and authenticity mistakes, and its objection handling and recommendation dialogue need work.

This is a Codex assessment of all **40 main scenarios / 67 customer turns**, using the calibration and dimensions in the repository's [judge rubric](../../eval_harness/judge.py). Each scenario receives one holistic outcome score; the equal-weight mean is **6.75**, displayed as **6.8/10**. The [individual scores and reasons](scenario_scores.json) are reviewable. No additional paid judge was called. This is one sampled run, not a statistical reliability estimate; offline test counts do not inflate the conversational score.

| Evaluation | Result |
| --- | --- |
| Django regression suite, local PostgreSQL | **1,401 passed**, zero failures/errors; 26.596 seconds |
| Main live conversation suite | **40/40 scenarios, 67/67 turns**, no agent crashes in the valid run |
| Additional historical live replays | **1/21 complete**; 20 stopped or not completed by the budget guard |
| Chat-client checks | **3/3 passed** |
| Dashboard workflows | **13 groups passed** |
| Dashboard layout | Zero overflow or browser runtime errors; 60 screenshots |
| Dashboard accessibility | Zero violations reported by the existing checks |
| Historical placement/praise backtests | Available-corpus checks passed; placement still documents two known misses and missing reference cases |
| Historical name-routing backtest | Required conversation 1106 is absent, so that check could not be validated |

**Spend: $0.28078685 from returned token usage, plus a $0.01007640 maximum allowance for one interrupted request = $0.29086325 conservatively accounted.** This includes the invalidated harness attempt, its rerun, and partial historical replays. The full 21-case historical extension did not fit the budget; it is not represented as completed or included in the score.

| Model | Completed calls | Prompt tokens | Cached prompt tokens | Completion tokens | Cost, including any interrupted-call allowance |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-4.1-mini | 220 | 1,274,937 | 945,408 | 13,017 | $0.25725600 |
| gpt-5-mini | 42 | 88,165 | 55,040 | 11,975 | $0.03360725 |

The configured model choices were used. Prices were verified against official [gpt-4.1-mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and [gpt-5-mini](https://developers.openai.com/api/docs/models/gpt-5-mini) pages: respectively $0.40/$0.10/$1.60 and $0.25/$0.025/$2.00 per million input/cached-input/output tokens. The [usage ledger](live_resume/usage.json) contains every attempted request's accounting. This is usage-based accounting, not an invoice reconciliation.

**The most important failures are behavioral, despite the passing regression suite.**

1. **Wrong product in checkout — P2, 2/10.** The customer asks for “امبيرو 90 ملي”; the agent adds **Stronger With You Intensely** and carries it through the order summary. Ambero is inactive in this snapshot, which calls for clarification or an explicitly offered alternative. Silently choosing a different fragrance is a serious failure. This scenario stops before final order confirmation. [Transcript](live/P2.json)
2. **Misleading authenticity claim — F4, 2/10.** The customer rejects blends and requests original brands. The agent replies “عطر رجالي أصلي من البراندات” while quoting blended products. The store's configured facts explicitly say the liquid is always a blend, even when an original-style bottle is available. The last two replies also repeat the same bilingual catalogue list. [Transcript](live_resume/F4.json), [store facts](live_resume/store_facts.txt)
3. **Invented scent notes — F2, 5/10.** Both Stronger With You Intensely and Absolutely are described as containing oud. Neither product's recorded notes include oud. The later Eros availability and pricing replies are correct. [Transcript and catalogue evidence](live/F2.json)
4. **Weak discovery and matching — D1, 4/10; S4, 3/10.** A completely vague request immediately receives two recommendations. A Khamrah-style request is led toward Oud Wood without explaining the mismatch with the requested gourmand profile. [Discovery](live/D1.json), [similarity](live/S4.json)
5. **Poor objection handling and repetition — O4/O5/P3, 4/10 each.** Higher price is justified with an unsupported longevity comparison; “I'll think about it” receives another pitch; a quantity change receives the exact same unresolved-product reply. [Price objection](live/O4.json), [deferral](live/O5.json), [quantity change](live/P3.json)

| Main-suite category | Scenarios | Mean /10 |
| --- | ---: | ---: |
| Purchase | 4 | 5.50 |
| Objection handling | 5 | 5.60 |
| Discovery | 3 | 6.67 |
| Memory | 4 | 6.75 |
| Similarity | 4 | 6.75 |
| Recommendation | 5 | 7.00 |
| Adversarial / regression cases | 8 | 7.12 |
| Comparison | 3 | 7.33 |
| Customer service | 2 | 8.00 |
| Gifts | 2 | 8.00 |

The strongest cases included resolving all three misspelled product names in X2, maintaining the multiple-product price references in F3, asking a useful diagnostic question after rejection in CS2, and handling gibberish cleanly in X5. F1 correctly calculated a two-item total and warned when it exceeded the customer's budget. The valid M1 rerun retained the accumulated budget, similarity reference, uncommon preference and longevity priority.

**Evaluation corrections and limits:** the first-pass checks produced five flags. Three were false positives: G1 explicitly says it cannot guarantee satisfaction, and the two F1 price flags mistake a two-unit subtotal for a unit price. The repository's offline second pass reports repetition in P3 and F4. Manual review found the semantic problems above that the automated checks missed. Both the [raw transcripts](live_resume/runs.json) and [second-pass findings](automated_findings.json) are retained.

The snapshot contains 46 products, of which 40 are active and sellable. C2, P2, P3 and M4 have stale assumptions about Ambero/Oudora availability. Scores use current catalogue facts: C2's unavailability answer is correct; P2's substitution and P3's unhelpful repetition remain failures. Store configuration also contains differing similarity percentages and payment descriptions, so configured statements were not automatically treated as hallucinations.

A Windows file-sharing error invalidated the first M1 attempt. It was excluded and rerun after fixing the harness's file replacement handling. The interruption allowance above preserves the budget conservatively. Automatic API retries were disabled; output limits were 2,048 tokens for gpt-4.1-mini and 8,192 for gpt-5-mini, and **none of the 262 returned responses was truncated**.

The source catalogue was read in a read-only transaction and copied into disposable local PostgreSQL databases. Scenario transactions rolled back, customer delivery was disabled, and both temporary databases and the preview server were cleaned up. The offline tests blocked live HTTP. [Machine-readable summary](summary.json), [Django test log](offline_tests.log), [UI reports](ui/initial-report.json), [budgeted runner](../../scripts/evaluate_agent_budgeted.py).
