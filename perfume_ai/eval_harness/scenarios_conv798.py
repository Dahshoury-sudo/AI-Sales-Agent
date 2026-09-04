# -*- coding: utf-8 -*-
"""Replay of conversations 798 and 799: the promise that lost its subject on the next turn.

Two transcripts, one failure. A customer names a perfume this store does not carry, the bot
correctly says "لحظة أتأكدلك منه" — and volunteers an unrelated perfume in the same breath. Then
the customer comes back to collect, and the reply is about the volunteered perfume:

  798  "عندك لادور بخور ؟"  → defer, plus a pitch for Dior Homme Sport and Bleu de Chanel
       "ها لقيت اي ؟"        → "لقيت Dior Homme Sport متوفر عندنا" and two prices. A completed
                               lookup that never happened, reported on a perfume nobody asked
                               about, in answer to "what did you find?".
       "بقول لادور بخور"      → correct: "لحظة أتأكدلك منه." This turn is the control — the
                               customer re-typed the name, so the ordinary path fired.

  799  "عندك الكساندريا 2؟"  → defer, plus a pitch for Stronger With You
       "اتأكدلي منه"          → "Stronger With You متوفر عندنا، والـ90 ملي بـ700 جنيه..."

`product_info` hung the pending-lookup block, the ⚠️ "not the perfume asked about" header and the
deferral rules all on one flag, `named_but_unresolved`, which is a fact about the current message.
A chase names no perfume, so `resolve_products` answers the pronoun in "منه" with the only perfume
on offer, `products` comes back full, and all three guards switch off together on the turn that
needed them most. `described.pending_lookup` was built for exactly this and only `router` read it.

Both conversations are also why no ask defers any more. Nothing happens between two turns of a chat
— no lookup runs, no owner reply comes back — so "لحظة أتأكدلك" was never a hedge, it was a promise
made by someone who could not keep it. It bought a turn and spent the customer's patience; these two
transcripts are the customer coming back to collect and getting a perfume they never asked about.
`products.services.absence` now searches the whole active catalogue before the first reply is
written, so the answer these customers waited for exists on turn 1 and is given there. See
`scenarios_conv816` for the shape that reaches the chase turn by re-typing the name rather than
chasing it with a pronoun.
"""

_BAKHOOR_TRUTH = (
    "Two perfumes genuinely answer a بخور (incense) request and both are stocked: Dior Homme "
    "Sport (Dior), base notes Woody Notes/Amber/Olibanum, 50ml 450 and 90ml 1100; and Bleu de "
    "Chanel (Chanel), base notes Incense/Cedar/Sandalwood/Patchouli, 50ml 645 and 90ml 1015. "
)

_CHASE_RULES = (
    "❌ CRITICAL: answering the chase turn as a question about the perfume that was volunteered "
    "alongside the promise. The customer is asking about the perfume THEY named, and a price list "
    "or note list for anything else is not an answer to being asked to check. "
    "❌ CRITICAL: claiming to have looked, searched or found anything ('لقيت...', 'بصيت ولقيت'). "
    "No lookup happened between the two turns. Reporting one is inventing a fact about the shop. "
    "❌ CRITICAL: promising to check ('لحظة أتأكدلك' / 'هسأل وأرد عليك' / 'هشوفه لك') on EITHER "
    "turn. Nothing looks a perfume up between two messages of a chat and no owner reply comes back, "
    "so that promise is what left these two customers waiting — the chase turn is them collecting "
    "on it. "
    "❌ CRITICAL: denying and promising to check in one reply ('مش موجود عندنا، لحظة أتأكدلك منه') "
    "— the two halves contradict each other. "
    "❌ CRITICAL: any mention of a system, data, catalogue, 'مش ظاهر عندي' or 'البيانات اللي معايا'. "
    "The customer must never learn such a thing exists. "
    "✅ Required on the FIRST turn: say plainly that we do not carry the perfume they named, with a "
    "short apology, echoing the name in their own Arabic letters — AND in the SAME reply offer one "
    "or two stocked perfumes by full name as DIFFERENT perfumes. The name was searched against the "
    "whole active catalogue before the reply was written, so the answer is already known. "
    "❌ CRITICAL: a bare denial with nothing on offer beside it — the customer came to buy. "
)

SCENARIOS = [
    {
        "id": "CONV798",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك لادور بخور ؟",
            "ها لقيت اي ؟",
            "بقول لادور بخور",
        ],
        "probe": (
            "Replay of conversation 798. لادور بخور (Dior Bakhour) is not in this catalogue. "
            + _BAKHOOR_TRUTH
            + "Turn 2 is the failure: 'ها لقيت اي ؟' means 'so, what did you find?' and was "
            "answered 'لقيت Dior Homme Sport متوفر عندنا' with 450 and 1100 attached. "
            + _CHASE_RULES
            + "❌ CRITICAL: attributing a بخور, incense or frankincense note to a perfume whose "
            "recorded notes do not contain one. Wanting the note is not evidence of it. "
            "✅ Expected: turn 1 says plainly that لادور بخور is not ours and offers Dior Homme "
            "Sport or Bleu de Chanel by full name as different perfumes, citing the olibanum/incense "
            "note each one really has. Turn 2 carries that same answer forward rather than reporting "
            "a search. Turn 1 asked about availability only, so a size and price list is "
            "unrequested there. ❌ Never quote a price that is not one of the four figures above."
        ),
    },
    {
        "id": "CONV799",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك الكساندريا 2؟",
            "اتأكدلي منه",
        ],
        "probe": (
            "Replay of conversation 799. الكساندريا 2 (Alexandria II) is not in this catalogue. "
            "Stronger With You (Emporio Armani) is 50ml 400 and 90ml 700; Stronger With You "
            "Intensely is 50ml 450 and 90ml 780; Stronger With You Absolutely is 50ml 480 and "
            "90ml 830. These are three DIFFERENT perfumes on one line, not sizes of one perfume. "
            "Turn 2 is the failure: 'اتأكدلي منه' — go on, check it for me — was answered "
            "'Stronger With You متوفر عندنا، والـ90 ملي بـ700 جنيه، والـ50 ملي بـ400 جنيه'. The "
            "'منه' points at الكساندريا 2, the only perfume the customer has named. "
            + _CHASE_RULES
            + "❌ CRITICAL: implying الكساندريا 2 and Stronger With You are the same perfume, or "
            "that one is the other's 'correct name'. "
            "✅ Expected: turn 1 says plainly that الكساندريا 2 is not ours and offers a stocked "
            "perfume by full name as a different perfume. Turn 2 carries that same answer forward "
            "instead of pricing something else. Turn 1 asked about availability only, so a size "
            "and price list is unrequested there. ❌ Never quote a price for any perfume that is "
            "not one of the six figures above."
        ),
    },
]
