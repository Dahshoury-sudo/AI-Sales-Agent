from .product_resolver import resolve_products
from .product_formatting import format_products
from .ai.client import chat
from .ai.prompts import get_system_prompt
from .fallback import suggest_alternatives
from .static_faq_service import normalize_arabic
from .sales import described as sales_described


def _named_in_message(message, store):
    """Catalogue perfumes the customer named, matched deterministically.

    A fallback for `resolve_products`, which is an LLM call and can come back empty on a
    message that plainly names a perfume. When it does, the caller falls through to the
    "no product recognised" branch, and the model — told that a product absent from its data
    does not exist — reports the perfume as unavailable.

    That is what happened to Versace Eros in conversation 1099: the customer asked
    "ليه مرشحتش versace eros", the reply said "مش متوفر عندنا حالياً", and Eros was in the
    catalogue at 1019 جنيه the whole time. `naming.mentioned_in` resolves it in one pass with
    no model call, so the data is simply there and the question cannot arise.

    Latin names only, which is `naming.tokens`' existing limitation — an Arabic
    transliteration still depends on the LLM resolver.
    """
    if not message or store is None:
        return []

    from products.models import Product

    from .sales import naming

    candidates = list(
        Product.objects.filter(store=store, is_active=True)
        .prefetch_related("variants")
        .select_related("brand")
    )
    return naming.mentioned_in(message, candidates)


def _referent_from_conversation(message, store, conversation):
    """The perfumes we just offered, when the customer's message names none itself.

    Every `product_info` message of that shape is a question *about the perfumes under
    discussion* — "بكام؟", "ريحته عاملة ايه؟", "فيه أحجام تانية؟", "متأكد؟" — so what we just
    named is the subject, and the LLM resolver has nothing to anchor on. In conversation
    1099 it anchored on the wrong thing: "مش متوفر متأكد ؟" about Versace Eros resolved to
    Dior Sauvage and Lattafa Asad from two turns earlier, and the reply was only correct
    because the model happened to read Eros's prices out of the history rather than out of the
    injected data.

    Resolved from `Message.internal_context` rather than prose, so a perfume named while being
    withdrawn is never the referent.

    Returns **every** perfume the latest reply named, not just the one it led with. Taking
    `offered[0]` alone answered a plural question with one row — conversation 726's
    "كل واحده كام سعرها" was about two perfumes — and, worse, it forced a guess when the
    customer went on to name one of them in Arabic, which `_named_in_message` cannot match.
    Handing over both rows lets the model answer about whichever was named, and instruction 7
    below already forbids volunteering the other one's detail. `latest_only` keeps the set to
    the reply actually being responded to, so a stale cart line cannot ride along.

    Returns [] unless a conversation is present and something is genuinely under discussion —
    that gate is what keeps this off the first turn of a conversation and out of the callers
    that pass no conversation at all.

    Whether the message names a perfume itself is decided by the caller via
    `naming.may_name_a_perfume`, NOT by `_named_in_message` coming back empty. That conflation is
    what made this function answer conversation 738's "طب اكوا دي جيو ؟" with the previous turn's
    perfume: an Arabic-written name matches `_named_in_message` never, so a message that named a
    perfume outright looked identical to one that named nothing.
    """
    if conversation is None or store is None:
        return []

    from .sales import described as sales_described

    try:
        offered = sales_described.offered_in_order(
            conversation, store, latest_only=True
        )
    except Exception:
        return []
    if not offered:
        return []

    from products.models import Product

    # Ordered by what we said, which `name__in` does not preserve on its own.
    by_name = {
        product.name: product
        for product in Product.objects.filter(
            store=store, is_active=True, name__in=offered
        )
        .prefetch_related("variants")
        .select_related("brand")
    }
    return [by_name[name] for name in offered if name in by_name]


# Vocabulary that makes a message a question about whether we *stock* something, and vocabulary
# that makes it a question about price or size. A message carrying the first and none of the second
# is asking one thing only, and answering it with a price list is answering something else.
_AVAILABILITY_WORDS = ("متوفر", "متوفرة", "موجود", "موجودة", "عندكو", "عندكم", "عندك", "بتوفروا")
_PRICE_OR_SIZE_WORDS = (
    "بكام", "كام", "سعر", "اسعار", "ثمن", "غالي", "رخيص", "ملي", "مل ", "حجم", "احجام", "اوفر",
)


