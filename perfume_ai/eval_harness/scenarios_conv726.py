# -*- coding: utf-8 -*-
"""Replay of conversation 726: a live order stalled by a false "not in the data".

The customer had one perfume in the cart and asked to add two more, naming them in Arabic
transliteration ("سترينجر وذ يو انتنسلي"). The order flow resolved both against the catalogue
and asked which bottle type — proof they were active with both bottle types in stock. One
minute later, asked for their prices, the bot answered with the cart perfume's data only and
said the other two were "مش موجودين في البيانات اللي معايا دلوقتي" — four turns running. The
customer asked for the Instagram account and left.

The seam: an order turn writes a *cart* into `Message.internal_context`, and it omits any
perfume it is still waiting on a bottle type for (order_service.py:621-624).
`described.under_discussion` read that omission as "no data behind it" and subtracted both
perfumes as withdrawn, leaving the stale cart line as the only thing under discussion — which
then suppressed the only resolver that can read Arabic.
"""

SCENARIOS = [
    {
        "id": "CONV726",
        "category": "regression",
        "persona": "ready_buyer",
        "turns": [
            "9pm night out متاح",
            "عايزه النوع 9pm night out القزاز السوداء",
            "عايز واحده 50 ميلي",
            "وعايز معا الأوردة سترينجر وذ يو انتنسلي",
            "كل واحده كام سعرها",
            "انااا بتكلم دلوقتي سعر سترينجر وذ يو انتنسلي عامل كام",
            "متاح ولا لسه",
            "انااا عله سترينجر وذ يو انتنسلي",
        ],
        "probe": (
            "Replay of conversation 726. Afnan 9PM, Stronger With You and Stronger With You "
            "Intensely are ALL in the catalogue, active, with sellable bottles — turn 4's own "
            "reply proves it, since the order flow only asks for a bottle type after resolving "
            "a perfume and clearing every stock check. Turn 5 asks the price of EACH of the two "
            "just added, so both must be quoted from real data; answering about Afnan 9PM alone "
            "means the referent came from the stale cart instead of the reply the customer is "
            "answering. Turns 6 and 8 name Stronger With You Intensely outright, in Arabic — "
            "❌ deferring on it ('هسأل وأرد عليك', 'لحظة أتأكدلك') or implying it is unavailable "
            "is a critical trust failure that killed a live order. ❌ Saying anything about "
            "'البيانات' out loud is a separate failure: that is internal plumbing, and the "
            "customer must never hear it. Turn 7 ('متاح ولا لسه') is still about the two "
            "Stronger perfumes, not about Afnan 9PM."
        ),
    }
]
