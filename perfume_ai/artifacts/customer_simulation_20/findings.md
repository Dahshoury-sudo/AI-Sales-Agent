# Baseline findings — 20 adaptive customer simulations

Baseline run `20261111T200325Z-0f1462` (seller code at commit `ea27ef8`, unmodified), transcripts in
`baseline/attempt-1/`, re-graded with the corrected judge in `baseline/rejudged/` (the judge was
missing each perfume's scent tags and the fact that offers are fulfilled by staff). After J1 below
(judge missing the catalogue overview) the same transcripts were graded again in
`baseline/rejudged-2/` with the exact judge used for the final run (rubric sha256 `9bed0237…`
unchanged throughout). **1 pass / 19 fail in all three gradings**; mean judge score 5.94 (rejudged)
vs 5.78 (rejudged-2) on identical transcripts, per-scenario means moving by up to 0.7: the judge's
own noise.

Every quotation below is actual model/application output from those files. Severity follows the
judge rubric (critical = invented fact / wrong order / false denial; high = memory, exclusion,
re-asking, wrong reference, mishandled complaint; medium = unnecessary question, scripted reply).

| ID | Scenario & turn | Expected | Actual (verbatim, abridged) | Sev | Root cause |
|---|---|---|---|---|---|
| B01 | S01 t3–t4, S18 t3, S07 t1 | After "مش عارف" offer accessible choices or an example | Identical `بتحب الفريش، الخشبي، العود، ولا المسكر؟` three turns running | high | `router._route` returns the fixed `scent_answers.PREFERENCE_QUESTION` whenever no scent is known; nothing reads a "don't know" answer |
| B02 | S13 t1–t8 | Accept "rigali" (Franco for رجالي) | `محتاج برفان رجالي ولا حريمي؟` seven times, incl. after "thanks" | high | `sales/gender.py` vocabulary has no Franco forms; model-inferred gender is discarded by design, so the scripted gate loops |
| B03 | S10 t1 | Exclude sweet and oud | Recommended 212 Sexy man + Black XS (both sweet) | critical | `scent_preferences.reconcile_scent_intent`: `_NEGATIVE` does not allow "أي" after the negator, so "مش عايز **أي** حاجة مسكرة" is read positive and overrides the model's correct negative evidence; "ولا فيها عود" (literal mention of a non-material tag) is dropped entirely |
| B04 | S01 t6 | "مش عايزها تقيلة، حاجة هادية" = avoid heavy, want light | Saved `excluded_scent_tags: [heavy, light]` | high | same function: negation propagates across a comma into the model's explicitly positive phrase |
| B05 | S14 t2/t4, S06 t8 | Remember "مش بحب العود" and "الميزانية لحد 1500" said inside a question | Neither saved; later recommendation ran without the 1500 budget | high | preferences are extracted/merged only on `recommendation` turns; product_info/comparison/FAQ turns never persist stated exclusions or budgets |
| B06 | S15 t3–t9 | Mother's preferences scoped separately; "نرجع لبتاعي" restores his | His `male/woody` overwritten by `female/floral`; women's perfumes offered to him; gender re-asked twice | high | a single flat preference profile; a recipient change resets it and nothing restores the previous recipient |
| B07 | S02 t1, S05 t4, S06 t5–t6, S08 t5–t6, S09 t2, S14 t7/t9, S16 t5–t6 | Answer the question about the named/discussed perfume | `ماقدرش أأكد المعلومة دي عن العطر. ممكن تحدد المواصفة اللي بتسأل عنها؟` | high | `router._rephrased`: when both drafts fail claim checks and no explicitly requested attribute is detected, the deterministic fallback renders nothing and returns this non-answer (see debug replay for which checks rejected the drafts) |
| B08 | S16 t5–t6 | General longevity question → store policy answer | Same non-answer | high | same fallback has no product-free path for performance questions although the policy applies to every stocked perfume |
| B09 | S04 t1–t2, S08 t7, S09 t8, S11 t3–t4 | "سوفاج 90 بكام" / "والـ50؟" → that size | Every size and bottle quoted; repeated block | medium | `product_formatting.requested_quote_options` recognises a size only when followed by ملي/ml |
| B10 | all price replies | "1000 جنيه" | "1000.00 جنيه" | low | `render_quotes` formats the DB Decimal with `:f` |
| B11 | pilot S11 t2, S07 t3/t6 | State known price; give the configured exchange policy | "السعر محتاج تأكيد من الفريق"; "هسأل وأرد عليك يا فندم" | high | persona red line 2 scripts "هسأل وأرد عليك / لحظة أتأكدلك" (nothing follows up); static FAQ answers reach the customer only when the whole message matches a short keyword, never the model |
| B12 | S07 t2, S10 (prompt) | Use the rich preferences in "مش متأكد… ترشحلي حاجة آمنة" | Objection handler asked for a perfume name | medium | `router`: keyword objection detection pre-empts `recommendation` turns even when the message carries actionable preferences; the `rejected` playbook forbids correcting even when the reason is given |
| B13 | S08 t2–t4, S06 t7 | "أنهي فيهم أنسب للشغل؟" about the 3 discussed perfumes | Gender gate → scent gate → two unrelated perfumes; "اختارلي واحد فيهم" → a third perfume | high | classifier sends "which of them" to recommendation; nothing routes references to perfumes under discussion back to them |
| B14 | S03 t3–t6 | Explain 3 bottles cost ≥2250 > 1600; ask which constraint to relax | Generic `مش لاقي اختيار… تحب تحدد ميزانية تانية؟` three times after refusals | high | `no_match_reply` has no specific limitation; a hypothetical "لو عايز 3" permanently set `purchase_quantity=3` |
| B15 | S11 t6–t9, S19 t5–t8, pilot | Accept "معنديش رقم بديل" | `ولسه ناقص رقم بديل.` four times; S19 buyer abandoned the order; pilot cancelled the cart | high | `order_service` requires a secondary phone with no way to decline it |
| B16 | S11 t5 | Answer "الشحن بكام؟" during checkout; label total | Shipping ignored; `المجموع: 750` (excludes 60 shipping, unlabelled) | high | the side-question append exists only for the regex checkout branch, not for classifier-routed `order`; summary total omits the configured shipping fee |
| B17 | S03 t8 | "212 Sexy" right after offering 212 Sexy man | "تقصد أنهي واحد: 212 Sexy man، 212 Sexy Women؟" | medium | `order_service` edition ambiguity ignores the perfumes just offered |
| B18 | S19 t1–t3 | Ask about the unresolved Bleu de Chanel; don't ask bottle type for 90 ml Sauvage (store bottle only) | Pending line hidden; bottle-type question; quantity flipped 1→2 | medium | order extraction (LLM) merged names; unresolved pending lines not surfaced before contact details; bottle type asked though only one exists at that size |
| B19 | S09 t1–t6 | "Club de Nuit Intense" is not stocked → deny + alternatives | Asked to retype 3×, then "La Nuit: متوفر" | critical | LLM resolver placed the Latin name on La Nuit; `_verify_placement` skips Latin spans by design |
| B20 | S16 t2/t4 | Answer "إزاي ده بـ1000 والأصلي بآلاف؟" | Authenticity FAQ verbatim, then identical again | medium | static FAQ keyword match answers any overlapping message and re-sends identical text |
| B21 | S18 t4–t5, S20 t1–t3 | Joke/close handled lightly; handoff offered once | Promotion script + "تحب أحولك لمندوب" each turn; after "لا مش دلوقتي… تمام شكراً" the script again | medium | promotion flow appends the same transfer offer and re-fires on a declined transfer + thanks |
| B22 | S12 t4 | Smell question about "الاولانى" | Smell + unrequested price block | low | `price_question`: ordinal + any amount in the previous reply (here the shipping fee) counts as a price question |
| B23 | S05 t6, S01 t7 | Brief thanks for "ذوقك حلو" / "ترشيحاتك عجباني" | Praise ignored ("تمام، خد وقتك.") | low | appreciation detector covers praise of perfumes only |
| B24 | S12 t2 | Price reply | Provider returned empty output → turn crashed | critical (infra) | one empty completion from the provider; the budget meter converts invalid outputs into errors by design |
| B25 | S07 t8 | Address the male buyer | "خدي وقتك… لو حابة تجربيها" | low | recipient gender used as customer gender |
| D1 | store data | — | facts say both "حوالي 90%" and "95%" similarity, and "prices start at 450" while the cheapest active bottle is 750 | — | conflicting StoreSettings/FAQ text; reported, not changed |

## Defects found during the debug replays (after the first fixes)

Debug replays are `debug-after-fixes/attempt-1` (code partly fixed), `attempt-2`, and the targeted
`attempt-3-targeted`. Each defect below was observed there, then fixed and regression-tested.

| ID | Where observed | Actual (verbatim, abridged) | Sev | Root cause |
|---|---|---|---|---|
| R01 | replay-1 S02 t2/t6 | flat notes list instead of the top/middle/base answer | high | note checker read "الـCardamom مش موجود" (predicate negation after the word) as a claim and "عنبر رمادي" as amber |
| R02 | replay-1 S03 t3–t7 | bare price blocks to "الإجمالي كده كام؟ ولو عايز 3 بنفس الـ1600 ينفع؟"; silent staff handoff | high | product-info route cannot state totals; escalation set `needs_human` without telling the customer |
| R03 | replay-1 S19 t8 | "حصلت مشكلة في تسجيل الطلب" after "معنديش رقم بديل… أكد" | critical | **introduced by fix B15**: declined number reached the NOT NULL `secondary_phone` column as `None` |
| R04 | replay-1 S10 t3–t4 | "سعره كام؟" quoted both perfumes; "الـ90 ملي ده فريش؟" answered with a price | medium | any size mention made the turn a price question |
| R05 | replay-1 S06 t8, S13 t4 | "اختارلي انت" opened the scent preference (sweet pick for a floral customer); "أنهي واحد فيهم أحلى للصيف؟" got unrequested prices | high | delegation among on-table perfumes not treated as a choice; long messages "inherited" a price question |
| R06 | replay-1 S15 t3–t8 | mother never got her own profile | high | recipient switch needed "ل" before the noun and the discussed-choice override swallowed a request for someone else |
| R07 | replay-1 S16 t4–t7 | honest "أكتر من 8 ساعات، بس الجلد ولا الهدوم محتاج تأكيد" rejected; "تقصد أنهي عطر؟" to a general question | high | "الـ٨" glued the digit to "ال" so the duration request was missed; last-resort fallback assumed a perfume |
| R08 | replay-1 S11 t5–t7 | order summary blocked "أعلى من ميزانيتك الإجمالية" after the customer accepted 750 | high | "الـ50 بكام **بالظبط**؟" marked the budget strict |
| R09 | replay-1 S18 t1–t3 | browsing question → gender and scent gates | medium | classifier sent general catalogue questions to recommendation; no grounded overview for a general answer |
| R10 | replay-2 S05 t5 | "متاح منه زجاجة اوريجنال 100 ملي بـ**1500**" (Alexandria II's is 2400; 1500 is the other perfume's) | critical | **introduced by fix B23**: praise skipped name resolution, both rows stayed in context, and an original-bottle question was not quoted by code |
| R11 | replay-2 S06 t5 | "مناسبته محتاجة تأكيد" although Beach/Summer is recorded | medium | fallback said "needs confirmation" without the recorded use |
| R12 | replay-2 S10 t2–t9 | "مش هقدر أأكد إنه مش مسكر… التاجات المسجلة"; store visit offered twice; same two perfumes re-offered | high | "مش قادر تضمن" matched the price objection keyword "مش قادر"; absent-trait answers not allowed confidently; "حاجة تانية" did not exclude the on-table pair |
| R13 | replay-2 S11 t2, t9 | 1000/1375 quoted against a 700 budget; "والإجمالي 810" confirmation blocked | high | `_rephrased` price fallback ignored the budget; bare "إجمالي" set a strict combined budget |
| R14 | targeted S05 t6–t8 | "هكمل الأوردر بعدين" three times → the same bottle question three times (t8 100% identical to t7) | critical | missing-detail prompt ignored an explicit deferral. (The pending line was for Stronger With You Oud, which the customer never chose: extractor output, not fixed, same class as B18.) |
| R15 | targeted S06 t7–t8 | "خلاص اختارلي انت" → "تقصدي أنهي عطر بالظبط؟ اكتبيلي اسمه"; "اختارلي بين Her وJumana" → Her vs **Power of You**, no pick | high | comparison asked the resolver before using the on-table perfumes when the message named none; t8's substitution not reproduced offline (name matching of "وJumana" is correct) — verified only by the final run |
| R16 | targeted S10 t3–t4 | after "طب K By Dolce مفيهوش عود…؟", "سعره كام؟" quoted both perfumes and "ده فريش…؟" was answered about 212 NYC Man ("وفيه… مسك") | high | `_referent_from_conversation` returned every offered perfume although the customer's latest message singled one out |
| R17 | targeted S11 t5–t6 | order confirmed, then "تقصد أنهي عطر وحجم؟ ممكن تكتبهم عشان أقولك السعر الحالي؟" appended; sent alone again next turn. Same fallback in S11 in 4 runs | high | a "الإجمالي 810 بالشحن صح؟" fragment went to the general branch, which has no order: its 810 and the order's own 750 failed the price check and the no-rows price fallback asked for a perfume |
| J1 | targeted S18 t1 (harness) | judge: "غربي وشرقي ونيش وتركيبات" is "invented" (critical) | — | the judge's store facts lacked the catalogue overview line the seller prompt carries (computed from `perfume_type`); judge input corrected and **the baseline re-judged with the identical judge** (`baseline/rejudged-2`) before the final run |

## Defects found in the first final verification run (`final/attempt-1`, run `20261111T221106Z-9ad01c`)

Result: **6 pass / 14 fail** (baseline 1 / 19), mean judge score 7.54 (baseline 5.78). F01–F04 were
fixed after this run and re-verified (see below); the rest are recorded and not fixed.

| ID | Where observed | Actual (verbatim, abridged) | Sev | Root cause | Status |
|---|---|---|---|---|---|
| F01 | S04 t1 | "سوفاج 90 بكام؟ ومتوفر؟" → quote only, no "متوفر" (earlier runs said "متوفر يا فندم.") | medium | `compose_price_reply` drops a bare "متوفر" clause as redundant with the quote, but the quote never states availability | fixed |
| F02 | S03 t3 | "لو عايز 3 بنفس الـ1600 ينفع؟" → the t2 two-bottle reply, 100% identical | critical | the hypothetical count was left to the extractor (read as 3 in replay 2, ignored here) | fixed |
| F03 | S08 t2–t8 | after a comparison written as "بلو دي شانيل… سوفاج… إيروس", "أنهي فيهم أنسب للشغل؟" → gender gate, scent gate, Eternity + Pasha | high | `names_in` reads only Latin catalogue spellings, so nothing was "under discussion" after an Arabic-named comparison (B13 regressed through this path) | fixed |
| F04 | S11 t4 | under the summary: "ابعتلي الاسم ورقم الموبايل الأساسي والعنوان بالتفصيل" (all just given) | high | the fee fragment "والشحن كده 60؟" missed the FAQ matcher and went to the general model, which saw only the fragment | fixed |
| F05 | S06 t7–t8 | "هو الأولاني كان إيه؟" answered "Her" over Cassilia's injected row; then "اختارلي انت" → Billie Eilish | high | the two-reply window no longer held Her (offered t4); the reply named Her without its row, so Her counted as withdrawn | not fixed (window is a deliberate design; needs its own study) |
| F06 | S10 t3–t6 | musk excluded, yet BMW/For Him/CK One (musk in notes) offered; "212 VIB تقيل… مش ده اللي طلبته" saved `heavy` as a wanted tag | high | musk excluded as a tag only, never as an ingredient (`avoid_notes`); a complaint's trait word read as a preference | not fixed (scent-preference engine) |
| F07 | S07 t3, t5 | "Body… ممكن يكون أدفى"; "مش طابعها مسكر قوي" | critical (judge) | comparative trait claim not caught by the claim checker; the judge rejects absent-tag negatives that R12 allows | not fixed; t5 is a policy question for the owner |
| F08 | S13 t4 | "anhy wa7ed a7la lel sef?" → both descriptions again (98% identical to t1) | critical (det.) | comparison draft fell back to tag descriptions with no pick | not fixed |
| F09 | S14 t5–t6 | "التوابل فيه دافية، مش حادة"; delivery-time half of a two-part FAQ question unanswered | critical (judge) | trait claim not in data; FAQ shortcut answers one question per message | not fixed |
| F10 | S18 t3–t6 | price range asked four times, never given; t6 identical reply + "تحبي أساعدك…؟" after "تمام شكراً" | high | D1 (owner's "450 لحد 1200" vs catalogue 750–2000) and the price checker cannot verify a range with no product; promotion script repeated (B21) | not fixed |
| F11 | S15 t7 | empty reply | critical (infra) | provider returned empty output (as B24); S15 verified only through t6 | not fixed |
| F12 | S19 t1–t4 | Bleu de Chanel dropped from the order; size re-asked; quantity reverted 1 → 2 | high | order extractor output (B18 class) | not fixed |
| F13 | S20 t1–t3 | transfer offer repeated; no explicit "can't confirm an order without products/details" | medium | promotions flow (B21) | not fixed |
| F14 | S09 t1–t3 | "La Nuit: متوفر." for Club de Nuit Intense | critical | B19 resolver placement | not fixed (needs production backtest) |

## Second final verification run (`final/attempt-2`, run `20261111T223012Z-56d5e1`, code after F01–F04)

Result: **6 pass / 14 fail**, mean judge score 7.79. Passing sets differ between the two final runs
(attempt-1: S01 S02 S05 S12 S16 S17; attempt-2: S01 S04 S08 S13 S16 S17); with one sample per
scenario and a one-high-issue-fails rule, single turns flip verdicts. New defects:

| ID | Where observed | Actual (verbatim, abridged) | Sev | Root cause | Status |
|---|---|---|---|---|---|
| F15 | S05 t4 | "إيه اللي يميز Alexandria II عن التاني؟" → "Alexandria II: ريحته خشبي وعودي وتقيل." only | medium | a comparison naming one perfume fell through to a one-perfume answer | fixed in `comparison_service` (unit test); **not verified live**: in the replay the classifier sent the same question to product_info, which still answers one perfume |
| F16 | S02 t6 | "سعر Aventus الأصلي 100 ملي، مش زجاجة الستور" → "الحجم ونوع الزجاجة المطلوبين مش متاحين حالياً" (original 100 ml at 2400 is in stock) | critical | bare "مش" before a bottle phrase was not a rejection, so the request became 100 ml + store bottle | fixed (`bottle_wording._REJECTED`), unit test shows old → "normal", new → none; S02 replay passed but the customer did not repeat that phrasing |
| F17 | S03 t3–t6 | "…لو عايز 3 بنفس الـ1600 ينفع؟" classified as an order → "تحب تطلب أنهي عطر؟ قولي اسمه."; then "آه، ينفع 3 زجاجات من عرض «اشتري برفانين 90 ملي وخد التالت هدية»" (2 × 1000 > 1600); promotion script repeated | high | classifier variance bypasses F02 (the total-question re-route covers product_info/faq/comparison, not order); a feasibility claim with no number escapes the price checker; B21 | not fixed |
| F18 | S07 t3–t4 | exchange condition for original-shape bottles stated as a general rule; price said to be unavailable and a transfer offered | critical (judge) | model paraphrase of a configured policy; no deterministic check on policy wording | not fixed |
| F19 | S14 t7 | a question about Pure XS's leather facet saved as a leather preference | high | same class as F06 (question/complaint trait words read as wishes) | not fixed |
| F20 | S12 t5, S15 t7 | empty reply | critical (infra) | provider empty output, as B24/F11 | not fixed |
| F21 | S06 t6–t7 | "Her وBloom" pair replaced by Billie Eilish | high | same as F05 | not fixed |

## Fixes (code) and their regression tests (`products/test_customer_simulation_fixes.py`)

| Defects | Change | Files |
|---|---|---|
| B01 | everyday-choices follow-up after "مش عارف"; a second "don't know" or "مش فارقة" delegates | `sales/scent_answers.py`, `router.py` — `ScentQuestionAfterDontKnowTests` |
| B02 | Franco gender category words (rigali, 7arimi…) | `sales/gender.py` — `FrancoGenderTests` |
| B03, B04 | "أي" accepted after negators; model-stated phrases not overridden/negated by neighbours; negative literal tag mentions kept | `sales/scent_preferences.py` — `ScentPolarityTests` |
| B05 | exclusions and stated budgets persisted on non-recommendation turns (deterministic, no extra call) | `sales/constraints.py`, `router.py` — `StatedPreferenceUpdateTests` |
| B06, R06 | per-recipient preference profiles saved/restored in `merge_preferences` | `conversation_service.py`, `router.py` — `RecipientProfileTests` |
| B07, B08, R01, R07, R11 | claim-checker negation (scent, notes, occasion), notes/occasion request detection, fallback renders supported facts or policy | `scent_description.py`, `reply_sanitizer.py`, `sales/scent_preferences.py`, `router.py`, `price_reply.py`, `performance_policy.py` — `ClaimNegationTests`, `NoteClaimNegationTests`, `RephrasedFallbackTests`, `ReplayFollowUpTwoTests`, `ReplayTwoTests` |
| B09, B10, B22, R04 | bare sizes; whole-number prices; size/ordinal price heuristics scoped | `product_formatting.py` (+3 test files updated for the price format) — `BareSizeInPriceQuestionTests`, `NaturalPriceFormatTests`, `SmallRoutingFixesTests`, `ReplayFollowUpTests` |
| B11, B12, B23, B25 | persona: no follow-up promises, configured FAQ facts, judgement rules (questions, "مش عارف", praise, jokes, anger, recipients); soft objections no longer hijack actionable requests; seller praise acknowledged | `ai/prompts.py`, `product_formatting.py`, `router.py`, `sales/appreciation.py`, `objection_service.py` — `NoUnkeepablePromisesInPromptTests`, `SoftObjectionDoesNotHijackRecommendationTests`, `SmallRoutingFixesTests` |
| B13, R05 | choice among on-table perfumes → comparison (incl. delegation and product-info "which of them") | `router.py`, `comparison_service.py`, `scent_description.py` |
| B14, R02 | specific total-budget limitation; hypothetical quantity reverted; total questions routed to the flow that computes totals; handoff announced | `ai/scent_recommendation.py`, `router.py` — `TotalBudgetLimitTests`, `ReplayFollowUpTests`, `EscalationNoticeTests` |
| B15, B16, B17, R03, R08, R13 | declinable alternate number (stored empty); shipping side-answers on both checkout routes; labelled total + configured shipping; offered-product disambiguation; deferral acknowledgement; "بالظبط"/"إجمالي" strictness scoped; budget-aware fallback; shipping-inclusive confirmation | `order_service.py`, `router.py`, `sales/constraints.py` — `CheckoutTests`, `ReplayFollowUpTwoTests`, `ReplayTwoTests` |
| B20, B21 | identical FAQ answer not re-sent; thanks after a declined transfer closes briefly | `router.py`, `sales/appreciation.py` — `SmallRoutingFixesTests` |
| R09 | browsing/catalogue questions → FAQ; grounded catalogue overview line | `ai/classifier.py`, `ai/prompts.py` — `ReplayFollowUpTwoTests` |
| R10, R12 | original-bottle questions quoted by code; resolver skipped only for pure praise; "مش قادر" price keyword scoped; confident absent-trait answers; "حاجة تانية" excludes on-table perfumes | `product_formatting.py`, `product_info.py`, `sales/objection.py`, `ai/prompts.py`, `router.py` — `ReplayTwoTests` |
| R14 | a deferral ("بعدين") with details missing acknowledges and stops asking | `order_service.py` — `TargetedReplayTests` |
| R15, R16 | a perfume singled out in the customer's last messages is the referent (not for "أنهي/اختار" questions); a comparison naming nothing uses the on-table perfumes first | `product_info.py`, `comparison_service.py` — `SingledOutReferentTests`, `TargetedReplayTests` |
| R17 | "total with shipping" answered from the open cart / latest live order plus a flat fee (two shipping amounts → quoted, never summed); the customer's own cart and orders are price evidence in `_rephrased`, via a per-turn `ContextVar` set in `route` | `order_service.py`, `router.py` — `CheckoutTests` (6 new tests) |
| F01 | availability asked with a price is stated by code for each quoted perfume (quotes are in-stock by construction) | `price_reply.py` — `FinalRunOneFixesTests` |
| F02 | "لو عايز N بنفس/في حدود…" sets the purchase quantity literally; the router already restores it when it does not fit | `sales/constraints.py` — `FinalRunOneFixesTests` |
| F03 | comparisons record the compared perfumes (`COMPARED_PRODUCTS:` line, written after the model call) so Arabic-named comparisons stay under discussion, in comparison order; never adds a perfume without its row | `comparison_service.py`, `sales/described.py` — `FinalRunOneFixesTests` |
| F04 | a shipping-fee fragment at checkout gets the store's shipping answer, never the general model | `router.py`, `order_service.py` — `CheckoutTests` |
| F15 | a comparison naming one perfume adds the single other perfume on the table | `comparison_service.py` — `FinalRunTwoFixesTests` |
| F16 | a bare "مش" before a bottle phrase rejects that bottle | `bottle_wording.py` — `FinalRunTwoFixesTests` |

Not fixed: B18 (order extraction merged two names — model output), B19 (Latin resolver
placement; the placement guard must be backtested on production conversations first), B21's
repeated transfer question in the promotions flow (pinned by `test_promotions` because consent
detection reads the latest offer), D1 (store data), R14's wrong pending line (extractor output).

Store decision, not changed: the order-success template says "فريق المبيعات هيتواصل معاك قريب".
The only thing behind it is a dashboard `Notification`; the judge (S11 targeted t5, high) and the
`unsupported_promise` check both flag it. It is the store's own wording, so it stays until the
owner confirms the team calls every order or changes the line.

Known checker false positives (deterministic findings the judge can dismiss; logic unchanged so
baseline and final are scored alike): `over_budget_total` uses a strict `>` while the app treats
750 vs 700 as within `BUDGET_TOLERANCE`, even after the customer accepted it twice;
`named_product_without_data` when the customer names their own ordered perfume; DB notes such as
"Leather, Woody Notes" flagged as unsupported scent tags; short names (Si, Pi, Her) matched inside
other words; "فريق المبيعات هيتواصل" flagged on the app's fixed success template.

## Round 2 (2026-11-12): owner decisions, then fixes in batches

Owner decisions applied:

1. "فريق المبيعات هيتواصل معاك قريب" is approved store behaviour. The judge rubric states it; the
   deterministic `unsupported_promise` check exempts that exact sentence and nothing else.
2. Production backtest allowed read-only. Run through a wrapper that sets every connection to
   `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` + `default_transaction_read_only = on` on
   creation and aborts unless the server reports `transaction_read_only = on`; Sentry off, in-memory
   cache, no model calls. Production lacks `products_product.scent_tags` (only missing column), so
   that column is deferred. Output: `backtest-placement-readonly*.txt`.
3. Store figures come from the business facts only: the computed price range left the catalogue
   overview; FAQ answers stating a percentage are kept out of the prompt (the 95% FAQ vs the facts'
   90%). The static FAQ "أصلي ولا تركيب" still says 95% when matched verbatim — store data to fix.
4. No "مش مسكر" inferred from a missing label: persona rule rewritten; the claim checker flags a
   negated label the perfume does not carry (except a recorded opposite, light/heavy, or an
   ingredient absent from recorded notes for oud/musk/leather, or an explicit uncertainty phrase).
5. Cap raised to USD 4.00, recorded in `ledger.json` → `cap_changes`.

Fixes (each with tests in `SecondRoundBatchOneTests` / `SecondRoundBatchTwoTests`):

| Scenario | Defect | Change |
|---|---|---|
| S12, S15 | an empty provider completion crashed the turn | `ai.client.chat` retries once on an empty answer; the meter raises `EmptyCompletion` (a `ValueError`, so `test_ai_budget` stays unchanged) which `chat` treats as empty |
| S09 | "Club de Nuit Intense" → "La Nuit: متوفر." | `naming.latin_disagreement`: a Latin run that carries name words the placed row and its house lack (flanker-only words and English/Franco filler excepted), or that shares none while ≥2 of its words are store name words, disagrees. `_named_in_message` drops such matches; the resolver checks every placed row and confirms through the narrow `confirm_placement`; a Latin name confirmed as none of the shortlist goes to `unplaced` (the denial path with its own check). Backtest on production: 4 of 25 Latin placements questioned (Ultra Male→Le Male ×2, 9PM Night Out→9PM ×2), 9 of 48 deterministic matches dropped (Club de Nuit→La Nuit, Dior Homme Sport→Sport, Black Phantom→Phantom, …), no false positives after adding promotion filler words and clipped-word matching |
| S09 | confirmed absence answered "1 Million: متوفر." | a fallback under ABSENCE_DENIED keeps the denial and describes the alternatives from their labels |
| S09, S03 | the same fallback resent word for word | `_not_a_repeat`: a fallback ≥0.9 similar to a recent reply becomes a short close or one honest line |
| S03, S18, S20 | the same transfer offer every turn | `promotion_service.reply`: with an invitation still open, a question about the offer with nothing new to add gets "ده كل المكتوب عندي عن العرض…" (no limitation/offer again); new details and checkout attempts keep the full boundary, with the invitation alternating between two wordings; a message ending in thanks gets no transfer question. `test_promotions` passes unchanged |
| S18 | "رينج للأسعار" read as a bare decline | `information_requested` covers prices/range |
| S18 | the price range never given | a general price-range question naming no perfume is answered with the owner's own business-facts line; the price checker accepts amounts from a business-facts line about prices |
| S19 | the merged "سوفاج بلو شانيل" (read as house Chanel) left pending in silence; later renamed into a second Sauvage | house questions are asked even when other items were carted; `_merge_lines` refuses to rename a line into another line's name; a saved pending line is not resolved into a perfume another line holds |
| S03 | total questions classified as checkout; an empty cart blocked the re-route | `_total_question_needs_recommendation` covers `order` (unless buying words) and ignores an empty cart |
| S03 | "لأخويا" asked "رجالي ولا حريمي؟" again | a recipient with a fixed gender starts with it; a combined budget follows to the next recipient |
| S06 | "الأولاني" after two single offers pointed at the second | `ordinal_products` accumulates consecutive single offers |
| S10 | musk excluded as a label only | `search_service.avoided_notes`: an excluded musk/oud/leather label also excludes that ingredient |
| S10, S14 | "212 VIB تقيل… مش ده اللي طلبته", "Pure XS فيه جلد؟" saved as wishes | an Arabic trait word in a clause opened by a perfume name or a pointer describes that perfume and is ignored as a preference |
| S07, S14 | "أدفى", "دافية مش حادة" passed the checker | words no label supports (warm, sharp, soft, luxurious, cool) are unsupported claims unless phrased as uncertain |
| S05 | "إيه اللي يميز X عن التاني؟" answered one perfume; a comparison object taken as the referent; praise not thanked | routed to comparison; `_subject_names` skips comparison objects; `_thanked` prefixes a short thanks to product-info/comparison replies |
| S13 | pick fallback repeated both descriptions | `_label_difference` adds the label difference when a pick or difference was asked |
| S07 | "هو ينفع في حدود الألف؟" got no price | budget-fit questions are price questions |
| S11 (judge input) | the judge read the order's `total_price` (items) as the full bill | the judge prompt labels it `items_total_excluding_shipping`; the baseline is re-graded with the same judge |

### Round 2 full run 1 (`round2-full/attempt-1`, run `20261111T235817Z-15e919`): 10/20

Pass: S01, S04, S05, S06, S08, S12, S13, S15, S17, S18. The run used the code at its start (code
sha in its `config.json`); fixes for S02, S03, S07, S09, S11 and S16 were written while it ran and
were not part of it. Failures and the batch-3 changes (tests in `SecondRoundBatchThreeTests`):

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | agreed that pepper is a top note (stored in the base) (t4); a "dry down" described from labels (t7) | prompt rule: a note sits only in its recorded layer, the dry down is the recorded base notes, and a customer's contrary note claim is not agreed with; checker `note-layer:` flags a note placed in a layer it is not recorded in |
| S03 | "الفريش ليا، والسويت لأخويا" merged into one fresh-and-sweet request; the same block resent on every total question (t2–t7) | `_per_recipient_scents` + `_pair_recommendation`: one search per person, each excluding the other's distinct scent, recommended together with "X ليك، وY لـأخوك"; the pair is kept while the quantity is 2. A total question that would resend the same block gets the total line alone. The pair code had a crash on the second person (tuple vs product), caught by the new test before any run used it |
| S07 | the exchange policy was "needs confirmation" in a comparison (t3); "تمام، Chance شكله أقربلها.. هتعجبها أكيد؟" was taken as yes to the side transfer offer (t4) | comparison prompt: store-policy side questions are answered from the approved store answers; `handoff_offer_reply`: an approval followed by talk about the pick (هتعجب/شكله/أقرب…) and nothing about the team is not consent |
| S09 | "قريبة من ريحته" after a confirmed absence offered our own alternatives as references (t3–t5); "Aventus أقرب لـ Club de Nuit" (t7); a smaller-size pitch on "هفكر… عشان سعره مناسب. تسلم" (t8) | after a recent denial, a likeness request uses the scent words given or asks for them once; `_similarity_claim_issues` flags a likeness to a perfume with no data unless hedged; the "thinking" objection gives the short close when the message closes and asks nothing (its "سعر" was a reason, not a price question) |
| S10 | "ومش خشبية تقيلة" saved heavy as a wish (t3); "فيه عود أو ريحته مسكرة؟" answered with the full notes list (t4); the perfume the customer had just turned down came back first (t6); the checker counted Si/Pi/Her as recommended inside "Passionfruit… Spices… Leather" | an adjective right after a negated trait shares the negation; `asked_attribute_answers`: ingredients answered from the notes, traits from the labels, an unrecorded trait "مش مؤكدة" (decision 4); `_rejected_subject`: "مش عايزه، رشحلي…" excludes the subject of our latest reply (the pronoun must close the phrase, so "مش عايزه حاجة مسكرة" is not a rejection); `naming.names_in` matches whole Latin word runs only (also used by the seller) |
| S11 | generic price defence when nothing fits the budget (criterion) | `without_value_defence` strips the defence sentences when nothing in the store fits the budget |
| S14 | "يعني ريحته حمضيات وخشب أكتر؟" saved citrus (t3); "ولا سويت خفيف؟" excluded sweet (t4; already fixed by a change made after this run started); the delivery-time half of a two-question message unanswered (t5); "طب Black XS ده…" answered for the plain Black XS right after Black XS L'Exces was offered (t7) | يعني/هل open a described clause; `_faq_lookup` maps delivery-time paraphrases to the store's own delivery FAQ and `_faq_answers` answers each question in order; `offered_extensions`: a short name means the one offered perfume whose name extends it, unless the customer says the plain edition |
| S16 | "طب بيقعد قد إيه بجد؟" → "محتاجة تأكيد… تحب أحولك؟" (t3) | a general performance question naming no perfume gets the store policy for all perfumes |
| S19 | "وواحد بلو دي شانيل 50 ملي" became "which Chanel?" and lost its size (t1) | `naming.house_mentioned_alone` reads per order line (cut at sizes, counts, commas, joining و): a house is bare only when it is the whole line, so "بلو دي شانيل" is a product and "وواحد امبيرو 50 ملي" is still a house |
| S20 | "أكدلي الأوردر دلوقتي" with nothing chosen got the turn-1 reply word for word (t3) | an order confirmation with no cart lines says why nothing can be confirmed (keeping the boundary and an offer); a promotions reply identical to an earlier one is reworded |

Test-infrastructure finding: the scratchpad settings file imported `perfume_ai.settings`, which loads
`.env`, so one round-1 test (`test_identical_faq_answer_is_not_resent`) reached the resolver and the
objection model with the real key on every suite run (about 2 calls a run). The settings now use a
placeholder key and a dead base URL, and log any request that escapes a mock; the full suite logs
none. The test now mocks both calls. The unmetered spend is charged to the ledger as an upper-bound
estimate of USD 0.10 (`phase: unmetered-test-calls`; about USD 0.03 measured at the simulations'
per-call cost).

### Round 2 replay batch 2 (`round2-batch2/attempt-1`, run `20261112T003818Z-be212f`): 3/10 after re-scoring

S09 and S20 passed. S02 passed on re-scoring (below). The rest failed on new or remaining defects:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | the judge dismissed both `unknown_latin_token` findings ("dry down" is the customer's term) and scored 9–10, yet the verdict was fail | **harness**: the judge echoed the finding as `"unknown_latin_token: 'dry'"`, and `verdict_for` compared the whole string, so an explicit dismissal was ignored. Codes are now read as their leading identifier; a code both confirmed and dismissed on one turn stays confirmed; a conversation-level finding is dismissed by a dismissal of its code. Every saved run was re-scored (`verdict-rescore.json`): this is the only verdict that changes — baseline 1/20, round-1 finals 6/20 and 6/20, round-2 full run 1 10/20 are unchanged |
| S03 | "أنا بحب الفريش للشغل، وأخويا بيحب السويت" not split (t2); "خليهم اتنين…" after "تحب نخليها 2 زجاجات…؟" sent to checkout, which carted a perfume nobody chose (t4–t7); "الطلب محفوظ" read as an order that exists (t9) | أنا/اني mark the speaker and a bare recipient ("وأخويا بيحب") counts inside one person's clause; that answer to the keep-the-count question routes to recommendation; the saved-cart line now says "اختياراتك محفوظة في السلة"; each person's search gets an even share of a combined budget |
| S07 | an exchange question ("لو مجاش على ذوقها أعمل إيه؟", "ينفع أبدلها؟") fell back to "تقصد أنهي عطر وحجم؟… أقولك السعر" (t3–t4), then a model paraphrase invented an eligibility rule (t5) | `_faq_lookup` maps exchange questions (asked as questions, one question per message) to the store's own exchange answer; the exchange policy's own fees count as store amounts under their subject; with no perfume and no price asked, a draft with an unsupported figure loses only that sentence |
| S10 | the same two perfumes after "ولا واحد فيهم…"/"مش عايز حاجة حلاوتها مش مؤكدة" (t3, t5, t8); full notes lists in recommendations (t4, t8); Fresh Vibe ("Sweet Orange") offered to "واضح إنها مش مسكرة" (t6, t8) | `_REJECTS_THE_TABLE` excludes the perfumes on the table; a recommendation answers an excluded ingredient ("مفيش عود ضمن نوتاته المسجلة") instead of listing every note; with sweet excluded, perfumes whose notes carry sweet materials rank after label-equal ones (ranking only — eligibility stays label-based, as `test_requested_and_negative_labels_use_assigned_style` requires; a first version that filtered them failed that test and was replaced) |
| S11 | "يعني مفيش أي حاجة أرخص من 750؟" resent the same block (t5, critical); the summary had 750 and 60 but no 810 (t7, criterion; met in run 1 with the same format) | a cheaper question that would resend the same perfumes, already at the store's lowest price, gets "X وY بـ750 جنيه للـ50 ملي، ودي أقل أسعار عندنا؛ مفيش حاجة أرخص منها حاليًا."; the summary adds "الإجمالي مع الشحن" for a flat fee unless the configured free-shipping threshold (1500) applies |
| S14 | "رشحلي تاني حاجة كده، وريحة Pure XS … عاملة إزاي؟" answered the second half only (t7, criterion); "والـ Black XS … ولا التوابل" excluded spicy (t5); the new budget in "والميزانية خليها لحد 1500. والتوصيل…؟" was lost (t6) | a product question that also asks for another pick is a recommendation, with the named perfume's labels appended; an article or "ريحة" before a Latin name still opens a described clause; the static-FAQ path saves stated preferences and acknowledges a new budget |
| S16 | "طب إزاي ده بـ1000 والأصلي بآلاف؟ إيه اللي بشتريه بالظبط؟" asked to retype a perfume name (t2); "أكيد هتقولي 48 ساعة زي الكل 🙄 طب ده الثبات بيختلف…؟" got the hours figure (t5, criterion) | authenticity/value words and "بـ1000"-style prices are not name tokens; a duration counts as requested only when the request is in the clause that carries it (clauses end at punctuation or an emoji; "قول" must be a whole word) |
| S19 | "عايز زجاجة الستور العادية مش الخاصة" — the customer (and the judge) read "الخاصة بينا" as "special"; the identical summary went out three times (t6–t9) | the store's label is kept; a request for the regular bottle when every line already is one puts "زجاجة الستور الخاصة بينا هي الزجاجة العادية عندنا…" above the summary (inside the confirmation hash) |

Tests: `SecondRoundBatchThreeTests` (39 tests at this point); full suite 2312, green, no request reaching the API.

### Round 2 replay batch 3 (`round2-batch3/attempt-1`, run `20261112T010305Z-6b73d0`): 2/7

S11 and S19 passed. S03, S07 and S14 failed on defects fixed while it ran (first three rows); S10 and
S16 on new ones:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S03 | "الإجمالي كده 1500 بالظبط؟ طب لو عايز 3 بنفس الـ1600 ينفع؟" classified as a promotion: the offer script twice, no arithmetic (t3–t4) | under a saved combined budget, a total/count question with no explicit offer term and no open promotion goes to the totals flow, whatever the classifier said |
| S07 | "طب هتعجبها أكيد؟ ولو مجتش على ذوقها أعمل إيه؟" — the exchange half went to the model, which made a bottle-specific condition general (t3) | `_policy_side_questions`: in product info, comparison and recommendation turns, an exchange or delivery-time question is answered with the store's own text and the model answers the rest |
| S14 | the delivery-time question beside a product question (t4) and beside a recommendation request (t5) unanswered; "الجلدية مش مؤكدة… والتوابل مش مؤكدة" about Pure XS, labelled leather and spicy (t6); "رشحلي حاجة سويت… وأنا هفكر في Wanted by Night كمان" answered "تمام، خد وقتك." (t9) | the side-question split above; the claim checker flags a recorded label stated as unconfirmed (the other half of decision 4); direct answers confirm recorded material labels and answer unrecorded ones from the notes; a "thinking" objection does not pre-empt an explicit request for a pick |
| S10 | the t4 pair resent word for word (t6); "إنت كده بتكرر نفس الترشيحات!" not acknowledged (t7); "هاجي أشمه في الفرع الأول… شكراً" read الأول as "the first perfume" and described it (t10) | a recommendation that would repeat a recent reply (not a total/cheaper question) is searched again without the perfumes shown; a repetition complaint gets "حقك عليا. دي اختيارات تانية:"; an ordinal does not reroute a closing message |
| S16 | "يعني زي الأصلي 100% ولا لأ؟" read as a likeness request: gender question, then "تقصد عطر شبه أنهي عطر؟" (t3–t4) | "زي/شبه الأصلي، الأوريجينال، البراند" is an authenticity comparison, excluded like "زي الفانيليا" |
| S07 | "هي بتحب الحاجات الخفيفة ومش المسكرة أوي" — هي read as a pointer to a perfume; "ومش بتحب الحاجات المسكرة" read as a sweet wish (latent; the model's extraction had covered it) | بتحب/بيحب/تحب/يحب are wish verbs and negation fillers; the soft-objection gate counts deterministic preferences too |

Tests: `SecondRoundBatchThreeTests` 45 tests; full suite 2320, green, no request reaching the API.

### Round 2 full run 2 (`round2-full/attempt-2`): failures and the fixes made while it ran

(Its code is the one recorded in its `config.json`; everything below came after it started.)

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | "طب سعر الـ90 من Aventus كام؟" got the notes list above the price; the price criterion counted as unmet (t7; all scores 8–10) | the price composer drops an unrequested notes line from a price answer |
| S03 | "خليهم 2، بس كده الإجمالي 1500؟" re-ran the pair search, and the already-offered picks ranked last: a different pair replaced the customer's (t4) | each person's pick is kept (with their scent) and reused while the scent is unchanged and no other options were asked for |
| S06 | "أنا محتارة أكتر من الأول… اختارلي انت" — "من الأول" (than before) read as "the first one"; the pick between two became "retype the name" (t7) | comparative idioms ("أكتر/أقل/أحسن من الأول", "زي الأول") are not ordinals; a single on-table referent is kept so the comparison adds the other one |
| S07 | "هي في حدود ١٠٠٠؟ وهفكر أطلبه" got "سعرها محتاج تأكيد" (batch 3 t4, medium) | a budget-fit question on the objection path gets code-rendered quotes |
| S08 | "أنهي فيهم أنسب للشغل؟" over three perfumes gave two of their recorded occasions (t2, criterion) | the comparison prompt asks for each perfume's recorded occasion when an occasion is asked |
| S09 | "غالي أوي كده 😅 هفكر وأشوف" got a smaller-size pitch and a defence of the price (t6) | a price objection that defers, with no question, gets "حقك يا فندم، خد وقتك…" and one open offer to look in a lower budget |
| S10 | "أنا مش بسأل على العود بس…" moved oud from excluded to wanted (t4, not caught by the judge); a generic apology for a violation that had not happened (t4, criterion) | "بسأل على/عن X" names a topic, not a wish; the persona tells the model to check the earlier pick's data on a complaint and not apologise for a mistake that did not happen; a recorded label is never "unconfirmed" |

Checker false positives seen in this run and left unchanged (the judge dismissed every one): the
eval price check matches product names without word boundaries ("Pi" inside "Pineapple") and does
not exempt "المجموع:" lines as it exempts "الإجمالي".
| S11 | "يعني الـ50 بـ750؟ … مفيش حاجة أرخص؟" on the objection path re-sent the quote with no answer (t4, criterion) | when nothing in the store fits the budget and the customer asks for cheaper, the objection reply opens with "مفيش حاجة أرخص من 750 جنيه عندنا حاليًا؛ ده أقل سعر عندنا." (t5's "تقصد أنهي عطر وحجم؟" could not be reproduced: with a correct extraction the order path carts the item and answers shipping; left open) |
| S15 | "تكون هادية فيها ورد" answered "مفيش رد ضمن نوتاته المسجلة" — my direct-answer code stripped the و of ورد as a conjunction (t3) | the unstripped word is kept when it is the known ingredient |
| S16 | "متاح لسوفاج كمان زجاجة اوريجنال بأحجام مختلفة" (one original size stocked) (t5); "الزجاجة نفسها أصلية من Dior" (t7) | claim checks for an original bottle "in several sizes" when one is stocked, and for a bottle called genuinely the brand's. These run in the eval checker too (it builds its facts with the same `product_claim_facts`), so deterministic grading became slightly stricter from run 3 on |
| S18 | "ولو اشتريت كل حاجة هتعملولي خصم؟ 😂" got the procedure alone; the joke criterion was unmet in run 2 and met in run 1 with the identical reply | a laughing message gets "هههه فكرة حلوة 😄" before the boundary (the boundary and offer stay, as `test_promotions` requires) |

### Round 2 full run 3 (`round2-full/attempt-3`, run `20261112T013256Z-0367c0`): 11/20, mean 8.86

Pass: S01, S03 (first time), S04, S08, S09, S12, S13, S17, S18, S19, S20. Failures and changes:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | "كنت بسأل عن الأصلي مش التركيب" got the original bottle's price with no word that it holds تركيب (t8) | the original-liquid pattern accepts "مش التركيب" with the article |
| S05 | "حاجة مميزة ومش منتشرة" got a mainstream perfume beside a niche one, and no word of which is niche (t3; criterion) | `wants_uncommon` orders niche/store-own perfumes first among equal label fits (it had no effect on ordering before), and the reply names the niche/exclusive status from the data when the model did not |
| S06 | "خلاص اختارلي انت" fell back to two descriptions and a difference with no pick (t6); "خلاص هختار Bloom… هطلبه بعدين، شكراً" got Bloom's description (t7) | a delegated choice in the fallback ends in "لو هختارلك: X؛ …", chosen by the saved preferences; a closing message on the product-info route gets a short close |
| S07 | "Cassilia… مش متوفر منه زجاجة اوريجنال حالياً" graded critical: the judge's facts listed Cassilia's two store sizes, and beside the store fact that "some perfumes" have original bottles it read the absence as unknown | **judge input**: a perfume with no available original variant now says so in the judge's database line ("original bottle: NONE available for this perfume"), as the seller's product row already did. Evidence: the same transcript failed 2/2 re-gradings before the change (`noise-check/S07-run3-rejudge-1`, `-2`) and passed after it (`-3-judge-input-fixed`, all scores 9–10) |
| S10 | a demand for a guaranteed non-sweet pick got options with unconfirmed sweetness, with 212 VIB Man again right after it was turned down as "مش مضمون" (t6–t7) | a guarantee demand under a sweet exclusion excludes the perfumes on the table and opens with "مقدرش أضمن 100% إن عطر مش مسكر من البيانات اللي عندي", states for each pick that sweetness is not among its classification and its notes have no vanilla or honey, and suggests trying it in the store (decision 4: nothing is inferred) |
| S11 | "المتاح منه هو الحجم ده بس" about 212 NYC Man, stocked in 50 and 90 ml (t3, critical) | claim check: "only this size" when more than one size is stocked |
| S14 | "Wanted by Night تقيل أوي ولا ينفع للسهرات؟" answered the heaviness only (t7, criterion) | with one perfume, the occasion question is read from the raw message (the scoped text loses its question mark), as `description_issues` already did |
| S15 | "نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟" got a fresh search and a perfume never mentioned (t5) | switching recipient records what was on the table for the one left; a recall question for that recipient is answered from it |
| S16 | "إيه الفرق اللي بيدفعني أشتريه؟" got the payment methods — my per-question FAQ split let the single keyword "دفع" match inside "بيدفعني" (t2); "وبتقعد قد إيه بجد؟ متقوليش كلام بياعين" asked to retype a perfume name (t3) | question fragments match store keywords as whole words only; more everyday words are not name tokens |

Judge noise, shown by re-grading identical transcripts: S18 run 2 (joke criterion unmet) passes on re-grading
(`noise-check/S18-run2-rejudge-1`); run 1 had passed with the identical turn-4 reply.

### Round 2 full run 4 (`round2-full/attempt-4`, run `20261112T015843Z-11a66d`): 14/20, mean 8.82

Pass: S01, S03, S04, S06, S07, S10, S11, S12, S13, S15, S17, S18, S19, S20. Failures and changes:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | "2000 للـ90؟ ده سعر الستور بتاعكم ولا الأصلي؟" (about Layton) answered for Aventus: on the objection path the resolver was asked before the perfume on the table, and placed Aventus (also 2000 for 90 ml) (t6) | a message that names no perfume uses the conversation referent first; "للـ90"-style tokens and more everyday words are not name tokens |
| S05 | "هبعتلك البيانات لما أبقى جاهز أكمل" right after "اختياراتك محفوظة في السلة" got the recap and the details request again (t7) | the deferral pattern covers "لما أبقى/أجهز/أفضى…"; a repeated deferral gets "تمام يا فندم، مستنيك." |
| S08 | "أنهي فيهم أنسب للشغل؟" — the fallback said "مناسبته للشغل محتاجة تأكيد" for two of three (t2; criterion; met in run 3) | in a choice among several perfumes for one occasion, the answer opens with the one(s) recorded for it and gives the others' recorded occasions without the "needs confirmation" clause (kept for a single-perfume question) |
| S09 | "فيها سموكي وفاكهة" lost smoky (t4); "يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟" got the bare gender question (t2; criterion) | English trait words in Arabic letters are label aliases (سموكي، وودي، سبايسي، فلورال، فروتي، اكواتيك، ليذر; also خشب); right after a denial, the gender question is preceded by "أيوه للأسف مش عندنا، بس أقدر ألاقيلك حاجة قريبة منه." |
| S14 | "المسكر أوضح في طابعه" (t7, critical); which trait leads left unanswered (t5) | claim check: a trait called stronger/clearer than another without a hedge; the direct answer adds "ومقدرش أحدد أنهي أوضح فيهم من البيانات اللي عندي" when two traits are compared |
| S16 | an explicit "بيقعد قد إيه بجد؟" got "ثباتها ممتاز" without the policy's hours (t3); "يعني عمليًا بيقعد قد إيه؟" was read as a size question — "ملي" inside "عمليًا" (t4); "قولّي تقدير عملي… مدة تقريبية" was not a duration question and asked to retype a name (t5) | "بيقعد" is a longevity word; "ملي" counts only as a word or after a number; "مدة تقريبية/تقدير عملي" are duration questions, and a follow-up reads the customer's own previous question as well as our reply; with no perfume on the table and none named or pointed at, a performance question gets the store policy for all perfumes |


Tests: `SecondRoundBatchThreeTests` now 72 tests; full suite 2347, green, no request reaching the API.

### Round 2 full run 5 (`round2-full/attempt-5`, run `20261112T022227Z-405503`): 13/20, mean 8.82

Pass: S01, S04, S05, S08, S10, S11, S12, S13, S14, S16, S17, S19, S20. All six run-4 fixes held
(S05, S08, S14 and S16 passed; S02 and S09 failed on other turns). Failures and changes:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | "الـbase فيها cedarwood وambergris وmusk، مش فانيليا" got "معاك حق؛ أنا ذكرت الفانيليا في قاعدة Aventus بالغلط" — vanilla is in Aventus's stored base (t4, high); "كده النوتات دي للتركيبة عندكم… هفكر وأقارن شوية، شكراً" got the whole تركيب pitch (t8, high) | claim check: a recorded note called a mistake (per sentence, since the note may come before the perfume's name) or denied ("مفيهوش فانيليا") is an issue; a layer statement ("مش ضمن الـTop") is not a denial. A closing message with no question mark is not answered from the static FAQ |
| S03 | "مفيش أي مصاريف تانية؟" got "1500 جنيه، من غير مصاريف الشحن", read as "no shipping" (t4, critical); "هاخدهم كده" after one quoted bottle each re-asked bottle type, then size (t5–t6); "والإجمالي يفضل 1500؟" confirmed 1500 with the 60 EGP fee left out (t7, critical) | the repeated-total line and the running cart total (when the total is asked) state the fee and the total with shipping, from the store's own facts and free-shipping offer; taking the quoted bottles "كده" fills size and bottle from the exact quotation when it quoted one variant |
| S06 | "تحبي الحاجة أخف ولا أقوى شوية؟" after "مش تقيلة" (t5, high) | a "lighter or stronger?" question is dropped once the customer has said how strong |
| S07 | "Cassilia… ومش حلو زيادة" — no record supports "not sweet" (t2, critical; owner decision 4); "في حاجة تانية قريبة من ذوقها؟" read as a likeness request: "تقصد عطر شبه أنهي عطر أو إصدار؟" (t4); "ينفع أبدله لو مجاش على ذوقها؟" answered with the 50 ml price, twice identical (t5–t7) | claim check: a negated sweetness ("مش حلو/مش مسكر/من غير سكر") without a hedge; "قريب من ذوقها/اللي بتحبه" is not a likeness cue; store-policy questions are split by clause as well as by question mark, a policy question with only a remark beside it gets the store's answer alone, and a repeat gets the exchange lines alone ("زي ما قلتلك: …"), then the first of them |
| S09 | my run-4 prefix "بس أقدر ألاقيلك حاجة قريبة منه" promised a likeness with no data on the missing perfume (t2; honest-similarity criterion unmet) | "ومعنديش بيانات عن ريحته أقارن بيها، بس أقدر أرشحلك من المتاح على حسب الريحة اللي بتحبها" |
| S15 | "Aventus شكله مناسب ليا، وكمان عايز حاجة لماما" names both recipients, so nothing switched: his mother's request inherited his woody men's profile, and "ليا بنفس الستايل" later got women's perfumes (t3, t6) | with both named, the recipient after the last request word is the one shopped for; on a switch, scent labels from the profile being left are dropped unless this message states them |
| S18 | "لو اشتريت كل حاجة هتعملولي خصم؟ 😂" ended on "تحب أحولك لمندوب يساعدك؟" (t4; browsing criterion unmet) | a joking discount question with nothing in the cart ends on "ولو حابب تعرف العروض الحالية، قولي." instead of a transfer question (`test_promotions` unchanged and green) |

Tests changed that I wrote earlier in this round (expectations updated to the new behaviour, disclosed here):
the S18 batch-3 joke test now asserts no transfer offer; the batch-3 side-question test now reads the
three-part result and expects a lone policy question to be answered; the S03 batch-2 total test expects
"والمبلغ ده من غير مصاريف الشحن" when the store states no flat fee.

Also changed after run 5 (tests in `SecondRoundBatchFourTests`): S02 t8's closing remark was also
read as a new goal by `request_mode` and sent to a fresh search (caught by the new test, which hit the
blocked API; a closing turn now cancels that override); S11 t3–t4 "لو السعر ثابت قولّي بصراحة… يطلع
فيه زيادة" was read as a longevity question ("ثباته ممتاز" twice; S11 passed) — price-stability
phrasing is removed before longevity detection and the product-info reply states that the price is the
quoted one and the only addition is the store's shipping fee (and its free-shipping offer); S16 t1
(low) "زجاجة اوريجنال بنفس شكل زجاجة البراند" had been rewritten to "…شكل زجاجة الستور الخاصة بينا"
— after "شكل" it stays the brand's bottle ("زجاجة البراند الأصلي"). The not-sweet check first broke the
original test `test_fixed_negation_does_not_reject_a_supported_description` (it pins "Dry Breeze فريش
ومش مسكر" as acceptable to a direct, message-less call); it is now scoped like `description_issues`'
unrecorded-trait check — applied in the reply pipeline (with the current message), not to legacy direct
calls — and that test passes unchanged.

### Round 2 replay batch 4 (`round2-batch4/attempt-1`, run `20261112T030241Z-35a55e`): 5/8

The seven run-5 failures plus S11. S02, S06, S07, S15 and S18 passed. Failures and changes:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S03 | "خلاص خليهم اتنين، بس مش هزود عن 1600 بالشحن" re-sent the identical pair block (t5, critical repeat). The run-5 fixes worked: t3 stated "وفيه مصاريف شحن 60 جنيه، فالإجمالي مع الشحن 1560 جنيه", and "تمام كده 1560 بالشحن، هاخدهم" carted both quoted 50 ml store bottles with no bottle/size questions | a keep-the-count (or shipping) message that would resend the pair gets "تمام، X ليك، وY لـأخوك." with the total and shipping lines |
| S09 | after a denial that listed ONE alternative (1 Million), "طب عندك حاجة شبهه؟" resolved "شبهه" to 1 Million; the next turn's "سموكي وفاكهة" was ignored for 1 Million lookalikes (t2–t4, high). The new honest wording at t2 met the similarity criterion | right after a denial, a pronoun likeness request is not resolved to the perfumes our denial listed (an explicitly named one still is) |
| S11 | no verdict: the judge request timed out (`APITimeoutError`), so the harness failed it as "judge unavailable". The transcript shows "طيب ليه قلت مفيش حاجة تحت 700؟" answered "معاك حق، أنا غلطت لما قلت مفيش حاجة تحت 700" while nothing under 700 is stocked (t3), and "خلاص هاخد الـ50 من الأولاني، الشحن بكام؟" left the cart empty, so t7 asked "تحب تطلب أنهي عطر؟" (t6–t7; the same message carted the perfume in run 5 — extractor variance, not fixed) | in the reply pipeline, a mistake admitted in answer to "ليه قلت…؟" is a claim issue; the retry is told to check the earlier statement against the data |

### Round 2 replay batch 4, attempt 2 (`round2-batch4/attempt-2`, run `20261112T031524Z-3db2f0`): stopped by the provider

S03, S09 and S11 were replayed on the code above. After 5 completed calls every request returned
`429 insufficient_quota`: "Your project has reached its configured enforced spend limit" — the OpenAI
project's own limit, not this ledger's cap (ledger at USD 2.66 of 4.00 when it happened). No transcript
got past turn 2; all three are recorded as failed and kept. The meter charged the 12 failed requests at
their reserved maximum (USD 0.0879, status `uncertain_charged_maximum`), so the ledger overstates the
real cost of that run. No further paid call was attempted: the project limit can only be changed by the
owner, and the task forbids raising caps.

Earlier round-2 test adjustments, disclosed for completeness: the S14 batch-3 Pure XS direct-answer test
compares the first two lines, because the run-4 prominence fix appends a third ("ومقدرش أحدد أنهي
أوضح فيهم…"); the S10 `avoided_notes` assertion was edited during batch 2 and restored to its original
`["musk"]`, so it is net unchanged. No pre-existing (pre-round-2) test file was modified in round 2.

### After the owner raised the provider limit (2026-11-12, later the same day)

Free work done before spending again (model stubbed, tests in `SecondRoundBatchFourTests`):

| Scenario | Defect | Change |
|---|---|---|
| S11 | The batch-4 cart miss was **not** extractor variance. The ledger shows turn 6 made a classify call and one prose call, and no order extraction: after four near-identical price answers the anti-repetition redirect, which applies to every route except recommendation, sent "خلاص هاخد الـ50 من الأولاني، الشحن بكام؟" to the general model. The cart stayed empty and turn 7 asked "تحب تطلب أنهي عطر؟" | the redirect no longer applies to `order` / `order_cancel` turns. The new test fails on the old condition and passes on the new one |
| S09 | with "شبهه" no longer resolved to our own alternative, turn 3's "بحب الريحة اللي فيها سموكي وفاكهة" got the scent question again: "حاجة/عطر/الريحة اللي **فيها** X" was read as ingredient wording, and for a label with no ingredient behind it (smoky, fruity, spicy, citrus, fresh…) the wish was dropped, the model's own tag with it. Only the negative form survived. Older than this task | a generic noun leading "فيه/فيها" makes a plain label a style wish. Oud, leather and musk stay ingredient requirements, as the original test `test_literal_ingredients_do_not_add_a_style_requirement` pins (my first version broke it; narrowed, it passes unchanged). A question about a perfume ("Aventus فيه سموكي؟", "فيه فواكه؟") is still not a wish |

Paid checks:

- `noise-check/S11-batch4-judge-retry`: the batch-4 S11 transcript whose judge call had timed out, graded
  (fail, on the two defects above). It doubled as a one-call probe that the provider accepts requests.
- `round2-batch4/attempt-3` (run `20261112T182330Z-9a99ef`), S03, S09, S11: **S03 pass 9.7, S11 pass 9.3**,
  S09 fail on one criterion:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S09 | "رجالي، اللي عاجبني في Club de Nuit ريحته فيها سموكي وفاكهة" describes the missing perfume, so the wish parser rightly leaves it alone, and with no likeness word the after-denial path did not use it either: the scent question went out again (t3, medium) | right after a denial, scent labels in the message are used (and saved) when the request has no other scent preference |
| S09 | "طب أنهي واحد فيهم أقرب لـ Club de Nuit؟ وسعره كام؟" got the two price blocks only; the judge read that as choosing the first without support (t5; honest-similarity criterion unmet) | a "which is closer to <a Latin name we do not stock>" question after a denial opens with "معنديش بيانات عن ريحة Club de Nuit أقارن بيها، فمقدرش أقول أنهي أقرب له." |

- `round2-batch4/attempt-4` (run `20261112T183007Z-199114`), S09 alone: **pass**, all scores 9–10.

Not changed: S09 t6's "ترتيب الإحساس مختلف شوية" (model prose, medium); S16's "اكتبلي اسمه تاني" comes
from a product-info prompt rule (medium). Observed but outside the simulated store: in a store with no
promotions configured the classifier is not run before the static FAQ check, so "هاخد الـ50… الشحن
بكام؟" would get the shipping answer alone; not fixed (the simulated store has promotions).

Tests: `SecondRoundBatchFourTests` 28 tests; full suite 2375, green, no request reaching the API
(`tests-round2-28.log`).

### Round 2 full run 6 (`round2-full/attempt-6`, run `20261112T183107Z-e5513c`): 17/20, mean 9.07

Pass: every scenario except S11, S16 and S20. S02 passed a full run for the first time. Failures and
changes (tests in `SecondRoundBatchFiveTests`):

| Scenario | Defect (turn) | Change |
|---|---|---|
| S11 | "غالي عليا" with nothing at or under 700 was left to the model: "مفيش عندنا اختيار أقل من ميزانيتك" (leaves "exactly 700" open, never says 750 is the lowest; criterion) and, for "الشحن 60؟ كده الإجمالي 810، غالي عليا", only "فاهم إن مصاريف الشحن زوّدت الإجمالي عليك" (criterion) (t2, t5) | when nothing in the store fits the budget the objection reply states it in code: "فعلاً عندك حق، مفيش عندنا حاجة في حدود ميزانيتك أو أقل منها حاليًا، وده أقل سعر عندنا." (the last clause only when the quoted perfume is the cheapest we sell), plus the store's own shipping answer and "ومفيش عندي شحن أقل من كده" when the objection is about shipping or the total. The model's sentences that restate these are dropped |
| S16 | "الثبات ده على الجلد ولا الهدوم؟" got "اكتبلي اسم العطر تاني… عشان أتأكد من المقصود", then "ده كل اللي متسجل عندي عن ده" (t4–t5; question quality 4) | answered in code as not recorded ("المعلومة دي مش مسجلة عندي: المدة مش محدد فيها إذا كانت على الجلد ولا على الهدوم…"); asked again, the team is offered instead of the same sentence. "اكتبلي اسمه تاني" is removed from a product reply when the turn had no unreadable name |
| S20 | "ماشي خلاص، مش هطلب حاجة. سيبك" — its "ماشي" was read as yes to "تحب أحولك لمندوب يساعدك؟" and the conversation was handed to staff (t5, high; criterion) | an approval followed by giving up ("مش هطلب/هشتري/هاخد", "مش عايز حاجة", "سيبك", "انسى") is not consent; with an offer open it gets "تمام يا فندم، ولو احتجت أي حاجة أنا موجود." before the offers flow can add a sales line |

Found in scenarios that passed run 6:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S02 | "طب والـ Layton top/middle/base بتاعته إيه؟" lost Layton: my round-2 Latin-name rule counted "top"/"notes"/"base" as extra name words, so the row was dropped and the customer was offered the team for notes we hold (t3, medium). A regression from the S09 placement fix | perfume-talk words (notes, top, middle, heart, base, dry down, longevity, projection…) no longer count as name words. Scent adjectives and "original" still do ("Light Blue", "Original Santal" are names), and "Club de Nuit Intense" is still not placed on La Nuit |
| S07 | "هفكر أطلبه إن شاء الله. بس لو مجاش على ذوقها ينفع أبدله؟" went to the objection path, whose paraphrase left out the fees and the free in-store exchange (t5, medium) | the store-policy split runs before the objection path too |

### Round 2 replay batch 5 (`round2-batch5/attempt-1`, run `20261112T185526Z-bef1b9`): 2/3

S16 passed (9.3), S20 passed (8.7). S11 failed badly (mean 4.1) on a different conversation, which
exposed one mistake of mine and a group of older defects:

| Scenario | Defect (turn) | Change |
|---|---|---|
| S11 | my new sentence carried "700" and "750". `router._rephrased`'s price check rejects any amount written beside a code-rendered quote, so both drafts failed and the customer got the bare price block (t2; both criteria unmet again). My unit test had called `handle_objection` directly and never ran that check. The run-2 line "مفيش حاجة أرخص من 750 جنيه…" had the same flaw all along | the sentences carry no amount (the quote under them does); the tests now run the whole routed turn |
| S11 | with 750 already the lowest price: "طب في بديل أرخص شوية…؟" got "تقصد عطر شبه أنهي عطر أو إصدار؟" twice ("بديل" read as "like which perfume", then "مش فارق شبه أنهي عطر" read the same way); "رشحلي… أقل من 750" got the same 750 perfume; "مفيش حاجة أرخص منه؟" got two more at 750; "دول برضه بـ750؟ مفيش حاجة أرخص؟" got "تقصد أنهي عطر وحجم؟" (t4–t8, four high issues) | a cheaper question whose price (stated, else the lowest we quoted lately) is not above the store's lowest gets "مفيش حاجة أرخص من 750 جنيه عندنا حاليًا؛ ده أقل سعر عندنا." on every route; "بديل أرخص/تاني/أحسن" is not a likeness cue; "مش فارق/مش مهم … شبه" withdraws a reference like "مش لازم"; the reference question is never asked twice in a row |

A first version of the "مش فارق" change hid "مش لازم يبقى شبه 212" from the withdrawal check and failed
the original test `test_explicit_withdrawal_clears_both_fields_and_provenance`; it was moved into the
existing negation pattern and that test passes unchanged (`tests-round2-30.log` red, `-31` green).

`round2-batch5/attempt-2` (run `20261112T190546Z-cfeec5`), S11 alone: all four criteria met, all scores
6–8, and still a fail, on one turn:

- **Open, owner decision.** After the order summary the customer asked "الشحن بكام؟" (answered), then
  said "تمام أكد", and got the identical summary again (t8: critical repeat, high). The order extractor
  accepts a confirmation only when the summary is the message immediately before it. The code's own
  consent check (`_summary_was_shown`: summary sent for this exact cart) was satisfied. Accepting the
  confirmation there changes the order-confirmation rule, which the owner said must not change, so it
  is not changed.

Tests: `SecondRoundBatchFiveTests` 11 tests; full suite 2386, green, no request reaching the API
(`tests-round2-31.log`).
