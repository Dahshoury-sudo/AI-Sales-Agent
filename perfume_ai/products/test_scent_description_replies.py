"""Description policy at catalog, model-reply and recovery boundaries."""

import json
from unittest.mock import Mock, patch

from django.test import TestCase

from products.models import Brand, Product, ProductVariant, Store
from products.occasions import ACTIVE, DAILY, WORK
from products.services.ai.recommendation import recommend, recommendation_fit
from products.services.ai.scent_recommendation import _record
from products.services.price_reply import fallback_price_reply, requested_product_details
from products.services.performance_policy import performance_turn
from products.services.product_formatting import format_products, product_claim_facts
from products.services.product_info import get_product_info
from products.services.reply_sanitizer import product_claim_issues
from products.services.router import _rephrased, route
from products.services.search_service import search_products


class ScentDescriptionReplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Simple descriptions")
        cls.brand = Brand.objects.create(store=cls.store, name="House")
        cls.product = Product.objects.create(
            store=cls.store, brand=cls.brand, name="Test Breeze", gender="unisex",
            scent_tags=["fresh", "citrus", "spicy", "woody"], occasion=DAILY,
            top_notes="Vanilla", base_notes="Amber",
            description="عبير الفانيليا الساحر والدافي",
        )
        ProductVariant.objects.create(product=cls.product, volume=50, price="400.00")

    def assert_simple(self, reply):
        for word in ("فريش", "حمضيات", "توابل"):
            self.assertIn(word, reply)
        for word in ("خشبي", "فانيليا", "Vanilla", "Amber", "عبير", "ثبات", "فوحان", "مناسب", "اليومي", "للشغل"):
            self.assertNotIn(word, reply)

    def test_full_and_brief_context_use_model_tags_not_marketing_description(self):
        for brief in (False, True):
            with self.subTest(brief=brief):
                context = format_products([self.product], brief=brief)
                self.assertIn("Simple Scent Description:", context)
                description = context.split("Simple Scent Description:", 1)[1].splitlines()[0]
                self.assert_simple(description)
                self.assertNotIn(self.product.description, context)

    def test_recommendation_record_keeps_matching_tags_and_adds_description(self):
        record = _record(self.product, {})
        self.assertEqual(record["scent_tags"], self.product.scent_tags)
        self.assertEqual(record["description_tags"], self.product.scent_tags[:3])
        self.assert_simple(record["scent_description"])

    def test_recommendation_fallback_uses_first_three_without_preferred_occasion(self):
        self.assert_simple(recommendation_fit([self.product], {}))
        self.product.occasion = "Daily & Casual; Work & Office"
        reply = recommendation_fit([self.product], {"occasion": WORK})
        self.assert_simple(reply)

    def test_recommendation_rejects_incomplete_description_then_uses_grounded_fallback(self):
        intent = {"scent_tags": ["fresh"]}
        search = search_products(intent, self.store)
        draft = json.dumps({"product_ids": [self.product.pk], "reply": "Test Breeze فريش."})
        with patch("products.services.ai.recommendation.chat", return_value=draft) as model:
            reply, _ = recommend("رشحلي عطر فريش", search["products"], store=self.store,
                                 intent=intent, search=search)
        self.assertEqual(model.call_count, 2)
        self.assert_simple(reply)

    def test_valid_description_needs_no_retry(self):
        reply = "Test Breeze فريش ومنعش وفيه ريحة حمضيات وتوابل."
        retry = Mock()
        actual, _, _ = _rephrased(reply, format_products([self.product]), [], self.store, retry,
                                  message="Test Breeze ريحته عاملة إيه؟")
        self.assertEqual(actual, reply)
        retry.assert_not_called()

    def test_ordinary_smell_retry_uses_tags_even_with_recorded_vanilla(self):
        context = format_products([self.product])
        bad = "Test Breeze فيه فانيليا وعنبر."
        retry = Mock(return_value=(bad, context))
        reply, _, _ = _rephrased(bad, context, [], self.store, retry,
                                 message="Test Breeze ريحته عاملة إيه؟")
        retry.assert_called_once()
        self.assert_simple(reply)

    def test_followup_fallback_does_not_need_the_name_repeated(self):
        context = format_products([self.product])
        reply, _, _ = _rephrased("فيه فانيليا.", context, [], self.store,
                                 lambda hint: ("فيه فانيليا.", context), message="ريحته إيه؟")
        self.assert_simple(reply)

    def test_mixed_price_smell_fallback_keeps_verified_price_and_description(self):
        message = "Test Breeze بكام وريحته عاملة إيه؟"
        with patch("products.services.product_info._named_in_message", return_value=[self.product]), \
             patch("products.services.product_info.chat", return_value="Test Breeze فيه فانيليا."):
            _, context = get_product_info(message, [], self.store)
        reply, _ = fallback_price_reply(context, [self.product])
        self.assert_simple(reply)
        self.assertIn("400", reply)
        self.assertEqual(reply.count(self.product.name), 1)

    def test_notes_request_still_allowed_and_does_not_authorize_later_smell_reply(self):
        facts = product_claim_facts([self.product])
        reply = "Test Breeze فيه فانيليا."
        self.assertEqual(product_claim_issues(reply, facts, message="فيه فانيليا؟"), [])
        self.assertTrue(product_claim_issues(reply, facts, message="ريحته إيه؟", history=[
            {"role": "user", "content": "إيه نوتاته؟"},
            {"role": "assistant", "content": reply},
        ]))

    def test_price_and_performance_answers_do_not_gain_a_description(self):
        context = format_products([self.product])
        for message, reply in (("ثباته إيه؟", "ثباته ممتاز."), ("متوفر؟", "متوفر يا فندم.")):
            with self.subTest(message=message):
                retry = Mock()
                actual, _, _ = _rephrased(reply, context, [], self.store, retry, message=message)
                self.assertEqual(actual, reply)
                retry.assert_not_called()

    def test_empty_notes_do_not_block_a_grounded_tag_description(self):
        self.product.top_notes = self.product.base_notes = ""
        context = format_products([self.product])
        self.assertNotIn("أو تقول ريحته فيها إيه", context)
        self.assertIn("Simple Scent Description:", context)

    def test_current_occasion_overrides_saved_preference_during_validation(self):
        self.product.occasion = "Daily & Casual; Work & Office"
        self.product.save(update_fields=["occasion"])
        reply = "Test Breeze فريش وحمضي ومتبل ومناسب للشغل والمكتب."
        retry = Mock()
        result, _, _ = _rephrased(reply, format_products([self.product]), [], self.store, retry,
                                  message="Test Breeze ريحته إيه وهل مناسب للشغل؟", preferred_occasion=DAILY)
        retry.assert_not_called()
        self.assertEqual(result, reply)

    def test_current_occasion_does_not_enter_the_prompt_scent_description(self):
        self.product.occasion = "Daily & Casual; Work & Office"
        with patch("products.services.product_info._named_in_message", return_value=[self.product]), \
             patch("products.services.product_info.chat", return_value="وصف مختصر"):
            _, context = get_product_info("Test Breeze ريحته إيه للشغل؟", [], self.store)
        description = context.split("Simple Scent Description:", 1)[1].splitlines()[0]
        self.assert_simple(description)
        self.assertIn("Occasion: Daily & Casual; Work & Office", context)

    def test_price_fallback_does_not_take_occasion_from_product_name(self):
        self.product.name = "Night Breeze"
        self.product.occasion = "Daily & Casual; Night Out & Hanging Out"
        self.product.save(update_fields=["name", "occasion"])
        with patch("products.services.product_info._named_in_message", return_value=[self.product]), \
             patch("products.services.product_info.chat", return_value="Night Breeze فيه فانيليا."):
            _, context = get_product_info("Night Breeze بكام وريحته؟", [], self.store)
        reply, _ = fallback_price_reply(context, [self.product])
        self.assert_simple(reply)

    def test_routed_smell_question_applies_policy_to_model_draft(self):
        context = format_products([self.product])
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.router.get_product_info", return_value=("Test Breeze فيه فانيليا.", context)):
            reply, _ = route("Test Breeze ريحته عاملة إيه؟", [], self.store)
        self.assert_simple(reply)

    def test_comparison_fallback_keeps_each_products_own_description(self):
        other = Product.objects.create(store=self.store, brand=self.brand, name="Second Choice", gender="unisex",
                                       scent_tags=["sweet", "oud", "heavy"], occasion=WORK)
        context = format_products([self.product, other])
        bad = "Test Breeze فيه فانيليا. Second Choice فيه فانيليا."
        reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context),
                                 message="قارن ريحة Test Breeze وSecond Choice")
        first, second = reply.split("Second Choice")
        self.assert_simple(first)
        for word in ("مسكر", "عودي", "تقيل"):
            self.assertIn(word, second)
        self.assertNotIn("حمضيات", second)
        self.assertNotIn("للشغل", second)

    def test_unsolicited_occasion_is_removed_after_retry_or_provider_failure(self):
        context = format_products([self.product])
        bad = "Test Breeze فريش ومنعش وفيه ريحة حمضيات وتوابل ومناسب للاستخدام اليومي."
        for retry in (Mock(return_value=(bad, context)), Mock(side_effect=RuntimeError("provider unavailable"))):
            with self.subTest(retry=retry):
                reply, _, _ = _rephrased(bad, context, [], self.store, retry,
                                         message="Test Breeze ريحته ايه؟")
                self.assert_simple(reply)
                retry.assert_called_once()

    def test_explicit_occasion_details_are_independent_of_scent_details(self):
        self.product.occasion = "Daily & Casual; Work & Office"
        for question, wants_scent in (
            ("Test Breeze ينفع للشغل؟", False),
            ("Test Breeze مناسب لايه؟", False),
            ("ايه المناسبات المناسبة ليه؟", False),
            ("قولي المناسبات اللي ينفع ليها Test Breeze", False),
            ("Test Breeze المناسبات ايه؟", False),
            ("Is Test Breeze suitable for work?", False),
            ("Would Test Breeze be suitable for work?", False),
            ("ممكن ألبس Test Breeze للشغل؟", False),
            ("Test Breeze ريحته ايه وينفع للشغل؟", True),
            ("Describe Test Breeze and tell me if it is good for work", True),
            ("Describe Test Breeze and its occasions", True),
        ):
            with self.subTest(question=question):
                reply = "\n".join(requested_product_details([self.product], question))
                self.assertIn("مناسب", reply)
                self.assertIn("للشغل" if "work" in question or "للشغل" in question else "اليومي", reply)
                self.assertEqual("حمضيات" in reply, wants_scent)

    def test_recommendation_constraints_do_not_add_occasion_details(self):
        self.product.occasion = "Daily & Casual; Work & Office"
        for message in (
            "رشحلي عطر مناسب للشغل", "Recommend a perfume suitable for work",
            "What perfume is suitable for work?", "ايه العطر المناسب للشغل؟", "قولي عطر مناسب للشغل",
        ):
            with self.subTest(message=message):
                reply = recommendation_fit([self.product], {"occasion": WORK}, message=message)
                self.assert_simple(reply)

    def test_summer_answers_use_season_facts_without_requiring_a_summer_occasion(self):
        self.product.occasion = WORK
        self.product.season = "Summer"
        facts = product_claim_facts([self.product])
        for message, reply in (
            ("ينفع للصيف؟", "Test Breeze مناسب للصيف."),
            ("موسمه ايه؟", "Test Breeze صيفي."),
            ("Is it suitable for summer?", "Test Breeze is suitable for summer."),
        ):
            with self.subTest(message=message):
                self.assertEqual(product_claim_issues(reply, facts, message=message), [])
        for message in ("ينفع للصيف؟", "ممكن ألبسه للصيف؟"):
            with self.subTest(message=message):
                details = "\n".join(requested_product_details([self.product], message))
                self.assertIn("Summer", details)
                self.assertNotIn("للشغل", details)
                self.assertNotIn("مناسبته", details)
                self.assertNotIn("تأكيد", details)
        self.product.season = "Winter"
        self.assertTrue(any(issue.startswith("season:") for issue in product_claim_issues(
            "Test Breeze مناسب للصيف.", product_claim_facts([self.product]), message="ينفع للصيف؟",
        )))
        self.product.occasion = ACTIVE
        self.product.season = "Summer"
        self.assertIn("الجيم", "\n".join(requested_product_details([self.product], "ينفع للجيم؟")))

    def test_requested_missing_or_unrecorded_suitability_needs_confirmation(self):
        for occasion in ("", DAILY):
            with self.subTest(occasion=occasion):
                self.product.occasion = occasion
                reply = "\n".join(requested_product_details([self.product], "ينفع للشغل؟"))
                self.assertIn("تأكيد", reply)
                self.assertNotIn("مناسب للشغل", reply)
                self.assertEqual(product_claim_issues(
                    "Test Breeze مناسبته للشغل محتاجة تأكيد.", product_claim_facts([self.product]),
                    message="ينفع للشغل؟",
                ), [])

    def test_occasion_recovery_preserves_each_named_products_scope(self):
        self.product.occasion = WORK
        self.product.save(update_fields=["occasion"])
        other = Product.objects.create(store=self.store, brand=self.brand, name="Second Choice", gender="unisex",
                                       scent_tags=["sweet", "oud", "heavy"], occasion=DAILY)
        context = format_products([self.product, other])
        bad = "Test Breeze فيه قهوة. Second Choice فيه فانيليا."
        reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context),
                                 message="Test Breeze ينفع للشغل وSecond Choice ريحته ايه؟")
        first, second = reply.split("Second Choice")
        self.assertIn("للشغل", first)
        self.assertNotIn("حمضيات", first)
        self.assertIn("عودي", second)
        self.assertNotIn("مناسب", second)

    def test_recovery_keeps_performance_only_and_explicit_scent_opt_out(self):
        context = format_products([self.product])
        bad = "Test Breeze فيه فانيليا."
        for message in ("ثباته إيه؟", "Test Breeze ثباته إيه من غير وصف ريحته؟"):
            with self.subTest(message=message), performance_turn(message, []):
                reply, _, _ = _rephrased(bad, context, [], self.store,
                                         lambda hint: (bad, context), message=message)
                self.assertIn("ثباته ممتاز", reply)
                for word in ("فريش", "حمضيات", "توابل", "اليومي", "فانيليا"):
                    self.assertNotIn(word, reply)

    def test_availability_recovery_uses_actual_variants(self):
        context = format_products([self.product])
        bad = "Test Breeze فيه فانيليا."
        for available in (True, False):
            with self.subTest(available=available):
                if not available:
                    self.product.variants.all().delete()
                reply, _, _ = _rephrased(bad, context, [], self.store,
                                         lambda hint: (bad, context), message="متوفر؟")
                self.assertIn("متوفر" if available else "مش متوفر", reply)
                self.assertNotIn("فريش", reply)

    def test_recovery_retains_independently_requested_gender_and_performance(self):
        context = format_products([self.product])
        bad = "Test Breeze فيه فانيليا."
        message = "Test Breeze ثباته إيه وهل هو للرجال ولا للجنسين؟"
        with performance_turn(message, []):
            reply, _, _ = _rephrased(bad, context, [], self.store,
                                     lambda hint: (bad, context), message=message)
        self.assertIn("ثباته ممتاز", reply)
        self.assertIn("للجنسين", reply)
        self.assertNotIn("فريش", reply)

    def test_recommendation_accepts_actual_notes_without_treating_them_as_character(self):
        self.product.middle_notes = "Woody Notes"
        self.product.save(update_fields=["middle_notes"])
        intent = {"scent_tags": ["fresh"]}
        search = search_products(intent, self.store)
        expected = "Test Breeze فريش وحمضي ومتبل. نوتاته Woody Notes وVanilla وAmber."
        draft = json.dumps({"product_ids": [self.product.pk], "reply": expected})
        with patch("products.services.ai.recommendation.chat", return_value=draft):
            reply, _ = recommend("رشحلي عطر فريش وقولي نوتاته", search["products"], store=self.store,
                                 intent=intent, search=search)
        self.assertEqual(reply, expected)

    def test_recommendation_recovery_preserves_requested_actual_notes(self):
        intent = {"scent_tags": ["fresh"]}
        search = search_products(intent, self.store)
        draft = json.dumps({"product_ids": [self.product.pk], "reply": "Test Breeze فريش."})
        with patch("products.services.ai.recommendation.chat", return_value=draft):
            reply, _ = recommend("رشحلي عطر فريش وقولي نوتاته", search["products"], store=self.store,
                                 intent=intent, search=search)
        self.assertIn("Vanilla", reply)
        self.assertIn("Amber", reply)
        self.assertIn("حمضيات", reply)

    def test_recovery_preserves_each_named_products_question_scope(self):
        other = Product.objects.create(store=self.store, brand=self.brand, name="Second Choice", gender="unisex",
                                       scent_tags=["sweet", "oud", "heavy"], occasion=WORK, top_notes="Oud")
        context = format_products([self.product, other])
        bad = "Test Breeze فيه قهوة. وSecond Choice فيه فانيليا."
        message = "Test Breeze نوتاته ايه وSecond Choice ريحته ايه؟"
        reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context), message=message)
        first, second = reply.split("Second Choice")
        self.assertIn("Vanilla", first)
        self.assertNotIn("فريش", first)
        self.assertIn("عودي", second)
        self.assertNotIn("نوتاته", second)

    def test_price_recovery_preserves_each_named_products_question_scope(self):
        other = Product.objects.create(store=self.store, brand=self.brand, name="Second Choice", gender="unisex",
                                       scent_tags=["sweet", "oud", "heavy"], occasion=WORK, top_notes="Oud")
        ProductVariant.objects.create(product=other, volume=50, price="500.00")
        message = "Test Breeze بكام ونوتاته ايه وSecond Choice بكام وريحته ايه؟"
        with patch("products.services.product_info._named_in_message", return_value=[self.product, other]), \
             patch("products.services.product_info.chat", return_value="سعرهم؟"):
            _, context = get_product_info(message, [], self.store)
        reply, _ = fallback_price_reply(context, [self.product, other])
        first, second = reply.split("Second Choice")
        self.assertIn("Vanilla", first)
        self.assertNotIn("فريش", first)
        self.assertIn("عودي", second)
        self.assertNotIn("نوتاته", second)

    def test_recommendation_recovery_honors_explicit_no_description(self):
        intent = {"scent_tags": ["fresh"]}
        search = search_products(intent, self.store)
        draft = json.dumps({"product_ids": [self.product.pk], "reply": "Test Breeze فريش."})
        with patch("products.services.ai.recommendation.chat", return_value=draft):
            reply, _ = recommend("رشحلي فريش وقولي نوتاته من غير وصف ريحته", search["products"],
                                 store=self.store, intent=intent, search=search)
        self.assertIn("Test Breeze", reply)
        self.assertIn("Vanilla", reply)
        self.assertNotIn("فريش", reply)

    def test_availability_recovery_respects_original_bottle_stock(self):
        self.product.variants.all().update(bottle_type="original", stock=0)
        context = format_products([self.product])
        bad = "Test Breeze فيه فانيليا."
        reply, _, _ = _rephrased(bad, context, [], self.store,
                                 lambda hint: (bad, context), message="متوفر؟")
        self.assertIn("مش متوفر", reply)
        self.assertNotIn("فريش", reply)

    def test_recovery_retains_notes_dimension_before_each_named_product(self):
        other = Product.objects.create(store=self.store, brand=self.brand, name="Second Choice", gender="unisex",
                                       scent_tags=["sweet", "oud", "heavy"], occasion=WORK, top_notes="Oud")
        context = format_products([self.product, other])
        bad = "Test Breeze فيه قهوة. وSecond Choice فيه فانيليا."
        message = "نوتات Test Breeze ايه ونوتات Second Choice ايه؟"
        reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context), message=message)
        self.assertIn("Test Breeze", reply)
        self.assertIn("Vanilla", reply)
        self.assertIn("Second Choice", reply)
        self.assertIn("Oud", reply)
        self.assertNotIn("فريش", reply)
        self.assertNotIn("عودي", reply)

    def test_valid_dotted_name_comparison_needs_no_recovery(self):
        self.product.name = "No. 5"
        self.product.save(update_fields=["name"])
        other = Product.objects.create(store=self.store, brand=self.brand, name="Second Choice", gender="unisex",
                                       scent_tags=["sweet", "oud", "heavy"], occasion=WORK)
        context = format_products([self.product, other])
        expected = "Second Choice مسكر وعودي وتقيل، وNo. 5 فريش وحمضي ومتبل."
        reply, _, _ = _rephrased(expected, context, [], self.store, lambda hint: ("", context), message="قارن ريحتهم")
        self.assertEqual(reply, expected)
