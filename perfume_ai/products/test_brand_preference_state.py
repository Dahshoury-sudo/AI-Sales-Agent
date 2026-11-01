"""Only a customer's explicit brand restriction makes a preferred house strict."""
import json
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Message, Product, Store
from products.services.ai.intent import extract_intent
from products.services.conversation_service import merge_preferences
from products.services.sales.brand_preference import (
    STORE_BRAND_EXCLUSIVE, matches_brand, preferred_brand_name, strictness_update,
)


class BrandPreferenceStateTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Perfamix")
        self.dior = Brand.objects.create(store=self.store, name="Dior")
        self.own = Brand.objects.create(store=self.store, name=self.store.name)
        self.conversation = Conversation.objects.create(store=self.store)

    def merge(self, fresh, message):
        result = merge_preferences(self.conversation, fresh, message)
        self.conversation.refresh_from_db()
        return result

    def test_ordinary_brand_request_is_soft_and_remembered(self):
        result = self.merge({"brand": "Dior"}, "عايز عطر من ديور")
        self.assertEqual(result["brand"], "Dior")
        self.assertIs(result["brand_strict"], False)
        result = self.merge({"max_price": 1000}, "ميزانيتي 1000")
        self.assertIs(result["brand_strict"], False)
        self.assertEqual(result["brand"], "Dior")

    def test_explicit_exclusivity_is_grounded_and_persists(self):
        for text in ("عايز من ديور بس", "Dior only", "I only want Dior", "مش عايز غير ديور", "فقط من ديور"):
            with self.subTest(text=text):
                result = self.merge({"brand": "Dior"}, text)
                self.assertIs(result["brand_strict"], True)
                result = self.merge({"projection": "strong", "brand_strict": False}, "عايزه فواح")
                self.assertIs(result["brand_strict"], True)

    def test_but_is_not_brand_exclusivity(self):
        for text in ("عايز ديور بس يكون فريش", "ديور بس في حدود 1000", "ديور يكون فواح بس مش تقيل"):
            with self.subTest(text=text):
                result = self.merge({"brand": "Dior", "brand_strict": True}, text)
                self.assertIs(result["brand_strict"], False)

    def test_brand_references_can_restrict_the_remembered_house(self):
        self.merge({"brand": "Dior"}, "من ديور")
        self.assertIs(self.merge({}, "من نفس البراند بس")["brand_strict"], True)

    def test_explicit_openness_keeps_preference_but_clears_exclusivity(self):
        self.merge({"brand": "Dior"}, "من ديور بس")
        result = self.merge({"brand_strict": False}, "ممكن كمان من براند مختلف")
        self.assertIs(result["brand_strict"], False)
        self.assertEqual(result["brand"], "Dior")

    def test_english_openness_preserves_soft_preferred_brand(self):
        self.merge({"brand": "Dior"}, "Dior only")
        result = self.merge({}, "other brands are fine")
        self.assertEqual(result["brand"], "Dior")
        self.assertIs(result["brand_strict"], False)

    def test_negated_openness_does_not_clear_strictness(self):
        self.merge({"brand": "Dior"}, "Dior only")
        self.assertIs(self.merge({"brand_strict": False}, "مش ممكن من براند تاني")["brand_strict"], True)

    def test_changing_brand_does_not_inherit_previous_strictness(self):
        self.merge({"brand": "Dior"}, "Dior only")
        result = self.merge({"brand": "Chanel"}, "لا عايز شانيل")
        self.assertEqual(result["brand"], "Chanel")
        self.assertIs(result["brand_strict"], False)

    def test_changing_brand_can_explicitly_restrict_the_new_house(self):
        self.merge({"brand": "Dior"}, "Dior only")
        result = self.merge({"brand": "Chanel"}, "عايز شانيل فقط")
        self.assertIs(result["brand_strict"], True)

    def test_reset_and_clear_remove_strictness_with_brand(self):
        for fresh, text in (({"clear_preferences": ["brand"]}, "خلاص سيب البراند"),
                            ({"reset_preferences": True}, "لشخص تاني")):
            with self.subTest(fresh=fresh):
                self.merge({"brand": "Dior"}, "Dior only")
                result = self.merge(fresh, text)
                self.assertNotIn("brand", result)
                self.assertNotIn("brand_strict", result)
                self.assertNotIn("brand_strict", self.conversation.preferences)

    def test_refusing_the_preferred_brand_clears_its_strictness(self):
        self.merge({"brand": "Dior"}, "Dior only")
        result = self.merge({"exclude_brands": ["Dior"]}, "بلاش ديور")
        self.assertNotIn("brand", result)
        self.assertNotIn("brand_strict", result)
        self.assertEqual(result["exclude_brands"], ["Dior"])

    def test_legacy_strictness_uses_old_user_history_not_assistant_claims(self):
        self.conversation.preferences = {"brand": "Dior"}
        self.conversation.save(update_fields=["preferences"])
        Message.objects.create(conversation=self.conversation, role="user", content="عايز من ديور بس")
        for _ in range(10):
            Message.objects.create(conversation=self.conversation, role="assistant", content="حاضر")
        result = self.merge({"max_price": 1000}, "ميزانيتي 1000")
        self.assertIs(result["brand_strict"], True)

    def test_legacy_assistant_only_claim_cannot_restrict_brand(self):
        self.conversation.preferences = {"brand": "Dior"}
        self.conversation.save(update_fields=["preferences"])
        Message.objects.create(conversation=self.conversation, role="assistant", content="Dior only")
        self.assertIs(self.merge({}, "تمام")["brand_strict"], False)

    def test_legacy_opening_supersedes_older_exclusivity(self):
        self.conversation.preferences = {"brand": "Dior"}
        self.conversation.save(update_fields=["preferences"])
        for text in ("Dior only", "other brands are fine"):
            Message.objects.create(conversation=self.conversation, role="user", content=text)
        self.assertIs(self.merge({}, "تمام")["brand_strict"], False)

    def test_legacy_exclusivity_for_another_brand_is_not_inherited(self):
        self.conversation.preferences = {"brand": "Chanel"}
        self.conversation.save(update_fields=["preferences"])
        Message.objects.create(conversation=self.conversation, role="user", content="Dior only")
        self.assertIs(self.merge({}, "تمام")["brand_strict"], False)

    def test_legacy_brand_reference_requires_a_matching_customer_anchor(self):
        for earlier, expected in ((["من ديور", "نفس البراند بس"], True), (["نفس البراند بس"], False)):
            with self.subTest(earlier=earlier):
                conversation = Conversation.objects.create(store=self.store, preferences={"brand": "Dior"})
                for text in earlier:
                    Message.objects.create(conversation=conversation, role="user", content=text)
                result = merge_preferences(conversation, {}, "تمام")
                self.assertIs(result["brand_strict"], expected)

    def test_legacy_return_to_brand_does_not_revive_its_old_restriction(self):
        Brand.objects.create(store=self.store, name="Chanel")
        self.conversation.preferences = {"brand": "Dior"}
        self.conversation.save(update_fields=["preferences"])
        for text in ("Dior only", "عايز شانيل", "من ديور"):
            Message.objects.create(conversation=self.conversation, role="user", content=text)
        self.assertIs(self.merge({}, "تمام")["brand_strict"], False)

    def test_legacy_strictness_does_not_read_a_different_stores_history(self):
        self.conversation.preferences = {"brand": "Dior"}
        self.conversation.save(update_fields=["preferences"])
        Message.objects.create(conversation=self.conversation, role="user", content="Dior only")
        other = Store.objects.create(name="Other")
        result = merge_preferences(self.conversation, {}, "تمام", store=other)
        self.assertIs(result["brand_strict"], False)

    def test_store_recommendation_does_not_become_store_brand_or_type(self):
        with patch("products.services.ai.intent.chat", return_value=json.dumps({
            "brand": STORE_BRAND_EXCLUSIVE, "brand_strict": True, "perfume_type": "store_own_made", "gender": "male",
        })):
            intent = extract_intent("عايز عطر رجالي فاخر وفخم من اختيار الستور", store=self.store)
        self.assertNotIn("brand", intent)
        self.assertNotIn("brand_strict", intent)
        self.assertNotIn("perfume_type", intent)
        self.assertEqual(intent["gender"], "male")

    def test_named_brand_alongside_store_recommendation_survives(self):
        result = self.merge({"brand": "Dior"}, "عايز من اختيار الستور عطر من ديور")
        self.assertEqual(result["brand"], "Dior")
        self.assertIs(result["brand_strict"], False)

    def test_store_brand_exclusivity_is_distinct_from_store_recommendation(self):
        result = self.merge({"brand": STORE_BRAND_EXCLUSIVE}, "عايز البراند بتاعكو بس")
        self.assertIs(result["brand_strict"], True)
        self.assertEqual(preferred_brand_name(result["brand"], self.store), "Perfamix")

    def test_own_brand_restriction_survives_extraction_and_merging(self):
        for message in ("عايز عطوركم بس", "تركيبكم بس", "عطور من تصميمكم فقط"):
            with self.subTest(message=message), patch("products.services.ai.intent.chat", return_value=json.dumps({
                "brand": STORE_BRAND_EXCLUSIVE, "brand_strict": True,
            })):
                intent = extract_intent(message, store=self.store)
            self.assertIs(intent["brand_strict"], True)
            result = self.merge(intent, message)
            self.assertEqual(result["brand"], STORE_BRAND_EXCLUSIVE)
            self.assertIs(result["brand_strict"], True)

    def test_explicit_daytime_performance_request_survives_model_omission(self):
        message = "طيب ايه ترشحلي نهار حاجه حلوه وفواحه وثابته"
        for payload in ({}, {"gender": "male", "notes": []},
                        {"occasion": "evening", "projection": "moderate", "avoid_traits": ["strong"]}):
            with self.subTest(payload=payload), patch("products.services.ai.intent.chat", return_value=json.dumps(payload)):
                intent = extract_intent(message, store=self.store)
            self.assertEqual(intent["occasion"], "daytime")
            self.assertEqual(intent["projection"], "strong")
            self.assertEqual(intent["longevity"], "long-lasting")
            self.assertNotIn("strong", intent["avoid_traits"])
            self.assertFalse(intent.get("brand"))
            self.assertFalse(intent.get("season"))

    def test_explicit_daytime_performance_survives_malformed_model_json(self):
        with patch("products.services.ai.intent.chat", return_value="invalid JSON"):
            intent = extract_intent("عايز عطر نهاري ثابت وفواح", store=self.store)
        self.assertEqual(intent, {"occasion": "daytime", "projection": "strong", "longevity": "long-lasting",
                                  "recommendation_mode": "new_goal"})

    def test_daytime_performance_override_does_not_reinterpret_names_or_questions(self):
        from products.services.ai.intent import _explicit_recommendation_updates
        for message in ("هل سوفاج نهاري وثابت وفواح؟", "ترشحلي سوفاج نهار؟",
                        "عايز عطر نهاري مش فواح", "مش عايز حاجة نهار ثابتة وفواحة",
                        "عايز عطر نهاري مش ثابت", "ضيف عطر نهاري 90 ملي", "نهار وفواح وثابت؟"):
            with self.subTest(message=message):
                self.assertEqual(_explicit_recommendation_updates(message), {})

    def test_brand_matching_reuses_existing_positive_filter_semantics(self):
        product = Product(store=self.store, brand=self.dior, name="Sauvage")
        self.assertTrue(matches_brand(product, "dior", self.store))
        self.assertFalse(matches_brand(product, STORE_BRAND_EXCLUSIVE, self.store))
        product.brand = self.own
        self.assertTrue(matches_brand(product, STORE_BRAND_EXCLUSIVE, self.store))

    def test_brand_only_words_must_name_a_whole_house(self):
        self.assertIsNone(strictness_update("notdior only", "Dior", self.store))

    def test_no_conversation_still_obeys_explicit_strictness(self):
        result = merge_preferences(None, {"brand": "Dior"}, "Dior only", store=self.store)
        self.assertIs(result["brand_strict"], True)
