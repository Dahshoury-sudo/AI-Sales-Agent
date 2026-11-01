"""Cross-brand ranking must survive generated prose and factual recoveries."""
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services.ai.recommendation import recommend
from products.services.conversation_service import save_message, build_llm_history
from products.services.product_formatting import format_products
from products.services.router import route
from products.services.sales.brand_response import disclose_brands
from products.services.search_service import search_products


class BrandPreferenceReplyTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Perfamix")
        StoreSettings.objects.create(store=self.store)
        own = Brand.objects.create(store=self.store, name="Perfamix")
        other = Brand.objects.create(store=self.store, name="Dior")
        self.local = self.product("Local Blend", own, "Moderate")
        self.better = self.product("Fresh Choice", other, "Strong")
        self.intent = {"brand": "STORE_BRAND_EXCLUSIVE", "brand_strict": False,
                       "gender": "male", "occasion": "daily", "projection": "strong"}
        self.conversation = Conversation.objects.create(store=self.store, preferences=self.intent)

    def product(self, name, brand, projection):
        product = Product.objects.create(store=self.store, brand=brand, name=name, gender="male",
                                         occasion="Office", projection=projection, longevity="8 hours",
                                         top_notes="Bergamot", base_notes="Cedar")
        ProductVariant.objects.create(product=product, volume=50, price=750)
        return product

    def search(self):
        return search_products(self.intent, self.store, keep={self.local.name}, offered={self.better.name})

    def test_both_branches_disclose_alternative_brand_and_remove_stale_continuity_order(self):
        search = self.search()
        history = [{"role": "assistant", "content": "Local Blend مناسب للمكتب. هتستخدمه فين؟"}]
        for alternative_branch in (False, True):
            with self.subTest(alternative_branch=alternative_branch), patch(
                "products.services.ai.recommendation.chat", return_value="Fresh Choice مناسب للنهار وفوحانه قوي.",
            ) as chat:
                reply, _ = recommend("للنهار وعايزه فواح", Product.objects.none() if alternative_branch else search["products"],
                                     history, alternatives=search["products"] if alternative_branch else None,
                                     store=self.store, intent=self.intent, search=search)
            prompt = chat.call_args.args[0][-1]["content"]
            self.assertIn("الأفضل حسب طلبه الحالي هو Fresh Choice", prompt)
            self.assertNotIn("الكلام دلوقتي على: Local Blend", prompt)
            self.assertNotIn("ممنوع تعرض عليه اتنين تاني", prompt)
            self.assertIn("Fresh Choice من Dior", reply)
            self.assertIn("غير Perfamix", reply)
            self.assertEqual(chat.call_count, 1)

    def test_worse_fit_cannot_lead_after_the_correction_attempt(self):
        search = self.search()
        with patch("products.services.ai.recommendation.chat", return_value="Local Blend هو الأفضل.") as chat:
            reply, _ = recommend("رشحلي فواح للنهار", search["products"], store=self.store,
                                 intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(reply.startswith(self.better.name))
        self.assertIn("Dior", reply)
        self.assertIn("غير Perfamix", reply)
        self.assertNotIn("هو الأفضل", reply)

    def test_correct_disclosure_is_not_repeated(self):
        reply = "Fresh Choice من Dior، براند تاني غير Perfamix، مناسب للنهار وفوحانه قوي."
        self.assertEqual(disclose_brands(reply, [self.better], self.intent, self.store), reply)

    def test_internal_warning_is_rewritten_as_supported_customer_wording(self):
        search = self.search()
        with patch("products.services.ai.recommendation.chat", side_effect=[
            "Fresh Choice فوحانه قوي. ⚠️ مش مطابق في قوة الفوحان.",
            "Fresh Choice للنهار وفوحانه قوي.",
        ]) as chat:
            reply, _ = recommend("رشحلي فواح", search["products"], store=self.store, intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        self.assertNotIn("مش مطابق في", reply)
        self.assertIn("Fresh Choice من Dior", reply)

    def test_equal_projection_does_not_support_claiming_one_is_lighter(self):
        from products.services.sales.brand_response import response_issues
        self.local.projection = "Strong"
        draft = "Fresh Choice وLocal Blend ثابتين 8 ساعات، بس Local Blend أخف شوية."
        issues = response_issues(draft, [self.better, self.local], self.intent, store=self.store)
        self.assertTrue(any("الفوحان المسجل متساوي" in issue for issue in issues))
        self.local.projection = "Moderate"
        self.assertFalse(response_issues(draft, [self.better, self.local], self.intent, store=self.store))

    def test_stronger_projection_does_not_imply_longer_longevity(self):
        from products.services.sales.brand_response import response_issues
        draft = "Local Blend للمكتب. Fresh Choice ثباته وفوحانه أقوى."
        issues = response_issues(draft, [self.better, self.local], self.intent, store=self.store)
        self.assertTrue(any("الثبات المسجل متساوي" in issue for issue in issues))
        self.better.longevity = "10 hours"
        self.assertFalse(response_issues(draft, [self.better, self.local], self.intent, store=self.store))

    def test_optional_requested_brand_products_do_not_expand_reply_beyond_two(self):
        third = self.product("Third Option", self.better.brand, "Strong")
        search = self.search()
        with patch("products.services.ai.recommendation.chat", return_value="Fresh Choice وThird Option ومعاهم Local Blend.") as chat:
            reply, _ = recommend("رشحلي عطرين", search["products"], store=self.store, intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        self.assertIn(self.better.name, reply)
        self.assertIn(third.name, reply)
        self.assertNotIn(self.local.name, reply)

    def test_known_wrong_brand_gets_corrected(self):
        search = self.search()
        with patch("products.services.ai.recommendation.chat", return_value="Fresh Choice من Perfamix مناسب للنهار.") as chat:
            reply, _ = recommend("رشحلي فواح للنهار", search["products"], store=self.store,
                                 intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        self.assertNotIn("Fresh Choice من Perfamix", reply)
        self.assertIn("Fresh Choice من Dior", reply)

    def test_strict_brand_has_no_cross_brand_disclosure(self):
        intent = {**self.intent, "brand_strict": True}
        search = search_products(intent, self.store)
        with patch("products.services.ai.recommendation.chat", return_value="Local Blend مناسب للمكتب."):
            reply, _ = recommend("Perfamix فقط", search["products"], store=self.store, intent=intent, search=search)
        self.assertNotIn("Dior", reply)
        self.assertNotIn("براند تاني", reply)

    def test_constrained_recommendation_discloses_brand_and_keeps_exact_prices(self):
        intent = {**self.intent, "requested_volume": 50, "max_price": 800, "recommendation_count": 1}
        search = search_products(intent, self.store)
        with patch("products.services.ai.recommendation.chat") as chat:
            reply, _ = recommend("50 ملي تحت 800", search["products"], store=self.store, intent=intent, search=search)
        chat.assert_not_called()
        self.assertIn("Fresh Choice من Dior", reply)
        self.assertIn("غير Perfamix", reply)
        self.assertIn("750", reply)
        self.assertNotIn(self.local.name, reply)

    def test_price_recovery_keeps_brand_disclosure_and_saved_preference(self):
        save_message(self.conversation, "assistant", "Local Blend للمكتب.", internal_context=format_products([self.local]))
        with patch("products.services.router.classify", return_value="recommendation"), patch(
            "products.services.router.extract_intent", return_value={"projection": "strong", "max_price": 800},
        ), patch("products.services.ai.recommendation.chat", return_value="Fresh Choice 50 ملي بـ999 جنيه."):
            reply, _ = route("عايز فواح للنهار في حدود 800", build_llm_history(self.conversation), self.store, self.conversation)
        self.assertIn("750", reply)
        self.assertNotIn("999", reply)
        self.assertIn("Fresh Choice من Dior", reply)
        self.assertIn("غير Perfamix", reply)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences["brand"], "STORE_BRAND_EXCLUSIVE")
        self.assertFalse(self.conversation.preferences["brand_strict"])

    def test_named_product_question_is_not_a_new_cross_brand_recommendation(self):
        with patch("products.services.router.classify", return_value="product_info"), patch(
            "products.services.product_info.chat", return_value="Local Blend مناسب للمكتب.",
        ):
            reply, _ = route("Local Blend ينفع للمكتب؟", [], self.store, self.conversation)
        self.assertNotIn(self.better.name, reply)
        self.assertNotIn("براند تاني", reply)