def _availability_only_hint(message):
    """One line saying this message asked about availability and nothing else.

    Instruction 1 below already forbids both halves of what conversation 795 turn 2 did — the dead
    "أه متوفر عندنا" that stops the conversation, and the price dump nobody asked for — and the
    reply did both anyway, four sizes deep. A rule the model reads past is worth restating as a
    fact about *this* message, which is the one thing a static prompt cannot know.

    Derived rather than guessed, and deliberately not `show_prices=False`: hiding the prices on the
    strength of a keyword match means a customer who did want them gets nothing, and the model
    then has no data to fall back on. A labelled hint it can weigh is the safer shape.

    Returns "" for every other message, so the caller concatenates it unconditionally.
    """
    normalized = normalize_arabic(message or "")
    if not normalized:
        return ""
    if not any(word in normalized for word in _AVAILABILITY_WORDS):
        return ""
    if any(word in normalized for word in _PRICE_OR_SIZE_WORDS):
        return ""
    return (
        "\n🔴 ملاحظة على الرسالة دي: العميل سأل عن التوفر بس — مفيش في كلامه أي سؤال عن سعر "
        "ولا حجم. أكّد التوفر في جملة واحدة وكمّل بسؤال يضيّق. ❌ ممنوع تسرد أسعار أو أحجام "
        "في الرد ده.\n"
    )


# Marks the turn where a deferral has been chased and there is still nothing to say. Read by the
# prompt (`_ABSENT_RULES`), by `prompts.py` red line 3, and by `eval_harness.checks` — all three
# forbid denying availability, and all three have to make the same single exception.
LOOKUP_EXHAUSTED_MARKER = "LOOKUP_EXHAUSTED"


def _chasing_open_lookup(products, conversation, store):
    """True when nothing in `products` came from this message — it is all perfume we already named.

    The provenance test behind the `chasing` gate below. It cannot be `not products`: on a chase
    turn `products` is full. "ها لقيت اي ؟" and "اتأكدلي منه" name no perfume, but they clear
    `naming.may_name_a_perfume`, so `resolve_products` runs and answers the pronoun with the
    perfume the deferral reply had volunteered alongside its promise. Non-empty `products` then
    reads as a resolved name, `named_but_unresolved` goes False, and the pending block, the
    ⚠️ header and the deferral rules all disappear on the one turn that most needs them.

    Conversations 798 and 799 are each that turn. 798 turn 6 answered "ها لقيت اي ؟" with
    "لقيت Dior Homme Sport متوفر عندنا" — a completed lookup it never ran, on a perfume the
    customer had not asked about — and 799 turn 4 answered "اتأكدلي منه" with Stronger With
    You's full price list. 798 turn 8 is the control: there the customer re-typed the name, so
    the guards fired the ordinary way and the reply was correct.

    Everything offered is the signature of a resolved pronoun. A customer who names something new
    that we do carry brings back a perfume we have *not* offered, and that drops the carry — as
    does a deterministic match on this message, which the caller checks separately.
    """
    if not products:
        return True
    try:
        offered = set(sales_described.offered_in_order(conversation, store))
    except Exception:
        # Never lose a reply over the carry: without provenance, fall back to today's behaviour.
        return False
    if not offered:
        return False
    return all(getattr(product, "name", None) in offered for product in products)


def _pending_lookup_block(question, exhausted=False):
    """Record, inside the turn's own context, a question the catalogue could not answer.

    The customer named a perfume, `naming.may_name_a_perfume` agreed it was a name, and neither
    the deterministic matcher nor the resolver could place it. The honest reply to that is
    "لحظة أتأكدلك منه" — and until now the pipeline forgot it had said so the instant the reply
    was sent, because `described._deferred_in` tracks deferrals by *catalogue* name and a deferral
    is by definition about a name the catalogue does not have.

    Conversation 795 is that amnesia end to end. Turn 1 deferred on "لادور بخور"; turn 2's
    "طب اتأكدلي" found no record of it, fell through to `_referent_from_conversation`, and was
    answered with the previous turn's perfume; turn 3 asked the same question again and got the
    same wrong perfume, this time with an invented بخور note attached to make it fit.

    The customer's **raw message** is stored, not a name extracted from it. Extracting one means
    guessing which words were the perfume, and a guess written into the record is a fabrication
    the next turn will treat as fact. The wording is already in the history; what was missing is
    the flag that it is still open.

    `described.pending_lookup` reads this back, and `router` escalates on the count. The caller
    passes `question` rather than always the current message, because on a chase turn the
    question is the *earlier* message — "اتأكدلي منه" names nothing to look up.

    `exhausted` flips the instruction from "promise to check" to "say we do not have it". That
    reads like a contradiction of the paragraph above and is not: the two describe different
    turns. Deferring is right the first time, when the only fact in hand is that the catalogue
    came back empty. It is wrong the second time, because nothing has happened in between — no
    lookup runs between two turns of a chat — so repeating the promise is making it again without
    having kept it, and it leaves the customer waiting for an answer that is never coming. The
    denial is the only reply on that turn that lets them act.
    """
    block = (
        "═══ سؤال معلّق ═══\n"
        f"{sales_described.PENDING_LOOKUP_MARKER} {(question or '').strip()}\n"
    )
    if not exhausted:
        return block + (
            "العميل سمّى عطر مش موجود في البيانات المتاحة، والسؤال ده لسه مجاوبش عليه.\n"
            "🔴 ممنوع تقول إنه مش متوفر عندنا. النظام مالقاهوش، ودي حاجة تانية خالص عن إنه مش "
            "في المتجر — إحنا مش عارفين. الرد الصح: \"لحظة أتأكدلك منه\".\n\n"
        )
    return block + (
        f"{LOOKUP_EXHAUSTED_MARKER}\n"
        "العميل سأل عن العطر ده قبل كده، ووعدناه إننا نتأكد، وأهو رجع يسأل تاني — ولسه مفيش "
        "أي إجابة. الرد ده آخر رد هيوصله من عندك قبل ما زميل بشري يتسلم المحادثة.\n\n"
    )


