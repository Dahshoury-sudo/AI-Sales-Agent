"""A customer gender choice precedes recommendation questions and selection."""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.occasions import ROMANTIC, WORK
from products.services.ai.intent import extract_intent
from products.services.conversation_service import merge_preferences, save_message
from products.services.router import route
from products.services.sales import gender


class GenderChoiceTests(SimpleTestCase):
    def test_explicit_choices_and_recipient_words_are_bounded(self):
        for message, expected in (
            ("رجالي", "male"), ("حريمي", "female"), ("female", "female"), ("male", "male"),
            ("women's perfume", "female"), ("men's fragrance", "male"), ("للجنسين", "unisex"),
            ("for my wife", "female"), ("for my husband", "male"), ("لمراتي", "female"),
            ("عايز عطر لجوزي", "male"), ("لبنتي", "female"), ("unisex for my husband", "unisex"),
            ("not male, female", "female"), ("for my wife but men's perfume", "male"),
        ):
            with self.subTest(message=message):
                self.assertEqual(gender.customer_choice(message), expected)
        for message in ("لنفسي", "هديه", "شبه Le Male", "something like Good Girl", "femalevolent",
                        "عندكم مستحضرات", "بستين جنيه", "رجالي ولا حريمي", "choose for me"):
            with self.subTest(message=message):
                self.assertIsNone(gender.customer_choice(message))

    def test_unisex_in_customer_spellings_and_one_for_both_phrases(self):
        """Conversation 1314 said "يونيسكس" three times and got the binary question three times."""
        for message in ("يونيسكس", "عايز برفان يونيسكس", "منا قولت يونيسكس", "عايز حاجه يونيسكس من زيرجوف",
                        "يونسكس", "يوني سكس", "يونيكس", "uni sex", "gender neutral perfume",
                        "شبه Naxos يونيسكس",
                        # One perfume for both, without the word (conversation 23).
                        "عايز حاجه رجالي وحريمي فنفس الوقت", "رجالي وحريمي في نفس الوقت",
                        "ينفع للراجل والست", "عايز حاجه تنفع للاتنين", "for both men and women"):
            with self.subTest(message=message):
                self.assertEqual(gender.customer_choice(message), "unisex")
        for message, expected in (("عايز رجالي وحريمي", "multiple"), ("عايز عطر رجالي وعطر حريمي", "multiple"),
                                  ("بكام الاتنين", None), ("الاتنين", None), ("90 ملي من الاتنين", None),
                                  ("رجالي ولا حريمي", None)):
            with self.subTest(message=message):
                self.assertEqual(gender.customer_choice(message), expected)

    def test_inferred_or_default_gender_from_the_model_is_discarded(self):
        for message, supplied in (("something like Le Male", "male"), ("choose for me", "unisex"),
                                  ("my name is Sarah, recommend a fresh perfume", "female")):
            with self.subTest(message=message), patch("products.services.ai.intent.chat", return_value=json.dumps({"gender": supplied})):
                self.assertNotIn("gender", extract_intent(message))

    def test_customer_answer_overrides_bad_or_malformed_extraction(self):
        for response in ("{}", '{"gender":"male"}', "invalid JSON"):
            with self.subTest(response=response), patch("products.services.ai.intent.chat", return_value=response):
                self.assertEqual(extract_intent("حريمي")["gender"], "female")

    def test_stateless_history_uses_customer_choice_and_honors_recipient_reset(self):
        history = [{"role": "user", "content": "عايز عطر رجالي"}]
        self.assertEqual(merge_preferences(None, {}, "فريش", history=history)["gender"], "male")
        result = merge_preferences(None, {"reset_preferences": True}, "for a different recipient", history=history)
        self.assertNotIn("gender", result)
        history = [{"role": "user", "content": "something like Le Male"}]
        self.assertNotIn("gender", merge_preferences(None, {}, "fresh", history=history))


class GenderFirstRecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Gender first")
        StoreSettings.objects.create(store=cls.store)
        cls.brand = Brand.objects.create(store=cls.store, name="House")
        cls.products = {}
        for choice, name in (("male", "Le Male"), ("female", "Good Girl"), ("unisex", "Shared Breeze")):
            product = Product.objects.create(store=cls.store, brand=cls.brand, name=name, gender=choice,
                                             scent_tags=["fresh"], occasion=f"{WORK}; {ROMANTIC}")
            ProductVariant.objects.create(product=product, volume=50, price=400)
            cls.products[choice] = product

    def setUp(self):
        self.conversation = Conversation.objects.create(store=self.store)
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))
        self.enterContext(patch("products.services.router.confident_catalogue_match", return_value=None))

    def call(self, message, updates=None, history=None, classification="recommendation"):
        with patch("products.services.router.classify", return_value=classification) as classifier, \
             patch("products.services.router.extract_intent", return_value=updates or {}), \
             patch("products.services.router.recommend", return_value=("اختيار مناسب", "selected")) as recommender:
            result = route(message, history or [], self.store, self.conversation)
        return result, classifier, recommender

    def ask(self, updates=None, message="عايز عطر فريش"):
        result, _, model = self.call(message, updates or {"scent_tags": ["fresh"]})
        self.assertEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
        model.assert_not_called()
        save_message(self.conversation, "assistant", *result)
        return result

    def test_question_is_exact_and_precedes_scent_or_occasion_questions_and_search(self):
        for updates in ({}, {"occasion": "unrecognized"}, {"similarity_needs_reference": True}):
            with self.subTest(updates=updates), patch("products.services.router.search_products") as search:
                result, _, model = self.call("recommend a perfume", updates)
                self.assertEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
                search.assert_not_called()
                model.assert_not_called()

    def test_initial_preferences_survive_until_the_gender_answer(self):
        self.ask({"scent_tags": ["fresh"], "occasion": WORK, "max_price": 800,
                  "excluded_scent_tags": ["oud"]}, "عايز فريش للشغل مش عودي تحت 800")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences["occasion"], WORK)
        self.assertEqual(self.conversation.preferences["max_price"], 800)
        result, classifier, model = self.call("حريمي", {"gender": "male"}, classification="faq")
        classifier.assert_not_called()
        self.assertEqual(result[1], "selected")
        current = model.call_args.kwargs["intent"]
        self.assertEqual(current["gender"], "female")
        self.assertEqual(current["scent_tags"], ["fresh"])
        self.assertEqual(current["excluded_scent_tags"], ["oud"])
        self.assertEqual(current["max_price"], 800)
        self.assertEqual(current["occasion"], WORK)
        self.assertEqual(set(model.call_args.args[1]), {self.products["female"], self.products["unisex"]})
        self.assertEqual(self.conversation.preferences["gender"], "female")

    def test_gender_answer_can_also_refine_occasion_without_losing_scent_or_budget(self):
        self.ask({"scent_tags": ["fresh"], "max_price": 800, "occasion": WORK}, "عايز فريش للشغل")
        result, _, model = self.call("حريمي للديت", {"occasion": ROMANTIC}, classification="order")
        self.assertEqual(result[1], "selected")
        self.assertEqual(model.call_args.kwargs["intent"]["scent_tags"], ["fresh"])
        self.assertEqual(model.call_args.kwargs["intent"]["occasion"], ROMANTIC)
        self.assertEqual(model.call_args.kwargs["intent"]["max_price"], 800)

    def test_current_and_saved_choices_skip_the_question(self):
        for message, expected in (("عايز فريش رجالي", "male"), ("عايز فريش لخطيبتي", "female"), ("fresh unisex", "unisex")):
            with self.subTest(message=message):
                result, _, model = self.call(message, {"scent_tags": ["fresh"]})
                self.assertEqual(result[1], "selected")
                self.assertEqual(model.call_args.kwargs["intent"]["gender"], expected)
        result, _, model = self.call("عايز فريش", {"gender": "male", "scent_tags": ["fresh"]})
        self.assertEqual(result[1], "selected")
        self.assertEqual(model.call_args.kwargs["intent"]["gender"], "unisex")

    def test_reference_names_never_establish_gender(self):
        for choice in ("male", "female"):
            product = self.products[choice]
            with self.subTest(name=product.name):
                result, _, model = self.call(f"recommend something like {product.name}", {"similar_to": product.name, "gender": choice})
                self.assertEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
                model.assert_not_called()
                self.assertNotIn("gender", self.conversation.preferences)

    def test_reference_and_an_explicit_recipient_can_recommend_immediately(self):
        result, _, model = self.call("recommend something like Le Male for my wife", {"similar_to": "Le Male", "gender": "male"})
        self.assertEqual(result[1], "selected")
        self.assertEqual(model.call_args.kwargs["intent"]["gender"], "female")

    def test_unclear_answers_do_not_choose_unisex_or_change_occasion(self):
        self.ask()
        for answer in ("انت اختار", "choose for me", "لنفسي", "مش عارف", "رجالي ولا حريمي"):
            with self.subTest(answer=answer):
                result, _, model = self.call(answer, {"gender": "unisex"}, classification="faq")
                self.assertEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
                model.assert_not_called()
                self.assertNotIn("gender", self.conversation.preferences)
                self.assertNotIn("occasion", self.conversation.preferences)

    def test_explicit_unisex_answer_is_accepted(self):
        self.ask()
        result, _, model = self.call("للجنسين", classification="faq")
        self.assertEqual(result[1], "selected")
        self.assertEqual(model.call_args.kwargs["intent"]["gender"], "unisex")

    def test_unisex_in_the_customers_spelling_never_gets_the_binary_question(self):
        """Replay of conversation 1314."""
        result, _, _ = self.call("عايز برفان يونيسكس", {})
        self.assertNotEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
        self.assertEqual(self.conversation.preferences["gender"], "unisex")

        self.conversation.preferences = {}
        self.conversation.save(update_fields=["preferences"])
        self.ask()
        result, _, model = self.call("منا قولت يونيسكس", classification="faq")
        self.assertEqual(result[1], "selected")
        self.assertEqual(model.call_args.kwargs["intent"]["gender"], "unisex")

    def test_new_recipient_clears_old_gender_and_requires_a_choice(self):
        self.conversation.preferences = {"gender": "male", "scent_tags": ["fresh"]}
        self.conversation.save(update_fields=["preferences"])
        result, _, model = self.call("عايز فريش لشخص تاني", {"reset_preferences": True, "gender": "male", "scent_tags": ["fresh"]})
        self.assertEqual(result, (gender.QUESTION, gender.REQUIRED_MARKER))
        self.assertNotIn("gender", self.conversation.preferences)
        model.assert_not_called()

    def test_two_explicit_genders_ask_which_one_to_start_with(self):
        result, _, model = self.call("عايز عطر رجالي وعطر حريمي", {"scent_tags": ["fresh"], "gender": "multiple"})
        self.assertEqual(result[1], "MULTIPLE_RECIPIENTS")
        model.assert_not_called()
        self.assertNotIn("gender", self.conversation.preferences)

    def test_product_questions_and_orders_keep_their_routes_while_question_is_pending(self):
        self.ask()
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.router.get_product_info", return_value=("متوفر", "info")) as info, \
             patch("products.services.router.extract_intent") as extract:
            result = route("بكام Good Girl؟", [], self.store, self.conversation)
        info.assert_called_once()
        extract.assert_not_called()
        self.assertEqual(result[1], "info")
        with patch("products.services.router.classify", return_value="order"), \
             patch("products.services.router.handle_order", return_value=("طلبك", "order")) as order, \
             patch("products.services.router.extract_intent") as extract:
            route("ضيف Good Girl 50 ملي", [], self.store, self.conversation)
        order.assert_called_once()
        extract.assert_not_called()

    def test_missing_gender_precedes_recommendation_objection_handling(self):
        with patch("products.services.router.sales_objection.detect", return_value=object()), \
             patch("products.services.router.handle_objection") as objection:
            self.ask({"scent_tags": ["fresh"]}, "رشحلي فريش أرخص")
        objection.assert_not_called()


