from decimal import Decimal, InvalidOperation

from .client import chat
from ..bottle_wording import bottle_label
from ..product_formatting import format_products, is_variant_available
from ..sales.ranking import reasons_note
from ..sales.value import budget_tier

# Used by the standalone formatting helpers, never by recommendation selection.
_FORMATTING_LIMIT = 12


def recommendation_fit(products, intent, *, message=None, history=None):
    """Selected scent labels and explicitly requested details, with performance once."""
    from ..scent_description import describe_scent
    from ..performance_policy import current_request, description
    from ..sales.recommendation_response import _choice_limit
    limit = _choice_limit(intent)
    selected = list(products)[:limit]
    if message is not None:
        from ..price_reply import requested_product_details
        lines = requested_product_details(selected, message, history=history,
                                          preferred_occasion=(intent or {}).get("occasion"),
                                          description_required=True, include_performance=False)
    else:
        lines = []
        for product in selected:
            style = describe_scent(getattr(product, "scent_tags", []), product.occasion,
                                   preferred_occasion=(intent or {}).get("occasion"))
            lines.append(f"{product.name}: {style}.")
    request = current_request()
    if lines and (request.longevity or request.projection):
        performance = description(request, longevity=request.longevity, projection=request.projection)
        if len(lines) > 1:
            performance = "كل الاختيارات " + performance.replace("ثباته ", "ثباتهم ").replace("فوحانه ", "فوحانهم ")
        lines.append(performance + ".")
    return "\n".join(lines)


def _coerce_budget(value):
    """Turn an LLM-extracted budget into a Decimal, or None if unusable.

    The intent schema asks for a float, but a model can return "500" or "500.0",
    and the old int() call raised ValueError on the latter. Decimal also matches
    ProductVariant.price, so comparisons stay exact — Decimal and float can be
    compared but not multiplied together.
    """
    if value is None:
        return None
    try:
        budget = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return budget if budget > 0 else None


def _format_products(products, max_price=None, ranked=None, brief_for=(),
                     show_value_pick=True):
    """Thin wrapper over the shared renderer, capped for prompt size.

    search_products already applies the LIMIT; the cap here only bounds the
    prompt, so a caller passing an unsliced queryset can't blow up the request.

    When ranking ran, each product's own evidence line is appended to its block. The
    model gets the *reasons* — shared notes, which constraint matched — and never the
    score, because a number in the prompt is how "شبهه بنسبة 95%" gets invented.

    `brief_for` names perfumes the customer has already had described to them. Those render
    through the existing `brief=True` mode — which drops the scent and performance fields —
    and lose the ✅ half of their evidence line. Both are the recital's source material: the
    evidence line said "ثباته بيطابق طلبه | فوحانه أقل من اللي طلبه شوية" on every single
    turn, and instruction 7 asks the model to lean on it, so the same sentence came back four
    times. Prices and sizes stay, so a follow-up can still answer "بكام".

    `show_value_pick=False` keeps those prices while dropping the size verdict. A
    recommendation turn is about which *perfume*, not which *size*, and with three perfumes
    in context the old behaviour handed the model three separate "lead with this size" orders
    at once.

    The ⚠️ mismatch half is deliberately kept. It is a safety signal, not a selling point,
    and it never goes stale — dropping it along with the reasons is what let a reply offer an
    Evening/Formal perfume as "أنسب للنهار في المكتب" on the very turn the warning fired.
    Brief mode omits the Occasion field, so this line is then the only place the recorded
    value appears.
    """
    if not ranked and not brief_for:
        return format_products(
            products, max_price=max_price, limit=_FORMATTING_LIMIT,
            show_value_pick=show_value_pick,
        )

    blocks = []
    for product in list(products)[:_FORMATTING_LIMIT]:
        seen = product.name in brief_for
        block = format_products(
            [product], max_price=max_price, brief=seen,
            show_value_pick=show_value_pick,
        )
        note = reasons_note((ranked or {}).get(product.id), mismatches_only=seen)
        blocks.append(f"{block}{note}\n" if note else block)
    return "".join(blocks)


