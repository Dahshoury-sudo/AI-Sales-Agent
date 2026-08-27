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
    """The perfume we just offered, when the customer's message names none itself.

    Every `product_info` message of that shape is a question *about the perfume under
    discussion* — "بكام؟", "ريحته عاملة ايه؟", "فيه أحجام تانية؟", "متأكد؟" — so the perfume we
    led with is the subject, and the LLM resolver has nothing to anchor on. In conversation
    1099 it anchored on the wrong thing: "مش متوفر متأكد ؟" about Versace Eros resolved to
    Dior Sauvage and Lattafa Asad from two turns earlier, and the reply was only correct
    because the model happened to read Eros's prices out of the history rather than out of the
    injected data.

    Resolved from `Message.internal_context` rather than prose, so a perfume named while being
    withdrawn is never the referent.

    Returns [] unless a conversation is present and something is genuinely under discussion —
    that gate is what keeps this off the first turn of a conversation and out of the callers
    that pass no conversation at all.
    """
    if conversation is None or store is None:
        return []

    from .sales import described as sales_described

    try:
        offered = sales_described.offered_in_order(conversation, store)
    except Exception:
        return []
    if not offered:
        return []

    from products.models import Product

    return list(
        Product.objects.filter(store=store, is_active=True, name=offered[0])
        .prefetch_related("variants")
        .select_related("brand")
    )


def get_product_info(message, history=None, store=None, conversation=None):

    # An explicit name in this message beats anything inferred from earlier turns.
    products = _named_in_message(message, store)

    # Nothing named here: the subject is whatever we just offered. Decided in Python because
    # the resolver demonstrably gets this wrong, and `internal_context` is a harder record
    # than an 8-message prose window.
    if not products:
        products = _referent_from_conversation(message, store, conversation)

    if not products:
        products = resolve_products(message, history, store, conversation)

    if products:
        context = "═══ بيانات المنتجات الحقيقية من قاعدة البيانات ═══\n"
        context += format_products(products)
        instructions = """
═══ تعليمات صارمة ═══
1. 🔴 استخدم دائماً الاسم الصحيح للعطر الموجود في البيانات حتى لو أخطأ العميل في كتابته.
1. 🔴 فرّق بين نوع السؤال:
   • لو العميل بيسأل عن التوافر بس (زي "عندكم سوفاج؟" أو "فيه بلو دي شانيل؟" أو "موجود عندكم X؟") → أكّد إنه متوفر **وكمّل في نفس الرد بخطوة تقرّب البيعة**: إما تقوله سعر الحجم اللي في سطر 💡 Value Pick، أو تسأله سؤال يضيّق (بيدور على أنهي حجم؟ لنفسه ولا هدية؟). ❌ ممنوع ترد "أه متوفر عندنا" وتسكت — ده رد ميت وبيوقف المحادثة.
   • لو العميل سأل عن السعر أو الحجم صراحة (زي "بكام؟" أو "الأحجام إيه؟") → 🔴 ابدأ بالحجم اللي في سطر 💡 Value Pick ورشّحه بالأرقام اللي فيه، وبعدها اذكر باقي الأحجام في نص جملة. ❌ ممنوع تسرد الأسعار كلها في صف واحد زي فاتورة.
   • لو العميل سأل سؤال تفصيلي (زي "إيه مكوناته؟" أو "ثباته إيه؟") → جاوب على اللي سأله بس في جملة طبيعية واحدة أو اتنين. مثال: "ثباته حوالي 8-10 ساعات، وفوحانه قوي خصوصاً أول كام ساعة."
2. لو العميل سأل عن السعر، ابدأ بترشيح حجم الـ 💡 Value Pick بالفرق بالأرقام، وبعدها اذكر باقي الأحجام المتاحة باختصار في نص جملة عشان يعرف إن فيه خيارات. ❌ ممنوع تغير أي سعر، وممنوع تخفي إن فيه أحجام تانية.
3. لو العميل سأل عن الحجم أو الملي، اذكر كل الأحجام المتاحة كما هي مكتوبة بالظبط.
4. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة) في كلام طبيعي.
5. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
6. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌) أو حجم معين نفد، أخبر العميل بذلك بشكل لطيف واقترح عليه إنه يسأل عن عطور تانية متوفرة أو اعرض عليه الأحجام المتوفرة إن وجدت.
7. 🔴🔴 ادمج المعلومات في كلام طبيعي. ❌ ممنوع تسرد المواصفات في شكل قائمة جامدة (الثبات: ... / الفوحان: ... / الموسم: ...). العميل بيتكلم مع بياع مش قاعدة بيانات.
8. 🔴 متحشرش معلومات العميل مسألش عنها. جاوب على اللي اتسأل بس.
9. 🔴 ممنوع أسئلة فاضية (زي "عايز حاجة تانية؟" أو "تحب تعرف الأسعار والأحجام المتاحة؟"). مسموح بـ CTA بيعي ذكي بس مش كل مرة (زي "تحب تطلب؟" أو "تنورنا في الستور تجرب؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
10. 🔴🔴 الزجاجة الأوريجينال: لو العميل سأل عن زجاجة أوريجينال، اقرأ خانة (Original Bottle) في بيانات المنتج وقول الرد المكتوب فيها بالحرف. ❌ ممنوع تقول على أي عطر إنه "حصري للمتجر" إلا لو مكتوب في الـ Brand بتاعه "عطر تركيب حصري خاص بالمتجر".
11. 🔴 الفرق بين زجاجة البراند والأوريجينال: البرفان (التركيبة) واحد بالظبط. الفرق الوحيد شكل الزجاجة. فرق السعر بسبب تكلفة الزجاجة مش جودة البرفان. ❌ ممنوع توحي إن الأوريجينال فيها تركيبة أحسن.
12. 🔴 لو العميل بيسأل يجيب 50 ملي ولا 90 ملي: ساعده يختار من نفس العطر. لو أول مرة → الأصغر أأمن. لو عاجبه → الأكبر أوفر. ❌ ممنوع تبدل العطر.
13. 🔴🔴 ممنوع تحسب إجمالي طلب. البيانات اللي فوق فيها أسعار الأحجام بس — مفيش فيها عربة ولا كميات ولا إجمالي. ❌ ممنوع تضرب سعر في كمية، وممنوع تجمع أسعار، وممنوع تقول "الإجمالي" أو "المجموع" أو تتكلم عن "الطلبين" أو أي عدد قطع. لو العميل سأل الطلب بقى بكام، قوله إنك هتراجع الطلب معاه وابدأ تجمع تفاصيله — الإجمالي بيتحسب من الطلب نفسه مش من الأسعار دي. (عميل اتقاله إجمالي 1560 جنيه لطلب مش موجود، وهو أصلاً قال إن ميزانيته 900.)
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
4. بعد الاعتذار (في حالة (أ) فقط)، رشح له 1-2 من "البدائل المقترحة" أعلاه بشكل جذاب. ❌ إياك أن تتظاهر أو توحي بأن العطر البديل هو نفسه العطر الذي سأل عنه العميل!
5. ❌ ممنوع تخترع أي معلومة أو عطر غير موجود في القائمة المقترحة أو في حقائق الستور.
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