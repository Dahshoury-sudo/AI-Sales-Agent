# Bug reproduction conversations

Database: `AIAgent` on localhost. Store: **Perfamix (ID 1)**.
Checkout: `8ec2e69cfe26459135fe7dde5ef946fe4ddc1756`. Date: 2026-10-06.

**21 reproduced cases; 2 control conversations where the targeted claim did not reproduce.**

All conversations remain in the database. Their `platform_sender_id` starts with `BUG-REPRO-`; evaluation notes identify the setup, result and evidence. Test orders are labelled `DO NOT FULFILL`.

L cases used the configured live models and real router without canned model replies. C/D cases exercised the real application handlers with the specific fault or timing condition stated below. E01 is a deliberately wrong reply supplied to the real evaluator, not a live model hallucination.

The database still has migrations through 0041. The runner supplied neutral defaults for newer columns only in its own Python model metadata, leaving the application code and database schema as found. Newer database constraints therefore remain active. Existing catalog prices and stock were verified against the pre-run snapshot and match.

## Coverage of the nine problem areas

| Problem area | Conversations |
|---|---|
| Cart items disappearing or changing | 1170 (L01), 1173 (L02) |
| Premature or duplicate confirmation | 1174 (L03), 1182 (C01), 1183 (C02), 1184 (C03) |
| Stock/cancellation and committed-order notification errors | 1186 (C05), 1185 (C04) |
| Recommendations ignoring requirements | 1171 (L04), 1175 (L05), 1176 (L06) |
| Incomplete or unsupported product answers | 1172 (L07), 1176 (L06), 1177 (L08, control), 1192 (L13, control) |
| Lost context, preferences, and FAQ misrouting | 1178 (L09), 1179 (L10), 1180 (L11), 1181 (L12) |
| Duplicate, out-of-order or uncertain message delivery | 1187 (D01), 1188 (D02), 1189 (D03) |
| Missing staff replies in web chat | 1190 (D04) |
| Evaluator missing a wrong variant price | 1191 (E01) |

The catalog has 40 active products, so the former 60-product cutoff was not exercised with synthetic catalog rows. Test-environment database/network isolation is infrastructure behavior and was not represented as a customer conversation. The simple last-bottle oversell claim is not asserted here; this checkout already rechecks original-bottle stock under a row lock. C05 demonstrates the separate stock-restoration defect.

## Saved conversations

| ID | Case | Result | Problem |
|---:|---|---|---|
| 1170 | L01 | reproduced | Multiple unsized items lose quantity or the second item |
| 1171 | L04 | reproduced | Total budget for two bottles treated as a per-bottle budget |
| 1172 | L07 | reproduced | A question about two products and several dimensions is only partly answered |
| 1173 | L02 | reproduced | An unresolved product disappears from the cart |
| 1174 | L03 | reproduced | Confirmation combined with a quantity correction |
| 1175 | L05 | reproduced | Requested size and exact budget not enforced during recommendation |
| 1176 | L06 | reproduced | Unsupported relative sweetness recommendation |
| 1177 | L08 | not reproduced | Original bottle confused with verified designer formulation |
| 1178 | L09 | reproduced | An explicit false preference is replaced by the old true value |
| 1179 | L10 | reproduced | Changing recipient retains the previous recipients preferences |
| 1180 | L11 | reproduced | The second recommended product is forgotten after intervening FAQs |
| 1181 | L12 | reproduced | An FAQ intercepts contact details needed by checkout |
| 1182 | C01 | reproduced | Price changed after quote is confirmed without a new summary |
| 1183 | C02 | reproduced | The string false is accepted as order confirmation |
| 1184 | C03 | reproduced | Two concurrent confirmations create two orders for the same cart |
| 1185 | C04 | reproduced | A notification failure reports order failure after the order has committed |
| 1186 | C05 | reproduced | Reopening and cancelling a cancelled order restores stock twice |
| 1187 | D01 | reproduced | A repeated web request with the same client message ID is processed twice |
| 1188 | D02 | reproduced | Concurrent requests reply out of order and read incomplete history |
| 1189 | D03 | reproduced | A task retry resends a reply after delivery acknowledgement is lost |
| 1190 | D04 | reproduced | Web chat cannot retrieve a staff reply during handoff |
| 1191 | E01 | reproduced | Evaluator accepts another products genuine price as the requested products price |
| 1192 | L13 | not reproduced | Excluding store-exclusive products makes ordinary blends sound like designer originals |

