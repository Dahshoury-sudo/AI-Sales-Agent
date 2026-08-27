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


def under_discussion(conversation, store, turns=2):
    """Perfumes we actually put in front of the customer in our last `turns` replies.

    Narrower than `already_described`, and answering a different question. That one asks "has
    the customer heard this before"; this asks "is this what we are talking about right now".

    The failure it exists for (conversation 997): four different pairs of perfumes across
    seven turns, eight in total, with the customer never once rejecting anything — they were
    answering questions and adding constraints. On the last turn they said "معايا 800" and
    Le Male, whose 50ml is 623 and which they had been converging on for two turns, was
    dropped for a perfume they had never seen.

    A perfume counts only if it appears in the reply text **and** in that reply's saved
    `internal_context` — the product data the model was actually given. Requiring both matters
    in each direction:

      * the context alone lists up to twelve products, most of which were never mentioned to
        the customer, so it is not what the conversation is "on";
      * the text alone includes perfumes named while being *withdrawn*, and that created a
        feedback loop (conversation 1012). Announcing "Ambero خرج من الاختيارات" put Ambero in
        the reply, so the next turn found it under discussion again, found it still failing the
        customer's constraints, and announced the same withdrawal a second time. A dropped
        perfume is absent from the injected data by definition, so the intersection excludes it
        and the withdrawal is announced exactly once.

    Two replies is the window: it covers the common shape of a recommendation followed by a
    price question about the same perfumes, without pinning the conversation to perfumes the
    customer has moved past.
    """
    if conversation is None or store is None:
        return frozenset()

    from products.models import Product

    recent = list(
        conversation.messages.filter(role="assistant")
        .order_by("-created_at")
        .values_list("content", "internal_context")[:turns]
    )
    if not recent:
        return frozenset()

    names = list(Product.objects.filter(store=store).values_list("name", flat=True))

    def shown_in(content, context):
        prose, data = (content or "").lower(), (context or "").lower()
        return {n for n in names if n and n.lower() in prose and n.lower() in data}

    def withdrawn_in(content, context):
        """Named to the customer with no data behind it — i.e. we said it is gone."""
        prose, data = (content or "").lower(), (context or "").lower()
        return {n for n in names if n and n.lower() in prose and n.lower() not in data}

    found = set()
    for content, context in recent:
        found |= shown_in(content, context)

    # A withdrawal announced in our most recent reply settles the matter. Without this the
    # perfume stayed "under discussion" from the earlier reply that recommended it — still
    # inside the two-reply window — so the same withdrawal was announced on the next turn too.
    # Conversation 1012 told the customer Ambero was out twice over.
    latest_content, latest_context = recent[0]
    return frozenset(found - withdrawn_in(latest_content, latest_context))


def offered_in_order(conversation, store, turns=2):
    """The perfumes under discussion, in the order we named them in our latest reply.

    `under_discussion` answers "which perfumes are we on", and a set is the right shape for
    that. Resolving a *reference* needs the sequence as well: "اول واحد" points at a position,
    and a bare "ده" points at the one we led with.

    The failure this exists for (evaluation scenario F1): the bot recommended two perfumes,
    the customer replied "تمام هاخد ده، وضيف كمان واحد للهدية", and the order extractor
    answered "مش واضحلي عايز تطلب أنهي عطر" — twice, byte for byte, because the next message
    ("خليه 90 ملي بدل الـ50") named no perfume either. The extractor is told that an ordinal
    means "the perfume you named in your previous reply" but was never handed that list, and
    is separately told not to pull products out of the history when the cart is empty. Both
    rules were obeyed and the customer was stuck.

    Ordering is by first appearance in the reply prose, which is the order the customer read
    them in. Soundness is inherited from `under_discussion`: a perfume must appear in both the
    prose and that reply's injected data, so a withdrawn perfume is never offered as a referent.

    The scan matches the LONGEST name at each position rather than asking each name where it
    occurs. Catalogue names nest — "Stronger With You" is a prefix of "Stronger With You
    Intensely" — so a per-name `find` returns the same index for both and the shorter, more
    generic name wins the tie. That would point "ده" at the wrong perfume, which is the whole
    thing this function exists to get right.
    """
    names = under_discussion(conversation, store, turns=turns)
    if not names:
        return []

    latest = (
        conversation.messages.filter(role="assistant")
        .order_by("-created_at")
        .values_list("content", flat=True)
        .first()
    )
    prose = (latest or "").lower()

    longest_first = sorted(names, key=len, reverse=True)
    ordered, remaining = [], set(names)
    position = 0
    while position < len(prose) and remaining:
        for name in longest_first:
            if name in remaining and prose.startswith(name.lower(), position):
                ordered.append(name)
                remaining.discard(name)
                position += len(name)
                break
        else:
            position += 1

    # Whatever is under discussion from the older reply but absent from the latest one sorts
    # after everything we just said — the right precedence for resolving a reference.
    return ordered + sorted(remaining)


def offered_context_block(conversation, store):
    """The perfumes we just offered, rendered as an ordered prompt block.

    Lives here rather than in a caller because two branches need the same anchor. The order
    extractor uses it to resolve "ده" / "اول واحد" (evaluation scenario F1), and
    `product_resolver` uses it because its own prompt has only one weak sentence about
    pronouns and nothing structured to point at — which is how "مش متوفر متأكد ؟" about
    Versace Eros resolved to two perfumes from two turns earlier (conversation 1099).

    Derived from `Message.internal_context` via `offered_in_order`, not scraped from prose, so
    the anti-hallucination rules that surround both call sites are not weakened: every name
    here is one we demonstrably had real data for when we said it.

    Returns "" when nothing is offered, so a caller can concatenate it unconditionally.
    """
    try:
        offered = offered_in_order(conversation, store)
    except Exception:
        offered = []
    if not offered:
        return ""

    lines = "\n".join(f"{index}. {name}" for index, name in enumerate(offered, start=1))
    return (
        "\n═══ PERFUMES YOU JUST OFFERED (in the order you named them) ═══\n"
        f"{lines}\n"
        "Resolve \"ده\" / \"اول واحد\" / \"التاني\" against this list. Entry 1 is what you led with.\n"
    )


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
        # The exclusivity clause is load-bearing. Handed "Ambero dropped" alongside a keeping
        # list, the model announced *Vanilo* as dropped instead — a perfume that was on the
        # keeping list, in budget and available. Telling a customer an available perfume is
        # gone is worse than saying nothing, so the two lists have to be sealed against each
        # other by name.
        kept_names = "، ".join(sorted(keeping)) if keeping else "—"
        parts.append(
            "\n⚠️ العطور دي بس هي اللي خرجت من شروطه دلوقتي:\n"
            + lines
            + "\n- 🔴 قول للعميل إنها خرجت، بالسبب المكتوب جنبها بالظبط، بدل ما تشيلها في "
            "السكوت. ❌ ممنوع تخترع سبب تاني — ولو مفيش سبب مكتوب، قول إنها مش مناسبة "
            "لطلبه وبس.\n"
            f"- 🔴 ❌ ممنوع تقول عن أي عطر تاني إنه خرج أو مش متاح. العطور دي لسه متاحة "
            f"ومناسبة: {kept_names}.\n"
        )
    return "".join(parts)
