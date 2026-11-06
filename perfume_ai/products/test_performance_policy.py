import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Cart, Conversation, Product, ProductVariant, StaticFAQ, Store, StoreSettings
from products.services import performance_policy as policy
from products.services.ai.recommendation import recommend
from products.services.comparison_service import requested_facts
from products.services.product_formatting import factual_description, format_product, product_claim_facts
from products.services.reply_sanitizer import product_claim_issues
from products.services.router import _rephrased, route
from products.services.sales import ranking, similarity
from products.services.search_service import search_products


class PerformanceRequestTests(SimpleTestCase):
    def test_explicit_duration_questions(self):
        for message in (
            "بيثبت كام ساعة؟", "بتثبت كام ساعة؟", "ثباته قد إيه؟", "ثباته بالارقام",
            "عايزه يثبت 12 ساعة", "بيثبت ٨ ساعات؟", "هل ثباته يوم كامل؟",
            "how long does it last?", "longevity in hours?", "does it last 12 hours?",
        ):
            with self.subTest(message=message):
                self.assertTrue(policy.request_for(message).hours)

    def test_general_questions_and_performance_preferences_do_not_enable_hours(self):
        for message in (
            "ثباته إيه؟", "ثباته وفوحانه عاملين إيه؟", "عايز حاجة ثابتة وفواحة",
            "الثبات ممتاز؟", "longevity and projection?", "I want a long-lasting perfume",
            "جربته وثباته كان ساعتين بس", "جبت من عندكم عطر ثابت 8 ساعات وبعد ساعتين مش بحسه",
        ):
            with self.subTest(message=message):
                self.assertFalse(policy.request_for(message).hours)

    def test_projection_questions_never_enable_longevity_hours(self):
        for message in ("فوحانه كام ساعة؟", "الفوحان ٨ ساعات؟", "فوحانه كام متر؟", "projection duration?"):
            with self.subTest(message=message):
                request = policy.request_for(message)
                self.assertTrue(request.projection)
                self.assertFalse(request.hours)

    def test_followups_use_the_current_subject(self):
        for previous, expected in (
            ("ثباته ممتاز.", True), ("ثباته وفوحانه ممتازين.", True),
            ("فوحانه ممتاز.", False), ("التوصيل خلال يوم.", False),
        ):
            with self.subTest(previous=previous):
                history = [{"role": "assistant", "content": previous}]
                self.assertEqual(policy.request_for("طيب كام ساعة؟", history).hours, expected)
        self.assertTrue(policy.request_for("قولي رقم", [{"role": "assistant", "content": "ثباته ممتاز."}]).hours)

    def test_old_hour_request_does_not_enable_numbers_on_later_turns(self):
        history = [{"role": "user", "content": "كام ساعة؟"},
                   {"role": "assistant", "content": "ثباته أكتر من 8 ساعات."}]
        self.assertFalse(policy.request_for("طب ريحته إيه؟", history).hours)
        self.assertFalse(policy.request_for("بكام؟", history).hours)

    def test_prices_and_names_do_not_create_a_higher_duration_request(self):
        request = policy.request_for("بيثبت 8 ساعات؟ وبكام 212 VIP حجم 50 مل؟")
        self.assertTrue(request.hours)
        self.assertFalse(request.higher_duration)
        self.assertTrue(policy.request_for("عايز ثبات ٢٤ ساعة").higher_duration)

    def test_number_requests_belong_to_the_correct_axis_in_mixed_questions(self):
        for message, expected in (
            ("ثباته كويس؟ وسعره كام؟", False),
            ("ثباته إيه والتوصيل كام ساعة؟", False),
            ("ثباته كويس؟ وحجمه كام؟", False),
            ("longevity good, how many ml?", False),
            ("ثباته كويس، رقم التليفون كام؟", False),
            ("سعره 500، كام ساعة ثباته؟", True),
            ("فوحانه كام ساعة وثباته عامل إيه؟", False),
            ("ثباته وفوحانه كام ساعة؟", True),
            ("كام ساعة ثباته وفوحانه إيه؟", True),
            ("ثباته 8 ساعات؟ والفوحان 24 ساعة؟", True),
        ):
            with self.subTest(message=message):
                self.assertEqual(policy.request_for(message).hours, expected)
        self.assertFalse(policy.request_for("ثباته 8 ساعات؟ والفوحان 24 ساعة؟").higher_duration)
        self.assertFalse(policy.request_for(
            "give me a price number", [{"role": "assistant", "content": "excellent longevity"}],
        ).hours)

    def test_post_context_is_not_the_customer_question(self):
        text = "[سياق البوست]: ثبات 24 ساعة\n[تعليق العميل]: بكام؟"
        self.assertFalse(policy.request_for(text).hours)

    def test_only_performance_questions_can_bypass_response_generation(self):
        for message in ("ثبات Cedar Day وفوحانه إيه؟", "Cedar Day بيثبت كام ساعة؟",
                        "فوحانه كام متر؟", "how long does Cedar Day last?",
                        "مين أثبت Cedar Day ولا Amber Night؟"):
            with self.subTest(message=message):
                self.assertTrue(policy.performance_only_question(message))
        for message in ("ثباته كويس؟ وسعره كام؟", "ثباته إيه وريحته عاملة إيه؟",
                        "ثباته ممتاز وموجود عندكم؟", "عايز حاجة ثابتة وفواحة",
                        "رشحلي عطر ثابت", "ثباته كام ساعة وخدلي 50 مل",
                        "جربته وثباته كان ساعتين ومش راضي عنه", "ثباته ضعيف",
                        "how long does it last and what are its notes?"):
            with self.subTest(message=message):
                self.assertFalse(policy.performance_only_question(message))
        for previous, followup in (("Cedar Day ثباته ممتاز.", "قولي رقم"),
                                   ("Cedar Day فوحانه ممتاز.", "كام ساعة بقى؟")):
            with self.subTest(followup=followup):
                self.assertTrue(policy.performance_only_question(
                    followup, [{"role": "assistant", "content": previous}],
                ))

    def test_context_is_reset_and_isolated_between_concurrent_chats(self):
        barrier = Barrier(2)

        def read(message):
            with policy.performance_turn(message):
                barrier.wait(timeout=5)
                return policy.value("longevity")

        with ThreadPoolExecutor(max_workers=2) as pool:
            numeric = pool.submit(read, "بيثبت كام ساعة؟")
            general = pool.submit(read, "ثباته كويس؟")
            self.assertEqual(numeric.result(), policy.LONGEVITY_HOURS)
            self.assertEqual(general.result(), policy.EXCELLENT)
        self.assertFalse(policy.current_request().hours)
        with self.assertRaises(ValueError), policy.performance_turn("كام ساعة؟"):
            raise ValueError("failed generation")
        self.assertFalse(policy.current_request().hours)