## Conversation 1170 — L01

Multiple unsized items lose quantity or the second item

**Result:** reproduced. **Setup:** live model, actual router.

Requested 2 Leatherio and 3 Vanilo. The first cart stored only Leatherio as pending, with no quantities. After choosing Leatherio 90ml, the reply showed just 1 Leatherio and omitted Vanilo. Turn 3 recovered the original quantities from still-visible history; the earlier loss remains visible.

**Inspect turn 2.**

**Customer:**

> خلي Leatherio 90 مل.

**Actual reply:**

> 🛍️ الطلب لحد دلوقتي:
> - 1 × Leatherio (90ml) بـ 1075 جنيه
> المجموع: 1075 جنيه.
> 
> تمام، عشان أأكدلك الطلب ناقصني بس الاسم و رقمين للموبايل واحد اساسي وواحد بديل و عنوانك بالتفصيل (المحافظة - المنطقة - رقم المنزل - اسم الشارع ) لو فى أي علامة مميزة بجوار المنزل.

[Full transcript, state and diagnostic evidence](L01.json)


## Conversation 1171 — L04

Total budget for two bottles treated as a per-bottle budget

**Result:** reproduced. **Setup:** live model, actual router.

The customer explicitly limited BOTH 90ml bottles to a combined 1000 EGP. The bot offered bottles at 700 and 780 EGP, then built a 1480 EGP cart and falsely said the 1000 EGP limit had been for one perfume.

**Inspect turn 2.**

**Customer:**

> اختارلي الاتنين وقولي السعر النهائي للاتنين مع بعض، الحد الأقصى ألف جنيه.

**Actual reply:**

> 🛍️ الطلب لحد دلوقتي:
> - 1 × Stronger With You (90ml) بـ 700 جنيه
> - 1 × Stronger With You Intensely (90ml) بـ 780 جنيه
> المجموع: 1480 جنيه.
> 
> ⚠️ للعلم: إجمالي الطلب 1480 جنيه. الرقم اللي قلته (1000 جنيه) كان لعطر واحد، والطلب فيه أكتر من عطر — فالإجمالي أعلى من الميزانية دي، مش عشان عطر فيهم غالي. لو مش مقصود، أقدر أشيل حاجة أو أنزل حجم أصغر.
> 
> تمام، عشان أأكدلك الطلب ناقصني بس الاسم و رقمين للموبايل واحد اساسي وواحد بديل و عنوانك بالتفصيل (المحافظة - المنطقة - رقم المنزل - اسم الشارع ) لو فى أي علامة مميزة بجوار المنزل.

[Full transcript, state and diagnostic evidence](L04.json)


## Conversation 1172 — L07

A question about two products and several dimensions is only partly answered

**Result:** reproduced. **Setup:** live model, actual router.

The customer explicitly named both Dior Sauvage and Bleu de Chanel and asked for price, longevity, projection and price per ml. The bot answered only Sauvage, asked the customer to type Bleu de Chanel again, and omitted the requested value comparison.

**Inspect turn 1.**

**Customer:**

> قارنلي Dior Sauvage وBleu de Chanel: سعر 50 مل من زجاجة البراند لكل واحد كام، وثبات وفوحان كل واحد إيه، وأنهي واحد أوفر في سعر الملي؟

**Actual reply:**

> Dior Sauvage 50 ملي بـ642 جنيه، و90 ملي بـ944 جنيه، وفيه كمان زجاجات أوريجينال 100 و200 ملي. ثباته حوالي 8-10 ساعات وفوحانه قوي خصوصاً أول كام ساعة.
> 
> بالنسبة لـ Bleu de Chanel، ممكن تكتب اسمه عشان أتأكد من تفاصيله وأسعاره عندنا؟

[Full transcript, state and diagnostic evidence](L07.json)


