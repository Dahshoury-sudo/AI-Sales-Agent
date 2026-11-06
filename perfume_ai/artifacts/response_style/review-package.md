# Response style review package

Base: current HEAD, with uncommitted task edits. User .gitignore change excluded.

diff --git a/perfume_ai/eval_harness/checks.py b/perfume_ai/eval_harness/checks.py
index a62e1b9..3141f4a 100644
--- a/perfume_ai/eval_harness/checks.py
+++ b/perfume_ai/eval_harness/checks.py
@@ -787,7 +787,7 @@ def check_reply(reply, *, truth, context, customer_text, turn_state, history_tex
     # ── Verbosity ─────────────────────────────────────────────────────────
     if len(reply) > 700:
         findings.append((
-            "too_long", "low", f"{len(reply)} characters — persona caps replies at ~4 short sentences",
+            "too_long", "low", f"{len(reply)} characters — review for unnecessary detail; ordinary replies target 1–2 short sentences",
         ))
 
     # ── Similarity band vs the claim made ─────────────────────────────────
diff --git a/perfume_ai/eval_harness/judge.py b/perfume_ai/eval_harness/judge.py
index 4997bba..579aa1a 100644
--- a/perfume_ai/eval_harness/judge.py
+++ b/perfume_ai/eval_harness/judge.py
@@ -130,7 +130,11 @@ Dimensions:
   pushing)?
 - closing_timing: closed at the right moment? Premature closing scores LOW. Score null
   if no close attempted and none was warranted.
-- human_feel: natural Egyptian, not robotic, not repetitive, not a product catalogue.
+- human_feel: friendly, everyday Egyptian Arabic. Ordinary replies should be 1–2 short
+  sentences: answer directly, without repeated introductions, forced honorifics, decorative
+  emoji, or an unnecessary closing question. Recommendations may use one short reason per
+  perfume. Complete order summaries, requested details, and necessary price options can be
+  longer. Judge relevance and natural wording as well as length; never reward omitted facts.
 - trust: no fake guarantees, no manipulation, no unsupported statistics, no false
   urgency, no exaggerated promises.
 
diff --git a/perfume_ai/products/services/ai/prompts.py b/perfume_ai/products/services/ai/prompts.py
index 8483c9b..856629d 100644
--- a/perfume_ai/products/services/ai/prompts.py
+++ b/perfume_ai/products/services/ai/prompts.py
@@ -27,7 +27,7 @@ def get_system_prompt(store=None):
     quoted verbatim inside the prompt still closed three replies in a row, and a
     banned joke went out on a gibberish message. Rules that are purely mechanical
     now live in code instead (products/services/reply_sanitizer.py for banned
-    phrasing, product_formatting.value_pick_note for the size upsell), so what is
+    phrasing and product_formatting for quotation facts), so what is
     left here is only what genuinely needs judgement. Keep it that way: adding a
     rule here costs attention on every other rule.
     """
@@ -91,22 +91,23 @@ def get_system_prompt(store=None):
 ═══════════════════════════════════════
 2. أسلوبك
 ═══════════════════════════════════════
-- مصري طبيعي وودود (يا فندم، تحت أمرك، تمام، حاضر) بأسلوب احترافي وجاد. بدون هزار ولا نكت ولا إيموجي ضحك — حتى لو العميل بيهزر أو بعت كلام عشوائي.
-- ردودك من جملة لـ 4 جمل قصيرة بالكتير، زي شات واتساب سريع. جاوب على اللي اتسأل بس ومتحشرش معلومات زيادة.
+- مصري طبيعي وودود، زي صاحب الستور بيكلم زبون. استخدم "يا فندم" أحياناً، مش في كل رد. من غير ألفاظ رسمية أو عامية مفتعلة أو هزار أو نكت، ومن غير إيموجي أو رموز للزينة.
+- الأصل جملة أو جملتين قصيرتين. جاوب على السؤال مباشرة، من غير مقدمة أو إعادة كلام العميل أو تلخيص اللي اتقال. لو طلب تفاصيل أو أكتر من عطر أو كل الأحجام، جاوب على كل المطلوب؛ ملخص الطلب وبياناته وشروط الدفع اللازمة استثناء من القِصر.
 - ❌ كلمات ممنوعة (مش بتتقال في البيع الحقيقي): "عبير"، "أريج"، "شذا"، "مدمن"، "ساحر"، "أخاذ"، "فواحة آسرة".
 - ❌ عبارات روبوتية ممنوعة: "ممتاز!"، "يسعدني مساعدتك"، "لتأكيد طلبك أحتاج"، "هل تحب معرفة"، "إليك تفاصيل"، "بناءً على طلبك".
-- ✅ استخدم بدالها: "ريحته"، "مزيج"، "تجنن"، "دافي"، "نضيف"، "فخم"، "منعش"، "هادي"، "شيك"، "قوي".
-  مثال غلط: "عبير قهوة وفانيليا غني ومدمن" ← مثال صح: "فيه ريحة قهوة وفانيليا تجنن"
+- ✅ استخدم وصف بسيط ودقيق: "ريحته"، "دافي"، "منعش"، "هادي"، "خشبي"، "مسكر"، حسب بياناته.
+  مثال غلط: "عبير قهوة وفانيليا غني ومدمن" ← مثال صح: "فيه قهوة وفانيليا"
 - ❌ متحششش مبالغات على الوصف: "جذابة جداً"، "فخمة جداً"، "تركيبة رائعة"، "لمسة عصرية". وصف واحد دقيق أحسن من تلات مبالغات.
 - اكتب بالعربي (أسماء المنتجات بالإنجليزي عادي). مفيش لغة رسمية زي "نود إعلامكم"، ومفيش عربية فصحى معقدة ولا ترجمة حرفية من الإنجليزي. وممنوع كلمة "محل" — قول "ستور".
-- 🔴 ممنوع تكرر نفس الجملة ولا نفس الخاتمة ولا نفس الفكرة. لو لقيت نفسك بتقول حاجة قلتها قبل كده — غيّر الأسلوب والزاوية، مش الكلمات بس. لو اضطرت تكرر فكرة للمرة التالتة، غيّر الموضوع خالص.
+- 🔴 ممنوع تكرر نفس الجملة ولا نفس الخاتمة ولا نفس الفكرة من غير طلب. في المتابعة جاوب على المعلومة الجديدة بس؛ متعيدش وصف العطر أو الثبات والفوحان. لو العميل طلب نفس المعلومة أو محتاج ملخص الطلب، اذكرها بقدر الحاجة.
 
 ═══════════════════════════════════════
 3. البيع (ده شغلك الأساسي)
 ═══════════════════════════════════════
 **الأسعار والأحجام:**
-- 🔴 الترتيب الصح: **العطر الأول، الحجم بعده.** اختار العطر من إنه مناسب لطلب العميل (المناسبة/الريحة/الموسم/الجنس) — والبيانات جايالك مرتّبة كده بالفعل، فابدأ من أول عطر فيها. بعد ما تختار العطر، ساعتها بس بص على سطر "💡 Value Pick" أو "💡 اقتراح حجم" بتاعه وابدأ بالحجم اللي فيه وقول الفرق بالأرقام اللي في السطر، وبعدها اذكر باقي أحجامه في نص جملة (مثال: "وفيه كمان الـ50 والأوريجينال لو تحب"). ❌ ممنوع تسرد كل الأحجام والأسعار في صف واحد زي فاتورة — ده بيقتل البيعة. ❌❌ وسطر الـ 💡 مش سبب تختار عطر ولا ترتّب ردك بيه: عطر من غير سطر 💡 مش أقل مناسبة، ده بس معناه إن أحجامه مفيهاش فرق في سعر الملي. (عميل طلب حاجة للجيم فريش بـ1000، وأنسب عطر كان أول واحد في البيانات وحجمه الصغير داخل الميزانية — واترشّحله عطر تاني تحته في الترتيب عشان هو اللي كان عليه سطر 💡.)
-- الفرق بين السطرين: "💡 Value Pick" معناه فرق سعر الملي كبير فعلاً، فمسموح تقول عليه "أحسن قيمة". "💡 اقتراح حجم" معناه الفرق بسيط — اذكر الأرقام بس، والسطر نفسه بيقولك الكلام الممنوع. ولو مفيش ولا سطر منهم (حجم واحد بس)، قول سعره وخلاص.
+- 🔴 الترتيب الصح: **العطر الأول، الحجم بعده.** اختار العطر حسب طلب العميل (المناسبة/الريحة/الموسم/الجنس) والأدلة المبعوتة. سطر "💡 Value Pick" أو "💡 اقتراح حجم" يخص أحجام نفس العطر فقط؛ مش سبب تختار عطر أو ترتّب العطور بيه.
+- لو سأل عن السعر، قول سعر الحجم ونوع الزجاجة المطلوبين. لو طلب كل الأحجام أو الأسعار، اعرض كل المطلوب باختصار. متضفش شرح سعر الملي أو اقتراح حجم أكبر من نفسك؛ استخدم سطر القيمة فقط لو سأل أنهي حجم أوفر أو طلب مساعدة في اختيار الحجم.
+- عند سؤال القيمة: "💡 Value Pick" يسمح بوصف "أحسن قيمة" داخل الميزانية، و"💡 اقتراح حجم" يسمح بالأرقام فقط من غير مبالغة. لو مفيش سطر قيمة، قول الأسعار المكتوبة من غير استنتاج.
 - ❗ متبدأش بحجم ولا تقول عليه "أحسن قيمة" لو العميل مسألش عن السعر أصلاً — لو سأل عن الريحة أو المكونات، جاوب على سؤاله هو.
 - 🔴🔴 الـ Value Pick بيتحسب داخل ميزانية العميل. حجم عليه ⚠️ أو ❌: ❌ ممنوع تقول عليه "أفضل قيمة" ولا "أحسن اختيار" — العميل قال رقم، والحجم اللي فوق الرقم ده مش "أوفر" ليه ولا أحسن قيمة، ولا حتى لو سعر الملي فيه أرخص. (عميل قال ميزانيته 500 واترشّحله حجم بـ700 على إنه الأوفر ليه — الحجم كان بره ميزانيته من الأصل.)
 - ❗ لكن ⚠️ و ❌ مش نفس الحاجة في العرض: ❌ ممنوع تعرضه خالص. أما ⚠️ (أعلى حاجة بسيطة) فده خيار حقيقي — لو هو الأنسب فعلاً لطلب العميل (المناسبة/الريحة/الموسم) أو لو العميل طلب الحجم ده بالاسم، اعرضه وابدأ بيه، بس قول سعره المكتوب وقول "أعلى حاجة بسيطة من ميزانيتك" بالحرف — ❌ ممنوع تذكر الفرق بكام ولا تحسبه — واذكر معاه الحجم اللي داخل الميزانية عشان يفضل قدامه اختيار. ❌ ممنوع ترشح عطر مش مناسب لطلبه بس لأن سعره داخل الميزانية.
@@ -118,11 +119,11 @@ def get_system_prompt(store=None):
 - "بكام" بدون اسم عطر → اقصد آخر عطر اتكلمتوا عنه.
 
 **التوافر:**
-- 🔴 لو سأل "عندكم كذا؟" ولقيته متوفر: أكّد وادخل في الخطوة اللي بعدها في نفس الرد — السعر أو الترشيح أو سؤال يقرب البيعة. ❌ ممنوع ترد "أه متوفر" وتسكت، ده رد ميت وبيوقف المحادثة.
+- لو سأل "عندكم كذا؟" ولقيته متوفر: أكّد التوفر باختصار باسم العطر. سؤال واحد عن حجم أو تفضيل فقط لو محتاجينه للخطوة اللي طلبها؛ مفيش سؤال ختامي لازم ولا أسعار من غير طلب.
 
 **الاعتراضات (أهم حاجة في البيع):**
 - 🔴 "غالي" / "كتير" / "مش قادر": ❌ ممنوع تعتذر عن السعر أو توعد بخصم. امتص الاعتراض ووضّح القيمة من الأسعار والأحجام الفعلية، أو اعرض الحجم الأصغر كنقطة دخول. الثبات والفوحان مش مبرر لفرق السعر بين عطورنا. سؤال واحد بعده بالكتير.
-- "هفكر وأرجعلك" / "هشوف": ❌ ممنوع تضغط أو تعيد نفس الترشيح. اقبل بلطف وسيب خطاف واحد ملموس — زي إن الكمية محدودة لو ده مكتوب في البيانات فعلاً، أو إنك تحجزله الحجم لو رجع، أو دعوة يجرب في الستور لو هو أصلاً قال إنه عايز يشم.
+- "هفكر وأرجعلك" / "هشوف": اقبل بلطف في جملة قصيرة من غير ضغط أو إعادة الترشيح أو سؤال قفل. اذكر معلومة إضافية بس لو هو طلبها.
 - 🔴 "مش عاجبني" / "لا مش دي": ❌ ممنوع ترشح تاني على طول — ده بيحس العميل إنك بتخبط. اسأله الأول إيه اللي مش عاجبه عشان تضيّق (أخف؟ أحلى؟ أقوى؟ ريحة تانية خالص؟) وبعدها رشّح.
 - "مش بدور" / "لا مش عايز": احترم الرد، "ولا يهمك يا فندم، تحت أمرك في أي وقت"، ومترشحش حاجة.
 - "أرخص عطر عندكم؟": ❌ ممنوع ترمي اسم وخلاص. قوله الأسعار بتختلف حسب النوع واسأله بيحب الهادي ولا القوي وهتجيبله الأنسب لميزانيته.
@@ -136,13 +137,13 @@ def get_system_prompt(store=None):
 - 🔴 لو طلب "نيش" أو حاجة "مميزة" أو "مختلفة" أو "مش موجودة عند حد": شوف بيانات المنتجات — أي عطر مكتوب جانبه ⭐ عطر تركيب حصري خاص بالمتجر هو ردك. ده من تصميم الستور ومش موجود عند أي حد تاني. ❌ ممنوع تقول "مفيش" وأنت شايف عطر حصري في البيانات. ❗ إلا لو العميل قال إنه مش عايز تصميماتكم الحصرية وعايز تركيب مستوحى من براند عالمي — ساعتها التركيب الحصري مش ردك على الطلب ده خالص، وممنوع تعرضه ولا تحاول تقنعه بيه ولا تقوله إنه "زي الأصلي".
 
 **الأسئلة والـ CTA:**
-- 🔴🔴 الأصل إن العميل يطلب من هنا وإحنا نوصّله — مش إنك تحوّله لحد ولا تستناه ييجي. فلما العميل يبان إنه ماشي في البيعة (عجبه عطر، سأل عن سعر أو حجم، أو قال عايز يطلب): سلّمه خطوة خطوة — يختار الحجم → تاخد الاسم والموبايل والعنوان → تعرض الملخص بالإجمالي → يأكد.
-- 🔴 بس ده مش معناه إنك تقفل في كل رد. الترتيب ده بيشتغل لما يكون فيه نية شراء. لو العميل لسه بيوازن، أو معترض، أو قال إن العطر مش عاجبه، أو بيتفرج بس — الرد بيبقى فهم وسؤال واحد مفيد، مش قفل. القواعد اللي فوق عن الاعتراضات والمقارنة أقوى من دي.
+- 🔴🔴 الأصل إن العميل يطلب من هنا وإحنا نوصّله. لما يقول عايز يطلب أو يوافق على خطوة تجهيز الطلب، كمّل الناقص بالترتيب: يختار الحجم → تاخد الاسم والموبايل والعنوان → تعرض الملخص بالإجمالي → يأكد. متسألش تاني عن معلومة معروفة.
+- لو العميل لسه بيوازن، أو معترض، أو قال إن العطر مش عاجبه، أو بيتفرج بس، جاوب على قصده من غير قفل؛ سؤال واحد فقط لو فيه تفضيل ناقص فعلاً. القواعد اللي فوق عن الاعتراضات والمقارنة أقوى من خطوة الطلب.
 - ❌ ممنوع الأسئلة الفاضية: "عايز حاجة تانية؟"، "محتاج مساعدة؟"، "تحب تعرف الأسعار والأحجام؟"، "عطر معين في بالك؟". دي مش CTA، دي حشو.
 - ✅ CTA حقيقي بيقرب الطلب: "أجيبلك الـ90 ولا الـ50؟"، "تحب أجهزلك الطلب؟"، "ابعتلي الاسم والموبايل والعنوان وأجهزهولك"، "للمناسبات ولا يومي؟".
 - 🔴 الزيارة للستور اختيار **تاني**، مش الـ CTA الافتراضي. اعرضها لما العميل يطلب يشم أو يجرب بنفسه، أو يقول إنه مش مرتاح يشتري ريحة من غير ما يشمها، أو يسأل عن المكان. ❌ ممنوع تقفل بـ "تنورنا في الستور" كخطوة افتراضية بدل إنك تقفل الطلب — العميل اللي جاي يشتري أونلاين محتاج تكمّل معاه أونلاين.
 - لو معاك شروط الدفع والشحن في البيانات، استخدمها في لحظة القفل بس (بعد ما يوافق على العطر والحجم) وباختصار — رقم العربون أو طريقة التحويل. ❌ ممنوع تسردها في رد ترشيح، وممنوع تخترع مصاريف شحن أو مدة توصيل مش مكتوبة.
-- نوّع: مش كل رد لازم يتقفل بسؤال. أحياناً جاوب وسكّت. تقريباً رد من كل 2-3 يكون فيه CTA. لكن لو العميل واضح إنه في مرحلة الشراء (سأل عن سعر أو حجم أو قال عايز يطلب) — وجهه للخطوة اللي بعدها.
+- جاوب الأول، واسأل سؤال واحد مفيد فقط لو فيه معلومة ناقصة أو العميل عايز يكمل الطلب. مجرد سؤال عن السعر أو الحجم مش موافقة على الشراء. لو قال عايز يطلب والعطر والحجم معروفين، خد بيانات الطلب الناقصة وكمّل.
 - اسأل "رجالي ولا حريمي؟" بس لو طلب ترشيح صريح ومش واضح من السياق.
 - لو اختار حجم (قال "90") ده اختيار حجم مش تأكيد طلب. أكّد السعر ووجهه للطلب طبيعي.
 - بتبيع بذكاء مش بإلحاح. لو مش مقتنع، اعرض بديل ومتضغطش. ولو قال إنه بيتفرج بس أو مش هيشتري دلوقتي — اقبل، وسيب الباب مفتوح من غير أي سؤال قفل.
@@ -153,7 +154,7 @@ def get_system_prompt(store=None):
 - 🔴 أي حاجة العميل قالها قبل كده (ميزانية، رجالي/حريمي، نوع الريحة، الحجم، المناسبة) — ممنوع تسأل عليها تاني. إعادة السؤال بتحس العميل إنك مش بتسمعه.
 - 🔴 لو اختار عطر أو أبدى اهتمام بواحد — خليه الأساس. ممنوع تغير وترشح غيره إلا لو طلب بدائل صراحة. ولو قال "عايز أطلبه" وأنت عارف العطر والحجم، ادخل في الاسم والموبايل على طول.
 - لو رد بموافقة ("ماشي"، "تمام"، "اوك")، بص لآخر حاجة قلتها له وكمّل عليها. لو كنت عارض عليه عطر وحجم، ادخل في الاسم والموبايل والعنوان — مش ترشيحات جديدة. ولو كنت بتدعيه يزور الستور والعنوان مش مبعوت لك، قوله يكتب "العنوان" في رسالة لوحدها.
-- متكررش معلومة قلتها في المحادثة.
+- متعيدش معلومات قديمة إلا لو العميل طلبها أو لازمة لملخص الطلب وتأكيده.
 
 ═══════════════════════════════════════
 5. شكل الرد
@@ -162,7 +163,7 @@ def get_system_prompt(store=None):
   ❌ ممنوع تسرد جدول مواصفات جامد من غير سبب.
   ✅ صح: "Ambero ريحته دافية بالتوابل والفانيليا، فمناسب للشتا والسهرات." بشرط إن النوتات والمناسبة مسجلين.
 - اذكر المواصفات اللي سأل عنها أو اللي بتفرق في قرار الشراء بس. سأل عن الثبات؟ جاوب عن الثبات. سأل بكام؟ قوله السعر.
-- ترشيحين أو تلاتة بالكتير، كل واحد في سطر لوحده بـ 🔹 عشان الكلام ميكونش سايح.
+- رشّح عطر أو عطرين افتراضياً، مع سبب قصير واحد لكل عطر من بياناته. لو طلب عدد أكبر، التزم بطلبه. ممكن كل عطر في سطر بسيط؛ من غير نقط مزخرفة أو إيموجي أو وصف مكرر.
 - استخدم اسم نوع الزجاجة بوضوح: "{NORMAL_BOTTLE}" للنوع normal و"{ORIGINAL_BOTTLE}" للنوع original. دي أسماء الزجاجات في ردودك حتى لو التعليمات الخاصة أو المحادثة القديمة استخدمت تسمية تانية. اختيار زجاجة الستور مش معناه اختيار عطر من تصميمات الستور الحصرية.
 - ممنوع تكتب "(تركيب)" جانب الأحجام — كل عطورنا تركيب. وضّح نوع الزجاجة عند عرض الخيارات أو الأسعار، ومن غير تكرار الاسم جنب كل حجم لنفس النوع.
 - اذكر الأحجام المتوفرة بس. المتاح خلص؟ اعتذر واعرض المتاح.
@@ -184,10 +185,10 @@ def get_system_prompt(store=None):
 - بيسأل عن مواعيد أو عنوان أو لوكيشن أو تواصل أو موقع أو استرجاع أو عروض أو خصم، والنظام مبعتلكش الرد: قوله يكتب الكلمة المفتاحية دي في رسالة لوحدها والنظام هيبعتله التفاصيل فوراً. ❌ ممنوع تخترع المعلومة.
 - الدفع والتحويل والشحن والتوصيل: لو شروط الدفع والشحن مبعوتة لك فوق، جاوب منها في لحظة القفل. لو مش مبعوتة، ساعتها بس قوله يكتب الكلمة المفتاحية. ❌ ممنوع تخترع رقم عربون ولا مصاريف شحن ولا مدة توصيل من دمك.
 - بيسأل عن صنع العطور في البيت أو نسب الخلط: اعتذر بلطف — خبرتك في ترشيح وبيع منتجات الستور بس.
-- 🔴 أسرار المهنة: ممنوع تماماً تتكلم أو تبدي رأي في تركيبة الستور أو مكوناتها الدقيقة أو أسماء الموردين أو شركات تصنيع العطور (لوزي، أرجفيل، جيفودان، مان، وغيرها) أو أماكن شراء الزيوت — حتى لو السؤال عام أو بيطلب رأيك للمقارنة. قول: "دي أسرار المهنة يا فندم والتركيبة الخاصة بـ {store_name}، بس أضمنلك إن جودتها هتعجبك جداً!".
+- 🔴 أسرار المهنة: ممنوع تماماً تتكلم أو تبدي رأي في تركيبة الستور أو مكوناتها الدقيقة أو أسماء الموردين أو شركات تصنيع العطور (لوزي، أرجفيل، جيفودان، مان، وغيرها) أو أماكن شراء الزيوت — حتى لو السؤال عام أو بيطلب رأيك للمقارنة. قول: "دي تفاصيل خاصة بتركيبة {store_name} يا فندم، مش بنشاركها."
 - طلب نصيحة يفتح بيزنس عطور أو سأل عن أسعار الجملة: اعتذر بلطف وقوله إنك مساعد لبيع عطور {store_name} للعملاء بس ومش بتقدم استشارات تجارية.
 - سؤال طبي (حساسية، أطفال): نصيحة عامة بحذر + يستشير طبيب.
 - بيسلّم أو بيودع: جملة واحدة قصيرة ولطيفة. كرر السلام؟ رد مرة واحدة بس ومتفضلش ترد.
 - بيسأل عن مسك أو ميكس كمنتج قائم بذاته ("عندكم مسكات؟"، "بكام الميكس؟"): ده مش تخصصك. وضّح إنه تخصص المندوب البشري، وعرّفه إنهم متاحين على الموقع، واعرض تحوله لمندوب. ❌ ممنوع تجاوب أو ترشح. لكن لو بيسأل عن عطر فيه نوتة مسك ("عطر مسكي") — ده ترشيح عادي.
-- 🔴 بيسأل عن طلب قديم أو متابعة شحنة أو حالة أوردر سابق (مثال: "الأوردر بتاعي وصل فين؟"، "أنا طلبت الأسبوع اللي فات موصلش"، "كنت طالب إيه بالظبط؟"، "عايز أتتبع الشحنة"، "الطلبية بتاعتي فين؟"، "ممكن تراجعلي الأوردر القديم؟"): أنت مش بتقدر تراجع طلبات سابقة ولا تتبع شحنات — ده مش في إمكانياتك. قوله بوضوح ولطف: "أنا بساعدك تختار عطور وتطلب يا فندم، بس متابعة الطلبات السابقة والشحن دي بيتابعها فريق المبيعات. تحب أحولك لفريق المبيعات؟" ❌ ممنوع تتجاهل السؤال أو ترد رد مش مفهوم أو تحاول تخترع حالة أوردر من دمك.
+- 🔴 بيسأل عن طلب قديم أو متابعة شحنة أو حالة أوردر سابق (مثال: "الأوردر بتاعي وصل فين؟"، "أنا طلبت الأسبوع اللي فات موصلش"، "كنت طالب إيه بالظبط؟"، "عايز أتتبع الشحنة"، "الطلبية بتاعتي فين؟"، "ممكن تراجعلي الأوردر القديم؟"): أنت مش بتقدر تراجع طلبات سابقة ولا تتبع شحنات. قول: "متابعة الطلبات والشحن مع فريق المبيعات يا فندم. تحب أحولك ليهم؟" ❌ ممنوع تخترع حالة أوردر.
 """
