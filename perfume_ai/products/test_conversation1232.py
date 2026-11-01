"""Changed goals must replace stale recommendations, without losing real follow-ups."""
import json
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Message, Product, ProductVariant, Store
from products.services.ai.intent import extract_intent
from products.services.ai.recommendation import recommend
from products.services.conversation_service import build_llm_history, merge_preferences
from products.services.product_formatting import format_products
from products.services.router import route
from products.services.sales import ranking, reference, request_updates, similarity
from products.services.search_service import search_products


SIMILAR = "طيب عندك عطر نفس نوتات تيري هيرمز او قريب منه"
OWNED = "عايز حاجه شبهو عشان عندي منو"
MOSQUE = "فريش وخفيف للمسجد"
CASUAL = "وللمسجد ريحه نظافه كده ومريحه والخروجات الخفيفه"


class Conversation1232Tests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Perfamix")
        self.brand = Brand.objects.create(store=self.store, name="Gucci")
        self.terre = self.product("Terre d'Hermes", "Casual", top_notes="Orange, Grapefruit",
                                  middle_notes="Pepper, Pelargonium", base_notes="Vetiver, Cedar, Patchouli, Benzoin")
        self.guilty = self.product("Guilty Black", "Evening", top_notes="Lavender, Green Coriander",
                                  middle_notes="Green Notes, Orange Blossom, Neroli", base_notes="Cedar, Patchouli")
        self.quiet = self.product("Quiet Day", "Casual", top_notes="Neroli, Green Tea", base_notes="White Musk")
        self.loud = self.product("Loud Day", "Casual", projection="Strong", top_notes="Neroli, Green Tea", base_notes="White Musk")
        self.conversation = Conversation.objects.create(store=self.store, preferences={"gender": "male"})

    def product(self, name, occasion, **fields):
        p = Product.objects.create(store=self.store, brand=self.brand, name=name, occasion=occasion,
                                   **{"gender": "male", "projection": "Moderate", "longevity": "6 hours", **fields})
        ProductVariant.objects.create(product=p, volume=50, price=500)
        return p

    def offer(self, *products, status="sent"):
        Message.objects.create(conversation=self.conversation, role="assistant",
                               content="، ".join(p.name for p in products),
                               internal_context=format_products(products), delivery_status=status)

    def user(self, text):
        Message.objects.create(conversation=self.conversation, role="user", content=text)

    def merge(self, intent, message):
        return merge_preferences(self.conversation, intent, message,
                                 history=build_llm_history(self.conversation), store=self.store)

    def test_exact_similarity_recovers_target_without_model_field(self):
        result = self.merge({}, SIMILAR)
        self.assertEqual(result["similar_to"], self.terre.name)
        self.assertEqual(search_products(result, self.store)["reference_product"], self.terre)

    def test_colloquial_reference_uses_latest_identity_not_older_offer(self):
        self.offer(self.guilty)
        self.user(SIMILAR)
        self.offer(self.terre)
        for candidate in (None, self.guilty.name, "Terre d'Hermès"):
            with self.subTest(candidate=candidate):
                result = self.merge({"similar_to": candidate}, OWNED)
                self.assertEqual(result["similar_to"], self.terre.name)
        self.assertFalse(reference._source_supports(OWNED, self.guilty.name, self.store,
                                                   build_llm_history(self.conversation)))

    def test_owned_reference_survives_assistant_offering_multiple_alternatives(self):
        self.user(SIMILAR)
        self.offer(self.guilty, self.quiet)
        self.assertEqual(self.merge({}, OWNED)["similar_to"], self.terre.name)

    def test_ambiguous_latest_offer_does_not_fall_back_to_older_single_offer(self):
        self.offer(self.terre)
        self.offer(self.guilty, self.quiet)
        result = self.merge({"similar_to": self.terre.name}, "عايز حاجة شبهه")
        self.assertNotIn("similar_to", result)
        self.assertEqual(set(result["similarity_candidates"]), {self.guilty.name, self.quiet.name})

    def test_failed_offer_cannot_ground_a_reference(self):
        self.offer(self.terre, status="failed")
        result = self.merge({"similar_to": self.terre.name}, OWNED)
        self.assertTrue(result["similarity_needs_reference"])
        self.assertNotIn("similar_to", result)

    def test_new_reference_drops_old_positive_axes_but_keeps_constraints(self):
        self.conversation.preferences = {"gender": "male", "max_price": 800, "notes": ["fresh"],
                                         "occasion": "mosque", "projection": "moderate", "longevity": "eternal",
                                         "brand": "Gucci", "brand_strict": True, "avoid_notes": ["vanilla"]}
        self.conversation.save()
        result = self.merge({"notes": ["fresh"], "occasion": "mosque", "projection": "moderate"}, SIMILAR)
        for key in ("notes", "occasion", "projection", "longevity"):
            self.assertNotIn(key, result)
        for key in ("gender", "max_price", "avoid_notes", "brand"):
            self.assertIn(key, result)
        self.assertTrue(result["brand_strict"])

    def test_new_purpose_clears_reference_and_stale_extraction(self):
        self.merge({}, SIMILAR)
        result = self.merge({"similar_to": self.terre.name, "similar_to_notes": ["vetiver"],
                             "notes": ["fresh"], "occasion": "multiple", "projection": "strong"}, CASUAL)
        self.assertNotIn("similar_to", result)
        self.assertNotIn(reference.STATE_KEY, self.conversation.sales_state)
        self.assertEqual(result["notes"], ["clean"])
        self.assertEqual(result["occasion"], "casual")
        self.assertEqual(result["projection"], "moderate")

    def test_budget_and_explicit_connected_use_refine_the_reference(self):
        self.user(SIMILAR)
        self.merge({}, SIMILAR)
        result = self.merge({"max_price": 800}, "800 جنيه")
        self.assertEqual(result["similar_to"], self.terre.name)
        result = self.merge({}, "نفس الطلب بس للمسجد ويكون مريح")
        self.assertEqual(result["recommendation_mode"], "refinement")
        self.assertEqual(result["similar_to"], self.terre.name)

    def test_mosque_is_not_inferred_as_daytime_and_multiple_is_not_saved(self):
        result = self.merge({"occasion": "daytime"}, MOSQUE)
        self.assertEqual(result["occasion"], "mosque")
        self.conversation.preferences = {"occasion": "multiple"}
        self.conversation.save()
        self.merge({"occasion": "multiple"}, "تمام")
        self.assertNotIn("occasion", self.conversation.preferences)

    def test_explicit_night_outings_take_precedence_over_generic_casual_use(self):
        result = self.merge({"occasion": "evening"}, "عايز برفان ثابت للخروجات بالليل بس مش تقيل")
        self.assertEqual(result["occasion"], "evening")

    def test_explicit_comparison_connected_to_mosque_keeps_both_requirements(self):
        self.user(SIMILAR)
        self.offer(self.terre)
        result = self.merge({}, "عايز حاجة شبهه وينفع للمسجد ويكون مريح")
        self.assertEqual(result["recommendation_mode"], "refinement")
        self.assertEqual(result["similar_to"], self.terre.name)
        self.assertEqual(result["occasion"], "mosque")

    def test_model_omission_or_malformed_json_does_not_lose_purpose(self):
        for payload in ("{}", "invalid JSON", json.dumps({"occasion": "multiple", "projection": "strong"})):
            with self.subTest(payload=payload), patch("products.services.ai.intent.chat", return_value=payload):
                result = extract_intent(CASUAL, store=self.store)
            self.assertEqual(result["occasion"], "casual")
            self.assertEqual(result["projection"], "moderate")
            self.assertEqual(result["notes"], ["clean"])

    def test_current_fit_wins_without_brand_despite_earlier_offers(self):
        intent = self.merge({}, CASUAL)
        result = search_products(intent, self.store, keep={self.guilty.name, self.loud.name},
                                 offered={self.quiet.name})
        self.assertEqual(result["products"][0], self.quiet)
        self.assertIn(self.guilty.name, result["dropped"])
        self.assertIn(self.loud.name, result["dropped"])

    def test_subdued_projection_does_not_reward_stronger_and_unknown_is_not_conflict(self):
        self.assertEqual(ranking._ordinal_hit("Strong", "moderate", ranking._WANTED_PROJECTION,
                                             similarity._PROJECTION_WORDS), 0)
        self.assertFalse(ranking.excessive_projection("", "moderate"))
        self.assertEqual(ranking._occasion_verdict("", "casual")[0], ranking.UNKNOWN)
        self.assertEqual(ranking._ordinal_hit("12 hours", "moderate", ranking._WANTED_LONGEVITY,
                                             similarity._LONGEVITY_WORDS), 1)

    def test_unavailable_and_other_store_products_cannot_lead(self):
        self.quiet.is_active = False
        self.quiet.save()
        other = Store.objects.create(name="Other")
        brand = Brand.objects.create(store=other, name="Other")
        p = Product.objects.create(store=other, brand=brand, name="Other Quiet", occasion="Casual")
        ProductVariant.objects.create(product=p, volume=50, price=100)
        result = search_products(self.merge({}, CASUAL), self.store)
        self.assertNotIn(self.quiet, result["products"])
        self.assertNotIn(p, result["products"])

    def test_ambiguous_reference_routes_to_clarification(self):
        self.offer(self.guilty, self.quiet)
        with patch("products.services.router.classify", return_value="order"), patch(
            "products.services.router.extract_intent", return_value={},
        ), patch("products.services.router.handle_order") as order:
            reply, context = route("عايز حاجة شبهه", build_llm_history(self.conversation), self.store, self.conversation)
        self.assertIn(self.guilty.name, reply)
        self.assertIn(self.quiet.name, reply)
        self.assertEqual(context, "SIMILARITY_NEEDS_REFERENCE")
        order.assert_not_called()

    def test_exact_messages_override_misleading_classifier_and_repetition_redirect(self):
        for message in (MOSQUE, SIMILAR, OWNED, CASUAL):
            for classification in ("product_info", "faq", "order", "recommendation"):
                with self.subTest(message=message, classification=classification):
                    self.user(SIMILAR)
                    self.offer(self.terre)
                    with patch("products.services.router.classify", return_value=classification), patch(
                        "products.services.router.extract_intent", return_value={},
                    ), patch("products.services.router._detect_semantic_repetition", return_value=3), patch(
                        "products.services.router.recommend", return_value=("Quiet Day فيه مسك.", ""),
                    ) as rec, patch("products.services.router.handle_order") as order, patch(
                        "products.services.router.handle_general",
                    ) as general:
                        route(message, build_llm_history(self.conversation), self.store, self.conversation)
                    rec.assert_called_once()
                    order.assert_not_called()
                    general.assert_not_called()

    def test_names_orders_negation_and_daily_pointer_keep_normal_handling(self):
        for message in ("عايز Guilty Black", "هل Guilty Black مناسب للنهار؟", "ضيف Quiet Day 50 ملي",
                        "هات العطر اليومي 90 ملي", "اعطيني 2 من العطر اليومي", "واعطيني العطر اليومي",
                        "هل Guilty Black شبه Terre d'Hermes؟", "Guilty Black شبه Terre d'Hermes؟",
                        "عندي عطر شبه Terre d'Hermes", "مش عايز حاجة شبهه", "مش عايز عطر يومي"):
            with self.subTest(message=message):
                self.assertEqual(request_updates.request_mode(message, store=self.store), "")

    def test_both_reply_branches_reject_stale_leader_and_unsupported_claims(self):
        intent = self.merge({}, CASUAL)
        result = search_products(intent, self.store)
        for alternatives in (False, True):
            with self.subTest(alternatives=alternatives), patch(
                "products.services.ai.recommendation.chat",
                return_value="Guilty Black مناسب للمسجد ونفس ريحة Terre d'Hermes.",
            ) as chat:
                reply, _ = recommend(CASUAL, Product.objects.none() if alternatives else result["products"],
                                     alternatives=result["products"] if alternatives else None,
                                     store=self.store, intent=intent, search=result)
            self.assertEqual(chat.call_count, 2)
            self.assertIn(self.quiet.name, reply)
            self.assertNotIn("نفس ريحة", reply)
            self.assertNotIn("مفيش", reply)

    def test_factual_occasion_guard_applies_to_mosque_and_multi_product_prose(self):
        from products.services.sales.recommendation_response import response_issues
        reply = "Guilty Black مناسب للنهار والمسجد، أما Quiet Day فثباته أطول."
        self.assertTrue(response_issues(reply, [self.guilty, self.quiet], {"occasion": "mosque"}))
        self.assertFalse(response_issues("Guilty Black مناسب للسهرات، أما Quiet Day مناسب للنهار.",
                                         [self.guilty, self.quiet], {"occasion": "mosque"}))

    def test_history_echo_cannot_create_a_new_own_brand_preference(self):
        result = self.merge({"brand": "STORE_BRAND_EXCLUSIVE"}, MOSQUE)
        self.assertNotIn("brand", result)
        self.conversation.preferences = {"brand": "Gucci", "brand_strict": True}
        self.conversation.save()
        result = self.merge({}, MOSQUE)
        self.assertEqual(result["brand"], "Gucci")
        self.assertTrue(result["brand_strict"])

    def test_no_brand_reply_cannot_invent_differences_in_equal_performance(self):
        from products.services.sales.recommendation_response import response_issues
        intent = {"recommendation_mode": "new_goal"}
        for claim in ("Quiet Day أهدى شوية من Guilty Black.", "Quiet Day ثابت أكتر من Guilty Black."):
            with self.subTest(claim=claim):
                self.assertTrue(response_issues(claim, [self.quiet, self.guilty], intent))
        self.assertFalse(response_issues("Quiet Day فيه مسك. Guilty Black فيه باتشولي.",
                                         [self.quiet, self.guilty], intent))

    def test_factual_recovery_cannot_offer_the_reference_itself(self):
        from products.services.router import _rephrased
        intent = {"similar_to": self.terre.name, "recommendation_mode": "new_goal"}
        bad = f"{self.quiet.name} ثابت 99 ساعة. {self.terre.name} ثابت 99 ساعة."
        context = format_products([self.quiet, self.terre])
        reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context),
                                recommendation_intent=intent, recommendation_candidates=[self.quiet])
        self.assertIn(self.quiet.name, reply)
        self.assertNotIn(self.terre.name, reply)

    def test_reference_as_a_standalone_alternative_is_rejected(self):
        from products.services.sales.recommendation_response import response_issues
        reply = f"{self.quiet.name}: فوحانه متوسط.\n{self.terre.name}: فوحانه متوسط."
        self.assertTrue(response_issues(reply, [self.quiet], {"recommendation_mode": "new_goal"},
                                       {"reference_product": self.terre}))
