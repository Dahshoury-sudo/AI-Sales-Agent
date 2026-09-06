# -*- coding: utf-8 -*-
"""Replay of conversation 973: the same two perfumes again, in the same two sentences.

Five turns. Rose, women's, 1010. Turn 2 offered Bloom and Coco Mademoiselle; "اي تاني" got
Jasmino and Good Girl; then the customer said **مش عايز حاجه من البراند بتاعكو** — and turn 4
answered with Bloom and Coco Mademoiselle. The two they had already seen and moved past.

Two independent defects, and both need watching here because a fix for either one leaves the
transcript looking wrong.

**The perfumes.** Repeat suppression was entirely `intent["exclude_names"]`-driven, and
`ai/intent.py` fills that slot on one trigger: the customer asking for an alternative ("في حاجة
تانية", "بديل", "زي"). Turn 4 is a *refusal* of a house, not a request for more options, so
`exclude_names` came back empty, the intent was otherwise unchanged, and `ranking.rank` re-derived
turn 2's shortlist verbatim. `already_described` did hold both names and `repeat_ban_hint` was
injected, but that hint only bans re-*describing* a scent — nothing demoted a perfume for having
been offered. `ranking.WEIGHTS["repeat"]` and `described.offered_ever` are what changed: every
perfume the customer has seen is now demoted whether or not they asked, and never deleted.

Note what turn 4 also *is*: Jasmino, offered one turn earlier, is a Perfamix own blend, and
"البراند بتاعكو" is a refusal of exactly that. `exclude_brands` should catch it — this replay
grades whether the refusal holds while the repeat penalty is also biting, which no other scenario
puts together.

**The sentences.** Four of the five replies open on "عندك X الـ50 ملي بـN جنيه داخل الميزانية،
و…" and four close on "أنا أرشحلك X أكتر لأنه…". Every repetition guard in the pipeline was
correctly silent, because all three measure whole replies: `router._is_repetitive` (> 0.7),
`_count_recent_repetitions` (> 0.7) and `checks.check_repeated_reply` (>= 0.9), against a maximum
whole-reply ratio of **0.451** across those five. The repetition a customer notices sat below the
reply — the frames pair off at up to 0.821 sentence-against-sentence with catalogue names masked.
`sales/repetition.py` looks there and `router._rephrased` regenerates once with the offending
sentence quoted back.

`prompts.py:94` already forbids this in words ("ممنوع تكرر نفس الجملة ولا نفس الخاتمة ولا نفس
الفكرة") and `:143` forbids it again; both were violated in this transcript, which is what
`get_system_prompt`'s docstring predicts at ~60 competing 🔴 rules. No prompt rule was added.

Every `internal_context` in the exported `conv_973.json` is `""` — the dump drops the column, it
was not empty on the wire. The stored rows carry 7158 / 5451 / 6037 / 3316 characters, which is
why `described.offered_ever` can read this very conversation back and does: it returns all six
perfumes the five replies named. Watch `context_chars` on turns 4 and 5 in the harness output
rather than the dump.

Run it twice. Extractor variance between runs is what exposed two separate bugs in the conv_990
work that a single run had hidden — and here the two defects are independent, so one run can
easily show one fixed and hide the other.
"""

