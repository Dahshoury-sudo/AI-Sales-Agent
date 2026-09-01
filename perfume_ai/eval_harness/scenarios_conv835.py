# -*- coding: utf-8 -*-
"""Replay of conversations 835 and 836: the deferral that was never recorded, and the one never kept.

Both customers named a perfume this catalogue does not carry, and in both the retrieval miss was
correct. Everything that went wrong went wrong afterwards.

  835  "عندك لادور بخور ؟"        → "لحظة أتأكدلك منه" + Dior Homme Sport and Bleu de Chanel ✅
       "ها لقيتو ؟"                → Dior Homme Sport's price list ❌
       "بقول لقيت لادور بخور؟"     → the denial, with ONE alternative ✅ / ❌
       "طب عاملين كام دو"          → one perfume priced, for a plural question ❌
       "وبلو دي شانيل ؟"           → "متوفر عندنا، تحب تعرف عن حجم معين؟" ❌
       "بكام يعم"                  → the price, on the sixth turn ❌

  836  "عايز اعرف اسعار بلو دي شانيل وسوفاج والكساندريا 2"
                                   → two priced, third deferred in prose only ❌
       "ها لقيت اي"                → the same two price lists again ❌
       "ماشي اعرفلي"               → the same two price lists a third time, الكساندريا 2 no longer
                                     mentioned at all ❌

836 is the worse of the two and the reason this file exists. No `PENDING_LOOKUP` marker was ever
written — `named_but_unresolved` was `resolver_ran and not products`, and two of the three names
resolved — so `pending_questions` stayed empty, the owner got **zero** notifications, `needs_human`
was never set, and the customer asked three times for a price nobody had been told to look up.
`router._count_repeated_customer_questions` was the last net and it missed too: the three messages
are genuinely different sentences and scored 0.07, 0.30 and 0.48 against a 0.70 threshold.

835 turn 2 is the other half. "لقيتو" was not one of `_CHASING`'s eight literal forms, over a dialect
that suffixes freely, and an unlisted inflection fails twice: the chase is missed, and the verb then
survives `identifying_tokens`, so the resolver is asked to place it and answers with whatever was
offered last. That is how "did you find it?" got Dior Homme Sport's prices.

Turns 4 and 5 are what the mishandled denial cost. Turn 3's context carried one product row, so the
denial had one alternative to offer and turn 4's plural "دو" ("how much are *those*") had one perfume
to price; turn 5's bare name then lost the price intent it inherited, and turn 6 exists only because
turn 5 did not answer.

`scenarios_conv816` is the neighbouring case — insisting by re-typing the name — and `conv798` the
pronoun chase against a recorded deferral. This file is the pair where the record itself was wrong or
missing.
"""

_BAKHOOR_TRUTH = (
    "لادور بخور is not in this catalogue and no amount of searching will find it. Two stocked "
    "perfumes genuinely answer a بخور (incense) request: Dior Homme Sport (Dior), base notes Woody "
    "Notes/Amber/Olibanum, 50ml 450 and 90ml 1100, plus original bottles 100ml 4500 and 200ml 8000; "
    "and Bleu de Chanel (Chanel), base notes Incense/Cedar/Sandalwood/Patchouli, 50ml 645 and 90ml "
    "1015, plus original bottles 50ml 800, 100ml 1400 and 150ml 1700. "
)

_ALEXANDRIA_TRUTH = (
    "الكساندريا 2 (Alexandria II, Xerjoff) is not in this catalogue. The other two perfumes the "
    "customer names on turn 1 ARE stocked and their prices are known: Bleu de Chanel (Chanel) 50ml "
    "645 and 90ml 1015, plus original bottles 50ml 800, 100ml 1400 and 150ml 1700; Dior Sauvage "
    "(Dior) 50ml 642 and 90ml 944, plus original bottles 100ml 815 (2 left) and 200ml 1623 (the "
    "60ml original is out of stock). "
)

# The reply owed on the turn a promise is collected. Overlaps deliberately with
# `scenarios_conv816._RE_ASK_RULES` and `scenarios_conv798._CHASE_RULES`: the required reply is the
# same one, and a judge scoring this file should not have to know which route into the turn was taken.
_CHASE_RULES = (
    "❌ CRITICAL: answering a chase with the price list, sizes, notes or availability of the perfume "
    "that was volunteered alongside the promise. 'ها لقيتو ؟' and 'ها لقيت اي' ask about the "
    "customer's OWN perfume; detail about a different one is not an answer to it, and reading it as "
    "one is the exact defect in 835 turn 2 and 836 turns 2 and 3. "
    "❌ CRITICAL: promising to check again ('لحظة أتأكدلك' / 'هسأل وأرد عليك' / 'لو حابب أعرفلك... "
    "هسأل وأرد عليك' / 'هشوفه لك'). That promise was already made and nothing has happened since — "
    "no lookup runs between two messages of a chat — so making it a second time leaves the customer "
    "waiting for an answer that is not coming. 836 turn 2 is that reply verbatim. "
    "❌ CRITICAL: claiming to have looked, searched, checked or found anything ('لقيت', 'بصيت "
    "ولقيت', 'راجعت', 'دورت'). No lookup happened between the turns; reporting one invents a fact. "
    "❌ CRITICAL: dropping the perfume from the reply. Answering only the perfumes that were found "
    "and saying nothing at all about the one that was not is how 836 turn 3 ended a conversation the "
    "customer had asked the same question in three times. "
    "❌ CRITICAL: denying and promising to check in one reply ('مش موجود عندنا، لحظة أتأكدلك منه') — "
    "the two halves contradict each other. "
    "❌ CRITICAL: any mention of a system, data, catalogue, 'مش ظاهر عندي' or 'البيانات اللي معايا'. "
    "The customer must never learn such a thing exists. "
    "❌ CRITICAL: writing the perfume's name in Latin letters when the customer wrote it in Arabic "
    "('L'Adour', 'Alexandria II'). Inventing a spelling for a perfume we are saying we do not know "
    "is incoherent — echo the customer's own letters. "
    "✅ Required on the turn the customer comes back: a short apology and a plain statement that the "
    "perfume they named is not available, then one or two stocked perfumes offered by FULL name and "
    "clearly labelled as DIFFERENT perfumes. "
)

