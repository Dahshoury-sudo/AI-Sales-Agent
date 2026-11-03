"""Conversation 1233: an explicit Arabic lookalike target must survive validation."""

from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store
from products.services.conversation_service import merge_preferences
from products.services.sales.reference import STATE_KEY, similarity_requested
from products.services.search_service import search_products


class Conversation1233SimilarityTests(TestCase):
    def setUp(self):
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))
        self.store = Store.objects.create(name="Reference evidence test")
        self.hermes = Brand.objects.create(store=self.store, name="Hermes")
        self.terre = Product.objects.create(
            store=self.store, brand=self.hermes, name="Terre d'Hermes", gender="male",
            top_notes="Orange, Grapefruit", middle_notes="Pepper, Pelargonium",
            base_notes="Vetiver, Cedar, Patchouli, Benzoin",
        )
        self.elite = Product.objects.create(
            store=self.store, brand=self.hermes, name="Elite", gender="male",
            top_notes="Grapefruit, Mint", base_notes="Amber, Sandalwood",
        )
        for product in (self.terre, self.elite):
            ProductVariant.objects.create(product=product, volume=50, price=750)
        self.conversation = Conversation.objects.create(store=self.store)

    def test_customer_spelling_retains_catalogue_reference_and_its_real_notes(self):
        message = "طيب عندك عطر نفس نوتات تيري هيرمز او قريب منه"
        result = merge_preferences(self.conversation, {
            "similar_to": self.terre.name,
            "similar_to_notes": ["vanilla"],
        }, message)
        self.assertEqual(result.get("similar_to"), self.terre.name)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sales_state[STATE_KEY]["source_text"], message)
        search = search_products(result, self.store)
        self.assertEqual(search["reference_product"], self.terre)
        self.assertIsNotNone(search["similarity"])
        self.assertNotIn(self.terre, search["products"])

    def test_same_notes_alone_is_a_similarity_cue(self):
        for message in ("عايز نفس نوتات تيري هيرمز", "عايز نفس النوتات بتاعة Terre d'Hermes", "same notes as Terre d'Hermes"):
            with self.subTest(message=message):
                self.assertTrue(similarity_requested(message))
                result = merge_preferences(self.conversation, {"similar_to": self.terre.name}, message)
                self.assertEqual(result.get("similar_to"), self.terre.name)

    def test_named_target_cannot_be_replaced_by_previous_suggestion(self):
        result = merge_preferences(self.conversation, {"similar_to": self.elite.name},
                                   "طيب عندك عطر نفس نوتات تيري هيرمز او قريب منه")
        self.assertEqual(result["similar_to"], self.terre.name)

    def test_house_or_different_flanker_is_not_evidence_for_base_perfume(self):
        for message in ("عايز نفس نوتات هيرمز", "عايز نفس نوتات تيري هيرمز انتنس", "عايز نفس نوتات الترا ميل"):
            with self.subTest(message=message):
                result = merge_preferences(self.conversation, {"similar_to": self.terre.name}, message)
                self.assertNotIn("similar_to", result)

    def test_negated_same_notes_request_does_not_establish_reference(self):
        message = "مش عايز نفس نوتات تيري هيرمز"
        self.assertFalse(similarity_requested(message))
        result = merge_preferences(self.conversation, {"similar_to": self.terre.name}, message)
        self.assertNotIn("similar_to", result)

    def test_base_spelling_does_not_validate_a_different_extracted_edition(self):
        flanker = Product.objects.create(store=self.store, brand=self.hermes, name="Terre d'Hermes Eau Intense Vetiver")
        result = merge_preferences(self.conversation, {"similar_to": flanker.name},
                                   "عايز نفس نوتات تيري هيرمز")
        self.assertEqual(result["similar_to"], self.terre.name)

    def test_base_spelling_cannot_select_between_only_flankers(self):
        self.terre.delete()
        for edition in ("Eau Intense Vetiver", "Eau Givree"):
            name = "Terre d'Hermes " + edition
            Product.objects.create(store=self.store, brand=self.hermes, name=name)
            result = merge_preferences(self.conversation, {"similar_to": name}, "عايز نفس نوتات تيري هيرمز")
            self.assertNotIn("similar_to", result)

    def test_named_edition_does_not_validate_the_base_reference(self):
        Product.objects.create(store=self.store, brand=self.hermes, name="Terre d'Hermes Eau Intense Vetiver")
        for message in ("same notes as Terre d'Hermes Eau Intense Vetiver",
                        "عايز نفس نوتات تيري هيرمز انتنس او قريب منه"):
            with self.subTest(message=message):
                result = merge_preferences(self.conversation, {"similar_to": self.terre.name}, message)
                self.assertNotEqual(result.get("similar_to"), self.terre.name)

    def test_negated_comparison_with_or_does_not_reintroduce_reference(self):
        message = "مش عايز نفس نوتات تيري هيرمز او قريب منه"
        self.assertFalse(similarity_requested(message))
        result = merge_preferences(self.conversation, {"similar_to": self.terre.name}, message)
        self.assertNotIn("similar_to", result)