SCENARIOS = [
    {
        "id": "CONV973",
        "category": "regression",
        "persona": "hard_budget",
        "assert_budget": 1010,
        # Verbatim from the transcript, typos and all.
        "turns": [
            "عايز برفان حريمي فيه ريحه ورد",
            "1010",
            "اي تاني",
            "مش عايز حاجه من البراند بتاعكو",
            "اي تاني",
        ],
        "probe": (
            "Replay of conversation 973. The customer wants a WOMEN'S perfume with ROSE in it, "
            "budget 1010 EGP per bottle, given in one word on turn 2. "
            "The perfumes with rose in their notes, women's or unisex, with real prices: "
            "Coco Mademoiselle (Chanel) 50ml 534 / 90ml 1006, Bloom (Gucci) 50ml 549 / 90ml 1067, "
            "Rosalia (Perfamix own blend) 50ml 575 / 90ml 1030, Jasmino (Perfamix own blend) 50ml "
            "578 / 90ml 1106, Good Girl (Carolina Herrera) 50ml 650 / 90ml 1083, Light Blue "
            "(Dolce & Gabbana) 50ml 691 / 90ml 852, Chloe Eau de Parfum (Chloe) 50ml 692 / 90ml "
            "1058, Oud Wood (Tom Ford, unisex) 50ml 540 / 90ml 1160, Oudora (Perfamix own blend, "
            "unisex) 50ml 514 / 90ml 988. "
            "❌ Never quote a price for any perfume that is not one of the figures above. "
            "TURN 4 IS THE REPORTED BUG. 'مش عايز حاجه من البراند بتاعكو' refuses the STORE'S OWN "
            "blends — Perfamix is the house brand, and Jasmino, offered on turn 3, is one of them. "
            "❌ CRITICAL on turn 4 and turn 5: offering Bloom or Coco Mademoiselle again. Both "
            "were offered on turn 2 and the customer moved past them with 'اي تاني'. Re-offering "
            "a perfume the customer has already seen and passed over is the whole reported defect, "
            "and it is a finding whether or not the reply is worded differently. "
            "❌ CRITICAL on turn 5: re-offering any perfume that turn 4 already offered. Replay 2 "
            "answered 'اي تاني' with Bloom, Coco Mademoiselle AND Good Girl — three perfumes the "
            "customer had already been shown, and nothing new. 'اي تاني' asks for something else. "
            "❌ CRITICAL on turns 4 and 5: recommending any Perfamix own blend — Jasmino, Rosalia, "
            "Oudora — or describing one as an exclusive in-house composition. The customer refused "
            "the house on turn 4 and never withdrew that. "
            "✅ Required on turn 4: at least one perfume the customer has NOT yet been shown, from "
            "an outside brand, with its real price. Light Blue, Chloe Eau de Parfum and Oud Wood "
            "are all available and none had been offered by then. "
            "✅ Allowed once on turn 4: acknowledging the refusal of the house — 'تمام، مش هرشحلك "
            "من تركيباتنا'. ❌ CRITICAL: re-asking about it on turn 5, or asking again whether the "
            "customer minds the own brand. It was said once and clearly. "
            "❌ CRITICAL: re-asking the budget on any turn after turn 2. It is 1010. "
            "❌ CRITICAL: saying nothing else matches, or that these are all the available options, "
            "on turn 4 or 5. Seven outside-brand perfumes with rose sit under 1010 in some size. "
            "THE SECOND DEFECT IS THE PHRASING, and it is graded independently of which perfumes "
            "are named. "
            "❌ CRITICAL: opening three or more replies on the same sentence frame — 'عندك X الـ50 "
            "ملي بـN جنيه داخل الميزانية، وY الـ50 ملي بـM جنيه كمان داخل الميزانية' with nothing "
            "changed but the names and the numbers. Four of the five original replies did this. "
            "❌ CRITICAL: closing three or more replies on the same sentence frame — 'أنا أرشحلك X "
            "أكتر لأنه…' followed by a reason. Four of the five original replies ended this way, "
            "and a customer reading the same closing sentence four times with a different noun in "
            "it is the repetition being fixed. "
            "✅ Required across turns 3, 4 and 5: each reply reaches its recommendation by a "
            "visibly different route — a different opening, a different way of putting the "
            "comparison, a different angle on why. Naming new perfumes in an unchanged template is "
            "not a different reply. "
            "✅ Still required, and NOT in tension with the above: every price stated with its "
            "budget verdict, and 'أعلى حاجة بسيطة من ميزانيتك' used verbatim for a size that is "
            "over 1010. Those phrasings are mandated; varying the SENTENCE is what is asked for, "
            "not varying the facts or the sanctioned budget wording. "
            "❌ CRITICAL: changing a price, a verdict, or which perfume is recommended between two "
            "replies in order to sound different. The facts are the facts. "
            "✅ Required on turn 1: ask for the budget, or recommend. Either is fine. "
            "❌ CRITICAL: quoting Jasmino's 90ml at 1030 or Rosalia's at 1106 — those two figures "
            "belong to the other perfume, and swapping them is the kind of error a repeated "
            "template invites."
        ),
    }
]
