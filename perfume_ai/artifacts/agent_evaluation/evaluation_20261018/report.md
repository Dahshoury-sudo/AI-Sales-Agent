# Perfume agent evaluation — 18 October 2026

**Score: 6.6/10. API spend: $0.852875 of $1.00.**

This is the completed quality score across all 63 conversations. The earlier 5.9/10 update was provisional, based on the first 33 graded conversations.

| Check | Result |
|---|---|
| Full Django regression suite | 1448/1448 passed in 30.942s; zero external HTTP calls |
| Live core scenarios | 42 completed |
| Live historical replay scenarios | 21 completed |
| Conversation turns | 185 completed; none skipped |
| Quality grading | All 63 scored; one malformed grading response retried |
| Historical backtests | Placement and praise passed; name routing incomplete because conversation 1106 is absent locally |
| Runtime errors | No unhandled scenario errors; 24 caught name-confirmation exceptions |
| API requests | 548 agent requests + 64 grading requests |

The main problems to address are:

1. **Name confirmation fails at runtime.** A literal JSON example has unescaped braces inside an f-string at `products/services/product_resolver.py:721`. `confirm_unplaced()` raises `ValueError` before calling the model. This happened 24 times during the run, causing fallback clarification replies. A separate reproduction confirmed the bug with zero API calls. The offline tests mock this confirmation function, leaving this path untested. See [runtime findings](runtime_findings.json) and [reproduction](resolver_reproduction.txt).
2. **The agent sometimes answers about the wrong perfume.** C2 compared Oud Wood when the customer requested Oudora. P2 interpreted Ambero as the Emporio Armani brand and repeated the same selection question after receiving contact details.
3. **Checkout context can be lost.** In CONV1075, a valid 100 ml original Dior Sauvage remained in the cart at EGP 815, but the fourth reply said no option met the requirements and asked the customer to change size or budget. No order was created in this scenario.
4. **Replies can become repetitive or irrelevant.** M3 repeated the identical two price lines across three turns. G1 answered a gift request with an unrelated statement about being unable to verify a perfume specification. Several Arabic-name replays repeatedly asked the customer to retype names without resolving the request.

The deterministic checker produced 18 raw flags across 11 scenarios: 12 repeated replies, two filler questions, two long answers, and two wrong-price flags. **Both wrong-price flags were false positives:** the checker read cart totals of EGP 1,319 and EGP 2,119 as item prices. The line prices and sums were correct. This correction was supplied to the judge; the original flags are preserved. Cart budget compliance was evaluated separately. Flags are not independent defect counts.

| Quality dimension | Score / 10 | Scenarios |
|---|---:|---:|
| sales effectiveness | 5.78 | 63 |
| objection handling | 6.00 | 22 |
| recommendation quality | 6.03 | 63 |
| constraint extraction | 6.21 | 63 |
| closing timing | 6.25 | 16 |
| trust | 6.32 | 63 |
| memory | 6.75 | 63 |
| confidence calibration | 6.79 | 63 |
| intent understanding | 6.97 | 63 |
| human feel | 7.03 | 63 |
| similarity accuracy | 7.40 | 5 |
| product accuracy | 7.98 | 63 |

The overall score uses the repository's 12-dimension rubric: average applicable dimensions within each conversation, then weight all 63 conversations equally. Core scenarios scored 7.35/10 and historical replays scored 5.07/10. The 100% offline test pass rate does not inflate the conversational quality score. Grading used `gpt-5-mini` with low reasoning effort and saved catalogue/store facts. This is one model-judged run, with manual review of the principal findings; it is not a statistical reliability estimate.

The tests used a disposable local PostgreSQL database and a snapshot of the 46-product Perfamix catalogue. Both temporary test databases were confirmed removed. Customer delivery was blocked, and application source files were unchanged. The source revision was `aa7df41fd548357597d5582703c258268cb18284`.

The historical name-routing backtest scanned 3,070 messages across six stores, but could not check conversation 1106 or its dependent store-specific abstention cases. The separate mocked regression tests for conversation 1106 passed. Placement backtesting also reported missing historical examples and two documented misses, so its passing status does not imply complete placement accuracy.

Cost: $0.595464 for live conversations and $0.257410 for grading, including the malformed attempt. Token prices were verified against the official [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and [GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini) documentation. Taxes and credits are excluded.

Evidence: [summary](summary.json), [all scenario scores](scenario_scores.csv), [offline test log](offline_tests.log), [live transcripts](live/runs.json), [grading verdicts](final/verdicts.json), [cumulative usage ledger](final/usage.json), [checker corrections](judge/finding_adjudications.json).