def _gender_note(gender_unknown):
    """Ask who it is for *inside* the recommendation, not instead of it.

    The router used to block the whole turn on this question. It now only reaches here
    when the customer supplied real taste information but no resolvable gender — a
    lookalike request for a perfume we do not stock, for instance. Answering and asking
    in one reply is what a salesperson does; spending the turn on the question is not.

    This used to also carry "فضّل اللي ينفع للجنسين", which was advice the data did not
    support: `search_products` applies no gender filter when gender is None and
    ranking.py scored `unisex` nowhere, so the shortlist handed over was mixed-gender and
    unordered on that axis. ranking.WEIGHTS["gender_safe"] does the preferring now, and the
    "ينفع للراجل وللست" reason arrives attached to the perfume, so what is left here is only
    how to say it.

    Note what is *not* claimed: that the shortlist leads with a unisex perfume. The weight is
    a tie-break by design and sized below every stated signal, so on a request whose taste
    gaps are wide it changes no order at all — a real "فريش للصيف" query returns eight
    gendered perfumes before the first unisex one. The instruction is therefore "start at the
    top, do not re-filter", which holds either way, and the either-way line is spoken only
    when the perfume's own row carries it.

    The question is folded into the recommending sentence as the reason for narrowing rather
    than appended after it. Trailing it is what made the reply read as a guess followed by an
    afterthought — the customer can see you recommended before you knew.

    Shown as a ✅/❌ pair rather than described, which is the house style at prompts.py:91 and
    was not a stylistic choice here. Described in prose ("والسؤال يجي في نفس الجملة … مش سؤال
    ملحوق في الآخر") the model complied on content and ignored the shape, returning both picks
    correctly labelled either-way and then the question alone after a blank line. Output shape
    is the thing prose instructions carry worst; one example of the line carries it.

    The question is worded "رجالي ولا حريمي" and NOT "هو لمين", which is what it asked first and
    is a mistake worth keeping written down. Folding the question in went too far and made it
    indirect: conversation 910 closed with "قولي هو لمين عشان أظبطلك الاختيار", and the answers
    that invites — "ليا", "لنفسي", "هدية" — carry no gender word, so sales.gender.from_words
    returns None on all three and the next turn has to ask again or guess. A question this
    pipeline cannot parse the answer to is not a question, however natural it reads. These two
    words are the ones the resolver handles most reliably, and they are what prompts.py:131
    already tells the persona to ask.
    """
    if not gender_unknown:
        return ""
    return (
        "\n👤 مش واضح العطر لراجل ولا لست، وعندنا معلومات كفاية نرشح منها:\n"
        "- رشّح حاجة واحدة أو اتنين مناسبين للوصف اللي قاله. البيانات مرتبة بالأنسب "
        "بالفعل — ابدأ من أولها ومتفلترش بنفسك.\n"
        "- لو العطر مكتوب في بياناته \"ينفع للراجل وللست\" — قول ده في نص جملة، دي الحاجة "
        "اللي تخلي الترشيح صح مهما كانت إجابته.\n"
        "- واسأله صريح \"رجالي ولا حريمي\" جوه الجملة نفسها كسبب إنك بتظبط الاختيار، "
        "مش سطر لوحده في الآخر:\n"
        "  ✅ \"Erba Pura حلو ومسكر وينفع للراجل وللست — هو رجالي ولا حريمي عشان أظبطلك "
        "الاختيار؟\"\n"
        "  ❌ \"هو لمين؟\" — سؤال مفتوح ممكن يجاوبه \"ليا\" أو \"هدية\" ونرجع زي ما إحنا.\n"
        "  ❌ ترشيح، وبعده سطر جديد فيه السؤال — ده بيبان إنك رشحت وانت مش عارف.\n"
        "- ❌ ممنوع تسأل السؤال ده لوحده من غير ما ترشح — ده بيضيّع دور العميل.\n"
        "- ❌ وممنوع تقول عن عطر إنه ينفع للاتنين إلا لو مكتوب كده في بياناته.\n"
    )


