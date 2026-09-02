from decimal import Decimal, InvalidOperation

from .client import chat
from .prompts import get_system_prompt
from ..product_formatting import format_products, is_variant_available
from ..sales import described
from ..sales.constraints import acknowledgement_hint
from ..sales.ranking import reasons_note
from ..sales.value import budget_tier
from ..search_service import MAX_PRODUCTS_IN_CONTEXT


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
            products, max_price=max_price, limit=MAX_PRODUCTS_IN_CONTEXT,
            show_value_pick=show_value_pick,
        )

    blocks = []
    for product in list(products)[:MAX_PRODUCTS_IN_CONTEXT]:
        seen = product.name in brief_for
        block = format_products(
            [product], max_price=max_price, brief=seen,
            show_value_pick=show_value_pick,
        )
        note = reasons_note((ranked or {}).get(product.id), mismatches_only=seen)
        blocks.append(f"{block}{note}\n" if note else block)
    return "".join(blocks)


def _similarity_instruction(search):
    """Tell the model how close we actually got, when similarity was asked for.

    This is the honesty path for "عايز حاجة شبه X". Presenting the nearest perfume as a
    match is exactly what produced Fahrenheit as an answer for Sauvage, so when nothing
    reaches the close band the reply has to say so.
    """
    summary = (search or {}).get("similarity")
    if not summary:
        return ""

    name = summary["reference_name"]
    if summary["has_close_match"]:
        return (
            f"\n🔎 العميل طلب حاجة شبه {name}. سطر Match في بيانات كل عطر بيقولك إيه "
            f"المشترك بينهم بالظبط — اعتمد عليه واذكر النوتات المشتركة دي بالاسم عشان "
            f"كلامك يكون مبني على حقيقة.\n"
            f"❌ ممنوع تذكر نسبة مئوية للتشابه ولا تقول \"مطابق\" أو \"نفس العطر\".\n"
        )

    return (
        f"\n🔎 العميل طلب حاجة شبه {name}، ومفيش عندنا عطر قريب منه فعلاً.\n"
        f"- 🔴 قوله كده بصراحة: مفيش حاجة قريبة من {name} بالظبط عندنا.\n"
        f"- ⚠️ ده استثناء من قاعدة \"ممنوع تقول مفيش\": القاعدة دي عن التوافر، وهنا "
        f"الكلام عن التشابه. مسموح — بل لازم — تقول إن مفيش حاجة شبهه، وبعدها تكمّل "
        f"وتعرض المتاح عادي.\n"
        f"- اعرض أقرب المتاح كـ\"مختلف بس ممكن يعجبك\"، ووضّح الفرق في جملة.\n"
        f"- ❌ ممنوع تقول عن أي عطر إنه شبهه أو بديله أو نفس ريحته.\n"
    )


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
    """Pin the lead recommendation to the ranking, and make it quote the recorded figure.

    Evaluation scenario M1, on the turn the customer said "بس اهم حاجه الثبات": the ranker put
    Ambero (10 hours) first and the reply recommended Dark Aura (8 hours) instead, describing
    both as "وثباتهم كويس" without quoting either figure. Two separate failures — the ranking
    was right and the prose ignored it, and a performance claim was asserted rather than sourced.

    Computed here rather than asked for in the persona because which product is first is a fact
    about the list we just built, the same reason _in_budget_note exists.
    """
    axes = [key for key in ("longevity", "projection") if intent.get(key)]
    if not axes:
        return ""

    shortlist = list(products)[:MAX_PRODUCTS_IN_CONTEXT]
    if not shortlist:
        return ""

    leader = shortlist[0]
    labels = {"longevity": "الثبات", "projection": "الفوحان"}
    asked = " و".join(labels[key] for key in axes)
    recorded = " / ".join(
        f"{labels[key]}: {(getattr(leader, key, '') or '').strip() or 'غير مسجل'}"
        for key in axes
    )

    return (
        f"\n🔴 العميل سأل عن {asked}. القائمة تحت مرتبة بالأنسب لطلبه، و**{leader.name}** هو "
        f"الأول فيها ({recorded}). رشحه هو الأساس. لو رشحت غيره قبله، لازم تقول السبب صراحة "
        f"من بيانات العطر. ❌ ممنوع تقول \"ثباته كويس\" أو \"ثباتهم عالي\" كده — اذكر الرقم "
        f"المسجل زي ما هو من بيانات العطر.\n"
    )


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
    for product in list(products)[:MAX_PRODUCTS_IN_CONTEXT]:
        for variant in product.variants.all():
            # Sellable, not merely cheap. Filtering on price alone named Dior Sauvage's
            # original 60ml — which is in budget at 456 and has stock=0 — so the reply
            # offered a bottle that cannot be sold. The same rule has to govern the
            # cheapest-overall figure below, or the one number we sanction is unbuyable.
            if not is_variant_available(variant):
                continue
            label = (
                "زجاجة أوريجينال" if variant.bottle_type == "original" else "زجاجة البراند"
            )
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
            "\n⚠️ مفيش حجم داخل الميزانية بالظبط في القائمة دي، بس دي أعلى منها شوية بس "
            "ومسموح تعرضها: "
            + "، ".join(tolerable[:4])
            + ".\n🔴 اعرض منها اللي الأنسب لطلب العميل وقول سعره الحقيقي والفرق بالرقم المكتوب "
            "جوه علامة الـ ⚠️ بتاعته. ❌ ممنوع تقول \"مفيش حاجة في الميزانية\" وتسكت، وممنوع "
            "ترشح عطر مش مناسب لطلبه بس لأنه أرخص.\n"
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


