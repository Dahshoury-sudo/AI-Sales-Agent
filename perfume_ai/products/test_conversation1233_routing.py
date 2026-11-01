"""Exercise the affected turns through routing, search, and response instructions."""
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
        self.bonded = self.product("Bonded", "Office", projection="Moderate", base_notes="Musk, Cedar")
        self.showmix = self.product("Showmix", "Casual", projection="Moderate", base_notes="Musk")
        self.elite = self.product("Elite", "Evening", base_notes="Amber, Sandalwood")
        self.dark = self.product("Dark Aura", "Evening", base_notes="Vanilla, Leather")
        self.conversation = Conversation.objects.create(store=self.store, preferences={"gender": "male"})

    def product(self, name, occasion, **fields):
        attrs = {"gender": "male", "longevity": "8 hours", "projection": "Strong", **fields}
        product = Product.objects.create(store=self.store, brand=self.brand, name=name, occasion=occasion, **attrs)
        ProductVariant.objects.create(product=product, volume=50, price=750)
        return product

    def offer(self, *products):
        save_message(self.conversation, "assistant", "، ".join(p.name for p in products),
                     internal_context=format_products(products))

    def test_exact_similarity_request_keeps_reference_through_router(self):
        self.offer(self.elite, self.dark)
        with patch("products.services.router.classify", return_value="recommendation"), patch(
            "products.services.router.extract_intent",
            return_value={"similar_to": self.terre.name, "similar_to_notes": ["vanilla"]},
        ), patch("products.services.ai.recommendation.chat", return_value="Bonded فيه مسك وخشب الأرز.") as chat, patch(
            "products.services.router.confident_catalogue_match", return_value=self.terre,
        ) as matcher, patch("products.services.router.search_products", wraps=search_products) as search:
            route(SIMILARITY_MESSAGE, build_llm_history(self.conversation), self.store, self.conversation)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences["similar_to"], self.terre.name)
        self.assertEqual(search.call_args.args[0]["similar_to"], self.terre.name)
        prompt = chat.call_args.args[0][-1]["content"]
        self.assertIn("Orange, Grapefruit", prompt)
        self.assertIn("Vetiver, Cedar, Patchouli, Benzoin", prompt)
        self.assertIn("ابدأ مباشرة بترشيح أفضل عطر أو عطرين", prompt)
        self.assertNotIn("مفيش حاجة قريبة", prompt)
        self.assertNotIn("الكلام دلوقتي على: Elite", prompt)
        matcher.assert_not_called()

    def test_exact_morning_request_reassesses_evening_offers(self):
        self.offer(self.bonded, self.showmix)
        self.offer(self.elite, self.dark)
        with patch("products.services.router.classify", return_value="recommendation"), patch(
            "products.services.router.extract_intent",
            return_value={"occasion": "daytime", "projection": "strong", "longevity": "long-lasting"},
        ), patch("products.services.ai.recommendation.chat", return_value="Bonded مناسب للنهار وفوحانه متوسط.") as chat, patch(
            "products.services.router.search_products", wraps=search_products,
        ) as search:
            route(MORNING_MESSAGE, build_llm_history(self.conversation), self.store, self.conversation)
        intent = search.call_args.args[0]
        self.assertEqual(intent["occasion"], "daytime")
        prompt = chat.call_args_list[0].args[0][-1]["content"]
        self.assertLess(prompt.index("Name (الاسم الصحيح): Bonded"), prompt.index("Name (الاسم الصحيح): Elite"))
        self.assertIn("قدّم المناسب للنهار حتى لو فوحانه أهدى", prompt)
        self.assertIn("فوحانه أقل", prompt)
        self.assertNotIn("الكلام دلوقتي على: Elite", prompt)

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
            "products.services.product_info.chat", return_value="Bonded مناسب للنهار.",
        ) as chat, patch("products.services.router.handle_order") as order:
            reply, context = route(DAILY_MESSAGE, build_llm_history(self.conversation), self.store, self.conversation)
        self.assertIn(self.bonded.name, reply)
        self.assertNotIn(self.elite.name, context)
        self.assertIn("للنهار: Bonded", chat.call_args.args[0][-1]["content"])
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
            ("عايز عطر يومي", "recommendation", "recommend"),
            ("مش عايز العطر اليومي رشحلي حاجة للسهرة", "recommendation", "recommend"),
            ("هل Elite ينفع يومي؟", "product_info", "get_product_info"),
        ):
            with self.subTest(message=message), patch("products.services.router.classify", return_value=classification), patch(
                "products.services.router.extract_intent", return_value={"occasion": "daily"},
            ), patch(f"products.services.router.{target}", return_value=("حاضر.", "")) as handler:
                route(message, [], self.store, self.conversation)
            handler.assert_called_once()

    def test_both_recommendation_branches_offer_direct_grounded_alternatives(self):
        intent = {"gender": "male", "occasion": "daily", "similar_to": self.terre.name}
        results = search_products(intent, self.store)
        self.assertFalse(results["similarity"]["has_close_match"])
        for alternative_branch in (False, True):
            with self.subTest(alternative_branch=alternative_branch), patch(
                "products.services.ai.recommendation.chat", return_value="Bonded فيه مسك وخشب الأرز.",
            ) as chat:
                recommend(SIMILARITY_MESSAGE, Product.objects.none() if alternative_branch else results["products"],
                          alternatives=results["products"] if alternative_branch else None,
                          store=self.store, intent=intent, search=results)
            prompt = chat.call_args.args[0][-1]["content"]
            self.assertIn("ابدأ مباشرة بترشيح أفضل عطر أو عطرين", prompt)
            self.assertIn("ممنوع تقول عن أي عطر إنه شبهه أو نفس ريحته", prompt)
            self.assertIn("قدّم المناسب للنهار حتى لو فوحانه أهدى", prompt)
            self.assertNotIn("مفيش حاجة قريبة", prompt)
            self.assertNotIn("بل لازم", prompt)

    def test_repeated_similarity_overclaim_falls_back_to_catalogue_facts_in_both_branches(self):
        intent = {"gender": "male", "occasion": "daily", "similar_to": self.terre.name}
        results = search_products(intent, self.store)
        for alternative_branch in (False, True):
            with self.subTest(alternative_branch=alternative_branch), patch(
                "products.services.ai.recommendation.chat", return_value="Bonded أقرب لطابع Terre d'Hermes.",
            ) as chat:
                reply, _ = recommend(
                    SIMILARITY_MESSAGE, Product.objects.none() if alternative_branch else results["products"],
                    alternatives=results["products"] if alternative_branch else None,
                    store=self.store, intent=intent, search=results,
                )
            self.assertEqual(chat.call_count, 2)
            self.assertIn("Bonded", reply)
            self.assertNotIn("أقرب لطابع", reply)
            self.assertNotIn("مفيش", reply)

    def test_no_match_declaration_is_rewritten_as_a_direct_alternative(self):
        intent = {"similar_to": self.terre.name}
        results = search_products(intent, self.store)
        corrected = "Bonded فيه مسك وخشب الأرز، وفوحانه متوسط."
        with patch("products.services.ai.recommendation.chat", side_effect=["مفيش عطر قريب منه.", corrected]) as chat:
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
