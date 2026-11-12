# Perfume seller — 20 adaptive customer simulations: final report (round 3)

Branch `opus`. The owner committed the round-2 state as `40bd122` ("before 18/20"). This round's changes
are uncommitted on top of it; I committed nothing. Everything referenced here is under
`artifacts/customer_simulation_20/`. Per-defect detail (scenario, turn, change) is in `findings.md`;
the round-1 report is kept as `REPORT-round1.md`.

## 1. Status: the done criterion is not met

The criterion for this round was one full run on the final code with at least 18 of 20 passing, and all
tests green.

- **The one full run scored 16/20** (mean judge score 8.82). No second full run was allowed.
- The four failures were S02, S07, S12 and S13. Each traced to a specific defect and was fixed with a
  test. Replayed afterwards: **S07, S12 and S13 pass**. S02 failed its last replay on one criterion,
  and that same transcript passes on both of two re-gradings (§7).
- So the code as it stands now has **no full run**. Everything after the full run is verified by tests
  and by replays of those four scenarios.
- **All 2,406 tests pass**, and no test reaches the API.
- **Spend: USD 3.3910 of the 3.50 cap.** USD 0.3917 was spent in this round, of the 0.50 allowed.
  No 429 occurred.

## 2. Outcome

| Run | Code | Pass / 20 | Mean judge score |
|---|---|---|---|
| Baseline, round-1 judge (`baseline/rejudged-2`) | unmodified `ea27ef8` | 1 | 5.78 |
| Baseline, round-2 judge (`baseline/rejudged-3` + `-3b`) | unmodified `ea27ef8` | 1 | 5.87 |
| Round-1 final 1 / final 2 | end of round 1 | 6 / 6 | 7.54 / 7.79 |
| Round-2 full runs 1–6 | successive fixes | 10, 10, 11, 14, 13, 17 | 8.29 → 9.07 |
| Round-2 full run 7 | — | stopped at 2/20 on request | — |
| **Round-3 full run** (`round3-full/attempt-1`) | round-3 code, new store data | **16** | 8.82 |

Round-3 replays (failed or changed scenarios only):

| Replay | Scenarios | Result |
|---|---|---|
| `round3-replay/attempt-1`, before the full run | S11, S09, S16 | S11 pass 9.3, S16 pass 9.9, S09 fail (fixed before the full run; S09 then passed it, 9.4) |
| `round3-replay/attempt-2`, after the full run | S02, S07, S12, S13 | S07 pass 8.6, S12 pass 9.9, S13 pass 9.7, S02 fail on a new turn |
| `round3-replay/attempt-3` | S02 | fail on one criterion; the same transcript passes 2 of 2 re-gradings |

How to read these:
- The baseline is now graded by the same judge as the later runs: 5.87 against 8.82 for the round-3 run.
- Run 6 (17/20) and the round-3 run (16/20) are not on the same store data. The local store was edited
  between them (§3), and two of the round-3 failures came from that change.
- One high issue fails a whole conversation, so pass counts move more than means.

## 3. This round's owner decisions, and what else changed

**Decisions applied** (tests in `ThirdRoundTests`)

1. *Confirmation after a side question (S11).* "تمام أكد" now places the order when the summary was
   sent for this exact, unchanged cart, even if other messages came in between.
   - It needs the confirm verb itself ("أكد", "أكدلي", "نأكد", "confirm"). A bare "تمام", "أكيد",
     "متأكد", a question, a negation or a deferral does not count.
   - Safeguard 1: a cart changed after the summary (items, sizes, quantities, bottle types or delivery
     details) needs a new summary first.
   - Safeguard 2: nothing confirms without a summary that was actually sent.
   - Both safeguards are the existing `_summary_was_shown` check, unchanged.
   - Tests cover all three cases, plus a summary that was composed but never delivered. The first test
     fails with the rule disabled.