# What the injected rows are, on a turn where the customer named something else. Without this the
# model reads a block headed "بيانات المنتجات الحقيقية" and reasonably concludes the perfume in it
# is the perfume being asked about — which is how conversation 795 answered "عندكو لادور بخور ؟"
# with Stronger With You's price list twice, and the second time grew it a بخور note to match.
_NOT_THE_PERFUME_ASKED_ABOUT = (
    "⚠️ العطور اللي تحت دي اللي كنا بنتكلم عنها في المحادثة — **مش** العطر اللي العميل سأل "
    "عنه في الرسالة دي. ممنوع ترد كأن العطر اللي سأل عنه هو واحد منهم، وممنوع تنسبله أي "
    "نوتة أو ريحة أو سعر منهم.\n"
)


# Appended to the found-branch instructions when the rows in context are the *referent* and the
# customer named something else. Rule 1 up there already says most of this in the abstract;
# conversation 795 turns 2 and 3 are what it costs when nothing in the data marks which perfume is
# which. Numbered 14 to continue that list rather than restart it.
_DEFERRAL_RULES = """14. 🔴🔴 العميل سمّى عطر مش موجود في البيانات اللي فوق (شوف قسم "سؤال معلّق" في أول الرسالة).
   • الرد الصح على العطر اللي سأل عنه: "لحظة أتأكدلك منه" — وبس.
   • ❌ ممنوع تقول إنه مش متوفر عندنا أو مش موجود، وممنوع تعتذر عن عدم توفره. النظام هو اللي مالقاهوش، ودي حاجة تانية خالص.
   • ❌ وممنوع تجمع النفي مع الوعد بالتأكد في رد واحد ("مش موجود عندنا، لحظة أتأكدلك منه") — الجملة دي بتنقض نفسها.
   • ❌ ممنوع تسرد أسعار أو مواصفات العطور اللي فوق كأنها ردك على سؤاله. ولو العميل بيستعجلك على التأكد (زي "طب اتأكدلي") فهو مستني رد على العطر اللي **هو** سأل عنه — مش على عطر تاني: قوله إنك لسه بتتأكد وإنك هترد عليه، مش أسعار عطر مسألش عنه.
   • ✅ لو حابب تعرض عليه حاجة وهو مستني، قوله بوضوح إنها عطر **تاني** بالاسم الكامل.
"""


