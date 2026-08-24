"""What the customer has already been told, and whether this turn is a follow-up.

The mirror image of `constraints.py`: that module renders what the customer told *us*, this
one what we told *them*.

The failure this exists for (conv_990.txt): the bot described Dior Homme Intense four times
across six turns, opening three replies with the identical "تمام جداً، أرشحلك Dior Homme
Intense لأنه ثابت وفوحانه متوسط". Two of those turns were the customer *answering a question
the bot had asked* — "راجل", "بالنهار اكتر" — and each answer produced a full re-pitch.

Nothing in the pipeline knew the customer had already been told. Worse, the machinery pushed
the other way: `longevity` and `projection` persist in conversation.preferences from the
first turn, so `ranking.reasons_note` re-injected the same evidence line every turn, and
`ai/recommendation.py` instruction 7 says to lean on it. The repeated phrase was a near
literal rendering of that line.
"""

from ..static_faq_service import normalize_arabic

# A customer asking us to pick between options already on the table. Not a new request: the
# perfumes have been described, and the useful answer is a verdict, not a re-description.
_CHOOSE_MARKERS = (
    "انهي", "انهى", "ايهما", "مين فيهم", "مين منهم", "احسن منهم", "افضل منهم",
    "اي واحد", "انهي واحد", "ترشحلي انهي", "تنصحني بانهي",
)

# Beyond this a message is making its own case rather than answering ours, even if the
# previous reply happened to end in a question.
_SHORT_ANSWER_WORDS = 6


def already_described(history, store):
    """Catalogue product names the bot has already named to this customer.

    Only assistant messages are scanned. A perfume the *customer* named is not something we
    have described — they may be asking about it for the first time.

    Matched by substring on the stored name, which is reliable here because names are stored
    and emitted in Latin: "Dior Homme Intense", "Le Male", "Y Eau de Parfum" and
    "XJ 1861 Naxos" all appear verbatim in the transcript this was written for. A name the
    bot rendered in Arabic transliteration is missed, and that turn behaves as it does today.

    "Named" is treated as "described" deliberately. Telling the two apart would need to parse
    the reply, and once a perfume has been put in front of a customer they have been told
    about it — which is the standard being applied.
    """
    if not history or store is None:
        return frozenset()

    said = " ".join(
        (entry.get("content") or "").lower()
        for entry in history
        if entry.get("role") == "assistant"
    )
    if not said:
        return frozenset()

    from products.models import Product

    names = Product.objects.filter(store=store).values_list("name", flat=True)
    return frozenset(name for name in names if name and name.lower() in said)


def _asked_a_question(history):
    """Did our last reply end by asking something?"""
    for entry in reversed(history or ()):
        if entry.get("role") == "assistant":
            return (entry.get("content") or "").rstrip().endswith(("؟", "?"))
    return False


def is_follow_up(message, history=None, described=()):
    """Is the customer answering us, or asking us to choose, rather than opening anew?

    Conservative on purpose — a false positive suppresses detail a customer genuinely wanted.
    Requires that something has already been described, so the very first recommendation can
    never be shortened, and then either:

      * our last reply ended in a question and this message is short enough to be its
        answer ("راجل", "بالنهار اكتر"), or
      * the message asks us to pick between what is already on the table
        ("ترشحلي انهي احسن منهم").
    """
    if not described:
        return False

    normalized = normalize_arabic(message or "")
    if not normalized:
        return False

    if any(normalize_arabic(marker) in normalized for marker in _CHOOSE_MARKERS):
        return True

    return (
        _asked_a_question(history)
        and len(normalized.split()) <= _SHORT_ANSWER_WORDS
    )


def repeat_ban_hint(described, follow_up):
    """Tell the model what it has already said, and that saying it again is the defect.

    A hint rather than a script, for the reason constraints.acknowledgement_hint gives: the
    behaviour being replaced was one hardcoded sentence, and mandating a different single
    sentence would be the same bug in nicer clothes.
    """
    if not described:
        return ""

    names = "، ".join(sorted(described)[:4])
    hint = (
        f"\n🗣️ العميل بالفعل سمع منك وصف: {names}.\n"
        f"- ❌ ممنوع تعيد وصف ريحته أو نوتاته أو ثباته أو فوحانه تاني — هو عارفهم خلاص.\n"
        f"- لو هتذكره تاني، قول اسمه وجاوب على اللي سأل عنه بس.\n"
    )
    if not follow_up:
        return hint

    return hint + (
        "\n🔴 الرسالة دي رد على سؤال أنت سألته (أو طلب إنك تختار له)، مش طلب ترشيح جديد:\n"
        "- سطر واحد قصير تقول فيه العطر اللي ترشحه، وخلاص.\n"
        "- ❌ ممنوع تعيد الترشيح من الأول ولا تسرد المواصفات ولا تفتح خيارات جديدة.\n"
        "- ✅ مسموح تضيف حاجة واحدة جديدة بس لو ردّه فعلاً غيّر الترشيح.\n"
    )