def recommend(message, products, history=None, alternatives=None, store=None, intent=None, search=None, gender_unknown=False):
    # Not repeating a recommendation is handled upstream, not here: ai/intent.py fills
    # intent["exclude_names"] when the customer asks for something else, and
    # search_service drops those from the queryset before this function ever sees it.
    # A prompt-level exclusion list used to live here as well, keyed on every catalogue
    # name mentioned anywhere in the conversation — which excluded the perfume the
    # customer had just asked about, contradicting the persona's own rule to stay on it.
    # Deleted rather than narrowed: a hard queryset filter beats asking the model twice.
    max_price = _coerce_budget(intent.get("max_price") if intent else None)
    budget_note = ""
    if max_price:
        budget_note = f"\n⚠️ ميزانية العميل: {int(max_price)} جنيه. اذكر الأسعار والأحجام اللي داخل الميزانية، واللي أعلى منها شوية (⚠️) كمان لو هي الأنسب لطلبه. متسألوش عن الميزانية تاني.\n"
        budget_note += "🔴 ملاحظة هامة جداً بخصوص الميزانية والأحجام: إذا طلب العميل حجماً معيناً (مثل 90 ملي) وكان سعره أعلى من ميزانيته شوية (⚠️)، ❌ ممنوع تتجاهله وترشح عطر تاني مش مناسب لطلبه عشان حجمه أرخص. اعرض عليه الحجم اللي طلبه بسعره الحقيقي وقول الفرق بالرقم المكتوب جانبه في العلامة، واعرض معاه الحجم الأصغر اللي داخل ميزانيته، وسيبه هو يقرر — من غير أي ضغط أو إلحاح.\n"
        # The ✅ half of this rule is the one conversation 912 was missing.
        #
        # Everything above, plus persona rules prompts.py:103-104, describes what to say when a
        # size is over budget: offer it anyway if it fits best, quote the real price, say by how
        # much it exceeds the number. Five separate instructions on that case and, until this
        # sentence, not one on what a ✅ obliges — so the label was authoritative in the
        # arithmetic and merely suggestive in the prose, and the model occasionally applied the
        # over-budget script to a size that was inside the budget.
        #
        # Conversation 912, budget 1200: "La Vie Est Belle 90 ملي بـ1046 جنيه أعلى شوية ⚠️ عن
        # ميزانيتك". 1046 is in budget, its price line said "✅ (داخل الميزانية)", and
        # _in_budget_note had *also* named that exact size in its affordable list. Two
        # affirmations already pointed the right way and were overridden, which is why this is
        # phrased as a prohibition instead of a third one: the model was not missing the fact,
        # it was reaching for a script that had no stated stopping condition.
        #
        # This prohibition alone was not enough, and that is worth recording because the prose
        # reads convincing. Measured on the failing turn: ~1 in 6 replies before it, still 2 in 44
        # after, and both survivors invented an overage figure ("بـ 124 جنيه", "بـ 126 جنيه")
        # against a real difference of 154 in the other direction. The model was doing budget
        # arithmetic rather than reading a verdict sales.value.budget_tier had already computed.
        #
        # What closed it was removing the arithmetic to do: `budget_label` now writes the overage
        # into the ⚠️ label itself and every instruction here points at that number instead of
        # asking for one, so a ✅ line has no figure to quote and the request is unfillable rather
        # than forbidden. That is the same move `_in_budget_note` made for evaluation scenario X3
        # below — name the figure rather than ask for it — and the reason both were needed is that
        # a ban only removes the permission, while this removes the gap that invited it.
        #
        # "مهما كان سعره قريب من الرقم" names the specific trap. A tolerance band exists
        # (value.BUDGET_TOLERANCE), so "close to the budget" is a real category in the data — but
        # it is ⚠️, and the model does not get to decide a ✅ price is close enough to count.
        budget_note += "🔴 كل حجم في البيانات اللي تحت مكتوب جانبه إذا كان داخل الميزانية (✅) أو أعلى منها شوية (⚠️) أو أعلى منها بكتير (❌). العلامة دي محسوبة وهي الحكم الوحيد على الميزانية — متحسبهاش بنفسك. التزم بده حرفياً: ممنوع تعرض أي حجم عليه ❌، والـ ⚠️ مسموح تعرضه بشرط تقول الفرق بالرقم المكتوب جوه العلامة نفسها.\n"
        budget_note += "🔴 وحجم عليه ✅ يبقى داخل الميزانية خلاص، مهما كان سعره قريب من الرقم اللي قاله: ❌ ممنوع تقول عنه إنه \"أعلى شوية\" ولا \"أعلى من ميزانيتك\"، وممنوع تحسبله فرق — مفيش فرق أصلاً، ومفيش رقم فرق مكتوب جانبه. ده بيخلي العميل يفتكر إنه مش قادر على حاجة هو قادر عليها فعلاً.\n"
    price_instruction = "🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله." if not max_price else "🔴🔴 العميل حدد ميزانيته، فلازم تذكر الأحجام والأسعار مع الترشيح — اللي داخل ميزانيته واللي أعلى منها شوية (⚠️) كمان. اذكر السعر بشكل طبيعي جوه الكلام (مثال: \"الـ50ml بـ 400 جنيه، يعني داخل ميزانيتك\"). لو الحجم الأنسب لطلبه عليه ⚠️، رشّحه وقول سعره والفرق بالرقم المكتوب جوه العلامة، واذكر معاه حجم داخل الميزانية. ❌ ممنوع تختار عطر مش مناسب لطلبه بس لأنه أرخص. متسألوش عن الميزانية تاني."

    # What the customer already told us, so the reply can nod to it once instead of
    # answering five stated constraints as though none had registered.
    constraint_note = acknowledgement_hint(intent or {})
    gender_note = _gender_note(gender_unknown)

    # What we already told *them*. A perfume the customer has had described once must not be
    # described again — conv_990 re-pitched the same one four times, three of those replies
    # opening identically, because nothing tracked this.
    seen = described.already_described(history, store)
    follow_up = described.is_follow_up(message, history, seen)
    repeat_note = described.repeat_ban_hint(seen, follow_up)
    # What the conversation is on, and what a new constraint has just ruled out. The ranking
    # boost alone is not enough: it puts the right perfume in front of the model, but nothing
    # stops the model presenting a fresh pair alongside it. `converge` is the same follow_up
    # signal, so on an answer to our own question the reply names exactly one.
    continuity = described.continuity_note(
        (search or {}).get("keeping") or (),
        (search or {}).get("dropped") or (),
        converge=follow_up,
    )
    # Only shorten what was already covered, and only on a follow-up. A fresh request still
    # gets the full block and the evidence line for everything, including a perfume mentioned
    # earlier — the customer asking anew deserves the detail.
    brief_for = seen if follow_up else frozenset()
    # Rule 4 used to be an unconditional "ممنوع ترشح لو مش واضح رجالي ولا حريمي". That
    # now contradicts the router, which reaches this function with an unresolved gender
    # only when the customer *has* given usable taste information — so the instruction
    # has to flip with it rather than veto the recommendation the router just decided to
    # make. The "متدورش بنفسك" half matters too: WEIGHTS["gender_safe"] has already leaned the
    # order safe wherever taste left room, and a model re-filtering by eye undoes that.
    #
    # It says "مرتبة بالأنسب" and not "مقدّمة اللي ينفع للجنسين", which is what it said first,
    # because the second claim is only sometimes true. The hedge is a tie-break: on a request
    # whose taste gaps are wide it changes nothing, and a shortlist can legitimately hold no
    # unisex perfume at all. Telling the model the data is always ordered that way is a claim
    # it can catch us out on, and the instruction it actually needs — start at the top, do not
    # re-filter — is true either way.
    gender_instruction = (
        "🔴 مش واضح رجالي ولا حريمي، بس العميل قال تفاصيل كفاية — رشّح من المتاح "
        "(البيانات مرتبة بالأنسب بالفعل، متدورش بنفسك) واسأله في نفس الجملة "
        "\"رجالي ولا حريمي\" عشان تظبط الاختيار. ❌ مش \"هو لمين؟\" — سؤال مفتوح إجابته "
        "مش بتحدد جنس."
        if gender_unknown else
        "🔴 لو الطلب عام ومش واضح رجالي ولا حريمي — ممنوع ترشح. اسأله الأول. لو واضح من السياق (قال \"لخطيبتي\") رشّح على طول."
    )
    # Instruction 7 tells the model to lean on the "✅ ليه مناسب" line for its stated reason.
    # Correct on a first recommendation; on a follow-up it is precisely what made the bot
    # recite "ثابت وفوحانه متوسط" four times, since that line is re-injected every turn.
    reasons_instruction = (
        "سطر \"✅ ليه مناسب\" هو دليلك لاختيار العطر لنفسك — ❌ مش كلام تقوله للعميل تاني، هو "
        "سمعه خلاص. وسطر \"⚠️ مش مطابق في\" لازم تحترمه: ❌ ممنوع تقول إن العطر بيطابق حاجة "
        "مكتوب جانبها إنه مش مطابق فيها."
        if follow_up else
        "سطر \"✅ ليه مناسب\" في بيانات كل عطر هو الدليل اللي بنيت عليه الترشيح — اعتمد عليه في سبب الترشيح بدل كلام عام. وسطر \"⚠️ مش مطابق في\" لازم تحترمه: ❌ ممنوع تقول إن العطر بيطابق حاجة مكتوب جانبها إنه مش مطابق فيها."
    )

    # Case 1: Exact matches found
    if products.exists():
        context = _format_products(
            products, max_price=max_price,
            ranked=(search or {}).get("ranked"), brief_for=brief_for,
            # Only when a budget is on the table. Without one, `price_instruction` below
            # forbids mentioning prices or sizes at all, so injecting a line that orders the
            # model to lead with a size put two opposite instructions in one request — the
            # same defect ComparisonSuppressesPricesTests pinned for comparison mode.
            show_value_pick=bool(max_price),
        )
        context += _named_but_missing_block(message, store, products, max_price)
        context += _reference_block(search, max_price)
        user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}{_in_budget_note(products, max_price)}{constraint_note}{gender_note}{repeat_note}{continuity}{_similarity_instruction(search)}{_performance_note(products, intent)}
