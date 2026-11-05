"""Six occasion categories stay consistent from customer wording to the final offer."""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.occasions import (
    ACTIVE, DAILY, FORMAL, NIGHT_OUT, OCCASIONS, ROMANTIC, WORK,
    normalize_occasion, occasion_groups, occasion_matches,
)
from products.services.ai.intent import extract_intent
from products.services.ai.recommendation import recommend
from products.services.conversation_service import merge_preferences, save_message
from products.services.product_formatting import format_products, product_claim_facts
from products.services.product_info import get_product_info, occasion_reference_requested
from products.services.reply_sanitizer import product_claim_issues
from products.services.router import route
from products.services.sales.occasion_preferences import CLARIFICATION, PENDING_KEY
from products.services.sales.recommendation_response import response_issues
from products.services.sales.request_updates import request_mode
from products.services.search_service import product_matches_request, search_products


class OccasionVocabularyTests(SimpleTestCase):
    def test_six_complete_labels_only_match_their_own_category(self):
        self.assertEqual(OCCASIONS, (
            "Daily & Casual", "Work & Office", "Night Out & Hanging Out",
            "Date Night & Romantic", "Formal & Special Occasions", "Beach, Summer & Gym",
        ))
        for recorded in OCCASIONS:
            self.assertEqual(occasion_groups(recorded), {recorded})
            for wanted in OCCASIONS:
                with self.subTest(recorded=recorded, wanted=wanted):
                    self.assertEqual(occasion_matches(recorded, wanted), recorded == wanted)

    def test_arabic_and_english_aliases(self):
        cases = {
            "university": DAILY, "daytime": DAILY, "للجامعة": DAILY, "للنهار": DAILY,
            "work": WORK, "job interview": WORK, "للشغل": WORK, "للمكتب": WORK,
            "with friends": NIGHT_OUT, "party": NIGHT_OUT, "خروجات": NIGHT_OUT, "للسهرة": NIGHT_OUT,
            "date night": ROMANTIC, "romantic dinner": ROMANTIC, "للديت": ROMANTIC,
            "wedding party": FORMAL, "engagement": FORMAL, "لفرح": FORMAL,
            "beach": ACTIVE, "summer": ACTIVE, "workout": ACTIVE, "للجيم": ACTIVE, "للبحر": ACTIVE,
            "خروجات خفيفة": DAILY, "للمسجد": DAILY,
        }
        for wording, expected in cases.items():
            with self.subTest(wording=wording):
                self.assertEqual(normalize_occasion(wording), expected)

    def test_legacy_multiple_values_and_full_labels_can_coexist(self):
        cases = {
            "Office, Formal, Daily": {WORK, FORMAL, DAILY},
            "Sport/Casual": {ACTIVE, DAILY},
            "Night out, Parties, Dates": {NIGHT_OUT, ROMANTIC},
            f"{ROMANTIC}; {ACTIVE}": {ROMANTIC, ACTIVE},
            "Evening, Casual, Winter": {NIGHT_OUT, DAILY},
        }
        for recorded, expected in cases.items():
            with self.subTest(recorded=recorded):
                self.assertEqual(occasion_groups(recorded), expected)

    def test_unknown_values_and_substrings_cannot_satisfy_an_occasion(self):
        for recorded in ("", None, "Unclassified", "Holiday", "Update"):
            self.assertFalse(occasion_matches(recorded, DAILY))
        self.assertFalse(occasion_matches("Unclassified", "Unclassified"))
        self.assertFalse(occasion_matches("Office", "daytime"))
        self.assertFalse(occasion_matches("Sport", "daytime"))
        self.assertTrue(occasion_matches("", None))

    def test_availability_questions_with_occasion_words_in_names_remain_product_questions(self):
        for message in ("و عندكو نسيم الليل ؟", "do you have Summer Breeze?", "عندكم بارتي نايت؟"):
            self.assertEqual(request_mode(message), "")
        self.assertEqual(request_mode("عندكو حاجة للشغل؟"), "new_goal")

    def test_negated_occasion_is_not_positive_evidence(self):
        self.assertEqual(occasion_groups("not for work or gym", positive_only=True), set())
        self.assertEqual(occasion_groups("مش عايز للشغل، عايز للديت", positive_only=True), {ROMANTIC})

    def test_bounded_offer_pointers_use_all_six_categories(self):
        for category in OCCASIONS:
            self.assertEqual(occasion_reference_requested(f"show me the {category} perfume"), category)
        for text, expected in (("واعطيني العطر اليومي", DAILY), ("العطر الليلي", NIGHT_OUT),
                               ("العطر الرومانسي", ROMANTIC), ("اللي للشغل", WORK),
                               ("اللي للجيم", ACTIVE)):
            self.assertEqual(occasion_reference_requested(text), expected)
        for text in ("عايز عطر يومي", "هل Elite ينفع للنهار؟", "اديني العطر اليومي Sauvage",
                     "مش عايز العطر اليومي", "ضيف العطر اليومي 90 ملي"):
            self.assertFalse(occasion_reference_requested(text))


