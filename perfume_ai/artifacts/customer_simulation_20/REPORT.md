# Perfume seller — 20 adaptive customer simulations: final report (round 2)

Branch `opus`, uncommitted working tree on top of `ea27ef8`; nothing was committed. Everything
referenced here is under `artifacts/customer_simulation_20/`. Per-defect detail (scenario, turn,
change) is in `findings.md`; the round-1 report is kept unchanged as `REPORT-round1.md`.

Work stopped on the owner's instruction during full run 7.

## 1. Status: the done criterion is not met

The criterion was two consecutive full 20-scenario runs with at least 18/20 passing, no regression, all
tests green.

- **Best full run: 17/20** (run 6, mean judge score 9.07). No full run reached 18.
- Run 6's three failures (S11, S16, S20) were then fixed. S16 and S20 passed their replays. S11 met all
  four of its criteria in its last replay and still failed, on one turn that needs an owner decision
  (§6).
- **Run 7**, on the final code, was stopped at the owner's request after 2 of 20 scenarios (both
  passed). It is not a result.
- So the final code has **no completed full run**. It is verified by the test suite and by replays of
  the scenarios that had failed (§5).
- All 2,386 tests pass on the final code, and no test reaches the API.

## 2. Outcome

| Run | Code | Pass / 20 | Mean judge score |
|---|---|---|---|
| Baseline (`baseline/rejudged-2`) | unmodified `ea27ef8` | 1 | 5.78 |
| Round-1 final 1 / final 2 | end of round 1 | 6 / 6 | 7.54 / 7.79 |
| Round-2 full run 1 | batch-1 fixes | 10 | 8.29 |
| Round-2 full run 2 | + batch 2–3 fixes | 10 | 8.68 |
| Round-2 full run 3 | + run-2 fixes | 11 | 8.86 |
| Round-2 full run 4 | + run-3 fixes | 14 | 8.82 |
| Round-2 full run 5 | + run-4 fixes | 13 | 8.82 |
| Round-2 full run 6 | + run-5 and batch-4 fixes | **17** | **9.07** |
| Round-2 full run 7 | + batch-5 fixes (final code) | stopped at 2/20 on request | — |

Replays between full runs (failed scenarios only):

| Replay | Scenarios | Passed |
|---|---|---|
| Batch 1 / 2 / 3 | 7 / 10 / 7 | 1 / 3 / 2 |
| Batch 4, attempt 1 | run 5's seven failures + S11 | 5 of 8 |
| Batch 4, attempt 2 | S03, S09, S11 | stopped at turn 2 by the OpenAI project spend limit; no verdicts |
| Batch 4, attempt 3 | S03, S09, S11 | 2 of 3 (S03 9.7, S11 9.3) |
| Batch 4, attempt 4 | S09 | 1 of 1 (9.9) |
| Batch 5, attempt 1 | S11, S16, S20 | 2 of 3 (S16 9.3, S20 8.7) |
| Batch 5, attempt 2 | S11 | 0 of 1 (all criteria met; §6) |

How to read the scores:
- The mean rose from 7.79 at the end of round 1 to 9.07 in run 6.
- One high issue fails a whole conversation, so pass counts move more than means.
- The baseline was graded with the round-1 rubric (sha `9bed0237…`); round 2 uses `4b88a27f…`
  (§3). The baseline was not re-graded under the round-2 judge, so the baseline-vs-round-2 comparison
  crosses judge versions.

## 3. Owner decisions, and every evaluation change (disclosed)

**Decisions applied**

1. *"فريق المبيعات هيتواصل معاك قريب" is approved.*
   - The judge rubric says it is approved store behaviour.
   - The deterministic `unsupported_promise` check exempts that exact sentence and nothing else.
   - S11 and S19 are graded on the rest. S19 has passed every full run since run 2.
2. *Production backtest, read-only.*
   - Every connection is set read-only, and the run aborts unless the server confirms it. Sentry is
     off and no model calls are made. Nothing was written to production.
   - Results are in `backtest-placement-readonly{,-2,-3}.txt`.
   - 4 of 25 Latin-script placements were questioned (Ultra Male→Le Male ×2, 9PM Night Out→9PM ×2).
   - 9 of 48 deterministic matches were dropped (Club de Nuit→La Nuit, Dior Homme Sport→Sport, Black
     Phantom→Phantom…), with no false positives after the final adjustments.
   - Not re-run after the last change to that rule (§4, "Names"): perfume-talk words such as "notes"
     and "top" no longer count as name words.
