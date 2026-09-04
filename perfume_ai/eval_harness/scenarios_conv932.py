# -*- coding: utf-8 -*-
"""Replay of conversation 932: the way out was offered, accepted, and offered again.

The customer wanted a women's Versace. There isn't one — Versace's only perfume in this
catalogue is Eros, a men's — so the reply said so and offered two ways forward:

    تحب أجرب أجيب لك من نفس البراند بس رجالي، ولا من براند تاني حريمي؟

They took the second option. Three times.

    شوفلي طيب اي حاجه حريمي تانيه   →  the same offer
    من براند تاني حريمي              →  the same offer
    يعم منا قولت من براند تاني حريمي  →  the same offer

Replies 4 and 5 are byte-identical (md5 `16927f0d`); 2, 3 and 4 are freshly generated
near-duplicates at 117, 106 and 112 characters. So this is not one string being replayed — it is
the model being handed the same situation five times and answering it consistently.

The situation never changed because the acceptance had nowhere to go. `brand` lives in
`PERSISTED_PREFERENCE_KEYS`, and the intent schema has one slot for it — a name or null, where
null means *unspecified*, never *withdrawn*. "براند تاني" names no replacement, so the extractor
had nothing to override with, and `merge_preferences` gap-filled Versace back from
`conversation.preferences` on every turn. Worse, `to_save` is built from the post-gap-fill dict,
so the stale brand was rewritten to the database each turn and could never age out: conversation
932's stored preferences still read `{'brand': 'Versace', 'gender': 'female', 'max_price': 1200}`.
`search_products` then ANDed brand with gender exactly as asked and returned nothing, five times,
about a shop holding twelve perfumes the customer would have bought.

The escape hatch could not fire, for three independent reasons. `_contradicted_keys` is gated on
`_is_reversal`, whose markers are all about a customer changing their own mind ("غيرت رايي",
"بلاش") and none about answering a question we asked; `brand` appears in no entry of `_AXES`; and
its shape is wrong anyway, since it clears an axis only when the *new* intent has a value on that
axis, which a withdrawal by definition does not. Nor did the repetition guards catch it:
`_count_recent_repetitions` needs 3 and walks back from the last reply, breaking at the first
message under 0.7 similarity — inside an 8-message window the oldest bot message is the budget
question, which breaks the chain and caps the counter at 2 however many identical replies ship.

This is the unbuilt half of the fix that landed for conversation **931**, whose replay sits
next door and shares this exact Versace + حريمي dead end. That work made the no-match reply
*honest* — it names the binding constraint instead of blaming the budget — and 931 has had zero
findings since. Its turn list ends at the offer. Nothing in the suite replayed a customer
accepting one, and `tests.py`'s `test_it_offers_to_relax_one_of_the_named_constraints` asserts
only that the prompt asks for the offer, not that the next turn can act on the answer. A reply
that offers a choice is half a feature; the graded half was the half that already worked.

Every `internal_context` in the original transcript is `""`, so none of this was visible in the
dump — the no-match branch hardcoded `context = ""`. It now writes `PENDING_RELAX:` naming the
filters that emptied the search, which both records what was offered and lets a terse "التانية"
be read as an answer to it. Watch `context_chars` on the no-match turn: every turn of the real
conversation recorded 0.
"""

SCENARIOS = [
    {
        "id": "CONV932",
        "category": "regression",
        "persona": "hard_budget",
        "assert_budget": 1200,
        # Verbatim from the transcript, typos and all. The last two turns say the same thing
        # twice because the customer had to — the second one ("يعم منا قولت") is them losing
        # patience, and it is the turn that makes this a reported bug rather than a rough edge.
        "turns": [
            "عايز حاجه حريمي من فيرزاتشي",
            "1200",
            "شوفلي طيب اي حاجه حريمي تانيه",
            "من براند تاني حريمي",
            "يعم منا قولت من براند تاني حريمي",
        ],
        "probe": (
            "Replay of conversation 932. The stated budget is 1200 EGP, per bottle. "
            "Versace has exactly ONE perfume in this catalogue: Eros, a MEN'S perfume, 50ml 666 "
            "and 90ml 1019. There is no women's Versace at any price, so the search on turn 2 "
            "correctly matches nothing. "
            "Twelve products match women's under 1200 once the brand is dropped, and EVERY size "
            "of all twelve is under 1200 — nine women's (Coco Mademoiselle 534/1006, Bloom "
            "549/1067, Libre 561/927, Flowerbomb 577/928, Good Girl 650/1083, La Vie Est Belle "
            "669/1046, Black Opium 677/883, Light Blue 691/852, Chloe Eau de Parfum 692/1058) "
            "and three unisex (Oud Wood 540/1160, Ombre Leather 592 or 700 for 50ml / 873 for "
            "90ml / 1000 for 100ml, Baccarat Rouge 540 at 686/1136). "
            "✅ Required on turn 2: name the actual reason nothing matched — Versace's only "
            "perfume here is a men's — and offer a way forward. Either half of the real offer is "
            "correct: Eros as a men's Versace, or a women's perfume from another brand. "
            "❌ CRITICAL on turn 2: blaming the budget. 1200 covers both Eros sizes and every one "
            "of the twelve alternatives; the brand-and-gender pair is what emptied the search. "
            "Turns 3, 4 and 5 are the customer ACCEPTING the second option. 'شوفلي طيب اي حاجه "
            "حريمي تانيه', 'من براند تاني حريمي' and 'يعم منا قولت من براند تاني حريمي' all mean "
            "the same thing: drop Versace, keep women's. Turn 5 says so with visible irritation "
            "at having to repeat it. There is no ambiguity to resolve on any of these turns. "
            "✅ Required on turns 3, 4 and 5: actual women's perfumes named, with their real "
            "prices, from the list above. A recommendation, not another question. "
            "❌ CRITICAL: re-offering the same either/or after the customer has chosen one of its "
            "options — 'تحب من نفس البراند بس رجالي، ولا من براند تاني حريمي؟' or any rewording of "
            "it. Asking a question the customer has just answered is the whole bug, and it is a "
            "finding on turn 3 as much as on turn 5. "
            "❌ CRITICAL: saying nothing matches, nothing is available, or that these are all the "
            "options, on any of turns 3, 4 and 5. Twelve products match once the brand is dropped. "
            "❌ CRITICAL: still treating Versace as a requirement after turn 3 — offering Eros as "
            "though it were a women's perfume, or explaining again that Versace has no women's "
            "line. The customer stopped asking for Versace on turn 3 and never asked again. "
            "❌ CRITICAL: re-asking the budget on any turn after turn 2. It is 1200 and it was "
            "given in one word. "
            "✅ Allowed: asking about notes or an occasion INSTEAD of recommending, but only once "
            "and only if it does not repeat the brand/gender question — with twelve matches in "
            "hand, naming two or three of them is the better reply and the one being graded for. "
            "❌ Never quote a price for any perfume that is not one of the figures above."
        ),
    }
]
