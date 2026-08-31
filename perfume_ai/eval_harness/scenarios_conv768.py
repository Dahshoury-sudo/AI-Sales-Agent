# -*- coding: utf-8 -*-
"""Replay of conversation 768: two perfumes from one line treated as one perfume.

The customer wanted a men's date fragrance on a 1300 budget and was recommended Stronger With
You Intensely at 780. Three turns later, asked for something sweeter, they were offered
Stronger With You — the base of the same line, at 700 — presented as a different perfume, which
it is. They noticed: "مش انت لسا مرشح سترونجر ده من شويه ؟". The bot agreed ("صح، Stronger With
You رشّحته قبل كده"), collapsing the two into one. Then: "انت قولت سعرين مختلفين للسترونجر" — and
the bot apologised, "أعتذر على اللبس", and declared 700 "ده السعر الصحيح".

Both prices were correct. 780 was Intensely, 700 was the base. The bot confessed to an error it
had not made and retracted a real price, which is the trust failure — a customer who now believes
Intensely costs 700.

Two seams, both fixed:

  * `described.under_discussion` tested catalogue names against the reply with a substring, and
    "Stronger With You" is a prefix of "Stronger With You Intensely". Turn 1 named only Intensely
    but the search had injected both rows, so the base satisfied both halves of the test and was
    recorded as under discussion without ever having been said. `ranking.WEIGHTS["continuity"]`
    (2.5, above the sum of every slot that shifts as a conversation narrows) then promoted that
    phantom into turn 3's answer. `naming.names_in` consumes the longest match at each position,
    so a reply naming only Intensely yields only Intensely.
  * Nothing told the model the two rows were different perfumes. `product_info` instruction 1
    guards the case where the data holds a *completely different* perfume from the one asked
    about (conversation 738's Acqua di Gio), and a flanker does not look like that.
    `product_formatting._line_mate_note` now names a perfume's line-mates in its own block, and
    `product_info` instruction 13 forbids apologising when two prices were two line-mates.
"""

SCENARIOS = [
    {
        "id": "CONV768",
        "category": "regression",
        "persona": "hard_budget",
        "turns": [
            "عايز برفان رجالي حلو ينفع للديت",
            "1300",
            "في اي تاني طيب يكون مسكر",
            "مش انت لسا مرشح سترونجر ده من شويه ؟",
            "انت قولت سعرين مختلفين للسترونجر",
        ],
        "probe": (
            "Replay of conversation 768. The catalogue holds THREE distinct perfumes on the "
            "Stronger With You line, all Emporio Armani, with different notes and different "
            "prices: Stronger With You (50ml 400, 90ml 700), Stronger With You Intensely "
            "(50ml 450, 90ml 780) and Stronger With You Absolutely. They are NOT the same "
            "perfume, not the same scent and not the same composition. "
            "❌ CRITICAL trust failure on turn 5: the customer is pointing at two prices they "
            "were quoted for what they called 'سترونجر'. If those prices belonged to two "
            "different perfumes on this line, apologising ('أعتذر على اللبس'), calling it a "
            "mix-up, retracting either price, or declaring one of them 'السعر الصحيح' is the "
            "defect — both were correct. The correct handling names the two perfumes apart and "
            "says which price belongs to which, or asks which one they mean before quoting. "
            "❌ CRITICAL on turn 4: agreeing that the perfume just offered is the one recommended "
            "earlier, or otherwise implying the base and Intensely are one perfume. "
            "❌ On turn 3 ('what else is sweet'), offering another perfume from the same line "
            "without saying plainly that it is a different perfume is what set the confusion up. "
            "❌ Never quote a price for any of these three that is not one of the figures above, "
            "and never offer or price a line-mate whose data was not injected — the ⚠️ line "
            "naming it is a disambiguation aid, not a product listing. "
            "The stated budget is 1300, so every 90ml here is within it."
        ),
    }
]