3. *Conflicting store figures.*
   - The computed price range left the prompt, and FAQ percentages are kept out of it; the agent
     reads the business facts.
   - The store data itself still conflicts and needs the owner's correction:
     - the business facts say both "حوالي 90%" and "تطابق… 95%";
     - the static FAQ "أصلي ولا تركيب" says 95%;
     - "450 لحد 1200" sits beside store bottles that cost 750–2000.
   - It still shows: in run 6 the bot said 95% on one turn and "حوالي 90%" on another (S16, medium).
4. *No "مش مسكر" from a missing label.*
   - The persona rule was rewritten.
   - In the reply pipeline, the checker flags a negated label the perfume does not carry, including
     "مش حلو زيادة". Exceptions: a recorded opposite (light/heavy), an ingredient-backed absence
     (oud/musk/leather), or a hedge.
   - A recorded label may not be called unconfirmed.
   - A "guaranteed not sweet" request gets "مقدرش أضمن 100%…" plus what the data does say.
5. *Budget USD 4.* Recorded in `ledger.json → cap_changes`. Spend is in §8.

**Evaluation changes.** Each one was made to stop a mis-grading. None weakens the pass rule,
scenarios or success criteria.

- Judge rubric: the approved sentence (decision 1).
- `unsupported_promise`: the single-sentence exemption (decision 1).
- Judge input: the order's `total_price` is labelled `items_total_excluding_shipping`. The judge had
  read the item total as the full bill (S11).
- Judge input: a perfume with no original variant in stock now says "original bottle: NONE available".
  The same S07 transcript failed 2 of 2 re-gradings before this change and passed after it.
- Harness: `verdict_for` reads a judge dismissal by its leading code. The old whole-string comparison
  ignored explicit dismissals. Every saved run was re-scored; only batch-2 S02 changed (fail → pass).
- Stricter, not looser: the eval checker shares the seller's claim checks, so each new seller check
  (sizes, bottle authenticity, note layers, unknown-perfume likeness, recorded notes denied or called
  a mistake) also made deterministic grading stricter.
- Unchanged: scenarios, success criteria, pass rule, and the eval price/number checks. Their known false
  positives are still there, and the judge dismissed every one it saw:
  - "Pi" inside "Pineapple";
  - "المجموع:" lines;
  - bundle names such as "Tobacco Vanilla" read as notes;
  - 810/1060/1560 sums flagged as invented numbers;
  - a denial of "Club de Nuit Intense" read as a denial of the stocked "La Nuit".

**Test-settings leak (found in round 2).** The scratchpad settings loaded `.env`, so one round-1 test
reached the real API (about 2 calls per suite run). The settings now use a placeholder key and a dead
base URL, and log and refuse any escaped request; the hardened copy is `local_test_settings.py` in
this folder. The unmetered spend is charged to the ledger as an upper-bound estimate of USD 0.10.

## 4. Bugs fixed in round 2 (by theme)

Detail and turn references are in `findings.md`. Every fix has a regression test.

**Names and availability (S09, S02)**
- "Club de Nuit Intense" was answered "La Nuit: متوفر": a Latin name that disagrees with the placed
  row is no longer placed.
- A confirmed absence was answered "1 Million: متوفر", and the same fallback was resent word for word.
- After a denial, "حاجة شبهه؟" offered our own alternatives as the reference, then resolved "شبهه" to
  the one alternative we had listed.
- "أقدر ألاقيلك حاجة قريبة منه" promised a likeness with no data (my own wording, replaced).
- The customer's description of the missing perfume ("اللي عاجبني فيه… سموكي وفاكهة") was ignored and
  the scent question was asked again.
- "أنهي واحد فيهم أقرب لـ Club de Nuit؟" got price blocks only; it now opens with "معنديش بيانات عن
  ريحة Club de Nuit أقارن بيها".