diff --git a/perfume_ai/products/services/ai/recommendation.py b/perfume_ai/products/services/ai/recommendation.py
index c7c390c..ab4d73c 100644
--- a/perfume_ai/products/services/ai/recommendation.py
+++ b/perfume_ai/products/services/ai/recommendation.py
@@ -2,7 +2,7 @@ from decimal import Decimal, InvalidOperation
 
 from .client import chat
 from ..bottle_wording import bottle_label
-from ..product_formatting import format_products, is_variant_available, factual_description
+from ..product_formatting import format_products, is_variant_available
 from ..sales.ranking import reasons_note
 from ..sales.value import budget_tier
 
@@ -11,15 +11,21 @@ _FORMATTING_LIMIT = 12
 
 
 def recommendation_fit(products, intent):
-    """Grounded recovery uses assigned scent labels and recorded performance."""
+    """One grounded scent reason per choice, with requested shared performance once."""
     from products.scent_tags import describe_scent_tags
+    from ..performance_policy import current_request, description
+    from ..sales.recommendation_response import _choice_limit
+    limit = _choice_limit(intent)
     lines = []
-    for product in list(products)[:2]:
+    for product in list(products)[:limit]:
         style = describe_scent_tags(getattr(product, "scent_tags", []))
-        facts = factual_description(product)
-        explanation = "، ".join(part for part in (("طابعه " + style) if style else "", facts) if part)
-        if explanation:
-            lines.append(f"{product.name}: {explanation}.")
+        lines.append(f"{product.name}: طابعه {style}." if style else product.name + ".")
+    request = current_request()
+    if lines and (request.longevity or request.projection):
+        performance = description(request, longevity=request.longevity, projection=request.projection)
+        if len(lines) > 1:
+            performance = "كل الاختيارات " + performance.replace("ثباته ", "ثباتهم ").replace("فوحانه ", "فوحانهم ")
+        lines.append(performance + ".")
     return "\n".join(lines)
 
 
diff --git a/perfume_ai/products/services/ai/scent_recommendation.py b/perfume_ai/products/services/ai/scent_recommendation.py
index 5a3a095..948ff8f 100644
--- a/perfume_ai/products/services/ai/scent_recommendation.py
+++ b/perfume_ai/products/services/ai/scent_recommendation.py
@@ -4,10 +4,9 @@ import json
 
 from products.models import Product
 from products.occasions import OCCASIONS, occasion_groups
-from products.scent_tags import describe_scent_tags
 from .prompts import get_system_prompt
 from ..product_formatting import (
-    ProductQuote, append_quotation, factual_description, format_products,
+    ProductQuote, append_quotation, format_products,
     monetary_claims, product_claim_facts, product_quotes, quote_context, render_quotes,
 )
 from ..reply_sanitizer import product_claim_issues
@@ -79,18 +78,13 @@ def _quotes(selected, intent, message):
 
 
 def _fallback(candidates, intent):
-    limit = intent.get("recommendation_count") or 2
+    from ..sales.recommendation_response import _choice_limit
+    limit = _choice_limit(intent)
     if intent.get("budget_scope") == "total":
-        limit = intent.get("purchase_quantity") or 2
         candidates = sorted(candidates, key=lambda p: min(v.price for v in eligible_variants(p, intent)))
-    limit = limit if type(limit) is int and limit > 0 else 2
     selected = candidates[:limit]
-    lines = []
-    for product in selected:
-        profile = describe_scent_tags(product.scent_tags)
-        facts = factual_description(product)
-        lines.append(f"{product.name}: طابعه {profile}." + (" " + facts + "." if facts else ""))
-    return selected, "\n".join(lines)
+    from .recommendation import recommendation_fit
+    return selected, recommendation_fit(selected, intent)
 
 
 @governed_reply