## Conversation 1173 — L02

An unresolved product disappears from the cart

**Result:** reproduced. **Setup:** live model, actual router.

Mystery Azure was requested alongside Vanilo. Only Vanilo was saved and recapped; the unresolved name never became a pending item, even after the customer explicitly asked to retain it.

**Inspect turn 1.**

**Customer:**

> عايز أطلب Vanilo 50 مل زجاجة البراند وواحد كمان اسمه Mystery Azure 50 مل. الاتنين في نفس الطلب.

**Actual reply:**

> 🛍️ الطلب لحد دلوقتي:
> - 1 × Vanilo (50ml) بـ 607 جنيه
> المجموع: 607 جنيه.
> 
> تمام، عشان أأكدلك الطلب ناقصني بس الاسم و رقمين للموبايل واحد اساسي وواحد بديل و عنوانك بالتفصيل (المحافظة - المنطقة - رقم المنزل - اسم الشارع ) لو فى أي علامة مميزة بجوار المنزل.

[Full transcript, state and diagnostic evidence](L02.json)


## Conversation 1174 — L03

Confirmation combined with a quantity correction

**Result:** reproduced. **Setup:** live model, actual router.

The only quote showed 1 Vanilo at 607 EGP. The customer said to confirm but change the quantity to 2. The next reply confirmed order 66 at 1214 EGP without showing a revised summary for approval.

**Inspect turn 2.**

**Customer:**

> تمام أكد الطلب بس خلي الكمية اتنين بدل واحدة.

**Actual reply:**

> تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #66.
> سيقوم فريق المبيعات بالتواصل معك قريباً.
> 
> 📌 لتأكيد وشحن الأوردر برجاء تحويل جزء من المبلغ (عربون لا يقل عن ٢٥٠ج) والباقي عند الاستلام، أو تحويل المبلغ كاملاً.
> ⚠️ في حالة إلغاء الأوردر بعد تأكيده لا يتم استرداد العربون لأنه بيكون اتحضر وخرج لشركة الشحن.
> 
> 💳 طرق التحويل:
> إنستاباي: https://ipn.eg/S/perfamix2/instapay/3dFdnw
> (اضغط الرابط لإرسال نقود إلى perfamix2@instapay)
> 
> برجاء إرسال سكرين شوت بالتحويل هنا فور الانتهاء لتأكيد الشحن.

[Full transcript, state and diagnostic evidence](L03.json)


## Conversation 1175 — L05

Requested size and exact budget not enforced during recommendation

**Result:** reproduced. **Setup:** live model, actual router.

The customer said 90ml, at most 900 EGP, and explicitly refused even one extra pound. The bot still offered Dior Sauvage 90ml at 944 EGP. It disclosed the overrun but had no permission to offer an upgrade.

**Inspect turn 1.**

**Customer:**

> عايز عطر رجالي للشغل، زجاجة البراند 90 مل، ومعايا 900 جنيه بالظبط ومش هقدر أزود جنيه. رشحلي اختيارين بس بالحجم ده وسعر كل واحد.

**Actual reply:**

> عندي ليك اختيارين مناسبين للشغل وبحجم 90 ملي:
> 
> 🔹 Stronger With You زجاجة البراند 90 ملي بـ 700 جنيه، ريحته دافية ومناسبة للخريف والشتا، وثباته حوالي 6 ساعات.
> 
> 🔹 Dior Sauvage زجاجة البراند 90 ملي بـ 944 جنيه، أعلى حاجة بسيطة من ميزانيتك، ريحته منعشة وقوية ومناسبة للاستخدام اليومي والشغل.
> 
> أنا أرشحلك Stronger With You أكتر لطلبك لأنه داخل الميزانية تمام وريحته مناسبة للشغل.

[Full transcript, state and diagnostic evidence](L05.json)


## Conversation 1176 — L06

Unsupported relative sweetness recommendation

**Result:** reproduced. **Setup:** live model, actual router.

The bot made unsupported relative-sweetness claims and contradicted itself in the same answer: it first called Black Opium less sweet than Bleu de Chanel, then described Bleu de Chanel as less sweet. This is a live model response, not an injected fixture.