- My Latin-name rule dropped "Layton" from "Layton notes إيه؟" (a regression I introduced and fixed).

**Orders and totals (S03, S05, S11, S19)**
- The order extractor dropped items: a house name inside "بلو دي شانيل" became "which Chanel?", and a
  line was renamed into another line.
- A purchase message ("خلاص هاخد الـ50… الشحن بكام؟") was swallowed by the anti-repetition redirect
  after several similar replies, leaving the cart empty.
- Two recipients' scents were merged into one request; the same pair block was resent on every total
  question and on "خليهم اتنين".
- Totals left out shipping: the summary, the repeated-total line ("1500، من غير مصاريف الشحن" read as
  "no shipping") and the running cart total.
- "هاخدهم كده" re-asked the bottle type and size that had just been quoted.
- A deferral ("هكمل بعدين") got the details request again.
- "أكدلي الأوردر" with nothing chosen got the previous reply word for word.
- "عايز زجاجة الستور العادية" sent the identical summary three times.

**Offers (S03, S18, S20)**
- The transfer offer repeated every turn (`test_promotions` passes unchanged).
- A total/count question under a combined budget was classified as a promotion.
- A joking discount question from a browsing customer ended on a transfer question.
- "ماشي خلاص، مش هطلب حاجة. سيبك" was read as yes to the transfer offer and handed to staff.

**Memory and recipients (S06, S10, S14, S15)**
- "الأولاني" after two single offers pointed at the second perfume.
- "Aventus مناسب ليا، وكمان عايز حاجة لماما" did not switch recipient; her profile inherited his.
- "نرجع لبتاعي، كنت قلت إيه؟" ran a fresh search and offered a perfume never mentioned.
- Musk was excluded as a label but not as an ingredient.
- Complaints and questions about a perfume ("212 VIB تقيل", "Pure XS فيه جلد؟") were saved as wishes.
- A new budget said on an FAQ turn was lost.
- "حاجة/عطر/الريحة اللي **فيها** فواكه/سموكي/توابل" dropped the wish entirely, the model's own tag
  with it (older than this task).

**Unsupported or wrong claims (S02, S07, S10, S14, S16)**
- Words no label supports ("أدفى", "دافية مش حادة"), and which trait "leads".
- A note placed in the wrong layer; a "dry down" described from labels.
- A recorded label called unconfirmed; "not sweet" inferred from a missing tag.
- A stored note called a mistake ("ذكرت الفانيليا… بالغلط"), or a mistake admitted in answer to "ليه
  قلت…؟" when the earlier statement was right.
- "Only this size" when two are stocked; an original bottle "in several sizes"; a bottle "genuinely
  from Dior".
- "زجاجة اوريجنال بنفس شكل زجاجة البراند" rewritten as "…شكل زجاجة الستور الخاصة بينا".
- A price question answered with an unrequested notes list.

**Store questions and price objections (S07, S11, S14, S16, S18)**
- Exchange and delivery questions went to the model (which invented a condition) or went unanswered
  beside another question; they now get the store's own text, clause by clause, shortened on a repeat.
- The price range was never given; it now comes from the business facts.
- "بيقعد قد إيه بجد؟" got "needs confirmation" and a handoff offer.
- "لو السعر ثابت قولّي" was read as a longevity question ("ثباته ممتاز").
- "الثبات ده على الجلد ولا الهدوم؟" got "اكتبلي اسم العطر تاني".
- "غالي عليا" when nothing fits the budget got a generic defence or a vague line; it now says nothing
  is at or under the budget and that the quoted price is the lowest.
- Cheaper questions at the lowest price got a similarity question (twice), the same-priced perfumes,
  and "تقصد أنهي عطر وحجم؟"; they now get "مفيش حاجة أرخص من 750 جنيه عندنا حاليًا؛ ده أقل سعر عندنا".
- A closing remark was answered with an FAQ pitch or sent to a new search.

**Robustness (S12, S15).** An empty completion from the provider is retried once.

**Preserved**
- Every price is bound to a catalogue variant. New price evidence comes only from the customer's own
  cart and orders, plus the store's shipping fee.
