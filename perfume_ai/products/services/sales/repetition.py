"""Saying the same sentence twice, detected below the level of a whole reply.

Three guards already ask "is this reply a repeat" and all three are calibrated on the whole text:
`router._is_repetitive` (> 0.7 against the last 4 replies), `router._count_recent_repetitions`
(> 0.7, walking back until the first miss) and `eval_harness.checks.check_repeated_reply` (>= 0.9).
Conversation 973 got past every one of them and was correct to: measured on its five replies, the
highest whole-reply ratio was **0.451**, replies 2 against 4 — the pair that re-offered the same
two perfumes.

What repeated was smaller. The closing frame "أنا أرشحلك X أكتر لأنه…" ends four of the five
replies and the price-listing frame "عندك X الـ50 ملي بـN جنيه داخل الميزانية، و…" opens four of
them. Compared sentence against sentence with the masking `_mask` applies, those two frames pair off
at up to 0.699 and 0.821 — four pairings of each above `SENTENCE_THRESHOLD`. A customer reads four
replies opening and closing on the same sentence with a different noun in it; the pipeline saw
replies that were 55% different and called them fresh.

`prompts.py` already forbids this twice in words — ":94" ("ممنوع تكرر نفس الجملة ولا نفس الخاتمة
ولا نفس الفكرة") and ":143" ("متكررش معلومة قلتها في المحادثة") — and both were violated in the
same transcript. `get_system_prompt`'s own docstring records why that is the expected outcome: at
roughly sixty competing 🔴 rules the model stops tracking any of them, and mechanical rules belong
in code. So this module is code, and adds no prompt rule.

Not in `reply_sanitizer`, which is deliberately Django-free with no history access, and whose
bail-rather-than-empty rule makes it the wrong home for something that needs a second *generation*
rather than a strip — stripping a recommendation's closing sentence would leave a reply with no
verdict, which is the one thing instruction 2 of that prompt insists on. Not in `router` either:
that module is already 940 lines and this needs tests of its own.
"""

import re
from collections import Counter
from difflib import SequenceMatcher

# Ratio at which two sentences are the same sentence.
#
# Calibrated on conversation 973's five replies, with catalogue names and `_MANDATED` masked (the
# masking `_mask` does). Every sentence pair either side of this number:
#
#   caught    0.821  the price frame, replies 3 vs 5
#             0.699  the closing frame, replies 4 vs 5
#             0.694  the price frame, replies 4 vs 5
#             0.693  the closing frame, replies 3 vs 5
#             0.642  the price frame, replies 3 vs 5
#             0.636  the price frame, replies 3 vs 4
#             0.629  the price frame, replies 4 vs 5
#   -------- 0.62 --------
#   left      0.604  the closing frame, replies 3 vs 4
#             0.593  the closing frame, replies 2 vs 5
#
# Both frames the conversation looped on are caught in both of their repeats. What sits immediately
# below is the widest-apart instance of the closing frame, and the only pair where the two sentences
# read as genuinely different sentences: "لأنه يناسب كل المواسم والمناسبات الرسمية، أما ◆ فهو مناسب
# أكتر للاستخدام اليومي والجو الربيعي" against "لأنه عطر تركيب حصري من تصميمنا وفيه ورد، مناسب
# للاستخدام اليومي". They share a frame and a clause; they are not the same sentence. Sitting the
# threshold just above them fires on nothing a reader would defend.
#
# A false positive here costs one extra model call and a reply phrased differently, which is cheap.
# The cost of moving it lower is not the call — it is that a model told "you already said this"
# about a sentence it did not repeat has been observed to change the *fact* to escape the warning
# (see `product_info.get_product_info`'s retry docstring, conversation 816).
SENTENCE_THRESHOLD = 0.62

# Below this a sentence is too short for similarity to carry meaning: "تحت أمرك يا فندم" twice is
# politeness, not a loop, and Arabic courtesy formulae are near-identical by construction. Same
# judgement `checks.check_repeated_reply` makes with `min_length` at reply level.
MIN_SENTENCE_WORDS = 5

# How many previous replies to compare against. Matches `router._is_repetitive`'s window so the two
# guards read the same history, and it spans conversation 973's 2↔5 pairing.
WINDOW = 4

