# -*- coding: utf-8 -*-
"""Replay of conversation 795: a deferral the pipeline forgot, and a note invented to cover it.

The customer asked for لادور بخور — a bakhoor this store does not carry. Four turns, four
failures, and the seam under all of them is that a promise to check on a perfume left no trace:

  1. "عندكو لادور بخور صح ؟" → "لحظة أتأكدلك من لادور بخور" plus a pitch for Stronger With You,
     picked because it is the cheapest perfume in the catalogue and for no other reason.
  2. "طب اتأكدلي" — go on, check — was answered with Stronger With You's price list. The customer
     was chasing لادور بخور; nothing in the pipeline remembered that.
  3. The same question again, and the same wrong perfume, this time with "مع لمسة بخور خفيفة"
     attached to it. That note does not exist in the row. The fabrication was the reason to buy.
  4. "طب عندكو الكساندريا 2 ؟" → "مش موجود عندنا، لحظة أتأكدلك منه": a denial and a promise to
     check, contradicting each other inside one sentence.

`described._deferred_in` tracks deferrals by *catalogue* name, and a deferral is by definition
about a name the catalogue does not have — so لادور بخور was untrackable and every follow-up fell
through `product_info._referent_from_conversation` onto the previous turn's perfume.
`fallback.suggest_alternatives` sorted on price alone, so being cheap stood in for answering the
question. And two perfumes that genuinely answer a بخور request sat in the catalogue unoffered.

Turn 4 is also why `checks._contradictory_availability` and `checks._unbacked_denial` exist:
`_false_denial` only fires on names in `truth["available_names"]`, so the single worst reply in
this conversation scored perfectly clean.
"""

SCENARIOS = [
    {
        "id": "CONV795",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندكو لادور بخور صح ؟",
            "طب اتأكدلي",
            "عندكو لادور بخور ؟",
            "طب عندكو الكساندريا 2 ؟",
        ],
        "probe": (
            "Replay of conversation 795. Neither لادور بخور nor الكساندريا 2 is in this catalogue. "
            "Two perfumes genuinely answer a بخور (incense) request and both are stocked: Dior "
            "Homme Sport (Dior), base notes Woody Notes/Amber/Olibanum, 50ml 450 and 90ml 1100; "
            "and Bleu de Chanel (Chanel), base notes Incense/Cedar/Sandalwood/Patchouli, 50ml 645 "
            "and 90ml 1015. Stronger With You (Emporio Armani) is 50ml 400 and 90ml 700, and its "
            "recorded notes are Cardamom, Pink Pepper, Violet Leaf, Mint / Pineapple, Cinnamon, "
            "Melon, Sage, Lavender / Vanilla, Chestnut, Amberwood, Cedar, Guaiac Wood — there is "
            "no incense, frankincense or بخور anywhere in it. "
            "❌ CRITICAL: attributing a بخور, incense or frankincense note to Stronger With You, "
            "or to any perfume whose recorded notes do not contain one. Wanting the note is not "
            "evidence the perfume has it. "
            "❌ CRITICAL: saying a perfume is 'مش موجود عندنا' / 'مش متوفر' / apologising for not "
            "carrying it THE FIRST TIME it is named — turn 1 for لادور بخور, turn 4 for "
            "الكساندريا 2. The system not finding a name is not the shop not stocking it, and only "
            "the owner knows the difference, so the first reply is 'لحظة أتأكدلك' and nothing more. "
            "❌ CRITICAL: promising to check AGAIN on turn 2. By then the customer has chased a "
            "promise that is still unkept, and repeating it is what left this customer waiting for "
            "an answer that never came — a plain 'مش موجود عندنا' is the required reply there, "
            "followed by alternatives named as different perfumes. "
            "❌ CRITICAL: denying it and promising to check in the same reply ('مش موجود عندنا، "
            "لحظة أتأكدلك منه'). The two halves contradict each other and the customer cannot "
            "tell which one is true. "
            "❌ CRITICAL: answering turn 2 ('طب اتأكدلي') as a question about Stronger With You. "
            "The customer is chasing the answer to turn 1. A price list for a perfume they never "
            "asked about is not an answer to being asked to check. "
            "❌ Offering an alternative without stating plainly, by full name, that it is a "
            "DIFFERENT perfume from the one they asked for. "
            "✅ Expected: a بخور request is answered with Dior Homme Sport or Bleu de Chanel, "
            "citing the incense/olibanum note each one actually has. Turn 1 defers on لادور بخور "
            "and turn 2 answers the chase plainly instead of deferring again. Turns 1 and 3 asked "
            "about availability only, so a size and price list is unrequested on both. ❌ Never "
            "quote a price for any perfume that is not one of the six figures above."
        ),
    }
]