- Scent statements come only from stored tags and notes.
- Stock checks, the performance policy text and store policies are unchanged.
- Order consent is unchanged: summary first, then explicit confirmation (§6 has one case this rule
  handles badly, left for the owner).

## 5. Per-scenario status

P/F with the judge's mean score. R1–R6 are full runs. "After R6" lists replays on later code.

| Scenario | R1 | R2 | R3 | R4 | R5 | R6 | After R6 | State |
|---|---|---|---|---|---|---|---|---|
| S01 beginner | P | P | P | P | P | P9.9 | | stable |
| S02 enthusiast | F | F | F | F | F | **P8.0** | | first full-run pass; its "Layton notes" regression fixed after, unit-tested only |
| S03 total budget | F | F | P | P | F | **P8.3** | P9.7 | fixed |
| S04 busy price | P | P | P | P | P | P9.9 | | stable |
| S05 premium | P | P | F | F | P | P9.3 | | passing since run 5 |
| S06 indecisive | P | F | F | P | F | P8.9 | | fixed |
| S07 gift | F | P | F | P | F | P8.0 | | fixed; exchange-policy gap fixed after, unit-tested only |
| S08 compare | P | F | P | F | P | P9.9 | | passing since run 5 |
| S09 unavailable | F | F | P | F | F | P9.3 | F8.6 → P9.9 | fixed |
| S10 dislikes | F | F | F | P | P | P9.3 | | passing since run 4 |
| S11 all constraints | F | F | F | P | P | F8.1 | P9.3, F4.1, F7.1 | criteria met in the last replay; fails on the confirmation case in §6 |
| S12 short refs | P | P | P | P | P | P9.9 | | stable |
| S13 mixed language | P | P | P | P | P | P9.7 | | stable |
| S14 mind change | F | P | F | F | P | P9.6 | | passing since run 5 |
| S15 self & mother | P | F | F | P | F | P9.7 | | fixed |
| S16 skeptic | F | F | F | F | P | F7.4 | P9.3 | fixed; replay only |
| S17 order complaint | P | P | P | P | P | P9.9 | | stable |
| S18 browser | P | F | P | P | F | P9.3 | | fixed |
| S19 ready buyer | F | P | P | P | P | P9.6 | | stable since run 2 |
| S20 pushy discount | F | P | P | P | P | F7.4 | P8.7 | fixed; replay only |

## 6. Open items

- **No completed full run on the final code** (§1). Two full runs are still needed to test the
  criterion.
- **Owner decision: confirmation after a side question (S11).**
  - After the order summary the customer asked "الشحن بكام؟", got the answer, then said "تمام أكد", and
    received the identical summary again (`round2-batch5/attempt-2`, t8).
  - The order extractor accepts a confirmation only when the summary is the message immediately before
    it. The code's own consent check (summary sent for this exact, unchanged cart) was satisfied.
  - Accepting that confirmation changes the order-confirmation rule, which was to stay as it is, so I
    did not change it.
- **Store data conflict** (90% vs 95%, 450–1200): owner to correct (§3).
- **S11 repeats the near-budget phrase** "أعلى حاجة بسيطة من ميزانيتك" on consecutive price replies
  (medium; the judge confirmed it in one run and dismissed it in another).
- **S09 t6:** model prose added "ترتيب الإحساس مختلف شوية" between two stocked perfumes (medium).
- **Stores without promotions (not in the simulation):** the classifier does not run before the static
  FAQ check there, so "هاخد الـ50… الشحن بكام؟" would get the shipping answer alone. Seen while
  tracing S11; not fixed.
- **Model-dependent turns stay probabilistic.** The classifier, the order extractor and model prose
  vary between runs.

## 7. Judge noise, shown by re-grading identical transcripts

- **S18 run 2:** it failed on the joke criterion. Re-graded unchanged, it passes
  (`noise-check/S18-run2-rejudge-1`). Run 1 had passed with the identical reply.
- **S07 run 3:** it failed 2 of 2 re-gradings because of a judge-input gap, and passed after the input
  was fixed (§3). That was an input defect rather than random noise.
- **S11 criterion:** near-identical wording ("مفيش عطر عندنا سعره تحتها") met the "nothing at or under
  700" criterion in runs 4 and 5 and missed it in run 6. I made the sentence explicit rather than
  argue noise.