# Sentence boundaries. `،` is deliberately absent: an Arabic comma joins clauses inside one sentence
# far more often than it ends one, and splitting on it turned the price frame into fragments of two
# and three words that `MIN_SENTENCE_WORDS` then discarded — the frame this exists to catch.
_BOUNDARY = re.compile(r"[.\n!?؟]+")

# What a masked catalogue name becomes. A single character, so the mask cannot itself carry
# similarity: replacing "Coco Mademoiselle" with "PRODUCT" would make two sentences about different
# perfumes share seven letters they did not share before, inflating every ratio in the direction of
# a false positive.
_MASK = "◆"

# Wording the persona REQUIRES verbatim, masked for the same reason a product name is.
#
# `prompts.py:104` and `:106`, `recommendation.py:502` / `:548-550` and `objection_service.py:171`
# all order the phrase "أعلى حاجة بسيطة من ميزانيتك" to be said **بالحرف**, and the budget markers
# "داخل الميزانية" / "داخل ميزانيتك" are the only sanctioned way to report a ✅ price — the whole
# `strip_false_over_budget` apparatus in `reply_sanitizer` exists because paraphrasing them produced
# false claims about a customer's budget.
#
# So a reply quoting two prices is obliged to contain these strings, and asking the model to rephrase
# a sentence built around them would put this guard in direct conflict with five separate rules —
# and a model resolving that conflict by rewording the budget verdict is `strip_false_over_budget`'s
# failure mode arriving from a new direction. Masking them means the comparison sees how the sentence
# is BUILT rather than the fixed vocabulary it is obliged to contain.
#
# Measured on conversation 973 this loses nothing and gains one. Both closing-frame hits are
# unchanged (0.693 and 0.699 — that sentence quotes no mandated phrase), the price frames stay
# caught, and the price frame of replies 3 vs 4 joins them at 0.636 where names-only masking left it
# at 0.560. The individual price ratios move both ways rather than up: masking removes text the two
# sentences shared, which lowers the ratio where the rest of the sentence differs (3 vs 5's top pair
# 0.758 → 0.821 as the remainder really is the same; 4 vs 5's 0.746 → 0.694 as it is not). That is
# the intended effect — the comparison now reads how the sentence is built, not the boilerplate it
# is obliged to carry.
_MANDATED = (
    "أعلى حاجة بسيطة من ميزانيتك",
    "أعلى حاجة بسيطة",
    "الاتنين داخلين الميزانية",
    "داخل الميزانية",
    "داخل ميزانيتك",
)
_MANDATED_MASK = "◇"

# How many opening words make a "frame". Four, measured: the closing frame this exists for is
# "أنا أرشحلك ◆ أكتر" and the price frame is "عندك ◆ الـ# ملي" — four words each once masked. Five
# words reaches into the reason clause ("لأنه…"), which is the part the model DOES vary, so a
# five-word prefix splits one frame into as many frames as there are reasons and stops matching:
# on the two replay transcripts it drops turn 4 and keeps only turn 5.
FRAME_WORDS = 4

# Fire on the THIRD outing, not the second. A frame reused once is a habit of speech; three replies
# in four opening a sentence the same way is the template conversation 973 read like.
#
# Measured over the last 200 conversations in this store (713 assistant replies, 658 once scripted
# literals are excluded): at the third occurrence 21 turns flag — 3.2%, of which 11 are this exact
# closing frame and the rest are price frames ("وكمان ◆ # ملي", "عندك ◆ الـ# ملي", "أنصحك بـ ◆ #").
# At the second occurrence it is 122 turns, 17.1%, mostly pairs that read as ordinary repetition of
# a price line — not what the customer complained about, and each one costs a model call.
FRAME_MIN_USES = 3

# Digits are masked in a frame and NOT in a whole sentence, which is the one place these two
# comparisons differ on masking. A frame is a template and its numbers are the data poured into it:
# "عندك ◆ الـ50 ملي بـ549" and "عندك ◆ الـ90 ملي بـ692" are the same frame twice, and leaving the
# digits in makes them two frames. At whole-sentence level the digits are most of what distinguishes
# two price sentences, which is why `repeated_sentences` keeps them — and why neither comparison
# inherits `checks.check_repeated_reply`'s digit *exemption*.
_DIGITS = re.compile(r"[0-9٠-٩]+")
_DIGIT_MASK = "#"