def _reference_block(search, max_price=None):
    """The row for the perfume the customer asked to be matched against.

    A perfume cannot be its own lookalike, so `search_service` excludes the reference from the
    candidate list — evaluation scenario M1 had Dior Sauvage come back first in its own "شبه
    Sauvage" shortlist, which is both nonsense and a wasted context slot.

    But the customer is plainly talking about it, and the earlier attempt at that exclusion is
    what produced conversation 630: with the row gone from the data, the persona's own red line
    ("do not mention a product absent from your data") turned the gap into an invention —
    "Intensely مش مناسب لميزانيتك" about a 780 EGP perfume on an 800 EGP budget. The data has to
    be here; only the candidacy is withdrawn.

    So the header says both halves out loud, which is the part `_named_but_missing_block` has no
    need to: this perfume is available, *and* it is the thing being compared against rather than
    an answer to the request.
    """
    product = (search or {}).get("reference_product")
    if product is None:
        return ""

    return (
        "\n═══ العطر اللي العميل بيقارن بيه (بيانات حقيقية من قاعدة البيانات) ═══\n"
        + format_products([product], max_price=max_price)
        + f"🔴 {product.name} متوفر عندنا — ❌ ممنوع تقول إنه مش موجود أو مش متوفر، "
        "ولو سأل عنه جاوبه من البيانات دي.\n"
        "🔴 بس ده مرجع المقارنة، مش ترشيح: العميل عايز حاجة *شبهه*، فـ ❌ ممنوع ترشحه "
        "له كإجابة على طلبه. رشّح من القائمة اللي فوق.\n"
    )


def _named_but_missing_block(message, store, products, max_price=None):
    """The row for a perfume the customer named that the shortlist happens to omit.

    The shortlist is filtered and capped at twelve out of a much larger catalogue, so a perfume
    the customer named by name can simply not be in it. Two things then go wrong at once: the
    model has no data to answer with, and the persona's own red line ("do not mention a product
    absent from your data") turns that gap into a denial.

    The extractor is *asked* to keep a named perfume in `similar_to`/`exclude_names`, but a
    prompt is advice. This is the guarantee: if they named it, its row is present. Deliberately
    no polarity inference — the row says nothing about whether they want it, only that it
    exists, which is the part the model cannot make up. Guessing direction from a bare name is
    what produced `avoid_traits: ["heavy"]` for a customer asking *for* a heavy perfume.

    Returns "" when the perfume is already in the shortlist, so nothing is rendered twice.
    """
    named = _named_in_message(message, store)
    if not named:
        return ""

    already = {product.name for product in products}
    missing = [product for product in named if product.name not in already]
    if not missing:
        return ""

    return (
        "\n═══ عطر العميل ذكره بالاسم وهو موجود عندنا (مكانش في القائمة فوق لأنها مختارة، "
        "مش الكتالوج كله) ═══\n"
        + format_products(missing, max_price=max_price)
        + "🔴 ده متوفر. ❌ ممنوع تقول إنه مش موجود أو مش متوفر.\n"
    )


def _named_in_message(message, store):
    """Catalogue perfumes the customer named, matched deterministically.

    Used only on the nothing-found path, to tell apart "no product matched the criteria" from
    "the customer asked for a perfume by name and the shortlist did not happen to contain it".
    The second is not an availability problem and must never be reported as one.

    Latin names only — `naming.tokens` cannot match an Arabic transliteration, so that case
    still relies on the resolver upstream.
    """
    if not message or store is None:
        return []

    from products.models import Product

    from ..sales import naming

    candidates = list(
        Product.objects.filter(store=store, is_active=True)
        .prefetch_related("variants")
        .select_related("brand")
    )
    return naming.mentioned_in(message, candidates)


def _performance_note(products, intent):
    """Explain the shared policy when performance is part of the request."""
    from ..performance_policy import instruction
    if not any((intent or {}).get(key) for key in ("longevity", "projection")) or not list(products):
        return ""
    return instruction()