SCENARIOS = [
    {
        "id": "CONV835",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك لادور بخور ؟",
            "ها لقيتو ؟",
            "بقول لقيت لادور بخور؟",
            "طب عاملين كام دو",
            "وبلو دي شانيل ؟",
            "بكام يعم",
        ],
        "probe": (
            "Replay of conversation 835. "
            + _BAKHOOR_TRUTH
            + "Turn 1 was right: 'لحظة أتأكدلك منه' with two alternatives, and no price list for a "
            "question that only asked about availability. "
            "Turn 2 is the first failure: 'ها لقيتو ؟' means 'so, did you find it?' — the customer "
            "collecting the promise, naming no perfume of their own — and the entire reply was Dior "
            "Homme Sport's availability and price list. "
            + _CHASE_RULES
            + "Turn 3 ('بقول لقيت لادور بخور؟' — 'I said, did you find Ladore Bakhour?') is the same "
            "question a third time. By then the customer has already been told once; the reply must "
            "hold the same answer without reading the whole denial back as though they had not heard "
            "it, and must move to what is actually on offer. ❌ It must never revert to 'لحظة "
            "أتأكدلك' after a denial, and ❌ never say the perfume is available. "
            "Turn 4 ('طب عاملين كام دو' — 'ok, how much are THOSE') is plural and refers to the "
            "perfumes just offered. ✅ It must price EVERY perfume that was actually offered by "
            "name, not one of them. ❌ Pricing a single perfume when two were offered leaves the "
            "customer to ask again, which is what happened. ❌ It must not read 'دو' as a new "
            "perfume name. "
            "Turn 5 ('وبلو دي شانيل ؟' — 'and Bleu de Chanel?') continues turn 4's question about a "
            "new perfume. ✅ It must answer with the PRICE. ❌ Replying only 'متوفر عندنا، تحب تعرف "
            "عن حجم معين؟' — confirming availability and asking the customer to ask again for the "
            "thing they just asked for — is the failure here; it is what forced turn 6 to exist. "
            "Turn 6 ('بكام يعم' — 'how much, man?') is the customer asking a third time, with "
            "audible impatience. It must be answered with Bleu de Chanel's prices. ✅ A run where "
            "turn 5 already gave the price and turn 6 reads as redundant is the better outcome, not "
            "a worse one. "
            "❌ CRITICAL: attributing a بخور, incense or frankincense note to a perfume whose "
            "recorded notes do not contain one. Wanting the note is not evidence of it. Dior Homme "
            "Sport has olibanum and Bleu de Chanel has incense — cite those, and no others. "
            "❌ Never quote a price that is not one of the ten figures above."
        ),
    },
    {
        "id": "CONV836",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عايز اعرف اسعار بلو دي شانيل وسوفاج والكساندريا 2",
            "ها لقيت اي",
            "ماشي اعرفلي",
        ],
        "probe": (
            "Replay of conversation 836. "
            + _ALEXANDRIA_TRUTH
            + "Turn 1 names three perfumes and asks for their prices. ✅ Both stocked perfumes must "
            "be priced in full — the customer asked for those prices and is owed them — AND "
            "الكساندريا 2 must be named in the same reply with 'لحظة أتأكدلك منه' and nothing more. "
            "❌ CRITICAL: answering two of the three and leaving the third out of the reply "
            "entirely. ❌ CRITICAL: withholding the two prices because of the third name — the rows "
            "for Bleu de Chanel and Dior Sauvage ARE the answer to two thirds of this question. "
            "❌ CRITICAL: saying الكساندريا 2 is not available on this turn: failing to find a name "
            "is not the same fact as not stocking it, so turn 1 defers and does not deny. "
            "❌ CRITICAL: implying الكساندريا 2 is one of the two priced perfumes, or that one of "
            "them is its 'correct name', or attributing any price or note to it. "
            "Turn 2 ('ها لقيت اي' — 'so what did you find?') collects that promise. Turn 3 ('ماشي "
            "اعرفلي' — 'fine, go find out for me') collects it again. "
            + _CHASE_RULES
            + "What actually happened on both turns: the same two price lists re-sent verbatim, "
            "with 'لو حابب أعرفلك أسعار الكساندريا 2، هسأل وأرد عليك' on turn 2 and no mention of "
            "الكساندريا 2 at all on turn 3. ❌ Re-sending prices the customer was already given, on "
            "a turn that asked a different question, is a critical failure however accurate those "
            "prices are. "
            "❌ Never quote a price that is not one of the figures above, and never quote the "
            "out-of-stock 60ml original as available."
        ),
    },
]