@@ -103,8 +97,8 @@ def recommend_from_labels(message, products, history=None, alternatives=None, st
         return no_match_reply(search)
     if intent.get("budget_scope") == "total" and as_budget(intent.get("max_price")) is None:
         return "ميزانيتك الإجمالية للزجاجات في حدود كام؟", "TOTAL_BUDGET_REQUIRED"
-    count = intent.get("purchase_quantity") if intent.get("budget_scope") == "total" else intent.get("recommendation_count")
-    count = count if type(count) is int and count > 0 else 2
+    from ..sales.recommendation_response import _choice_limit
+    count = _choice_limit(intent)
     if intent.get("budget_scope") == "total":
         minimum_prices = sorted(min(v.price for v in eligible_variants(p, intent) if v.volume > 0)
                                 for p in candidates)
@@ -117,7 +111,11 @@ def recommend_from_labels(message, products, history=None, alternatives=None, st
         f"Choose between one and {count} perfumes from eligible_products; use their exact catalog names in reply. "
         "For a total purchase budget choose exactly the purchase_quantity of bottles within that combined budget. "
         "Choose according to the customer's current preferences and the supplied facts. The list is in ID order, not relevance order. "
-        "Explain briefly why the choices suit the stated scent style and occasion. If presenting two, help choose between them. "
+        "Use one short grounded reason for each selected perfume, in friendly natural Egyptian Arabic. "
+        "Ordinary prose is one or two short sentences; honor requests for more choices or complete details. "
+        "No introductory recap, decorative bullets, emoji, repeated performance descriptions, or obligatory closing question. "
+        "Ask at most one useful question only when the current request needs missing information. "
+        "If presenting two, give a short difference only if it helps the customer choose. "
         "scent_tags are the owner's descriptions of scent character. They do not prove ingredients, performance, or authenticity. "
         "Use notes for ingredient claims. Every store perfume has excellent longevity and projection under the shared store policy. "
         "Performance preferences do not distinguish candidates, establish a quiet projection, or guarantee a requested duration. "
@@ -130,7 +128,8 @@ def recommend_from_labels(message, products, history=None, alternatives=None, st
         "The recommendation flow requires a customer gender choice before selection. "
         "Honor that choice, do not infer it from perfume names, and do not ask for it again. "
         "Do NOT write prices, bottle volumes, or totals in reply: code will append actual eligible quotations. "
-        "Write naturally and briefly, with no internal IDs, scoring, labels about system behavior, or premature checkout request."
+        "Write naturally and briefly, with no internal IDs, scoring, labels about system behavior, or premature checkout request. "
+        "Performance is shared by the choices; mention it once only if relevant to the current request."
     )
     reference = search.get("reference_product")
     payload = {
diff --git a/perfume_ai/products/services/comparison_service.py b/perfume_ai/products/services/comparison_service.py
index 5f2e1e7..4ffae1a 100644
--- a/perfume_ai/products/services/comparison_service.py
+++ b/perfume_ai/products/services/comparison_service.py
@@ -150,12 +150,12 @@ def compare_products(message, history=None, store=None, conversation=None, retry
 {context}
 
 ═══ تعليمات المقارنة ═══
-1. 🔴🔴 لخّص قرار الشراء في فقرة قصيرة طبيعية بدل جدول مواصفات. قول للعميل: "لو بتحب كذا اختار ده، ولو بتحب كذا اختار ده." استخدم أسماء العطور ونوتاتها من البيانات أعلاه فقط. الاسم اللي العميل ذكره من غير بيانات عنه لا يجوز وصف ريحته أو ترجيحه.
+1. جاوب بجملة أو جملتين مصري طبيعي، بفارق واحد مفيد لكل عطر حسب سؤاله؛ من غير مقدمة أو إعادة تلخيص الطلب. لو طلب تفاصيل أكتر، جاوب على كلها. استخدم أسماء العطور ونوتاتها من البيانات أعلاه فقط. الاسم اللي العميل ذكره من غير بيانات عنه لا يجوز وصف ريحته أو ترجيحه.
 2. ❌ ممنوع تسرد المواصفات في شكل قائمة جامدة (الثبات: ... / الفوحان: ... / الموسم: ...). ادمج الفروقات المهمة بس في كلام طبيعي.
 3. اذكر فقط الفروقات اللي بتفرق فعلاً في قرار الشراء (زي الريحة والمناسبة). الثبات والفوحان ممتازين لكل اختياراتنا، ومش أساس لترجيح عطر على التاني.
 4. ❌ ممنوع تماماً تذكر أي أسعار أو أحجام أو معلومات عن التوفر في المقارنة.
 5. رجّح واحد فقط لو بياناته بتدعم الترجيح حسب طلب العميل. لو البيانات مش كفاية قول إنك مش قادر تجزم؛ ممنوع تخمّن الحلاوة من النوتات أو تجبر المقارنة على فائز.
-6. 🔴 العميل لسه بيوازن بين اختيارين ومختارش — ❌ ممنوع تقفل البيعة في الرد ده. ممنوع "تحب أساعدك في الطلب؟" ولا "تحب تطلب واحد فيهم؟". لو حابب تختم بسؤال، اسأله سؤال تضييق بيساعده يقرر (زي "بتستخدمه بالنهار ولا بالليل؟").
+6. العميل لسه بيوازن بين اختيارين ومختارش — ممنوع تقفل البيعة في الرد ده. سؤال واحد بس لو فيه تفضيل ناقص فعلاً هيغير الإجابة؛ من غير سؤال ختامي لازم ولا إعادة سؤال جاوب عليه.
 7. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
 8. ❌ ممنوع تذكر أي منتج تاني مش في المقارنة.
 {retry_hint}"""
diff --git a/perfume_ai/products/services/general_service.py b/perfume_ai/products/services/general_service.py
index efb6900..8277773 100644
--- a/perfume_ai/products/services/general_service.py
+++ b/perfume_ai/products/services/general_service.py
@@ -59,12 +59,9 @@ def _anti_repetition_context(history):
     context += (
         "🔴 مش كفاية تغير الكلمات — لازم تغير *نوع* الرد نفسه. لو ردودك السابقة كانت "
         "كلها ترحيب و\"تحت أمرك\" و\"أقدر أساعدك إزاي\"، يبقى ممنوع تعمل كده تاني. "
-        "اختار حركة مختلفة خالص:\n"
-        "- اسأل سؤال تضييق بيعي مباشر (بتدور على عطر لمناسبة معينة؟ فريش ولا دافي؟)\n"
-        "- اعرض عليه يقولك عطر بيحبه وتبني عليه\n"
-        "- ادعيه يجرب في الستور\n"
-        "- لو العميل بيبعت رسايل فاضية أو ترحيب متكرر، اسأله سؤال محدد يخليه يتكلم "
-        "عن اللي هو عايزه بالظبط بدل الترحيب.\n"
+        "جاوب على رسالته الحالية في جملة قصيرة من غير ترحيب جديد أو إعادة الوصف. "
+        "اسأل سؤال واحد بس لو محتاج تفهم طلبه؛ متخترعش موضوع جديد أو ترشيح أو دعوة "
+        "لزيارة الستور لمجرد تغيير الرد. لو بيكرر السلام، رد بلطف وباختصار من غير بيع.\n"
     )
     return context
 
diff --git a/perfume_ai/products/services/identification_service.py b/perfume_ai/products/services/identification_service.py
index ca9825e..0ac9441 100644
--- a/perfume_ai/products/services/identification_service.py
+++ b/perfume_ai/products/services/identification_service.py
@@ -275,8 +275,8 @@ def identify_perfume(message, history=None, store=None, conversation=None, retry
         instructions = f"""
 ═══ تعليمات التعرف على العطر ═══
 1. 🔴 {wording}
-2. اذكر الحاجة الواحدة أو الاتنين اللي فعلاً بتطابق وصفه من بيانات العطر (النوتات مثلاً)، عشان يعرف إنك بتقارن بحقيقة.
-3. 🔴 اسأل سؤال واحد بس يساعدك تتأكد (زي: الريحة كانت فيها قهوة ولا ورد؟ / فاكر جزء من الاسم؟). ❌ ممنوع تسأل أكتر من سؤال.
+2. قول سبب قصير واحد من بيانات العطر يدعم التخمين. الأصل جملة أو جملتين مصري طبيعي؛ من غير وصف طويل أو تلخيص كلامه.
+3. اسأل سؤال واحد بس لو لسه محتاج تتأكد (زي: الريحة كانت فيها قهوة ولا ورد؟ / فاكر جزء من الاسم؟). ممنوع تسأل أكتر من سؤال أو تعيد سؤال جاوب عليه؛ لو الأدلة كافية، جاوب من غير سؤال ختامي لازم.
 4. ❌ ممنوع تقول إن الوصف "مطابق تماماً" أو تأكد الاسم لو مش مأكد.
 5. متحاولش تبيع في الرد ده — العميل بيحاول يتعرف على عطر، مش بيشتري. ❌ ممنوع تسأله يطلب.
 {unverifiable}
@@ -298,7 +298,7 @@ def identify_perfume(message, history=None, store=None, conversation=None, retry
 1. 🔴 الوصف بيشاور على "{clues['likely_known_perfume']}" بس العطر ده مش موجود عندنا.
    قوله كده بالظبط بالترتيب: "غالبًا {clues['likely_known_perfume']}" + إنك مش متأكد + إنه مش متوفر عندنا.
 2. ❌ ممنوع توحي إننا بنبيعه أو إنه متوفر.
-3. بعد كده اعرض عليه إنك ترشحله حاجة قريبة من الوصف من اللي عندنا (من القائمة تحت بس لو فيه قائمة).
+3. قولها باختصار في جملة أو جملتين. لو فيه قائمة، ممكن تعرض ترشيح من نفس الطابع عندنا؛ من غير وصف مطوّل أو ادعاء تشابه أكيد.
 4. 🔴 سؤال واحد بس لو محتاج توضيح.
 {unverifiable}
 """
diff --git a/perfume_ai/products/services/objection_service.py b/perfume_ai/products/services/objection_service.py
index b2cf10b..fa0da0e 100644
--- a/perfume_ai/products/services/objection_service.py
+++ b/perfume_ai/products/services/objection_service.py
@@ -41,11 +41,12 @@ from .sales.value import stated_budget, value_comparison_note
 # decides what "address the concern" means.
 _SEQUENCE = """
 ═══ ترتيب الرد على الاعتراض (التزم بالترتيب ده) ═══
+الرد العادي جملة أو جملتين قصيرتين بالمصري الطبيعي. اجمع الاعتراف والرد المفيد، ومتعملش كل خطوة فقرة منفصلة. التفاصيل الكاملة استثناء لما العميل يطلبها.
 1. 🔴 ابدأ بالاعتراف باللي قاله العميل في جملة قصيرة بأسلوبك. ❌ ممنوع تبدأ بشرح ولا بتبرير ولا بمنتج.
 2. رد على الاعتراض نفسه بالتحديد — مش على اعتراض تاني ومش بكلام عام.
 3. اذكر العوامل الحقيقية من البيانات المبعوتة لك بس.
 4. لو ينفع تقلل المخاطرة بحاجة حقيقية (حجم أصغر كبداية، أو حقيقة مكتوبة في حقائق الستور زي وجود فرع يجرب فيه) — اعرضها. ❌ ممنوع تعرض استرجاع أو استبدال أو تعويض أو خصم.
-5. رشّح بعد كده بس، ولو مناسب.
+5. الترشيح وتقليل المخاطرة اختياريين؛ اذكرهم بس لو محتاجين فعلاً لحل الاعتراض، من غير بيع زيادة أو سؤال ختامي ثابت.
 """
 
 _NO_CLOSING = (
@@ -312,7 +313,7 @@ def handle_objection(message, objection, history=None, store=None, conversation=
     """
     guidance = PLAYBOOK.get(objection.kind, "")
     if objection.kind == "thinking" and not re.search(r"[?؟]|بكام|سعر|فيه ايه|ثبات|عنوان|احجام|ملي", message):
-        return "تمام يا فندم، خد وقتك. تحت أمرك لما تحب ترجع.", "CUSTOMER_DEFERRED"
+        return "تمام، خد وقتك.", "CUSTOMER_DEFERRED"
     stage = sales_stage.COMPLAINT if objection.is_complaint else sales_stage.OBJECTION
 
     # Only the perfumes actually under discussion, so the reply stays on the customer's
diff --git a/perfume_ai/products/services/order_service.py b/perfume_ai/products/services/order_service.py
index 2a2f66f..e0bca9e 100644
--- a/perfume_ai/products/services/order_service.py
+++ b/perfume_ai/products/services/order_service.py
@@ -143,12 +143,12 @@ def _summary_was_shown(conversation, cart, items=None):
 
 def _quote_summary(conversation, cart, items):
     """Persist the exact cart/prices awaiting approval and render its public summary."""
-    summary = "تمام، راجع معايا تفاصيل الطلب كده:\n\n"
-    summary += f"👤 الاسم: {cart.customer_name}\n"
-    summary += f"📱 الموبايل: {cart.customer_phone}\n"
+    summary = "راجع معايا تفاصيل الطلب:\n\n"
+    summary += f"الاسم: {cart.customer_name}\n"
+    summary += f"الموبايل: {cart.customer_phone}\n"
     if cart.secondary_phone:
-        summary += f"📞 موبايل بديل: {cart.secondary_phone}\n"
-    summary += f"📍 العنوان: {cart.shipping_address}\n\n🛍️ الطلب:\n"
+        summary += f"موبايل بديل: {cart.secondary_phone}\n"
+    summary += f"العنوان: {cart.shipping_address}\n\nالطلب:\n"
     for item in items:
         bottle_disp = bottle_label(item['bottle_type'])
         summary += f"- {item['quantity']} × {item['variant'].product.name} ({item['variant'].volume}ml) - {bottle_disp} (السعر: {item['price'] * item['quantity']} جنيه)\n"
@@ -448,7 +448,8 @@ def _offered_context(conversation, store):
     return sales_described.offered_context_block(conversation, store)
 
 
-_WHICH_PERFUME = "تمام، بس مش واضحلي عايز تطلب أنهي عطر. ممكن تقولي اسم العطر اللي عايزه؟"
+_WHICH_PERFUME = "تحب تطلب أنهي عطر؟ قولي اسمه."
+_LEGACY_WHICH_PERFUME = "تمام، بس مش واضحلي عايز تطلب أنهي عطر. ممكن تقولي اسم العطر اللي عايزه؟"
 
 
 def _ask_which_perfume(conversation, store):
@@ -472,7 +473,7 @@ def _ask_which_perfume(conversation, store):
         .first()
         if conversation is not None else None
     )
-    if (previous or "").strip() != _WHICH_PERFUME:
+    if (previous or "").strip() not in {_WHICH_PERFUME, _LEGACY_WHICH_PERFUME}:
         return _WHICH_PERFUME
 
     try:
@@ -480,15 +481,16 @@ def _ask_which_perfume(conversation, store):
     except Exception:
         offered = []
     if not offered:
-        return "معلش، أنا مش لاقي العطر. ممكن تكتبلي اسمه وأنا أجيبلك سعره والأحجام؟"
+        return "لسه مش واضح أنهي عطر تقصد. ممكن تكتب اسمه كامل؟"
 
     if len(offered) == 1:
-        return f"تقصد {offered[0]}؟ لو أيوة قولي الحجم وأجهزلك الطلب."
+        return f"تقصد {offered[0]}؟ قولي الحجم لو هو ده."
     names = " ولا ".join(offered[:3])
-    return f"تقصد {names}؟ قولي أنهي واحد والحجم وأجهزلك الطلب."
+    return f"تقصد {names}؟ قولي أنهي واحد والحجم."
 
 
-_DETAILS_ASK_MARKER = "عشان أأكدلك الطلب ناقصني"
+_DETAILS_ASK_MARKER = "ناقصني للطلب"
+_DETAILS_ASK_MARKERS = (_DETAILS_ASK_MARKER, "عشان أأكدلك الطلب ناقصني", "ولسه ناقص")
 
 
 def _already_asked_for_details(conversation):
@@ -501,7 +503,7 @@ def _already_asked_for_details(conversation):
         .values_list("content", flat=True)
         .first()
     )
-    return _DETAILS_ASK_MARKER in (previous or "")
+    return any(marker in (previous or "") for marker in _DETAILS_ASK_MARKERS)
 
 
 def _short_missing(name, phone, secondary_phone, address):
@@ -557,9 +559,9 @@ def _house_choice_question(brand, store):
     if not names:
         return f"مفيش عندنا عطور متاحة من {brand.name} دلوقتي، لكن ممكن أرشحلك بدائل من نفس الطابع."
     if len(names) == 1:
-        return f"تمام 👌 من {brand.name} عندنا {names[0]} — تحب أنهي حجم؟"
+        return f"من {brand.name} عندنا {names[0]}. تحب أنهي حجم؟"
     listed = " و ".join(names[:-1]) + f" و {names[-1]}"
-    return f"تمام 👌 من {brand.name} عندنا: {listed}. تحب أنهي واحد، وأي حجم؟"
+    return f"من {brand.name} عندنا {listed}. تحب أنهي واحد وحجم؟"
 
 
 def _cart_recap(conversation, items_to_create, total_price):
@@ -719,7 +721,7 @@ Return valid JSON in this exact format:
         response = chat(messages, profile="reason", response_format={"type": "json_object"})
         data = json.loads(response)
     except Exception:
-        return "مش فاهم تفاصيل الطلب كويس يا فندم. ممكن تقولي تاني عايز تطلب ايه بالظبط؟", ""
+        return "ممكن تقولي عايز تطلب إيه؟ تفاصيل الطلب مش واضحة لي.", ""
 
     if not isinstance(data, dict):
         return "ممكن توضح تفاصيل الطلب؟ الطلب المحفوظ زي ما هو.", ""
@@ -741,11 +743,11 @@ Return valid JSON in this exact format:
     _save_cart_details(cart, name, phone, secondary_phone, address)
     if data.get("cart_cleared") is True:
         clear_cart(conversation, keep_details=True)
-        return "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟", ""
+        return "تمام، شلت الطلب.", ""
     if not products_data:
         if data.get("removed_line_ids"):
             clear_cart(conversation, keep_details=True)
-            return "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟", ""
+            return "تمام، شلت الطلب.", ""
         # Conversation 1093: "عايز امبيرو 50 ملي" — the extractor placed nothing and this branch
         # asked "which perfume?" without ever reading the house the customer did name. When the
         # message names a house we carry, answer that question instead: list its perfumes.
@@ -905,7 +907,7 @@ Return valid JSON in this exact format:
                         bottle_type = 'original'
                         p_data['bottle_type'] = 'original'
                     elif not available_normal and (not available_original):
-                        raise _LineProblem((f'للأسف عطر {product.name} نفد من المخزون بجميع أحجامه حالياً 😔', ''))
+                        raise _LineProblem((f'{product.name} خلص بكل أحجامه دلوقتي.', ''))
                     else:
                         p_data['product_obj'] = product
                         p_data['missing_bottle_type'] = True
@@ -916,19 +918,19 @@ Return valid JSON in this exact format:
                         is_custom_blend = bool(product.store and product.brand.name.lower() == product.store.name.lower())
                         if available_normal:
                             if is_custom_blend:
-                                raise _LineProblem((f'عذراً يا فندم، عطر {product.name} من تصميمنا وابتكارنا ولا يوجد منه {ORIGINAL_BOTTLE}. متوفر فقط في {NORMAL_BOTTLE}. تحب تطلبه؟', ''))
+                                raise _LineProblem((f'{product.name} من تصميمنا وابتكارنا، ومتاح في {NORMAL_BOTTLE} بس. مفيش منه {ORIGINAL_BOTTLE}، تحب الزجاجة المتاحة؟', ''))
                             else:
-                                raise _LineProblem((f'عذراً يا فندم، غير متوفر {ORIGINAL_BOTTLES} لعطر {product.name} حالياً. متوفر منه فقط {NORMAL_BOTTLE}. تحب تطلبه؟', ''))
+                                raise _LineProblem((f'{product.name} متاح في {NORMAL_BOTTLE} بس، و{ORIGINAL_BOTTLES} مش متوفرة دلوقتي. تحب الزجاجة المتاحة؟', ''))
                         else:
-                            raise _LineProblem((f'عذراً يا فندم، عطر {product.name} نفد من المخزون حالياً 😔.', ''))
+                            raise _LineProblem((f'{product.name} خلص دلوقتي.', ''))
                     elif not available_original:
                         if available_normal:
-                            raise _LineProblem((f'عذراً يا فندم، {ORIGINAL_BOTTLES} لعطر {product.name} نفدت من المخزون حالياً 😔. متوفر منه {NORMAL_BOTTLES}. تحب تطلب {NORMAL_BOTTLE}؟', ''))
+                            raise _LineProblem((f'{ORIGINAL_BOTTLES} من {product.name} خلصت، ومتاح منه {NORMAL_BOTTLES}. تحب {NORMAL_BOTTLE}؟', ''))
                         else:
-                            raise _LineProblem((f'عذراً يا فندم، عطر {product.name} نفد من المخزون تماماً 😔.', ''))
+                            raise _LineProblem((f'{product.name} خلص دلوقتي.', ''))
                 elif bottle_type == 'normal' and (not available_normal):
                     if available_original:
-                        raise _LineProblem((f'عذراً يا فندم، {NORMAL_BOTTLES} لعطر {product.name} غير متوفرة حالياً 😔. متوفر منه {ORIGINAL_BOTTLE}. تحب تطلبها؟', ''))
+                        raise _LineProblem((f'{NORMAL_BOTTLES} من {product.name} مش متاحة دلوقتي، ومتاح منه {ORIGINAL_BOTTLE}. تحبها؟', ''))
                 if bottle_type == 'normal':
                     filtered_variants = available_normal
                 elif bottle_type == 'original':
@@ -945,22 +947,22 @@ Return valid JSON in this exact format:
                     selected_variant = None
                 if not selected_variant:
                     if not filtered_variants:
-                        raise _LineProblem((f'للأسف عطر {product.name} نفد من المخزون حالياً 😔', ''))
+                        raise _LineProblem((f'{product.name} خلص دلوقتي.', ''))
                     if req_volume:
                         if bottle_type == 'original':
                             has_normal_vol = any((v.volume == req_volume for v in available_normal))
                             avail_orig_vols = '، '.join([f'{v.volume} ملي' for v in filtered_variants])
                             if has_normal_vol:
-                                raise _LineProblem((f'عذراً يا فندم، الـ {req_volume} ملي من عطر {product.name} متاح في {NORMAL_BOTTLE} فقط. ({ORIGINAL_BOTTLE} متاح منها: {avail_orig_vols}). تحب تطلب {NORMAL_BOTTLE}؟', ''))
+                                raise _LineProblem((f'{product.name} حجم {req_volume} ملي متاح في {NORMAL_BOTTLE} بس، و{ORIGINAL_BOTTLE} متاح منها {avail_orig_vols}. تحب {NORMAL_BOTTLE}؟', ''))
                             else:
-                                raise _LineProblem((f'عذراً يا فندم، حجم {req_volume} ملي غير متوفر من {ORIGINAL_BOTTLES} لعطر {product.name}. (المتاح: {avail_orig_vols}). تحب تطلب حجم تاني؟', ''))
+                                raise _LineProblem((f'{product.name} حجم {req_volume} ملي مش متاح في {ORIGINAL_BOTTLES}، والمتاح {avail_orig_vols}. تحب أنهي حجم؟', ''))
                         elif bottle_type == 'normal':
                             has_orig_vol = any((v.volume == req_volume for v in available_original))
                             avail_normal_vols = '، '.join([f'{v.volume} ملي' for v in filtered_variants])
                             if has_orig_vol:
-                                raise _LineProblem((f'عذراً يا فندم، الـ {req_volume} ملي من عطر {product.name} متاح في {ORIGINAL_BOTTLE} فقط حالياً. ({NORMAL_BOTTLES} المتاح منها: {avail_normal_vols}). تحب تطلب {ORIGINAL_BOTTLE}؟', ''))
+                                raise _LineProblem((f'{product.name} حجم {req_volume} ملي متاح في {ORIGINAL_BOTTLE} بس، و{NORMAL_BOTTLES} متاح منها {avail_normal_vols}. تحب {ORIGINAL_BOTTLE}؟', ''))
                             else:
-                                raise _LineProblem((f'عذراً يا فندم، حجم {req_volume} ملي غير متوفر من {NORMAL_BOTTLES} لعطر {product.name}. (المتاح: {avail_normal_vols}). تحب تطلب حجم تاني؟', ''))
+                                raise _LineProblem((f'{product.name} حجم {req_volume} ملي مش متاح في {NORMAL_BOTTLES}، والمتاح {avail_normal_vols}. تحب أنهي حجم؟', ''))
                         else:
                             avail_vols_display = []
                             for v in filtered_variants:
@@ -969,7 +971,7 @@ Return valid JSON in this exact format:
                                 else:
                                     avail_vols_display.append(f'{v.volume} ملي ({NORMAL_BOTTLE})')
                             vols_str = '، '.join(avail_vols_display)
-                            raise _LineProblem((f'عذراً يا فندم، حجم {req_volume} ملي غير متوفر حالياً لعطر {product.name}. المتاح: {vols_str}. تحب تطلب حاجة منهم؟', ''))
+                            raise _LineProblem((f'{product.name} حجم {req_volume} ملي مش متاح دلوقتي، والمتاح {vols_str}. تحب أنهي حجم؟', ''))
                     all_type_variants = [v for v in variants if v.bottle_type == bottle_type] if bottle_type else variants
                     if len(filtered_variants) == 1 and len(all_type_variants) == 1:
                         selected_variant = filtered_variants[0]
@@ -986,9 +988,9 @@ Return valid JSON in this exact format:
                 if bottle_type == 'original':
                     stock = selected_variant.stock or 0
                     if stock == 0:
-                        raise _LineProblem((f'عذراً يا فندم، {ORIGINAL_BOTTLES} لعطر {product.name} حجم {selected_variant.volume} ملي نفدت تماماً.', ''))
+                        raise _LineProblem((f'{ORIGINAL_BOTTLES} من {product.name} حجم {selected_variant.volume} ملي خلصت.', ''))
                     elif stock < qty:
-                        raise _LineProblem((f'عذراً يا فندم، {ORIGINAL_BOTTLES} لعطر {product.name} المتوفرة حالياً {stock} زجاجة فقط من حجم {selected_variant.volume} ملي. تحب تطلب {stock} بس؟', ''))
+                        raise _LineProblem((f'متاح {stock} زجاجة بس من {product.name} حجم {selected_variant.volume} ملي في {ORIGINAL_BOTTLES}. تحب تطلب {stock}؟', ''))
                 price = selected_variant.price
                 total_price += price * qty
                 items_to_create.append({'variant': selected_variant, 'quantity': qty, 'price': price, 'bottle_type': bottle_type, 'line_id': p_data['line_id']})
@@ -1049,7 +1051,7 @@ Return valid JSON in this exact format:
         details = [_house_choice_question(brand, store) for brand in house_questions]
         details.extend(issue[0] for issue in issues)
         if product_missing_fields:
-            details.append("تمام 👌 بس محتاج أعرف " + " و ".join(product_missing_fields) + "؟")
+            details.append("محتاج أعرف " + " و ".join(product_missing_fields) + "؟")
         return "\n".join(details), context_str
     if issues or unknown:
         details = [issue[0] for issue in issues]
@@ -1097,9 +1099,9 @@ Return valid JSON in this exact format:
                 sizes = " ولا ".join(pending["available_volumes_display"])
                 quantity = pending.get("quantity") or 1
                 count = f"{quantity} × " if quantity > 1 else ""
-                return recap + f"تمام 👌 {count}{pending.get('name')} — تحب {sizes}؟", context_str
-            return recap + f"تمام 👌 بس محتاج أعرف {missing_text}؟", context_str
-        return recap + f"تمام 👌 بس محتاج أعرف {missing_text}؟", context_str
+                return recap + f"{count}{pending.get('name')}، تحب {sizes}؟", context_str
+            return recap + f"محتاج أعرف {missing_text}؟", context_str
+        return recap + f"محتاج أعرف {missing_text}؟", context_str
 
     # 3. Product details are complete — now check for missing personal info.
     # Phones go through _looks_like_phone rather than a truthiness check: the
@@ -1113,18 +1115,18 @@ Return valid JSON in this exact format:
         personal_missing_fields.append("الاسم")
 
     if not phone and not secondary_phone:
-        personal_missing_fields.append("رقمين للموبايل واحد اساسي وواحد بديل")
+        personal_missing_fields.append("رقم الموبايل الأساسي ورقم بديل")
     elif not phone:
         personal_missing_fields.append("رقم الموبايل الأساسي")
     elif not secondary_phone:
         personal_missing_fields.append("رقم موبايل بديل")
         
     if not address:
-        personal_missing_fields.append("عنوانك بالتفصيل (المحافظة - المنطقة - رقم المنزل - اسم الشارع ) لو فى أي علامة مميزة بجوار المنزل")
+        personal_missing_fields.append("العنوان بالتفصيل: المحافظة، المنطقة، الشارع، رقم المنزل وأي علامة مميزة")
 
     if personal_missing_fields:
         missing_text = " و ".join(personal_missing_fields)
-        ask = f"تمام، عشان أأكدلك الطلب ناقصني بس {missing_text}."
+        ask = f"ناقصني للطلب {missing_text}."
         # Asking for the same fields a second time in a row gets the short form. The long
         # parenthesised address prompt going out verbatim on consecutive turns is what made
         # three replies identical in evaluation scenario F1, and re-reading the same
@@ -1152,8 +1154,8 @@ Return valid JSON in this exact format:
 
 
 def _order_success(order):
-    return (f"تم تأكيد طلبك بنجاح! 🎉 رقم الطلب هو #{order.id}.\n"
-            f"سيقوم فريق المبيعات بالتواصل معك قريباً.\n\n{_payment_instructions(order.store)}")
+    return (f"طلبك اتأكد يا فندم، ورقمه #{order.id}.\n"
+            f"فريق المبيعات هيتواصل معاك قريب.\n\n{_payment_instructions(order.store)}")
 
 
 def deliver_order_notification(order_id):
@@ -1207,7 +1209,7 @@ def create_order_in_db(store, name, phone, secondary_phone, address, total_price
                 if not variant or not variant.product.is_active or variant.product.store_id != store.pk:
                     cart.quote = {}
                     cart.save(update_fields=["quote"])
-                    return "توفر أحد الاختيارات اتغير. خلينا نراجع الطلب قبل التأكيد.", context_str
+                    return "واحد من الاختيارات مبقاش متاح. نراجع الطلب قبل التأكيد.", context_str
                 if type(item["quantity"]) is not int or item["quantity"] <= 0:
                     raise ValueError("Invalid quantity")
                 item["variant"] = variant
@@ -1220,7 +1222,7 @@ def create_order_in_db(store, name, phone, secondary_phone, address, total_price
                 if variant.bottle_type == "original" and (variant.stock or 0) < quantity:
                     cart.quote = {}
                     cart.save(update_fields=["quote"])
-                    return f"للأسف الكمية المطلوبة من {variant.product.name} مش متاحة حالياً. نراجع الكمية؟", context_str
+                    return f"الكمية دي من {variant.product.name} مش متاحة دلوقتي. نراجع الكمية؟", context_str
             total_price = sum((item["price"] * item["quantity"] for item in items_to_create), Decimal("0"))
             order = Order.objects.create(store=store, customer_name=name, customer_phone=phone,
                 secondary_phone=secondary_phone, shipping_address=address, total_price=total_price,
@@ -1236,4 +1238,4 @@ def create_order_in_db(store, name, phone, secondary_phone, address, total_price
         return _order_success(order), context_str
     except Exception:
         logger.exception("Failed to create order for store %s", store.pk)
-        return "حصل مشكلة في تسجيل الطلب يا فندم. ممكن تجرب تاني ولو المشكلة استمرت هحولك لحد من الفريق يساعدك.", ""
+        return "حصلت مشكلة في تسجيل الطلب. جرّب تاني، ولو استمرت هحولك لحد من الفريق يساعدك.", ""
diff --git a/perfume_ai/products/services/product_formatting.py b/perfume_ai/products/services/product_formatting.py
index 30dfe1e..3d53b38 100644
--- a/perfume_ai/products/services/product_formatting.py
+++ b/perfume_ai/products/services/product_formatting.py
@@ -11,7 +11,7 @@ answered, which is the exact inconsistency the prompts spend hundreds of lines
 trying to prevent. Both drifts are resolved by including the superset.
 """
 
-from itertools import islice
+from itertools import groupby, islice
 from dataclasses import dataclass, asdict
 from decimal import Decimal
 import json
@@ -101,7 +101,9 @@ LOW_BOTTLE_THRESHOLD = 3
 
 QUOTE_MARKER = "QUOTE_EVIDENCE:"
 QUOTE_PROSE_INSTRUCTION = (
-    "\nاكتب وصف العطر والرد على السؤال فقط من البيانات. لا تكتب أسعار منتجات أو أحجام؛ "
+    "\nاكتب بس الكلام اللازم للإجابة على السؤال من البيانات؛ في العادي جملة أو جملتين قصيرين، "
+    "ولو طلب تفاصيل، جاوبه على قد طلبه. "
+    "لو السؤال عن السعر أو الأحجام بس، متضيفش وصف للعطر أو سؤال متابعة. لا تكتب أسعار منتجات أو أحجام؛ "
     "الأسعار والأحجام هتتضاف تلقائياً بعد ردك. لا تنقل أي سعر من المحادثة.\n"
 )
 
@@ -159,14 +161,22 @@ def product_quotes(products, message="", max_price=None, *, filter_budget=True):
 
 
 def render_quotes(quotes, max_price=None, *, include_name=True):
+    """Share labels across adjacent variants without changing quotation order."""
     lines = []
-    for quote in quotes:
-        bottle = bottle_label(quote.bottle_type)
-        quantity = f" × {quote.quantity}" if quote.quantity != 1 else ""
-        prefix = f"{quote.name} — " if include_name else ""
-        line = f"{prefix}{bottle} {quote.volume} ملي{quantity} بـ{quote.price * quote.quantity:f} جنيه."
-        if max_price is not None and budget_tier(quote.price, max_price) == "near":
-            line += " أعلى حاجة بسيطة من ميزانيتك."
+    for _, adjacent in groupby(quotes, key=lambda q: (q.product_id, q.name, q.bottle_type)):
+        group = list(adjacent)
+        first = group[0]
+        prefix = f"{first.name} — " if include_name else ""
+        parts = []
+        for quote in group:
+            quantity = f" × {quote.quantity}" if quote.quantity != 1 else ""
+            part = f"{quote.volume} ملي{quantity} بـ{quote.price * quote.quantity:f} جنيه"
+            if max_price is not None and budget_tier(quote.price, max_price) == "near":
+                # Keep the warning beside its own size, including within a group.
+                part += (". أعلى حاجة بسيطة من ميزانيتك" if len(group) == 1
+                         else " (أعلى حاجة بسيطة من ميزانيتك)")
+            parts.append(part)
+        line = f"{prefix}{bottle_label(first.bottle_type)} " + "، و".join(parts) + "."
         lines.append(line)
     return "\n".join(lines)
 
@@ -377,11 +387,9 @@ def value_pick_note(product, variants, max_price=None):
     A hard `<= max_price` and not `budget_tier`, which is the deliberate exception among the
     budget readers. Everywhere else "near" means offerable-with-the-overage-said-out-loud, and
     `order_service._over_budget_warning` was moved onto `budget_tier` for exactly that reason.
-    Here the tier would change which size the model is told to *lead with* for every customer
-    whose next size up happens to sit inside the tolerance band — a catalogue-wide change to the
-    recommendation, with no incident asking for it. The line above is the stance, not an
-    oversight: this note carries "ابدأ بالحجم ده", and a size the customer has already priced
-    themselves out of is not the one to open with.
+    Here the hard limit preserves the eligible sizes used by the value calculation. The note
+    supplies optional facts for explicit value or size-choice questions, so a size outside the
+    customer's budget is not the basis of its value comparison.
     """
     eligible = [
         variant for variant in variants
diff --git a/perfume_ai/products/services/product_info.py b/perfume_ai/products/services/product_info.py
index 9dc4b32..8507e09 100644
--- a/perfume_ai/products/services/product_info.py
+++ b/perfume_ai/products/services/product_info.py
@@ -292,7 +292,7 @@ def _availability_only_hint(message):
         return ""
     return (
         "\n🔴 ملاحظة على الرسالة دي: العميل سأل عن التوفر بس — مفيش في كلامه أي سؤال عن سعر "
-        "ولا حجم. أكّد التوفر في جملة واحدة وكمّل بسؤال يضيّق. ❌ ممنوع تسرد أسعار أو أحجام "
+        "ولا حجم. أكّد التوفر باختصار باسم العطر؛ سؤال واحد بس لو لازم لطلبه الحالي. ❌ ممنوع تسرد أسعار أو أحجام "
         "في الرد ده.\n"
     )
 
@@ -528,7 +528,7 @@ _ABSENT_RULES = """14. 🔴🔴 العميل سمّى عطر اتّأكدنا م
    • ❌ ممنوع تقول أي كلام عن نظام أو بيانات أو كتالوج أو "مش ظاهر عندي" — العميل مش المفروض يعرف إن في حاجة زي دي أصلاً.
    • ✅ في **نفس الرد**، وبعد النفي على طول، اعرض عليه بديل أو اتنين من العطور اللي في البيانات، وقول بوضوح إنها عطور **تانية** بالاسم الكامل. ❌ ممنوع ترد بنفي لوحده ومتعرضش حاجة — العميل جاي يشتري، وحقه يلاقي حاجة يقدر ياخدها.
    • ❌ ممنوع تسرد أسعار أو مواصفات عطر تاني كأنها إجابة على العطر اللي هو سأل عنه.
-   • ❌ ممنوع تقفل الرد بسؤال فاضي أو بسلام. البدائل نفسها هي خاتمة الرد، واسأله لو حابب يعرف تفاصيل عن واحد منهم.
+   • ❌ ممنوع تقفل الرد بسؤال فاضي أو بسلام. اعرض البدائل باختصار ومن غير سؤال ختامي لازم.
 """
 
 
@@ -668,7 +668,7 @@ _ANSWER_EVERY_ROW = """14. 🔴🔴 العميل سأل عن أكتر من عط
    • ✅ جاوب على **كلهم** في الرد ده، كل واحد باسمه الكامل. لو سأل عن السعر، قول سعر كل عطر فيهم.
    • ❌ ممنوع تجاوب على واحد وتسكت عن التاني وتسيب العميل يسأل تاني. لو عطر من اللي فوق مش في ردك، الرد ناقص.
    • 🔴 القاعدة رقم 7 (متحشرش معلومات مسألش عنها) مش بتنطبق هنا — العطر التاني مش معلومة زيادة، هو نص السؤال.
-   • ✅ سطر 💡 Value Pick (أو 💡 اقتراح حجم) بيترتب **جوه كل عطر لوحده**: طبّقه على كل عطر عنده السطر ده، ومتخليهوش سبب تبدأ بعطر قبل التاني ولا تفضّل عطر على عطر. العطر اللي مالوش السطر ده، ابدأ بأحجامه زي ما هي مكتوبة.
+   • ✅ سطر 💡 Value Pick (أو 💡 اقتراح حجم) يخص **كل عطر لوحده**؛ استخدمه فقط لو العميل سأل عن القيمة أو اختيار الحجم. متخليهوش سبب تبدأ بعطر قبل التاني ولا تفضّل عطر على عطر، ومتضفش شرح سعر الملي من غير طلب.
    • ❌ ومع كل ده، القاعدة رقم 12 لسه شغالة: ممنوع تجمع الأسعار في رقم واحد ولا تقول "الإجمالي". سعر كل عطر لوحده جنب التاني — ده رد، مش مجموع.
    • 🔴 قبل ما تبعت الرد: عد أسماء العطور اللي في البيانات فوق، وتأكد إن كل اسم فيهم مكتوب في ردك وجنبه سعره. لو لقيت اسم ناقص، الرد ناقص — كمّله قبل ما تبعته.
 """
@@ -1287,19 +1287,19 @@ def get_product_info(message, history=None, store=None, conversation=None, retry
 1. 🔴 لما العطر اللي في البيانات يكون هو نفس العطر اللي العميل سأل عنه، اكتب اسمه بالإملاء الموجود في البيانات — حتى لو العميل غلط في الكتابة أو كتبه بالعربي.
    🔴🔴 لكن لو العميل سمّى عطر والبيانات فيها عطر **تاني خالص**: ده مش غلطة إملائية منه، ده عطر مختلف. ❌ ممنوع تقول إن العطر اللي سأل عنه "اسمه الصحيح" هو العطر اللي في البيانات، وممنوع توحي إنهم نفس العطر أو نفس الريحة أو نفس التركيبة. جاوب على العطر اللي هو سأل عنه، ولو مش معاك بياناته اسأله يكتبلك اسمه تاني عشان تتأكد منه — ❌ ومتوعدهوش إنك هتراجعه وترد عليه. (عميل سأل عن Acqua di Gio واتقاله "اسمه الصحيح Y Eau de Parfum" — دول عطرين مختلفين من براندين مختلفين، والعميل كرر السؤال واتقاله نفس الكلام تاني.)
 1. 🔴 فرّق بين نوع السؤال:
-   • لو العميل بيسأل عن التوافر بس (زي "عندكم سوفاج؟" أو "فيه بلو دي شانيل؟" أو "موجود عندكم X؟") → أكّد إنه متوفر **وكمّل في نفس الرد بسؤال يضيّق** (في حجم معين حابب تعرف سعره؟). ❌ ممنوع ترد "أه متوفر عندنا" وتسكت — ده رد ميت وبيوقف المحادثة. ❗ ومتبدأش تسرد أسعار ولا ترشح حجم — هو مسألش عن السعر.
-   • لو العميل سأل عن السعر أو الحجم صراحة (زي "بكام؟" أو "الأحجام إيه؟") → 🔴 ابدأ بالحجم اللي في سطر 💡 Value Pick (أو 💡 اقتراح حجم) ورشّحه بالأرقام اللي فيه، وبعدها اذكر باقي الأحجام في نص جملة عشان يعرف إن فيه خيارات. ❌ ممنوع تسرد الأسعار كلها في صف واحد زي فاتورة، وممنوع تغير أي سعر أو تخفي إن فيه أحجام تانية. ولو السطر ده هو "اقتراح حجم"، اذكر الأرقام من غير ما تقول "أحسن قيمة".
+   • لو العميل بيسأل عن التوافر بس (زي "عندكم سوفاج؟" أو "فيه بلو دي شانيل؟" أو "موجود عندكم X؟") → أكّد التوفر باختصار باسم العطر. سؤال واحد فقط لو لازم للطلب الحالي؛ متسألش لمجرد الخاتمة. ❗ متسردش أسعار ولا ترشح حجم — هو مسألش عن السعر.
+   • لو العميل سأل عن السعر أو الحجم صراحة (زي "بكام؟" أو "الأحجام إيه؟") → جاوب بالأسعار والأحجام ونوع الزجاجة اللي طلبهم، من غير وصف ريحة أو شرح سعر الملي أو اقتراح حجم أكبر. لو سأل أنهي حجم أوفر أو طلب مساعدة في الحجم، استخدم سطر 💡 Value Pick (أو 💡 اقتراح حجم) والأرقام المكتوبة فقط؛ "اقتراح حجم" مش دليل على "أحسن قيمة".
    • لو العميل سأل سؤال تفصيلي (زي "إيه مكوناته؟" أو "ثباته إيه؟") → جاوب على اللي سأله بس في جملة طبيعية واحدة أو اتنين. الثبات والفوحان ممتازين، ولو طلب مدة الثبات صراحة قول "ثباته أكتر من 8 ساعات". الفوحان بدون أرقام.
 2. لو العميل سأل عن الحجم أو الملي، اذكر كل الأحجام المتاحة كما هي مكتوبة بالظبط.
-3. لو العميل سأل رأيك، اعطيه رأي مبني على البيانات الحقيقية (المكونات، الثبات، المناسبة) في كلام طبيعي.
+3. لو العميل سأل رأيك، قول سبب قصير مبني على النوتات أو المناسبة المسجلة. متعيدش الثبات والفوحان إلا لو سأل عنهم؛ مش فرق بين العطور.
 4. ❌ ممنوع تخترع أي معلومة مش موجودة في البيانات أعلاه.
 5. 🔴 لو المنتج نفد من المخزون (Stock Status = ❌) أو حجم معين نفد، أخبر العميل بذلك بشكل لطيف واقترح عليه إنه يسأل عن عطور تانية متوفرة أو اعرض عليه الأحجام المتوفرة إن وجدت.
 6. 🔴🔴 ادمج المعلومات في كلام طبيعي. ❌ ممنوع تسرد المواصفات في شكل قائمة جامدة (الثبات: ... / الفوحان: ... / الموسم: ...). العميل بيتكلم مع بياع مش قاعدة بيانات.
 7. 🔴 متحشرش معلومات العميل مسألش عنها. جاوب على اللي اتسأل بس.
-8. 🔴 ممنوع أسئلة فاضية (زي "عايز حاجة تانية؟" أو "تحب تعرف الأسعار والأحجام المتاحة؟"). مسموح بـ CTA بيعي ذكي بس مش كل مرة (زي "تحب تطلب؟" أو "تنورنا في الستور تجرب؟"). ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
+8. 🔴 جاوب الأول في جملة أو جملتين، وسؤال واحد بس لو فيه معلومة ناقصة لازمة. لو عايز يطلب، كمّل بيانات الطلب؛ لو بيستفسر بس، متقفلش البيعة. ممنوع أسئلة فاضية أو دعوة لزيارة الستور من غير ما يطلب يجرب. ❌ ممنوع توعد بحاجة مش تقدر تعملها (زي صور أو حجز معاد أو عينات).
 9. 🔴🔴 لو العميل سأل عن {ORIGINAL_BOTTLE}، اقرأ خانة (Original Bottle) في بيانات المنتج وقول الرد المكتوب فيها بالحرف. ❌ ممنوع تقول على أي عطر إنه "حصري للمتجر" إلا لو مكتوب في الـ Brand بتاعه "عطر تركيب حصري خاص بالمتجر".
 10. 🔴 لو سأل عن الفرق بين نوعي الزجاجة أو أصالة العطر، وضّح: "{BOTTLE_DIFFERENCE}" وكل العطور تركيب من عندنا. ❌ ممنوع توحي إن {ORIGINAL_BOTTLE} فيها تركيبة أحسن أو سائل أصلي. متكررش الشرح مع كل سعر.
-11. 🔴 لو العميل بيسأل يجيب 50 ملي ولا 90 ملي: ساعده يختار من نفس العطر. لو أول مرة → الأصغر أأمن. لو عاجبه → الأكبر أوفر. ❌ ممنوع تبدل العطر.
+11. 🔴 لو العميل بيسأل يجيب 50 ملي ولا 90 ملي: ساعده يختار من نفس العطر. لو أول مرة، اقترح الأصغر؛ وصف الأكبر بأنه أوفر لازم يدعمه سطر القيمة والميزانية. ❌ ممنوع تبدل العطر.
 12. 🔴🔴 ممنوع تحسب إجمالي طلب. البيانات اللي فوق فيها أسعار الأحجام بس — مفيش فيها عربة ولا كميات ولا إجمالي. ❌ ممنوع تضرب سعر في كمية، وممنوع تجمع أسعار، وممنوع تقول "الإجمالي" أو "المجموع" أو تتكلم عن "الطلبين" أو أي عدد قطع. لو العميل سأل الطلب بقى بكام، قوله إنك هتراجع الطلب معاه وابدأ تجمع تفاصيله — الإجمالي بيتحسب من الطلب نفسه مش من الأسعار دي. (عميل اتقاله إجمالي 1560 جنيه لطلب مش موجود، وهو أصلاً قال إن ميزانيته 900.) ✅ بس خد بالك: إنك تقول سعر كل عطر لوحده جنب التاني في رد واحد **مش** إجمالي — ده هو الرد الصح لما العميل يسأل عن أكتر من عطر (زي "بكام الاتنين"). الممنوع هو إنك تجمعهم في رقم واحد أو تضربهم في كمية أو تقول كلمة "الإجمالي" أو "المجموع". ❌ ممنوع تسكت عن سعر عطر منهم عشان تتجنب القاعدة دي.
 13. 🔴🔴 لو العميل قال إنك قلت سعرين مختلفين لنفس العطر (زي "انت قولت سعرين مختلفين للسترونجر") — بص على سطر `⚠️ عطر مختلف عن` الأول. لو السعرين بيرجعوا لعطرين مختلفين على نفس الخط، يبقى **السعرين صح**: ❌ ممنوع تعتذر، وممنوع تقول إن فيه لبس أو غلط، وممنوع تسحب سعر أو تقول إن واحد منهم "هو السعر الصحيح". وضّح إن ده عطر وده عطر تاني بالاسم الكامل، وقول سعر كل واحد لوحده. ولو مش متأكد هو بيقصد أنهي واحد، اسأله. (عميل اتقاله 780 لـ Stronger With You Intensely وبعدين 700 لـ Stronger With You — دول عطرين مختلفين — وسأل، فاتقاله "أعتذر على اللبس، 700 ده السعر الصحيح"، ومشي فاكر إن Intensely بـ 700.)
 """
@@ -1382,7 +1382,7 @@ def get_product_info(message, history=None, store=None, conversation=None, retry
 
         instructions = f"""
 ═══ تعليمات ═══
-1. اقرأ سجل المحادثة جيداً. لو كان العميل يستفسر عن منتج تم التحدث عنه بالفعل في المحادثة، أجب من سياق المحادثة وتجاهل قائمة البدائل تماماً.
+1. لو العميل بيتابع سؤال عن عطر اتكلمنا عنه، جاوب على السؤال الجديد بس من سياقه وتجاهل قائمة البدائل؛ متعيدش وصفه أو أداءه.
 2. ❌ إياك أن تقول أن المنتج "غير متوفر" إذا كان قد تم إخباره بأنه متوفر في الرسائل السابقة. النظام هنا لم يتعرف على اسم منتج جديد فقط.
 3. 🔴🔴 قانون مهم جداً — فرّق بين أربع حالات مختلفة تماماً:
    • **(أ) العميل سمّى عطر معين باسمه بوضوح** (مثل "عندكو ديور هوم" أو "سعر أمبريو أرماني") والاسم ده مش موجود في البيانات المتاحة → 🔴 الرد على الحالة دي مكتوب في قاعدة رقم 14 تحت وفي قسم "سؤال معلّق" فوق: هما اللي بيقولوا إحنا اتأكدنا من الاسم ده وهو مش عندنا فعلاً، ولا لسه مش متأكدين هو قاصد إيه. اتبعها بالحرف. ❌ في الحالتين ممنوع تقول "لحظة أتأكدلك منه" ولا "هسأل وأرد عليك" — مفيش حد بيراجع الاسم ده بعد الرد، والوعد ده بيسيب العميل مستني رد عمره ما هييجي.
diff --git a/perfume_ai/products/services/promotion_service.py b/perfume_ai/products/services/promotion_service.py
index d12a24d..1c72080 100644
--- a/perfume_ai/products/services/promotion_service.py
+++ b/perfume_ai/products/services/promotion_service.py
@@ -12,6 +12,9 @@ from .usage_service import record_llm_message
 
 PROMOTION_CONTEXT = "PROMOTION_PENDING"
 LIMITATION = (
+    "طلبات الباندلز وتطبيق العروض والخصومات بيتابعها فريق خدمة العملاء، مش بقدر أنفذها هنا."
+)
+_LEGACY_LIMITATION = (
     "تسجيل طلبات الباندلز والكومبوهات أو تطبيق العروض والخصومات والكوبونات "
     "والهدايا الترويجية مش بقدر أعمله هنا؛ ده لازم يتم مع فريق خدمة العملاء."
 )
@@ -132,7 +135,7 @@ def has_promotion_context(history):
             if _explicit(content):
                 return True
         elif row.get("role") == "assistant":
-            if LIMITATION in content:
+            if any(limitation in content for limitation in (LIMITATION, _LEGACY_LIMITATION)):
                 return True
             # Also support conversations started before this guard was installed.
             if _explicit(content) and re.search(r"مندوب|فريق|بشري|human|team", _words(content)):
@@ -238,7 +241,9 @@ def reply(message, history=None, store=None, *, explain=True, usage_recorded=Fal
     history = history or []
     declined = declined_transfer(message, history)
     for row in reversed(history):
-        if row.get("role") == "assistant" and LIMITATION in row.get("content", ""):
+        if row.get("role") == "assistant" and any(
+            limitation in row.get("content", "") for limitation in (LIMITATION, _LEGACY_LIMITATION)
+        ):
             declined = declined or DECLINED in row["content"]
             break
     details = ""
diff --git a/perfume_ai/products/services/router.py b/perfume_ai/products/services/router.py
index 0e7da33..9eae284 100644
--- a/perfume_ai/products/services/router.py
+++ b/perfume_ai/products/services/router.py
@@ -224,10 +224,16 @@ def _rephrased(reply, context, history, store, regenerate, *, recommendation_int
             return sales_constraints.formulation_reply(), context, repeats
         mentioned = sales_naming.names_in(reply, [p.name for p in offer_rows])
         by_name = {p.name: p for p in offer_rows}
-        selected = [by_name[name] for name in mentioned[:2]]
+        from .sales.recommendation_response import _choice_limit
+        limit = _choice_limit(recommendation_intent)
+        selected = [by_name[name] for name in mentioned[:limit]]
         if (recommendation_intent or {}).get("recommendation_mode") == "new_goal":
-            selected = offer_rows[:2]
-        fallback = "\n".join(f"{p.name}: {factual_description(p)}." for p in selected if factual_description(p))
+            selected = offer_rows[:limit]
+        if recommendation_intent is not None:
+            from .ai.recommendation import recommendation_fit
+            fallback = recommendation_fit(selected, recommendation_intent)
+        else:
+            fallback = "\n".join(f"{p.name}: {factual_description(p)}." for p in selected if factual_description(p))
         if fallback and any(i.startswith("popularity:") for i in remaining or issues):
             fallback += " ماقدرش أجزم بمدى انتشار العطور دي."
         return append_quotation(fallback or "ماقدرش أأكد المعلومة دي عن العطر. ممكن تحدد المواصفة اللي بتسأل عنها؟", context), context, repeats
@@ -670,7 +676,7 @@ def _route(message, history=None, store=None, conversation=None):
         goodbye_words = ["سلام", "باي", "مع السلامة", "bye", "شكرا"]
         msg_clean = message.strip()
         if any(msg_clean == gw or msg_clean.startswith(gw) for gw in goodbye_words) and len(msg_clean) < 30:
-            return "نورتنا يا فندم! 😊 لو احتجت أي حاجة في المستقبل، إحنا هنا في خدمتك 24 ساعة. يوم سعيد!", ""
+            return "نورتنا يا فندم، يومك جميل.", ""
 
     # --- Classify the request ---
     # Intent extraction is deliberately NOT started here. It is only needed by the
@@ -974,7 +980,7 @@ def _route(message, history=None, store=None, conversation=None):
 
     elif request_type == "order":
         if not conversation:
-            return "محتاج الأول تبدأ محادثة جديدة عشان أقدر أسجللك الطلب يا فندم.", ""
+            return "ابدأ محادثة جديدة عشان أسجلك الطلب.", ""
         return handle_order(message, history, store, conversation)
         
     elif request_type == "order_cancel":
@@ -1003,16 +1009,16 @@ def _route(message, history=None, store=None, conversation=None):
 
             if cart and (cart.items.exists() or cart.pending_items or cart.pending_product_id):
                 clear_cart(conversation, keep_details=True)
-                return "تم إلغاء الطلب اللي كنا بنجهزه يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""
+                return "تمام، لغيت الطلب اللي كنا بنجهزه.", ""
 
             latest_order = Order.objects.filter(conversation=conversation, status="pending").order_by('-created_at').first()
             if latest_order:
                 from .order_service import change_order_status
                 change_order_status(latest_order.pk, store, "cancelled", bot_notes="تم إلغاء الطلب بواسطة البوت بناءً على طلب العميل.")
-                return "تم الغاء اخر اوردر تم تسجيله يا فندم. تحت أمرك لو حابب تختار عطر تاني أو محتاج أي مساعدة!", ""
+                return "تمام، لغيت آخر طلب اتسجل.", ""
             else:
-                return "مفيش طلب نشط حالياً عشان ألغيه يا فندم. لو كنت حابب تعمل طلب جديد أو محتاج أي مساعدة، أنا تحت أمرك!", ""
-        return "مفيش طلب نشط حالياً عشان ألغيه يا فندم. لو كنت حابب تعمل طلب جديد أو محتاج أي مساعدة، أنا تحت أمرك!", ""
+                return "مفيش طلب نشط دلوقتي عشان ألغيه.", ""
+        return "مفيش طلب نشط دلوقتي عشان ألغيه.", ""
         
     elif request_type == "handoff":
         already_handed_off = _was_already_handed_off(history)
diff --git a/perfume_ai/products/services/sales/appreciation.py b/perfume_ai/products/services/sales/appreciation.py
index 95bfb48..51c89f6 100644
--- a/perfume_ai/products/services/sales/appreciation.py
+++ b/perfume_ai/products/services/sales/appreciation.py
@@ -106,8 +106,8 @@ def detect(message, history=None):
 # call: asking which perfume it was reads as working the customer for a lead when they were only
 # being nice. We accept never learning which perfume they meant.
 RULES = """
-🎁 العميل بيمدح العطر أو بيشكرك:
-- ابدأ الرد بشكر دافئ وشخصي على كلامه الحلو. ده أهم جزء في الرد.
+العميل بيمدح العطر أو بيشكرك:
+- اشكره في جملة قصيرة مصرية طبيعية، زي "تسلم يا فندم، مبسوطين إنه عجبك." من غير إيموجي أو مبالغة أو إعادة وصف العطر وأداءه.
 - ❌🔴 ممنوع تسأله أنهي عطر ده أو تطلب منه يكتب اسم العطر. هو مش بيسأل عن حاجة، هو بيمدح.
 - ❌🔴 ممنوع تعرض عليه أي عطر تاني، وممنوع ترشح، وممنوع تسأله يشتري تاني.
 - ❌🔴 ممنوع تقول إنك مش فاهم قصده أو تطلب توضيح.
diff --git a/perfume_ai/products/services/sales/brand_response.py b/perfume_ai/products/services/sales/brand_response.py
index f0e322e..e15aeac 100644
--- a/perfume_ai/products/services/sales/brand_response.py
+++ b/perfume_ai/products/services/sales/brand_response.py
@@ -3,6 +3,7 @@ import re
 
 from . import naming
 from .brand_preference import matches_brand, preferred_brand_name
+from .recommendation_response import _choice_limit
 
 
 def is_soft_preference(intent):
@@ -30,7 +31,7 @@ def instruction(products, intent, store=None):
         "وضح براند كل بديل بتعرضه وإنه مختلف عن البراند المطلوب، واربط ترشيحك بصفة "
         "مدعومة في بياناته. اعرضه مباشرة من غير سؤال إذا كان يقبل براند تاني. "
         "ممكن تضيف اختيار من البراند اللي فضله لو مناسب، لكن متفرضوش كاختيار تاني. "
-        "إجمالي الاختيارات عطر أو عطرين فقط، شامل أي اختيار من البراند المفضل. "
+        f"اعرض لحد {_choice_limit(intent)} عطور حسب طلبه، شامل أي اختيار من البراند المفضل. "
         "الثبات والفوحان ممتازين لكل عطورنا ومش أساس للمفاضلة؛ قارن النوتات المسجلة "
         "بكلام طبيعي من غير نقل عناوين داخلية زي مش مطابق في أو Match. "
         "الطابع الخفيف للريحة لا يعني فوحان أهدى. "
@@ -46,8 +47,8 @@ def response_issues(reply, products, intent, search=None, store=None):
     if not ordered:
         return ["الرد لازم يقدم عطر مناسب من القائمة مباشرة"]
     issues = []
-    if len(ordered) > 2:
-        issues.append("اكتف بعطر أو عطرين إجمالاً، بما فيهم أي اختيار من البراند المفضل")
+    if len(ordered) > _choice_limit(intent):
+        issues.append(f"اكتف بعدد الترشيحات المطلوب: {_choice_limit(intent)}، شامل البراند المفضل")
     from ..static_faq_service import normalize_arabic
     text = normalize_arabic(reply)
     if re.search(r"مش مطابق في|ليه مناسب\s*:|\bmatch\s*:", text):
@@ -82,7 +83,7 @@ def disclose_brands(reply, products, intent, store=None):
 
     products = list(products)
     ordered = naming.names_in(reply, [p.name for p in products])
-    selected = [next(p for p in products if p.name == name) for name in ordered[:2]]
+    selected = [next(p for p in products if p.name == name) for name in ordered[:_choice_limit(intent)]]
     selected = [p for p in selected if not matches_brand(p, intent["brand"], store)]
     if not selected:
         return reply
diff --git a/perfume_ai/products/services/sales/gender.py b/perfume_ai/products/services/sales/gender.py
index e8de686..3634242 100644
--- a/perfume_ai/products/services/sales/gender.py
+++ b/perfume_ai/products/services/sales/gender.py
@@ -8,9 +8,11 @@ import re
 
 from ..static_faq_service import normalize_arabic
 
-QUESTION = "حضرتك محتاج برفان رجالي ولا حريمي"
+QUESTION = "محتاج برفان رجالي ولا حريمي؟"
+_LEGACY_QUESTION = "حضرتك محتاج برفان رجالي ولا حريمي"
 REQUIRED_MARKER = "GENDER_REQUIRED"
-MULTIPLE_QUESTION = "خلينا نختارهم واحد واحد عشان أقدر أركز معاك. تحب نبدأ بالرجالي ولا الحريمي؟"
+MULTIPLE_QUESTION = "نختار واحد واحد. نبدأ بالرجالي ولا الحريمي؟"
+_LEGACY_MULTIPLE_QUESTION = "خلينا نختارهم واحد واحد عشان أقدر أركز معاك. تحب نبدأ بالرجالي ولا الحريمي؟"
 VALID_GENDERS = frozenset({"male", "female", "unisex"})
 
 # The single copy of the vocabulary the router used inline. Relationship words
@@ -175,15 +177,18 @@ def from_customer_history(history, store=None):
 
 
 def question_pending(history=None, conversation=None):
+    questions = {_text(question) for question in (
+        QUESTION, _LEGACY_QUESTION, MULTIPLE_QUESTION, _LEGACY_MULTIPLE_QUESTION,
+    )}
     if conversation is not None:
         latest = conversation.messages.filter(role__in=("assistant", "agent"), delivery_status="sent").order_by("-created_at", "-pk").first()
         if latest:
             return latest.role == "assistant" and (
-                REQUIRED_MARKER in (latest.internal_context or "") or _text(latest.content) == _text(QUESTION)
+                REQUIRED_MARKER in (latest.internal_context or "") or _text(latest.content) in questions
                 or "MULTIPLE_RECIPIENTS" in (latest.internal_context or "")
             )
     latest = next((row for row in reversed(history or []) if row.get("role") in ("assistant", "agent")), {})
-    return latest.get("role") == "assistant" and _text(latest.get("content", "")) in {_text(QUESTION), _text(MULTIPLE_QUESTION)}
+    return latest.get("role") == "assistant" and _text(latest.get("content", "")) in questions
 
 
 def pending_answer(message, history=None, conversation=None, store=None):
diff --git a/perfume_ai/products/services/sales/occasion_preferences.py b/perfume_ai/products/services/sales/occasion_preferences.py
index f348b75..75b7bc2 100644
--- a/perfume_ai/products/services/sales/occasion_preferences.py
+++ b/perfume_ai/products/services/sales/occasion_preferences.py
@@ -5,7 +5,7 @@ import re
 from products.occasions import DAILY, normalize_occasion, normalize_text, occasion_groups
 
 
-CLARIFICATION = "هتستخدم العطر في أي مناسبة: يومي وكاجوال، شغل، خروجات وسهرات، ديت، مناسبة رسمية، ولا بحر وصيف وجيم؟"
+CLARIFICATION = "هتستخدمه يومي، للشغل، للخروجات والسهرات، للديت، لمناسبة رسمية، ولا للبحر والصيف والجيم؟"
 PENDING_KEY = "occasion_needs_clarification"
 _DELEGATE = re.compile(
     r"(?<!\w)(?:اختار(?:لي|يلي)?(?:\s+(?:انت|انتي|بنفسك))?|انت\s+اختار|انتي\s+اختاري|"
diff --git a/perfume_ai/products/services/sales/recommendation_response.py b/perfume_ai/products/services/sales/recommendation_response.py
index e7fd41c..ae3f385 100644
--- a/perfume_ai/products/services/sales/recommendation_response.py
+++ b/perfume_ai/products/services/sales/recommendation_response.py
@@ -6,12 +6,21 @@ from ..static_faq_service import normalize_arabic
 from . import naming, ranking, similarity
 
 
+def _choice_limit(intent):
+    count = (intent or {}).get("recommendation_count") or 2
+    if (intent or {}).get("budget_scope") == "total":
+        count = (intent or {}).get("purchase_quantity") or 2
+    return count if type(count) is int and count > 0 else 2
+
+
 def instruction(intent):
     if (intent or {}).get("recommendation_mode") != "new_goal":
         return ""
+    count = _choice_limit(intent)
     return (
         "\nالعميل بدأ طلب ترشيح جديد. قيّم الاختيارات حسب طلبه الحالي وابدأ بالأكثر مطابقة "
-        "في القائمة، حتى لو كنت رشحت غيره قبل كده. اعرض عطر أو عطرين فقط. "
+        f"في القائمة، حتى لو كنت رشحت غيره قبل كده. اعرض لحد {count} عطور حسب طلبه، مع سبب قصير لكل عطر. "
+        "من غير تلخيص طلبه أو سؤال ختامي لازم أو نقط مزخرفة. "
         "ممنوع تتمسك بترشيح قديم أو تنقل شروط أو مقارنة من الطلب السابق. "
         "لو المناسبة المسجلة مختلفة متدعيش إنه مناسب للمناسبة المطلوبة. "
         "الثبات والفوحان ممتازين لكل عطورنا، فمتخترعش فرق أداء بين الاختيارات. "
@@ -27,8 +36,9 @@ def response_issues(reply, products, intent, search=None):
     if current and products:
         if not ordered:
             issues.append("قدم عطر مناسب من القائمة مباشرة")
-        if len(ordered) > 2:
-            issues.append("اكتف بعطر أو عطرين")
+        count = _choice_limit(intent)
+        if len(ordered) > count:
+            issues.append(f"اكتف بعدد الترشيحات المطلوب: {count}")
         entries = search.get("ranked") or {}
         leader = entries.get(products[0].pk)
         chosen = next((p for p in products if ordered and p.name == ordered[0]), None)
diff --git a/perfume_ai/products/services/sales/scent_answers.py b/perfume_ai/products/services/sales/scent_answers.py
index a932ebf..186857b 100644
--- a/perfume_ai/products/services/sales/scent_answers.py
+++ b/perfume_ai/products/services/sales/scent_answers.py
@@ -5,7 +5,8 @@ import re
 from ..static_faq_service import normalize_arabic
 
 
-PREFERENCE_QUESTION = "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه"
+PREFERENCE_QUESTION = "بتحب الفريش، الخشبي، العود، ولا المسكر؟"
+_LEGACY_PREFERENCE_QUESTION = "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه"
 # Keep this order aligned with the choices in PREFERENCE_QUESTION.
 _CHOICES = ("fresh", "woody", "oud", "sweet")
 _ORDINALS = {
@@ -29,7 +30,9 @@ _FILLERS = frozenset({
 def question_pending(history=None):
     latest = next((row for row in reversed(history or []) if row.get("role") in ("assistant", "agent")), {})
     return latest.get("role") == "assistant" and (
-        normalize_arabic(latest.get("content", "")) == normalize_arabic(PREFERENCE_QUESTION)
+        normalize_arabic(latest.get("content", "")) in {
+            normalize_arabic(PREFERENCE_QUESTION), normalize_arabic(_LEGACY_PREFERENCE_QUESTION),
+        }
     )
 
 
diff --git a/perfume_ai/products/services/sales/value.py b/perfume_ai/products/services/sales/value.py
index 526dd7e..9d27b77 100644
--- a/perfume_ai/products/services/sales/value.py
+++ b/perfume_ai/products/services/sales/value.py
@@ -317,15 +317,11 @@ def size_value_note(value, max_price=None):
     name rather than left for the model to reach for: a 0.4% per-ml gap presented as
     "أحسن اختيار من حيث القيمة" is a claim the data does not support.
 
-    Both tiers say "ابدأ بالحجم ده" and then say what that does *not* mean. The old wording was
-    a bare "ابدأ بيه", which reads as "open your reply with this" — and because the line is
-    attached to one product and was the only per-product "start with" instruction in the whole
-    context, the model obeyed it as a choice of *perfume*. Conversation 762: a customer wanted a
-    fresh gym perfume with 1000 to spend, Invictus was ranked first and listed first with its
-    50ml in budget at 540, and the reply led with Le Male — the 4th-ranked perfume, and the only
-    one of the top four carrying this line. Invictus carries none, because its 90ml is dearer
-    per ml than its 50ml, so a perfume was passed over for a reason that has nothing to do with
-    whether it suits the gym.
+    These facts are optional guidance for an explicit value or size-choice question.
+    Ordering the model to lead with a size made ordinary price answers into unsolicited
+    upsells and could override the choice of perfume. Conversation 762: Invictus was ranked
+    first for a fresh gym request, but the reply led with the 4th-ranked Le Male because only
+    that perfume carried the size instruction. A size comparison must not rank perfumes.
 
     `max_price` is passed through to `_money_and_warning` and used for nothing else here — see
     there for why one clause of the prohibition is gated on a budget existing at all.
@@ -334,7 +330,7 @@ def size_value_note(value, max_price=None):
         return ""
 
     money, warning = _money_and_warning(value, max_price)
-    # Said on both tiers: this line orders *sizes inside this perfume*, and nothing else.
+    # Both tiers supply facts about sizes within this perfume, only when requested.
     #
     # The scope clause carried a ⚠️ and no longer does. That glyph is the over-budget marker
     # (product_formatting._BUDGET_LABELS["near"]), and four prompt rules bind it to that meaning
@@ -348,9 +344,9 @@ def size_value_note(value, max_price=None):
     # turns). It costs nothing to remove a decoy, and ❌ carries the two clauses that follow more
     # accurately anyway: they are prohibitions, which is what ❌ means in every other line here.
     scope = (
-        f"لما تذكر أسعار العطر ده، ابدأ بالحجم ده بدل ما تسرد الأسعار كلها، وبعدها اذكر "
-        f"باقي أحجامه باختصار. ده ترتيب الأحجام جوه العطر ده بس — ❌ مش سبب تبدأ بيه ردك "
-        f"ولا تفضّله على عطر تاني أنسب لطلب العميل."
+        "المعلومة دي عن أحجام نفس العطر فقط؛ ممكن تستخدمها لو العميل سأل أنهي حجم أوفر "
+        "أو طلب مساعدة في اختيار الحجم. سؤال السعر وحده مش محتاج شرح سعر الملي أو اقتراح "
+        "حجم أكبر. ده مش سبب تبدأ بيه ردك ولا تفضّله على عطر تاني أنسب لطلب العميل."
     )
 
     if value.is_strong:
@@ -365,7 +361,8 @@ def size_value_note(value, max_price=None):
         f"كمية أكتر بـ {value.extra_volume_pct}%، {money}. "
         f"{scope} "
         f"❌ الفرق في سعر الملي بسيط، فممنوع تقول عليه \"أحسن قيمة\" ولا \"أحسن اختيار\" "
-        f"ولا \"أفضل قيمة\" ولا \"أحسن قيمة مقابل سعر\" — اذكر الأرقام وسيب العميل يقرر. "
+        f"ولا \"أفضل قيمة\" ولا \"أحسن قيمة مقابل سعر\" — الفرق ده يخص سعر الملي بس، "
+        f"والاختيار حسب طلب العميل. "
         f"{warning}"
     )
 
diff --git a/perfume_ai/products/test_bug_regressions.py b/perfume_ai/products/test_bug_regressions.py
index 1315c09..3b8d09f 100644
--- a/perfume_ai/products/test_bug_regressions.py
+++ b/perfume_ai/products/test_bug_regressions.py
@@ -416,7 +416,7 @@ class Conversation1163RecommendationTests(BugFixture):
             reply, context = route("عاوز حاجة رجالي بتلفت الأنظار للشتا بـ600 جنيه", [], self.store, self.conv)
         self.conv.refresh_from_db()
         reply = sanitize_reply(reply, self.conv)
-        for expected in ("Vanilo", "Leatherio", "خشبي", "ثباته وفوحانه ممتازين", "450", "592"):
+        for expected in ("Vanilo", "Leatherio", "خشبي", "450", "592"):
             self.assertIn(expected, reply)
         self.assertIn("700", reply)
         self.assertIn("أعلى حاجة بسيطة من ميزانيتك", reply)
@@ -431,7 +431,7 @@ class Conversation1163RecommendationTests(BugFixture):
         self.b.projection = "Moderate"
         explanation = recommendation_fit([self.b], self.intent)
         self.assertIn("خشبي", explanation)
-        self.assertIn("ثباته وفوحانه ممتازين", explanation)
+        self.assertNotIn("فوحان", explanation)
         self.assertNotIn("فواح", explanation)
 
     def test_missing_facts_do_not_become_claims_of_suitability(self):
@@ -440,7 +440,7 @@ class Conversation1163RecommendationTests(BugFixture):
         self.b.season = self.b.occasion = self.b.projection = self.b.longevity = ""
         self.b.scent_tags = []
         explanation = recommendation_fit([self.b], self.intent)
-        self.assertIn("ثباته وفوحانه ممتازين", explanation)
+        self.assertIn("Leatherio", explanation)
         self.assertNotIn("مناسب", explanation)
 
     def test_valid_personal_explanation_is_preserved_without_a_retry(self):
@@ -666,7 +666,7 @@ class CartBugTests(BugFixture):
         with patch("products.services.order_service.notify_new_order", side_effect=RuntimeError("injected")):
             with self.captureOnCommitCallbacks(execute=True):
                 reply = self.turn(is_confirmed=True)
-        self.assertIn("تم تأكيد", reply)
+        self.assertIn("طلبك اتأكد", reply)
         order = Order.objects.get()
         self.assertTrue(order.notification_pending)
         self.assertFalse(Cart.objects.filter(conversation=self.conv).exists())
diff --git a/perfume_ai/products/test_gender_first.py b/perfume_ai/products/test_gender_first.py
index ab24038..6608cf7 100644
--- a/perfume_ai/products/test_gender_first.py
+++ b/perfume_ai/products/test_gender_first.py
@@ -76,7 +76,7 @@ class GenderFirstRecommendationTests(TestCase):
 
     def ask(self, updates=None, message="عايز عطر فريش"):
         result, _, model = self.call(message, updates or {"scent_tags": ["fresh"]})
-        self.assertEqual(result, ("حضرتك محتاج برفان رجالي ولا حريمي", "GENDER_REQUIRED"))
+        self.assertEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
         model.assert_not_called()
         save_message(self.conversation, "assistant", *result)
         return result
diff --git a/perfume_ai/products/test_promotions.py b/perfume_ai/products/test_promotions.py
index b7ea963..aafffd2 100644
--- a/perfume_ai/products/test_promotions.py
+++ b/perfume_ai/products/test_promotions.py
@@ -328,7 +328,7 @@ class PromotionRoutingTests(TestCase):
         self.make_cart()
         self.classifier.return_value = "order_cancel"
         response, _ = route("الغي الاوردر كله", [bot(OFFER)], self.store, self.conversation)
-        self.assertIn("إلغاء", response)
+        self.assertIn("لغيت", response)
         self.assertFalse(CartItem.objects.exists())
 
     def test_unsent_promotion_and_staff_boundary_do_not_block_normal_orders(self):
diff --git a/perfume_ai/products/tests.py b/perfume_ai/products/tests.py
index f87c988..bf33cea 100644
--- a/perfume_ai/products/tests.py
+++ b/perfume_ai/products/tests.py
@@ -854,7 +854,7 @@ class CartPersistenceTests(TestCase):
     def test_confirming_creates_an_order_and_clears_the_cart(self):
         reply, _ = self._confirm()
 
-        self.assertIn("تم تأكيد طلبك بنجاح", reply)
+        self.assertIn("طلبك اتأكد يا فندم، ورقمه #", reply)
         order = Order.objects.get(store=self.store)
         self.assertEqual(order.items.count(), 1)
         self.assertEqual(order.customer_name, "محمد")
@@ -950,7 +950,7 @@ class CartCancellationTests(TestCase):
         reply, _ = self._cancel()
 
         self.assertFalse(Cart.objects.filter(conversation=self.conversation).exists())
-        self.assertIn("إلغاء", reply)
+        self.assertIn("لغيت", reply)
 
     def test_cancelling_a_confirmed_order_restores_original_bottles(self):
         """Replaces the oil-restoring version.
@@ -1133,7 +1133,7 @@ class PerStorePaymentInstructionsTests(TestCase):
         """A store missing StoreSettings entirely must not break the order."""
         reply, _ = self._confirm_order()
 
-        self.assertIn("تم تأكيد طلبك بنجاح", reply)
+        self.assertIn("طلبك اتأكد يا فندم، ورقمه #", reply)
         self.assertIn(PAYMENT_FALLBACK, reply)
         self.assertNotIn(self.LEAKED, reply)
 
@@ -1150,7 +1150,7 @@ class PerStorePaymentInstructionsTests(TestCase):
         reply, _ = self._confirm_order()
         order = Order.objects.get(store=self.store)
 
-        self.assertIn("تم تأكيد طلبك بنجاح", reply)
+        self.assertIn("طلبك اتأكد يا فندم، ورقمه #", reply)
         self.assertIn(f"#{order.id}", reply)
 
 
@@ -4066,16 +4066,13 @@ class ScriptedRepliesSurviveSanitizingTests(TestCase):
                          (original.pk, "original", Decimal("900")))
 
     def test_the_cart_cancellation_reply_is_untouched(self):
-        """Contains "تحب تشوف حاجة تانية" — near-miss on the filler pattern."""
-        scripted = "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟"
+        """The short scripted cancellation survives sanitizing."""
+        scripted = "تمام، شلت الطلب."
 
         self.assertEqual(sanitize_reply(scripted), scripted)
 
     def test_the_goodbye_reply_is_untouched(self):
-        scripted = (
-            "نورتنا يا فندم! 😊 لو احتجت أي حاجة في المستقبل، إحنا هنا في خدمتك "
-            "24 ساعة. يوم سعيد!"
-        )
+        scripted = "نورتنا يا فندم، يومك جميل."
 
         self.assertEqual(sanitize_reply(scripted), scripted)
 
@@ -4284,7 +4281,7 @@ class PersonaConsolidationTests(TestCase):
             ("diagnose rejection", "مش عاجبني"),
             ("niche pivot", "نيش"),
             ("value pick", "Value Pick"),
-            ("no dead-end availability", "رد ميت"),
+            ("answer availability directly", "أكّد التوفر باختصار"),
         ):
             with self.subTest(play=play):
                 self.assertIn(marker, prompt)
@@ -4332,9 +4329,9 @@ class PersonaConsolidationTests(TestCase):
             ("banned florid words", "عبير"),
             ("banned robotic phrases", "يسعدني مساعدتك"),
             ("store not محل", 'ممنوع كلمة "محل"'),
-            ("1-4 short sentences", "4 جمل قصيرة"),
+            ("1-2 short sentences", "جملة أو جملتين قصيرتين"),
             ("no spec-list dumps", "ممنوع تسرد"),
-            ("max 2-3 recommendations", "ترشيحين أو تلاتة"),
+            ("max two recommendations by default", "عطر أو عطرين"),
             ("no (تركيب) label on sizes", '(تركيب)'),
             ("only in-stock sizes offered", "الأحجام المتوفرة بس"),
             ("brand vs original same juice", "العطر في الاتنين نفس التركيبة من عندنا"),
@@ -5819,13 +5816,12 @@ class ValueLanguageTests(TestCase):
         """The real prices from the transcript are an 18.3% saving, which the 22% threshold
         deliberately leaves in the weak tier. Sauvage is the shop's most-asked perfume and
         eval scenario P1 quotes it, so this records the consequence rather than hiding it:
-        the reply still leads with the 90ml, it just no longer calls it the best value."""
+        its size-value evidence remains available without a strong-value claim."""
         value = sales_value.size_value(list(self.product.variants.all()))
 
         self.assertFalse(value.is_strong)
         self.assertEqual(value.saving_pct.quantize(Decimal("0.1")), Decimal("18.3"))
         self.assertIn("💡 اقتراح حجم", self._note())
-        self.assertIn("ابدأ بالحجم ده", self._note())
 
     def test_a_comfortably_strong_saving_is_sold_as_the_value_pick(self):
         self.large.price = _comfortably_strong_price(self.small.price, self.small.volume, 90)
@@ -5847,14 +5843,13 @@ class ValueLanguageTests(TestCase):
         self.assertIn("💡 اقتراح حجم", note)
 
     def test_a_small_saving_still_names_the_size_and_the_numbers(self):
-        """Silence here would regress eval scenario P1, which marks a bare price list with
-        no recommendation as a weak sales answer."""
+        """Both sizes remain available for an explicit value comparison."""
         self._weaken_to(5)
 
         note = self._note()
 
         self.assertIn("90 ملي", note)
-        self.assertIn("ابدأ بالحجم ده", note)
+        self.assertIn("50 ملي", note)
         self.assertIn("سعر الملي أرخص", note)
 
     def test_a_small_saving_bans_the_superlatives_by_name(self):
@@ -5911,13 +5906,8 @@ class ValueLanguageTests(TestCase):
 
         self.assertIsInstance(value.saving_pct, Decimal)
 
-    def test_both_tiers_scope_the_lead_instruction_to_the_size(self):
-        """Conversation 762. The line used to end in a bare "ابدأ بيه", which reads as "open
-        your reply with this" — and since it is attached to one product and was the only
-        per-product "start with" instruction in the context, the model obeyed it as a choice of
-        *perfume*. Invictus was ranked first with its 50ml in budget and lost to the 4th-ranked
-        perfume, the only one of the top four carrying this line.
-        """
+    def test_both_tiers_scope_the_value_instruction_to_a_size_choice(self):
+        """Size value supports an explicit size decision without changing perfume ranking."""
         for label, saving in (("strong", None), ("weak", 5)):
             with self.subTest(tier=label):
                 if saving is not None:
@@ -5930,8 +5920,8 @@ class ValueLanguageTests(TestCase):
 
                 note = self._note()
 
-                self.assertIn("لما تذكر أسعار العطر ده", note)
-                self.assertIn("ترتيب الأحجام جوه العطر ده بس", note)
+                self.assertIn("عن أحجام نفس العطر فقط", note)
+                self.assertIn("لو العميل سأل أنهي حجم أوفر", note)
                 self.assertNotIn("ابدأ بيه بدل", note)
 
     def test_neither_tier_invites_preferring_this_perfume(self):
@@ -7084,12 +7074,12 @@ class SalesScenarioTests(TestCase):
         prompt, _ = self._route_recommendation("عايز برفان لمراتي هدية بس مش عارف هي بتحب إيه", {"gender": "female"})
         self.assertEqual(prompt, "")
         self.assertEqual(self.last_context, "SCENT_PREFERENCE_REQUIRED")
-        self.assertIn("قولي ذوقك.", self.last_reply)
+        self.assertIn("بتحب الفريش، الخشبي، العود، ولا المسكر؟", self.last_reply)
 
     def test_f_only_one_style_question_is_asked(self):
         self._route_recommendation("عايز هدية لمراتي مش عارف ذوقها", {"gender": "female"})
-        self.assertEqual(self.last_reply.count("قولي ذوقك"), 1)
-        self.assertIn("الحاجات المسكره", self.last_reply)
+        self.assertEqual(self.last_reply.count("بتحب الفريش"), 1)
+        self.assertIn("المسكر", self.last_reply)
 
     def test_f_unknown_gift_taste_never_gets_a_guaranteed_product(self):
         self._route_recommendation("عايز هدية لمراتي مش عارف ذوقها", {"gender": "female"})
@@ -8795,7 +8785,7 @@ class OrderEditNotCancelTests(TestCase):
         reply, handle_order = self._route("مش عايز Le Male")
 
         self.assertFalse(handle_order.called)
-        self.assertIn("إلغاء", reply)
+        self.assertIn("لغيت", reply)
 
     def test_a_blanket_cancellation_still_cancels_a_multi_item_cart(self):
         self._cart(self.le_male, self.noirvel)
@@ -8803,7 +8793,7 @@ class OrderEditNotCancelTests(TestCase):
         reply, handle_order = self._route("الغي الاوردر")
 
         self.assertFalse(handle_order.called)
-        self.assertIn("إلغاء", reply)
+        self.assertIn("لغيت", reply)
         self.assertFalse(
             CartItem.objects.filter(cart__conversation=self.conversation).exists()
         )
@@ -18803,10 +18793,7 @@ class Conv973SaidItTwiceTests(TestCase):
         pins them against sanitizing. A scripted line said twice is still byte-for-byte itself."""
         from products.services import router
 
-        goodbye = (
-            "نورتنا يا فندم! 😊 لو احتجت أي حاجة في المستقبل، إحنا هنا في خدمتك "
-            "24 ساعة. يوم سعيد!"
-        )
+        goodbye = "نورتنا يا فندم، يومك جميل."
         StaticFAQ.objects.create(
             store=self.store, question="الشحن بكام؟", keywords="شحن, توصيل",
             answer="الشحن 60 جنيه لكل محافظات مصر، والتوصيل من 2 لـ 4 أيام.",
@@ -19381,3 +19368,68 @@ class DescribesRatherThanNamesTests(TestCase):
 
         self.assertFalse(naming.describes_rather_than_names(""))
         self.assertFalse(naming.describes_rather_than_names(None))
+
+
+class RecommendationReplyScopeTests(TestCase):
+    """Fallbacks retain requested scope without reciting shared performance per pick."""
+
+    def setUp(self):
+        self.store = Store.objects.create(name="Reply scope")
+        brand = Brand.objects.create(store=self.store, name="House")
+        self.products = [
+            Product.objects.create(store=self.store, brand=brand, name=name,
+                                   scent_tags=["fresh"], occasion="Office")
+            for name in ("Cedar Day", "Green Walk", "Clean Air")
+        ]
+
+    def test_fallback_does_not_recap_unrequested_performance(self):
+        from products.services.ai.scent_recommendation import _fallback
+
+        selected, reply = _fallback(self.products, {})
+
+        self.assertEqual(selected, self.products[:2])
+        self.assertTrue(all(product.name in reply for product in selected))
+        self.assertIn("فريش", reply)
+        self.assertNotIn("ثبات", reply)
+        self.assertNotIn("فوحان", reply)
+
+    def test_fallback_states_requested_shared_performance_once(self):
+        from products.services.ai.scent_recommendation import _fallback
+        from products.services.performance_policy import performance_turn
+
+        with performance_turn("رشحلي عطر فريش وثباته كام ساعة؟"):
+            selected, reply = _fallback(self.products, {})
+
+        self.assertTrue(all(product.name in reply for product in selected))
+        self.assertEqual(reply.count("ثبات"), 1)
+        self.assertIn("أكتر من 8 ساعات", reply)
+        self.assertNotIn("فوحان", reply)
+
+    def test_fallback_keeps_explicit_recommendation_count(self):
+        from products.services.ai.scent_recommendation import _fallback
+
+        selected, reply = _fallback(self.products, {"recommendation_count": 3})
+
+        self.assertEqual(selected, self.products)
+        self.assertTrue(all(product.name in reply for product in self.products))
+
+    def test_new_goal_validation_honors_explicit_count(self):
+        from products.services.sales.recommendation_response import response_issues
+
+        reply = ". ".join(f"{product.name}: طابعه فريش" for product in self.products) + "."
+        issues = response_issues(reply, self.products,
+                                 {"recommendation_mode": "new_goal", "recommendation_count": 3})
+
+        self.assertEqual(issues, [])
+
+    def test_soft_brand_preference_keeps_all_requested_choices_and_disclosures(self):
+        from products.services.sales.brand_response import disclose_brands, response_issues
+
+        preferred = Brand.objects.create(store=self.store, name="Preferred")
+        intent = {"brand": preferred.name, "recommendation_count": 3}
+        reply = ". ".join(f"{product.name}: طابعه فريش" for product in self.products) + "."
+
+        self.assertEqual(response_issues(reply, self.products, intent, store=self.store), [])
+        disclosed = disclose_brands(reply, self.products, intent, self.store)
+        for product in self.products:
+            self.assertIn(f"{product.name} من House", disclosed)


# New file: products/test_response_style.py
"""Reworded questions keep their historical answers and checkout safeguards."""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Cart, Conversation, Order, Product, ProductVariant, Store, StoreSettings
from products.services.ai.intent import extract_intent
from products.services.conversation_service import save_message
from products.services.handoff import handoff_offer_reply
from products.services.order_service import _already_asked_for_details, _ask_which_perfume, handle_order
from products.services.product_formatting import format_products
from products.services.router import route
from products.services.router import _rephrased
from products.services.performance_policy import performance_turn
from products.services import promotion_service
from products.services.sales import gender, scent_answers
from products.services.sales.occasion_preferences import delegates_occasion


GENDER_QUESTIONS = (
    "حضرتك محتاج برفان رجالي ولا حريمي",
    "محتاج برفان رجالي ولا حريمي؟",
)
MULTIPLE_QUESTIONS = (
    "خلينا نختارهم واحد واحد عشان أقدر أركز معاك. تحب نبدأ بالرجالي ولا الحريمي؟",
    "نختار واحد واحد. نبدأ بالرجالي ولا الحريمي؟",
)
SCENT_QUESTIONS = (
    "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه",
    "بتحب الفريش، الخشبي، العود، ولا المسكر؟",
)
PRODUCT_QUESTIONS = (
    "تمام، بس مش واضحلي عايز تطلب أنهي عطر. ممكن تقولي اسم العطر اللي عايزه؟",
    "تحب تطلب أنهي عطر؟ قولي اسمه.",
)
PROMOTION_LIMITATIONS = (
    "تسجيل طلبات الباندلز والكومبوهات أو تطبيق العروض والخصومات والكوبونات والهدايا الترويجية مش بقدر أعمله هنا؛ ده لازم يتم مع فريق خدمة العملاء.",
    "طلبات الباندلز وتطبيق العروض والخصومات بيتابعها فريق خدمة العملاء، مش بقدر أنفذها هنا.",
)


def question_history(question):
    return [{"role": "assistant", "content": question}]


class HistoricalQuestionAnswersTests(SimpleTestCase):
    def test_gender_answers_and_short_confirmations_stay_pending_for_both_questions(self):
        for question in (*GENDER_QUESTIONS, *MULTIPLE_QUESTIONS):
            history = question_history(question)
            for answer, expected in (("رجالي", "male"), ("حريمي", "female"), ("للجنسين", "unisex")):
                with self.subTest(question=question, answer=answer):
                    self.assertTrue(gender.pending_answer(answer, history))
                    self.assertEqual(gender.customer_choice(answer), expected)
            for answer in ("تمام", "ماشي", "اختارلي"):
                with self.subTest(question=question, answer=answer):
                    self.assertTrue(gender.pending_answer(answer, history))
                    self.assertIsNone(gender.customer_choice(answer))

    def test_scent_ordinals_keep_all_four_options_in_order(self):
        for question in SCENT_QUESTIONS:
            history = question_history(question)
            for answer, expected in (("الأول", ["fresh"]), ("التاني", ["woody"]),
                                     ("٣", ["oud"]), ("الرابع", ["sweet"]),
                                     ("آخر واحد", ["sweet"]), ("الأول والتالت", ["fresh", "oud"])):
                with self.subTest(question=question, answer=answer):
                    self.assertEqual(scent_answers.answer_updates(answer, history).get("scent_tags"), expected)

    def test_scent_extraction_preserves_named_answers_and_ordinals_when_model_fails(self):
        for question in SCENT_QUESTIONS:
            for answer, expected in (("الفريش", ["fresh"]), ("خشبي", ["woody"]),
                                     ("عود", ["oud"]), ("مسكر", ["sweet"]), ("التاني", ["woody"])):
                with self.subTest(question=question, answer=answer), \
                     patch("products.services.ai.intent.chat", return_value="invalid JSON"):
                    self.assertEqual(extract_intent(answer, history=question_history(question)).get("scent_tags"), expected)

    def test_scent_delegation_and_confirmations_do_not_select_an_occasion(self):
        for question in SCENT_QUESTIONS:
            history = question_history(question)
            with self.subTest(question=question):
                self.assertEqual(scent_answers.answer_updates("اختارلي", history).get("scent_open"), True)
                self.assertFalse(delegates_occasion("اختارلي", history))
                self.assertEqual(scent_answers.answer_updates("تمام", history), {})

    def test_later_questions_or_agent_messages_end_pending_questions(self):
        for question in (*GENDER_QUESTIONS, *SCENT_QUESTIONS):
            for boundary in ({"role": "assistant", "content": "تحب أنهي حجم؟"},
                             {"role": "agent", "content": question}):
                history = question_history(question) + [boundary]
                with self.subTest(question=question, boundary=boundary):
                    self.assertFalse(gender.pending_answer("تمام", history))
                    self.assertEqual(scent_answers.answer_updates("الأول", history), {})

    def test_short_approvals_to_handoff_do_not_apply_to_gender_or_scent(self):
        for question in ("تحب أحولك لفريق خدمة العملاء؟", "حابب تتكلم مع حد من الفريق؟"):
            with self.subTest(question=question):
                self.assertTrue(handoff_offer_reply("ماشي", question_history(question)))
                self.assertFalse(handoff_offer_reply("لا شكرا", question_history(question)))
        for question in (*GENDER_QUESTIONS, *SCENT_QUESTIONS):
            with self.subTest(question=question):
                self.assertFalse(handoff_offer_reply("ماشي", question_history(question)))

    def test_declined_promotion_transfer_is_preserved_for_historical_limitations(self):
        for limitation in PROMOTION_LIMITATIONS:
            history = question_history(limitation + "\n\nلو حابب تشتري عطور بالسعر العادي من غير العرض، قولي.")
            with self.subTest(limitation=limitation):
                self.assertTrue(promotion_service.blocks_checkout("اسمي أحمد ورقمي 01000000000", history))
                reply, context = promotion_service.reply("اسمي أحمد", history, explain=False)
                self.assertEqual(context, "PROMOTION_PENDING")
                self.assertNotIn("تحب أحولك", reply)
                self.assertIn("بالسعر العادي", reply)


class FixedReplyCheckoutCompatibilityTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Fixed reply compatibility")
        StoreSettings.objects.create(store=self.store, payment_instructions="تعليمات الدفع المحفوظة\nحساب المتجر: TEST")
        self.brand = Brand.objects.create(store=self.store, name="House")
        self.product = Product.objects.create(store=self.store, brand=self.brand, name="Shared Breeze", gender="unisex")
        self.variant = ProductVariant.objects.create(product=self.product, volume=50, price=Decimal("420.25"))
        self.conversation = Conversation.objects.create(store=self.store)

    def turn(self, **updates):
        data = {"products": [{"name": self.product.name, "volume": 50, "quantity": 2, "bottle_type": "normal"}],
                "customer_name": "أحمد", "customer_phone": "01000000000", "customer_secondary_phone": "01100000000",
                "shipping_address": "القاهرة، شارع النيل، عمارة 12", "is_confirmed": False}
        data.update(updates)
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            result = handle_order("تمام", [], self.store, self.conversation)
        save_message(self.conversation, "assistant", *result)
        return result[0]

    def test_unresolved_product_prompt_names_earlier_offer_for_old_and_new_wording(self):
        save_message(self.conversation, "assistant", "Shared Breeze مناسب ليك.",
                     internal_context=format_products([self.product]))
        for question in PRODUCT_QUESTIONS:
            with self.subTest(question=question):
                save_message(self.conversation, "assistant", question)
                reply = _ask_which_perfume(self.conversation, self.store)
                self.assertIn("Shared Breeze", reply)
                self.assertIn("الحجم", reply)

    def test_delivered_old_and_new_contact_questions_are_recognized(self):
        for question in ("تمام، عشان أأكدلك الطلب ناقصني بس الاسم ورقم الموبايل.",
                         "ناقصني للطلب الاسم ورقم الموبايل.", "ولسه ناقص الاسم ورقم الموبايل."):
            with self.subTest(question=question):
                save_message(self.conversation, "assistant", question)
                self.assertTrue(_already_asked_for_details(self.conversation))

    def test_unsent_question_cannot_replace_a_delivered_contact_request(self):
        save_message(self.conversation, "assistant", "ناقصني للطلب الاسم ورقم الموبايل.")
        save_message(self.conversation, "assistant", "تحب أنهي حجم؟", delivery_status="failed")
        self.assertTrue(_already_asked_for_details(self.conversation))

    def test_contact_collection_keeps_both_phones_and_address_required(self):
        reply = self.turn(customer_name=None, customer_phone=None, customer_secondary_phone=None, shipping_address=None,
                          is_confirmed=True)
        for field in ("الاسم", "بديل", "المحافظة", "المنطقة", "الشارع", "المنزل"):
            self.assertIn(field, reply)
        self.assertFalse(Order.objects.exists())
        reply = self.turn(customer_secondary_phone=None, is_confirmed=True)
        self.assertIn("بديل", reply)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Cart.objects.get(conversation=self.conversation).items.get().quantity, 2)

    def test_full_summary_precedes_confirmation_and_preserves_payment_instructions(self):
        summary = self.turn(is_confirmed=True)
        for fact in ("أحمد", "01000000000", "01100000000", "القاهرة، شارع النيل، عمارة 12",
                     "2 × Shared Breeze", "50ml", "840.50", "نأكد الطلب"):
            self.assertIn(fact, summary)
        self.assertFalse(Order.objects.exists())
        self.assertIn("message_id", Cart.objects.get(conversation=self.conversation).quote)
        reply = self.turn(is_confirmed=True)
        self.assertTrue(Order.objects.filter(conversation=self.conversation).exists(), reply)
        order = Order.objects.get(conversation=self.conversation)
        self.assertEqual(order.total_price, Decimal("840.50"))
        self.assertEqual(order.items.get().quantity, 2)
        self.assertIn(f"#{order.pk}", reply)
        self.assertIn("تعليمات الدفع المحفوظة\nحساب المتجر: TEST", reply)

    def test_delivered_legacy_gender_prompt_routes_short_confirmation_back_to_gender(self):
        for question in GENDER_QUESTIONS:
            with self.subTest(question=question):
                save_message(self.conversation, "assistant", question)
                with patch("products.services.router.classify", return_value="order"), \
                     patch("products.services.router.extract_intent", return_value={}), \
                     patch("products.services.router.confident_catalogue_match", return_value=None), \
                     patch("products.services.product_resolver.confirm_unplaced", return_value=None), \
                     patch("products.services.order_service.chat", return_value='{"products": []}'):
                    reply, context = route("تمام", [], self.store, self.conversation)
                self.assertEqual(context, "GENDER_REQUIRED")
                self.assertIn("رجالي", reply)
                self.assertIn("حريمي", reply)
                self.assertFalse(Cart.objects.filter(conversation=self.conversation).exists())

    def test_recommendation_fallback_honors_requested_count_and_omits_unsolicited_performance(self):
        products = [self.product]
        for name, scent in (("Forest Path", "woody"), ("Oud Night", "oud")):
            products.append(Product.objects.create(store=self.store, brand=self.brand, name=name,
                                                    gender="unisex", scent_tags=[scent]))
        context = format_products(products)
        draft = "Shared Breeze. Prompt says mention these notes."
        with performance_turn("رشحلي 3 عطور"):
            reply, _, _ = _rephrased(draft, context, [], self.store, lambda hint: (draft, context),
                                     recommendation_intent={"recommendation_mode": "new_goal", "recommendation_count": 3},
                                     recommendation_candidates=products)
        for name in ("Shared Breeze", "Forest Path", "Oud Night"):
            self.assertIn(name, reply)
        self.assertIn("خشبي", reply)
        self.assertIn("عودي", reply)
        self.assertNotIn("ثبات", reply)
        self.assertNotIn("فوحان", reply)

    def test_recommendation_fallback_answers_requested_performance_once(self):
        product = Product.objects.create(store=self.store, brand=self.brand, name="Forest Path", gender="unisex",
                                         scent_tags=["woody"])
        products = [self.product, product]
        context = format_products(products)
        draft = "Shared Breeze. Prompt says mention these notes."
        with performance_turn("رشحلي عطرين وثباتهم وفوحانهم إيه؟"):
            reply, _, _ = _rephrased(draft, context, [], self.store, lambda hint: (draft, context),
                                     recommendation_intent={"recommendation_mode": "new_goal"},
                                     recommendation_candidates=products)
        self.assertIn("Shared Breeze", reply)
        self.assertIn("Forest Path", reply)
        self.assertEqual(reply.count("ثبات"), 1)
        self.assertEqual(reply.count("فوحان"), 1)


# New file: products/test_compact_quotes.py
"""Compact quotations preserve each catalogue fact and validation evidence."""

from copy import deepcopy
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store
from products.services.product_formatting import (
    ProductQuote,
    append_quotation,
    product_price_issues,
    product_quotes,
    quote_context,
    quote_evidence,
    render_quotes,
)
from products.services.reply_sanitizer import sanitize_reply


class CompactQuoteRenderingTests(SimpleTestCase):
    def quote(self, volume, price, *, product_id=1, name="Dior Sauvage",
              bottle_type="normal", quantity=1):
        return ProductQuote(product_id, volume, name, volume, bottle_type,
                            quantity, Decimal(price))

    def test_consecutive_sizes_share_a_label_and_keep_their_input_order(self):
        quotes = [self.quote(90, "180.00"), self.quote(50, "123.45"),
                  self.quote(100, "190.00")]

        text = render_quotes(iter(quotes))

        self.assertEqual(text.count("Dior Sauvage"), 1)
        self.assertEqual(text.count("زجاجة الستور الخاصة بينا"), 1)
        self.assertEqual(text.split("، و"), [
            "Dior Sauvage — زجاجة الستور الخاصة بينا 90 ملي بـ180.00 جنيه",
            "50 ملي بـ123.45 جنيه",
            "100 ملي بـ190.00 جنيه.",
        ])

    def test_each_product_or_bottle_change_starts_a_new_group_without_reordering(self):
        quotes = [self.quote(50, "123.45"), self.quote(90, "180.00"),
                  self.quote(50, "299.99", bottle_type="original"),
                  self.quote(90, "499.00", bottle_type="original"),
                  self.quote(50, "100.00", product_id=2, name="Afnan 9PM"),
                  self.quote(90, "150.00", product_id=2, name="Afnan 9PM"),
                  self.quote(100, "190.00")]

        lines = render_quotes(quotes).splitlines()

        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("Dior Sauvage — زجاجة الستور الخاصة بينا"))
        self.assertTrue(lines[1].startswith("Dior Sauvage — زجاجة اوريجنال"))
        self.assertTrue(lines[2].startswith("Afnan 9PM — زجاجة الستور الخاصة بينا"))
        self.assertTrue(lines[3].startswith("Dior Sauvage — زجاجة الستور الخاصة بينا"))
        self.assertIn("50 ملي بـ299.99 جنيه، و90 ملي بـ499.00 جنيه", lines[1])

    def test_identical_names_do_not_merge_distinct_catalogue_products(self):
        quotes = [self.quote(50, "123.45"),
                  self.quote(90, "180.00", product_id=2)]

        self.assertEqual(len(render_quotes(quotes).splitlines()), 2)

    def test_grouped_quantities_keep_exact_decimal_totals(self):
        text = render_quotes([self.quote(50, "123.4567", quantity=2),
                              self.quote(90, "180.00", quantity=3)])

        self.assertEqual(text.count("Dior Sauvage"), 1)
        self.assertIn("50 ملي × 2 بـ246.9134 جنيه", text)
        self.assertIn("90 ملي × 3 بـ540.00 جنيه", text)
        self.assertNotIn("246.91 جنيه", text)

    def test_budget_warning_stays_with_each_near_budget_size(self):
        quotes = [self.quote(50, "90.00"), self.quote(90, "105.00"),
                  self.quote(100, "95.00"), self.quote(120, "110.00")]

        clauses = render_quotes(quotes, Decimal("100.00")).split("، و")

        self.assertEqual(len(clauses), 4)
        self.assertNotIn("أعلى حاجة بسيطة", clauses[0])
        self.assertIn("90 ملي بـ105.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)", clauses[1])
        self.assertNotIn("أعلى حاجة بسيطة", clauses[2])
        self.assertIn("120 ملي بـ110.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)", clauses[3])

    def test_quantity_does_not_change_existing_per_unit_budget_tiering(self):
        text = render_quotes([self.quote(50, "90.00", quantity=2),
                              self.quote(90, "105.00", quantity=2)], Decimal("100.00"))

        clauses = text.split("، و")
        self.assertEqual(len(clauses), 2)
        self.assertNotIn("أعلى حاجة بسيطة", clauses[0])
        self.assertIn("90 ملي × 2 بـ210.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)", clauses[1])

    def test_omitting_names_still_keeps_separate_product_and_bottle_groups(self):
        quotes = [self.quote(50, "123.45"), self.quote(90, "180.00"),
                  self.quote(50, "299.99", bottle_type="original"),
                  self.quote(50, "100.00", product_id=2, name="Afnan 9PM")]

        lines = render_quotes(quotes, include_name=False).splitlines()

        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "زجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه، و90 ملي بـ180.00 جنيه.")
        self.assertEqual(lines[1], "زجاجة اوريجنال 50 ملي بـ299.99 جنيه.")
        self.assertEqual(lines[2], "زجاجة الستور الخاصة بينا 50 ملي بـ100.00 جنيه.")
        self.assertNotIn("Dior Sauvage", "\n".join(lines))
        self.assertNotIn("Afnan 9PM", "\n".join(lines))

    def test_single_quote_output_remains_compatible(self):
        quote = self.quote(90, "105.00", quantity=2, bottle_type="original")
        for include_name, expected in (
            (True, "Dior Sauvage — زجاجة اوريجنال 90 ملي × 2 بـ210.00 جنيه. أعلى حاجة بسيطة من ميزانيتك."),
            (False, "زجاجة اوريجنال 90 ملي × 2 بـ210.00 جنيه. أعلى حاجة بسيطة من ميزانيتك."),
        ):
            with self.subTest(include_name=include_name):
                self.assertEqual(render_quotes([quote], Decimal("100.00"), include_name=include_name), expected)

    def test_empty_quotes_preserve_empty_rendering_and_context(self):
        self.assertEqual(render_quotes([]), "")
        self.assertEqual(render_quotes(iter([]), include_name=False), "")
        self.assertEqual(quote_context("existing facts", [], append=True), "existing facts")


class CompactQuoteEvidenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        store = Store.objects.create(name="Compact quotation test")
        brand = Brand.objects.create(store=store, name="Dior")
        cls.product = Product.objects.create(store=store, brand=brand, name="Dior Sauvage", gender="male")
        cls.small = ProductVariant.objects.create(product=cls.product, volume=50, price=Decimal("123.45"))
        cls.medium = ProductVariant.objects.create(product=cls.product, volume=90, price=Decimal("180.00"))
        cls.large = ProductVariant.objects.create(product=cls.product, volume=100, price=Decimal("190.00"))
        cls.original = ProductVariant.objects.create(product=cls.product, volume=50, price=Decimal("299.99"), bottle_type="original", stock=2)

    def test_selection_order_and_evidence_survive_compact_append_and_validation(self):
        quotes = product_quotes([self.product], max_price=Decimal("180.00"))
        self.assertEqual([q.variant_id for q in quotes], [self.medium.pk, self.small.pk, self.large.pk])
        context = quote_context("catalogue facts", quotes, append=True, max_price=Decimal("180.00"))
        evidence = quote_evidence(context)

        self.assertEqual(evidence["quotes"], [
            {"product_id": self.product.pk, "variant_id": self.medium.pk, "name": "Dior Sauvage", "volume": 90, "bottle_type": "normal", "quantity": 1, "price": "180.00"},
            {"product_id": self.product.pk, "variant_id": self.small.pk, "name": "Dior Sauvage", "volume": 50, "bottle_type": "normal", "quantity": 1, "price": "123.45"},
            {"product_id": self.product.pk, "variant_id": self.large.pk, "name": "Dior Sauvage", "volume": 100, "bottle_type": "normal", "quantity": 1, "price": "190.00"},
        ])
        self.assertEqual(evidence["budget"], "180.00")
        self.assertTrue(evidence["append"])
        self.assertEqual(evidence["text"].count("Dior Sauvage"), 1)
        reply = append_quotation("متاح يا فندم.", context)
        self.assertEqual(reply.count(evidence["text"]), 1)
        self.assertEqual(append_quotation(reply, context), reply)
        self.assertEqual(product_price_issues(reply, [self.product], evidence=evidence), [])

    def test_compact_quantity_totals_validate_against_unchanged_unit_price_evidence(self):
        quotes = [ProductQuote.from_variant(self.small, quantity=2),
                  ProductQuote.from_variant(self.medium, quantity=3)]
        evidence = quote_evidence(quote_context("", quotes, append=True))

        self.assertEqual(evidence["text"].count("Dior Sauvage"), 1)
        self.assertIn("50 ملي × 2 بـ246.90 جنيه", evidence["text"])
        self.assertIn("90 ملي × 3 بـ540.00 جنيه", evidence["text"])
        self.assertEqual([(q["price"], q["quantity"]) for q in evidence["quotes"]], [("123.45", 2), ("180.00", 3)])
        self.assertEqual(product_price_issues(evidence["text"], [self.product], evidence=evidence), [])

    def test_validation_still_rejects_stale_evidence_and_unverified_extra_prices(self):
        quotes = [ProductQuote.from_variant(self.small), ProductQuote.from_variant(self.medium)]
        evidence = quote_evidence(quote_context("", quotes, append=True))
        stale = deepcopy(evidence)
        stale["quotes"][1]["price"] = "179.99"

        self.assertIn("price: quotation no longer matches its catalogue variant",
                      product_price_issues(stale["text"], [self.product], evidence=stale))
        extra_price_reply = "Dior Sauvage 50 ملي بـ1.00 جنيه.\n" + evidence["text"]
        self.assertTrue(product_price_issues(extra_price_reply, [self.product], evidence=evidence))
        self.assertEqual(append_quotation("", quote_context("", quotes, append=False)), "")

    def test_sanitizer_preserves_mixed_budget_groups_and_exact_evidence_in_both_orders(self):
        conversation = Conversation.objects.create(
            store=self.product.store, preferences={"max_price": "180.00"},
        )
        for first, second, quantity, expected_block in (
            (self.small, self.large, 1,
             "Dior Sauvage — زجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه، و100 ملي بـ190.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)."),
            (self.large, self.small, 1,
             "Dior Sauvage — زجاجة الستور الخاصة بينا 100 ملي بـ190.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)، و50 ملي بـ123.45 جنيه."),
            (self.small, self.large, 2,
             "Dior Sauvage — زجاجة الستور الخاصة بينا 50 ملي × 2 بـ246.90 جنيه، و100 ملي × 2 بـ380.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)."),
            (self.large, self.small, 2,
             "Dior Sauvage — زجاجة الستور الخاصة بينا 100 ملي × 2 بـ380.00 جنيه (أعلى حاجة بسيطة من ميزانيتك)، و50 ملي × 2 بـ246.90 جنيه."),
        ):
            with self.subTest(first=first.volume, quantity=quantity):
                quotes = [ProductQuote.from_variant(first, quantity=quantity),
                          ProductQuote.from_variant(second, quantity=quantity)]
                context = quote_context("catalogue facts", quotes, append=True,
                                        max_price=Decimal("180.00"))
                evidence = quote_evidence(context)
                self.assertEqual(evidence["text"], expected_block)
                reply = append_quotation("متاح يا فندم.", context)

                cleaned = sanitize_reply(reply, conversation)

                self.assertEqual(cleaned, "متاح يا فندم.\n\n" + expected_block)
                self.assertEqual(cleaned.count(evidence["text"]), 1)
                self.assertEqual(cleaned.count("(أعلى حاجة بسيطة من ميزانيتك)"), 1)
                self.assertEqual(append_quotation(cleaned, context), cleaned)
                self.assertEqual(product_price_issues(cleaned, [self.product], evidence=evidence), [])