# Replaces `_DEFERRAL_RULES` — and overrides the not-found branch's case (أ) — on the turn where
# the customer has come back for a deferral we never delivered. Deliberately unnumbered so it
# reads correctly appended to either branch's list, and deliberately stated as outranking them,
# because both of those lists say "اختار التأكد دايماً" and on this one turn that is wrong.
#
# Nothing has happened between the two turns: no lookup runs between two messages of a chat, so
# the second "لحظة أتأكدلك" is the same promise made again by someone who did not keep it the
# first time, to a customer who is waiting for an answer that is not coming. Saying plainly that
# we do not carry it is the only reply that leaves them able to act — which is what the store
# owner asked for after reading 798 — and the alternatives that follow are what they can act on.
#
# `router._escalate_pending_lookup` notifies the owner on this turn and leaves the bot serving, so
# the customer can take up one of those alternatives; a second denial about the same perfume is
# where it hands over instead.
_ABSENT_RULES = """🔴🔴🔴 قاعدة فوق كل القواعد اللي فوق — العميل رجع يسأل تاني (شوف LOOKUP_EXHAUSTED في قسم "سؤال معلّق"):
   • ✅ الرد الصح: قوله بوضوح، وباعتذار قصير، إن العطر اللي سأل عنه مش موجود عندنا. جملة واحدة.
   • ✅ سمّي العطر بنفس الحروف اللي العميل كتبها بيها — لو كتبه بالعربي، ردّه بالعربي زي ما هو. ❌ ممنوع تترجمه أو تكتبه بحروف لاتينية من عندك ("L'Adour")، دي هجاء بتخترعه لعطر إحنا أصلاً بنقول إننا مش عارفينه.
   • ❌ ممنوع توعده تتأكد تاني، وممنوع تقول "لحظة أتأكدلك" ولا "هسأل وأرد عليك" ولا "هشوفه لك" — الوعد ده اتقال قبل كده ومحصلش، وتكراره بيسيب العميل مستني حاجة مش جايه.
   • ❌ وممنوع تجمع النفي مع وعد بالتأكد في رد واحد ("مش موجود عندنا، لحظة أتأكدلك منه") — الجملة دي بتنقض نفسها.
   • ❌ ممنوع تقول أي كلام عن نظام أو بيانات أو كتالوج أو "مش ظاهر عندي" — العميل مش المفروض يعرف إن في حاجة زي دي أصلاً.
   • ✅ بعد النفي، اعرض عليه بديل أو اتنين من العطور اللي في البيانات، وقول بوضوح إنها عطور **تانية** بالاسم الكامل.
   • ❌ ممنوع تسرد أسعار أو مواصفات عطر تاني كأنها إجابة على العطر اللي هو سأل عنه.
"""