def _sentences(text):
    """The clauses of a reply that are long enough to be worth comparing."""
    return [
        clause
        for clause in (part.strip() for part in _BOUNDARY.split(text or ""))
        if len(clause.split()) >= MIN_SENTENCE_WORDS
    ]


def _catalogue_names(store):
    if store is None:
        return []

    from products.models import Product

    return [
        name
        for name in Product.objects.filter(store=store).values_list("name", flat=True)
        if name
    ]


def _earlier_replies(history):
    """The last `WINDOW` assistant replies, newest first.

    Shared by both comparisons below so they cannot read different histories — the bug
    `router._is_repetitive` and this module were calibrated together to avoid.
    """
    return [
        (entry.get("content") or "")
        for entry in reversed(history or ())
        if entry.get("role") == "assistant"
    ][:WINDOW]


def _mask(text, names):
    """Replace catalogue names with `_MASK`, longest first.

    Longest first for the reason `naming.names_in` consumes the longest match at each position:
    catalogue names nest, so masking "Stronger With You" before "Stronger With You Intensely" would
    leave a stray " Intensely" behind and two sentences about the two perfumes would stop looking
    alike for a reason that has nothing to do with how they are written.

    `_MANDATED` goes with them: phrases the persona requires verbatim are not this reply's own
    wording, so they are not evidence of anything about how it was written.
    """
    masked = text
    for name in sorted(names or (), key=len, reverse=True):
        masked = re.sub(re.escape(name), _MASK, masked, flags=re.IGNORECASE)
    # Longest first here too: "أعلى حاجة بسيطة من ميزانيتك" contains "أعلى حاجة بسيطة", and masking
    # the shorter one first would leave a " من ميزانيتك" tail behind on one side only.
    for phrase in sorted(_MANDATED, key=len, reverse=True):
        masked = masked.replace(phrase, _MANDATED_MASK)
    return masked


def repeated_sentences(reply, history, store=None):
    """Sentences in this draft we have already said, quoted from the draft.

    Returns the offending sentences as they appear in the DRAFT — unmasked, in order, deduplicated —
    so the caller can put them in front of the model verbatim. That return shape is the whole
    difference from `router._is_repetitive`: a whole-reply ratio can only answer yes or no, and a
    model told "your reply was repetitive" cannot see which part to change. Conversation 973's
    replies would each have been answered "no" anyway.

    Catalogue names are masked before comparison and only for comparison. "أنا أرشحلك Bloom أكتر
    لأنه فيه ورد" and the same sentence about Light Blue ARE the same sentence — a different noun in
    a frame the customer has already read four times is the repetition they notice, and it is
    invisible to any comparison that treats the noun as content. Masking is what makes it a signal:
    conversation 973's two closing-frame repeats measure 0.631 and 0.623 on the raw text — under
    `SENTENCE_THRESHOLD`, undetected — and 0.699 and 0.693 masked. Wording the persona
    mandates verbatim is masked alongside the names, for the reason `_MANDATED` records: this guard
    must not ask the model to rephrase a sentence five other rules require it to phrase that way.

    **No digit exemption**, and this is the one place this module deliberately parts company with
    `checks.check_repeated_reply`, which skips any pair whose figures differ. That exemption is
    right at reply level: a changed number there means a recap template rendered new data, which is
    progress (conversation 931 turn 7 repeats its entire order recap and differs by one added item).
    It is wrong at sentence level, because a price frame about a different perfume carries different
    figures *by construction* — every price-frame pair in conversation 973 differs in every number,
    and inheriting the exemption would blind this to one of the two frames it exists for.
    """
    draft = (reply or "").strip()
    if not draft or not history:
        return []

    earlier = _earlier_replies(history)
    if not earlier:
        return []

    names = _catalogue_names(store)
    previous = [
        masked
        for text in earlier
        for masked in _sentences(_mask(text, names))
    ]
    if not previous:
        return []

    repeats, seen = [], set()
    for sentence in _sentences(draft):
        masked = _mask(sentence, names)
        for before in previous:
            if SequenceMatcher(None, masked, before).ratio() >= SENTENCE_THRESHOLD:
                if sentence not in seen:
                    seen.add(sentence)
                    repeats.append(sentence)
                break
    return repeats


