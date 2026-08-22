# -*- coding: utf-8 -*-
"""Egyptian perfume-customer personas used to drive the evaluation.

Each persona carries the *behavioural* traits that matter for grading, not just a
label: how they type (typos, mixed script, one-word messages), what they will and
will not volunteer, and what a good salesperson is supposed to do with them.

The judge sees `expectation` so it grades against sales craft rather than against
one blessed wording.
"""

PERSONAS = {
    "price_sensitive": {
        "name": "محمود - price sensitive",
        "traits": "ميزانية ضيقة، بيسأل عن السعر بدري، بيقارن بأرخص حاجة، بيكتب بسرعة وبأخطاء",
        "expectation": (
            "Must not push the expensive option. Must offer the smaller size as an entry "
            "point, quote real prices, and never apologise for the price or promise a discount."
        ),
    },
    "luxury": {
        "name": "شريف - luxury buyer",
        "traits": "مش بيسأل عن السعر، عايز حاجة فخمة ومميزة ونيش، بيكتب مزيج عربي/انجليزي",
        "expectation": (
            "Should be shown the niche / ultra-niche or store-exclusive tier, with real "
            "differentiators. Should not be treated as budget-constrained."
        ),
    },
    "expert": {
        "name": "كريم - fragrance head",
        "traits": "يعرف النوتات والـ DNA، بيستخدم مصطلحات (ambroxan, dry-down, projection)، بيمسك أي كلام غلط",
        "expectation": (
            "Answers must be technically accurate and sourced from stored notes. Any vague "
            "or invented claim is a hard failure with this persona."
        ),
    },
    "novice": {
        "name": "أحمد - يعرف حاجة عن العطور",
        "traits": "مش عارف يعبر، بيقول 'عايز ريحة حلوة'، رسايل قصيرة جداً",
        "expectation": (
            "Should be asked ONE well-shaped orienting question with concrete choices, not "
            "interrogated. Must not be buried in jargon or in five options."
        ),
    },
    "indecisive": {
        "name": "هاني - محتار",
        "traits": "بيقول 'مش عارف'، بيرجع لخيارات قديمة، بيطلب رأيك",
        "expectation": (
            "The agent should take the choice burden off them: one narrowing question then "
            "pick FOR them. Must not dump more options."
        ),
    },
    "gift_buyer": {
        "name": "مصطفى - بيشتري هدية",
        "traits": "مش عارف ذوق المستلم، عايز حاجة 'آمنة'",
        "expectation": (
            "Must acknowledge the uncertainty, must NOT say مضمون / هتعجبها أكيد, and should "
            "ask the one high-value question (a perfume the recipient already wears)."
        ),
    },
    "comparer": {
        "name": "طارق - بيقارن",
        "traits": "بيحدد عطرين ويسأل مين أحسن/أقوى/أثبت",
        "expectation": (
            "Must differentiate from stored data only and give a decision rule ('if you like "
            "X take this'). Must not close the sale while they are still weighing."
        ),
    },
    "clone_hunter": {
        "name": "عمرو - بيدور على بديل",
        "traits": "بيقول 'عايز حاجة شبه X'، ساعات بيوصف العطر بدل ما يسميه",
        "expectation": (
            "Similarity must be judged on overall scent character, not one shared note. If "
            "nothing is genuinely close the agent must say so."
        ),
    },
    "sweet_lover": {
        "name": "نورا - بتحب المسكر",
        "traits": "عايزة حاجة مسكرة/جورماند، بتستخدم كلمة 'حلو' بمعنى 'جميل' كمان",
        "expectation": (
            "Must distinguish Egyptian 'حلو' = nice from 'مسكر' = sweet. Should land on "
            "actual gourmand notes from the data."
        ),
    },
    "performance_seeker": {
        "name": "إسلام - عايز فوحان وثبات",
        "traits": "أهم حاجة عنده الثبات والفوحان، بيسأل 'بيقعد كام ساعة'",
        "expectation": (
            "Must quote longevity/projection from the product row only, never invent hours, "
            "and must actually rank by the recorded longevity."
        ),
    },
    "longevity_worrier": {
        "name": "سامح - قلقان من الثبات",
        "traits": "خايف العطر يطير، مش واثق في التركيب",
        "expectation": (
            "Acknowledge first, then use only recorded longevity, then reduce risk with "
            "something real (smaller size / visit the store). No guarantees."
        ),
    },
    "authenticity_worrier": {
        "name": "وليد - قلقان من الأصلية",
        "traits": "بيسأل 'ده تقليد؟'، 'أصلي ولا مضروب؟'",
        "expectation": (
            "Must be honest that these are تركيب, may relay only the store's configured "
            "similarity figure, and must not invent a percentage or say مضمون 100%."
        ),
    },
    "angry": {
        "name": "هشام - زعلان",
        "traits": "بيشتم شوية، مستعجل، مضايق من تجربة سابقة",
        "expectation": (
            "Absorb, apologise briefly, stay professional, keep helping. Do NOT hand off "
            "unless they explicitly ask for a human. No blaming the customer."
        ),
    },
    "returning": {
        "name": "ياسر - عميل قديم",
        "traits": "اشترى قبل كده وراضي، بيرجع يجرب حاجة تانية",
        "expectation": (
            "Should build on the stated previous purchase rather than restart discovery."
        ),
    },
    "burned": {
        "name": "عماد - جرب ومش عجبه",
        "traits": "اشترى ترشيح قبل كده وما عجبهوش",
        "expectation": (
            "Must acknowledge, ask ONE diagnostic question about what was wrong, and only "
            "then recommend. Must not immediately fire another recommendation."
        ),
    },
    "ready_buyer": {
        "name": "بلال - جاهز يشتري",
        "traits": "عارف العطر والحجم وعايز يطلب",
        "expectation": "Should move straight into the order without re-discovery.",
    },
    "browser": {
        "name": "دينا - بتتفرج بس",
        "traits": "مش بتشتري دلوقتي، بتسأل عمومي",
        "expectation": (
            "Must recognise low intent, not push, not close. A good salesperson does not "
            "always sell."
        ),
    },
    "hard_budget": {
        "name": "رامي - ميزانية صارمة",
        "traits": "بيقول رقم محدد ومش هيزيد عليه",
        "expectation": (
            "Must never offer a size far above the stated number, and must not re-ask the "
            "budget."
        ),
    },
    "contradictory": {
        "name": "فادي - طلبات متعارضة",
        "traits": "عايز حاجات مستحيلة تتحقق مع بعض (رخيص + نيش + ثبات يومين + مش تقيل)",
        "expectation": (
            "Must surface the conflict honestly and propose the best achievable trade-off. "
            "Must not silently drop a constraint and pretend everything was met."
        ),
    },
    "mind_changer": {
        "name": "زياد - بيغير رأيه",
        "traits": "بيقول حاجة ويرجع فيها في نفس المحادثة",
        "expectation": (
            "The LATEST explicit preference must win over the earlier one. Stale memory "
            "overriding a fresh statement is a hard failure."
        ),
    },
}
