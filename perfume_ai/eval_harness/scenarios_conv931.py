# -*- coding: utf-8 -*-
"""Replay of conversation 931: a price inside the budget, called over it, twice.

The customer's budget was 1200. Versace Eros' 90ml is 1019. The reply (turn 10) said:

    الـ90 ملي بـ1019 جنيه ⚠️ يعني أغلى من ميزانيتك بـ353 جنيه

353 is not an overage. It is 1019 − 666 — the gap between Eros' two *sizes*. The customer
objected ("ازاي اعلي من ميزانيتي"), and turn 11 repeated the claim with the same number. They
objected again with the budget restated, and the third reply dropped the subject rather than
retracting it.

The backend was right and every layer of it agreed. `preferences["max_price"]` was 1200,
`budget_tier(1019, 1200)` is "in", and both sizes reached the model labelled
`✅ (داخل الميزانية)`. Nothing told it 1019 was over anything. One line below those labels, in
the same per-product block, `value.size_value_note` printed `أغلى بـ 353 جنيه في الإجمالي
(1019 مقابل 666)` — a quotable figure introduced by the same word, whose referent is only named
in the parenthetical. The model lifted the number, re-attributed it from "the 50ml" to "your
budget", and lifted the ⚠️ glyph too, which four prompt rules bind to the over-budget marker.

Not a guess: turn 13 injected the same product block *without* budget labels, because
`product_info` passes no `max_price`, and made no over-budget claim at all.

Turn 11 is why the deterministic guard had to live at persistence time. That turn ran with an
empty context — nothing was injected — so the only place the falsehood could have come from is
turn 10's own reply, read back through `build_llm_history`. A wrong sentence that reaches
`Message.content` becomes an example of acceptable output for every turn after it, which is how
one bad clause became a loop the customer could not argue the agent out of.

Two earlier attempts at this failed on the prompt side, and their premise is written into
`recommendation.py`: "a ✅ line has no figure to quote, so the request is unfillable rather than
forbidden." `size_value_note` is the counter-example, one line away. The check that grades this
scenario is `checks.check_false_over_budget`, which reads the claim as a falsifiable statement
instead of a licence — the patterns existed before, but only to *excuse* a turn that named an
overage, never to ask whether the overage was real.

Turn 9 carries a second failure worth freezing beside it. Versace + female + 1200 matched no
product, and the reply was "حالياً دي كل الخيارات المتاحة اللي بتناسب ميزانيتك بالظبط" — an
appeal to a list that was empty, blaming the budget. Versace has exactly one perfume in this
catalogue and it is a men's, so brand-and-gender was the binding constraint and the budget was
never involved; twelve women's perfumes sit under 1200.
"""

SCENARIOS = [
    {
        "id": "CONV931",
        "category": "regression",
        "persona": "hard_budget",
        "assert_budget": 1200,
        # Verbatim from the transcript, typos and all. The four cart turns are kept because the
        # basket is what makes turns 10-12 hard: by then a real 2119 total exists alongside a
        # 1200 per-bottle budget, and the correct reply has to keep those two numbers apart.
        "turns": [
            "عندكو اي ؟",
            "حريمي",
            "المسكره",
            "بكام الاتنين",
            "ماشي عايزه 50 ملي من كل واحد اجربهم",
            "عندكو سترينجر ويذ يو ؟",
            "زودلي اتنين 50 ملي",
            "عندكو حاجه من فيرزاتشي ؟",
            "1200",
            "بص انا عايزاه لجوزي مش ليا",
            "ازاي اعلي من ميزانيتي",
            "انا ميزانيتي 1200",
        ],
        "probe": (
            "Replay of conversation 931. The stated budget is 1200 EGP, per bottle. "
            "Versace has exactly ONE perfume in this catalogue: Eros, a MEN'S perfume, 50ml 666 "
            "and 90ml 1019. Both sizes are UNDER 1200. There is no women's Versace at any price. "
            "The cart built over turns 5 and 7 holds 1 × Good Girl 50ml at 650, 1 × La Vie Est "
            "Belle 50ml at 669, and 2 × Stronger With You 50ml at 400 each — a real order total "
            "of 2119. Good Girl is 90ml 1083, La Vie Est Belle is 90ml 1046. "
            "❌ CRITICAL on turns 10, 11 and 12: saying or implying that Eros' 90ml at 1019, or "
            "its 50ml at 666, is above / outside / more than the customer's budget. 1019 < 1200. "
            "Both sizes are in budget and the injected context labels them so. "
            "❌ CRITICAL: quoting 353 as a gap between a price and the budget. 353 is 1019 − 666, "
            "the difference between the two SIZES of Eros. Any sentence that puts that number "
            "next to the word ميزانية is false however the arithmetic got there. The same goes "
            "for any other difference figure lifted from the value note: the value comparison is "
            "between two bottles of the same perfume and has no budget content in it. "
            "✅ Required on turn 10: both Eros sizes named with their real prices and stated to be "
            "within the budget — and Eros named as a MEN'S perfume, which is what the customer "
            "asked for once they said لجوزي. "
            "✅ Required on turn 11 ('ازاي اعلي من ميزانيتي'): a plain retraction. The customer is "
            "correct and the previous reply was wrong. Say so — 'معاك حق، الاتنين داخل ميزانيتك' "
            "— and give the two prices again. "
            "❌ CRITICAL: repeating the over-budget claim on turn 11 or 12 after being challenged. "
            "Repeating a false statement the customer has just disproved is worse than making it "
            "the first time, and it is what turned this conversation into a loop. "
            "❌ CRITICAL: answering turn 11 or 12 by changing the subject. An objection to a "
            "factual error is answered by correcting the error, not by moving on from it. "
            "✅ Allowed, and true: a statement about the ORDER TOTAL being more than 1200 — 2119 "
            "really is more than 1200. The budget is per bottle and the basket has four bottles "
            "in it, so this is a different claim from the one banned above and it is not a "
            "finding. It must be about the total, by name, and never about a single size. "
            "✅ Required on turn 9 (Versace, women's, 1200): name the actual reason nothing "
            "matched — Versace's only perfume here is a men's — and offer either Eros for the "
            "husband or a women's perfume from another brand. Twelve women's perfumes sit under "
            "1200. ❌ CRITICAL on turn 9: blaming the budget, or implying a list of matching "
            "options exists ('دي كل الخيارات المتاحة اللي بتناسب ميزانيتك') when the search "
            "returned nothing. The customer was never shown options to be at the end of. "
            "❌ Never quote a price for any perfume that is not one of the figures above."
        ),
    }
]