═══ المنتجات المتاحة (هذه هي المنتجات الوحيدة الموجودة — لا تذكر أي منتج خارج هذه القائمة) ═══
{context}

═══ تعليمات الرد ═══
1. اختر أفضل 1-2 منتج بيطابق شروط العميل كلها مع بعض. لو مفيش منتج بيطابق كل الشروط، ماترشحوش. 🔴 الترتيب اللي فوق مرتّب بالمطابقة لطلب العميل، فابدأ من أوله. **الاختيار بيكون بالمطابقة بس — مش بسطر 💡.** سطر "💡 Value Pick" / "💡 اقتراح حجم" بيقولك أنهي *حجم* تبدأ بيه جوه العطر بعد ما تختاره، وعطر مالوش سطر 💡 مش أقل مناسبة — ده بس معناه إن أحجامه مفيهاش فرق في سعر الملي. ❌ ممنوع تنزل تحت في الترتيب وتختار عطر أقل مطابقة عشان هو اللي عليه سطر 💡.
2. 🔴 لو رشحت عطرين، لازم تقارن بينهم في جملة واحدة سريعة تساعده يختار، **وترجّح واحد بالاسم** وتقول ليه: "أنا أرشحلك كذا أكتر لطلبك 👌". مثال: "Ambero أنسب لو بتحب التوابل والريحة الدافية، أما Afnan 9PM فهو أحلى ومسَكّر أكتر وفيه طابع فاكهي." ❌ ممنوع تسيبه بين خيارين من غير ترجيح — ده بيرجّع القرار لعميل سألك عشان تساعده يقرر، ومهم بشكل خاص لو الطلب هدية أو لو هو محتار.
3. {price_instruction}
4. {gender_instruction}
5. 🔴 تجاهل تماماً أي منتج Stock Status = ❌ واختار غيره من المتوفر.
6. 🔴 لو العميل محدد ميزانية ولقيت عطر ممتاز أرخص بكتير منها، رشحه كـ"قيمة مقابل سعر" — متفضلش الأغلى لمجرد إنه بيقفل الميزانية.
7. {reasons_instruction}
8. 🔴 القائمة اللي فوق هي مجموعة مختارة من العطور، مش كل الستور. ❌ ممنوع تعمل تعميم على المتجر كله زي "كل العطور أغلى من كده" أو "مفيش حاجة في الميزانية دي" — أنت شايف جزء بس، وممكن تكون رشحت للعميل حاجة أرخص في رسالة قبل كده. اتكلم عن العطور اللي قدامك بس.
"""

    # Case 2: No exact match, but we have alternatives (e.g. higher price)
    elif alternatives and alternatives.exists():
        context = _format_products(
            alternatives, max_price=max_price,
            ranked=(search or {}).get("ranked"), brief_for=brief_for,
            show_value_pick=bool(max_price),  # same reasoning as case 1
        )
        context += _reference_block(search, max_price)
        price_instruction_alt = "🔴🔴 ممنوع تذكر الأسعار أو الأحجام في الترشيح! اذكر اسم العطر وليه يناسبه بس. لما العميل يسأل عن السعر أو الحجم، ساعتها بس قوله." if not max_price else "🔴🔴 العميل حدد ميزانيته، فلازم تذكر الأحجام والأسعار اللي داخل أو قريبة من ميزانيته مع الترشيح. لو السعر أعلى من الميزانية، وضّح ذلك بصراحة. متسألوش عن الميزانية تاني."
        user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}{_in_budget_note(alternatives, max_price)}{constraint_note}{gender_note}{repeat_note}{continuity}{_similarity_instruction(search)}
═══ ملحوظة مهمة ═══
لم يتم العثور على تطابق 100% مع طلب العميل، ولكن المنتجات التالية هي أفضل وأقرب البدائل المتاحة لطلبه:

{context}

═══ تعليمات الرد ═══
1. ❌ ممنوع تبدأ بـ"للأسف" أو تسيب العميل بإيد فاضية. البائع الماهر يركز على بيع المتاح.
2. ادخل في الموضوع فوراً ورشّح أفضل 1-2 من القائمة.
3. 🔴 لكن ممنوع توهمه إنهم بيطابقوا كل شروطه. سطر "⚠️ مش مطابق في" في بيانات كل عطر بيقولك الشرط اللي مش متحقق — لازم تقول الفرق ده بصراحة في نص جملة (مثال: "ثباته 8 ساعات، أقل من اللي طلبته، بس هو أقرب حاجة عندنا"). ❌ ممنوع تقول إن الثبات أو الفوحان "مناسب لطلبك" لو مكتوب إنه مش مطابق.
4. 🔴 لو شرط من شروطه مستحيل يتحقق مع الباقي (زي ثبات يومين بميزانية صغيرة)، قوله كده بصراحة وقوله أنهي شرط لازم يتنازل عنه شوية — ده بيبني ثقة أكتر من إنك تبيعه حاجة وهو متوقع حاجة تانية.
5. 🔴 لو رشحت عطرين، قارن بينهم في جملة واحدة سريعة تساعده يختار.
6. {price_instruction_alt}
7. 🔴 تجاهل أي منتج Stock Status = ❌ ورشّح المتوفر بس.
"""

    else:
        # Before reporting nothing found, check whether the customer simply named a perfume
        # the shortlist happened to miss. The shortlist is a filtered, capped selection of a
        # 47-product catalogue, so "not in my data" and "not in the store" are different
        # statements — and this branch used to tell the customer the second one. Conversation
        # 1099: "ليه مرشحتش versace eros" got "مش متوفر عندنا حالياً" while Eros sat in the
        # catalogue at 1019 جنيه.
        named = _named_in_message(message, store)
        if named:
            context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
            context += format_products(named, max_price=max_price)
            user_content = f"""
═══ طلب العميل ═══
{message}
{budget_note}

═══ ملحوظة مهمة ═══
العميل سأل عن عطر بالاسم، والعطر ده **موجود عندنا** وبياناته تحت. الشورت ليست اللي جالك قبل كده كانت مجموعة مختارة مش الكتالوج كله، فمجرد إنه مكانش فيها لا يعني إنه مش متوفر.

═══ تعليمات الرد ═══
1. 🔴 جاوب على سؤاله عن العطر ده من البيانات تحت. ❌ ممنوع تقول إنه مش متوفر أو مش موجود.
2. 🔴 لو كان بيسأل ليه مرشحتهوش قبل كده، اعتذر في نص جملة قصيرة وقوله البيانات بتاعته، وخلاص. ❌ ممنوع تدخل في تفاصيل عن طريقة اختيارك للترشيحات.
3. {price_instruction_alt}
4. 🔴 تجاهل أي حجم Stock Status = ❌ واذكر المتوفر بس.
"""
        else:
            context = ""
            user_content = f"""
═══ طلب العميل ═══
{message}

═══ ملحوظة مهمة ═══
لم يتم العثور على أي منتجات تطابق طلب العميل في المتجر حالياً، أو تم ترشيح كل الخيارات المتاحة بالفعل.

═══ تعليمات الرد ═══
1. ⭐ إذا كان العميل يطلب المزيد من الخيارات (مثل "إيه تاني؟"، "غيره"، "في حاجة تانية")، اعتذر بلطف وقوله إن دي كل الخيارات المتاحة حالياً اللي بتطابق طلبه بالظبط، واعرض عليه يغير المواصفات بشكل عام عشان يظهرله عطور تانية. ❌ ممنوع تقترح عليه روائح محددة (زي "تحب حاجة خشبية؟" أو "فريش") لأنك لا تعرف ما هو متوفر في المخزون حالياً.
2. ⭐ إذا كانت رسالة العميل غامضة أو غير مفهومة، لا تعتذر عن عدم توفر العطر، بل قل له بوضوح: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر عشان أقدر أساعدك؟".
3. أما إذا كان يطلب عطراً بالاسم وهو مش في البيانات اللي معاك: ❌ ممنوع تقول إنه "مش متوفر" أو "مش موجود عندنا" — إنت شايف جزء من الكتالوج بس. ✅ قول "لحظة أتأكدلك منه" واسأله لو يحب ترشحله بديل في نفس الجو. ممنوع تجزم بعدم التوفر إلا لو البيانات نفسها بتقول كده.
4. ❌ ممنوع ترشيح أو ذكر أي منتج غير موجود أو اختراع أسماء منتجات.
5. رد بشكل قصير ومباشر (1-4 جمل).
6. 🔴🔴 متسألش أسئلة كتير. سؤال واحد بس لو محتاج توضيح، ومتسألش سؤال متابعة لو الموقف مش محتاج.
"""

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
        "content": user_content,
    })

    response = chat(messages, profile="converse")
    return response, context