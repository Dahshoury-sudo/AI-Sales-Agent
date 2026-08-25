# -*- coding: utf-8 -*-
"""Evaluation scenarios: realistic Egyptian perfume-shopping conversations.

Deliberately not random. Each scenario targets a behaviour the agent claims to have,
and the multi-turn ones exist to attack a specific seam (memory override, stale
preference, constraint accumulation, stage progression).

`turns` is the sequence of customer messages. `probe` records what a senior perfume
salesperson would consider a correct handling of THIS conversation — it is handed to
the judge so grading is against sales craft, not against one blessed wording.

Typos, Egyptian slang, mixed script and missing information are intentional
throughout. Real customers do not type cleanly.
"""

SCENARIOS = [
    # ─────────────────────────────── DISCOVERY ───────────────────────────────
    {
        "id": "D1",
        "category": "discovery",
        "persona": "novice",
        "turns": ["عايز عطر حلو"],
        "probe": (
            "Customer said essentially nothing. Correct move: ONE short orienting question "
            "with concrete choices (fresh vs warm vs sweet) plus budget, in a single message. "
            "Must NOT recommend a perfume yet. Must NOT ask a chain of separate questions."
        ),
    },
    {
        "id": "D2",
        "category": "discovery",
        "persona": "novice",
        "turns": ["ازيك", "بصراحه مش فاهم حاجه في العطور، بس عايز حاجه تعجب الناس"],
        "probe": (
            "Novice with zero vocabulary. Correct move: reassure, then ask one question they "
            "CAN answer. Must not use jargon (notes/accords/projection) and must not list 5 options."
        ),
    },
    {
        "id": "D3",
        "category": "discovery",
        "persona": "browser",
        "turns": ["بتبيعوا ايه بالظبط؟", "لا انا بس بتفرج مش هشتري دلوقتي"],
        "probe": (
            "Second turn is an explicit low-intent signal. Correct move: accept it gracefully, "
            "leave the door open, and STOP selling. Any closing question or fresh recommendation "
            "after 'مش هشتري دلوقتي' is a sales failure."
        ),
    },

    # ────────────────────────── RECOMMENDATION / CONSTRAINTS ──────────────────────────
    {
        "id": "R1",
        "category": "recommendation",
        "persona": "hard_budget",
        "turns": ["عايز عطر رجالي في حدود 600 جنيه"],
        "probe": (
            "Budget + gender given. Correct move: recommend 1-2 real perfumes with real prices "
            "at or under 600 (a size slightly over may be mentioned with a caveat). Must NOT ask "
            "for the budget again. Must NOT offer a size far above 600."
        ),
        "assert_budget": 600,
    },
    {
        "id": "R2",
        "category": "recommendation",
        "persona": "performance_seeker",
        "turns": [
            "عايز برفان رجالي ريحته فخمه وثابته، مناسب للخروجات بالليل، بس مش عايز حاجه تقيله تخنق اللي حواليا"
        ],
        "probe": (
            "Four constraints stated (male, luxurious, long-lasting, evening, NOT heavy/suffocating). "
            "Correct move: acknowledge briefly and RECOMMEND — the constraint count is enough. "
            "Blocking the whole turn on a bare budget question is the not-listening failure. "
            "The recommendation must not be a heavy oud/leather bomb, since heaviness was excluded."
        ),
    },
    {
        "id": "R3",
        "category": "recommendation",
        "persona": "sweet_lover",
        "turns": ["عايزه عطر حريمي مسكر اوي زي الحلويات، وميزانيتي 700"],
        "probe": (
            "Explicit gourmand request with budget. Correct move: land on perfumes whose recorded "
            "notes are actually gourmand (vanilla/praline/coffee/tonka), priced within 700. "
            "Recommending a fresh or purely floral perfume here is a recommendation-quality failure."
        ),
        "assert_budget": 700,
    },
    {
        "id": "R4",
        "category": "recommendation",
        "persona": "luxury",
        "turns": ["عايز حاجه نيش مش منتشره، ومش مهم السعر"],
        "probe": (
            "Luxury + uncommon. Correct move: show the niche/ultra-niche tier or the store's own "
            "exclusive blends, with a real differentiator. Must NOT say 'مفيش' while niche products "
            "exist. Must NOT drag them down to a cheap option."
        ),
    },
    {
        "id": "R5",
        "category": "recommendation",
        "persona": "expert",
        "turns": ["عايز حاجه فيها ambroxan و lavender، رجالي، للشغل"],
        "probe": (
            "Expert naming actual molecules. Correct move: match on the recorded notes and be "
            "technically precise. Any invented note or vague 'fresh and elegant' filler is a "
            "hard failure with this persona."
        ),
    },

    # ─────────────────────────────── SIMILARITY ───────────────────────────────
    {
        "id": "S1",
        "category": "similarity",
        "persona": "clone_hunter",
        "turns": ["عايز حاجه شبه سوفاج"],
        "probe": (
            "Sauvage IS in the catalogue, so its real notes are known (bergamot/mandarin/lavender/"
            "ambroxan/vanilla). Correct move: offer something that genuinely shares that fresh-"
            "ambroxan character and name the shared notes as evidence. A heavy oud, a leather, or a "
            "gourmand is NOT similar to Sauvage no matter how the reply words it. Sharing one note "
            "is not similarity. No similarity percentage may be quoted."
        ),
    },
    {
        "id": "S2",
        "category": "similarity",
        "persona": "clone_hunter",
        "turns": ["عندك حاجه زي 9pm بتاع افنان؟"],
        "probe": (
            "9PM is in the catalogue (apple/cinnamon/vanilla/tonka/amber — sweet spicy). Correct "
            "move: a sweet-warm lookalike with named shared notes, or an honest 'nothing really "
            "close' followed by the nearest option labelled as different."
        ),
    },
    {
        "id": "S3",
        "category": "similarity",
        "persona": "clone_hunter",
        "turns": ["عايز حاجه شبه Baccarat Rouge بس مش هي"],
        "probe": (
            "Explicit alternative request, so BR540 itself must be excluded from the answer. "
            "Correct move: nearest genuine match on scent character with evidence, or honest "
            "admission that nothing is close."
        ),
        "assert_excludes": ["Baccarat Rouge 540"],
    },
    {
        "id": "S4",
        "category": "similarity",
        "persona": "clone_hunter",
        "turns": ["عايز عطر زي Lattafa Khamrah"],
        "probe": (
            "Khamrah is NOT in this catalogue, so the reference comes from general knowledge "
            "(dates/cinnamon/vanilla/tonka — sweet boozy gourmand). Correct move: be explicit that "
            "the comparison rests on the known profile of a perfume we do not stock, and either "
            "offer a genuine sweet-spicy match or admit nothing is close. Must NOT imply we sell it."
        ),
    },

    # ─────────────────────────────── COMPARISON ───────────────────────────────
    {
        "id": "C1",
        "category": "comparison",
        "persona": "comparer",
        "turns": ["ايه الفرق بين بلو دي شانيل وسوفاج؟"],
        "probe": (
            "Both are stocked. Correct move: differentiate on stored data (Bleu = citrus/mint/"
            "incense/cedar, All Seasons, office/formal; Sauvage = bergamot/ambroxan/vanilla, "
            "daily/office/dates) and give a decision rule. Must not invent differences and must "
            "not close the sale while they are still weighing."
        ),
    },
    {
        "id": "C2",
        "category": "comparison",
        "persona": "comparer",
        "turns": ["مين اثبت، اوداورا ولا سافرانو؟"],
        "probe": (
            "Both are store-exclusive blends with recorded longevity (Oudora 12 hours, Safrano "
            "11 hours). Correct move: answer the actual question from recorded longevity. Refusing "
            "to answer, or inverting the recorded values, is a failure."
        ),
    },
    {
        "id": "C3",
        "category": "comparison",
        "persona": "gift_buyer",
        "turns": ["انهي احسن كهديه لمراتي، Good Girl ولا La Vie Est Belle؟"],
        "probe": (
            "Gift framing on a comparison. Correct move: differentiate the two on stored data and "
            "recommend one with a reason tied to the recipient. Must not say either is مضمون."
        ),
    },

    # ─────────────────────────────── OBJECTIONS ───────────────────────────────
    {
        "id": "O1",
        "category": "objection",
        "persona": "price_sensitive",
        "turns": ["عايز عطر رجالي ثابت", "ده غالي اوي، مش معايا الفلوس دي"],
        "probe": (
            "Price objection after a recommendation. Correct move: acknowledge the price is real "
            "money WITHOUT apologising for it, convert to value using real numbers (per-ml, size, "
            "recorded longevity), and offer the smaller size as an entry point. Must NOT promise "
            "a discount, must NOT re-pitch the same perfume unchanged, and must NOT close."
        ),
    },
    {
        "id": "O2",
        "category": "objection",
        "persona": "longevity_worrier",
        "turns": [
            "جبت من عندكم عطر قبل كده وكان مكتوب ثابت 8 ساعات، وبعد ساعتين مش بحسه. خايف اطلب تاني"
        ],
        "probe": (
            "This is a COMPLAINT about a past purchase, not a sales objection. Correct order: "
            "acknowledge the let-down FIRST, then address longevity from recorded data only, then "
            "reduce risk with something real. Must NOT blame the customer's skin or usage, must NOT "
            "promise a refund/replacement/discount, must NOT open with an explanation or an upsell, "
            "and must NOT close the sale."
        ),
    },
    {
        "id": "O3",
        "category": "objection",
        "persona": "authenticity_worrier",
        "turns": ["ده تقليد مضروب صح؟ انا مش واثق"],
        "probe": (
            "Authenticity doubt. Correct move: be straight that these are عطور تركيب, relay ONLY "
            "the store's own configured similarity figure, and reassure from real store facts. "
            "Must NOT invent a match percentage and must NOT say مضمون 100%."
        ),
    },
    {
        "id": "O4",
        "category": "objection",
        "persona": "price_sensitive",
        "turns": ["ليه ادفع 1200 في فهرنهايت وانا ممكن اجيب حاجه ب 500؟"],
        "probe": (
            "Price-gap objection naming a real price difference. Correct move: answer with the "
            "REAL recorded differences between the two tiers only, and explicitly allow that the "
            "cheaper one may be the better fit for them. Must not manufacture a differentiator."
        ),
    },
    {
        "id": "O5",
        "category": "objection",
        "persona": "indecisive",
        "turns": ["عايز عطر للسهرات", "طيب هفكر وارجعلك"],
        "probe": (
            "Soft 'I'll think about it'. Correct move: accept gracefully, no pressure, no repeat "
            "of the same recommendation, at most one concrete real hook. Any push to order here is "
            "a trust failure."
        ),
    },

    # ─────────────────────────────── GIFT ───────────────────────────────
    {
        "id": "G1",
        "category": "gift",
        "persona": "gift_buyer",
        "turns": ["عايز هديه لمراتي بس معرفش بتحب ايه بصراحه"],
        "probe": (
            "Gift with unknown recipient taste. Correct move: acknowledge honestly that we are "
            "narrowing not guaranteeing, ask the ONE high-value question (name a perfume she "
            "already wears), and optionally offer one safe-not-guaranteed option. The words "
            "مضمون / هتعجبها اكيد / الاتنين مضمونين are hard failures."
        ),
    },
    {
        "id": "G2",
        "category": "gift",
        "persona": "gift_buyer",
        "turns": ["هديه لاخويا الصغير، شاب 22 سنه، ميزانيه 500"],
        "probe": (
            "Gift with age + budget + inferable male gender. Correct move: infer male from "
            "'لاخويا/شاب', respect 500, and recommend something young and versatile. Must NOT ask "
            "whether it is for a man or a woman — that is inferable and re-asking reads as not listening."
        ),
        "assert_budget": 500,
    },

    # ─────────────────────────────── PURCHASE ───────────────────────────────
    {
        "id": "P1",
        "category": "purchase",
        "persona": "ready_buyer",
        "turns": ["سوفاج بكام؟"],
        "probe": (
            "Direct price question on a stocked perfume. Correct move: lead with the value-pick "
            "size and its real price, mention the other sizes briefly, and move toward the next "
            "step. Prices must match the database exactly. A bare price list with no recommendation "
            "is a weak sales answer."
        ),
    },
    {
        "id": "P2",
        "category": "purchase",
        "persona": "ready_buyer",
        "turns": ["عايز اطلب امبيرو 90 ملي", "اسمي بلال حسن، 01012345678، ورقم تاني 01198765432", "العنوان مدينة نصر، شارع عباس العقاد، عماره 12 الدور 3"],
        "probe": (
            "Clear purchase with details supplied across turns. Correct move: collect what is "
            "missing without re-asking what was already given, then show a summary WITH the total "
            "before any confirmation. Prices and total must be arithmetically correct from the DB."
        ),
    },
    {
        "id": "P3",
        "category": "purchase",
        "persona": "ready_buyer",
        "turns": ["هاخد اودورا", "خليها 2 بدل واحده"],
        "probe": (
            "Quantity change mid-order. Correct move: carry the perfume, apply the new quantity, "
            "ask only for what is genuinely still missing. Must not lose the perfume or duplicate it."
        ),
    },

    # ─────────────────────────── CUSTOMER SERVICE ───────────────────────────
    {
        "id": "CS1",
        "category": "customer_service",
        "persona": "angry",
        "turns": ["انتو نصابين، الاوردر بقاله اسبوع ومجاني", "عايز اكلم حد مسؤول حالا"],
        "probe": (
            "Delivery complaint escalating to an explicit request for a human. Correct move: "
            "absorb the anger, apologise briefly and professionally, and on the SECOND turn hand "
            "off once (the request is explicit). Must not blame the customer, must not invent a "
            "delivery status or compensation."
        ),
    },
    {
        "id": "CS2",
        "category": "customer_service",
        "persona": "burned",
        "turns": ["العطر اللي رشحتوه ليا مش عاجبني خالص"],
        "probe": (
            "Rejected recommendation. Correct move: acknowledge, ask ONE diagnostic question "
            "(too heavy? too sweet? want something else entirely?) and only THEN recommend. Firing "
            "another recommendation immediately reads as flailing."
        ),
    },

    # ─────────────────────── MEMORY / MULTI-TURN OVERRIDE ───────────────────────
    {
        "id": "M1",
        "category": "memory",
        "persona": "mind_changer",
        "turns": [
            "بحب سوفاج",
            "بس مش عايز حاجه منتشره",
            "وبالمناسبه ميزانيتي 700",
            "بس اهم حاجه الثبات",
        ],
        "probe": (
            "The canonical accumulation test. By turn 4 the ACTIVE requirement set is: male, "
            "Sauvage-like, uncommon, <=700, longevity is the top priority. Correct move: the final "
            "reply must respect all four — especially longevity, which was just made the top "
            "priority — and must not re-ask the budget or the gender. Dropping or contradicting an "
            "earlier constraint is a memory failure."
        ),
        "assert_budget": 700,
    },
    {
        "id": "M2",
        "category": "memory",
        "persona": "mind_changer",
        "turns": [
            "عايزه عطر حريمي فريش للصيف",
            "لا غيرت رايي، عايزه حاجه تقيله للشتا",
        ],
        "probe": (
            "Explicit reversal. The LATEST preference (heavy, winter) must win outright. If the "
            "reply still pushes a fresh summer perfume, stale memory has overridden a fresh "
            "explicit statement — a hard failure."
        ),
    },
    {
        "id": "M3",
        "category": "memory",
        "persona": "price_sensitive",
        "turns": [
            "عايز عطر رجالي ميزانيتي 500",
            "مش بحب العود خالص",
            "طيب ايه اللي عندك؟",
        ],
        "probe": (
            "Budget then a negative preference then a vague follow-up. Correct move: the third "
            "turn must still honour BOTH the 500 budget and the oud exclusion without re-asking "
            "either. Recommending an oud-heavy perfume here is a hard failure."
        ),
        "assert_budget": 500,
    },
    {
        "id": "M4",
        "category": "memory",
        "persona": "returning",
        "turns": [
            "انا اشتريت منكم امبيرو قبل كده وعجبني جدا",
            "عايز حاجه تانيه في نفس الجو",
        ],
        "probe": (
            "Returning happy customer. Ambero is stocked (cinnamon/pepper/amber/vanilla/musk/"
            "sandalwood — warm spicy amber). Correct move: treat Ambero as the reference and offer "
            "something in the same warm-spicy family, EXCLUDING Ambero itself. Restarting discovery "
            "from scratch wastes what they told us."
        ),
        "assert_excludes": ["Ambero"],
    },

    # ─────────────────────── ADVERSARIAL / EDGE CASES ───────────────────────
    {
        "id": "X1",
        "category": "adversarial",
        "persona": "novice",
        "turns": ["عندكم عطر اسمه بلاك اوركيد؟"],
        "probe": (
            "Named perfume that is NOT in the catalogue. Correct move: say plainly it is not "
            "available and offer 1-2 real alternatives. Must NOT claim it is available, must NOT "
            "silently answer about a different perfume as if it were the one asked for."
        ),
    },
    {
        "id": "X2",
        "category": "adversarial",
        "persona": "novice",
        "turns": ["عايز ديور سيفاج و بلو دى شنيل و ايروس، بكام التلاته؟"],
        "probe": (
            "Three misspelled perfume names in one message. Correct move: resolve all three to "
            "the database spellings (Dior Sauvage, Bleu de Chanel, Eros) and quote real prices for "
            "each. Silently dropping one is an entity-resolution failure."
        ),
    },
    {
        "id": "X3",
        "category": "adversarial",
        "persona": "contradictory",
        "turns": ["عايز عطر نيش ثباته يومين وفواح جدا بس مش تقيل وميزانيتي 300"],
        "probe": (
            "Mutually unsatisfiable: niche + 2-day longevity + huge projection + not heavy + 300 "
            "EGP. Correct move: surface the conflict honestly and propose the best achievable "
            "trade-off, naming which constraint has to give. Silently dropping constraints and "
            "presenting something as though it met all of them is a trust failure. Inventing a "
            "'ثبات يومين' claim is a hard failure."
        ),
        "assert_budget": 300,
    },
    {
        "id": "X4",
        "category": "adversarial",
        "persona": "novice",
        "turns": ["مش فاكر اسم البرفان، الزجاجه سودا والريحه فيها فانيليا وحاجه حلوه وثابته"],
        "probe": (
            "Identification from vague clues. Bottle colour is NOT stored, so it cannot count as "
            "evidence. Correct move: hedge proportionally ('غالبًا X' at best, never 'ده X'), name "
            "only the clues that actually matched stored data, and ask exactly ONE narrowing "
            "question. Must not try to close a sale on this turn."
        ),
    },
    {
        "id": "X5",
        "category": "adversarial",
        "persona": "novice",
        "turns": ["asdkjh asd؟؟"],
        "probe": (
            "Genuine gibberish. Correct move: one short 'مش فاهم قصد حضرتك، ممكن توضح؟'. Must NOT "
            "joke, must NOT recommend a perfume, must NOT invent a product."
        ),
    },

    # ─────────────────── HELD OUT (no fix was tuned against these) ───────────────────
    # F1 attacks the seam the rest of the suite structurally cannot reach: a budget stated
    # during CHECKOUT. _over_budget_warning (order_service.py:203-217) compares each
    # item["price"] against the budget and never the cart total, so two individually
    # in-budget lines can assemble a cart at double the stated number in silence. P2/P3
    # never state a budget, and extract_intent runs only on the recommendation branch
    # (router.py:226-231), so merged_intent is null on every order turn and the
    # reasked_budget / reasked_gender checks cannot fire there either.
    {
        "id": "F1",
        "category": "purchase",
        "persona": "hard_budget",
        "turns": [
            "عايز عطر رجالي ميزانيتي 900",
            "تمام هاخد ده، وضيف كمان واحد للهدية",
            "خليه 90 ملي بدل الـ50",
            "الاجمالي بقى كام؟",
        ],
        "probe": (
            "A hard 900 budget, then a second line added, then a size upgrade. Correct move: "
            "the running TOTAL must be checked against the 900, not each line separately. When "
            "the second item or the size upgrade pushes the cart over 900, say so plainly and "
            "offer a real way back under (smaller size, drop one). The final total must be "
            "arithmetically correct from the database and must reflect the LATEST size only. "
            "Silently assembling a 1700+ cart against a stated 900 is a trust failure, and "
            "re-asking the budget it was already given is a memory failure."
        ),
        "assert_budget": 900,
    },
]

assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS), "duplicate scenario id"
MULTI_TURN = [s for s in SCENARIOS if len(s["turns"]) > 1]
