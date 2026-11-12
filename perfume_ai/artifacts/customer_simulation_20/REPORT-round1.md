# Perfume seller — 20 adaptive customer simulations: final report

Branch `opus`, uncommitted working tree on top of `ea27ef8`. Everything referenced here is under
`artifacts/customer_simulation_20/`. Per-defect detail (turn, expected vs actual, severity, root
cause, status) is in `findings.md`; this report summarises it.

## 1. Outcome

| Run | Code | Pass / 20 | Mean judge score |
|---|---|---|---|
| Baseline (`baseline/attempt-1`, graded in `rejudged-2`) | unmodified `ea27ef8` | 1 | 5.78 |
| Final verification 1 (`final/attempt-1`) | after B/R fixes | 6 | 7.54 |
| Final verification 2 (`final/attempt-2`) | + F01–F04 | 6 | 7.79 |

Against the baseline, final run 2 raised the mean score in 16 scenarios; 12 by more than the
judge's own noise (0.7). It was flat in S03 and S17 and lower in S02 (one critical false denial,
F16, fixed afterwards) and S20. Under the strict pass rule 14 of 20 still fail, mostly on one turn
each; section 8 lists every open cause. The two final runs passed different scenarios
(S01 S02 S05 S12 S16 S17 vs S01 S04 S08 S13 S16 S17), so single-run pass counts are noisy.

Two fixes (F15, F16) were made after the last full run. They are covered by unit tests and the full
suite and were replayed on S02 and S05, but they were not part of a full 20-scenario run (details in 8).

Tests: 2243 of 2243 pass on the final code. The unmodified code had 2176 passing; the 67 new ones are
regression tests, at least one per fixed defect (unfixed defects have none). Spend: USD 1.3495 of
the 3.00 cap, 3,058 API calls.

Pass rule, identical for every run (`scripts/simulate_customers.py::verdict_for`): no error; no
critical/high deterministic finding unless the judge dismissed it with a reason; no critical/high
judge issue; all seven judge scores ≥ 6; every scenario success criterion met. One high issue fails
the conversation, so "fail" often means "one bad turn", and the mean score is the better trend signal.

## 2. How it was run

- **Seller**: the real pipeline (`conversation_service.generate_event` → `router.route`), against a
  disposable local PostgreSQL copy of the store (251 products, settings, FAQs) created per run and
  dropped afterwards; each scenario in its own rolled-back transaction. Synthetic customer
  (كريم سامي, 01011223344). Outbound HTTP disabled (`requests.Session.request` patched), so no
  customer messaging or notifications leave the process; checkout, carts and orders run inside the
  test DB only. No payment integration exists in the code path.
- **Customer simulator**: the configured `OPENAI_MODEL` (gpt-6-luna), reasoning `none`,
  temperature 0.6, customer-only brief (it never sees success criteria or assertions).
- **Judge**: gpt-6-luna, reasoning `high`, rubric sha256 `9bed0237…` unchanged for every grading.
  It receives the transcript, internal state per turn, database facts for mentioned perfumes and the
  store-configured facts. Deterministic checks (`eval_harness/checks.py`, unchanged since before the
  baseline) run per turn and per conversation.
- **Budget**: one persistent ledger (`ledger.json`) for seller, simulator and judge; the maximum
  possible cost of each request is reserved before it is sent; cap USD 3.00, never raised.
- **Consistent scoring**: the baseline transcripts were re-graded with the exact judge used for both
  final runs (`baseline/rejudged-2`). Grading the same baseline transcripts twice moved the mean
  5.94 → 5.78 and single scenarios by up to 0.7 with identical verdicts: that is the judge's noise.

## 3. What was wrong, and what changed

IDs refer to `findings.md` (B = baseline, R = debug replays, F = first final run, D = store data).

