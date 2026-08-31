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

import re

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

    Matched on the stored name, which is reliable here because names are stored and emitted in
    Latin: "Dior Homme Intense", "Le Male", "Y Eau de Parfum" and "XJ 1861 Naxos" all appear
    verbatim in the transcript this was written for. A name the bot rendered in Arabic
    transliteration is missed, and that turn behaves as it does today.

    Matched via `naming.names_in` rather than a plain substring, because catalogue names nest:
    a reply naming only "Stronger With You Intensely" used to report the base "Stronger With
    You" as described too, so `repeat_ban_hint` forbade re-describing a perfume the customer
    had never heard of.

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

    from .naming import names_in

    names = Product.objects.filter(store=store).values_list("name", flat=True)
    return frozenset(names_in(said, names))


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


# The order flow writes its own `internal_context`: one line per cart item, formatted at
# order_service.py:732 as "Afnan 9PM (50 ملي) (زجاجة البراند) x 1 (508.00 EGP)" and joined with
# commas, or the "No products found" sentinel. It records what is being *bought*, not the
# product data a reply was handed.
_CART_LINE = re.compile(r"\bx\s*\d+\s*\(\s*[\d.]+\s*EGP\s*\)")


def _is_cart_context(context):
    """Was this reply's `internal_context` written by the order flow rather than injected?

    Detected by the cart's own shape rather than by the absence of a product-data label,
    because that label is not stable: `format_products` emits "Name (الاسم الصحيح):" while
    older rows and some fixtures carry a bare "Name:". Matching the cart positively means
    only contexts that provably came from the order flow take the branch below, and it works
    on rows already in the database.
    """
    text = (context or "").strip()
    return bool(text) and (text == "No products found" or bool(_CART_LINE.search(text)))


# "I will check and come back to you" is not "we do not have it". prompts.py rules 2 and 3
# dictate these phrasings whenever the bot does not know something — rule 3 specifically for a
# perfume the customer named that is missing from the injected data — so they are both common
# and correct. Read as withdrawals they drop the perfume out of the very next turn's referent,
# which is the loop that let conversation 726 repeat its false denial four turns running: a
# customer waiting to hear back about a perfume is maximally on that perfume.
_DEFERRAL = (
    "هسأل وأرد",
    "هسأل و أرد",
    "أتأكدلك",
    "أتأكد لك",
    "هشوفه لك",
    "هشوفهولك",
)

# Clause boundaries, so a reply that withdraws one perfume and defers on another is read
# correctly on both counts. Same split, for the same reason, as eval_harness/checks.py.
_CLAUSE = re.compile(r"[.،,؛;!?؟\n]+")


def _deferred_in(content, names):
    """Catalogue names this reply promised to check on, rather than declared gone."""
    from .naming import names_in

    deferred = set()
    for clause in _CLAUSE.split(content or ""):
        normalized = normalize_arabic(clause)
        if not any(normalize_arabic(marker) in normalized for marker in _DEFERRAL):
            continue
        deferred |= set(names_in(clause, names))
    return deferred


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

    That rule assumes `internal_context` holds injected product data, and on an ORDER turn it
    does not — it holds a cart (`_is_cart_context`). There the prose alone decides, because the
    order flow names a perfume only after resolving it against the catalogue and clearing every
    stock branch, and it deliberately omits one it is still waiting on a bottle type for
    (order_service.py:621-624). Reading that omission as "no data behind it" inverted the
    meaning of the most confident evidence in the system: conversation 726 treated the question
    "نوع الزجاجة ... من عطر Stronger With You" as a withdrawal of both perfumes it asked about,
    left a stale cart line as the only thing under discussion, and told a customer four times
    that perfumes sitting in stock in both bottle types were not in its data.

    A *deferral* is not a withdrawal either (`_deferred_in`). "لحظة أتأكدلك منه" is what rule 3
    of the persona asks for when a named perfume is missing from the injected data, so it is
    correct output — but with no data behind the name it looked identical to a withdrawal, and
    the perfume the customer was waiting to hear about dropped out of the next turn.

    Both halves of the test go through `naming.names_in`, not a substring, because catalogue
    names nest. "Stronger With You" is a prefix of "Stronger With You Intensely", so a reply
    naming only Intensely satisfied *both* halves for the base as well — the base's own row was
    sitting in the same injected context — and the base was recorded as under discussion
    without ever having been said. `ranking.WEIGHTS["continuity"]` (2.5) then promoted that
    phantom into the next answer, which is how conversation 768 quoted a customer two prices
    for what they read as one perfume, then apologised and retracted the correct one.

    Two replies is the window: it covers the common shape of a recommendation followed by a
    price question about the same perfumes, without pinning the conversation to perfumes the
    customer has moved past.
    """
    if conversation is None or store is None:
        return frozenset()

    from products.models import Product

    from .naming import names_in

    recent = list(
        conversation.messages.filter(role="assistant")
        .order_by("-created_at")
        .values_list("content", "internal_context")[:turns]
    )
    if not recent:
        return frozenset()

    names = list(Product.objects.filter(store=store).values_list("name", flat=True))

    def shown_in(content, context):
        said = set(names_in(content, names))
        # On an order turn the prose is the record and the cart is not — see the docstring.
        if _is_cart_context(context):
            return said
        return said & set(names_in(context, names))

    def withdrawn_in(content, context):
        """Named to the customer with no data behind it — i.e. we said it is gone."""
        # Nothing is withdrawn on an order turn: a perfume we are asking a question about is
        # the opposite of one we have dropped.
        if _is_cart_context(context):
            return set()
        gone = set(names_in(content, names)) - set(names_in(context, names))
        # Nor is one we promised to go and check on.
        return gone - _deferred_in(content, names)

    found = set()
    for content, context in recent:
        # A perfume we promised to check on has no data behind it by definition, so `shown_in`
        # cannot see it — yet it is precisely what the customer is waiting to hear about.
        found |= shown_in(content, context) | _deferred_in(content, names)

    # A withdrawal announced in our most recent reply settles the matter. Without this the
    # perfume stayed "under discussion" from the earlier reply that recommended it — still
    # inside the two-reply window — so the same withdrawal was announced on the next turn too.
    # Conversation 1012 told the customer Ambero was out twice over.
    latest_content, latest_context = recent[0]
    return frozenset(found - withdrawn_in(latest_content, latest_context))


def offered_in_order(conversation, store, turns=2, latest_only=False):
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

    Ordered by `naming.names_in`, which matches the LONGEST name at each position rather than
    asking each name where it occurs. Catalogue names nest — "Stronger With You" is a prefix of
    "Stronger With You Intensely" — so a per-name `find` returns the same index for both and the
    shorter, more generic name wins the tie. That would point "ده" at the wrong perfume, which is
    the whole thing this function exists to get right. The scan lived inline here; it is shared
    now because `under_discussion` needed the same rule and had a plain substring instead.

    `latest_only` drops the older-reply tail, so the answer is strictly "what we named in the
    reply the customer is responding to". `product_info` needs that: the tail is what carried a
    stale cart-resident Afnan 9PM into conversation 726's referent while the customer was
    asking about the two perfumes the previous reply had just named. It falls back to the full
    list when the latest reply named nothing at all, which is the case a withdrawal-only or
    FAQ reply produces and where the older turn is genuinely the best anchor available.
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
    from .naming import names_in

    ordered = names_in(latest, names)
    remaining = set(names) - set(ordered)

    # Whatever is under discussion from the older reply but absent from the latest one sorts
    # after everything we just said — the right precedence for resolving a reference.
    if latest_only and ordered:
        return ordered
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
