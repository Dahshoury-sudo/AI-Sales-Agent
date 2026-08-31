# -*- coding: utf-8 -*-
"""Replay of conversations 816 and 817: insisting by re-typing the name, and getting the stall back.

`scenarios_conv798` covers the customer who comes back with a pronoun — "اتأكدلي منه", "ها لقيت
اي ؟". These two are the other way back to the same question, and the more natural one: the
customer simply types the name again.

  816  "عندك الكساندريا 2؟"        → "لحظة أتأكدلك منه" + a pitch for Stronger With You
       "ماشي شوفو"                  → answered about Stronger With You
       "بتكلم علي الكساندريا 2؟"    → "لحظة أتأكدلك منه." — the stall, repeated
       "اتأكد"                      → nothing at all

  817  "عندك لادور بخور ؟"         → "لحظة أتأكدلك منه" + Dior Homme Sport and Bleu de Chanel
       "بسأل علي لادور بخور"        → "لحظة أتأكدلك منه." — the stall, repeated
       "ماشي"                       → nothing at all

815 is the control and it already worked: there the customer chased with "اتأكدلي منو", which is
the pronoun shape, and the reply was the right one — "بعتذر يا فندم، الكساندريا 2 مش موجود عندنا"
with Stronger With You offered by name and priced.

The difference was in `product_info`: re-typing an unplaceable name sets `named_but_unresolved`,
which vetoes the chase carry, so the turn read as a first ask and was deferred afresh — while
`router` simultaneously counted it as the second deferral and set `needs_human`. So the stall was
the last thing the bot ever said, and the denial and alternatives the customer was owed never came.
`naming.re_asks` compares the customer's own words to tell a repeat ask from a genuinely new name,
which is what lets the veto stand and the re-ask be recognised beside it.

816 also needs the open question to outlive a reply that never mentions it: turn 2 was answered
about Stronger With You and recorded no pending marker at all.
"""

_ALEXANDRIA_TRUTH = (
    "الكساندريا 2 (Alexandria II, Xerjoff) is not in this catalogue. Stronger With You (Emporio "
    "Armani) is 50ml 400 and 90ml 700; Stronger With You Intensely is 50ml 450 and 90ml 780; "
    "Stronger With You Absolutely is 50ml 480 and 90ml 830. Those are three DIFFERENT perfumes on "
    "one line, not three sizes of one perfume. "
)

_BAKHOOR_TRUTH = (
    "لادور بخور is not in this catalogue. Two stocked perfumes genuinely answer a بخور (incense) "
    "request: Dior Homme Sport (Dior), base notes Woody Notes/Amber/Olibanum, 50ml 450 and 90ml "
    "1100; and Bleu de Chanel (Chanel), base notes Incense/Cedar/Sandalwood/Patchouli, 50ml 645 "
    "and 90ml 1015. "
)

# The failure these two transcripts share, stated once. Deliberately overlapping with
# `scenarios_conv798._CHASE_RULES` on the substance — the required reply is the same — because the
# route into the turn is what differs and a judge scoring this file should not have to know that.
_RE_ASK_RULES = (
    "❌ CRITICAL: deferring again on the turn where the customer re-types the name "
    "('لحظة أتأكدلك' / 'هسأل وأرد عليك' / 'هشوفه لك' / 'ثانية واحدة'). That promise was already "
    "made on the previous turn and nothing has happened since — no lookup runs between two "
    "messages of a chat — so repeating it leaves the customer waiting for an answer that is not "
    "coming. This is the exact defect in 816 and 817. "
    "❌ CRITICAL: claiming to have looked, searched, checked or found anything ('لقيت', 'بصيت "
    "ولقيت', 'راجعت'). No lookup happened between the turns; reporting one invents a fact. "
    "❌ CRITICAL: answering the re-ask with the price list, notes or availability of the perfume "
    "that was volunteered alongside the promise. The customer is naming THEIR perfume again, and "
    "detail about a different one is not an answer. "
    "❌ CRITICAL: implying the perfume asked for and the stocked perfume are the same thing, or "
    "that one is the other's 'correct name' or 'correct spelling'. "
    "❌ CRITICAL: denying and promising to check in one reply ('مش موجود عندنا، لحظة أتأكدلك منه') "
    "— the two halves contradict each other. "
    "❌ CRITICAL: any mention of a system, data, catalogue, 'مش ظاهر عندي' or 'البيانات اللي "
    "معايا'. The customer must never learn such a thing exists. "
    "❌ CRITICAL: writing the perfume's name in Latin letters when the customer wrote it in Arabic "
    "('L'Adour', 'Alexandria'). Inventing a spelling for a perfume we are saying we do not know is "
    "incoherent — echo the customer's own letters. "
    "❌ On the FIRST turn, denying it instead of deferring: not finding a name is not the same "
    "fact as not stocking it, so the first reply is 'لحظة أتأكدلك منه' and nothing more. "
    "✅ Required on the re-ask turn: a short apology and a plain statement that the perfume the "
    "customer named is not available, followed by one or two stocked perfumes offered by FULL name "
    "and clearly labelled as DIFFERENT perfumes. 815 is the model answer: "
    "'بعتذر يا فندم، الكساندريا 2 مش موجود عندنا' and then Stronger With You with its prices. "
)

SCENARIOS = [
    {
        "id": "CONV816",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك الكساندريا 2؟",
            "ماشي شوفو",
            "بتكلم علي الكساندريا 2؟",
            "اتأكد",
        ],
        "probe": (
            "Replay of conversation 816. "
            + _ALEXANDRIA_TRUTH
            + "Turn 3 is the failure: 'بتكلم علي الكساندريا 2؟' means 'I'm talking about "
            "Alexandria 2' — the customer correcting the subject back after turn 2 was answered "
            "about Stronger With You — and it got 'لحظة أتأكدلك منه' for the second time. Turn 4 "
            "('اتأكد' — go check) then got no reply at all. "
            + _RE_ASK_RULES
            + "Note turn 2 ('ماشي شوفو') is ambiguous and being answered about Stronger With You "
            "there is acceptable; it is turn 3 that must not stall. Turn 4 must still be served "
            "and must not repeat the denial as though the customer had not just heard it — by then "
            "the perfume has been answered and the reply should move to what is actually on offer. "
            "Turn 1 asked about availability only, so a size and price list is unrequested there. "
            "❌ Never quote a price that is not one of the six figures above."
        ),
    },
    {
        "id": "CONV817",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك لادور بخور ؟",
            "بسأل علي لادور بخور",
            "ماشي",
        ],
        "probe": (
            "Replay of conversation 817. "
            + _BAKHOOR_TRUTH
            + "Turn 2 is the failure: 'بسأل علي لادور بخور' means 'I'm asking about Ladore "
            "Bakhour' — the customer naming it again, immediately after the deferral — and the "
            "entire reply was 'لحظة أتأكدلك منه يا فندم.' Turn 3 ('ماشي') then got nothing. "
            + _RE_ASK_RULES
            + "❌ CRITICAL: attributing a بخور, incense or frankincense note to a perfume whose "
            "recorded notes do not contain one. Wanting the note is not evidence of it — Stronger "
            "With You was pitched as having 'لمسة بخور' and its notes are cardamom, pineapple, "
            "cinnamon, vanilla, chestnut and amberwood. When offering Dior Homme Sport or Bleu de "
            "Chanel, cite the olibanum/incense note each one really has. "
            "Turn 1 asked about availability only, so a size and price list is unrequested there. "
            "❌ Never quote a price that is not one of the four figures above."
        ),
    },
]