2. *Store figures.* No "90%" reaches the customer.
   - Code and prompts: none. The only mentions were a comment and a docstring, now reworded.
   - Local store data (read-only query): none in the business facts, settings or FAQ answers.
   - Tests that contained the old value were updated to 95% / 750–2000: three of mine, and one
     pre-existing fixture in `products/tests.py` (store "Perfamix Test": "نسبة تشابه 90%" → 95% in the
     fixture and its two assertions). The pre-existing guard that the shared prompt hardcodes no "90%"
     is untouched.
3. *The two medium issues.*
   - S11: "أعلى حاجة بسيطة من ميزانيتك" is said once per price. Later quotes of the same price lose
     it, and so does a second copy in the model's sentence. A message that asks about the budget keeps
     it.
   - S09: a claimed difference in "feel" between perfumes ("ترتيب الإحساس مختلف شوية") is an
     unsupported claim unless hedged.
4. *Production backtest, read-only* (`backtest-placement-readonly-4.txt`).
   - The server reported `transaction_read_only=on` and `default_transaction_read_only=on` before any
     query. Nothing was written, and no model was called.
   - Latin-name check: unchanged from the previous run. 4 of 25 placements questioned, 9 of 48
     deterministic matches dropped, the same items. So the "notes/top/base" change altered no
     production result.
   - Arabic guard: the same 7 flagged. Counts moved slightly (329 → 328 examined) from new production
     conversations and earlier filler-word additions.
5. *Budget.*
   - The 3.50 cap was recorded in `ledger.json → cap_changes` before any paid call.
   - The meter reserves each call's maximum cost before sending it and refuses a call that could pass
     the cap.
   - It now also halts every further call after the first 429 (tested). None occurred.

