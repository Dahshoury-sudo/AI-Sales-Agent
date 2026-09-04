from . import absence
from .product_resolver import resolve_products
from .product_formatting import format_products
from .ai.client import chat
from .ai.prompts import get_system_prompt
from .fallback import suggest_alternatives
from .static_faq_service import normalize_arabic
from .sales import described as sales_described
from .sales import value as sales_value


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
    below already forbids volunteering the other one's detail.

    How wide the window is depends on the message. `latest_only` normally keeps the set to the
    reply actually being responded to, so a stale cart line cannot ride along — but it cannot
    represent a referent that spans two replies, and a plural pointer is exactly that case.
    Conversation 842 asked "عندك سوفاج ؟", then "طب بلو دي شانيل ؟", then "بكام الاتنين": both
    perfumes were under discussion, each introduced by its **own** reply, so `latest_only` cut the
    older one and the price of "the two" was answered for one. `naming.refers_to_several` is what
    distinguishes that from the singular "بكام ده", which still gets the narrow window and 726's
    stale-cart protection unchanged.

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
    from .sales import naming

    # Outside the `try` on purpose: the handler below turns any failure into "no referent at all",
    # which is the right answer for a database or window error and a silent misfire for a bug in
    # here.
    several = naming.refers_to_several(message)

    try:
        offered = sales_described.offered_in_order(
            conversation, store, latest_only=not several
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


def _products_named(names, store):
    """Rows for catalogue names, in the order given, dropping anything not currently stocked.

    `described.offered_in_order` and its neighbours deal in names, because they read them back out of
    `Message.internal_context`; everything downstream of here needs rows. Split out of
    `_referent_from_conversation` when a second caller needed the same conversion — passing the names
    straight through cost a crash inside `format_products`, which reasonably assumed it had products.
    """
    if not names:
        return []

    from products.models import Product

    by_name = {
        product.name: product
        for product in Product.objects.filter(
            store=store, is_active=True, name__in=list(names)
        )
        .prefetch_related("variants")
        .select_related("brand")
    }
    return [by_name[name] for name in names if name in by_name]


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


# Marks the turn where a perfume name has been checked against the whole active catalogue and is
# genuinely not in it. Read by the prompt (`_ABSENT_RULES`), by `prompts.py` red line 3, by
# `router._escalate_absent_name`, and by `eval_harness.checks` — every one of those otherwise
# forbids denying availability, and all of them have to make the same single exception.
#
# Replaces `LOOKUP_EXHAUSTED`, which named a different fact: that the customer had asked twice and
# we had run out of ways to stall. The denial no longer waits for a second ask, so the marker no
# longer records patience running out — it records `absence.catalogue_verdict` returning ABSENT.
ABSENCE_DENIED_MARKER = "ABSENCE_DENIED"

# Marks the turn where a name could not be checked — the extractor call failed, the span is a bare
# brand, an Arabic name nobody could clear against Latin-only rows. The reply on this turn must
# neither deny nor promise: it asks the customer to retype the name. See `_UNREADABLE_NAME_RULES`.
NAME_UNREADABLE_MARKER = "NAME_UNREADABLE"


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


def _pending_lookup_block(question, verdict):
    """Record, inside the turn's own context, a question the catalogue could not answer.

    The customer named a perfume, `naming.may_name_a_perfume` agreed it was a name, and neither the
    deterministic matcher nor the resolver could place it. What to say next depends entirely on
    `verdict` — `absence.catalogue_verdict`'s answer for that name — and the two branches below are
    opposite replies, which is the whole reason that function returns three states instead of a
    boolean.

    The customer's **raw message** is stored, not a name extracted from it, except where the caller
    has an unplaced span to hand (see its comment). Extracting one here means guessing which words
    were the perfume, and a guess written into the record is a fabrication the next turn will treat
    as fact. The wording is already in the history; what was missing is the flag that it is open.

    `described.pending_lookup` reads this back, and `router` escalates on it. The caller passes
    `question` rather than always the current message, because on a chase turn the question is the
    *earlier* message — "اتأكدلي منه" names nothing to look up.

    Conversation 795 is why the record exists at all. Turn 1 could not place "لادور بخور"; turn 2's
    "طب اتأكدلي" found no trace of it, fell through to `_referent_from_conversation`, and was
    answered with the previous turn's perfume; turn 3 asked again and got the same wrong perfume,
    this time with an invented بخور note attached to make it fit.

    ABSENT is the ordinary case and it denies on the first ask. It used to promise to check and deny
    only if chased, on the reasoning that the catalogue coming back empty is not proof of absence —
    which was true of the old check and is no longer true of this one: `absence.catalogue_verdict`
    reaches ABSENT only with a witness, and every uncertain case now lands in UNKNOWN instead. What
    the promise cost is conversations 795, 798, 799, 816 and 817: nothing looks the name up between
    two messages of a chat, so the promise was made by someone who could not keep it, and the
    customer waited for an answer that was never coming.
    """
    block = (
        "═══ سؤال معلّق ═══\n"
        f"{sales_described.PENDING_LOOKUP_MARKER} {(question or '').strip()}\n"
    )
    if verdict == absence.ABSENT:
        return block + (
            f"{ABSENCE_DENIED_MARKER}\n"
            "العطر اللي العميل سمّاه اتّأكدنا منه في الكتالوج كله — مش عندنا فعلاً، ودي حقيقة "
            "متأكدين منها مش تخمين.\n"
            "🔴 المحادثة مكمّلة بعد الرد ده، فمتقفلهاش ومتقولش سلام: العميل لازم يقدر ياخد واحد من "
            "البدائل اللي بتعرضها عليه في نفس الرد.\n\n"
        )
    return block + (
        f"{NAME_UNREADABLE_MARKER}\n"
        "مش واضح العميل بيقصد أنهي عطر بالظبط — الاسم اللي كتبه مش متأكدين منه، ومقدرناش نتأكد "
        "منه في الكتالوج.\n"
        "🔴 ممنوع تقول إنه مش موجود عندنا — إحنا مش عارفين، والنفي هنا غلط زي التأكيد. "
        "🔴 وممنوع توعده تراجع وترجعله — مفيش حد بيراجع. اطلب منه يكتب الاسم تاني.\n\n"
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
#
# Replaces `_DEFERRAL_RULES`, which scripted "لحظة أتأكدلك منه" and nothing else on this turn, and
# which is gone: no part of this pipeline ever looked a name up between two messages, so that
# promise could not be kept. The rules below are almost unchanged from the version that used to
# fire only after a customer had asked twice — the change is which turn reaches them, not what they
# say. What did go is the "قاعدة فوق كل القواعد" framing: this no longer overrides a competing
# instruction elsewhere, because case (أ) in the not-found branch now says the same thing.
#
# `absence.catalogue_verdict` is what earns the denial. Reaching these rules requires an ABSENT
# verdict, which requires a witness — a full sweep of the active catalogue in the customer's own
# alphabet, or the extractor reporting the name unplaced against a catalogue it was shown whole.
# Anything less lands in `_UNREADABLE_NAME_RULES` instead. That is the guard on the Versace Eros
# incident (told unavailable, in stock at 1019 جنيه): denying a perfume we sell is still the worst
# outcome here, and the check moved rather than the risk being accepted.
#
# The bullet against a closing question is prose and unmeasured — it reduces the supply of ambiguous
# next turns rather than fixing how one is read. It survives from `_DEFERRAL_RULES` because 915
# turn 12 ended one of these turns with "تحب أعرفلك عن حجم معين أو سعر؟" and the customer answered
# *that* question, so their next message was shaped by our own CTA and matched no vocabulary
# anywhere. Here the closing move is the alternatives, which are a CTA with something behind it.
#
# `router._escalate_absent_name` notifies the owner on this turn and leaves the bot serving, so the
# customer can take up one of those alternatives; a second denial about the same perfume is where it
# hands over instead.
_ABSENT_RULES = """14. 🔴🔴 العميل سمّى عطر اتّأكدنا منه في الكتالوج كله ومش عندنا (شوف ABSENCE_DENIED في قسم "سؤال معلّق").
   • ✅ الرد الصح: قوله بوضوح، وباعتذار قصير، إن العطر اللي سأل عنه مش موجود عندنا. جملة واحدة.
   • ✅ سمّي العطر بنفس الحروف اللي العميل كتبها بيها — لو كتبه بالعربي، ردّه بالعربي زي ما هو. ❌ ممنوع تترجمه أو تكتبه بحروف لاتينية من عندك ("L'Adour")، دي هجاء بتخترعه لعطر إحنا بنقول إننا مش بنبيعه.
   • ❌ ممنوع توعده تتأكد، وممنوع تقول "لحظة أتأكدلك" ولا "هسأل وأرد عليك" ولا "هشوفه لك" — إحنا **اتأكدنا خلاص**، والوعد ده بيسيب العميل مستني حاجة مش جايه.
   • ❌ وممنوع تجمع النفي مع وعد بالتأكد في رد واحد ("مش موجود عندنا، لحظة أتأكدلك منه") — الجملة دي بتنقض نفسها.
   • ❌ ممنوع تقول أي كلام عن نظام أو بيانات أو كتالوج أو "مش ظاهر عندي" — العميل مش المفروض يعرف إن في حاجة زي دي أصلاً.
   • ✅ في **نفس الرد**، وبعد النفي على طول، اعرض عليه بديل أو اتنين من العطور اللي في البيانات، وقول بوضوح إنها عطور **تانية** بالاسم الكامل. ❌ ممنوع ترد بنفي لوحده ومتعرضش حاجة — العميل جاي يشتري، وحقه يلاقي حاجة يقدر ياخدها.
   • ❌ ممنوع تسرد أسعار أو مواصفات عطر تاني كأنها إجابة على العطر اللي هو سأل عنه.
   • ❌ ممنوع تقفل الرد بسؤال فاضي أو بسلام. البدائل نفسها هي خاتمة الرد، واسأله لو حابب يعرف تفاصيل عن واحد منهم.
"""


# The other half of the fork, for a name `absence.catalogue_verdict` could not clear either way:
# the extractor call failed, or the customer typed a bare brand ("عندكو ديور؟"), or they typed an
# Arabic name the extractor did not report as unplaceable. Numbered 14 like its sibling, and
# injected only where that one is not.
#
# Both a denial and a promise are wrong here, which is what makes this its own reply rather than a
# fallback to either neighbour. The denial is wrong because nobody checked — an extractor timeout
# denying a stocked perfume is the Versace Eros incident with an infrastructure cause. The promise
# is wrong for the reason it is gone everywhere else: nothing looks the name up afterwards.
#
# Asking the customer to retype converges, which is why it is safe to ask. The retyped name gets a
# fresh extractor call, and `naming.re_asks` recognises the shape if it comes back the same, so
# `router._escalate_absent_name` can hand a second unreadable ask to a human instead of asking a
# third time.
_UNREADABLE_NAME_RULES = """14. 🔴🔴 العميل كتب اسم عطر مش متأكدين منه، ومقدرناش نتأكد منه (شوف NAME_UNREADABLE في قسم "سؤال معلّق").
   • ✅ الرد الصح: قوله إنك عايز تتأكد هو قاصد أنهي عطر، واطلب منه يكتبلك الاسم تاني أو يكتبه بشكل تاني. جملة واحدة.
   • ❌ ممنوع تقول إنه مش موجود عندنا ولا "مش متوفر" ولا تعتذر عن عدم توفره — محدش اتأكد، والنفي هنا غلط زي التأكيد.
   • ❌ وممنوع توعده تتأكد وترد عليه ("لحظة أتأكدلك"، "هسأل وأرد عليك"، "هشوفه لك") — مفيش حد بيراجع بعد الرد ده، والوعد بيسيبه مستني حاجة مش جايه. الفرق بسيط وبيغير كل حاجة: إنت بتسأله سؤال، مش بتوعده بوعد.
   • ✅ ولو حابب، اعرض عليه في نفس الرد عطر أو اتنين من البيانات على إنهم اقتراحات لحد ما يوضّح، بالاسم الكامل. ❌ وممنوع توحي إن واحد منهم هو العطر اللي هو سأل عنه.
"""


# Replaces `_NOT_THE_PERFUME_ASKED_ABOUT` when the customer named several perfumes and we could place
# some of them. That constant's whole claim — these rows are not what you were asked about — is false
# here, and acting on it would withhold prices the customer did ask for. 836 turn 1 is the turn:
# "عايز اعرف اسعار بلو دي شانيل وسوفاج والكساندريا 2", two in stock, one not.
_PARTIALLY_ANSWERED = (
    "⚠️ العميل سمّى أكتر من عطر في الرسالة دي. العطور اللي تحت دي **فعلاً** من اللي سأل عنهم — "
    "جاوب عليهم عادي بالبيانات اللي تحت. بس فيه اسم واحد على الأقل سمّاه ومش موجود في البيانات "
    "(مكتوب في قسم \"سؤال معلّق\" فوق) — العطر ده لوحده هو اللي محتاج رد مختلف، شوف القاعدة رقم "
    "14 تحت.\n"
)


# Replaces `_ABSENT_RULES` on a partially-resolved turn whose unplaceable name came back ABSENT.
# The plain denial rules forbid quoting the prices of the rows in context, which is right when they
# are a different perfume and wrong here: the customer asked for these prices in the same breath as
# the name we could not place. What still has to hold is that the unplaceable name does not quietly
# disappear — 836 answered two of three names for three turns running and by the third had stopped
# mentioning the third one at all.
#
# Deliberately no alternatives bullet, which is the one place this diverges from `_ABSENT_RULES`
# rather than just re-scoping it. The reason the plain denial must carry alternatives is that a bare
# denial leaves a customer who came to buy with nothing to buy; here they already have two real
# perfumes with real prices in the same reply, so a third suggestion is the "متحشرش معلومات" that
# rule 7 forbids.
_PARTIAL_ABSENT_RULES = """14. 🔴🔴 العميل سمّى أكتر من عطر، وواحد منهم اتّأكدنا منه ومش عندنا (شوف ABSENCE_DENIED في قسم "سؤال معلّق").
   • ✅ جاوب على العطور اللي في البيانات عادي — قول أسعارها ومواصفاتها زي أي سؤال تمن طبيعي. دي أسئلة العميل وسألها بجد.
   • ✅ وفي نفس الرد، اذكر العطر التاني بالاسم اللي العميل كتبه بيه وقوله بوضوح وباعتذار قصير إنه مش موجود عندنا. جملة واحدة.
   • ✅ سمّيه بنفس الحروف اللي العميل كتبها بيها — لو كتبه بالعربي، ردّه بالعربي زي ما هو. ❌ ممنوع تترجمه أو تكتبه بحروف لاتينية من عندك.
   • ❌ ممنوع تنسى العطر ده أو تسيبه من الرد. لو جاوبت على اللي لقيته وسكت عن اللي مش عندنا، العميل هيفضل يسأل عليه.
   • ❌ ممنوع توعده تتأكد منه ("لحظة أتأكدلك"، "هسأل وأرد عليك") — إحنا اتأكدنا خلاص، والوعد بيسيبه مستني حاجة مش جايه.
   • ❌ ممنوع تنسب أي سعر أو نوتة من العطور اللي فوق للعطر ده، وممنوع توحي إنه واحد منهم أو إن ليه نفس الريحة.
"""


# The same turn shape, for a name `absence.catalogue_verdict` could not clear. Bullet 2 is the only
# real difference from `_PARTIAL_ABSENT_RULES`: ask which perfume they meant instead of denying it.
_PARTIAL_UNREADABLE_RULES = """14. 🔴🔴 العميل سمّى أكتر من عطر، وواحد منهم مكتوب باسم مش متأكدين منه (شوف NAME_UNREADABLE في قسم "سؤال معلّق").
   • ✅ جاوب على العطور اللي في البيانات عادي — قول أسعارها ومواصفاتها زي أي سؤال تمن طبيعي. دي أسئلة العميل وسألها بجد.
   • ✅ وفي نفس الرد، اذكر الاسم التاني زي ما العميل كتبه واطلب منه يكتبه تاني أو يوضّح هو قاصد أنهي عطر.
   • ❌ ممنوع تنسى العطر ده أو تسيبه من الرد. لو جاوبت على اللي لقيته وسكت عن التاني، العميل هيفضل يسأل عليه.
   • ❌ ممنوع تقول إنه مش متوفر عندنا أو مش موجود، وممنوع تعتذر عن عدم توفره — محدش اتأكد منه.
   • ❌ وممنوع توعده تتأكد وترد عليه ("لحظة أتأكدلك"، "هسأل وأرد عليك") — مفيش حد بيراجع. إنت بتسأله سؤال، مش بتوعده بوعد.
   • ❌ ممنوع تنسب أي سعر أو نوتة من العطور اللي فوق للعطر ده، وممنوع توحي إنه واحد منهم أو إن ليه نفس الريحة.
"""


# Appended to the found-branch instructions when every row in context is a perfume the customer
# asked about and there is more than one of them. Numbered 14 like `_ABSENT_RULES` and the partial
# pair, and injected only on the `else` of `if deferring:` so it can never appear alongside any of
# them.
#
# Exists because nothing else in the found branch asks for coverage. The line
# "✅ جاوب على العطور اللي في البيانات عادي" lives in the partial rules and nowhere else, and those
# are gated on `partially_resolved` — so a turn where every name resolved cleanly got no coverage
# instruction at all. 841 turn 4 is that turn: both perfumes were in the context with full prices,
# "بكام لااتنين ؟" asked for both, and the reply priced one.
#
# The found-branch rules actively push the other way, which is why this restates two of them:
# rule 1's price bullet is singular-shaped ("ابدأ بالحجم اللي في سطر 💡 Value Pick" orders sizes
# inside one perfume), and on 841 only Bleu de Chanel had that line while the model led with the
# perfume that had none. Rule 7 forbids volunteering information the customer did not ask for, and
# the second perfume looks exactly like that until something says it is not.
_ANSWER_EVERY_ROW = """14. 🔴🔴 العميل سأل عن أكتر من عطر، وكل العطور اللي في البيانات فوق دي اللي هو سأل عنهم.
   • ✅ جاوب على **كلهم** في الرد ده، كل واحد باسمه الكامل. لو سأل عن السعر، قول سعر كل عطر فيهم.
   • ❌ ممنوع تجاوب على واحد وتسكت عن التاني وتسيب العميل يسأل تاني. لو عطر من اللي فوق مش في ردك، الرد ناقص.
   • 🔴 القاعدة رقم 7 (متحشرش معلومات مسألش عنها) مش بتنطبق هنا — العطر التاني مش معلومة زيادة، هو نص السؤال.
   • ✅ سطر 💡 Value Pick (أو 💡 اقتراح حجم) بيترتب **جوه كل عطر لوحده**: طبّقه على كل عطر عنده السطر ده، ومتخليهوش سبب تبدأ بعطر قبل التاني ولا تفضّل عطر على عطر. العطر اللي مالوش السطر ده، ابدأ بأحجامه زي ما هي مكتوبة.
   • ❌ ومع كل ده، القاعدة رقم 12 لسه شغالة: ممنوع تجمع الأسعار في رقم واحد ولا تقول "الإجمالي". سعر كل عطر لوحده جنب التاني — ده رد، مش مجموع.
   • 🔴 قبل ما تبعت الرد: عد أسماء العطور اللي في البيانات فوق، وتأكد إن كل اسم فيهم مكتوب في ردك وجنبه سعره. لو لقيت اسم ناقص، الرد ناقص — كمّله قبل ما تبعته.
"""


def _carried_price_intent_hint(message, history):
    """One line saying this message continues the previous question's subject on a new perfume.

    835 turn 5 is "وبلو دي شانيل ؟" — *and Bleu de Chanel?* — one turn after "طب عاملين كام دو" asked
    a price. It carries no price word of its own, so instruction 1 read it as an availability question
    and answered "أه متوفر"; the customer then had to type "بكام يعم" to ask a sixth time for the
    thing they had already asked for. The elision is ordinary Arabic and ordinary everything else: a
    bare name after a price question inherits the price question.

    Deliberately narrow. A message carrying an availability word is asking about availability and is
    left alone (that is `_availability_only_hint`'s turn), and a message carrying a price word needs no
    help. Only the bare continuation qualifies, and only when the customer's own previous message is
    what it continues — the assistant's offer does not count, because "تحب تعرف الأسعار؟" followed by a
    name is the assistant's intent, not the customer's.

    The caller must skip this when the turn is deferring; see the call site for why.

    Returns "" for every other message, so the caller concatenates it unconditionally.
    """
    normalized = normalize_arabic(message or "")
    if not normalized:
        return ""
    if any(word in normalized for word in _PRICE_OR_SIZE_WORDS):
        return ""
    if any(word in normalized for word in _AVAILABILITY_WORDS):
        return ""

    previous = ""
    for entry in reversed(list(history or ())):
        if entry.get("role") == "user":
            previous = normalize_arabic(entry.get("content", ""))
            break
    if not previous or not any(word in previous for word in _PRICE_OR_SIZE_WORDS):
        return ""

    return (
        "\n🔴 ملاحظة على الرسالة دي: العميل بيكمّل نفس السؤال اللي سأله قبل كده — سؤال السعر — بس "
        "على العطر الجديد اللي سمّاه هنا. جاوبه بالسعر على طول. ❌ ممنوع ترد بتأكيد التوفر بس "
        "وتستنى منه يسأل \"بكام\" تاني.\n"
    )


def _named_budget_hint(budget):
    """What the ✅/⚠️/❌ markers oblige on a turn about a perfume the customer named by name.

    Returns "" with no budget, so the caller concatenates it unconditionally and the ordinary turn
    pays nothing for it.

    The ❌ label reads "ممنوع تعرضه" and persona rule `prompts.py:104` repeats it. That is a rule
    about *choosing* which size to recommend, and this branch is not choosing: the customer typed
    the perfume's name and asked what it costs. Without this line the labels arrive carrying a
    prohibition written for a different question, and the answer to "بكام" becomes a refusal to
    say — the one outcome every instruction in this block exists to prevent.

    The ✅ half is conversation 931's half, and it is deliberately `prompts.py:106` restated as a
    fact about the data in front of the model rather than a new rule: the marker is the verdict, and
    there is no difference figure to quote unless one is written inside a ⚠️.
    """
    if budget is None:
        return ""
    return (
        f"\n🔴 العميل قال إن ميزانيته {int(budget)} جنيه، وكل سعر في البيانات فوق جانبه علامة "
        "(✅ داخل الميزانية / ⚠️ أعلى شوية / ❌ أعلى بكتير). العلامة دي محسوبة وهي الحكم الوحيد "
        "على الميزانية: ❌ ممنوع تحسب الفرق بنفسك، وممنوع تقول رقم فرق مش مكتوب جوه علامة ⚠️، "
        "وحجم عليه ✅ يبقى داخل الميزانية خلاص — ممنوع تقول عنه \"أعلى من ميزانيتك\" ولا \"أعلى "
        "شوية\".\n"
        "🔴 و\"ممنوع تعرضه\" اللي جوه علامة ❌ معناها ممنوع **ترشحه**، مش ممنوع تقول سعره: العميل "
        "هنا سأل عن العطر ده بالاسم، فقوله سعر الحجم اللي سأل عنه زي ما هو مكتوب، وقوله إنه أعلى "
        "من الرقم اللي قاله، واعرض معاه حجم داخل ميزانيته لو فيه. ❌ ممنوع تخفي سعر عطر العميل "
        "سأل عنه بالاسم، وممنوع تقول إنه مش متوفر عشان سعره.\n"
    )


def _alternatives_budget_hint(budget):
    """The same markers on the fallback list, where the model *is* the one choosing.

    Separate from `_named_budget_hint` because the ❌ prohibition is correct here and wrong there —
    nobody named these perfumes, so declining to pitch an over-budget one is the right call. What
    needs saying instead is what to do when the whole list is over budget: `suggest_alternatives`
    only sorts in-budget first, it does not filter, so a stated budget under the cheapest thing in
    the catalogue yields a list of ⚠️ and ❌ rows and an instruction (rule 4) to pitch from it.
    Silence and an unmarked over-budget pitch are both worse than saying the number out loud.
    """
    if budget is None:
        return ""
    return (
        f"\n🔴 ميزانية العميل {int(budget)} جنيه، والبدائل فوق مرتبة بحيث اللي داخل الميزانية (✅) "
        "الأول — فابدأ بيه. ولو كل البدائل عليها ⚠️ أو ❌، قول للعميل بصراحة إن اللي عندنا أعلى من "
        "الرقم اللي قاله واذكر أرخص حاجة عندنا بسعرها. ❌ ممنوع تعرض سعر أعلى من ميزانيته من غير "
        "ما تقول إنه أعلى، وممنوع تسكت وتسيبه من غير أي اقتراح.\n"
    )


def get_product_info(message, history=None, store=None, conversation=None, retry_hint=""):
    """Answer a question about a named perfume.

    `retry_hint` is instruction text for the model, not something the customer said, and it is kept
    out of `message` for that reason. `router` used to append its anti-repetition warning to the
    message itself and call again — and every name-reading step below then read the warning as the
    customer's words. "اتأكد" plus "⚠️ تنبيه: ردك السابق كان مكرر..." clears
    `naming.may_name_a_perfume` on the strength of the warning's own vocabulary, comes back
    unresolvable because it is not a perfume, and so sets `named_but_unresolved` — which recorded the
    warning text as an open customer question and put the turn back on the deferral rules.

    816 turn 4 is that: "اتأكد" arriving after the denial was correctly denied again, the denial read
    as repetitive, and the retry replaced it with "لحظة أتأكدلك منه يا فندم، وهرد عليك أول ما أعرف" —
    a promise to look up a perfume the customer had just been told we do not stock. The retry undid
    the right answer, which is why nothing in the deferral logic could be blamed for it.
    """
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

    # The `Resolution` itself, kept aside because `products` is about to be reassigned to the
    # referent and the two facts only it carries are needed much further down: which spans it could
    # not place, and whether the extractor call succeeded at all. `absence.catalogue_verdict` reads
    # both, and reads them through `getattr`, so a plain list here is safe.
    resolution = products if resolver_ran else None

    # The names the resolver read out of this message and could not place. See
    # `product_resolver.Resolution`; `getattr` because a plain list is still a valid return here and
    # every mocked `resolve_products` in the test suite hands back one.
    unplaced = tuple(getattr(products, "unplaced", ()))

    # A name we could not place. Kept as a fact about this turn rather than inferred later from an
    # empty `products`, because the referent lookup below is about to fill that list with a
    # different perfume entirely — which is exactly the confusion conversation 795 was built on.
    #
    # `unplaced` is the first clause because an empty `products` only catches the case where *every*
    # name failed. Conversation 836 turn 1 named three perfumes, two of them in stock, so `products`
    # came back full and this flag stayed False — and with it False no pending record was written, no
    # owner notification fired, and the third perfume was dropped in silence while the customer asked
    # for its price three times. The second clause is the original condition, unchanged, so a total
    # miss still reaches every path it reached before.
    named_but_unresolved = bool(unplaced) or (resolver_ran and not products)

    # A message that named some perfumes we have and some we do not. Kept apart from
    # `named_but_unresolved` because the two need opposite instructions: on a total miss the rows in
    # context are a different perfume and must not be priced as the answer, while here they *are*
    # part of the answer and withholding them would drop a question the customer did ask.
    partially_resolved = bool(unplaced) and bool(products)

    # Nothing named here: the subject is whatever we just offered. Decided in Python because
    # the resolver demonstrably gets this wrong, and `internal_context` is a harder record
    # than an 8-message prose window. This is also the recovery path when the gate fired but the
    # resolver could not place the name, which is what keeps a false alarm above harmless.
    #
    # Which branch produced the rows is recorded first, because the coverage instruction below needs
    # it and nothing downstream can tell afterwards. Rows the customer named themselves are all
    # theirs to be answered; rows that came from the referent are only all theirs when they pointed
    # at several of them.
    products_from_message = bool(products)
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
        resolution = products

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
        and (naming.chasing_a_promise(message) or naming.insisting_on_a_promise(message))
        # The second predicate covers the customer who insists without a collection verb and
        # without re-typing the name: 915 turn 13 is "اه عايز اعرف اسعاره" after a deferral on
        # لادور بخور. `chasing_a_promise` is False on it by design, so the turn was read as neither
        # a chase nor a re-ask, the promise was repeated verbatim, and `router` — which counts
        # content-free deferrals rather than reading the verdict — handed the conversation to a
        # human. Nothing else here changes: the four conditions around it are what make the looser
        # vocabulary safe, because a message that names a perfume never reaches this branch.
        #
        # Deliberately no router change. This branch carries the earlier turn's ABSENCE_DENIED
        # forward, so `_escalate_absent_name` notifies the owner without setting `needs_human` — the
        # bot denies plainly and keeps serving. The handoff was a symptom of this classification, not
        # a second bug.
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
    # supplies that: the question the previous reply left open is the one this reply has to answer.
    # See `_ABSENT_RULES`.
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
    #
    # `named_but_unresolved` is not enough to gate this, which the harness replay of 816 showed and
    # a unit test with `resolve_products` mocked to `[]` could not. On turn 3 the resolver *placed*
    # الكساندريا 2 — on Stronger With You, the perfume the deferral had volunteered — so `products`
    # came back full, the flag went False, and the turn fell through to the plain product branch and
    # was answered "حضرتك تقصد Stronger With You ولا Absolutely؟". That is `_chasing_open_lookup`'s
    # failure exactly, arriving by the named route instead of the pronoun one, so it is the same
    # provenance test that answers it: everything in `products` being perfume we already offered is
    # the signature of a resolved reference, not of a name this message placed. A deterministic
    # catalogue match is excluded because that is proof we stock the thing, and denying it would
    # break red line 3 — the same carve-out `chasing` makes with `named_here`.
    #
    # Provenance alone would be too loose ("الديور بكام؟" after the same deferral is also all
    # offered perfume, and it is a price question we should answer). `naming.re_asks` is the half
    # that keeps it tight: the customer's own words have to name the open question again.
    re_asked = ""
    if named_but_unresolved or (
        not named_here and _chasing_open_lookup(products, conversation, store)
    ):
        re_asked = next(
            (
                question
                for question in sales_described.pending_questions(conversation)
                if naming.re_asks(message, question)
            ),
            "",
        )

    # Did we actually check, and what did the check say? The one decision this whole turn turns on.
    #
    # Denying a perfume we sell is still the worst outcome in this file — the Versace Eros incident
    # (told unavailable, in stock at 1019 جنيه) is why the reply used to be a promise to check
    # instead. The promise is gone because nothing in the pipeline ever kept it; what replaces it as
    # the safety mechanism is this verdict. `absence.catalogue_verdict` reaches ABSENT only with a
    # witness and files every uncertain case as UNKNOWN, which has its own reply — ask the customer
    # to retype the name — that is neither a denial nor a promise.
    #
    # The verdict is taken for `unplaced[0]`, the extractor's own report of a span it could not place
    # against a catalogue it was shown whole. That span is the only name on this turn that is not a
    # guess: `product_resolver._unplaced_names` has already proved the deterministic matcher cannot
    # place it, that it carries an identifying token, and that its words are the customer's own.
    #
    # Deliberately not the raw message when there is no such span. Handing "عندكو حاجة من شانيل" or
    # "do you have anything nice" to the verifier asks it to treat a whole sentence as a perfume
    # name, and the token-subset test that makes `candidates` reliable on a name is unreliable on a
    # sentence. No span means no denial, which is the safe direction: the customer gets asked which
    # perfume they meant.
    #
    # Only the first span, because `described._pending_payloads` reads one pending question per reply
    # by design. A message that failed on two names addresses both in prose and records the first.
    if unplaced:
        verdict = absence.catalogue_verdict(unplaced[0], store, resolution)
    elif chasing or re_asked:
        # This message carries no name — it is "اتأكدلي منه" or "ها لقيت اي ؟" collecting an answer
        # about a question asked earlier. The check ran on the turn that question arrived, and the
        # record of it is a reply carrying the marker. Re-deriving a verdict from a message with no
        # name in it would abstain on a perfume we have already verified and denied, and asking the
        # customer to retype a name they never typed on this turn is not a question they can answer.
        verdict = (
            absence.ABSENT
            if sales_described.replies_carrying(conversation, ABSENCE_DENIED_MARKER)
            else absence.UNKNOWN
        )
    else:
        verdict = absence.UNKNOWN

    denied = verdict == absence.ABSENT

    if named_but_unresolved or re_asked:
        # `re_asked` is the wording the question was first asked in, and recording that rather than
        # this message keeps the record stable — which is what lets a third ask match the same
        # question instead of starting a new one.
        #
        # `unplaced[0]` comes next, and only on a partially-resolved message. The docstring below
        # insists on recording the raw message, and for a total miss it still is — that is the
        # `or message` at the end. But 836 turn 1's raw message is "عايز اعرف اسعار بلو دي شانيل
        # وسوفاج والكساندريا 2", and recording *that* as the open question means the next turn's
        # denial denies Bleu de Chanel and Dior Sauvage too, both of which are in stock. Red line 3
        # forbids exactly that. The unplaced span is the only payload here that is not a guess:
        # `product_resolver._unplaced_names` has already proved the catalogue cannot place it, that it
        # carries an identifying token, and that its words are the customer's own.
        pending_block = _pending_lookup_block(
            re_asked or (unplaced[0] if partially_resolved else "") or message,
            verdict,
        )
    elif chasing:
        pending_block = _pending_lookup_block(pending_question, verdict)
    else:
        pending_block = ""

    # One name for "this turn owes an answer we do not have", however we found that out.
    deferring = bool(pending_block)

    # On the turn the denial lands, the rows in context are no longer a referent — they are the only
    # thing the customer can act on, and `_ABSENT_RULES` asks for "بديل أو اتنين" of them. 835 turn 3
    # had exactly one row to offer, because the resolver had placed the unplaceable name onto the one
    # perfume it had seen most recently, so the denial could name a single alternative and turn 4's
    # plural "طب عاملين كام دو" had one perfume to price instead of two.
    #
    # The comment above records that unioning the referent with the resolver's output was tried and
    # reverted. That was the *found* branch, where the union put a wrong guess back in as the answer.
    # Here the rows are already labelled `_NOT_THE_PERFUME_ASKED_ABOUT`: nothing in this context claims
    # to be what the customer asked about, so widening the pool cannot mislabel anything.
    #
    # Every deferring turn now needs this, which is the change: the denial arrives on the first ask,
    # so the first ask is the turn that has to name an alternative. It used to be gated on the second
    # ask, when the denial was, and a first ask deliberately got no more data than it came with —
    # that made sense while its reply was a promise, which needs nothing to offer.
    #
    # `not partially_resolved` is the exception, and the reason is the customer's own screen: they
    # already have two real perfumes with real prices in front of them, so a third they did not ask
    # about is noise, and `_PARTIAL_ABSENT_RULES` deliberately asks for no alternatives.
    if deferring and not partially_resolved:
        pool = list(products)
        for product in _products_named(
            sales_described.offered_in_order(conversation, store), store
        ):
            if product not in pool:
                pool.append(product)
        products = pool

    availability_hint = _availability_only_hint(message)
    # Only when this turn is not already about something we could not find. 836 turn 2 is
    # "ها لقيت اي" one turn after a message containing "اسعار", so without the guard the carry would
    # push a price list onto the turn that has to deliver the denial.
    carried_intent_hint = "" if deferring else _carried_price_intent_hint(message, history)
    # Both branches below render prices, so both want the budget markers beside them. Until this,
    # `grep max_price` over this module returned nothing: a customer who had said 1200 saw a 3800
    # size with no marker at all, and `value_pick_note` — which filters to *in-budget* variants —
    # was picking the best value out of the whole size ladder and calling it that. Persona rule
    # prompts.py:103 asserts the opposite ("الـ Value Pick بيتحسب داخل ميزانية العميل"), which was
    # true of the recommendation branch and false here.
    #
    # One asymmetry is worth recording, because it points the other way. Conversation 931's false
    # over-budget claim came out of a *labelled* recommendation block, and the turn of that same
    # conversation which rendered through here — unlabelled, because of this very gap — made no
    # such claim. That is not an argument for leaving prices bare: an unmarked over-budget size is
    # conversation 757 from the other side. It is an argument for the labels landing only once the
    # falsehood is caught wherever it comes from, which `reply_sanitizer.strip_false_over_budget`
    # now does deterministically. That guard is why this can land at all.
    budget = sales_value.stated_budget(conversation)

    # Does this turn owe the customer an answer about every row it is being given? Computed here,
    # after the widening above, so `products` is final.
    #
    # `products_from_message`: the customer named them all, so they are all part of the question.
    # This is the 836-shaped case where every name resolves and no deferral fires, which today
    # receives no coverage instruction at all.
    #
    # `refers_to_several`: the rows came from the referent, and a plural pointer is what makes the
    # whole referent the subject. A *singular* "بكام ده" against a two-row referent must keep rule 7's
    # protection — the second row is there so the model can answer about whichever perfume was meant,
    # not so it can volunteer both.
    #
    # `not deferring` is semantic and not just numbering hygiene: on a deferral the rows are labelled
    # `_NOT_THE_PERFUME_ASKED_ABOUT`, and pricing all of them is precisely what that label forbids.
    # It also covers the widened pool above, whose rows the customer never named at all.
    answer_every_row = (
        len(products) > 1
        and not deferring
        and (products_from_message or naming.refers_to_several(message))
    )

    if products:
        context = pending_block
        context += "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        if partially_resolved:
            context += _PARTIALLY_ANSWERED
        elif deferring:
            context += _NOT_THE_PERFUME_ASKED_ABOUT
        # Capped as a prompt-size safety net. The referent branch can now hand over every
        # perfume the last reply named, which is ~2 in practice and bounded by the two-reply
        # window — the limit only guards the pathological case.
        context += format_products(products, max_price=budget, limit=6)
        instructions = """
═══ تعليمات صارمة ═══
1. 🔴 لما العطر اللي في البيانات يكون هو نفس العطر اللي العميل سأل عنه، اكتب اسمه بالإملاء الموجود في البيانات — حتى لو العميل غلط في الكتابة أو كتبه بالعربي.
   🔴🔴 لكن لو العميل سمّى عطر والبيانات فيها عطر **تاني خالص**: ده مش غلطة إملائية منه، ده عطر مختلف. ❌ ممنوع تقول إن العطر اللي سأل عنه "اسمه الصحيح" هو العطر اللي في البيانات، وممنوع توحي إنهم نفس العطر أو نفس الريحة أو نفس التركيبة. جاوب على العطر اللي هو سأل عنه، ولو مش معاك بياناته اسأله يكتبلك اسمه تاني عشان تتأكد منه — ❌ ومتوعدهوش إنك هتراجعه وترد عليه. (عميل سأل عن Acqua di Gio واتقاله "اسمه الصحيح Y Eau de Parfum" — دول عطرين مختلفين من براندين مختلفين، والعميل كرر السؤال واتقاله نفس الكلام تاني.)
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
12. 🔴🔴 ممنوع تحسب إجمالي طلب. البيانات اللي فوق فيها أسعار الأحجام بس — مفيش فيها عربة ولا كميات ولا إجمالي. ❌ ممنوع تضرب سعر في كمية، وممنوع تجمع أسعار، وممنوع تقول "الإجمالي" أو "المجموع" أو تتكلم عن "الطلبين" أو أي عدد قطع. لو العميل سأل الطلب بقى بكام، قوله إنك هتراجع الطلب معاه وابدأ تجمع تفاصيله — الإجمالي بيتحسب من الطلب نفسه مش من الأسعار دي. (عميل اتقاله إجمالي 1560 جنيه لطلب مش موجود، وهو أصلاً قال إن ميزانيته 900.) ✅ بس خد بالك: إنك تقول سعر كل عطر لوحده جنب التاني في رد واحد **مش** إجمالي — ده هو الرد الصح لما العميل يسأل عن أكتر من عطر (زي "بكام الاتنين"). الممنوع هو إنك تجمعهم في رقم واحد أو تضربهم في كمية أو تقول كلمة "الإجمالي" أو "المجموع". ❌ ممنوع تسكت عن سعر عطر منهم عشان تتجنب القاعدة دي.
13. 🔴🔴 لو العميل قال إنك قلت سعرين مختلفين لنفس العطر (زي "انت قولت سعرين مختلفين للسترونجر") — بص على سطر `⚠️ عطر مختلف عن` الأول. لو السعرين بيرجعوا لعطرين مختلفين على نفس الخط، يبقى **السعرين صح**: ❌ ممنوع تعتذر، وممنوع تقول إن فيه لبس أو غلط، وممنوع تسحب سعر أو تقول إن واحد منهم "هو السعر الصحيح". وضّح إن ده عطر وده عطر تاني بالاسم الكامل، وقول سعر كل واحد لوحده. ولو مش متأكد هو بيقصد أنهي واحد، اسأله. (عميل اتقاله 780 لـ Stronger With You Intensely وبعدين 700 لـ Stronger With You — دول عطرين مختلفين — وسأل، فاتقاله "أعتذر على اللبس، 700 ده السعر الصحيح"، ومشي فاكر إن Intensely بـ 700.)
"""
        # Which fork of the deferral rules, decided by the verdict and nothing else. The old
        # condition was the ask count (`exhausted`) — first ask promises, second ask denies. The
        # count is gone; what stands in its place is whether we actually know, so a verified absence
        # is denied on the first ask and an unverified name is never denied at all.
        if deferring:
            if partially_resolved:
                instructions += _PARTIAL_ABSENT_RULES if denied else _PARTIAL_UNREADABLE_RULES
            else:
                instructions += _ABSENT_RULES if denied else _UNREADABLE_NAME_RULES
        elif answer_every_row:
            instructions += _ANSWER_EVERY_ROW
        instructions += availability_hint
        instructions += carried_intent_hint
        instructions += _named_budget_hint(budget)
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
            # An ordering tier, not a filter — the function sorts in-budget first and keeps the
            # rest. That is the shape this branch needs: pitching a perfume above the stated
            # number is a worse answer than pitching one below it, and pitching nothing is worse
            # than both.
            max_price=budget,
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
            context += format_products(alternatives, max_price=budget, show_value_pick=False)

        instructions = """
═══ تعليمات ═══
1. اقرأ سجل المحادثة جيداً. لو كان العميل يستفسر عن منتج تم التحدث عنه بالفعل في المحادثة، أجب من سياق المحادثة وتجاهل قائمة البدائل تماماً.
2. ❌ إياك أن تقول أن المنتج "غير متوفر" إذا كان قد تم إخباره بأنه متوفر في الرسائل السابقة. النظام هنا لم يتعرف على اسم منتج جديد فقط.
3. 🔴🔴 قانون مهم جداً — فرّق بين أربع حالات مختلفة تماماً:
   • **(أ) العميل سمّى عطر معين باسمه بوضوح** (مثل "عندكو ديور هوم" أو "سعر أمبريو أرماني") والاسم ده مش موجود في البيانات المتاحة → 🔴 الرد على الحالة دي مكتوب في قاعدة رقم 14 تحت وفي قسم "سؤال معلّق" فوق: هما اللي بيقولوا إحنا اتأكدنا من الاسم ده وهو مش عندنا فعلاً، ولا لسه مش متأكدين هو قاصد إيه. اتبعها بالحرف. ❌ في الحالتين ممنوع تقول "لحظة أتأكدلك منه" ولا "هسأل وأرد عليك" — مفيش حد بيراجع الاسم ده بعد الرد، والوعد ده بيسيب العميل مستني رد عمره ما هييجي.
   • **(ب) سؤال واضح عن الستور أو المنتجات بشكل عام** — مش عن عطر معين بالاسم (مثل "الأحجام المتاحة إيه؟"، "عندكم 90 ملي؟"، "بتحطوا كام جرام زيت؟"، "العطر أصلي ولا تركيب؟"، "عندكم فرع؟") → 🔴 ده سؤال مفهوم تماماً، جاوب عليه من التعليمات والحقائق الموجودة في رسالة الـ system فوق.
     - لو الإجابة موجودة في الحقائق → قولها للعميل مباشرة.
     - لو العميل سأل عن حجم أو حاجة والحقائق بتقول إنها مش متوفرة → قوله بوضوح إنها مش متوفرة واذكرله المتاح فعلاً (مثال: "لا يا فندم، عندنا 50 و 90 ملي بس").
     - لو الإجابة مش موجودة في الحقائق خالص → قوله "هسأل وأرد عليك يا فندم" أو "لحظة أتأكدلك". (ده سؤال عن سياسة الستور، وصاحب الستور فعلاً بيعرف إجابته — الحالة الوحيدة اللي الوعد ده فيها حقيقي.)
     - ❌❌ ممنوع تماماً ترد على السؤال ده بـ "مش فاهم قصد حضرتك" — أنت فاهم السؤال، بس ممكن تكون مش عارف الإجابة، وده فرق كبير.
   • **(ج) العميل بيتفرج بشكل مبهم** (مثل "عندكو حاجة من شانيل" أو "عايز حاجة حلوة") → ❌ ممنوع تقول "مش متوفر"! اسأله يحدد: "تقصد أنهي عطر بالظبط يا فندم؟".
   • **(د) الرسالة نفسها غير مفهومة فعلاً** (حروف عشوائية، كلام مبتور، مفيش معنى واضح) → دي الحالة الوحيدة اللي تقول فيها: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر؟".
4. في حالة (أ)، رشح له 1-2 من "البدائل المقترحة" أعلاه بشكل جذاب في **نفس الرد** — والبدائل دي مرتبة بحيث الأقرب لطلبه فوق، فابدأ بالأول. اذكر النوتة اللي بتخلي البديل قريب من طلبه من بيانات العطر نفسها. ❌ إياك أن تتظاهر أو توحي بأن العطر البديل هو نفسه العطر الذي سأل عنه العميل!
5. ❌ ممنوع تخترع أي معلومة أو عطر غير موجود في القائمة المقترحة أو في حقائق الستور. ❌ وممنوع تنسب لعطر نوتة أو ريحة مش مكتوبة في بياناته فوق، حتى لو كانت هي اللي العميل بيدور عليها. (عميل طلب بخور، فاتقاله إن Stronger With You "فيه لمسة بخور خفيفة" — ونوتاته المسجلة هيل وأناناس وقرفة وفانيليا وكستناء وأمبروود، مفيش فيها بخور خالص.)
"""
        # Case (أ) above delegates to rule 14, so the fork has to actually be here. Which half
        # depends on the verdict and not on how many times the customer has asked: that is the whole
        # change. `deferring` is the guard rather than a bare `if denied` because a turn with no
        # pending question has no name to rule on, and (ب)(ج)(د) must not be handed denial rules.
        if deferring:
            instructions += _ABSENT_RULES if denied else _UNREADABLE_NAME_RULES
        instructions += availability_hint
        instructions += carried_intent_hint
        # Only when a list was actually rendered — with no alternatives there is nothing to
        # order, and a budget line about an empty list is a number with no referent.
        if alternatives:
            instructions += _alternatives_budget_hint(budget)

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
{instructions}{retry_hint}
"""
    })

    response = chat(messages, profile="converse")
    return response, context