**Choosing the next move.** "أنهي فيهم أنسب للشغل؟" after a comparison went to the gender and scent
gates and then to unrelated perfumes (B13), and again whenever the comparison named its perfumes in
Arabic, because `names_in` only reads Latin catalogue spellings (F03). Keyword objection detection
pre-empted messages that carried usable preferences (B12; "مش قادر تضمن" read as a price objection,
R12). Browsing questions went through the gates (R09). Changes: choices among perfumes on the table
(including "اختارلي انت") route to comparison, which uses the on-table perfumes when the message
names none; comparisons record what they compared so the next "أنهي فيهم؟" finds them whatever
script the reply used; soft objections no longer hijack actionable requests; browsing questions get a
grounded one-line catalogue overview computed from product rows.

**Questions that earn their place, and "مش عارف".** The fixed scent question was re-sent verbatim
after "مش عارف" (B01); Franco "rigali" was not understood, so "رجالي ولا حريمي؟" went out seven times
(B02); "معنديش رقم بديل" could not be accepted (B15, four identical asks, one abandoned order);
"هكمل بعدين" got the same missing-detail question three times (R14). Changes: one everyday-choice
question after "مش عارف" ("منعشة زي ريحة الصابون… ولا دافية ومسكرة شوية زي الفانيليا؟ ولو مش فارقة
أختارلك أنا"); a second "مش عارف"/"مش فارقة" delegates the pick; Franco gender words; a declined
alternate number is remembered and stored empty; a deferral is acknowledged instead of re-asked.

**Remembering accurately.** "مش عايز **أي** حاجة مسكرة" was parsed as wanting sweet and two sweet
perfumes were recommended (B03); "مش عايزها تقيلة، حاجة هادية" excluded both heavy and light (B04);
exclusions and budgets said on non-recommendation turns were never saved (B05); one flat preference
profile let the mother's floral profile overwrite the customer's woody one (B06); a hypothetical
"لو عايز 3" either stuck permanently (B14) or was ignored (F02); a perfume the customer singled out
was not the referent of "سعره كام؟" (R16). Changes: negation-scope fixes in the deterministic
scent parser; stated exclusions and budgets persisted on every turn without an extra model call;
per-recipient preference profiles saved and restored in `merge_preferences`; the hypothetical count
is read literally and reverted when it does not fit; the singled-out perfume is the referent.

**Sounding natural.** Prices rendered "1000.00 جنيه" (B10); "سوفاج 90" / "والـ50؟" quoted every size
(B09); the persona scripted "هسأل وأرد عليك" although nothing follows up (B11); praise of the seller
was ignored (B23). Changes: whole-number prices, bare sizes understood, the red line rewritten so
the model states known facts or offers the team, configured FAQ facts given to the model, praise
acknowledged briefly.

**Adapting to emotion.** Changes are small judgement rules in the persona (anger, jokes, praise,
"مش عارف", recipients) and a brief close after thanks following a declined transfer (B21 partly).

**Trustworthy recommendations and checkout.** Nine baseline turns got the non-answer "ماقدرش أأكد
المعلومة دي" because the claim checker misread negations and its fallback rendered nothing
(B07/B08); the price fallback ignored the budget (R13); the summary total did not say shipping was
extra (B16); "الإجمالي 810 بالشحن صح؟" and "أكدته خلاص" were answered "تقصد أنهي عطر وحجم؟" in four
runs (R17); a shipping-fee fragment made the general model re-ask details already given (F04);
"ومتوفر؟" went unanswered when the model's prose was only "متوفر" (F01); one of my own fixes
produced a wrong original-bottle price (R10) and was corrected. Changes: negation and scope fixes in
the claim checker; fallbacks render the perfume's supported tags or the store performance policy;
budget-aware fallback; labelled summary total plus the store's shipping answer; total-with-shipping
computed from the cart or order and a flat fee (never summed from a multi-amount shipping answer);
the customer's own cart and orders count as price evidence (read from the DB); availability stated
by code for quoted variants (in stock by construction); original-bottle questions quoted by code.

**Preserved.** Every price is still bound to a catalogue variant; the only new price evidence is the
customer's own cart/order rows. Scent descriptions are still limited to stored tags; stock and
original-bottle checks, the performance policy text, store policies and the summary-then-explicit-
confirmation order consent (`_summary_was_shown`, `create_order_in_db`) are unchanged. No model call
was added; the fixes are deterministic code except the persona rules (prompt 23,339 → 27,472
characters, +18%).

## 4. All 20 scenarios

Mean judge score in brackets. Bug IDs are defined in `findings.md`; "fixes" lists what changed for that
scenario; the last column is what still fails it.

| Scenario | Baseline | Bugs found | Fixes | Final 1 | Final 2 | Still failing on |
|---|---|---|---|---|---|---|
| S01 beginner | fail (6.7) | B01, B04, B23 | everyday-choice question, negation scope, praise | **pass** (10.0) | **pass** (9.9) | — |
| S02 enthusiast | fail (7.6) | B07, R01, F16 | claim-checker negation, notes scope, bottle negation | **pass** (8.9) | fail (6.3) | F16 false denial (fixed after; replay passed) |
| S03 total budget | fail (3.9) | B14, R02, F02, F17 | budget limitation, quantity revert, literal "لو عايز 3", total questions re-routed | fail (6.4) | fail (4.0) | F17 (classifier → order; offer feasibility claim; B21); one recipient's scent applied to both |
| S04 busy price | fail (9.3) | B09, B10, F01 | bare sizes, whole prices, availability by code | fail (9.4) | **pass** (9.9) | — |
| S05 premium | fail (6.6) | B07, B23, R10, R14, F15 | fallback descriptions, praise, original-bottle quotes, deferral | **pass** (8.4) | fail (8.7) | F15 (fixed after, not exercised by the replay) |
| S06 indecisive | fail (5.4) | B01, B05, B13, R05, R15, F05 | "مش عارف" choices, delegation → comparison | fail (7.7) | fail (7.1) | F05/F21 two-reply referent window |
| S07 gift | fail (5.6) | B11, B12, B25, F07, F18 | persona red line, FAQ facts, soft objections | fail (8.3) | fail (7.7) | F07, F18 (model claims/paraphrase) |
| S08 compare | fail (6.0) | B13, F03 | choice routing, compared-products marker | fail (4.1) | **pass** (9.9) | — |
| S09 unavailable | fail (2.6) | B19, B07 | handoff offer when the name cannot be placed | fail (3.9) | fail (5.9) | B19 (needs production backtest) |
| S10 dislikes | fail (8.0) | B03, R12, R16, F06 | negation scope, "مش قادر" scope, singled-out referent | fail (6.1) | fail (8.6) | F06 (musk as ingredient; complaint read as preference) |
| S11 all constraints | fail (3.1) | B15, B16, R08, R13, R17, F04 | declinable alt. phone, shipping in summary, total with shipping, checkout evidence | fail (7.4) | fail (7.1) | order-success "هيتواصل معاك" (owner decision); generic price defence |
| S12 short refs | fail (8.3) | B22, B24 | ordinal price heuristic scoped | **pass** (9.9) | fail (8.7) | F20 provider empty output (t5) |
| S13 mixed language | fail (0.7) | B02, F08 | Franco gender words | fail (8.9) | **pass** (9.7) | F08 seen in run 1 only |
| S14 mind change | fail (7.3) | B05, F09, F19 | stated exclusions/budgets persisted | fail (8.7) | fail (7.6) | F09, F19 |
| S15 self & mother | fail (5.0) | B06, R06 | per-recipient profiles | fail (7.9) | fail (8.4) | F11/F20 provider empty output (t7) |
| S16 skeptic | fail (5.3) | B07, B08, B20, R07 | policy fallback, FAQ repeat guard, duration parsing | **pass** (8.0) | **pass** (9.0) | — |
| S17 order complaint | **pass** (9.7) | — | — | **pass** (9.9) | **pass** (9.9) | — |
| S18 browser | fail (3.9) | B01, B21, R09, F10, D1 | browsing → FAQ + catalogue overview | fail (3.9) | fail (5.7) | F10 price range (D1 conflict), B21 |
| S19 ready buyer | fail (3.9) | B15, B18, F12 | declinable alt. phone | fail (6.0) | fail (6.1) | B18/F12 extractor; order-success promise |
| S20 pushy discount | fail (6.6) | B21, F13 | — (promotions flow pinned by tests) | fail (7.1) | fail (5.7) | B21/F13; checker false positives on bundle notes |

## 5. Before/after excerpts (actual output copied from saved transcripts)

None of these is illustrative; file and turn are given for each. "Before" is
`baseline/attempt-1` (unmodified code), "after" is the named later run.

**"مش عارف"** — S01 baseline t3–t4 vs final/attempt-1 t3
> C: مش عارف والله → S: بتحب الفريش، الخشبي، العود، ولا المسكر؟
> C: يا عم مش فاهم الكلام ده → S: بتحب الفريش، الخشبي، العود، ولا المسكر؟

> C: مش عارف والله → S: ولا يهمك. تحبها منعشة وخفيفة زي ريحة الصابون بعد الدش، ولا دافية ومسكرة شوية زي الفانيليا؟ ولو مش فارقة معاك أختارلك أنا.

**Franco** — S13 baseline t1–t3 vs final/attempt-1 t1
> C: hi, 3ayez perfume rigali lel sef, 7aga fresh w msh ghalya → S: محتاج برفان رجالي ولا حريمي؟
> C: rigali ya basha 😅 → S: محتاج برفان رجالي ولا حريمي؟

> C: (same opening) → S: K By Dolce ريحته فريش ومنعش وخشبي وفيه ريحة حمضيات. و212 NYC Man ريحته فريش ومنعش وفيه ريحة حمضيات ومسكي.

**Exclusions** — S10 baseline t1 vs final/attempt-1 t1
> C: عايز برفان رجالي، بس مش عايز أي حاجة مسكرة ولا فيها عود خالص → S: 212 Sexy man: ريحته مسكر وخشبي وفيه ريحة توابل… / Black XS: فيه ريحة توابل ومسكر وخشبي.

> → S: K By Dolce ريحته فريش ومنعش وخشبي وفيه ريحة حمضيات. و212 NYC Man ريحته فريش ومنعش وفيه ريحة حمضيات ومسكي.

**Recipient memory** — S15 baseline t5–t6 vs final/attempt-1 t3, t5
> C: طب نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟ → S: 212 Sexy Women: فيه ريحة زهور ومسكر… / L'Eau d'Issey…
> C: …طب فيه حاجة تانية ليا بنفس الستايل؟ → S: Sexy Graffiti من Escada… وBright Crystal من Versace…

> C: وكمان عايز حاجة لماما تكون هادية فيها ورد → S: لماما أرشّح Cassilia؛ طابعها زهري وفاكهي وخفيف.
> C: طب نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟ → S: كنت رشحتلك Aventus كأقرب اختيار لذوقك الخشبي، وفيه كمان ريحة فواكه ودخان.

**Non-answers** — S16 baseline t5–t6 vs final/attempt-1 t4
> C: طب والثبات بجد قد إيه؟ → S: ماقدرش أأكد المعلومة دي عن العطر. ممكن تحدد المواصفة اللي بتسأل عنها؟ (twice)

> C: …يعني زي الأصلي 100% ولا لأ؟ → S: لا، مش 100%؛ العطور تركيب ومستوحاة من الماركات العالمية، والتطابق بيوصل لـ95%. وبشكل عام ثباتها ممتاز وفوحانها قوي، لكن المدة بتختلف من عطر للتاني.

**Checkout** — S11 baseline t6–t9 vs final/attempt-1 t5
> C: كريم سامي، 01011223344، … → S: …ولسه ناقص رقم بديل.
> C: معنديش رقم بديل، ينفع الأساسي بس؟ → S: تقصد أنهي عطر وحجم؟ ممكن تكتبهم عشان أقولك السعر الحالي؟
> C: معنديش رقم بديل، الأساسي بس. كده تمام أكد الطلب؟ → S: …ولسه ناقص رقم بديل.

> C: فيه حاجة غلط، أنا بعتلك البيانات كلها فوق. الإجمالي 810 بالشحن، تمام أكد. → S: طلبك اتأكد يا فندم، ورقمه #1. … أيوه، الطلب 750 جنيه + الشحن 60 جنيه = 810 جنيه.

**Choosing among compared perfumes** — S08 baseline t2–t4 vs debug-after-final t2
> C: أنهي فيهم أنسب للشغل؟ → S: محتاج برفان رجالي ولا حريمي؟ → (scent question) → Eternity / Pasha de Cartier

> C: أنهي فيهم أنسب للشغل؟ → S: للشغل، Bleu de Chanel أنسب؛ مناسب للشغل والمكتب، وريحته خشبيّة فيها حمضيات وفريش. Sauvage مناسب أكتر للاستخدام اليومي والكاجوال، وEros للخروجات والسهر.

**Availability** — S04 final/attempt-1 t1 vs debug-after-final t1
> C: سوفاج 90 بكام؟ ومتوفر؟ → S: Sauvage / زجاجة الستور الخاصة بينا 90 ملي بـ1000 جنيه.

> → S: Sauvage / متوفر. / زجاجة الستور الخاصة بينا 90 ملي بـ1000 جنيه.

## 6. Regression tests and commands

`products/test_customer_simulation_fixes.py`: 65 tests in 22 classes, each naming the scenario and
turn of the defect it pins. Five existing test files were updated where they pinned behaviour the
task asked to change (decimal prices, the scripted follow-up promise, the gift scent question):
`products/tests.py`, `test_compact_quotes.py`, `test_price_name_once.py`,
`test_scent_recommendations.py`, `test_conversation1337.py`.

Commands, run from `perfume_ai/`. Redis is not running on this machine, so the suite uses an
in-memory-cache settings shim (`local_test_settings.py`, copied to this folder):

```
PYTHONPATH=artifacts/customer_simulation_20 python manage.py test products --keepdb --noinput --settings=local_test_settings
  → Ran 2243 tests … OK                       (tests-after-fixes-20.log, final code)
  → unmodified code: 2176 tests OK            (tests-original-code.log)
python manage.py test products.test_customer_simulation_fixes --keepdb --noinput --settings=local_test_settings
  → 67 tests OK
python scripts/simulate_customers.py --phase baseline --attempt rejudged-2 --rejudge artifacts/customer_simulation_20/baseline/attempt-1
python scripts/simulate_customers.py --phase final --attempt attempt-2 --workers 5
python eval_harness/summarize_simulation.py artifacts/customer_simulation_20/baseline/rejudged-2 \
    artifacts/customer_simulation_20/final/attempt-1 artifacts/customer_simulation_20/final/attempt-2 \
    --out artifacts/customer_simulation_20/comparison-baseline-vs-final.md
python eval_harness/ledger_totals.py --out artifacts/customer_simulation_20/ledger-totals.md
```

All 20 post-change suite logs (`tests-after-fixes-1…20.log`) are kept, red ones included. Runs 1, 2, 5
and 11 failed (22, 3, 5 and 2 failures) on my own changes, each fixed before the next green run.
`tests-before-fixes.log` (95 failures, 13 errors) mixed the new, not-yet-passing regression tests with
rows left by a killed run; the unmodified code run clean is `tests-original-code.log` (2176 OK).
Each new test was written against the observed failure. For F16 I showed it fails on the old code
(old pattern → "normal", new → none); for the others the failing branch follows from the test's
construction (e.g. a patched general model that raises if called), but I did not revert each fix to
re-run it.

## 7. Executions, API calls and spend

From `ledger.json` (`ledger-totals.md` has the per-phase/per-role table):

| | |
|---|---|
| Runs | 12 (pilot, baseline, 2 baseline re-gradings, 5 debug/replay runs, 2 final verifications, 1 post-final replay) |
| Simulated conversations | 119 = 2 pilot + 20 baseline + 4 claim-debug + 47 debug replays + 20 final-1 + 4 replay + 20 final-2 + 2 replay; plus 40 judge-only re-gradings of the baseline transcripts |
| API calls | 3,058: seller 2,155, simulator 748, judge 155; all `gpt-6-luna`; all completed, none interrupted |
| Tokens | 19,394,541 prompt (12,568,394 cached), 763,292 completion |
| Spend | **USD 1.3495 of 3.00**, 1.6505 unspent; baseline 0.3603 (run 0.2258 + re-gradings 0.0666 + 0.0680), debug replays 0.5264, finals 0.3497 (0.1902 + 0.1595), other 0.1131 |
| Latency | seller 1.98 s mean / 4.36 s p95 per call; simulator 1.34 s; judge 32.9 s |

Every failed or superseded attempt is kept: `pilot/`, `baseline/{attempt-1,rejudged,rejudged-2}`,
`debug-claims-before/`, `debug-after-fixes/{attempt-1,attempt-2,attempt-3-targeted}`,
`final/{attempt-1,attempt-2}`, `debug-after-final/`, `debug-after-final-2/`, each with transcripts,
per-turn traces, `config.json` (models, prompt hashes, git state), `catalog_facts.json`,
`store_facts.txt`, `summary.json` and `totals.json`, plus the run logs.

## 8. Remaining bugs, limitations and incomplete verification

**Open defects (not fixed):**
- **B19 / S09**: "Club de Nuit Intense" is placed on La Nuit ("La Nuit: متوفر."). The fix is in
  `_verify_placement` (it skips Latin spans), but it must be measured on real conversations with
  `eval_harness/backtest_placement.py`, which reads the production database. I did not run it without
  your approval.
- **B18 / F12 / S19**: the order extractor (LLM) drops an item ("2 سوفاج… وواحد بلو دي شانيل" → Sauvage
  only), re-asks a stated size and reverts a corrected quantity.
- **B21 / F13 / S18, S20, S03**: the promotions flow repeats its transfer offer every turn. Changing it
  broke `test_promotions`, because consent detection reads the latest offer, so I reverted it.
- **F05 / F21 / S06**: the two-reply "under discussion" window loses the first of two perfumes offered
  in separate replies, so "اختارلي انت" can bring a third perfume.
- **F06 / F19 / S10, S14**: musk excluded as a tag but not as an ingredient. Trait words inside a
  complaint or question ("212 VIB تقيل", "Pure XS فيه جلد؟") get saved as preferences.
- **F17 / S03**: a total question classified as an order with an empty cart gets "تحب تطلب أنهي عطر؟".
  An offer-feasibility claim with no number ("ينفع 3 من العرض") escapes the price checker.
  Different scents for two recipients in one purchase are merged.
- **F07, F09, F18**: model claims not in the data ("أدفى", "دافية مش حادة") and a paraphrased exchange
  condition pass the claim checker.
- **F08 / S13 run 1**: a comparison fallback repeats both descriptions with no pick.
- **F10 / S18**: no price range is ever given (see D1 below; the price checker cannot verify a range).
- **F11 / F20 / B24**: the provider occasionally returns an empty completion and that turn has no reply
  (S12 t5, S15 t7 in the finals). The harness meter treats it as an error by design, so S12 and S15 were
  verified only up to those turns.

**Owner decisions needed:**
- **Order-success text** says "فريق المبيعات هيتواصل معاك قريب". The only thing behind it is a dashboard
  notification. The judge and the `unsupported_promise` check both flag it, and it alone fails S11 and
  S19. It is the store's wording, so it is unchanged.
- **Absent-tag negatives**: the seller now answers "مش مسكر" when "sweet" is not among the stored tags
  (needed for S10). The judge treats that as unsupported (S07).
- **D1 store data conflicts**: business facts say both "حوالي 90%" and "95%" similarity, and "الاسعار
  بتبدأ من 450 لحد 1200" while active store bottles run 750–2000.

**Verification limits:**
- F15 and F16 came after the last full run: unit tests, the full suite and an S02/S05 replay only.
  The replayed S05 never sent the F15 question to the comparison branch, and the S02 customer did not
  repeat the F16 phrasing.
- One sample per scenario per run. The simulator adapts, so the "same" scenario is a different
  conversation each time, and the judge moves single scenarios by up to 0.7 on identical transcripts.
- Not exercised: Redis-backed paths (local in-memory cache everywhere), real channel delivery
  (outbound HTTP blocked), and the production database.
- Known checker false positives are listed in `findings.md`. Their logic was left unchanged so the
  baseline and final are scored alike; the judge dismisses some of them.
