"""Conversation 1337: direct answers without qualifiers or repeated sales copy."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services import performance_policy as policy
from products.services import objection_service
from products.services.comparison_service import requested_facts
from products.services.conversation_service import build_llm_history, save_message
from products.services.objection_service import handle_objection
from products.services.product_formatting import format_product, product_claim_facts, quote_evidence
from products.services.product_info import _referent_from_conversation, get_product_info
from products.services.reply_sanitizer import product_claim_issues, sanitize_reply
from products.services.router import _rephrased
from products.services.sales import described
from products.services.sales.objection import detect


class ProjectionWordingTests(SimpleTestCase):
    def test_strong_projection_survives_validation(self):
        request = policy.request_for("والفوحان؟")
        reply = "فوحانه قوي."
        self.assertEqual(policy.enforce_reply(reply, request), reply)
        self.assertEqual(policy.claim_issues(reply, request), [])

    def test_old_projection_wording_is_updated_without_changing_longevity(self):
        for draft, expected in (
            ("فوحانه ممتاز.", "فوحانه قوي."),
            ("ثباته وفوحانه ممتازين.", "ثباته ممتاز وفوحانه قوي."),
            ("ثباته قوي وفوحانه ممتاز.", "ثباته ممتاز وفوحانه قوي."),
            ("ثباته قوي.", "ثباته ممتاز."),
        ):
            with self.subTest(draft=draft):
                self.assertEqual(policy.enforce_reply(draft), expected)
                self.assertEqual(policy.enforce_reply(expected), expected)

    def test_projection_comparisons_negation_and_measurements_are_still_corrected(self):
        request = policy.request_for("فوحانه كام متر؟")
        for draft in (
            "فوحانه مش قوي.", "فوحانه أقوى من غيره.", "فوحانه قوي لمدة 6 ساعات.",
            "فوحانه قوي لمسافة 3 متر.", "its projection is not strong.",
        ):
            with self.subTest(draft=draft):
                self.assertEqual(policy.enforce_reply(draft, request), "فوحانه قوي.")

    def test_numeric_longevity_and_strong_projection_are_idempotent(self):
        request = policy.request_for("ثباته كام ساعة وفوحانه إيه؟")
        reply = "ثباته أكتر من 8 ساعات، وفوحانه قوي."
        self.assertEqual(policy.enforce_reply(reply, request), reply)
        self.assertEqual(policy.claim_issues(reply, request), [])

    def test_correct_performance_claims_separated_by_a_scent_fact_are_not_duplicated(self):
        for reply in (
            "ثباته ممتاز وريحته فانيليا وفوحانه قوي.",
            "فوحانه قوي وريحته فانيليا وثباته ممتاز.",
            "ثباتها ممتاز وريحته فانيليا وفوحانها قوي.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(policy.enforce_reply(reply), reply)
                self.assertEqual(policy.claim_issues(reply), [])


class ConfidentWordingTests(SimpleTestCase):
    def test_source_qualifiers_are_removed_without_changing_the_answer(self):
        for draft, expected in (
            ("لا يا فندم، ده رجالي حسب المتوفر عندنا.", "لا يا فندم، ده رجالي."),
            ("ده رجالي حسب تصنيفه عندنا.", "ده رجالي."),
            ("دي حريمي حسب تصنيفها عندنا.", "دي حريمي."),
            ("حسب الداتا الي معايا، ده للجنسين.", "ده للجنسين."),
            ("حسب البيانات اللي عندي، ده مش يونيسكس.", "ده مش يونيسكس."),
            ("حسب معلوماتي ده رجالي.", "ده رجالي."),
            ("ده مصنّف عندنا نيش.", "ده نيش."),
        ):
            with self.subTest(draft=draft):
                self.assertEqual(sanitize_reply(draft), expected)
                self.assertEqual(sanitize_reply(expected), expected)

    def test_uncertainty_and_normal_customer_qualifications_survive(self):
        for reply in (
            "السعر بيختلف حسب الحجم.", "نختار حسب ذوقك.",
            "مش متأكد من نوعه، ممكن تكتب اسمه كامل؟", "ده مش متوفر عندنا.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(sanitize_reply(reply), reply)

    def test_negated_classification_preserves_its_predicate_and_uncertainty(self):
        for reply in (
            "ده مش مصنف عندنا، محتاج أتأكد من نوعه.",
            "ده مش مصنف عندنا.",
            "دي مش متصنفة عندنا، محتاج أتأكد من نوعها.",
            "ده غير مصنّف عندنا، محتاج أتأكد من نوعه.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(sanitize_reply(reply), reply)

    def test_source_aside_after_a_negative_answer_is_still_removed(self):
        self.assertEqual(sanitize_reply("ده مش يونيسكس حسب معلوماتي."), "ده مش يونيسكس.")

    # Owner, 2026-11-20: no reply cites where its information comes from. The six phrases above matched
    # none of 6,687 stored and simulated replies; these are the forms the bot actually wrote.
    def test_real_source_talk_is_removed_or_reworded(self):
        for draft, expected in (
            ("الفرق في الطابع المسجل: Alexandria II عودي وخشبي.", "الفرق في الطابع: Alexandria II عودي وخشبي."),
            ("212 Sexy Women: طابعه المسجل فيه ريحة بودرة.", "212 Sexy Women: طابعه فيه ريحة بودرة."),
            ("مفيش عود ضمن نوتاته المسجلة.", "مفيش عود ضمن نوتاته."),
            ("الـBase Notes المسجلة عندنا هي Vanilla وPepper.", "الـBase Notes هي Vanilla وPepper."),
            ("طابعها زهور وبحري ونضيف، والحلاوة فيها مش مؤكدة عندي.", "طابعها زهور وبحري ونضيف، والحلاوة فيها مش مؤكدة."),
            ("Her فيها ريحة فواكه؛ خفتها مش مؤكدة في بياناته.", "Her فيها ريحة فواكه؛ خفتها مش مؤكدة."),
            ("سعر Afnan 9PM هو 2000 جنيه زي ما هو مكتوب عندنا.", "سعر Afnan 9PM هو 2000 جنيه."),
            ("ومقدرش أحدد أنهي أوضح فيهم من البيانات اللي عندي.", "ومقدرش أحدد أنهي أوضح فيهم."),
            ("مناسب للشغل حسب بياناته.", "مناسب للشغل."),
            ("Bleu de Chanel هو الأنسب للشغل، ومناسب للمكتب حسب استخدامه.",
             "Bleu de Chanel هو الأنسب للشغل، ومناسب للمكتب."),
            ("للخروج، Aventus أنسب حسب المناسبة المسجلة له، وريحته خشبية.", "للخروج، Aventus أنسب، وريحته خشبية."),
            ("لكن حسب النوتات المتاحة عندي، الـBase Notes فيها فانيليا.", "لكن الـBase Notes فيها فانيليا."),
            ("ومش تقيل حسب وصف الريحة المسجل.", "ومش تقيل في طابعه."),
            ("لا يا فندم، مفيش عود حسب وصف الريحة المتاح.", "لا يا فندم، مفيش عود في طابعه."),
            ("بس طابعها زهري ونضيف، ومش مسكر حسب وصفها.", "بس طابعها زهري ونضيف، ومش مسكر في طابعها."),
            ("ريحته مسكر وخشبي، ووصفه عندنا جذاب.", "ريحته مسكر وخشبي، وريحته جذاب."),
            ("212 Sexy man ريحته مسكرة وخشبية، ووصفه جذاب.", "212 Sexy man ريحته مسكرة وخشبية، وريحته جذاب."),
            ("ده موصوف عندنا إنه جذاب.", "ده جذاب."),
            ("ده كل اللي متسجل عندي عن ده؛ أي تفاصيل أكتر محتاجة تأكيد.", "ده كل اللي أعرفه عن ده؛ أي تفاصيل أكتر محتاجة تأكيد."),
            ("ده كل المكتوب عندي عن العرض.", "ده كل اللي أعرفه عن العرض."),
            ("أقدر أأكدلك المسجل عندنا بس.", "أقدر أأكدلك اللي أعرفه بس."),
            ("الاتنين مسجلين للمناسبات الرسمية.", "الاتنين مناسبين للمناسبات الرسمية."),
            ("K By Dolce أنسب لأنه مسجل للشاطئ والصيف.", "K By Dolce أنسب لأنه مناسب للشاطئ والصيف."),
            # Found in the backtest over stored replies:
            ("الاتنين مناسبتهم مسجلة للمناسبات الرسمية والخاصة.", "الاتنين مناسبتهم للمناسبات الرسمية والخاصة."),
            ("الـPepper موجود في الـBase Notes حسب التقسيم المتاح عندي.", "الـPepper موجود في الـBase Notes."),
            ("حقك عليّا، وصفهم المسجل ما بيأكدش إذا فيهم حلاوة.", "حقك عليّا، وصفهم ما بيأكدش إذا فيهم حلاوة."),
            ("مش هقدر أأكد إن BMW مش مسكر؛ وصفه المسجل حمضيات وفريش وخشبي.",
             "مش هقدر أأكد إن BMW مش مسكر؛ ريحته حمضيات وفريش وخشبي."),
            ("أما Layton ففاكهي وخشبي ومسكر، من غير طابع دخاني مسجل.", "أما Layton ففاكهي وخشبي ومسكر، من غير طابع دخاني."),
            ("Cigar ريحته خشبي، بس مفيش مقارنة مسجلة تثبت إنه شبه Aventus.",
             "Cigar ريحته خشبي، بس مفيش مقارنة تثبت إنه شبه Aventus."),
            ("بس مناسبة الاستخدام اليومي مش مسجلة عندي؛ بتحبي العطر أخف؟", "بس مناسبة الاستخدام اليومي مش مؤكدة؛ بتحبي العطر أخف؟"),
            ("مفيش عندي بيانات عن أكتر العطور مبيعًا، فالمعلومة محتاجة تأكيد.", "معرفش أكتر العطور مبيعًا، فالمعلومة محتاجة تأكيد."),
            ("Cigar ريحته خشبي. مفيش بيانات بتأكد إنهم شبه Club de Nuit.", "Cigar ريحته خشبي. مفيش حاجة بتأكد إنهم شبه Club de Nuit."),
            ("مش قادر أجزم إنه شبه Club de Nuit من غير بيانات عن ريحته.", "مش قادر أجزم إنه شبه Club de Nuit."),
            ("مش قادر أجزم أنهي أقرب من غير بيانات مقارنة بينهم؛ Ultra Male مسكر.", "مش قادر أجزم أنهي أقرب؛ Ultra Male مسكر."),
            ("وده مطابق للنوتات المسجلة عندنا.", "وده مطابق للنوتات."),
        ):
            with self.subTest(draft=draft):
                self.assertEqual(sanitize_reply(draft), expected)
                self.assertEqual(sanitize_reply(expected), expected)

    def test_a_negated_registry_phrase_is_reworded_never_deleted(self):
        # Deleting "مش مسجل" from "ومش مسجل إنه تقيل" would say it IS heavy.
        for draft, expected in (
            ("ومش مسجل إنه تقيل.", "ومش مؤكد إنه تقيل."),
            ("Black Code خيار جيد، لكن مش مسجل فيه الريحة النظيفة اللي طلبتها.",
             "Black Code خيار جيد، لكن مش مؤكد إن فيه الريحة النظيفة اللي طلبتها."),
            # A trait missing from the labels is absent (owner, 2026-11-20).
            ("والطابع الخشبي مش من وصفه؛ هو فريش وحمضي.", "والطابع الخشبي مش فيه؛ هو فريش وحمضي."),
            ("Cassilia ريحته فيها زهور وفواكه، ومش من وصفه إنه مسكر.", "Cassilia ريحته فيها زهور وفواكه، ومش مؤكد إنه مسكر."),
        ):
            with self.subTest(draft=draft):
                self.assertEqual(sanitize_reply(draft), expected)
        # "مش مسجل للشغل" (unrecorded) is not "مش مناسب للشغل": left as written, and logged.
        self.assertEqual(sanitize_reply("ده مش مسجل للشغل."), "ده مش مسجل للشغل.")

    def test_customer_order_and_stock_wording_is_not_source_talk(self):
        from products.services.reply_sanitizer import source_talk, strip_source_qualifiers
        for reply in (
            "التوصيل بيوصل خلال 2 لـ4 أيام عمل حسب المحافظة يا فندم.",
            "للشغل، Eternity أهدى اختيار حسب طابعه الفريش والخشبي.",
            "وكمان ممكن زجاجة أوريجينال حسب المتوفر.",
            "أرشحلك على حسب الريحة اللي بتحبها.", "حسب وصفك، أقرب حاجة Aventus.",
            "كل البيانات كده تمام ونأكد الطلب، ولا تحب تعدل حاجة؟",
            "اكتبلي بيانات التوصيل: الاسم والعنوان والتليفون.",
            "أيوه، مسجل لك 1 × Dior Sauvage، زجاجة أوريجينال 100 ملي بـ815 جنيه.",
            "الاختيارات والكمية متسجلة زي ما طلبت.", "طلبك مسجل عندنا وهيتواصل معاك المندوب.",
            "الوصف ده قريب من Lattafa Asad، بس مش متأكد.", "العطر اللي بتوصفه ممكن يكون Aventus.",
            "عندنا تصنيفات مختلفة: عطور شرقية، عطور غربية، عطور نيش.",
            "مش متوفر عندنا عطر أصلي من تصنيع البراند.", "المعلومة دي محتاجة تأكيد من فريق خدمة العملاء.",
            "يعني السعر اللي مكتوب هو للـ4 عطور مع بعض.", "والحلاوة مش ضمن طابعه.",
            "لو تحب أعدل أي بيانات في الطلب قولي.", "ماعنديش معلومة مؤكدة إن Cigar قريب من Club de Nuit.",
            "من غير ما تكون عندك تجربة، ابدأ بالـ50 ملي.",
        ):
            with self.subTest(reply=reply):
                # Checked on the source pass alone: `sanitize_reply` also standardises the bottle spelling
                # ("أوريجينال" → "اوريجنال").
                self.assertEqual(strip_source_qualifiers(reply), reply)
                self.assertEqual(source_talk(reply), [])
        # A denial is never read as a noun with its adjective (backtest: "وصف مش مسجل" lost its "مسجل").
        denial = "من غير ما أضيف وصف مش مسجل، طابعه منعش وخشبي."
        self.assertEqual(strip_source_qualifiers(denial), denial)

    def test_what_no_rule_can_repair_is_logged(self):
        with self.assertLogs("products.services.reply_sanitizer", level="WARNING") as logs:
            self.assertEqual(sanitize_reply("ده مش مصنف عندنا، محتاج أتأكد من نوعه."),
                             "ده مش مصنف عندنا، محتاج أتأكد من نوعه.")
        self.assertIn("SOURCE_TALK", logs.output[0])

    def test_canned_replies_cite_no_records_and_pass_through_unchanged(self):
        from types import SimpleNamespace

        from products.services import promotion_service, router, scent_description
        from products.services.reply_sanitizer import source_talk
        from products.services.sales import attraction

        product = SimpleNamespace(name="For Him", top_notes="Lemon", middle_notes="", base_notes="Cedar",
                                  scent_tags=["fresh", "woody"])
        other = SimpleNamespace(name="K By Dolce", top_notes="", middle_notes="", base_notes="",
                                scent_tags=["fresh", "citrus"])
        canned = [
            router.NOTES_SOURCE, router.NOTES_SOURCE_AGAIN, router.SKIN_OR_CLOTHES_ANSWER,
            router.SKIN_OR_CLOTHES_AGAIN, promotion_service.NO_MORE_DETAILS, promotion_service.NO_MORE_DETAILS_AGAIN,
            attraction.ACKNOWLEDGEMENT, attraction.appealing_line(["Velvet Night"]), scent_description._FALLBACK,
            router._not_sweet_line([product]), router._not_sweet_line([product, other]),
            router._label_difference(product, other), router._label_difference(product, product),
            router._not_a_repeat("نفس الرد اللي اتقال قبل كده بالظبط.", "طب وبعدين؟",
                                 [{"role": "assistant", "content": "نفس الرد اللي اتقال قبل كده بالظبط."}]),
            *scent_description._DENIALS.values(),
        ]
        for text in canned:
            with self.subTest(text=text):
                self.assertEqual(source_talk(text), [])
                self.assertEqual(sanitize_reply(text), text.strip())
        # The "did we already say it?" checks compare these constants with saved history.
        self.assertEqual(router._not_sweet_line([product]), "For Him مش مسكر، ومفيش فانيليا ولا عسل في نوتاته.")
        self.assertEqual(router._not_sweet_line([product, other]), "For Him وK By Dolce مش مسكرين.")


class MissingTraitTests(SimpleTestCase):
    """Owner, 2026-11-20 (reversing 2026-11-12): a trait missing from the labels is absent, for every trait."""

    def product(self, tags, notes=("", "", "")):
        from types import SimpleNamespace
        return SimpleNamespace(name="X", scent_tags=tags, top_notes=notes[0], middle_notes=notes[1], base_notes=notes[2])

    def test_every_denial_reads_back_as_a_denial_of_its_own_label(self):
        from products.scent_tags import SCENT_TAG_LABELS
        from products.services import scent_description as sd
        self.assertEqual(set(sd._DENIALS), set(SCENT_TAG_LABELS))
        for tag, phrase in sd._DENIALS.items():
            with self.subTest(tag=tag):
                self.assertEqual([(found, negative) for found, _, _, negative in sd._tag_mentions(sd._NORMALIZE(phrase))],
                                 [(tag, True)])

    def test_a_missing_trait_is_answered_no_then_the_character_it_has(self):
        from products.services.scent_description import asked_attribute_answers
        fresh = self.product(["fresh", "citrus", "woody"], ("Bergamot", "Lavender", "Cedar"))
        self.assertEqual(asked_attribute_answers("هو تقيل؟", fresh),
                         ["مش تقيل", "ريحته فريش ومنعش وفيه ريحة حمضيات وخشبي"])
        self.assertEqual(asked_attribute_answers("هو مسكر؟", fresh)[0], "مش مسكر")
        self.assertEqual(asked_attribute_answers("ريحته نضيفة؟", fresh)[0], "ريحته مش من النوع النضيف")
        self.assertEqual(asked_attribute_answers("هو فريش؟", fresh), ["طابعه فريش ومنعش"])

    def test_a_perfume_with_no_labels_still_needs_confirmation(self):
        from products.services.scent_description import asked_attribute_answers
        self.assertEqual(asked_attribute_answers("هو مسكر؟", self.product([])), ["ريحته محتاجة تأكيد"])

    def test_which_trait_leads_is_only_answered_when_it_has_both(self):
        from products.services.scent_description import asked_attribute_answers
        both = self.product(["sweet", "spicy", "woody"])
        self.assertIn("ومقدرش أحدد أنهي فيهم أوضح",
                      asked_attribute_answers("ريحته سويت قوي ولا التوابل والخشب أوضح؟", both))
        neither = self.product(["fresh", "citrus"], ("Lemon", "Ginger", "Cedar"))
        self.assertNotIn("ومقدرش أحدد أنهي فيهم أوضح", asked_attribute_answers("فيه عود أو ريحته مسكرة؟", neither))


class Conversation1337ReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Perfamix")
        StoreSettings.objects.create(
            store=cls.store,
            business_facts="كل عطورنا تركيب. بنستخدم زيوت غالية عشان نقدم ريحة شبه الأوريجنال بالظبط.",
        )
        brand = Brand.objects.create(store=cls.store, name="Xerjoff")
        cls.product = Product.objects.create(
            store=cls.store, brand=brand, name="Alexandria II", gender="male", perfume_type="niche",
            top_notes="Vanilla",
        )
        for volume, price, bottle, stock in ((50, 1400, "normal", None), (90, 2000, "normal", None),
                                              (100, 2400, "original", 2)):
            ProductVariant.objects.create(product=cls.product, volume=volume, price=price,
                                          bottle_type=bottle, stock=stock)
        cls.other = Product.objects.create(store=cls.store, brand=brand, name="Cedar Night", gender="female")
        ProductVariant.objects.create(product=cls.other, volume=50, price=600)

    def setUp(self):
        self.conversation = Conversation.objects.create(store=self.store)
        save_message(self.conversation, "assistant", "Alexandria II متوفر يا فندم.",
                     internal_context=format_product(self.product))

    def product_turn(self, message):
        history = build_llm_history(self.conversation)
        save_message(self.conversation, "user", message)
        def resolve(message, history, store, conversation):
            return _referent_from_conversation(message, store, conversation)

        with patch("products.services.product_info.chat", return_value=""), \
             patch("products.services.product_info.resolve_products", side_effect=resolve):
            reply, context = get_product_info(message, history, self.store, self.conversation)
        reply = sanitize_reply(reply, self.conversation)
        save_message(self.conversation, "assistant", reply, internal_context=context)
        return reply, context

    def test_projection_followup_omits_the_already_known_name(self):
        reply, context = self.product_turn("والفوحان؟")
        self.assertEqual(reply, "فوحانه قوي.")
        self.assertIn("Name (الاسم الصحيح): Alexandria II", context)

    def test_consecutive_nameless_replies_keep_the_subject_and_its_prices(self):
        for message, expected in (
            ("والفوحان؟", "فوحانه قوي."),
            ("ثباته إيه؟", "ثباته ممتاز."),
            ("كام ساعة؟", "ثباته أكتر من 8 ساعات."),
            ("فوحانه كام متر؟", "فوحانه قوي."),
        ):
            with self.subTest(message=message):
                reply, _ = self.product_turn(message)
                self.assertEqual(reply, expected)
        self.assertEqual(described.offered_in_order(self.conversation, self.store), ["Alexandria II"])
        reply, _ = self.product_turn("بكام؟")
        for fact in ("Alexandria II", "1400 جنيه", "2000 جنيه", "2400 جنيه"):
            self.assertIn(fact, reply)
        self.assertEqual(reply.count("Alexandria II"), 1)

    def test_a_new_product_is_named_when_the_subject_changes(self):
        reply, _ = self.product_turn("Cedar Night فوحانه إيه؟")
        self.assertEqual(reply, "Cedar Night: فوحانه قوي.")
        self.assertNotIn("Alexandria II", reply)

    def test_comparison_keeps_each_products_name(self):
        reply, _ = requested_facts("قارن الفوحان", [self.product, self.other])
        self.assertEqual(reply, "Alexandria II: فوحانه قوي.\n\nCedar Night: فوحانه قوي.")

    def test_nameless_answer_still_checks_the_known_products_notes(self):
        context = format_product(self.product)
        reply, _, _ = _rephrased("فيه عود.", context, [], self.store,
                                 lambda hint: ("فيه فانيليا.", context))
        self.assertEqual(reply, "فيه فانيليا.")
        self.assertEqual(product_claim_issues(reply, product_claim_facts([self.product])), [])

    def test_unknown_external_subject_is_not_attributed_to_the_only_alternative(self):
        context = "ABSENCE_DENIED\n" + format_product(self.product)
        self.assertEqual(product_claim_issues("العود من النوتات اللي حضرتك بتدور عليها.",
                         product_claim_facts([self.product]), context=context), [])

    def objection_turn(self, message, reply, history=None):
        with patch("products.services.objection_service.resolve_products", return_value=[self.product]), \
             patch("products.services.objection_service.chat", return_value=reply):
            return handle_objection(message, detect(message), history or [], self.store, self.conversation)

    def test_price_objection_does_not_append_an_unrequested_quotation(self):
        draft = "فعلاً عندك حق، بنستخدم زيوت غالية عشان ريحة شبه الأوريجنال بالظبط. ممكن تجرب الحجم الصغير."
        reply, context = self.objection_turn("غالي ليه كدا؟", draft)
        self.assertEqual(reply, draft)
        self.assertFalse(quote_evidence(context).get("append"))

    def test_previous_price_question_does_not_authorize_another_price_list(self):
        history = [{"role": "user", "content": "بكام؟"},
                   {"role": "assistant", "content": "Alexandria II حجم 90 ملي بـ2000 جنيه."}]
        draft = "فعلاً عندك حق، بنستخدم زيوت غالية عشان نقدم نفس الريحة."
        reply, context = self.objection_turn("ليه السعر عالي؟", draft, history)
        self.assertEqual(reply, draft)
        self.assertFalse(quote_evidence(context).get("append"))

    def test_asking_to_explain_the_price_is_not_a_request_for_prices(self):
        draft = "فعلاً عندك حق، بنستخدم زيوت غالية وعالية الجودة."
        reply, context = self.objection_turn("عايز أعرف ليه السعر عالي؟", draft)
        self.assertEqual(reply, draft)
        self.assertFalse(quote_evidence(context).get("append"))

    def test_explicit_smaller_size_price_only_quotes_the_requested_size(self):
        reply, context = self.objection_turn("غالي، الـ50 ملي بكام؟", "ممكن تبدأ بالحجم ده.")
        self.assertIn("50 ملي بـ1400 جنيه", reply)
        self.assertNotIn("2000", reply)
        self.assertNotIn("2400", reply)
        self.assertEqual([row["volume"] for row in quote_evidence(context)["quotes"]], [50])

    def test_causal_question_can_also_request_a_specific_price(self):
        for message in (
            "ليه غالي؟ وإيه سعر الـ50 ملي؟",
            "ليه غالي؟ وقولي سعر الـ50 ملي",
            "ليه غالي وسعر الـ50 ملي إيه؟",
            "عايز أعرف ليه غالي وقولي سعر الـ50 ملي",
            "سعر الـ50 ملي إيه وليه غالي؟",
        ):
            with self.subTest(message=message):
                reply, context = self.objection_turn(message, "فعلاً عندك حق، بنستخدم زيوت غالية.")
                self.assertIn("50 ملي بـ1400 جنيه", reply)
                self.assertEqual([row["volume"] for row in quote_evidence(context)["quotes"]], [50])

    def test_causal_only_requests_do_not_quote_prices(self):
        for message in (
            "قولي ليه السعر عالي؟", "عايز أعرف سبب السعر العالي",
            "ليه سعره عالي وريحته إيه؟",
        ):
            with self.subTest(message=message):
                self.assertFalse(objection_service._requests_objection_prices(message))

    def test_smaller_option_must_cost_less_and_be_available(self):
        ProductVariant.objects.create(product=self.product, volume=30, price=3000, bottle_type="original", stock=1)
        ProductVariant.objects.create(product=self.product, volume=20, price=400, bottle_type="original", stock=0)
        choices = objection_service.smaller_size_quotes([self.product], "الـ90 ملي غالي")
        self.assertEqual([(choice.volume, choice.price) for choice in choices], [(50, 1400)])

    def test_no_smaller_offer_for_the_only_size_or_an_already_small_selection(self):
        self.assertEqual(objection_service.smaller_size_quotes([self.other], "غالي"), [])
        self.assertEqual(objection_service.smaller_size_quotes([self.product], "الـ50 ملي غالي"), [])
        history = [{"role": "user", "content": "بكام 50 ملي؟"},
                   {"role": "assistant", "content": "الـ50 ملي بـ1400 جنيه."}]
        self.assertEqual(objection_service.smaller_size_quotes([self.product], "غالي", history), [])

    def test_selected_small_size_survives_intervening_nameless_followups(self):
        history = [
            {"role": "assistant", "content": "Alexandria II متوفر."},
            {"role": "user", "content": "عايز 50 ملي"},
            {"role": "assistant", "content": "الـ50 ملي بـ1400 جنيه."},
            {"role": "user", "content": "ثباته إيه؟"},
            {"role": "assistant", "content": "ثباته ممتاز."},
        ]
        self.assertEqual(objection_service.smaller_size_quotes([self.product], "غالي", history), [])

    def test_new_product_does_not_inherit_the_previous_products_small_size(self):
        history = [
            {"role": "assistant", "content": "Cedar Night متوفر."},
            {"role": "user", "content": "عايز 50 ملي"},
            {"role": "assistant", "content": "الـ50 ملي بـ600 جنيه."},
        ]
        choices = objection_service.smaller_size_quotes([self.product], "Alexandria II غالي", history)
        self.assertEqual([(choice.volume, choice.price) for choice in choices], [(50, 1400)])
        history += [
            {"role": "user", "content": "Alexandria II ثباته إيه؟"},
            {"role": "assistant", "content": "ثباته ممتاز."},
            {"role": "user", "content": "والفوحان؟"},
            {"role": "assistant", "content": "فوحانه قوي."},
        ]
        choices = objection_service.smaller_size_quotes([self.product], "غالي", history)
        self.assertEqual([(choice.volume, choice.price) for choice in choices], [(50, 1400)])
