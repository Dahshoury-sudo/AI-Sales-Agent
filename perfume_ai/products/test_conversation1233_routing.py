"""Exercise the affected turns through routing, search, and response instructions."""
import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Cart, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services.ai.recommendation import recommend
from products.services.conversation_service import build_llm_history, save_message
from products.services.product_formatting import format_products
from products.services.router import route
from products.services.search_service import search_products


SIMILARITY_MESSAGE = "طيب عندك عطر نفس نوتات تيري هيرمز او قريب منه"
MORNING_MESSAGE = "طيب ايه ترشحلي نهار حاجه حلوه وفواحه وثابته"
DAILY_MESSAGE = "واعطيني العطر اليومي"


class Conversation1233RoutingTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Routing test")
        StoreSettings.objects.create(store=self.store)
        self.brand = Brand.objects.create(store=self.store, name=self.store.name)
        self.terre = self.product(
            "Terre d'Hermes", "Daily", top_notes="Orange, Grapefruit",
            middle_notes="Pepper, Pelargonium", base_notes="Vetiver, Cedar, Patchouli, Benzoin",
        )
        self.bonded = self.product("Bonded", "Office, Daily", projection="Moderate", base_notes="Musk, Cedar")
        self.showmix = self.product("Showmix", "Casual", projection="Moderate", base_notes="Musk")
        self.elite = self.product("Elite", "Evening", scent_tags=["heavy"], base_notes="Amber, Sandalwood")
        self.dark = self.product("Dark Aura", "Evening", scent_tags=["sweet", "heavy"], base_notes="Vanilla, Leather")
        self.conversation = Conversation.objects.create(store=self.store, preferences={"gender": "male"})

    def product(self, name, occasion, **fields):
        attrs = {"gender": "male", "longevity": "8 hours", "projection": "Strong", "scent_tags": ["fresh", "woody"], **fields}
        product = Product.objects.create(store=self.store, brand=self.brand, name=name, occasion=occasion, **attrs)
        ProductVariant.objects.create(product=product, volume=50, price=750)
        return product

    def offer(self, *products):
        save_message(self.conversation, "assistant", "، ".join(p.name for p in products),
                     internal_context=format_products(products))

    def test_exact_similarity_request_keeps_reference_through_router(self):
        self.offer(self.elite, self.dark)
        answer = json.dumps({"product_ids": [self.bonded.pk], "reply": "Bonded ريحته فريش ومنعش وخشبي."})
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"similar_to": self.terre.name}), \
             patch("products.services.ai.recommendation.chat", return_value=answer) as chat, \
             patch("products.services.router.confident_catalogue_match", return_value=self.terre) as matcher:
            reply, _ = route(SIMILARITY_MESSAGE, build_llm_history(self.conversation), self.store, self.conversation)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences["similar_to"], self.terre.name)
        payload = json.loads(chat.call_args_list[0].args[0][-1]["content"])
        self.assertEqual(payload["reference"], {"name": self.terre.name, "scent_tags": ["fresh", "woody"]})
        candidates = {p["id"] for p in payload["eligible_products"]}
        self.assertIn(self.bonded.pk, candidates)
        self.assertNotIn(self.terre.pk, candidates)
        self.assertNotIn(self.elite.pk, candidates)
        self.assertIn(self.bonded.name, reply)
        matcher.assert_not_called()

    def test_morning_request_asks_style_then_matches_all_supplied_requirements(self):
        self.offer(self.bonded, self.showmix)
        self.offer(self.elite, self.dark)
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"occasion": "daytime", "projection": "strong", "longevity": "long-lasting"}), \
             patch("products.services.ai.recommendation.chat") as chat:
            reply, context = route(MORNING_MESSAGE, build_llm_history(self.conversation), self.store, self.conversation)
        chat.assert_not_called()
        self.assertEqual(context, "SCENT_PREFERENCE_REQUIRED")
        save_message(self.conversation, "assistant", reply, internal_context=context)
        answer = json.dumps({"product_ids": [self.terre.pk], "reply": "Terre d'Hermes ريحته فريش ومنعش وخشبي."})
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"scent_tags": ["fresh"]}), \
             patch("products.services.ai.recommendation.chat", return_value=answer) as chat:
            route("فريش", build_llm_history(self.conversation), self.store, self.conversation)
        payload = json.loads(chat.call_args_list[0].args[0][-1]["content"])
        self.assertEqual({p["id"] for p in payload["eligible_products"]}, {self.terre.pk, self.bonded.pk, self.showmix.pk})
        self.assertEqual(payload["preferences"]["occasion"], "Daily & Casual")
        self.assertNotIn("projection", payload["preferences"])

    def test_daily_pointer_overrides_recommendation_and_order_even_with_cart(self):
        self.offer(self.bonded, self.showmix)
        for _ in range(5):
            save_message(self.conversation, "user", "تمام")
            save_message(self.conversation, "assistant", "حاضر.")
        self.offer(self.elite, self.dark)
        Cart.objects.create(conversation=self.conversation)
        history = build_llm_history(self.conversation)
        self.assertFalse(any(self.bonded.name in m["content"] for m in history))
        for classification in ("recommendation", "order", "product_info", "faq"):
            with self.subTest(classification=classification), patch(
                "products.services.router.classify", return_value=classification,
            ), patch("products.services.router.handle_order") as order, patch(
                "products.services.router.extract_intent",
            ) as extract, patch("products.services.product_info.resolve_products") as resolver:
                reply, context = route(DAILY_MESSAGE, history, self.store, self.conversation)
            self.assertIn("Bonded، Showmix", reply)
            self.assertIn("تقصد أنهي", reply)
            self.assertIn("OCCASION_REFERENCE_UNRESOLVED", context)
            self.assertNotIn(self.elite.name, reply)
            order.assert_not_called()
            extract.assert_not_called()
            resolver.assert_not_called()

    def test_unique_daily_pointer_reaches_response_model_with_only_daily_product(self):
        self.offer(self.bonded)
        self.offer(self.elite, self.dark)
        with patch("products.services.router.classify", return_value="order"), patch(
            "products.services.product_info.chat", return_value="العطر اللي تقصده هو Bonded.",
        ) as chat, patch("products.services.router.handle_order") as order:
            reply, context = route(DAILY_MESSAGE, build_llm_history(self.conversation), self.store, self.conversation)
        self.assertIn(self.bonded.name, reply)
        self.assertNotIn(self.elite.name, context)
        self.assertIn("للاستخدام اليومي والكاجوال: Bonded", chat.call_args.args[0][-1]["content"])
        order.assert_not_called()

    def test_product_size_and_quantity_orders_stay_orders(self):
        for message in ("اعطيني Elite", "اعطيني العطر اليومي 90 ملي", "اعطيني 2 من العطر اليومي"):
            with self.subTest(message=message), patch("products.services.router.classify", return_value="order"), patch(
                "products.services.router.handle_order", return_value=("طلب", ""),
            ) as order, patch("products.services.router.get_product_info") as info:
                route(message, [], self.store, self.conversation)
            order.assert_called_once()
            info.assert_not_called()

    def test_new_request_and_named_question_keep_their_routes(self):
        for message, classification, target in (
            ("عايز عطر فريش يومي", "recommendation", "recommend"),
            ("مش عايز العطر اليومي رشحلي حاجة سويت للسهرة", "recommendation", "recommend"),
            ("هل Elite ينفع يومي؟", "product_info", "get_product_info"),
        ):
            with self.subTest(message=message), patch("products.services.router.classify", return_value=classification), patch(
                "products.services.router.extract_intent", return_value={"occasion": "daily"},
            ), patch(f"products.services.router.{target}", return_value=("حاضر.", "")) as handler:
                route(message, [], self.store, self.conversation)
            handler.assert_called_once()

    def test_reference_selection_uses_a_single_eligible_path(self):
        intent = {"gender": "male", "occasion": "daily", "similar_to": self.terre.name}
        result = search_products(intent, self.store)
        self.assertIsNone(result["alternatives"])
        explanation = "Bonded ريحته فريش ومنعش وخشبي."
        answer = json.dumps({"product_ids": [self.bonded.pk], "reply": explanation})
        with patch("products.services.ai.recommendation.chat", return_value=answer) as chat:
            reply, _ = recommend(SIMILARITY_MESSAGE, result["products"], store=self.store, intent=intent, search=result)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(reply, explanation)

    def test_repeated_similarity_overclaim_falls_back_to_catalogue_facts(self):
        intent = {"occasion": "daily", "similar_to": self.terre.name}
        result = search_products(intent, self.store)
        invalid = json.dumps({"product_ids": [self.bonded.pk], "reply": "Bonded أقرب لطابع Terre d'Hermes."})
        with patch("products.services.ai.recommendation.chat", return_value=invalid) as chat:
            reply, _ = recommend(SIMILARITY_MESSAGE, result["products"], store=self.store, intent=intent, search=result)
        self.assertEqual(chat.call_count, 2)
        self.assertIn(self.bonded.name, reply)
        self.assertNotIn("أقرب لطابع", reply)
        self.assertNotIn("مفيش", reply)

    def test_no_match_declaration_is_rewritten_as_a_direct_alternative(self):
        intent = {"similar_to": self.terre.name}
        results = search_products(intent, self.store)
        corrected = "Bonded ريحته فريش ومنعش وخشبي، وفوحانه قوي."
        with patch("products.services.ai.recommendation.chat", side_effect=[json.dumps({"product_ids": [], "reply": "مفيش عطر قريب منه."}), json.dumps({"product_ids": [self.bonded.pk], "reply": corrected})]) as chat:
            reply, _ = recommend(SIMILARITY_MESSAGE, results["products"], store=self.store, intent=intent, search=results)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(reply, corrected)


class Conversation1233EvaluationTests(SimpleTestCase):
    def test_direct_alternatives_need_no_no_match_declaration(self):
        from eval_harness.checks import claims_unsupported_similarity
        for reply in ("أرشحلك Bonded فيه مسك وخشب الأرز.", "كبديل جرب Bonded بطابعه الخشبي.",
                      "Bonded مش نفس ريحة تيري هيرمز.", "مش موجود عندنا حاجة شبهه بالظبط.",
                      "فيهم باتشولي زي Terre d'Hermes."):
            with self.subTest(reply=reply):
                self.assertFalse(claims_unsupported_similarity(reply))

    def test_similarity_claim_cannot_hide_behind_unrelated_disclaimer(self):
        from eval_harness.checks import claims_unsupported_similarity
        for reply in ("Elite نفس الريحة.", "Elite شبه تيري هيرمز.", "Elite نفس نوتات تيري هيرمز.",
                      "مفيش 90 ملي، بس Elite نفس الريحة.", "Elite شبهه. Bonded مختلف.",
                      "Elite ريحته زي تيري هيرمز.", "Dark Aura أقرب لطابع Terre d'Hermes."):
            with self.subTest(reply=reply):
                self.assertTrue(claims_unsupported_similarity(reply))