**Inspect turn 2.**

**Customer:**

> أنهي واحد أقل حلاوة فعلاً؟ وقولي على أساس إيه حكمت إنه أقل حلاوة.

**Actual reply:**

> Black Opium أقل حلاوة من Bleu de Chanel، لأن Black Opium فيه نوتة قهوة وفانيليا واضحة، لكن الحلاوة فيها متوازنة مع نوتات الفلفل والزهور، أما Bleu de Chanel ريحته أنظف وأقل حلاوة بفضل وجود الحمضيات والنعناع في البداية.
> 
> بالنسبة للأسعار، الـ90 ملي من Black Opium بـ883 جنيه والـ50 ملي بـ677 جنيه، والـ90 ملي من Bleu de Chanel بـ1015 جنيه والـ50 ملي بـ645 جنيه. تحب أظبطلك طلب أي حجم منهم؟

[Full transcript, state and diagnostic evidence](L06.json)


## Conversation 1177 — L08

Original bottle confused with verified designer formulation

**Result:** not reproduced. **Setup:** live model, actual router.

The targeted authenticity confusion did not reproduce in this run. The store FAQ correctly said the perfumes are blends and that original-looking bottles can contain those blends. Kept as a control conversation, not counted as a reproduced bug.

**Inspect turn 2.**

**Customer:**

> أنا بسأل عن السائل نفسه: هل أصلي مصنع ديور ولا تركيب؟ هل الزجاجة الأوريجينال لوحدها دليل إن العطر أصلي؟

**Actual reply:**

> العطور بتاعتنا كلها "عطور تركيب" (مستوحاة من الماركات العالمية) بخامات زيوت عالية الجودة جداً وتطابق بيوصل لـ 95%، وبنقدملك نفس ريحة الأوريجينال بسعر مميز جداً. ومتاح لبعض العطور نركبها في زجاجات لأوريجينال بالظبط!

[Full transcript, state and diagnostic evidence](L08.json)


## Conversation 1178 — L09

An explicit false preference is replaced by the old true value

**Result:** reproduced. **Setup:** live model, actual router.

The customer withdrew the uncommon requirement: “مش لازم يكون نادر، عادي لو منتشر”. The persisted preferences still have wants_uncommon=true. The reply also called normal-bottle prices “original perfumes”; that secondary wording issue is not counted as a separate reproduction here.

**Inspect turn 2.**

**Customer:**

> خلاص مش لازم يكون نادر، عادي لو منتشر، رشحلي حاجة معروفة من ديور أو شانيل.

**Actual reply:**

> العطور الأصلية اللي تناسب طلبك ضمن ميزانيتك:
> 
> 🔹 Stronger With You من Emporio Armani، الـ90 ملي بـ700 جنيه والـ50 ملي بـ400 جنيه، ريحته دافية ومناسبة للخروج والسهرات.
> 
> 🔹 Dior Homme Sport، الـ50 ملي بـ450 جنيه داخل الميزانية، وفيه 90 ملي بـ1100 جنيه، أعلى حاجة بسيطة من ميزانيتك.
> 
> لو تحب عطر غني وقوي للسهرات، أنصحك بـ Stronger With You، أما لو عطر رياضي ومنعش، Dior Homme Sport خيار جيد.

[Full transcript, state and diagnostic evidence](L09.json)


## Conversation 1179 — L10

Changing recipient retains the previous recipients preferences

**Result:** reproduced. **Setup:** live model, actual router.

After switching from a personal winter purchase to a summer gift for a woman, explicitly dropping the prior conditions and budget, the database retained max_price=600 and projection=strong from the first recipient.

**Inspect turn 2.**

**Customer:**

> سيبك من طلبي أنا خالص. دلوقتي عايز هدية لصاحبتي للصيف، مش نفس الشروط ومش محدد ميزانية للهدية.

**Actual reply:**

> أنصحك بـ Libre من Yves Saint Laurent، زجاجة البراند 50 ملي بـ561 جنيه، فوحانه قوي ومناسب للصيف والمناسبات الرسمية.
> 
> وكمان Flowerbomb من Viktor & Rolf، 50 ملي بـ577 جنيه، فواح وقوي، مناسب للسهرات الصيفية.
> 
> أنا أرشحلك Libre أكتر لأنه فوحانه قوي ومناسب لكل المواسم، أما Flowerbomb فهي مناسبة أكتر للسهرات.

