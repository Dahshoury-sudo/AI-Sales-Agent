from .product_resolver import resolve_products
from .product_formatting import format_products
from .ai.client import chat
from .ai.prompts import get_system_prompt
from .fallback import suggest_alternatives


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


def get_product_info(message, history=None, store=None, conversation=None):
    from .sales import naming

    # An explicit name in this message beats anything inferred from earlier turns.
    products = _named_in_message(message, store)

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

    if products:
        context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        # Capped as a prompt-size safety net. The referent branch can now hand over every
        # perfume the last reply named, which is ~2 in practice and bounded by the two-reply
        # window — the limit only guards the pathological case.
        context += format_products(products, limit=6)
        instructions = """
═══ تعليمات صارمة ═══
1. 🔴 لما العطر اللي في البيانات يكون هو نفس العطر اللي العميل سأل عنه، اكتب اسمه بالإملاء الموجود في البيانات — حتى لو العميل غلط في الكتابة أو كتبه بالعربي.
   🔴🔴 لكن لو العميل سمّى عطر والبيانات فيها عطر **تاني خالص**: ده مش غلطة إملائية منه، ده عطر مختلف. ❌ ممنوع تقول إن العطر اللي سأل عنه "اسمه الصحيح" هو العطر اللي في البيانات، وممنوع توحي إنهم نفس العطر أو نفس الريحة أو نفس التركيبة. جاوب على العطر اللي هو سأل عنه، ولو مش معاك بياناته قول "لحظة أتأكدلك منه" زي ما الخط الأحمر رقم 3 بيقول. (عميل سأل عن Acqua di Gio واتقاله "اسمه الصحيح Y Eau de Parfum" — دول عطرين مختلفين من براندين مختلفين، والعميل كرر السؤال واتقاله نفس الكلام تاني.)
1. 🔴 فرّق بين نوع السؤال:
   • لو العميل بيسأل عن التوافر بس (زي "عندكم سوفاج؟" أو "فيه بلو دي شانيل؟" أو "موجود عندكم X؟") → أكّد إنه متوفر **وكمّل في نفس الرد بسؤال يضيّق** (بيدور على أنهي حجم؟). ❌ ممنوع ترد "أه متوفر عندنا" وتسكت — ده رد ميت وبيوقف المحادثة. ❗ ومتبدأش تسرد أسعار ولا ترشح حجم — هو مسألش عن السعر.
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
"""
    else:
        # Product not found, let's get some alternatives. Chosen deterministically and
        # with the customer's gender in mind: `order_by('?')` here offered a women's
        # perfume and a men's perfume side by side to someone whose gender was never
        # established, and made the reply impossible to reproduce.
        from .sales import gender as sales_gender

        alternatives = suggest_alternatives(
            store,
            gender=sales_gender.resolve({}, message, history, store),
        )
        
        context = "═══ تنبيه للنظام ═══\nلم يتم التعرف على اسم منتج محدد في رسالة العميل الأخيرة.\n\n"
        if alternatives:
            context += "═══ بدائل مقترحة متوفرة في المتجر ═══\n"
            context += format_products(alternatives, brief=True)
        
        instructions = """
═══ تعليمات ═══
1. اقرأ سجل المحادثة جيداً. لو كان العميل يستفسر عن منتج تم التحدث عنه بالفعل في المحادثة، أجب من سياق المحادثة وتجاهل قائمة البدائل تماماً.
2. ❌ إياك أن تقول أن المنتج "غير متوفر" إذا كان قد تم إخباره بأنه متوفر في الرسائل السابقة. النظام هنا لم يتعرف على اسم منتج جديد فقط.
3. 🔴🔴 قانون مهم جداً — فرّق بين أربع حالات مختلفة تماماً:
   • **(أ) العميل سمّى عطر معين باسمه بوضوح** (مثل "عندكو ديور هوم" أو "سعر أمبريو أرماني") والعطر مش موجود عندنا → اعتذر بلباقة وقوله إن العطر ده مش متوفر عندنا حالياً، ورشحله 1-2 بديل من القائمة أدناه.
   • **(ب) سؤال واضح عن الستور أو المنتجات بشكل عام** — مش عن عطر معين بالاسم (مثل "الأحجام المتاحة إيه؟"، "عندكم 90 ملي؟"، "بتحطوا كام جرام زيت؟"، "العطر أصلي ولا تركيب؟"، "عندكم فرع؟") → 🔴 ده سؤال مفهوم تماماً، جاوب عليه من التعليمات والحقائق الموجودة في رسالة الـ system فوق.
     - لو الإجابة موجودة في الحقائق → قولها للعميل مباشرة.
     - لو العميل سأل عن حجم أو حاجة والحقائق بتقول إنها مش متوفرة → قوله بوضوح إنها مش متوفرة واذكرله المتاح فعلاً (مثال: "لا يا فندم، عندنا 50 و 90 ملي بس").
     - لو الإجابة مش موجودة في الحقائق خالص → قوله "هسأل وأرد عليك يا فندم" أو "لحظة أتأكدلك".
     - ❌❌ ممنوع تماماً ترد على السؤال ده بـ "مش فاهم قصد حضرتك" — أنت فاهم السؤال، بس ممكن تكون مش عارف الإجابة، وده فرق كبير.
   • **(ج) العميل بيتفرج بشكل مبهم** (مثل "عندكو حاجة من شانيل" أو "عايز حاجة حلوة") → ❌ ممنوع تقول "مش متوفر"! اسأله يحدد: "تقصد أنهي عطر بالظبط يا فندم؟".
   • **(د) الرسالة نفسها غير مفهومة فعلاً** (حروف عشوائية، كلام مبتور، مفيش معنى واضح) → دي الحالة الوحيدة اللي تقول فيها: "مش فاهم قصد حضرتك يا فندم، ممكن توضحلي أكتر؟".
3. بعد الاعتذار (في حالة (أ) فقط)، رشح له 1-2 من "البدائل المقترحة" أعلاه بشكل جذاب. ❌ إياك أن تتظاهر أو توحي بأن العطر البديل هو نفسه العطر الذي سأل عنه العميل!
4. ❌ ممنوع تخترع أي معلومة أو عطر غير موجود في القائمة المقترحة أو في حقائق الستور.
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
        "content": f"""
═══ سؤال العميل ═══
{message}

{context}
{instructions}
"""
    })

    response = chat(messages, profile="converse")
    return response, context