**The local store data changed, and that mattered.** Between run 7 and this round the local Perfamix
store was edited. Besides 95% and 750–2000, shipping, delivery time, the website and the contact numbers
moved into the business facts, and their FAQ entries were switched off ("بكام مصاريف الشحن؟", "كم مدة
التوصيل؟", "هل العطر أصلي ولا تركيب؟" and others).
- My shipping code read the fee from the shipping FAQ entry only. On this data the order summary would
  have lost its shipping line and its total with shipping, silently. I found and fixed this before the
  paid runs: the shipping helpers fall back to the owner's shipping sentence in the business facts.
- What I did not anticipate: plain shipping questions ("الشحن بيوصل اسكندريه؟", "el sha7n b kam?") had
  been answered by that FAQ entry too. With it off they went to the model. That cost S12 and S13 in the
  full run (§4).

**Evaluation and harness changes in this round** (none weakens the pass rule, scenarios or criteria)
- A re-grade now gives the judge the store facts saved with the run being re-graded. Without this the
  baseline would have been judged against today's store data and marked wrong for quoting the facts of
  its day. The catalogue is unchanged since the baseline.
- The meter's 429 halt and the 3.50 cap (decision 5).
- Stricter, as before: the new claim checks are shared with the eval checker (a difference in "feel";
  a notes list after a complaint that notes keep repeating).
- Earlier evaluation changes (rounds 1–2) are listed in `findings.md` and are unchanged.

## 4. Bugs fixed in this round

**Found by the pre-run replay**
- S09: "غالي أوي بصراحة، مفيش حاجة شبهه في حدود ١٠٠٠؟" was treated as a bare price objection and the
  1,400 perfume was described again. A price objection that asks for an alternative now goes to the
  recommendation, when something in the store fits the budget.

**The four full-run failures**
- S12, S13: a plain shipping question got "تقصد أنهي عطر وحجم؟" (the store-data change above). Any
  plain shipping question, in Arabic, Franco or English, now gets the owner's shipping sentence; a
  shipping draft that fails the price check falls back to that sentence too.
- S02: the model wrote its own drafting instruction to the customer ("سعر الـ90 ملي هيتضاف
  تلقائيًا."). Such a sentence is removed from every reply.
- S02: "مش مطمن… هفكر وأراجعها قبل ما أقرر، تسلم." got a recap and a branch invitation. A message that
  closes with thanks and "هفكر/هراجع" now gets the short close whatever objection kind was detected.
- S07: "والضمان ده ينفع لو فتحته وجربته؟" was not recognised as an exchange-policy question, and the
  model invented a restriction. The policy's own names (ضمان، استبدال، استرجاع) now route to the
  store's text.

**Found by the replays after the full run**
- S02: "لسه بتكرر نفس النوتات… الـ90 بكام؟" listed the notes again. A notes word after "بتكرر/كررت"
  is a complaint, not a request.
- S02: asked where the notes came from, the bot repeated the list. It now says once: "النوتات دي هي
  المسجلة عندنا في بيانات العطر، ومعنديش مصدر رسمي من البراند أستند له."

**From the owner's decisions:** the confirmation rule, the near-budget sentence, the "feel" claim, and
shipping read from the business facts (§3).

The bugs fixed in rounds 1 and 2 are listed by theme in `findings.md` and in the previous version of
this report (commit `40bd122`).

Preserved: every price is bound to a catalogue variant; scent statements come only from stored tags and
notes; stock checks, the performance policy text and store policies are unchanged.

## 5. Per-scenario status

P/F with the judge's mean score. R6 is round-2 full run 6; R3 is the round-3 full run.

| Scenario | R6 | R3 full | After R3 | State |
|---|---|---|---|---|
| S01 beginner | P9.9 | P9.9 | | stable |
| S02 enthusiast | P8.0 | F6.6 | F7.9, then F on a criterion that passes 2/2 re-grades | least stable: a long technical conversation that fails on a different turn each time; every fault found is fixed; the last one ("متأكد من المصدر؟") is unit-tested only |
| S03 total budget | P8.3 | P9.6 | | passing |
| S04 busy price | P9.9 | P9.9 | | stable |
| S05 premium | P9.3 | P9.1 | | passing |
| S06 indecisive | P8.9 | P8.7 | | passing |
| S07 gift | P8.0 | F7.9 | P8.6 | fixed, replay passed |
| S08 compare | P9.9 | P9.9 | | passing |
| S09 unavailable | P9.3 | P9.4 | | passing (failed the pre-run replay, fixed, then passed the full run) |
| S10 dislikes | P9.3 | P8.1 | | passing |
| S11 all constraints | F8.1 | P8.6 | | passing; the confirmation rule and the near-budget fix held |
| S12 short refs | P9.9 | F8.1 | P9.9 | fixed, replay passed |
| S13 mixed language | P9.7 | F7.1 | P9.7 | fixed, replay passed |
| S14 mind change | P9.6 | P9.3 | | passing |
| S15 self & mother | P9.7 | P9.1 | | passing |
| S16 skeptic | F7.4 | P8.7 | | passing |
| S17 order complaint | P9.9 | P9.9 | | stable |
| S18 browser | P9.3 | P9.7 | | passing |
| S19 ready buyer | P9.6 | P8.4 | | passing |
| S20 pushy discount | F7.4 | P8.4 | | passing |

## 6. Open items

- **No full run on the code as it stands.** One would cost about USD 0.17; USD 0.109 is left under the
  cap, so it needs a new allowance.
- **S02** (above). Its "store or original?" answer does not add the original bottle's price; the judge
  graded that both ways on the same transcript.
- **S09 t4:** "طب أنهي واحد أقربله؟" (the missing perfume, by pronoun) was answered "Aventus أقرب
  لطلبك" although Cigar carries the same labels (medium).
- **S07:** a bare description for "هتعجبها أكيد؟ … مناسب ليها؟" (medium), and feminine forms addressed
  to a male buyer (low).
- **Checker false positives the judge has to dismiss** (unchanged; it has dismissed every one):
  - totals with shipping (810, 1060, 1560) flagged as invented numbers;
  - a denial of "Club de Nuit Intense" read as a denial of the stocked "La Nuit";
  - the cart total against a budget the customer knowingly exceeded.
- **Stores without promotions (not in the simulation):** the classifier does not run before the static
  FAQ check there, so "هاخد الـ50… الشحن بكام؟" would get the shipping answer alone. Not fixed.
- **Model-dependent turns stay probabilistic.** The classifier, the order extractor and model prose vary
  between runs.

## 7. Judge noise, shown by re-grading identical transcripts

- **S02, round-3 replay 3:** failed on "Price question gets the actual variant price". The identical
  transcript re-graded twice passes both times with that criterion met
  (`noise-check/S02-round3-replay3-rejudge-1`, `-2`).
- **S18, round-2 run 2:** failed on the joke criterion; re-graded unchanged, it passes.
- **S07, round-2 run 3:** failed 2 of 2 re-gradings because of a judge-input gap, and passed after the
  input was fixed. That was an input defect rather than random noise.
- **No noise claim for the round-3 full run:** all four failures traced to real defects.

## 8. Spend

From `ledger.json`; the per-phase table is in `ledger-totals.md`.

| | USD |
|---|---|
| Spent before this round | 2.9992 |
| Two calls in flight when run 7 was stopped, charged at their maximum | 0.0177 |
| Replay before the full run (S11, S09, S16) | 0.0348 |
| The full run | 0.1727 |
| Replay of the four failures | 0.0371 |
| Replay of S02 | 0.0191 |
| Two re-gradings of the S02 transcript | 0.0042 |
| Baseline re-grade, plus the retry of two scenarios | 0.1062 |
| **This round** | **0.3917** (of 0.50 allowed) |
| **Total** | **3.3910 of 3.50** (0.1090 unspent) |

- The baseline re-grade cost more than my USD 0.07 estimate. Two of its judge calls got a provider
  server error and were charged at their maximum (USD 0.0373), then retried.
- One seller call in the S02 replay failed the same way (USD 0.0097); the turn recovered.
- No 429 occurred, and the cap was never approached within one call's maximum.
- Every attempt is kept with its transcripts, `config.json` and log: `round3-replay/attempt-1…3`,
  `round3-full/attempt-1`, `baseline/rejudged-3` and `-3b`, the two new `noise-check` folders, and all
  earlier folders.

## 9. Tests and commands

- **2,406 tests pass** on the final code (`tests-round3-6.log`): the 2,386 from before plus 20 new ones
  in `ThirdRoundTests`.
- **Old tests:** one pre-existing fixture was changed, because it contained the old 90% value
  (decision 2). No other old test was edited.
- **My own earlier tests:** three were updated to the new figures (95%, 750–2000).
- **API check:** the suite's blocked-call log did not grow over this round's six suite runs.

```
# from perfume_ai/ (Redis is not running here; the settings use an in-memory cache and block the API)
PYTHONPATH=artifacts/customer_simulation_20 python manage.py test products --keepdb --noinput --settings=local_test_settings
python scripts/simulate_customers.py --phase round3-full --attempt attempt-2 --workers 4
python scripts/simulate_customers.py --phase round3-replay --attempt attempt-4 --scenarios S02_enthusiast
python eval_harness/summarize_simulation.py <run folders…> --out artifacts/customer_simulation_20/comparison-round3.md
python eval_harness/ledger_totals.py --out artifacts/customer_simulation_20/ledger-totals.md
```

## 10. Limitations

- **Single samples:** one conversation per scenario per run. The simulator adapts, so a replay is a
  different conversation.
- **Store data moved under the runs:** round-2 runs and the round-3 run used different store facts.
- **Not exercised:** Redis-backed paths, real channel delivery (outbound HTTP is blocked), and the
  production database except for the read-only backtest.
- **Commits:** none by me. The round-3 changes are uncommitted on top of `40bd122`.