[Full transcript, state and diagnostic evidence](L10.json)


## Conversation 1180 — L11

The second recommended product is forgotten after intervening FAQs

**Result:** reproduced. **Setup:** live model, actual router.

The initial second recommendation was Acqua di Gio, which is active and stocked. After four unrelated questions, “the second one you recommended” produced an unavailable-product reply and an empty cart.

**Inspect turn 6.**

**Customer:**

> عايز أطلب التاني اللي رشحته في الأول، 50 مل زجاجة البراند، واحد منه.

**Actual reply:**

> للأسف العطر ده مش متوفر عندنا يا فندم 😔
> 
> بس عندنا عطور تانية مميزة ممكن تعجبك زي:
> • Stronger With You (Emporio Armani)
> • Dior Homme Sport (Dior)
> • Stronger With You Intensely (Emporio Armani)
> 
> تحب تعرف تفاصيل أكتر عن أي واحد فيهم؟

[Full transcript, state and diagnostic evidence](L11.json)


## Conversation 1181 — L12

An FAQ intercepts contact details needed by checkout

**Result:** reproduced. **Setup:** live model, actual router.

When the customer supplied both phone numbers, a static FAQ returned the store contact numbers instead. Neither customer number was saved on that turn. The next follow-up recovered them from history; the intercepted checkout action is visible in turn 2.

**Inspect turn 2.**

**Customer:**

> رقم موبايلي 01000000000 والرقم البديل 01100000000. وبتوصلوا للمحافظات؟

**Actual reply:**

> رقم التواصل للطلبات اونلاين او واتس: "01127027327"
> رقم التواصل للطلبات من المكان: "01098190098"

[Full transcript, state and diagnostic evidence](L12.json)


## Conversation 1182 — C01

Price changed after quote is confirmed without a new summary

**Result:** reproduced. **Setup:** Live model; controlled catalog price change, restored before transaction commits.

The customer saw 607.00 EGP. Confirmation created order totals [Decimal('707.00')] without first showing and approving the new price. Catalog price restored.

**Diagnostic events:**

- Catalog price changed between quote and approval
- Original catalog price restored

[Full transcript, state and diagnostic evidence](C01.json)


## Conversation 1183 — C02

The string false is accepted as order confirmation

**Result:** reproduced. **Setup:** Live initial quote; malformed extractor boolean injected into the real checkout handler.

The customer explicitly said not to confirm. Injecting JSON is_confirmed="false" still created an order because the string is truthy. This is an extractor-boundary test, not a claim that the live model emitted that JSON.

**Diagnostic events:**

- Fault injection

[Full transcript, state and diagnostic evidence](C02.json)


## Conversation 1184 — C03

Two concurrent confirmations create two orders for the same cart

**Result:** reproduced. **Setup:** Live initial quote; two synchronized valid checkout requests with recorded-cart extractor fixtures.

Two simultaneous approvals of the same cart created 2 orders: [69, 70].

**Diagnostic events:**

- Both confirmations reached the commit boundary before either completed

[Full transcript, state and diagnostic evidence](C03.json)


## Conversation 1185 — C04

A notification failure reports order failure after the order has committed

**Result:** reproduced. **Setup:** Live checkout; dashboard-notification function raises a controlled exception.

Order(s) [71] exist, but the customer received an order-failure message and was asked to retry.

**Diagnostic events:**

- Notification fault occurred after SQL order commit

[Full transcript, state and diagnostic evidence](C04.json)


## Conversation 1186 — C05

Reopening and cancelling a cancelled order restores stock twice

**Result:** reproduced. **Setup:** Live checkout/cancellation plus a simulated staff status change; stock restored under a row lock.

Stock changed 2 → 1 → 2 → 3. A cancelled order could be reopened without consuming stock, then cancelled to credit it again. Test restored stock to 2.

**Diagnostic events:**

