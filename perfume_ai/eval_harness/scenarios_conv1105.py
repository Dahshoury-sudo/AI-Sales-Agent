# -*- coding: utf-8 -*-
"""Conversation 1105: a customer praised the perfume and was asked to spell its name again.

Two messages, 2026-09-30 02:38, store Misk Perfume. Both are compliments; both were answered with a
request to retype a name the customer had never typed:

  1105  "البرفان تحفه وثباته ممتاز"          -> "ممكن تكتب لي اسم العطر تاني بشكل أوضح يا فندم؟
                                                عشان أتأكد حضرتك تقصد أنهي بالظبط."
        "البرفان الي جبته من عندكو واو بجد"  -> "ممكن توضح لي اسم العطر بالظبط أو تكتبه بطريقة تانية
                                                عشان أتأكد حضرتك بتتكلم عن أنهي برفان؟"

Both `internal_context`s carry `PENDING_LOOKUP`, `NAME_UNREADABLE` and
`لم يتم التعرف على اسم منتج محدد`. So the pipeline had decided the customer named a perfume it could
not read — when they had named none at all, and were only being nice.

The cause was in `naming`, and it was three gaps compounding. Membership in `_STOPWORDS` and
`_REFERENTIAL` was exact-string, so "برفان" counted as filler but "البرفان" did not, and "ثباته" did
but "وثباته" did not; "الي" was absent while "اللي" was present; and there was no praise vocabulary
at all. Four filler words survived `identifying_tokens`, `may_name_a_perfume` said yes, the resolver
was asked to place praise, it placed nothing — and `named_but_unresolved` reads an empty placement
as proof of an unreadable name. Measured before the fix: 10 of 10 realistic compliment phrasings
took that path.

🔴 The step that turns a wasted call into a wrong answer is `(resolver_ran and not products)` inside
`named_but_unresolved`. Two comments in the tree — `naming.may_name_a_perfume` and the gate in
`product_info` — promised that a false alarm was "slower, not wrong" and fell through to the referent
anyway. That stopped being true when that clause landed, and this conversation is the proof. Both
comments were corrected in the same change; a reader who trusts them will under-rate the next gap.

Unlike `scenarios_conv1021.py`, **this file replays the transcript verbatim and needs no
substitution.** That is a property of the bug rather than luck: these turns name no perfume, so the
`Perfamix` catalogue is irrelevant to grading them. There is no ground truth about a product to get
wrong, which makes this the one replay that transfers between stores cleanly.

The second half of the fix is `sales/appreciation.py`, which is why the probe asks for thanks and not
merely for the absence of an interrogation. It is an added instruction rather than a branch of its
own: praise is often not the only thing in a message, and a branch would have swallowed the other
half.

🔴 The probe forbids asking which perfume it was. That is the store owner's explicit decision, taken
over the alternative of thanking and then asking — asking reads as working a customer for a lead
when they were only being nice, and the cost, never learning which perfume they meant, was accepted
deliberately. A grader that rewards the question would be grading against the wrong spec.
"""

_NO_NAME = (
    "Neither turn names a perfume. There is no product to look up, no product data is needed, and "
    "nothing in either message is a question. The customer is complimenting a perfume they already "
    "bought and telling us they are happy with it. "
)

SCENARIOS = [
    {
        "id": "CONV1105",
        "category": "regression",
        "persona": "browser",
        # Verbatim from the transcript, in order, typos and all. Two compliments in a row is the
        # shape that matters: in production both got the same retype request, so the second reply
        # also has to differ from the first.
        "turns": [
            "البرفان تحفه وثباته ممتاز",
            "البرفان الي جبته من عندكو واو بجد",
        ],
        "probe": (
            "Replay of conversation 1105. "
            + _NO_NAME
            + "Turn 1 ('البرفان تحفه وثباته ممتاز') means 'the perfume is gorgeous and its "
            "longevity is excellent'. It is praise. "
            "✅ Required: thank the customer warmly and personally for the compliment. "
            "❌ CRITICAL: asking the customer to write, retype, clarify or spell out the name of "
            "the perfume, in any wording. They never wrote a name. This is the production failure "
            "this replay exists for, and it is the worst reply on this turn. "
            "❌ CRITICAL: saying we could not identify the perfume, are not sure which one they "
            "mean, or need to know which one it is before answering. "
            "❌ CRITICAL: asking which perfume it was, even politely and even after thanking them. "
            "The store owner ruled this out explicitly: the customer is being nice, not opening a "
            "sales conversation, and the question reads as working them for a lead. "
            "❌ CRITICAL: recommending, offering, naming or pricing any perfume. Nothing should be "
            "pitched into a compliment. "
            "Turn 2 ('البرفان الي جبته من عندكو واو بجد') means 'the perfume I got from you — wow, "
            "honestly'. It is a second compliment, about a past purchase. "
            "✅ Required: thank them again, warmly. "
            "✅ Required: the second reply must not repeat the first one sentence for sentence. In "
            "production both turns got the same retype request; two identical thank-yous would be "
            "the same defect with better manners. "
            "❌ CRITICAL: all four prohibitions from turn 1 apply again, unchanged — no retype "
            "request, no 'which perfume', no uncertainty about the name, no perfume offered. "
            "❌ CRITICAL: treating the past purchase as a complaint. Nothing here is negative; an "
            "apology or an offer to resolve something would be answering a message they did not "
            "send."
        ),
    },
]
