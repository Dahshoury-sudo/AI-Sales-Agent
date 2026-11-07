"""Brand disclosures and verified prices survive AI selection and factual recovery."""
import json
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
                       "gender": "male", "occasion": "daily", "projection": "strong", "scent_tags": ["fresh"]}
        self.conversation = Conversation.objects.create(store=self.store, preferences=self.intent)

    def product(self, name, brand, projection):
        product = Product.objects.create(store=self.store, brand=brand, name=name, gender="male",
                                         occasion="Office, Daily", projection=projection, longevity="8 hours",
                                         scent_tags=["fresh", "woody"],
                                         top_notes="Bergamot", base_notes="Cedar")
        ProductVariant.objects.create(product=product, volume=50, price=750)
        return product

    def search(self):
        return search_products(self.intent, self.store, keep={self.local.name}, offered={self.better.name})

    def selection(self, reply, *products):
        return json.dumps({"product_ids": [p.pk for p in (products or (self.better,))], "reply": reply})

    def test_selection_discloses_brand_and_prefers_fit_over_continuity(self):
        self.local.occasion = "Evening"
        self.local.save(update_fields=["occasion"])
        search = self.search()
        history = [{"role": "assistant", "content": "Local Blend مناسب للمكتب. هتستخدمه فين؟"}]
        with patch("products.services.ai.recommendation.chat",
                   return_value=self.selection("Fresh Choice ريحته فريش ومنعش وخشبي، وفوحانه قوي.")) as chat:
            reply, _ = recommend("للنهار وعايزه فواح", search["products"], history,
                                 store=self.store, intent=self.intent, search=search)
        payload = json.loads(chat.call_args.args[0][-1]["content"])
        self.assertEqual([p["id"] for p in payload["eligible_products"]], [self.better.pk, self.local.pk])
        self.assertIn(self.local.name, payload["under_discussion"])
        self.assertNotIn(self.local.name, payload["no_longer_matching"])
        self.assertIn("Fresh Choice من Dior", reply)
        self.assertIn("غير Perfamix", reply)
        self.assertEqual(chat.call_count, 1)

    def test_worse_fit_cannot_lead_after_the_correction_attempt(self):
        self.local.occasion = "Evening"
        self.local.save(update_fields=["occasion"])
        search = self.search()
        with patch("products.services.ai.recommendation.chat", return_value=self.selection("Local Blend هو الأفضل.", self.local)) as chat:
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
            self.selection("Fresh Choice فوحانه قوي. ⚠️ مش مطابق في قوة الفوحان."),
            self.selection("Fresh Choice للنهار وفوحانه قوي."),
        ]) as chat:
            reply, _ = recommend("رشحلي فواح", search["products"], store=self.store, intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        self.assertNotIn("مش مطابق في", reply)
        self.assertIn("Fresh Choice من Dior", reply)

    def test_equal_projection_does_not_support_claiming_one_is_lighter(self):
        from products.services.sales.brand_response import response_issues
        self.local.projection = "Strong"
        draft = "Fresh Choice فوحانه أهدى من Local Blend."
        issues = response_issues(draft, [self.better, self.local], self.intent, store=self.store)
        self.assertTrue(any(issue.startswith("performance:") for issue in issues))
        self.local.projection = "Moderate"
        self.assertTrue(response_issues(draft, [self.better, self.local], self.intent, store=self.store))

    def test_stronger_projection_does_not_imply_longer_longevity(self):
        from products.services.sales.brand_response import response_issues
        draft = "Local Blend للمكتب. Fresh Choice ثباته وفوحانه أقوى."
        issues = response_issues(draft, [self.better, self.local], self.intent, store=self.store)
        self.assertTrue(any(issue.startswith("performance:") for issue in issues))
        self.better.longevity = "10 hours"
        self.assertTrue(response_issues(draft, [self.better, self.local], self.intent, store=self.store))

    def test_optional_requested_brand_products_do_not_expand_reply_beyond_two(self):
        third = self.product("Third Option", self.better.brand, "Strong")
        search = self.search()
        with patch("products.services.ai.recommendation.chat", return_value=self.selection(
            "Fresh Choice وThird Option ومعاهم Local Blend.", self.better, third, self.local)) as chat:
            reply, _ = recommend("رشحلي عطرين", search["products"], store=self.store, intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        from products.services.sales.naming import names_in
        self.assertEqual(len(set(names_in(reply, [self.better.name, third.name, self.local.name]))), 2)

    def test_known_wrong_brand_gets_corrected(self):
        search = self.search()
        with patch("products.services.ai.recommendation.chat", return_value=self.selection("Fresh Choice من Perfamix مناسب للنهار.")) as chat:
            reply, _ = recommend("رشحلي فواح للنهار", search["products"], store=self.store,
                                 intent=self.intent, search=search)
        self.assertEqual(chat.call_count, 2)
        self.assertNotIn("Fresh Choice من Perfamix", reply)
        self.assertIn("Fresh Choice من Dior", reply)

    def test_strict_brand_has_no_cross_brand_disclosure(self):
        intent = {**self.intent, "brand_strict": True, "projection": "moderate"}
        search = search_products(intent, self.store)
        with patch("products.services.ai.recommendation.chat", return_value=self.selection("Local Blend مناسب للمكتب.", self.local)):
            reply, _ = recommend("Perfamix فقط", search["products"], store=self.store, intent=intent, search=search)
        self.assertNotIn("Dior", reply)
        self.assertNotIn("براند تاني", reply)

    def test_constrained_recommendation_discloses_brand_and_keeps_exact_prices(self):
        intent = {**self.intent, "requested_volume": 50, "max_price": 800, "recommendation_count": 1}
        search = search_products(intent, self.store)
        with patch("products.services.ai.recommendation.chat", return_value=self.selection(
            "Fresh Choice ريحته فريش ومنعش وخشبي، وفوحانه قوي."
        )) as chat:
            reply, _ = recommend("50 ملي تحت 800", search["products"], store=self.store, intent=intent, search=search)
        chat.assert_called_once()
        self.assertEqual(reply.count("Fresh Choice"), 1)
        self.assertIn("من Dior", reply)
        self.assertIn("غير Perfamix", reply)
        self.assertIn("750", reply)
        self.assertNotIn(self.local.name, reply)

    def test_price_recovery_keeps_brand_disclosure_and_saved_preference(self):
        save_message(self.conversation, "assistant", "Local Blend للمكتب.", internal_context=format_products([self.local]))
        with patch("products.services.router.classify", return_value="recommendation"), patch(
            "products.services.router.extract_intent", return_value={"projection": "strong", "max_price": 800},
        ), patch("products.services.ai.recommendation.chat", return_value=self.selection("Fresh Choice 50 ملي بـ999 جنيه.")):
            reply, _ = route("عايز فواح للنهار في حدود 800", build_llm_history(self.conversation), self.store, self.conversation)
        self.assertIn("750", reply)
        self.assertNotIn("999", reply)
        self.assertEqual(reply.count("Fresh Choice"), 1)
        self.assertIn("من Dior", reply)
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