class PerformanceReplyTests(SimpleTestCase):
    def test_unsupported_duration_values_become_the_exact_allowed_answer(self):
        request = policy.request_for("بيثبت كام ساعة؟")
        for value in ("6 ساعات", "8 ساعات", "8-10 ساعات", "١٢ ساعة", "48 ساعة", "تمان ساعات", "يومين", "طول اليوم"):
            with self.subTest(value=value):
                cleaned = policy.enforce_reply("ثباته " + value + ".", request)
                self.assertEqual(cleaned, "ثباته أكتر من 8 ساعات.")
                self.assertEqual(policy.claim_issues(cleaned, request), [])

    def test_default_answers_never_volunteer_hours(self):
        for draft in ("ثباته 8 ساعات.", "ثباته حوالي 8-10 ساعات وفوحانه قوي.",
                      "ثباته ممتاز ويدوم 48 ساعة."):
            with self.subTest(draft=draft):
                cleaned = policy.enforce_reply(draft)
                self.assertNotRegex(cleaned, r"\d|ساعات|ساعة")
                self.assertIn("ممتاز", cleaned)

    def test_projection_duration_and_distance_stay_qualitative(self):
        for draft in ("فوحانه 8 ساعات.", "فوحانه بيوصل لـ3 متر.", "فوحانه أول ساعتين قوي.",
                      "فوحانه ممتاز وبيوصل 3 متر.", "فوحانه ممتاز لمسافة تلاتة أمتار.", "فوحانه ممتاز 10/10."):
            with self.subTest(draft=draft):
                request = policy.request_for("فوحانه كام ساعة وكام متر؟")
                self.assertEqual(policy.enforce_reply(draft, request), "فوحانه قوي.")

    def test_negated_excellence_and_english_descriptors_do_not_escape_validation(self):
        for draft, expected in (
            ("ثباته مش ممتاز.", "ثباته ممتاز."),
            ("فوحانه غير ممتاز.", "فوحانه قوي."),
            ("Cedar Day has strong projection.", "Cedar Day فوحانه قوي."),
            ("Cedar Day has weak longevity.", "Cedar Day ثباته ممتاز."),
            ("Cedar Day أقوى في الفوحان.", "Cedar Day فوحانه قوي."),
        ):
            with self.subTest(draft=draft):
                self.assertTrue(policy.claim_issues(draft))
                self.assertEqual(policy.enforce_reply(draft), expected)

    def test_bare_hour_replies_and_conjugated_verbs_read_naturally(self):
        request = policy.request_for("طيب كام ساعة؟")
        for draft in ("أكتر من 12 ساعة.", "حوالي 8 ساعات.", "بتثبت 12 ساعة.", "بيثبت 6 ساعات."):
            with self.subTest(draft=draft):
                self.assertEqual(policy.enforce_reply(draft, request), "ثباته أكتر من 8 ساعات.")

    def test_legitimate_numbers_and_scent_facts_survive_correction(self):
        draft = ("212 VIP سعره 500.50 جنيه لحجم 50 مل، ثباته 6 ساعات وسعر حجم 90 مل 800 جنيه. "
                 "طابعه خفيف. الشحن 24 ساعة. خدمة العملاء 24 ساعة على 01012345678.")
        cleaned = policy.enforce_reply(draft)
        for fact in ("212 VIP", "500.50 جنيه", "50 مل", "90 مل 800 جنيه", "طابعه خفيف",
                     "الشحن 24 ساعة", "خدمة العملاء 24 ساعة", "01012345678"):
            self.assertIn(fact, cleaned)
        self.assertNotIn("6 ساعات", cleaned)
        self.assertEqual(policy.enforce_reply(cleaned), cleaned)

    def test_unrelated_messages_are_unchanged(self):
        for reply in ("طابعه خفيف ومسكر.", "عندنا زجاجة 50 مل بـ500 جنيه.",
                      "الستور بيفتح الساعة 8.", "فترة الشحن من 8 إلى 24 ساعة.", ""):
            with self.subTest(reply=reply):
                self.assertEqual(policy.enforce_reply(reply), reply)

    def test_unknown_performance_is_replaced_by_store_policy(self):
        for draft in ("مش متأكد من ثباته.", "Cedar Day: الثبات غير مسجل، الفوحان غير معروف."):
            with self.subTest(draft=draft):
                cleaned = policy.enforce_reply(draft)
                self.assertNotIn("غير مسجل", cleaned)
                self.assertNotIn("مش متأكد", cleaned)
                self.assertIn("ممتاز", cleaned)

    def test_plural_wording_is_preserved_in_store_answers(self):
        draft = "كل عطورنا ثباتها 24 ساعة وفوحانها أول 6 ساعات قوي."
        self.assertEqual(policy.enforce_reply(draft), "كل عطورنا ثباتها ممتاز وفوحانها قوي.")
        self.assertEqual(policy.enforce_reply(draft, policy.request_for("الثبات كام ساعة؟")),
                         "كل عطورنا ثباتها أكتر من 8 ساعات، وفوحانها قوي.")

    def test_customer_experience_is_not_rewritten_as_a_store_claim(self):
        report = "متفهم إن تجربتك مع Cedar Day كانت ساعتين."
        draft = report + " ثبات عطورنا 12 ساعة."
        cleaned = policy.enforce_reply(draft)
        self.assertTrue(cleaned.startswith(report))
        self.assertNotIn("12 ساعة", cleaned)
        self.assertFalse(policy.claim_issues(report))

    def test_external_perfume_does_not_receive_store_claims(self):
        context = "ABSENCE_DENIED\nName (الاسم الصحيح): Cedar Day\n"
        for draft in (
            "External: ثباته 12 ساعة. Cedar Day: ثباته 6 ساعات.",
            "Cedar Day: ثباته 6 ساعات. External: ثباته 12 ساعة.",
            "Cedar Day: ثباته 6 ساعات. External مش عندنا، ثباته 12 ساعة.",
        ):
            with self.subTest(draft=draft):
                cleaned = policy.enforce_reply(draft, context=context)
                self.assertIn("Cedar Day: ثباته ممتاز.", cleaned)
                self.assertIn(policy.UNKNOWN_REPLY, cleaned)
                self.assertNotRegex(cleaned, r"\d")
                self.assertEqual(policy.enforce_reply(cleaned, context=context), cleaned)
                self.assertFalse(product_claim_issues(cleaned, {}, context=context))

    def test_higher_thresholds_are_not_promised(self):
        for hours in (12, 24, 48):
            with self.subTest(hours=hours):
                request = policy.request_for(f"عايز ثبات {hours} ساعة")
                cleaned = policy.enforce_reply(f"ثباته {hours} ساعة.", request)
                self.assertIn(policy.LONGEVITY_HOURS, cleaned)
                self.assertNotIn(str(hours), cleaned)
                self.assertIn("ماقدرش أوعدك بمدة أطول محددة", cleaned)
                self.assertEqual(policy.enforce_reply(cleaned, request), cleaned)

    def test_performance_comparisons_are_rejected_independent_of_records(self):
        facts = {"Cedar Day": {"longevity": "12 hours", "projection": "Strong", "notes": []},
                 "Amber Night": {"longevity": "4 hours", "projection": "Weak", "notes": []}}
        for claim in ("ثباته أطول من", "فوحانه أقوى من", "ثابت أكتر من", "أثبت من"):
            with self.subTest(claim=claim):
                self.assertTrue(product_claim_issues(f"Cedar Day {claim} Amber Night.", facts))
        self.assertFalse(product_claim_issues("Cedar Day: ثباته ممتاز وفوحانه قوي.", facts))


class PerformanceIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Performance shop")
        cls.settings = StoreSettings.objects.create(store=cls.store)
        cls.brand = Brand.objects.create(store=cls.store, name="House")
        cls.products = []
        for name, longevity, projection in (("Cedar Day", "4 hours", "Weak"),
                                            ("Amber Night", "12-24 hours", "Enormous"),
                                            ("Blank Fragrance", "", "")):
            product = Product.objects.create(store=cls.store, brand=cls.brand, name=name,
                                             gender="male", occasion="Office", scent_tags=["fresh", "light"],
                                             top_notes="Cedar", longevity=longevity, projection=projection)
            ProductVariant.objects.create(product=product, bottle_type="normal", volume=50, price=500)
            cls.products.append(product)

    def test_facts_and_fallbacks_use_policy_without_rewriting_catalog(self):
        facts = product_claim_facts(self.products)
        for product in self.products:
            with self.subTest(product=product.name):
                original = (product.longevity, product.projection)
                self.assertEqual(facts[product.name]["longevity"], policy.EXCELLENT)
                self.assertEqual(facts[product.name]["projection"], "قوي")
                self.assertEqual(factual_description(product), "ثباته ممتاز وفوحانه قوي")
                block = format_product(product)
                self.assertIn("Longevity: ممتاز", block)
                self.assertIn("Projection: قوي", block)
                product.refresh_from_db()
                self.assertEqual((product.longevity, product.projection), original)

    def test_catalog_descriptions_and_business_facts_cannot_restore_old_values(self):
        from products.services.ai.prompts import get_system_prompt
        product = self.products[0]
        product.description = "طابعه خفيف. ثباته 4 ساعات وفوحانه متوسط."
        self.assertNotIn("4 ساعات", format_product(product))
        self.assertIn("طابعه خفيف", format_product(product))
        self.settings.business_facts = "الشحن 24 ساعة. عطورنا ثباتها 48 ساعة."
        self.settings.save()
        prompt = get_system_prompt(Store.objects.get(pk=self.store.pk))
        self.assertNotIn("ثباتها 48 ساعة", prompt)
        self.assertIn("الشحن 24 ساعة", prompt)

    def test_direct_comparison_uses_current_question(self):
        for message, numeric in (("قارن الثبات والفوحان", False), ("ثباتهم كام ساعة؟", True)):
            with self.subTest(message=message):
                reply, _ = requested_facts(message, self.products)
                for product in self.products:
                    self.assertIn(product.name, reply)
                self.assertEqual(policy.LONGEVITY_HOURS in reply, numeric)
                self.assertNotRegex(reply, r"4 ساعات|12|24|غير مسجل|مش متأكد")

    def test_comparison_followup_resolves_hours_from_history(self):
        history = [{"role": "assistant", "content": "ثباتهم وفوحانهم ممتازين."}]
        reply, _ = requested_facts("طيب كام ساعة؟", self.products, history)
        self.assertIn(policy.LONGEVITY_HOURS, reply)

    def test_performance_followups_answer_the_resolved_subject_without_generation(self):
        from products.services.conversation_service import save_message
        product = self.products[0]
        for previous, message, expected in (
            ("Cedar Day ثباته ممتاز.", "قولي رقم", "ثباته أكتر من 8 ساعات."),
            ("Cedar Day فوحانه ممتاز.", "كام ساعة بقى؟", "فوحانه قوي."),
        ):
            with self.subTest(message=message):
                conversation = Conversation.objects.create(store=self.store)
                save_message(conversation, "assistant", previous, internal_context=format_product(product))
                history = [{"role": "assistant", "content": previous}]
                with patch("products.services.router.classify", return_value="product_info"), \
                     patch("products.services.product_info.resolve_products", return_value=[product]), \
                     patch("products.services.product_info.chat") as generate:
                    reply, _ = route(message, history, self.store, conversation)
                self.assertEqual(reply, expected)
                generate.assert_not_called()

    def test_evaluator_uses_current_question_and_structured_history(self):
        from eval_harness import checks
        truth = checks.build_ground_truth(self.store)
        context = format_product(self.products[0])
        for message, previous, reply, invalid in (
            ("ثبات Cedar Day كام ساعة؟", "", "Cedar Day: ثباته أكتر من 8 ساعات.", False),
            ("قولي رقم", "Cedar Day ثباته ممتاز.", "Cedar Day: ثباته أكتر من 8 ساعات.", False),
            ("كام ساعة بقى؟", "Cedar Day فوحانه ممتاز.", "Cedar Day: فوحانه قوي.", False),
            ("كام ساعة بقى؟", "Cedar Day فوحانه ممتاز.", "Cedar Day: ثباته أكتر من 8 ساعات.", True),
            ("ثباته إيه؟", "", "Cedar Day: ثباته أكتر من 8 ساعات.", True),
            ("ريحته إيه؟", "Cedar Day ثباته أكتر من 8 ساعات.", "Cedar Day: ثباته أكتر من 8 ساعات.", True),
            ("ثباته كام ساعة؟", "", "Cedar Day: ثباته 12 ساعة.", True),
        ):
            with self.subTest(message=message, previous=previous):
                findings = checks.check_reply(
                    reply, truth=truth, context=context, customer_text=message, turn_state={},
                    history=[{"role": "assistant", "content": previous}] if previous else [],
                )
                issues = [detail for code, _, detail in findings
                          if code == "unsupported_product_claim" and detail.startswith("performance:")]
                self.assertEqual(bool(issues), invalid)

    def test_evaluator_and_judge_ignore_historical_performance_facts(self):
        from eval_harness import checks, judge
        product = self.products[0]
        product.longevity = "777 hours"
        product.save(update_fields=["longevity"])
        self.settings.business_facts = "ثبات عطورنا 48 ساعة. الشحن 24 ساعة."
        self.settings.save(update_fields=["business_facts"])
        truth = checks.build_ground_truth(Store.objects.get(pk=self.store.pk))
        self.assertNotIn("777", checks._allowed_numbers(truth, "", ""))
        self.assertNotIn("48 ساعة", truth["store_text"])
        self.assertIn("الشحن 24 ساعة", truth["store_text"])
        with patch("products.models.Store.objects.get", return_value=self.store):
            facts = judge.database_facts([product.name])
            store_facts = judge.store_facts()
        self.assertIn("longevity=ممتاز", facts)
        self.assertIn("projection=قوي", facts)
        self.assertNotIn("777", facts)
        self.assertNotIn("48 ساعة", store_facts)
        self.assertIn("الشحن 24 ساعة", store_facts)

    def test_saved_performance_preferences_do_not_filter_rank_or_relax(self):
        conversation = Conversation.objects.create(store=self.store, preferences={
            "gender": "male", "scent_tags": ["fresh"], "longevity": "48 hours", "projection": "intimate",
        })
        search = search_products(conversation.preferences, self.store, keep=[self.products[0].name])
        self.assertEqual(set(search["products"]), set(self.products))
        self.assertIsNone(search["relax_key"])
        self.assertEqual(search["dropped"], {})
        self.assertNotIn("longevity", search["effective_intent"])
        self.assertNotIn("projection", search["effective_intent"])
        baseline = ranking.rank(self.products, {"gender": "male"})
        ranked = ranking.rank(self.products, conversation.preferences)
        self.assertEqual([r.score for r in baseline], [r.score for r in ranked])
        self.assertFalse(any(r.projection_conflict for r in ranked))

    def test_scent_and_budget_constraints_remain_effective(self):
        for intent in ({"excluded_scent_tags": ["light"]}, {"max_price": 200}, {"scent_tags": ["oud"]}):
            with self.subTest(intent=intent):
                search = search_products({**intent, "longevity": "48 hours", "projection": "strong"}, self.store)
                self.assertFalse(search["products"].exists())
                self.assertNotIn(search["relax_key"], {"longevity", "projection"})

    def test_similarity_and_value_do_not_use_historical_performance(self):
        from products.services.sales.value import cross_product_value
        reference = similarity.reference_from_product(self.products[0])
        result = similarity.compare(reference, self.products[1])
        self.assertIsNone(result.performance)
        dimensions, unknown = cross_product_value(*self.products[:2])
        self.assertFalse({"الثبات", "الفوحان"} & ({row[0] for row in dimensions} | set(unknown)))

    def test_static_faq_also_obeys_the_current_turn(self):
        StaticFAQ.objects.create(store=self.store, question="الثبات؟", keywords="ثبات",
                                 answer="عطورنا ثباتها 6 ساعات وفوحانها ضعيف.")
        reply, _ = route("الثبات إيه؟", [], self.store)
        self.assertNotRegex(reply, r"\d")
        self.assertIn("ثباتها ممتاز وفوحانها قوي", reply)
        reply, _ = route("الثبات كام ساعة؟", [], self.store)
        self.assertIn(policy.LONGEVITY_HOURS, reply)
        self.assertIn("فوحانها قوي", reply)

    def test_router_finalization_keeps_checkout_confirmation_hash_in_sync(self):
        conversation = Conversation.objects.create(store=self.store)
        draft = "50 مل بـ500 جنيه. ثباته 6 ساعات."
        cart = Cart.objects.create(conversation=conversation, quote={
            "summary_hash": hashlib.sha256(draft.encode()).hexdigest(), "other": "preserve",
        })
        with patch("products.services.router._route", return_value=(draft, "CONTEXT")):
            reply, context = route("ثباته إيه؟", [], self.store, conversation)
        self.assertEqual(context, "CONTEXT")
        cart.refresh_from_db()
        self.assertEqual(cart.quote["summary_hash"], hashlib.sha256(reply.encode()).hexdigest())
        self.assertEqual(cart.quote["other"], "preserve")
        self.assertIn("50 مل بـ500 جنيه", reply)

    def test_generated_bad_claim_uses_existing_retry(self):
        product = self.products[0]
        context = format_product(product)
        with policy.performance_turn("ثبات Cedar Day كام ساعة؟"):
            with patch("products.services.ai.recommendation.chat") as unused:
                from unittest.mock import Mock
                retry = Mock(return_value=("Cedar Day: ثباته أكتر من 8 ساعات.", context))
                reply, _, _ = _rephrased("Cedar Day: ثباته 48 ساعة.", context, [], self.store, retry)
            retry.assert_called_once()
            unused.assert_not_called()
        self.assertIn(policy.LONGEVITY_HOURS, reply)

    def test_failed_regeneration_falls_back_to_shared_policy(self):
        context = format_product(self.products[0])
        with policy.performance_turn("ثبات Cedar Day كام ساعة؟"):
            reply, _, _ = _rephrased("Cedar Day: ثباته 48 ساعة.", context, [], self.store,
                                     lambda hint: ("Cedar Day: ثباته 12 ساعة.", context))
        self.assertIn(policy.LONGEVITY_HOURS, reply)
        self.assertNotIn("12", reply)

    def test_recommendation_validates_model_and_sends_only_uniform_facts(self):
        product = self.products[0]
        intent = {"gender": "male", "scent_tags": ["fresh"], "longevity": "24 hours", "projection": "intimate"}
        search = search_products(intent, self.store)
        responses = [json.dumps({"product_ids": [product.pk], "reply": reply}, ensure_ascii=False)
                     for reply in ("Cedar Day: ثباته 6 ساعات.", "Cedar Day: طابعه فريش وثباته ممتاز وفوحانه قوي.")]
        with patch("products.services.ai.recommendation.chat", side_effect=responses) as model:
            reply, _ = recommend("رشحلي عطر فريش", search["products"], store=self.store, intent=intent, search=search)
        self.assertEqual(model.call_count, 2)
        self.assertNotRegex(reply, r"\d+\s*ساع")
        payload = json.loads(model.call_args.args[0][1]["content"])
        self.assertNotIn("projection", payload["preferences"])
        self.assertNotIn("longevity", payload["preferences"])
        for row in payload["eligible_products"]:
            self.assertEqual((row["longevity"], row["projection"]), ("ممتاز", "قوي"))

    def test_identification_cannot_differentiate_by_duration(self):
        from products.services.identification_service import score_candidates
        self.assertEqual(score_candidates({"longevity_hint": "long"}, self.products), [])