def get_product_info(message, history=None, store=None, conversation=None):
    from .sales import naming

    # An explicit name in this message beats anything inferred from earlier turns.
    products = _named_in_message(message, store)
    # Kept because the `chasing` gate below needs to know the name came from *this* message: a
    # deterministic match is never a resolved pronoun, so it always ends a chase.
    named_here = bool(products)

    # `_named_in_message` is Latin-only, so its empty result does NOT mean the customer named
    # nothing — an Arabic transliteration matches it never. Treating the two as the same thing is
    # what broke conversation 738: "طب اكوا دي جيو ؟" fell straight through to the referent branch
    # below and was answered with Y Eau de Parfum, the previous turn's recommendation, while the
    # one resolver that can read Arabic was skipped entirely. So ask whether the message could be
    # naming a perfume at all, and if it could, resolve it before reaching for the referent.
    #
    # The gate is liberal by design: a false alarm costs this one call, which comes back empty and
    # falls through to exactly the referent it would have used anyway.
    resolver_ran = False
    if not products and naming.may_name_a_perfume(message):
        products = resolve_products(message, history, store, conversation)
        resolver_ran = True

    # A name we could not place. Kept as a fact about this turn rather than inferred later from an
    # empty `products`, because the referent lookup below is about to fill that list with a
    # different perfume entirely — which is exactly the confusion conversation 795 was built on.
    named_but_unresolved = resolver_ran and not products

    # Nothing named here: the subject is whatever we just offered. Decided in Python because
    # the resolver demonstrably gets this wrong, and `internal_context` is a harder record
    # than an 8-message prose window. This is also the recovery path when the gate fired but the
    # resolver could not place the name, which is what keeps a false alarm above harmless.
    if not products:
        products = _referent_from_conversation(message, store, conversation)

    # Each branch is exclusive on purpose. Unioning the referent with the resolver's output
    # was tried and reverted: it put the resolver's wrong guess back into the context and
    # undid the whole point of resolving the referent in Python. A partially-resolved
    # multi-perfume question (three Arabic transliterations in, two out) is fixed in the
    # resolver's own prompt instead, since `naming` cannot match Arabic at all.
    #
    # `resolver_ran` keeps this to at most one resolver call per turn: a message that tripped the
    # gate and came back empty has already had its chance, and asking twice would only spend a
    # second call on the same answer.
    if not products and not resolver_ran:
        products = resolve_products(message, history, store, conversation)

    # A deferral we already made and still owe. Read from the persisted record, because on this
    # turn the customer is chasing it rather than re-naming it, and `named_but_unresolved` above is
    # a fact about the current message alone — which is why all three guards below used to vanish
    # on exactly the turn the customer came back to collect. See `_chasing_open_lookup`.
    #
    # Two windows would be one too many. The carry looks at the previous reply *only*: chasing
    # means the promise is the last thing we said, and a wider window would let a customer who
    # moved on to a perfume we do stock collect a denial about the old question two turns later.
    # `router` keeps reading the full window for its own escalation count.
    pending_question, _ = sales_described.pending_lookup(conversation, turns=1)
    chasing = (
        bool(pending_question)
        # The message has to actually be collecting the promise. Resolving to something already
        # offered is not enough on its own: "بكام؟" right after a deferral does that too, and it
        # is a price question about the perfume we offered alongside the promise — answering it
        # with "مش موجود عندنا" would deny a perfume the customer never named and drop the
        # question they did ask. `naming` owns that vocabulary; see `chasing_a_promise`.
        and naming.chasing_a_promise(message)
        # Still vetoed by an unplaceable name in *this* message, which is a new question rather
        # than the old one — "اتأكدلي من الكساندريا 2" both chases and names, and the name is the
        # part that has not been deferred on yet.
        and not named_but_unresolved
        and not named_here
        and _chasing_open_lookup(products, conversation, store)
    )

    # Asked once, promised once, asked again — about the *same* question. Two shapes reach this,
    # and they need different windows because they carry different evidence.
    #
    # `chasing` is the pronoun shape ("اتأكدلي منه", "ها لقيت اي ؟"). It carries no name at all, so
    # the only thing tying it to the open question is adjacency, and `turns=1` above is what
    # supplies that: a chase implies exactly the `pending_before >= 1` that makes
    # `_escalate_pending_lookup` act, and this reply the one that has to answer. See `_ABSENT_RULES`.
    #
    # `re_asked` is the re-typed shape ("بتكلم علي الكساندريا 2؟", "بسأل علي لادور بخور"), and it is
    # the more natural way to insist. `chasing` cannot see it: re-typing an unplaceable name sets
    # `named_but_unresolved`, which vetoes the carry above. That veto is right and stays — a
    # raw-message record cannot tell one unplaceable name from another, so a brand-new name must not
    # inherit an older one's exhaustion, and 795 turn 4 asks about الكساندريا 2 while لادور بخور is
    # still open. What was missing is the thing that tells the two apart, which is `naming.re_asks`
    # comparing the customer's own words; with that in hand the veto can stand and this sits beside
    # it rather than loosening it.
    #
    # Conversations 816 and 817 are each the turn this exists for. 816: "عندك الكساندريا 2؟" was
    # deferred, then "بتكلم علي الكساندريا 2؟" was deferred *again* — and `router` set `needs_human`
    # on that same turn, so "لحظة أتأكدلك منه" was the last thing the customer ever heard, with the
    # denial and the alternatives they were owed never sent. 817 is the same two turns with
    # لادور بخور. This branch used to be dismissed as unreachable in production on the grounds that
    # the chase before it had already handed off — but a chase whose vocabulary we do not recognise
    # never happens, and 816 turn 2 ("ماشي شوفو") is one of those.
    #
    # The window is deliberately wider than the chase's `turns=1`. A re-typed name is its own
    # anchor and needs no adjacency, and that is precisely what makes 816 turn 3 reachable: turn 2
    # was answered about Stronger With You and its reply carried no marker at all, so the narrow
    # window sees nothing and the open question would be lost.
    re_asked = ""
    if named_but_unresolved:
        re_asked = next(
            (
                question
                for question in sales_described.pending_questions(conversation)
                if naming.re_asks(message, question)
            ),
            "",
        )

    exhausted = chasing or bool(re_asked)

    if named_but_unresolved:
        # `re_asked` is the wording the question was first asked in, and recording that rather than
        # this message keeps the record stable — which is what lets a third ask match the same
        # question instead of starting a new one. Empty when this name is new here, and a name we
        # have not placed even once is a question we have not answered even once, so it gets the
        # promise rather than the denial.
        pending_block = _pending_lookup_block(re_asked or message, exhausted=bool(re_asked))
    elif chasing:
        pending_block = _pending_lookup_block(pending_question, exhausted=exhausted)
    else:
        pending_block = ""

    # One name for "this turn owes an answer we do not have", however we found that out.
    deferring = bool(pending_block)
    availability_hint = _availability_only_hint(message)

    if products:
        context = pending_block
        context += "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        if deferring:
            context += _NOT_THE_PERFUME_ASKED_ABOUT
        # Capped as a prompt-size safety net. The referent branch can now hand over every
        # perfume the last reply named, which is ~2 in practice and bounded by the two-reply
        # window — the limit only guards the pathological case.
        context += format_products(products, limit=6)
        instructions = """
═══ تعليمات صارمة ═══
1. 🔴 لما العطر اللي في البيانات يكون هو نفس العطر اللي العميل سأل عنه، اكتب اسمه بالإملاء الموجود في البيانات — حتى لو العميل غلط في الكتابة أو كتبه بالعربي.
   🔴🔴 لكن لو العميل سمّى عطر والبيانات فيها عطر **تاني خالص**: ده مش غلطة إملائية منه، ده عطر مختلف. ❌ ممنوع تقول إن العطر اللي سأل عنه "اسمه الصحيح" هو العطر اللي في البيانات، وممنوع توحي إنهم نفس العطر أو نفس الريحة أو نفس التركيبة. جاوب على العطر اللي هو سأل عنه، ولو مش معاك بياناته قول "لحظة أتأكدلك منه" زي ما الخط الأحمر رقم 3 بيقول. (عميل سأل عن Acqua di Gio واتقاله "اسمه الصحيح Y Eau de Parfum" — دول عطرين مختلفين من براندين مختلفين، والعميل كرر السؤال واتقاله نفس الكلام تاني.)
1. 🔴 فرّق بين نوع السؤال:
   • لو العميل بيسأل عن التوافر بس (زي "عندكم سوفاج؟" أو "فيه بلو دي شانيل؟" أو "موجود عندكم X؟") → أكّد إنه متوفر **وكمّل في نفس الرد بسؤال يضيّق** (في حجم معين حابب تعرف سعره؟). ❌ ممنوع ترد "أه متوفر عندنا" وتسكت — ده رد ميت وبيوقف المحادثة. ❗ ومتبدأش تسرد أسعار ولا ترشح حجم — هو مسألش عن السعر.
   • لو العميل سأل عن السعر أو الحجم صراحة (زي "بكام؟" أو "الأحجام إيه؟") → 🔴 ابدأ بالحجم اللي في سطر 💡 Value Pick (أو 💡 اقتراح حجم) ورشّحه بالأرقام اللي فيه، وبعدها اذكر باقي الأحجام في نص جملة عشان يعرف إن فيه خيارات. ❌ ممنوع تسرد الأسعار كلها في صف واحد زي فاتورة، وممنوع تغير أي سعر أو تخفي إن فيه أحجام تانية. ولو السطر ده هو "اقتراح حجم"، اذكر الأرقام من غير ما تقول "أحسن قيمة".
   • لو العميل سأل سؤال تفصيلي (زي "إيه مكوناته؟" أو "ثباته إيه؟") → جاوب على اللي سأله بس في جملة طبيعية واحدة أو اتنين. مثال: "ثباته حوالي 8-10 ساعات، وفوحانه قوي خصوصاً أول كام ساعة."
2. لو العميل سأل عن الحجم أو الملي، اذكر كل الأحجام المتاحة كما هي مكتوبة بالظبط.
3. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة) في كلام طبيعي.
4. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
5. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌) أو حجم معين نفد، أخبر العميل بذلك بشكل لطيف واقترح عليه إنه يسأل عن عطور تانية متوفرة أو اعرض عليه الأحجام المتوفرة إن وجدت.
6. 🔴🔴 ادمج المعلومات في كلام طبيعي. ❌ ممنوع تسرد المواصفات في شكل قائمة جامدة (الثبات: ... / الفوحان: ... / الموسم: ...). العميل بيتكلم مع بياع مش قاعدة بيانات.
7. 🔴 متحشرش معلومات العميل مسألش عنها. جاوب على اللي اتسأل بس.
8. 🔴 ممنوع أسئلة فاضية (زي "عايز حاجة تانية؟" أو "تحب تعرف الأسعار والأحجام المتاحة؟"). مسموح بـ CTA بيعي ذكي بس مش كل مرة (زي "تحب تطلب؟" أو "تنورنا في الستور تجرب؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
9. 🔴🔴 الزجاجة الأوريجينال: لو العميل سأل عن زجاجة أوريجينال، اقرأ خانة (Original Bottle) في بيانات المنتج وقول الرد المكتوب فيها بالحرف. ❌ ممنوع تقول على أي عطر إنه "حصري للمتجر" إلا لو مكتوب في الـ Brand بتاعه "عطر تركيب حصري خاص بالمتجر".
10. 🔴 الفرق بين زجاجة البراند والأوريجينال: البرفان (التركيبة) واحد بالظبط. الفرق الوحيد شكل الزجاجة. فرق السعر بسبب تكلفة الزجاجة مش جودة البرفان. ❌ ممنوع توحي إن الأوريجينال فيها تركيبة أحسن.
11. 🔴 لو العميل بيسأل يجيب 50 ملي ولا 90 ملي: ساعده يختار من نفس العطر. لو أول مرة → الأصغر أأمن. لو عاجبه → الأكبر أوفر. ❌ ممنوع تبدل العطر.
12. 🔴🔴 ممنوع تحسب إجمالي طلب. البيانات اللي فوق فيها أسعار الأحجام بس — مفيش فيها عربة ولا كميات ولا إجمالي. ❌ ممنوع تضرب سعر في كمية، وممنوع تجمع أسعار، وممنوع تقول "الإجمالي" أو "المجموع" أو تتكلم عن "الطلبين" أو أي عدد قطع. لو العميل سأل الطلب بقى بكام، قوله إنك هتراجع الطلب معاه وابدأ تجمع تفاصيله — الإجمالي بيتحسب من الطلب نفسه مش من الأسعار دي. (عميل اتقاله إجمالي 1560 جنيه لطلب مش موجود، وهو أصلاً قال إن ميزانيته 900.)
13. 🔴🔴 لو العميل قال إنك قلت سعرين مختلفين لنفس العطر (زي "انت قولت سعرين مختلفين للسترونجر") — بص على سطر `⚠️ عطر مختلف عن` الأول. لو السعرين بيرجعوا لعطرين مختلفين على نفس الخط، يبقى **السعرين صح**: ❌ ممنوع تعتذر، وممنوع تقول إن فيه لبس أو غلط، وممنوع تسحب سعر أو تقول إن واحد منهم "هو السعر الصحيح". وضّح إن ده عطر وده عطر تاني بالاسم الكامل، وقول سعر كل واحد لوحده. ولو مش متأكد هو بيقصد أنهي واحد، اسأله. (عميل اتقاله 780 لـ Stronger With You Intensely وبعدين 700 لـ Stronger With You — دول عطرين مختلفين — وسأل، فاتقاله "أعتذر على اللبس، 700 ده السعر الصحيح"، ومشي فاكر إن Intensely بـ 700.)
"""
        if deferring:
            instructions += _ABSENT_RULES if exhausted else _DEFERRAL_RULES
        instructions += availability_hint
    else:
        # Product not found, let's get some alternatives. Chosen deterministically and
        # with the customer's gender in mind: `order_by('?')` here offered a women's
        # perfume and a men's perfume side by side to someone whose gender was never
        # established, and made the reply impossible to reproduce.
        from .sales import gender as sales_gender
        from .sales import notes as sales_notes

        alternatives = suggest_alternatives(
            store,
            gender=sales_gender.resolve({}, message, history, store),
            # The accords the customer actually asked for. Without them this ranked on price
            # alone, so conversation 795's "عندكو لادور بخور صح ؟" was answered with the
            # cheapest perfume in the catalogue while Dior Homme Sport (olibanum) and Bleu de
            # Chanel (incense) sat in it unoffered.
            notes=sales_notes.terms_in(message),
        )

        context = pending_block
        context += "═══ تنبيه للنظام ═══\nلم يتم التعرف على اسم منتج محدد في رسالة العميل الأخيرة.\n\n"
        if alternatives:
            context += "═══ بدائل مقترحة متوفرة في المتجر ═══\n"
            # Not `brief=True` any more. Brief withholds the note and performance fields, and
            # conversation 795 turn 1 shows what that costs here: an alternative is a perfume the
            # customer has never heard of, quoted a price and pitched "بشكل جذاب" by rule 5 below,
            # and the model filled the missing scent data itself — "فيها لمسة بخور خفيفة" on a
            # perfume whose notes are cardamom, pineapple, cinnamon, vanilla, chestnut and
            # amberwood. Handing over the real notes is what lets the pitch be true, and it is
            # the whole point of ranking these by the accord that was asked for.
            #
            # The value pick still goes, for recommendation's reason: a turn about *which perfume*
            # must not open with a verdict about *which size*.
            context += format_products(alternatives, show_value_pick=False)

        instructions = """
═══ تعليمات ═══
1. اقرأ سجل المحادثة جيداً. لو كان العميل يستفسر عن منتج تم التحدث عنه بالفعل في المحادثة، أجب من سياق المحادثة وتجاهل قائمة البدائل تماماً.
2. ❌ إياك أن تقول أن المنتج "غير متوفر" إذا كان قد تم إخباره بأنه متوفر في الرسائل السابقة. النظام هنا لم يتعرف على اسم منتج جديد فقط.
3. 🔴🔴 قانون مهم جداً — فرّق بين أربع حالات مختلفة تماماً:
   • **(أ) العميل سمّى عطر معين باسمه بوضوح** (مثل "عندكو ديور هوم" أو "سعر أمبريو أرماني") والاسم ده مش موجود في البيانات المتاحة → 🔴 ده **مش** معناه إن العطر مش عندنا. النظام هو اللي مالقاهوش، ودي حاجة تانية خالص. قوله "لحظة أتأكدلك منه" وبس. ❌ ممنوع تقول "مش متوفر" ولا "مش موجود عندنا" ولا "للأسف مش عندنا" ولا تعتذر عن عدم توفره — إحنا مش عارفين، والاعتذار نفسه بيقول إنه مش موجود. بعد كده تقدر تعرض عليه 1-2 من البدائل أدناه على إنهم عطور **تانية** ممكن تعجبه وهو مستني الرد.
   • **(ب) سؤال واضح عن الستور أو المنتجات بشكل عام** — مش عن عطر معين بالاسم (مثل "الأحجام المتاحة إيه؟"، "عندكم 90 ملي؟"، "بتحطوا كام جرام زيت؟"، "العطر أصلي ولا تركيب؟"، "عندكم فرع؟") → 🔴 ده سؤال مفهوم تماماً، جاوب عليه من التعليمات والحقائق الموجودة في رسالة الـ system فوق.
     - لو الإجابة موجودة في الحقائق → قولها للعميل مباشرة.
     - لو العميل سأل عن حجم أو حاجة والحقائق بتقول إنها مش متوفرة → قوله بوضوح إنها مش متوفرة واذكرله المتاح فعلاً (مثال: "لا يا فندم، عندنا 50 و 90 ملي بس").
     - لو الإجابة مش موجودة في الحقائق خالص → قوله "هسأل وأرد عليك يا فندم" أو "لحظة أتأكدلك".
     - ❌❌ ممنوع تماماً ترد على السؤال ده بـ "مش فاهم قصد حضرتك" — أنت فاهم السؤال، بس ممكن تكون مش عارف الإجابة، وده فرق كبير.
   • **(ج) العميل بيتفرج بشكل مبهم** (مثل "عندكو حاجة من شانيل" أو "عايز حاجة حلوة") → ❌ ممنوع تقول "مش متوفر"! اسأله يحدد: "تقصد أنهي عطر بالظبط يا فندم؟".
   • **(د) الرسالة نفسها غير مفهومة فعلاً** (حروف عشوائية، كلام مبتور، مفيش معنى واضح) → دي الحالة الوحيدة اللي تقول فيها: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر؟".
4. 🔴🔴 ممنوع تجمع النفي مع الوعد بالتأكد في رد واحد. "مش موجود عندنا، لحظة أتأكدلك منه" جملة بتنقض نفسها: يا إنك عارف إنه مش موجود يا إنك لسه هتتأكد. اختار التأكد دايماً. (عميل سأل "طب عندكو الكساندريا 2 ؟" واتقاله بالحرف: "عطر الكساندريا 2 مش موجود عندنا، لحظة أتأكدلك منه".)
5. بعد ما تقول إنك هتتأكد (في حالة (أ) فقط)، رشح له 1-2 من "البدائل المقترحة" أعلاه بشكل جذاب — والبدائل دي مرتبة بحيث الأقرب لطلبه فوق، فابدأ بالأول. اذكر النوتة اللي بتخلي البديل قريب من طلبه من بيانات العطر نفسها. ❌ إياك أن تتظاهر أو توحي بأن العطر البديل هو نفسه العطر الذي سأل عنه العميل!
6. ❌ ممنوع تخترع أي معلومة أو عطر غير موجود في القائمة المقترحة أو في حقائق الستور. ❌ وممنوع تنسب لعطر نوتة أو ريحة مش مكتوبة في بياناته فوق، حتى لو كانت هي اللي العميل بيدور عليها. (عميل طلب بخور، فاتقاله إن Stronger With You "فيه لمسة بخور خفيفة" — ونوتاته المسجلة هيل وأناناس وقرفة وفانيليا وكستناء وأمبروود، مفيش فيها بخور خالص.)
"""
        # Case (أ) and rule 4 above both script a deferral unconditionally. On the chase turn that
        # is the wrong reply, so this goes last and says so in as many words.
        if exhausted:
            instructions += _ABSENT_RULES
        instructions += availability_hint

    messages = [
        {
            "role": "system",
            "content": get_system_prompt(store),
        }
    ]
    if history:
        messages.extend(history)
        
    messages.append({
        "role": "user",
        "content": f"""
═══ سؤال العميل ═══
{message}

{context}
{instructions}
"""
    })

    response = chat(messages, profile="converse")
    return response, context