def _in_budget_note(products, max_price):
    """State outright that affordable sizes exist, when they do.

    A prompt rule was not enough. After the previously-recommended perfumes were
    excluded, the remaining shortlist's brand bottles were all over budget while one
    *original* bottle sat at 326 against a 500 budget — and the reply opened "للأسف
    العطور المتوفرة عندنا كلها فوق الميزانية دي" and then offered the 326 bottle two
    sentences later. Every size already carries a ✅/⚠️/❌ label, so whether an affordable
    option exists is arithmetic; asserting it here removes the room to claim otherwise.
    """
    if not max_price:
        return ""

    affordable = []
    tolerable = []
    cheapest = None
    for product in list(products)[:_FORMATTING_LIMIT]:
        for variant in product.variants.all():
            # Sellable, not merely cheap. Filtering on price alone named Dior Sauvage's
            # original 60ml — which is in budget at 456 and has stock=0 — so the reply
            # offered a bottle that cannot be sold. The same rule has to govern the
            # cheapest-overall figure below, or the one number we sanction is unbuyable.
            if not is_variant_available(variant):
                continue
            label = bottle_label(variant.bottle_type)
            line = f"{product.name} {label} {variant.volume} ملي بـ {variant.price:.0f}"
            if cheapest is None or variant.price < cheapest[0]:
                cheapest = (variant.price, line)
            tier = budget_tier(variant.price, max_price)
            if tier == "in":
                affordable.append(line)
            elif tier == "near":
                tolerable.append(line)
    if not affordable and tolerable:
        # The middle case, which used to fall into the "nothing is in budget" branch below and
        # get the single-figure treatment meant for an all-❌ shortlist. These sizes are ⚠️:
        # over the stated number but inside the tolerance band, which `budget_label` already
        # tells the model it may offer. Saying "nothing here is in your budget" about them and
        # then permitting exactly one price is both untrue and the reason a well-matched
        # perfume lost to a badly-matched cheaper one in conversation 757.
        return (
            "\n⚠️ مفيش حجم داخل الميزانية بالظبط في القائمة دي، بس دي أعلى منها حاجة بسيطة "
            "ومسموح تعرضها: "
            + "، ".join(tolerable[:4])
            + ".\n🔴 اعرض منها اللي الأنسب لطلب العميل، وقول سعره المكتوب وقول \"أعلى حاجة "
            "بسيطة من ميزانيتك\" بالحرف — ❌ ممنوع تذكر الفرق بكام ولا تحسبه. ❌ وممنوع تقول "
            "\"مفيش حاجة في الميزانية\" وتسكت، وممنوع ترشح عطر مش مناسب لطلبه بس لأنه أرخص.\n"
        )
    if not affordable:
        # Naming the figure rather than asking for it, for the same reason the affordable
        # branch does — and because *not* naming it is what produced a critical finding.
        #
        # Evaluation scenario X3: budget 300, every size in the shortlist labelled
        # "❌ (أعلى من الميزانية بكتير — ممنوع تعرضه)". The model was told never to present a ❌
        # size and, one line later, to present the nearest size with its price difference. Both
        # cannot be obeyed when every size is ❌, and the way out it found was a number it made
        # up: "الـ50 ملي بتاعهم فوق 590 جنيه" — no such price exists in the catalogue.
        #
        # So the contradiction is resolved explicitly, in favour of the one true figure: this
        # single price is carved out of the ❌ ban, and every other number is forbidden outright.
        gap = (
            f"\n🔴 أرخص حجم متاح في القائمة دي هو {cheapest[1]} جنيه، وده الرقم الوحيد "
            "المسموح تذكره من القائمة دي رغم إنه فوق الميزانية — قوله بصراحة كده عشان "
            "العميل يعرف الفرق الحقيقي ويقرر.\n"
            "❌ ممنوع تقرّب أو تخمّن أو تقول \"فوق كذا\" برقم مش مكتوب في البيانات.\n"
            if cheapest else "\n"
        )
        return (
            "\n🔴 مفيش أي حجم في القائمة دي داخل ميزانية العميل. قوله كده بصراحة عن "
            "العطور اللي بتعرضها. ❌ ممنوع تعمم على الستور كله.\n"
            + gap
        )
    return (
        "\n✅ فيه أحجام داخل ميزانية العميل في القائمة دي: "
        + "، ".join(affordable[:4])
        + ".\n🔴 ❌ ممنوع تقول \"كل العطور فوق الميزانية\" أو \"مفيش حاجة في الميزانية\" — "
        "ده غلط، وفوق كده مكتوب الأحجام اللي داخلها. ابدأ بواحد منهم.\n"
    )


def recommend(message, products, history=None, alternatives=None, store=None, intent=None, search=None,
              gender_unknown=False, repeat_hint=""):
    """Select eligible catalog IDs and write a grounded scent-label recommendation."""
    from .scent_recommendation import recommend_from_labels
    return recommend_from_labels(message, products, history, alternatives, store, intent, search,
                                 gender_unknown, repeat_hint, chat=chat)
