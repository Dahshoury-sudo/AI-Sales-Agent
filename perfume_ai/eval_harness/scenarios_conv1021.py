# -*- coding: utf-8 -*-
"""Conversation 1021: a perfume we stock denied by name, and a name we could not read
answered with the previous perfume's price.

Eight perfume questions in nineteen minutes (2026-09-25 01:56–02:15), all in Egyptian phonetic
spelling. Five were answered correctly. Three were not, and two of those three gave the customer a
confidently *wrong* answer rather than a stall — which is the harder failure, because nothing in the
reply tells the customer to doubt it:

  1021  "طب لامال لكريز"   → "عطر لامال لكريز مش عندنا يا فندم" plus two alternatives.
                              Le Male Elixir was active in that store's catalogue. ❌ ABSENCE_DENIED
        "طب في bmw"        → asked to retype. A stall, and the honest one of the three.
        "B m w"            → "عطر Le Male من Jean Paul Gaultier متوفر عندنا، الـ 100 ملي سعره 600
                              جنيه" — the customer retyped the name they had just been asked to
                              retype, and got a price for a perfume they had never mentioned, with
                              no marker on the turn at all. ❌

The denial is provable from the transcript rather than argued. Turns 25, 27, 29 and 33 all carry
`⚠️ عطر مختلف عن: Le Male Elixir، Ultra Male`, and `product_formatting._line_mates_for` builds that
warning only from `is_active=True` rows of that store. We denied the perfume at 02:0x and listed it
as available two turns later.

Two root causes, one per failure.

The denial is `absence.catalogue_verdict`. For an Arabic name the extractor's own `unplaced` report
is the *only* witness there is (`absence.py:140-144`) — `Product.name` holds Latin spellings, there
is no alias column and no transliteration anywhere, so Python cannot clear "لامال لكريز" against
Latin rows no matter how careful the token matching is. One unverified witness denied a stocked
perfume. What made the witness wrong: the extractor prompt listed `Le Male`, `Le Male Elixir` and
`Ultra Male` as three unrelated strings, so a name whose head is a line root and whose tail is a
flanker word read as one unknown perfume. The grouping already existed in `naming.line_mates`;
nothing was showing it to the model. Both halves are now closed — the prompt carries a families
block, and a denial on an Arabic span needs a second confirming call before it goes out.

The stale price is `naming.tokens`, which dropped every token of length ≤ 1, so `tokens("B m w")`
was empty, `may_name_a_perfume` was False, and **the resolver was never called**. With no resolution
there was no unplaced span to report and no denial to make; `product_info` fell through to
`_referent_from_conversation`, and the previously offered rows became the answer under the bare
`بيانات المنتجات الحقيقية` header. Turn 27 shows what the same rows look like when the pipeline knows
they are not the subject — `⚠️ العطور اللي تحت دي اللي كنا بنتكلم عنها — مش العطر اللي العميل سأل عنه`
— and turn 29 carries no such line. That label is now unconditional on referent rows, and runs of
single characters fuse before the length filter.

🔴 **This file cannot replay the transcript verbatim, and the reason matters.** Conversation 1021 ran
against a different store: twelve Jean Paul Gaultier rows, every product priced 250/350/600. The
harness store is `Perfamix` (`runner.py:77`), whose 40 active products contain `Le Male` at 623/856
and **no `Le Male Elixir` and no `Ultra Male`**. Replaying turn 22 against Perfamix would assert a
ground truth that is false there — `checks.build_ground_truth` reads the same rows and would be
right to call the Elixir absent — so the probe would be grading the wrong answer as correct.

What is substituted is turn 22 alone, and it is substituted for the one line Perfamix actually has:
`Stronger With You` / `Stronger With You Intensely` / `Stronger With You Absolutely`, which
`naming.families` groups and which is the *only* family in that catalogue. It is the same structure
that failed — root plus flanker word, flanker stocked, shorthand fitting three rows — and it is a
line this business has already been burned on once: conversation 768 quoted the base's 700 as
Intensely's price and then apologised for a mistake it had not made. The other four turns are
verbatim from 1021.

Expect one `repeated_reply` from `rescore` on this scenario, and do not read it as a regression.
Three of the five turns name a perfume we do not stock, so three replies in a row are a denial plus
the same shortlist of alternatives, and turns 4 and 5 measured 0.727 against rescore's deliberately
low 0.7 floor (`rescore.py:313`) — the live runner's 0.9-over-4 does not fire. The same check fires
on `conv772`, whose turn 3 measures 0.791 and whose probe *requires* that reply to carry turn 2's
answer forward. It is the conversation 973 complaint — repeated sentence frames — which
`sales/repetition.py` owns and which no part of this change touches.
"""

_INTENSELY = (
    "Stronger With You Intensely (Emporio Armani) IS stocked and active: 50ml 450 and 90ml 780, "
    "plus original bottles 100ml 4200 and 200ml 7500. It is one of THREE separate perfumes on that "
    "line, all stocked: Stronger With You (50ml 400, 90ml 700, original 100ml 3800 / 200ml 6800) "
    "and Stronger With You Absolutely (50ml 480, 90ml 830, original 100ml 4500 / 200ml 8000). "
    "Different scents, different compositions, different prices. "
)