def _frame(sentence, names):
    """The first `FRAME_WORDS` words of a sentence, masked — or None if it is shorter.

    None rather than a short frame: a two-word opening is shared by half the replies in any
    conversation ("تمام يا فندم") and matching on it would fire on politeness.
    """
    words = _DIGITS.sub(_DIGIT_MASK, _mask(sentence, names)).split()
    if len(words) < FRAME_WORDS:
        return None
    return " ".join(words[:FRAME_WORDS])


def repeated_frames(reply, history, store=None):
    """Sentence openings this draft reuses from a frame it has already used twice.

    The half of conversation 973 that survived `repeated_sentences`. That function caught both of
    the price frame's repeats and two of the closing frame's, and the closing frame still ended
    four of five replies in both replays — its remaining pairs measure 0.604, 0.588 and below,
    under `SENTENCE_THRESHOLD` and correctly so: the model varies the reason clause enough that the
    two sentences really are different sentences. What it does not vary is the first four words,
    and that is what a customer reading four replies in a row sees.

    Deliberately NOT a lower `SENTENCE_THRESHOLD`. 0.58 would catch this frame and would also catch
    the 0.604 pair the calibration table above documents as the one pair a reader would defend,
    plus everything between. A prefix test is the narrow instrument: it fires on the frame and is
    indifferent to how much the rest of the sentence differs.

    Counted per earlier REPLY rather than per sentence, so a reply listing three prices in the same
    frame is one use of it. Three sentences in one breath is a list; the same opening in three
    separate replies is a template.

    Returns the offending sentences from the DRAFT — unmasked, in order, deduplicated — the same
    shape `repeated_sentences` returns, so `retry_hint` takes either list.
    """
    draft = (reply or "").strip()
    if not draft or not history:
        return []

    earlier = _earlier_replies(history)
    if not earlier:
        return []

    names = _catalogue_names(store)

    uses = Counter()
    for text in earlier:
        seen_here = {
            frame
            for frame in (_frame(sentence, names) for sentence in _sentences(text))
            if frame
        }
        uses.update(seen_here)

    repeats, seen = [], set()
    for sentence in _sentences(draft):
        frame = _frame(sentence, names)
        if not frame or uses[frame] < FRAME_MIN_USES - 1:
            continue
        if sentence not in seen:
            seen.add(sentence)
            repeats.append(sentence)
    return repeats


def retry_hint(repeats):
    """Instruction text quoting back what the draft repeated.

    Quoted rather than described, because the described version is already in the system prompt
    twice and conversation 973 violated both copies. A model that cannot see which sentence it
    repeated cannot avoid repeating it.

    The last line is `get_product_info`'s retry lesson, transplanted: a retry told only "you
    repeated yourself" rewrote the *answer* to escape the warning. Conversation 816's turn 4 turned
    a correct "we don't stock it" into "لحظة أتأكدلك منه" — a promise nothing in this pipeline
    keeps — because that was a different thing to say. The phrasing changes; the facts do not.
    """
    if not repeats:
        return ""

    quoted = "\n".join(f"- «{sentence.strip()}»" for sentence in repeats[:3])
    return (
        "\n⚠️ الجمل دي أنت قلتها بالفعل في المحادثة، والعميل قراها:\n"
        f"{quoted}\n"
        "🔴 قول نفس المعنى بصيغة تانية خالص — غيّر التركيب والزاوية اللي بتتكلم بيها، مش الأسماء "
        "والأرقام بس.\n"
        # Named separately because `repeated_frames` can flag a sentence whose *whole* text is new:
        # only its opening repeats, and "قول نفس المعنى بصيغة تانية" reads as being about the
        # sentence as a whole, so a model that had already varied the rest would think it complied.
        "🔴 وابدأ الجملة نفسها بشكل مختلف — الافتتاحية اللي بتفتح بيها كل رد بقت متكررة، حتى لو "
        "باقي الجملة اتغير.\n"
        "❌ ومتغيّرش المعلومة نفسها عشان تهرب من التنبيه ده: السعر هو السعر، والترشيح هو الترشيح، "
        "والمتوفر هو المتوفر. الصيغة بس هي اللي تتغير.\n"
    )
