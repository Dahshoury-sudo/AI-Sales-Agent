# -*- coding: utf-8 -*-
"""Replay of conversations 841 and 842: two perfumes asked about, one priced.

Both customers asked for two prices in one message and were given one price. From the outside the
two failures are indistinguishable; underneath they share nothing.

  842  "عندك سوفاج ؟"          → "Dior Sauvage متوفر عندنا، تحب تعرف سعر حجم معين؟" ✅
       "طب بلو دي شانيل ؟"     → "Bleu de Chanel متوفر عندنا، تحب تعرف سعر حجم معين؟" ✅
       "بكام الاتنين"          → Bleu de Chanel's prices only ❌

  841  "عندك لادور بخور ؟"     → "لحظة أتأكدلك منه" + Dior Homme Sport and Bleu de Chanel ✅
       "بكام لااتنين ؟"        → Dior Homme Sport's prices only ❌ (and the promise vanished ❌)

842 is a **retrieval** failure. The two perfumes were introduced one per reply, and the referent
took `latest_only=True` — every perfume the *newest* reply named — so Dior Sauvage was never in the
prompt at all. The model answered the single row it was given, correctly and completely. The guard
was written for conversation 726, where two perfumes arrived in ONE reply and the narrowing cost
nothing; a referent spanning two replies is the shape it could not express.

841 is a **generation** failure, and the more troubling of the two. Both perfumes came from one
reply, so the window held both, and turn 4's saved context carries both product blocks with full
prices. Nothing in the found branch asked the reply to cover them. The only instruction that does —
"✅ جاوب على العطور اللي في البيانات عادي" — is gated on a *partially* resolved turn, so 836 got it
by accident of having a third name it could not place, and 841, where everything resolved cleanly,
got nothing. Two rules pushed the other way besides: the price rule is written for one perfume's
size ladder (on 841 the model led with the perfume that had no value-pick line at all), and rule 7
forbids volunteering what the customer did not ask for, which is what a second perfume looks like
until something says otherwise.

Two details make the plural question harder than it looks. "بكام الاتنين" literally asks *how much
are the two* — the surface form of a question the total-ban rule exists to refuse — so the reply has
to quote two prices side by side while never summing them. And 841's "لااتنين" is "الاتنين" with the
definite article mistyped; read as a name it becomes a perfume we would go on to deny by name.

`scenarios_conv835` is the neighbouring file: 835 turn 4's "طب عاملين كام دو" is the same plural
question reached through a mishandled denial, and 841 turn 1 is 835 turn 1 verbatim.
"""

# Both conversations draw on the same three rows, so the figures live in one place. Every price
# below is from the products' own variants as the two transcripts recorded them.
_SPORT = (
    "Dior Homme Sport (Dior): 50ml 450 and 90ml 1100, plus original bottles 100ml 4500 and "
    "200ml 8000 (2 left). Base notes Woody Notes/Amber/Olibanum. "
)
_BLEU = (
    "Bleu de Chanel (Chanel): 50ml 645 and 90ml 1015, plus original bottles 50ml 800, 100ml 1400 "
    "and 150ml 1700. Base notes Incense/Cedar/Sandalwood/Patchouli. "
)
_SAUVAGE = (
    "Dior Sauvage (Dior): 50ml 642 and 90ml 944, plus original bottles 100ml 815 (2 left) and "
    "200ml 1623; the 60ml original is out of stock. "
)

# The reply owed on a turn that asks the price of more than one perfume. Shared by both scenarios
# deliberately, exactly as `scenarios_conv835._CHASE_RULES` is: the required reply is the same one
# whether the perfumes were introduced in one earlier reply or two, and a judge scoring this file
# should not have to know which route into the turn was taken.
_PLURAL_COVERAGE_RULES = (
    "✅ REQUIRED: every perfume the customer is asking about gets its price in THIS reply, each "
    "under its own full name. Two perfumes asked about means two perfumes priced. "
    "❌ CRITICAL: pricing one of them and saying nothing about the other. That is the defect both "
    "of these conversations were reported for, and it leaves the customer to ask a second time for "
    "something they already asked once. "
    "❌ CRITICAL: adding the two prices together, quoting a combined figure, multiplying by a "
    "quantity, or using the words 'الإجمالي' / 'المجموع' / 'الطلبين'. The question 'بكام الاتنين' "
    "literally reads 'how much are the two', but the answer is each perfume's own prices side by "
    "side — a total for an order that does not exist is a fabricated number. "
    "❌ CRITICAL: shrinking to one perfume in order to avoid quoting a total. Both prices, no sum. "
    "❌ CRITICAL: answering with a size ladder for one perfume and nothing for the other. Whichever "
    "perfume the reply happens to open with, the second one still needs its own prices. "
    "❌ CRITICAL: reading the plural pointer itself ('الاتنين', 'لااتنين', 'دو', 'دول') as a perfume "
    "name — deferring on it ('لحظة أتأكدلك من لااتنين'), saying it is unavailable, or asking the "
    "customer which perfume 'لااتنين' is. It means 'the two', and which two is unambiguous from the "
    "reply just above it. "
    "❌ Never quote a price that is not one of the figures listed above, and never quote an "
    "out-of-stock size as available. "
    "✅ A reply that also asks which size the customer wants, after both prices are given, is good "
    "selling and not a failure."
)

