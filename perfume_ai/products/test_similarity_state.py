"""Customer evidence is required before a perfume becomes a lookalike target."""

from django.test import TestCase
from unittest.mock import patch

from products.models import Brand, Conversation, Message, Product, ProductVariant, Store
from products.services.conversation_service import merge_preferences
from products.services.sales.reference import STATE_KEY, similarity_requested
from products.services.search_service import search_products


class SimilarityStateTests(TestCase):
    def setUp(self):
        self.confirmer = self.enterContext(patch(
            "products.services.product_resolver.confirm_unplaced", return_value=None,
        ))
        self.store = Store.objects.create(name="Similarity Store")
        self.brand = Brand.objects.create(store=self.store, name="Carolina Herrera")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="212 VIB Man", gender="male",
            top_notes="Ginger", middle_notes="Pepper", base_notes="Wood",
        )
        ProductVariant.objects.create(product=self.product, volume=50, price=500)
        self.conversation = Conversation.objects.create(store=self.store)

    def merge(self, intent, message, history=None):
        result = merge_preferences(self.conversation, intent, message, history=history)
        self.conversation.refresh_from_db()
        return result

    def start(self, name="212 VIB Man", notes=None):
        return self.merge(
            {"similar_to": name, "similar_to_notes": notes or ["wood"]},
            f"عايز حاجة شبه {name}",
        )

    def seed_legacy(self):
        self.conversation.preferences = {
            "similar_to": self.product.name, "similar_to_notes": ["wood"],
            "gender": "male", "projection": "strong", "max_price": 1000,
        }
        self.conversation.save(update_fields=["preferences"])

    def test_exact_name_and_product_interest_do_not_start_similarity(self):
        for text in ("212", "عايز 212 VIB Man", "بحب 212 VIB Man", "عندي 212 VIB Man", "ليه مرشحتش 212 VIB Man", "زي ما قلت عايز ديور"):
            with self.subTest(text=text):
                result = self.merge({"similar_to": self.product.name, "similar_to_notes": ["wood"]}, text)
                self.assertNotIn("similar_to", result)
                self.assertNotIn("similar_to_notes", self.conversation.preferences)
                self.assertNotIn(STATE_KEY, self.conversation.sales_state)

    def test_explicit_reference_records_actual_customer_source(self):
        self.start()
        self.assertEqual(self.conversation.sales_state[STATE_KEY], {
            "reference_name": self.product.name,
            "source_text": "عايز حاجة شبه 212 VIB Man",
        })

    def test_same_vibe_can_establish_a_resolved_reference(self):
        self.merge({"similar_to": self.product.name}, "عايز حاجة تانية في نفس الجو", history=[
            {"role": "assistant", "content": "212 VIB Man مناسب لطلبك"},
        ])
        self.assertEqual(self.conversation.preferences["similar_to"], self.product.name)

    def test_reference_can_be_named_earlier_in_same_customer_message(self):
        result = self.merge({"similar_to": self.product.name},
                            "بحب ريحة 212 VIB Man بس عايز حاجة شبهه ومش منتشرة أوي")
        self.assertEqual(result["similar_to"], self.product.name)

    def test_same_message_shortened_name_keeps_genuine_similarity(self):
        product = Product.objects.create(store=self.store, brand=self.brand, name="Dior Sauvage", gender="male")
        result = self.merge({"similar_to": product.name},
                            "بحب ريحة Sauvage بس عايز حاجة شبهه ومش منتشرة أوي")
        self.assertEqual(result["similar_to"], product.name)

    def test_brand_and_budget_refine_a_genuine_similarity_request(self):
        self.start()
        origin = self.conversation.sales_state[STATE_KEY]
        self.merge({"brand": "Dior"}, "ديور")
        result = self.merge({"max_price": 900}, "900 جنيه")
        self.assertEqual(result["similar_to"], self.product.name)
        self.assertEqual(result["brand"], "Dior")
        self.assertEqual(result["max_price"], 900)
        self.assertEqual(self.conversation.sales_state[STATE_KEY], origin)

    def test_scent_analogies_do_not_require_a_perfume_reference(self):
        for text in ("عايزه عطر حريمي مسكر اوي زي الحلويات، وميزانيتي 700",
                     "عايز ريحة شبه الفانيليا", "something like desserts"):
            with self.subTest(text=text):
                result = self.merge({"gender": "female", "notes": ["sweet"], "max_price": 700}, text)
                self.assertNotIn("similarity_needs_reference", result)
                self.assertNotIn("similar_to", result)
                self.assertEqual(result["notes"], ["sweet"])
        self.confirmer.assert_not_called()

    def test_a_negated_scent_analogy_does_not_withdraw_the_reference(self):
        self.start()
        result = self.merge({"avoid_notes": ["sweet"]}, "بس مش عايز حاجة زي الحلويات")
        self.assertEqual(result["similar_to"], self.product.name)

    def test_arabic_reference_uses_a_confirmed_catalogue_identity(self):
        product = Product.objects.create(store=self.store, brand=self.brand, name="Sauvage", gender="male")
        Product.objects.create(store=self.store, brand=self.brand, name="Sauvage Elixir", gender="male")
        self.confirmer.return_value = product
        result = self.merge({"similar_to": "Sauvage", "similar_to_notes": ["ambroxan"]},
                            "عايز حاجة شبه سوفاج")
        self.assertEqual(result["similar_to"], product.name)
        self.assertNotIn("similarity_needs_reference", result)
        self.confirmer.assert_called_once_with("سوفاج", self.store)

    def test_wrong_or_failed_confirmation_cannot_establish_a_reference(self):
        Product.objects.create(store=self.store, brand=self.brand, name="Sauvage", gender="male")
        for answer in (None, self.product):
            with self.subTest(answer=answer):
                self.confirmer.return_value = answer
                result = self.merge({"similar_to": "Sauvage"}, "عايز حاجة شبه سوفاج")
                self.assertNotIn("similar_to", result)
                self.assertTrue(result.get("similarity_needs_reference"))
        self.confirmer.side_effect = TimeoutError("simulated")
        result = self.merge({"similar_to": "Sauvage"}, "عايز حاجة شبه سوفاج")
        self.assertNotIn("similar_to", result)

    def test_literal_names_do_not_need_an_extra_confirmation(self):
        self.start()
        self.confirmer.assert_not_called()

    def test_unsupported_fresh_reference_cannot_replace_a_valid_one(self):
        self.start()
        result = self.merge({"brand": "Dior", "similar_to": "Dior Sauvage", "similar_to_notes": ["citrus"]}, "ديور")
        self.assertEqual(result["similar_to"], self.product.name)
        self.assertEqual(result["similar_to_notes"], ["wood"])

    def test_brand_example_is_not_a_similarity_source(self):
        result = self.merge({"brand": "Dior", "similar_to": self.product.name}, "عايز حاجة من براند زي ديور")
        self.assertNotIn("similar_to", result)
        self.assertNotIn(STATE_KEY, self.conversation.sales_state)

    def test_source_must_name_the_actual_target_after_comparison_cue(self):
        for message in ("عايز حاجة شبه Dior Sauvage", "بحب 212 VIB Man بس عايز حاجة شبه Dior Sauvage"):
            with self.subTest(message=message):
                result = self.merge({"similar_to": self.product.name}, message)
                self.assertNotIn("similar_to", result)

    def test_incidental_comparison_words_do_not_withdraw_valid_reference(self):
        self.start()
        for message in ("محتاج فوحان قوي زي ما قلت", "عايز حاجة من براند زي ديور"):
            with self.subTest(message=message):
                result = self.merge({}, message)
                self.assertEqual(result["similar_to"], self.product.name)

    def test_numeric_similarity_target_uses_the_recently_offered_variant(self):
        Product.objects.create(store=self.store, brand=self.brand, name="212 Men", gender="male")
        history = [{"role": "assistant", "content": "212 VIB Man مناسب ليك. ميزانيتك كام؟"}]
        result = self.merge({"similar_to": self.product.name}, "عايز حاجة شبه 212", history)
        self.assertEqual(result["similar_to"], self.product.name)

    def test_legacy_numeric_target_uses_offer_before_request(self):
        from products.services.product_formatting import format_products
        Product.objects.create(store=self.store, brand=self.brand, name="212 Men", gender="male")
        self.seed_legacy()
        Message.objects.create(conversation=self.conversation, role="assistant", content="212 VIB Man مناسب ليك", internal_context=format_products([self.product]))
        Message.objects.create(conversation=self.conversation, role="user", content="عايز حاجة شبه 212")
        self.assertEqual(self.merge({}, "ديور")["similar_to"], self.product.name)

    def test_history_only_caller_can_establish_contextual_similarity(self):
        result = merge_preferences(None, {"similar_to": self.product.name}, "نفس الجو", history=[
            {"role": "assistant", "content": self.product.name},
        ])
        self.assertEqual(result["similar_to"], self.product.name)

    def test_same_vibe_without_any_target_context_cannot_start_similarity(self):
        result = self.merge({"similar_to": self.product.name}, "نفس الجو")
        self.assertNotIn("similar_to", result)

    def test_unsent_offer_cannot_establish_fresh_or_legacy_similarity(self):
        Message.objects.create(conversation=self.conversation, role="assistant",
                               content="212 VIB Man مناسب", delivery_status="failed")
        result = self.merge({"similar_to": self.product.name}, "نفس الجو")
        self.assertNotIn("similar_to", result)
        self.seed_legacy()
        Message.objects.create(conversation=self.conversation, role="user", content="نفس الجو")
        self.assertNotIn("similar_to", self.merge({}, "ديور"))

    def test_history_only_numeric_source_uses_explicit_store(self):
        result = merge_preferences(None, {"similar_to": self.product.name}, "عايز حاجة شبه 212",
                                   history=[{"role": "assistant", "content": self.product.name}],
                                   store=self.store)
        self.assertEqual(result["similar_to"], self.product.name)

    def test_replacement_does_not_reuse_old_reference_notes(self):
        self.start()
        result = self.merge({"similar_to": "Mystery Perfume"}, "لا عايز حاجة شبه Mystery Perfume")
        self.assertEqual(result["similar_to"], "Mystery Perfume")
        self.assertNotIn("similar_to_notes", result)
        self.assertEqual(self.conversation.sales_state[STATE_KEY]["reference_name"], "Mystery Perfume")

    def test_explicit_withdrawal_clears_both_fields_and_provenance(self):
        for message in ("مش عايز حاجة شبهه", "بلاش المقارنة", "العطر نفسه مش بديل", "مش لازم يبقى شبه 212"):
            with self.subTest(message=message):
                self.start()
                result = self.merge({"similar_to": self.product.name, "similar_to_notes": ["wood"]}, message)
                self.assertNotIn("similar_to", result)
                self.assertNotIn("similar_to_notes", self.conversation.preferences)
                self.assertNotIn(STATE_KEY, self.conversation.sales_state)

    def test_clearing_either_reference_field_clears_the_pair(self):
        self.start()
        result = self.merge({"clear_preferences": ["similar_to_notes"]}, "مش لازم شبهه")
        self.assertNotIn("similar_to", result)
        self.assertNotIn("similar_to_notes", result)

    def test_new_recipient_reset_discards_old_similarity(self):
        self.start()
        result = self.merge({"reset_preferences": True, "gender": "female"}, "لا دي هدية لمراتي")
        self.assertEqual(result["gender"], "female")
        self.assertNotIn("similar_to", result)
        self.assertNotIn(STATE_KEY, self.conversation.sales_state)

    def test_reset_can_start_a_new_explicit_reference(self):
        self.start()
        result = self.merge({"reset_preferences": True, "similar_to": "Mystery Perfume"}, "انسى اللي قلته عايز حاجة شبه Mystery Perfume")
        self.assertEqual(result["similar_to"], "Mystery Perfume")
        self.assertNotIn("similar_to_notes", result)
        self.assertEqual(self.conversation.sales_state[STATE_KEY]["reference_name"], "Mystery Perfume")

    def test_legacy_bare_name_and_assistant_claim_cannot_establish_similarity(self):
        self.seed_legacy()
        Message.objects.create(conversation=self.conversation, role="user", content="212")
        Message.objects.create(conversation=self.conversation, role="assistant", content="العميل طلب حاجة شبه 212 VIB Man، ومفيش عطر قريب منه")
        result = self.merge({"brand": "Dior", "similar_to": self.product.name, "similar_to_notes": ["wood"]}, "ديور")
        self.assertNotIn("similar_to", result)
        self.assertNotIn("similar_to_notes", self.conversation.preferences)
        self.assertEqual(result["gender"], "male")
        self.assertEqual(result["projection"], "strong")
        self.assertEqual(result["max_price"], 1000)
        self.assertEqual(result["brand"], "Dior")

    def test_legacy_explicit_request_survives_outside_short_history_window(self):
        self.seed_legacy()
        source = "عايز حاجة شبه 212 VIB Man"
        Message.objects.create(conversation=self.conversation, role="user", content=source)
        for _ in range(10):
            Message.objects.create(conversation=self.conversation, role="user", content="تمام")
        result = self.merge({"brand": "Dior"}, "ديور", history=[])
        self.assertEqual(result["similar_to"], self.product.name)
        self.assertEqual(self.conversation.sales_state[STATE_KEY]["source_text"], source)

    def test_legacy_request_followed_by_withdrawal_stays_cleared(self):
        self.seed_legacy()
        for text in ("عايز حاجة شبه 212 VIB Man", "مش عايز حاجة شبهه"):
            Message.objects.create(conversation=self.conversation, role="user", content=text)
        self.assertNotIn("similar_to", self.merge({}, "ديور"))

    def test_legacy_request_for_different_reference_is_not_evidence(self):
        self.seed_legacy()
        Message.objects.create(conversation=self.conversation, role="user", content="عايز حاجة شبه Dior Sauvage")
        self.assertNotIn("similar_to", self.merge({}, "ديور"))

    def test_legacy_same_vibe_uses_context_only_with_customer_request(self):
        self.seed_legacy()
        Message.objects.create(conversation=self.conversation, role="assistant", content="212 VIB Man مناسب")
        Message.objects.create(conversation=self.conversation, role="user", content="عايز حاجة تانية في نفس الجو")
        self.assertEqual(self.merge({}, "ديور")["similar_to"], self.product.name)

    def test_notes_answer_completes_valid_unknown_reference_profile(self):
        self.merge({"similar_to": "Mystery Perfume"}, "عايز حاجة شبه Mystery Perfume")
        result = self.merge({"similar_to_notes": ["sweet"]}, "بحب الحلاوة")
        self.assertEqual(result["similar_to"], "Mystery Perfume")
        self.assertEqual(result["similar_to_notes"], ["sweet"])

    def test_dropped_reference_is_not_excluded_from_search(self):
        intent = self.merge({"similar_to": self.product.name}, "212")
        results = search_products(intent, self.store)
        self.assertIsNone(results["similarity"])
        self.assertIn(self.product, results["products"])

    def test_trusted_search_contract_still_excludes_explicit_reference(self):
        results = search_products({"similar_to": self.product.name}, self.store)
        self.assertNotIn(self.product, results["products"])
        self.assertEqual(results["reference_product"], self.product)

    def test_same_vibe_and_explicit_similarity_cues_respect_negation(self):
        for text in ("عايز حاجة زي سوفاج", "بديل 212", "نفس الجو", "something like Sauvage", "عايز حاجة تشبه سوفاج", "عايز نفس الريحة", "حاجة على نفس ستايل 212"):
            self.assertTrue(similarity_requested(text), text)
        for text in ("212", "بحب سوفاج", "مش عايز بديل", "مش شبه 212", "زي ما قلت", "I don't want something like Sauvage"):
            self.assertFalse(similarity_requested(text), text)