_LE_MALE = (
    "Le Male (Jean Paul Gaultier) is stocked at 50ml 623 and 90ml 856, and it is the ONLY Jean Paul "
    "Gaultier perfume in this 40-product catalogue — there is no Le Male Elixir, no Ultra Male, no "
    "Scandal and no Classique here. It has no original-bottle variant, so the fixed sentence "
    "'للأسف مش متوفر منه زجاجة أوريجينال حالياً' is CORRECT if it appears and is not a denial of the "
    "perfume. "
)

_ABSENT = (
    "BMW and جوب (Joop!) are NOT in this catalogue under any spelling, in either alphabet. "
)

SCENARIOS = [
    {
        "id": "CONV1021",
        "category": "regression",
        "persona": "browser",
        # Turn 1 is substituted for "طب لامال لكريز" — same shape, a line this catalogue has.
        # See the module docstring. Turns 2-5 are verbatim from the transcript, typos and all,
        # and in the transcript's order: the retype request comes before the retype.
        "turns": [
            "موجود عطر سترونجر انتنس",
            "طب في من جان بول",
            "طب في bmw",
            "B m w",
            "طب ممكن اعرف في برفيوم جوب",
        ],
        "probe": (
            "Replay of conversation 1021. "
            + _INTENSELY
            + _LE_MALE
            + _ABSENT
            + "Turn 1 ('موجود عطر سترونجر انتنس') is the turn this conversation exists for. It is a "
            "line ROOT plus a clipped flanker word, naming a perfume we stock, in Arabic. "
            "✅ Required: treat it as a perfume we carry — either identify Stronger With You "
            "Intensely by its full name and quote its real prices, or, if the shorthand is read as "
            "ambiguous across the three, ask which of the three they mean, naming them in full. "
            "❌ CRITICAL: saying we do not have it, do not carry it, or cannot find it. It is active "
            "at 450 and 780, and in the real transcript this exact shape of question produced a "
            "denial by name of a perfume that was in stock. That is the worst reply on this turn. "
            "❌ CRITICAL: quoting 400 or 700 as this perfume's price — those are the base Stronger "
            "With You, a different perfume. Conversation 768 is that mistake. "
            "❌ CRITICAL: treating the three as one perfume, or quoting one's price as another's. "
            "❌ CRITICAL: promising to check ('لحظة أتأكدلك منه' / 'هسأل وأرد عليك'). Nothing looks a "
            "perfume up between two messages of a chat, so the promise leaves the customer waiting "
            "for an answer that never comes. "
            "Turn 2 ('طب في من جان بول') asks for the house, not a perfume. ✅ Required: offer Le "
            "Male by full name — its real prices, or a question about size. "
            "❌ CRITICAL: saying we carry no Jean Paul Gaultier. "
            "❌ CRITICAL: naming or pricing any Jean Paul Gaultier perfume other than Le Male. There "
            "is exactly one in this catalogue, and inventing line-mates for it is a hard failure. "
            "Turn 3 ('طب في bmw') names a perfume we do not have, in Latin letters. ✅ Required: say "
            "so plainly, with a short apology, and offer one or two stocked perfumes in the SAME "
            "reply, clearly labelled as different perfumes — the customer came to buy. "
            "❌ CRITICAL: answering about Le Male, or quoting 623 or 856, as though BMW were it. "
            "❌ CRITICAL: promising to check. "
            "Turn 4 ('B m w') is the turn that gave the wrong answer in production. It is the same "
            "name, letter-spaced, sent by a customer who had just been asked to retype it. "
            "✅ Required: the reply must be about the name the customer just typed — either denying "
            "it plainly, as on turn 3, or asking them to write it once more. "
            "❌ CRITICAL: presenting Le Male as the perfume asked about — naming it as the answer, "
            "or attributing its price, notes, longevity or sizes to 'B m w'. In production this "
            "turn answered 'عطر Le Male ... الـ 100 ملي سعره 600 جنيه' to a customer who had never "
            "mentioned Le Male, with nothing in the reply marking it as a different perfume. "
            "Repeating that — presenting a previously discussed perfume as the answer to a name we "
            "could not read — is the failure this turn tests, and it is worse than either honest "
            "reply above. Offering Le Male as an explicitly labelled ALTERNATIVE beside the denial, "
            "the way turn 3 does, is correct and is not this failure. "
            "❌ CRITICAL: promising to check, or claiming to have looked, searched or found "
            "anything ('لقيت' / 'راجعت'). "
            "❌ Contradicting turn 3: BMW cannot be absent on one turn and available on the next. "
            "Turn 5 ('طب ممكن اعرف في برفيوم جوب') names a second perfume we do not have, this time "
            "in Arabic. ✅ Required: the same plain answer as turn 3 — say we do not have it, echo "
            "the name in the customer's own Arabic letters, and offer something stocked beside it. "
            "❌ CRITICAL: answering about Le Male, Stronger With You Intensely, or any other "
            "catalogue perfume as though it were جوب, or claiming one of them is its 'correct name' "
            "or the same perfume. A name we cannot place must not be resolved into a perfume we "
            "happen to have. "
            "❌ CRITICAL: writing جوب in Latin letters as though confirming a catalogue match. "
            "❌ CRITICAL: promising to check. "
            "❌ Any mention of a system, data, catalogue or 'مش ظاهر عندي' on any turn. "
            "❌ Never quote a price that is not among the figures given above."
        ),
    },
]