class OccasionExtractionTests(SimpleTestCase):
    def extract(self, message, data=None, history=None):
        with patch("products.services.ai.intent.chat", return_value=json.dumps(data or {})) as model:
            result = extract_intent(message, history=history)
        model.assert_called_once()
        return result

    def test_deterministic_extraction_covers_six_categories_when_model_omits_them(self):
        cases = (
            ("عايز فريش للجامعة", DAILY), ("recommend fresh perfume for work", WORK),
            ("عايز فريش للخروجات مع صحابي", NIGHT_OUT), ("recommend perfume for a date night", ROMANTIC),
            ("رشحلي عطر لفرح", FORMAL), ("recommend perfume for the beach", ACTIVE),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(self.extract(message).get("occasion"), expected)

    def test_date_night_overrides_an_incorrect_nightlife_extraction(self):
        result = self.extract("recommend a perfume for a date night", {"occasion": "evening"})
        self.assertEqual(result["occasion"], ROMANTIC)

    def test_unfamiliar_situation_keeps_the_models_grounded_nearest_category(self):
        result = self.extract("recommend a woody perfume for a museum opening", {
            "occasion": FORMAL, "occasion_text": "museum opening", "scent_tags": ["woody"],
        })
        self.assertEqual(result["occasion"], FORMAL)
        self.assertEqual(result["recommendation_mode"], "new_goal")

    def test_unsupported_model_category_requires_clarification(self):
        result = self.extract("recommend fresh perfume for interplanetary travel", {
            "occasion": "interplanetary travel", "occasion_text": "interplanetary travel",
        })
        self.assertTrue(result[PENDING_KEY])
        self.assertNotIn("occasion", result)

    def test_contextual_mapping_is_not_overwritten_by_an_incidental_keyword(self):
        result = self.extract("recommend a perfume for a quiet evening at home", {
            "occasion": DAILY, "occasion_text": "quiet evening at home",
        })
        self.assertEqual(result["occasion"], DAILY)

    def test_failed_extraction_keeps_known_aliases_or_requests_clarification(self):
        with patch("products.services.ai.intent.chat", return_value="invalid JSON"):
            self.assertEqual(extract_intent("عايز فريش للجيم")["occasion"], ACTIVE)
            result = extract_intent("recommend fresh perfume for interplanetary travel")
            self.assertTrue(result[PENDING_KEY])

    def test_delegation_is_transient_and_does_not_open_scent_preferences(self):
        for message in ("انت اختار", "على ذوقك", "choose for me", "you decide", "choose yourself"):
            with self.subTest(message=message):
                result = self.extract(message, {"occasion": DAILY})
                self.assertTrue(result.get("occasion_delegated"))
                self.assertNotIn("occasion", result)
                self.assertFalse(result.get("scent_open"))

    def test_confirmation_and_negated_delegation_do_not_establish_an_occasion(self):
        for message in ("تمام", "okay", "don't choose for me", "بلاش انت اختار", "انا اختار", "عايز اختار عطر"):
            with self.subTest(message=message):
                result = self.extract(message, {"occasion": DAILY, "occasion_delegated": True,
                                                "occasion_text": message})
                self.assertNotIn("occasion", result)
                self.assertFalse(result.get("occasion_delegated"))


class OccasionConversationTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Occasion memory")
        self.conversation = Conversation.objects.create(store=self.store)
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))

    def saved(self, **preferences):
        self.conversation.preferences = preferences
        self.conversation.save(update_fields=["preferences"])

    def merge(self, message, intent=None, **kwargs):
        return merge_preferences(self.conversation, intent or {}, message, store=self.store, **kwargs)

    def test_delegated_default_is_saved_and_keeps_scent_budget_and_exclusions(self):
        self.saved(scent_tags=["fresh"], excluded_scent_tags=["oud"], max_price=800)
        result = self.merge("انت اختار")
        self.assertEqual(result["occasion"], DAILY)
        self.assertEqual(result["scent_tags"], ["fresh"])
        self.assertEqual(result["excluded_scent_tags"], ["oud"])
        self.assertEqual(result["max_price"], 800)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences["occasion"], DAILY)
        self.assertNotIn("occasion_delegated", self.conversation.preferences)

    def test_delegating_product_choice_keeps_the_existing_occasion(self):
        for old in (WORK, ROMANTIC, "Office", "Date Night"):
            with self.subTest(old=old):
                self.saved(occasion=old, scent_tags=["fresh"])
                result = self.merge("choose for me", {"occasion": DAILY})
                self.assertEqual(result["occasion"], normalize_occasion(old))

    def test_new_explicit_occasion_takes_precedence_over_delegation_and_memory(self):
        self.saved(occasion=WORK, scent_tags=["fresh"])
        result = self.merge("عايز سويت للديت وانت اختار", {"occasion": DAILY, "scent_tags": ["sweet"]})
        self.assertEqual(result["occasion"], ROMANTIC)
        self.assertEqual(result["scent_tags"], ["sweet"])

    def test_omission_and_confirmations_do_not_invent_a_default(self):
        for text in ("عايز فريش", "تمام"):
            self.assertNotIn("occasion", self.merge(text, {"scent_tags": ["fresh"]}))
        self.saved(occasion="Office", scent_tags=["fresh"])
        self.assertEqual(self.merge("تمام", {"occasion": DAILY})["occasion"], WORK)

    def test_scent_size_and_between_product_choices_do_not_delegate_occasion(self):
        cases = (
            ("اختار الريحه", []), ("choose the size for me", []), ("choose between these two", []),
            ("انت اختار", [{"role": "assistant", "content": "تحب الريحة فريش ولا سويت؟"}]),
        )
        for text, history in cases:
            with self.subTest(text=text, history=history):
                self.assertNotIn("occasion", self.merge(text, history=history))

    def test_occasion_question_accepts_delegation_and_short_category_answers(self):
        history = [{"role": "assistant", "content": CLARIFICATION}]
        self.assertEqual(self.merge("انت اختار", history=history)["occasion"], DAILY)
        self.assertEqual(self.merge("Work & Office", history=history)["occasion"], WORK)

    def test_latest_question_and_explicit_choice_scope_control_delegation(self):
        history = [{"role": "assistant", "content": "ده مناسب للمناسبة. تحب حجم 50 ولا 100 ملي؟"}]
        self.assertNotIn("occasion", self.merge("انت اختار", history=history))
        history = [{"role": "assistant", "content": "للشغل ولا للخروجات؟"}]
        self.assertNotIn("occasion", self.merge("اختار الحجم", history=history))
        self.assertEqual(self.merge("انت اختار", history=history)["occasion"], DAILY)

    def test_stateless_delegation_keeps_customer_history_occasion(self):
        result = merge_preferences(None, {}, "choose for me", history=[
            {"role": "user", "content": "recommend fresh perfume for work"},
        ])
        self.assertEqual(result["occasion"], WORK)

    def test_clear_and_accepted_relaxation_do_not_restore_occasion(self):
        self.saved(occasion=WORK, scent_tags=["fresh"])
        result = self.merge("أي مناسبة", {"occasion": WORK, "clear_preferences": ["occasion"]})
        self.assertNotIn("occasion", result)
        self.saved(occasion=WORK, scent_tags=["fresh"])
        result = self.merge("اه", {"occasion": WORK}, pending={"occasion": "offered"})
        self.assertNotIn("occasion", result)
        result = merge_preferences(None, {"occasion": "unrecognized", "clear_preferences": ["occasion"]}, "أي مناسبة")
        self.assertNotIn("occasion", result)
        self.assertNotIn(PENDING_KEY, result)

    def test_unresolved_new_occasion_does_not_restore_old_one_and_survives_followup(self):
        self.saved(occasion=WORK, scent_tags=["fresh"])
        result = self.merge("for interplanetary travel", {"occasion": "interplanetary travel"})
        self.assertNotIn("occasion", result)
        self.assertTrue(result[PENDING_KEY])
        self.assertTrue(self.merge("500", {"max_price": 500})[PENDING_KEY])
        result = self.merge("للجامعة", history=[{"role": "assistant", "content": CLARIFICATION}])
        self.assertEqual(result["occasion"], DAILY)
        self.assertNotIn(PENDING_KEY, result)
        self.assertEqual(result["max_price"], 500)


class OccasionRecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Six occasions")
        StoreSettings.objects.create(store=cls.store)
        cls.brand = Brand.objects.create(store=cls.store, name="House")
        cls.products = {}
        for category, name in zip(OCCASIONS, (
            "Cedar Ease", "Desk Breeze", "City Pulse", "Rose Whisper", "Velvet Silk", "Coastal Splash",
        )):
            product = Product.objects.create(store=cls.store, brand=cls.brand, name=name,
                                             occasion=category, gender="unisex", scent_tags=["fresh"])
            ProductVariant.objects.create(product=product, volume=50, price=400)
            cls.products[category] = product

    def setUp(self):
        self.conversation = Conversation.objects.create(store=self.store)
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))

    def test_search_and_current_eligibility_use_exact_categories(self):
        for category, product in self.products.items():
            intent = {"occasion": category, "scent_tags": ["fresh"]}
            result = search_products(intent, self.store)
            self.assertEqual(list(result["products"]), [product])
            for candidate in self.products.values():
                self.assertEqual(product_matches_request(candidate, intent, self.store), candidate == product)

    def test_legacy_products_remain_eligible_without_rewriting_data(self):
        product = self.products[ACTIVE]
        product.occasion = "Sport/Casual"
        product.save(update_fields=["occasion"])
        self.assertIn(product, search_products({"occasion": "gym"}, self.store)["products"])
        self.assertIn(product, search_products({"occasion": DAILY}, self.store)["products"])
        product.refresh_from_db()
        self.assertEqual(product.occasion, "Sport/Casual")

    def test_unknown_request_asks_for_occasion_without_relaxing_search(self):
        intent = {"occasion": "unrecognized", "scent_tags": ["fresh"]}
        result = search_products(intent, self.store)
        self.assertTrue(result["needs_occasion"])
        self.assertFalse(result["products"].exists())
        self.assertIsNone(result["relax_key"])
        with patch("products.services.ai.recommendation.chat") as model:
            reply, context = recommend("fresh for something else", result["products"], store=self.store,
                                       intent=intent, search=result)
        model.assert_not_called()
        self.assertEqual((reply, context), (CLARIFICATION, "OCCASION_REQUIRED"))

    def test_empty_category_match_keeps_existing_relaxation_flow(self):
        Product.objects.exclude(pk=self.products[WORK].pk).update(is_active=False)
        intent = {"occasion": ROMANTIC, "scent_tags": ["fresh"]}
        result = search_products(intent, self.store)
        self.assertFalse(result["products"].exists())
        self.assertEqual(result["relax_key"], "occasion")
        with patch("products.services.ai.recommendation.chat") as model:
            reply, context = recommend("fresh for a date", result["products"], store=self.store,
                                       intent=intent, search=result)
        model.assert_not_called()
        self.assertIn("occasion=offered", context)
        self.assertNotIn(self.products[WORK].name, reply)

    def test_suitability_claims_use_the_same_categories_in_both_validators(self):
        for category, product in self.products.items():
            facts = product_claim_facts([product])
            for claimed in OCCASIONS:
                with self.subTest(recorded=category, claimed=claimed):
                    reply = f"{product.name} is suitable for {claimed}."
                    self.assertEqual(bool(product_claim_issues(reply, facts)), claimed != category)
                    self.assertEqual(bool(response_issues(reply, [product], {})), claimed != category)

    def test_occasion_followup_finds_the_appropriate_previous_offer(self):
        date, night = self.products[ROMANTIC], self.products[NIGHT_OUT]
        for product in (date, night):
            save_message(self.conversation, "assistant", product.name, format_products([product]))
        with patch("products.services.product_info.chat", return_value=f"{date.name} مناسب للديت."):
            reply, context = get_product_info("show me the date night perfume", [], self.store, self.conversation)
        self.assertIn(date.name, context)
        self.assertNotIn(night.name, context)

    def test_router_retains_work_on_delegation_even_if_extractor_supplies_default(self):
        self.conversation.preferences = {"occasion": "Office", "scent_tags": ["fresh"]}
        self.conversation.save(update_fields=["preferences"])
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router.extract_intent", return_value={"occasion": DAILY}), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.router.recommend", return_value=("اختيارك", "chosen")) as model:
            route("انت اختار", [], self.store, self.conversation)
        self.assertEqual(list(model.call_args.args[1]), [self.products[WORK]])
        self.assertEqual(model.call_args.kwargs["intent"]["occasion"], WORK)

    def test_router_clarifies_failed_mapping_before_searching_or_recommending(self):
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"occasion": "unrecognized", "scent_tags": ["fresh"]}), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.router.search_products") as search, \
             patch("products.services.router.recommend") as model:
            reply, context = route("recommend fresh perfume", [], self.store, self.conversation)
        self.assertEqual((reply, context), (CLARIFICATION, "OCCASION_REQUIRED"))
        search.assert_not_called()
        model.assert_not_called()