def under_discussion(history, store, turns=2):
    """Catalogue names the bot itself named in its last `turns` replies.

    Narrower than `already_described`, and answering a different question. That one asks "has
    the customer heard this before"; this asks "is this what we are talking about right now".

    The failure it exists for (conversation 997): four different pairs of perfumes across
    seven turns, eight in total, with the customer never once rejecting anything — they were
    answering questions and adding constraints. On the last turn they said "معايا 800" and
    Le Male, whose 50ml is 623 and which they had been converging on for two turns, was
    dropped for a perfume they had never seen.

    The cause is that `search_products` re-derives its shortlist from scratch every turn.
    Le Male survived every hard filter and landed at rank 3 — and at rank 3 two persona rules
    conflict with the data deciding which wins: "لو أبدى اهتمام بواحد — خليه الأساس" against
    "المنتج اللي مش في البيانات = مش موجود عندنا" plus "اختر أفضل 1-2 منتج". Staying on the
    perfume was not something the model could do, however firmly it was told to.

    Two replies is the window: it covers the common shape of a recommendation followed by a
    price question about the same perfumes, without pinning the conversation to perfumes the
    customer has genuinely moved past. Assistant messages only — a perfume the *customer*
    named is not one we put on the table.
    """
    if not history or store is None:
        return frozenset()

    recent = [
        (entry.get("content") or "").lower()
        for entry in history
        if entry.get("role") == "assistant"
    ][-turns:]
    if not recent:
        return frozenset()

    blob = " ".join(recent)

    from products.models import Product

    names = Product.objects.filter(store=store).values_list("name", flat=True)
    return frozenset(name for name in names if name and name.lower() in blob)


def continuity_note(keeping, dropped, converge=False):
    """Stay on what the conversation is already about, and account for what left it.

    `dropped` names perfumes that *were* under discussion and no longer pass the customer's
    constraints. Naming them is the point: a perfume vanishing without comment is what made
    conversation 997 read as random, while "Le Male لسه داخل الـ800، بس Green Irish Tweed خرج
    من الميزانية" is a genuinely useful answer.

    `converge` comes from `is_follow_up`, so the two fixes compose — that one makes the reply
    short, this one makes it about the right perfume. Without the converge clause the ranking
    boost alone still leaves the model free to present a fresh pair every turn.
    """
    if not keeping and not dropped:
        return ""

    parts = []
    if keeping:
        parts.append(
            "\n🎯 الكلام دلوقتي على: " + "، ".join(sorted(keeping)) + ".\n"
            "- 🔴 كمّل عليهم. ❌ ممنوع تقلب على عطور جديدة والعميل لسه بيسأل عن دول — ده "
            "بيحسسه إنك بتخبط.\n"
            "- ✅ غيّر بس لو واحد منهم مطابقش شرط جديد قاله، وساعتها قول السبب.\n"
        )
    if converge and keeping:
        parts.append(
            "- 🔴 العميل بيرد على سؤال أنت سألته، فالجواب عطر **واحد** بالاسم من اللي فوق. "
            "❌ ممنوع تعرض عليه اتنين تاني ولا تفتح خيارات جديدة — هو بيضيّق مش بيبدأ من الأول.\n"
        )
    if dropped:
        # `dropped` maps name -> computed reason, or None when the cause cannot be named. It
        # used to be a bare list, with this note suggesting "(السعر مثلاً)" as the reason to
        # give — and the model asserted price for a perfume dropped on *gender*, with no budget
        # anywhere in the conversation. Offering an example invited a guess, and a guess about
        # why something was withdrawn is a trust failure, not a wording problem.
        reasons = dropped if isinstance(dropped, dict) else {name: None for name in dropped}
        lines = "\n".join(
            f"- {name}: {reason}" if reason else f"- {name}"
            for name, reason in sorted(reasons.items())
        )
        parts.append(
            "\n⚠️ العطور دي كانت في الكلام وخرجت من شروطه دلوقتي:\n"
            + lines
            + "\n- 🔴 قول للعميل إنها خرجت، بالسبب المكتوب جنبها بالظبط، بدل ما تشيلها في "
            "السكوت. ❌ ممنوع تخترع سبب تاني — ولو مفيش سبب مكتوب، قول إنها مش مناسبة "
            "لطلبه وبس.\n"
        )
    return "".join(parts)