- **No noise claim for runs 5 and 6:** every failure in them traced to a real defect, so none is
  attributed to noise.

## 8. Executions, API calls and spend

From `ledger.json`; the per-phase table is in `ledger-totals.md`.

| | |
|---|---|
| Ledger total | **USD 2.9992 of 4.00**; USD 0.0177 more is reserved for two calls in flight when run 7 was stopped; remaining 0.9831 |
| Round 1 | USD 1.3495 |
| Round 2 | USD 1.6497: full runs 1.0091 (six complete runs at 0.15–0.19 each, plus 0.0135 for the stopped run 7), replays 0.5295 (batches 1–5), re-gradings 0.0111, unmetered test-leak estimate 0.1000 |
| Not real spend | 13 calls charged at their reserved maximum (USD 0.1076): 12 rejected with 429 when the OpenAI project hit its own spend limit (USD 0.0879), and 1 judge timeout (USD 0.0197) |
| API calls | 6,913 ledger entries, all `gpt-6-luna` |

Every attempt is kept with its transcripts, `config.json` and log, including failed and stopped ones:
- `round2-full/attempt-1…7` (7 is partial)
- `round2-batch1…3/attempt-1`, `round2-batch4/attempt-1…4`, `round2-batch5/attempt-1…2`
- `noise-check/*`
- the round-1 folders

Stopping run 7 left a stale `ledger.lock` and its disposable database
(`test_customer_sim_fb1ce0f34b`, local PostgreSQL). Both were removed.

## 9. Tests and commands

- **Totals:** 2,243 tests at the end of round 1; **2,386** now, all passing (`tests-round2-31.log`).
  No code file changed after that run.
- **New tests:** all 143 are in `products/test_customer_simulation_fixes.py`, in
  `SecondRoundBatchOneTests` … `SecondRoundBatchFiveTests` (13, 18, 72, 28 and 11 tests; the 143
  includes one added to an earlier class).
- **Pre-existing tests:** none were changed in round 2. Three of them caught mistakes in my first
  versions, which I then narrowed until they passed unchanged:
  - `test_fixed_negation_does_not_reject_a_supported_description`
  - `test_literal_ingredients_do_not_add_a_style_requirement`
  - `test_explicit_withdrawal_clears_both_fields_and_provenance`
- **My own earlier tests:** I changed some round-2 tests when later fixes changed the behaviour they
  pinned; each is listed in `findings.md`.
- **A gap I found in my own testing:** two tests called `handle_objection` directly and never ran the
  routed turn's price check, which was rejecting the sentence they asserted. They now run the whole
  routed turn.
- **API check:** the suite's blocked-call log did not grow over the last six suite runs.

```
# from perfume_ai/ (Redis is not running here; the settings use an in-memory cache and block the API)
PYTHONPATH=artifacts/customer_simulation_20 python manage.py test products --keepdb --noinput --settings=local_test_settings
python scripts/simulate_customers.py --phase round2-full --attempt attempt-8 --workers 4      # then attempt-9
python scripts/simulate_customers.py --phase baseline --attempt rejudged-3 --rejudge artifacts/customer_simulation_20/baseline/attempt-1
python eval_harness/summarize_simulation.py <run folders…> --out artifacts/customer_simulation_20/comparison-round2.md
python eval_harness/ledger_totals.py --out artifacts/customer_simulation_20/ledger-totals.md
```

To finish the criterion: two full runs on the current code (about USD 0.16 each) and the baseline
re-grade (about USD 0.07). The ledger has USD 0.98 left.

## 10. Limitations

- **Single samples:** one conversation per scenario per run. The simulator adapts, so a replay is a
  different conversation, and single pass/fail flips are within noise.
- **Not exercised:** Redis-backed paths, real channel delivery (outbound HTTP is blocked), and the
  production database except for the read-only backtest.
- **Wording:** deterministic texts still say "المسجل" ("recorded") where the persona discourages it.
- **Tooling:** `pyflakes` was installed locally for checking and is not a project dependency.
- **Commits:** none. The working tree holds every change, uncommitted, as requested.
