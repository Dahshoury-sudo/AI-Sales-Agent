# -*- coding: utf-8 -*-
"""Replay of conversation 772: a perfume we stock, denied, and then a silence.

Three messages, and by the third the bot had stopped answering at all:

  772  "موجود عطر جنتل مان ؟"   → "لحظه اتأكدلك منه يا فندم" plus a pitch for Stronger With You.
                                  Correct on the facts — there is no Gentleman in this catalogue —
                                  under the policy of the time.
       "طب ڤيرزاتشي ايروس"      → the same promise again. Versace Eros IS stocked, id 23, at 666
                                  and 1019. ❌
       "ها ؟"                    → nothing at all. `needs_human` had been set on turn 2, and
                                  `views.py` answers a handed-off conversation with silence.

Two independent defects, one visible outcome.

The false miss is `resolve_products`, and it had two contributors.

The catalogue list handed to the extractor carried names without brands, so "Versace Eros" — listed
under the bare name "Eros" — had to be matched against two customer words with nothing in the prompt
saying they belong together. That is fixed: the list now prints `- Eros  [brand: Versace]`, and rule
1 tells the model to match the house against the annotation. It is the half of this that Python can
be sure of.

The other contributor is still there. `offered_context_block` — "PERFUMES YOU JUST OFFERED: 1.
Stronger With You" — is spliced into the *extraction* prompt on every turn, including one that names
a perfume of its own, and a numbered list under a heading saying these are the subject reads as the
answer whatever the prose around it says. Measured on the real prompt with turn 1's real history, six
runs per cell: 0/6 with the block, 6/6 without it.

🔴 It is not gated, and not for want of trying. The gate would have to separate "this message names
its own perfume" from "this message points at one we offered", and no predicate in `naming` does
that: `identifying_tokens` is non-empty for "قول سعرهم" and "اول واحد" — the two messages the anchor
exists for, rule 6 and evaluation scenario F1 — and `may_name_a_perfume` is True for those and for
"ڤيرزاتشي ايروس" alike. Gating on either one trades this defect for that one. Conversation 1099 is
why the block cannot simply go: "مش متوفر متأكد ؟", a doubt utterance naming nothing, resolved to two
perfumes from two turns earlier because nothing pointed at the newest.

ڤ (U+06A4) sits underneath both. `normalize_arabic` does not fold it onto ف and nothing in this
pipeline does, so the two spellings of the brand are different strings to every matcher, and
`_unplaced_names` drops the span whenever the model echoes the other one.

🔴 What turn 2 now costs if the extractor still misses: `absence.catalogue_verdict` takes the
resolver's unplaced report as its witness for an Arabic name, so a miss no longer produces a harmless
promise — it produces a denial, by name, of a perfume active at 666 and 1019. That makes this
scenario the regression floor for the whole deny-on-the-first-ask change, and the reason to run it
twice: a single clean run is not evidence when the extractor is an LLM.

The silence is `router._escalate_absent_name`. It handed the conversation over on the second
unplaceable perfume, whoever that perfume was. Two different perfumes is not a customer being
stonewalled — it is two ordinary questions — and the handoff now compares this turn's question
against the ones already denied.

Note what this file cannot see. The harness calls `route` directly, so `needs_human` is set but
never gates anything, and turn 3 comes back with content either way. The silence itself is asserted
in `products.tests.TwoAbsentPerfumesKeepTheBotServingTests`, where the flag can be read straight off
the conversation. What the probe below holds turn 3 to is the answer it should be carrying.
"""

_EROS = (
    "Versace Eros (brand Versace, listed in the catalogue under the bare name 'Eros') IS stocked "
    "and active: 50ml 666 and 90ml 1019. It is an EDT, male, longevity 7 hours, projection Strong. "
    "Top notes Mint, Green Apple, Lemon / middle Tonka Bean, Ambroxan, Geranium / base Vanilla, "
    "Vetiver, Oakmoss. It has no original-bottle variant, so the fixed sentence 'للأسف مش متوفر منه "
    "زجاجة أوريجينال حالياً' is CORRECT if it appears and is not a denial of the perfume. "
)

_STRONGER = (
    "Stronger With You (Emporio Armani) is 50ml 400 and 90ml 700, plus original bottles 100ml 3800 "
    "and 200ml 6800. "
)

_GENTLEMAN = (
    "جنتل مان (Givenchy Gentleman) is NOT in this 35-product catalogue under any spelling. "
)

SCENARIOS = [
    {
        "id": "CONV772",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "موجود عطر جنتل مان ؟",
            "طب ڤيرزاتشي ايروس",
            "ها ؟",
        ],
        "probe": (
            "Replay of conversation 772. "
            + _GENTLEMAN
            + _EROS
            + _STRONGER
            + "Turn 1 asks whether we have جنتل مان, and we do not. ✅ Required: say so plainly, in "
            "one sentence, with a short apology, echoing the name in the customer's own Arabic "
            "letters — AND in the SAME reply offer one or two stocked perfumes by FULL name, "
            "clearly labelled as DIFFERENT perfumes. "
            "❌ CRITICAL: promising to check ('لحظة أتأكدلك منه' / 'هسأل وأرد عليك' / 'هشوفه لك'). "
            "Nothing looks a perfume up between two messages of a chat, so the promise leaves the "
            "customer waiting for an answer that never comes. That promise is what this transcript "
            "opens with. "
            "❌ CRITICAL: a bare denial with nothing on offer beside it — the customer came to buy. "
            "❌ CRITICAL: writing جنتل مان in Latin letters, or claiming a stocked perfume is its "
            "'correct name' or the same perfume. "
            "❌ Any mention of a system, data, catalogue or 'مش ظاهر عندي'. "
            "Turn 2 ('طب ڤيرزاتشي ايروس' — 'ok then, Versace Eros') is the turn this conversation "
            "exists for. It switches to a DIFFERENT perfume, one we stock, written with ڤ instead "
            "of ف. ✅ Required: the reply is about Versace Eros and treats it as a perfume we carry "
            "— confirming it and asking which size, or quoting its prices. "
            "❌ CRITICAL: saying Eros is unavailable, not found, or not in stock. It is active at "
            "666 and 1019, and telling a customer otherwise is the worst reply on this turn. "
            "❌ CRITICAL: promising to check on Eros. "
            "❌ CRITICAL: answering about Stronger With You, or any perfume offered on turn 1, "
            "instead of Eros. The customer changed the subject and 'طب' is how they said so. "
            "❌ CRITICAL: still answering about جنتل مان, or re-denying it, as though turn 2 had "
            "not named anything. "
            "❌ Reading ڤيرزاتشي as a perfume in its own right, or as a perfume we do not carry. "
            "Turn 3 ('ها ؟' — 'well?') is the customer waiting on turn 2. ✅ Required: a real reply "
            "that carries the Eros answer forward — its prices, or its sizes, or a question that "
            "moves the sale on. "
            "❌ CRITICAL: an empty reply, or a reply that says nothing about Eros. This turn got "
            "absolute silence in production and the whole conversation ended there. "
            "❌ CRITICAL: claiming to have looked, searched or found anything ('لقيت', 'راجعت'). "
            "❌ CRITICAL: reverting to جنتل مان, or announcing that a human will follow up. "
            "❌ Never quote a price for Eros that is not 666 or 1019, and never quote a price for "
            "any perfume that is not among the figures above."
        ),
    },
]
