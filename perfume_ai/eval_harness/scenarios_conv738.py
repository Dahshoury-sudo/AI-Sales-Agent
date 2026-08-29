# -*- coding: utf-8 -*-
"""Replay of conversation 738: an Arabic-written perfume name answered with a different perfume.

The customer was shopping for a fresh gym scent on a 900 budget and had just been recommended
Y Eau de Parfum. They asked "طب اكوا دي جيو ؟" — Acqua di Gio, which this store stocks — and were
answered with Y's data. They repeated themselves, "بقول اكوا دي جيو", and were told
"Acqua Di Gio اسمه الصحيح Y Eau de Parfum": that Acqua di Gio's correct name IS Y Eau de Parfum.
Two real perfumes, two different brands, both in the catalogue, declared the same product.

The seam: `product_info` decided "the customer named no perfume" from `_named_in_message` coming
back empty, and that matcher needs the catalogue's Latin tokens in the message — an Arabic-script
name matches it never. So the referent branch claimed the turn, handed the model the *previous*
turn's recommendation, and `resolve_products` — the only component that reads Arabic — never ran.
`scenarios_conv726.py` hit the same seam from the other side: "which then suppressed the only
resolver that can read Arabic."

The false identity claim is a second, independent defect: instruction 1 told the model to use the
injected name "even if the customer wrote it wrong", with no guard for the injected perfume being
the wrong one, so a mis-resolution was laundered into an assertion about the world.
"""

SCENARIOS = [
    {
        "id": "CONV738",
        "category": "regression",
        "persona": "hard_budget",
        "turns": [
            "عايز حاجه حلوه كدا للجيم وفريش",
            "900",
            "لراجل",
            "اي تاني يكون 90 ملي",
            "اي تاني",
            "طب اكوا دي جيو ؟",
            "بقول اكوا دي جيو",
        ],
        "probe": (
            "Replay of conversation 738. Turns 6 and 7 name Acqua di Gio (Giorgio Armani), which "
            "IS in the catalogue: 50ml at 503 and 90ml at 1032. Y Eau de Parfum (Yves Saint "
            "Laurent) is a DIFFERENT perfume that happened to be turn 5's recommendation. "
            "❌ CRITICAL: answering turn 6 or 7 with Y's data, name or prices instead of Acqua di "
            "Gio's means the subject came from the previous turn's offer rather than from what the "
            "customer actually asked about. ❌ CRITICAL trust failure: claiming Acqua di Gio's "
            "'correct name' is Y Eau de Parfum, or that the two are the same perfume, the same "
            "scent or the same composition — they are different perfumes from different brands. "
            "❌ Deferring on Acqua di Gio ('هسأل وأرد عليك', 'لحظة أتأكدلك') or implying it is "
            "unavailable is the conversation-1099 false denial: it is stocked. Turn 7 is the "
            "customer CORRECTING turn 6's answer, so repeating turn 6 back at them is its own "
            "failure. The stated budget is 900, so Acqua di Gio's 90ml at 1032 is above it — lead "
            "with the 50ml at 503 and say plainly that the 90ml is over budget. ❌ Never quote a "
            "price for either perfume that is not one of those four figures."
        ),
    }
]