- Simulated staff action: reopen cancelled order
- Inventory sequence

[Full transcript, state and diagnostic evidence](C05.json)


## Conversation 1187 — D01

A repeated web request with the same client message ID is processed twice

**Result:** reproduced. **Setup:** Two identical requests to the real ChatAPIView.post handler; live model/static FAQ responses.

One client_message_id produced two user messages and two assistant replies in the same conversation.

**Diagnostic events:**

- Identical request delivered twice

[Full transcript, state and diagnostic evidence](D01.json)


## Conversation 1188 — D02

Concurrent requests reply out of order and read incomplete history

**Result:** reproduced. **Setup:** Actual chat handler; a barrier delays the first request while the second completes.

The saved order is customer 1, customer 2, reply 2, reply 1. The second request ran without the first reply in its history.

**Diagnostic events:**

- Processing order

[Full transcript, state and diagnostic evidence](D02.json)


## Conversation 1189 — D03

A task retry resends a reply after delivery acknowledgement is lost

**Result:** reproduced. **Setup:** Simulated Messenger transport accepts a send then times out; no external messages are sent.

The same worker input was saved and replied to twice after an ambiguous first delivery. The simulated provider received two sends; no actual customer was contacted.

**Diagnostic events:**

- First delivery acknowledgement timed out; task requested a retry
- Simulated provider send attempts

[Full transcript, state and diagnostic evidence](D03.json)


## Conversation 1190 — D04

Web chat cannot retrieve a staff reply during handoff

**Result:** reproduced. **Setup:** Real web chat and staff-reply handlers; synthetic staff text.

The staff reply was saved, but the next public POST returned an empty reply and /api/chat/messages/ does not exist in this checkout.

**Diagnostic events:**

- Staff reply is in the database but the public client has no retrieval route

[Full transcript, state and diagnostic evidence](D04.json)


## Conversation 1191 — E01

Evaluator accepts another products genuine price as the requested products price

**Result:** reproduced. **Setup:** Explicitly injected incorrect assistant quote to exercise the real evaluator; not a live model response.

The evaluator returned [('named_product_without_data', 'high', "named 'Vanilo' on a turn where no product data was injected")]; it failed to flag 645 EGP for Vanilo 50ml, whose actual price is 607 EGP, because 645 is a valid price elsewhere in the catalog.

**Diagnostic events:**

- Evaluate a known incorrect quote

[Full transcript, state and diagnostic evidence](E01.json)


## Conversation 1192 — L13

Excluding store-exclusive products makes ordinary blends sound like designer originals

**Result:** not reproduced. **Setup:** live model, actual router.

The targeted claim that an original bottle proves designer-made liquid was not explicitly made in this second probe. The model gave an evasive manufacturer answer and an over-budget alternative, but these do not establish the targeted authenticity claim. Retained as a control.

**Inspect turn 2.**

**Customer:**

> قولي نوع السائل اللي هيوصلني بالأسعار دي ومين اللي مصنّعه؟

**Actual reply:**

> الزيت العطري في Stronger With You من Emporio Armani ريحته دافية ومزيج من الهيل، الفلفل الوردي، النعناع، واللافندر مع قاعدة فانيليا وكستناء وخشب الأرز. الحجم اللي معاك 90 ملي بـ700 جنيه، وفيه كمان 50 ملي بـ400 جنيه.
> 
> أما Dior Homme Sport، فهو ريحته منعشة بحمضيات الليمون والبرغموت مع لمسة خشبية وتوابل، والـ50 ملي بـ450 جنيه، والـ90 ملي بـ1100 جنيه أعلى حاجة بسيطة من ميزانيتك.
> 
> التركيبة نفسها من عطور غربية معروفة، والزجاجة الأوريجينال متوفرة لكن بأسعار أعلى بكتير.

[Full transcript, state and diagnostic evidence](L13.json)

## Verification

- 23 unique conversations and their complete saved transcripts verified in AIAgent.
- All simulation orders have diagnostic labels.
- No catalog price or stock differences remain.
- External platform delivery in the retry test was simulated; no customer messages were sent.
- The injected wrong-price evaluator fixture and two controls are explicitly identified.