SCENARIOS = [
    {
        "id": "CONV841",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك لادور بخور ؟",
            "بكام لااتنين ؟",
        ],
        "probe": (
            "Replay of conversation 841. "
            + _SPORT
            + _BLEU
            + "لادور بخور is not in this catalogue, and failing to find a name is not the same fact "
            "as not stocking it. "
            "Turn 1 was right and must stay right: 'لحظة أتأكدلك منه' for لادور بخور, then one or "
            "two stocked perfumes offered by FULL name and clearly labelled as DIFFERENT perfumes. "
            "❌ CRITICAL: saying لادور بخور is not available on this turn. ❌ CRITICAL: a price list "
            "for a turn that only asked about availability. ❌ CRITICAL: attributing a بخور, incense "
            "or frankincense note to a perfume whose recorded notes do not contain one — Dior Homme "
            "Sport has olibanum and Bleu de Chanel has incense, cite those and no others. "
            "Turn 2 ('بكام لااتنين ؟' — 'how much are the two?', with the definite article of "
            "'الاتنين' mistyped) asks the price of BOTH perfumes turn 1 offered. What actually "
            "happened: Dior Homme Sport's sizes were quoted and Bleu de Chanel was not mentioned at "
            "all, even though both rows were in front of the model with full prices. "
            + _PLURAL_COVERAGE_RULES
            + " ❌ CRITICAL and separately tracked: the لادور بخور promise disappearing. Turn 1 "
            "promised to check and turn 2 never mentions لادور بخور again — the customer is still "
            "owed that answer, and a reply that quietly drops it ends the conversation with an open "
            "question nobody is looking into. ✅ The prices AND one line keeping the promise alive "
            "belong in the same reply. (This half is a known separate defect: the chase machinery "
            "does not fire on a price question, so a run that prices both perfumes and still loses "
            "the promise is the expected partial pass, not a regression.)"
        ),
    },
    {
        "id": "CONV842",
        "category": "regression",
        "persona": "browser",
        "turns": [
            "عندك سوفاج ؟",
            "طب بلو دي شانيل ؟",
            "بكام الاتنين",
        ],
        "probe": (
            "Replay of conversation 842. "
            + _SAUVAGE
            + _BLEU
            + "Turns 1 and 2 each ask about availability ONLY, one perfume at a time, and both were "
            "answered correctly: 'متوفر عندنا يا فندم' plus a narrowing question about size. "
            "✅ Both must keep that shape. ❌ CRITICAL: turning either into a price list or a size "
            "recommendation — the customer has not asked about price yet, and a value-pick verdict "
            "on a turn about availability is a rule violation regardless of how accurate it is. "
            "❌ CRITICAL: replying 'أه متوفر' and stopping, with no question that moves the "
            "conversation on. "
            "Turn 3 ('بكام الاتنين' — 'how much are the two') asks the price of BOTH perfumes, and "
            "they were introduced on two SEPARATE turns: Dior Sauvage on turn 1, Bleu de Chanel on "
            "turn 2. Nothing in the message names either of them, so the whole answer rests on the "
            "conversation. What actually happened: only Bleu de Chanel's row reached the model at "
            "all, and its prices were the entire reply. "
            + _PLURAL_COVERAGE_RULES
            + " ❌ CRITICAL: treating the older perfume as forgotten. Dior Sauvage was confirmed "
            "available two messages earlier and 'الاتنين' is exactly the customer's way of pointing "
            "back at it — asking 'أنهي اتنين؟' or answering about Bleu de Chanel alone both drop a "
            "perfume that is plainly still on the table."
        ),
    },
]