class UnisexNoMatchTests(TestCase):
    """When no unisex perfume fits: say so, then ask رجالي ولا حريمي (owner, 2026-11-20)."""

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Unisex no match")
        StoreSettings.objects.create(store=cls.store)
        cls.brand = Brand.objects.create(store=cls.store, name="House")
        for name, choice, tags, price in (("Shared Breeze", "unisex", ["fresh"], 900),
                                          ("Oud Man", "male", ["oud"], 400),
                                          ("Fresh Man", "male", ["fresh"], 800),
                                          ("Fresh Lady", "female", ["fresh"], 300)):
            product = Product.objects.create(store=cls.store, brand=cls.brand, name=name, gender=choice,
                                             scent_tags=tags, occasion=f"{WORK}; {ROMANTIC}")
            ProductVariant.objects.create(product=product, volume=50, price=price)

    def setUp(self):
        self.conversation = Conversation.objects.create(store=self.store)
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))
        self.enterContext(patch("products.services.router.confident_catalogue_match", return_value=None))

    def search(self, intent):
        from products.services.search_service import search_products
        return search_products(intent, self.store)

    def test_reply_names_the_one_requirement_the_unisex_perfumes_miss(self):
        from products.services.ai.scent_recommendation import no_match_reply
        for intent, blocker, detail in (
            ({"gender": "unisex", "scent_tags": ["oud"]}, "scent_tags", "بطابع عودي"),
            ({"gender": "unisex", "scent_tags": ["fresh"], "max_price": 500}, "max_price", "في الميزانية دي"),
            # Neither alone unblocks it, so neither is named.
            ({"gender": "unisex", "scent_tags": ["oud"], "max_price": 500}, None, "بالمواصفات دي"),
        ):
            with self.subTest(intent=intent):
                result = self.search(intent)
                self.assertFalse(result["products"].exists())
                self.assertEqual(result["no_match_reason"], "gender")
                self.assertIsNone(result["relax_key"])
                self.assertEqual(result["unisex_blocker"], blocker)
                reply, context = no_match_reply(result)
                self.assertEqual(reply, f"مفيش عندي عطر للجنسين {detail}. تحب أشوفلك رجالي ولا حريمي؟")
                self.assertIn(gender.REQUIRED_MARKER, context)

    def test_a_male_request_keeps_the_budget_first_relax_order(self):
        result = self.search({"gender": "male", "scent_tags": ["fresh"], "max_price": 350})
        self.assertEqual((result["no_match_reason"], result["relax_key"], result["unisex_blocker"]),
                         ("max_price", "max_price", None))

    def test_the_reply_reaches_the_customer_and_the_answer_continues_the_request(self):
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"scent_tags": ["oud"]}), \
             patch("products.services.ai.recommendation.chat") as model:
            reply, context = route("عايز برفان يونيسكس عودي", [], self.store, self.conversation)
        model.assert_not_called()
        self.assertIn("مفيش عندي عطر للجنسين بطابع عودي. تحب أشوفلك رجالي ولا حريمي؟", reply)
        self.assertIn(gender.REQUIRED_MARKER, context)
        save_message(self.conversation, "assistant", reply, context)

        with patch("products.services.router.classify", return_value="faq") as classifier, \
             patch("products.services.router.extract_intent", return_value={}), \
             patch("products.services.router.recommend", return_value=("اختيار مناسب", "selected")) as recommender:
            result = route("رجالي", [], self.store, self.conversation)
        classifier.assert_not_called()
        self.assertEqual(result[1], "selected")
        current = recommender.call_args.kwargs["intent"]
        self.assertEqual(current["gender"], "male")
        self.assertEqual(current["scent_tags"], ["oud"])
        self.assertEqual([p.name for p in recommender.call_args.args[1]], ["Oud Man"])
