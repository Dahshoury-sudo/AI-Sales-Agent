import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.occasions import WORK
from products.services.ai.recommendation import recommend
from products.services.conversation_service import merge_preferences, save_message
from products.services.product_formatting import product_claim_facts, quote_evidence
from products.services.reply_sanitizer import product_claim_issues
from products.services.search_service import search_products
from products.services.sales.scent_preferences import explicit_scent_updates, normalize_scent_intent


class ScentRecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Scent shop")
        StoreSettings.objects.create(store=cls.store)
        cls.brand = Brand.objects.create(store=cls.store, name="House")

    def make_product(self, name, tags, *, price=400, occasion="Office", **fields):
        product = Product.objects.create(store=self.store, brand=self.brand, name=name, gender="unisex",
                                         occasion=occasion, scent_tags=tags, longevity="8 hours", projection="Moderate", **fields)
        ProductVariant.objects.create(product=product, bottle_type="normal", volume=50, price=price)
        return product

    def ids(self, intent):
        return set(search_products(intent, self.store)["products"].values_list("pk", flat=True))

    def generate(self, intent, model_data, message="رشحلي عطر"):
        search = search_products(intent, self.store)
        with patch("products.services.ai.recommendation.chat", return_value=json.dumps(model_data, ensure_ascii=False)) as model:
            result = recommend(message, search["products"], store=self.store, intent=intent, search=search)
        return result, model

    def test_all_any_and_negative_labels_use_assigned_style(self):
        fresh = self.make_product("Cedar Day", ["fresh"], top_notes="Vanilla")
        sweet = self.make_product("Amber Night", ["sweet"], top_notes="Bergamot")
        both = self.make_product("Mixed Mood", ["fresh", "sweet"])
        self.make_product("Unclassified", [], top_notes="Bergamot, Vanilla")
        self.assertEqual(self.ids({"scent_tags": ["fresh", "sweet"]}), {both.pk})
        self.assertEqual(self.ids({"scent_tags": ["fresh", "sweet"], "scent_match": "any"}), {fresh.pk, sweet.pk, both.pk})
        self.assertEqual(self.ids({"scent_tags": ["fresh"], "excluded_scent_tags": ["sweet"]}), {fresh.pk})
        self.assertEqual(self.ids({"scent_tags": ["sweet"]}), {sweet.pk, both.pk})

    def test_occasion_aliases_keep_work_and_gym_distinct(self):
        office = self.make_product("Cedar Day", ["fresh"], occasion="Office, Formal, Daily")
        sport = self.make_product("Citrus Run", ["fresh"], occasion="Sport/Casual")
        night = self.make_product("Amber Night", ["fresh"], occasion="Evening")
        self.assertEqual(self.ids({"scent_tags": ["fresh"], "occasion": "work"}), {office.pk})
        self.assertEqual(self.ids({"scent_tags": ["fresh"], "occasion": "gym"}), {sport.pk})
        self.assertEqual(self.ids({"scent_tags": ["fresh"], "occasion": "daytime"}), {office.pk, sport.pk})
        self.assertNotIn(night.pk, self.ids({"scent_tags": ["fresh"], "occasion": "work"}))

    def test_scent_without_occasion_searches_all_matching_occasions(self):
        day = self.make_product("Cedar Day", ["fresh"])
        night = self.make_product("Amber Night", ["fresh"], occasion="Evening")
        self.assertEqual(self.ids({"scent_tags": ["fresh"]}), {day.pk, night.pk})

    def test_every_eligible_candidate_reaches_ai_including_beyond_sixty(self):
        for index in range(65):
            self.make_product(f"Catalog Item {index}", ["fresh"], price=100)
        selected = self.make_product("Cedar Day", ["fresh"], price=900)
        (reply, context), model = self.generate({"scent_tags": ["fresh"]},
                                               {"product_ids": [selected.pk], "reply": "Cedar Day: ريحته فريش ومنعش ومناسب للشغل والمكتب."})
        payload = json.loads(model.call_args.args[0][-1]["content"])
        self.assertEqual(len(payload["eligible_products"]), 66)
        self.assertIn(selected.pk, {p["id"] for p in payload["eligible_products"]})
        self.assertIn(selected.name, reply)
        self.assertNotIn("Catalog Item 0", context)

    def test_variant_requirements_are_satisfied_by_the_same_sellable_variant(self):
        product = self.make_product("Cedar Day", ["fresh"], price=700)
        ProductVariant.objects.create(product=product, bottle_type="original", volume=100, price=200, stock=1)
        base = {"scent_tags": ["fresh"], "max_price": 500, "budget_strict": True}
        self.assertNotIn(product.pk, self.ids({**base, "requested_volume": 50, "bottle_type": "normal"}))
        self.assertIn(product.pk, self.ids({**base, "requested_volume": 100, "bottle_type": "original"}))
        product.variants.filter(bottle_type="original").update(stock=0)
        self.assertNotIn(product.pk, self.ids(base))

    def test_approximate_and_strict_budget_and_hard_restrictions(self):
        near = self.make_product("Cedar Day", ["fresh"], price=550)
        far = self.make_product("Amber Night", ["fresh"], price=601)
        other_store = Store.objects.create(name="Other")
        foreign = self.make_product("Other Shop Perfume", ["fresh"])
        foreign.store = other_store
        foreign.save()
        inactive = self.make_product("Inactive", ["fresh"], is_active=False)
        intent = {"scent_tags": ["fresh"], "max_price": 500}
        self.assertEqual(self.ids(intent), {near.pk})
        self.assertEqual(self.ids({**intent, "budget_strict": True}), set())
        self.assertEqual(self.ids({"scent_tags": ["fresh"], "exclude_brands": ["House"]}), set())
        self.assertEqual(self.ids({"scent_tags": ["fresh"], "exclude_names": [near.name]}), {far.pk})

    def test_ingredients_require_evidence_and_performance_does_not_filter(self):
        vanilla = self.make_product("Vanilla Garden", ["sweet"], top_notes="Vanilla")
        generic = self.make_product("Amber Night", ["sweet"])
        self.assertEqual(self.ids({"scent_tags": ["sweet"], "notes": ["vanilla"]}), {vanilla.pk})
        self.assertEqual(self.ids({"scent_tags": ["sweet"], "longevity": "12 hours"}), {vanilla.pk, generic.pk})
        self.assertEqual(self.ids({"scent_tags": ["sweet"], "projection": "strong"}), {vanilla.pk, generic.pk})
        self.assertEqual(self.ids({"scent_tags": ["sweet"], "avoid_notes": ["vanilla"]}), {generic.pk})

    def test_reference_labels_replace_note_similarity_and_exclude_reference(self):
        reference = self.make_product("Cedar Day", ["fresh", "woody"], top_notes="Bergamot")
        alternative = self.make_product("Green Walk", ["fresh", "woody"], top_notes="Vanilla")
        self.make_product("Citrus Run", ["fresh"], top_notes="Bergamot")
        result = search_products({"similar_to": reference.name}, self.store)
        self.assertFalse(result["needs_scent_reference"])
        self.assertEqual(set(result["products"].values_list("pk", flat=True)), {alternative.pk})
        self.assertEqual(result["effective_intent"]["scent_tags"], ["fresh", "woody"])
        self.assertIsNone(result["similarity"])

    def test_reference_exclusions_override_inherited_labels(self):
        reference = self.make_product("Cedar Day", ["fresh", "heavy"])
        replacement = self.make_product("Citrus Run", ["fresh", "light"])
        self.assertEqual(self.ids({"similar_to": reference.name, "excluded_scent_tags": ["heavy"]}), {replacement.pk})

    def test_untagged_reference_requests_clarification(self):
        reference = self.make_product("Cedar Day", [])
        result = search_products({"similar_to": reference.name, "similar_to_notes": ["bergamot"]}, self.store)
        self.assertTrue(result["needs_scent_reference"])
        self.assertFalse(result["products"].exists())

    def test_empty_match_asks_permission_to_relax_one_constraint(self):
        self.make_product("Cedar Day", ["fresh"])
        intent = {"scent_tags": ["fresh", "sweet"], "occasion": "office", "max_price": 500}
        search = search_products(intent, self.store)
        self.assertEqual(search["relax_key"], "scent_tags")
        with patch("products.services.ai.recommendation.chat") as model:
            reply, context = recommend("فريش وسويت للشغل", search["products"], store=self.store, intent=intent, search=search)
        model.assert_not_called()
        self.assertIn("؟", reply)
        self.assertIn("scent_tags=offered", context)
        self.assertNotIn("Cedar Day", reply)

    def test_prices_are_rendered_from_eligible_variants(self):
        product = self.make_product("Cedar Day", ["fresh"], price=550)
        intent = {"scent_tags": ["fresh"], "max_price": 500}
        (reply, context), model = self.generate(intent, {"product_ids": [product.pk], "reply": "Cedar Day: ريحته فريش ومنعش ومناسب للشغل والمكتب."})
        self.assertIn("550.00", reply)
        self.assertIn("أعلى حاجة بسيطة من ميزانيتك", reply)
        self.assertEqual(quote_evidence(context)["quotes"][0]["product_id"], product.pk)
        self.assertEqual(model.call_count, 1)

    def test_invalid_ids_retry_once_then_use_only_eligible_facts(self):
        product = self.make_product("Cedar Day", ["fresh"])
        (reply, _), model = self.generate({"scent_tags": ["fresh"]},
                                          {"product_ids": [999999], "reply": "Imaginary Choice"})
        self.assertEqual(model.call_count, 2)
        self.assertIn(product.name, reply)
        self.assertNotIn("Imaginary", reply)

    def test_invalid_style_and_ingredient_claims_are_rewritten(self):
        product = self.make_product("Cedar Day", ["fresh"])
        for bad_reply in ("Cedar Day: طابعه سويت.", "Cedar Day: فيه فانيليا."):
            with self.subTest(reply=bad_reply):
                (reply, _), model = self.generate({"scent_tags": ["fresh"]}, {"product_ids": [product.pk], "reply": bad_reply})
                self.assertEqual(model.call_count, 2)
                self.assertIn("فريش", reply)
                self.assertNotIn("فانيليا", reply)
                self.assertNotIn("سويت", reply)

    def test_scent_weight_does_not_change_uniform_projection(self):
        product = self.make_product("Amber Night", ["heavy"])
        facts = product_claim_facts([product])
        self.assertFalse(product_claim_issues("Amber Night: طابعه تقيل وفوحانه قوي.", facts))
        self.assertTrue(product_claim_issues("Amber Night: فوحانه متوسط.", facts))
        self.assertFalse(product_claim_issues("Amber Night: فوحانه قوي.", facts))
        product.scent_tags = ["light"]
        self.assertFalse(product_claim_issues("Amber Night: طابعه خفيف وفوحانه قوي.", product_claim_facts([product])))

    def test_recorded_ingredient_does_not_require_the_corresponding_style_label(self):
        product = self.make_product("Cedar Day", ["woody"], base_notes="Oud")
        self.assertEqual(self.ids({"notes": ["oud"]}), {product.pk})
        facts = product_claim_facts([product])
        self.assertFalse(product_claim_issues("Cedar Day فيه عود.", facts))
        self.assertTrue(product_claim_issues("Cedar Day: طابعه عود.", facts))

    def test_equal_performance_cannot_be_explained_as_a_difference(self):
        first = self.make_product("Cedar Day", ["fresh"])
        second = self.make_product("Citrus Run", ["fresh"])
        for claim in ("ثباته أطول من", "فوحانه أقوى من"):
            with self.subTest(claim=claim):
                (reply, _), model = self.generate({"scent_tags": ["fresh"]},
                    {"product_ids": [first.pk, second.pk], "reply": f"Cedar Day {claim} Citrus Run."})
                self.assertEqual(model.call_count, 2)
                self.assertNotIn(claim, reply)

    def test_stock_is_rechecked_after_model_selection(self):
        product = self.make_product("Cedar Day", ["fresh"])
        product.variants.all().delete()
        variant = ProductVariant.objects.create(product=product, bottle_type="original", volume=50, price=400, stock=1)
        search = search_products({"scent_tags": ["fresh"]}, self.store)

        def sold_out(*args, **kwargs):
            ProductVariant.objects.filter(pk=variant.pk).update(stock=0)
            return json.dumps({"product_ids": [product.pk], "reply": "Cedar Day: طابعه فريش."})

        with patch("products.services.ai.recommendation.chat", side_effect=sold_out):
            reply, context = recommend("فريش", search["products"], store=self.store, intent={"scent_tags": ["fresh"]}, search=search)
        self.assertNotIn(product.name, reply)
        self.assertFalse(quote_evidence(context))

    def test_total_budget_is_checked_in_code(self):
        expensive = self.make_product("Cedar Day", ["fresh"], price=500)
        second = self.make_product("Citrus Run", ["fresh"], price=400)
        cheap = self.make_product("Green Walk", ["fresh"], price=200)
        cheap_two = self.make_product("Clean Air", ["fresh"], price=250)
        intent = {"scent_tags": ["fresh"], "max_price": 600, "budget_scope": "total", "purchase_quantity": 2}
        (reply, context), model = self.generate(intent, {"product_ids": [expensive.pk, second.pk],
                                                        "reply": "Cedar Day و Citrus Run طابعهم فريش."})
        self.assertEqual(model.call_count, 2)
        quotes = quote_evidence(context)["quotes"]
        self.assertEqual({q["product_id"] for q in quotes}, {cheap.pk, cheap_two.pk})
        self.assertLessEqual(sum(Decimal(q["price"]) for q in quotes), Decimal("600"))
        self.assertIn("450.00", reply)

    def test_only_selected_products_are_saved_as_offered(self):
        selected = self.make_product("Cedar Day", ["fresh"])
        self.make_product("Citrus Run", ["fresh"])
        (reply, context), _ = self.generate({"scent_tags": ["fresh"]},
                                           {"product_ids": [selected.pk], "reply": "Cedar Day: ريحته فريش ومنعش ومناسب للشغل والمكتب."})
        conversation = Conversation.objects.create(store=self.store)
        save_message(conversation, "assistant", reply, context)
        conversation.refresh_from_db()
        self.assertEqual(conversation.sales_state["offered"]["product_ids"], [selected.pk])


class ScentPreferenceTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Scent shop")
        self.conversation = Conversation.objects.create(store=self.store)

    def test_arabic_and_english_and_or_exclusions(self):
        self.assertEqual(explicit_scent_updates("فريش وسويت")["scent_match"], "all")
        self.assertEqual(explicit_scent_updates("fresh or sweet")["scent_match"], "any")
        updates = explicit_scent_updates("فريش مش تقيل")
        self.assertEqual(updates["scent_tags"], ["fresh"])
        self.assertEqual(updates["excluded_scent_tags"], ["heavy"])
        updates = explicit_scent_updates("any scent except oud")
        self.assertTrue(updates["scent_open"])
        self.assertEqual(updates["excluded_scent_tags"], ["oud"])

    def test_unknown_model_labels_are_rejected_and_legacy_styles_are_normalized(self):
        result = normalize_scent_intent({"scent_tags": ["invented"], "notes": ["fresh", "vanilla"]})
        self.assertEqual(result["scent_tags"], ["fresh"])
        self.assertEqual(result["notes"], ["vanilla"])

    def test_literal_ingredients_do_not_add_a_style_requirement(self):
        self.assertFalse(explicit_scent_updates("عايز عطر فيه عود").get("scent_tags"))
        self.assertFalse(explicit_scent_updates("contains oud and leather").get("scent_tags"))
        self.assertEqual(explicit_scent_updates("عايز طابع عودي")["scent_tags"], ["oud"])

    def test_negative_lists_and_replacement_do_not_invert_preferences(self):
        for message in ("مش عايز عود ولا سويت", "بلاش عود وسويت", "no oud or sweet", "without oud and sweet"):
            with self.subTest(message=message):
                updates = explicit_scent_updates(message)
                self.assertEqual(updates["excluded_scent_tags"], ["oud", "sweet"])
                self.assertFalse(updates.get("scent_tags"))
        self.assertEqual(explicit_scent_updates("بدل فريش خلينا سويت")["scent_tags"], ["sweet"])
        for message in ("not too heavy", "don't want it too heavy", "not very heavy"):
            self.assertEqual(explicit_scent_updates(message)["excluded_scent_tags"], ["heavy"])

    def test_explicit_clear_cannot_be_undone_by_an_extractor_echo(self):
        merge_preferences(self.conversation, {"scent_tags": ["fresh"]}, "فريش")
        result = merge_preferences(self.conversation, {"clear_preferences": ["scent_tags"],
                                                       "scent_tags": ["fresh"]}, "شيل شرط طابع الريحة")
        self.assertFalse(result.get("scent_tags"))

    def test_memory_survives_window_and_new_style_replaces_old(self):
        merge_preferences(self.conversation, {"scent_tags": ["fresh"]}, "عايز فريش")
        for index in range(10):
            save_message(self.conversation, "user", f"message {index}")
        result = merge_preferences(self.conversation, {"max_price": 800}, "800")
        self.assertEqual(result["scent_tags"], ["fresh"])
        result = merge_preferences(self.conversation, {"scent_tags": ["sweet"]}, "عايز سويت")
        self.assertEqual(result["scent_tags"], ["sweet"])
        self.assertEqual(result["max_price"], 800)

    def test_addition_negation_and_recipient_reset(self):
        merge_preferences(self.conversation, {"scent_tags": ["fresh"]}, "فريش")
        result = merge_preferences(self.conversation, {"scent_tags": ["woody"]}, "وخشبي كمان")
        self.assertEqual(result["scent_tags"], ["fresh", "woody"])
        result = merge_preferences(self.conversation, {"excluded_scent_tags": ["fresh"]}, "مش فريش")
        self.assertEqual(result["scent_tags"], ["woody"])
        self.assertEqual(result["excluded_scent_tags"], ["fresh"])
        result = merge_preferences(self.conversation, {"reset_preferences": True, "gender": "female", "scent_tags": ["sweet"]}, "دلوقتي عايز سويت لخطيبتي")
        self.assertEqual(result["scent_tags"], ["sweet"])
        self.assertFalse(result.get("excluded_scent_tags"))

    def test_accepting_scent_relaxation_keeps_other_requirements(self):
        merge_preferences(self.conversation, {"scent_tags": ["fresh"], "excluded_scent_tags": ["oud"],
                                              "occasion": "office", "max_price": 800}, "فريش للشغل مش عودي")
        result = merge_preferences(self.conversation, {}, "اه", pending={"scent_tags": "offered"})
        self.assertTrue(result["scent_open"])
        self.assertEqual(result["scent_tags"], [])
        self.assertEqual(result["excluded_scent_tags"], ["oud"])
        self.assertEqual((result["occasion"], result["max_price"]), (WORK, 800))

    def test_occasion_only_asks_style_before_recommending(self):
        from products.services.router import route
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"occasion": "office"}), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.router.recommend") as model:
            reply, context = route("عايز عطر رجالي للشغل", [], self.store, self.conversation)
        self.assertEqual(context, "SCENT_PREFERENCE_REQUIRED")
        self.assertIn("فريش", reply)
        model.assert_not_called()

    def test_scent_only_routes_to_recommendation_without_occasion_question(self):
        from products.services.router import route
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"scent_tags": ["fresh"]}), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.router.recommend", return_value=("اختيار فريش", "chosen")) as model:
            reply, context = route("عايز عطر رجالي فريش", [], self.store, self.conversation)
        model.assert_called_once()
        self.assertEqual(context, "chosen")

    def test_scent_answer_after_unknown_reference_does_not_loop(self):
        from products.services.router import route
        save_message(self.conversation, "assistant", "قولي طابع الريحة اللي بتدور عليه: فريش، سويت، عودي، ولا حاجة تانية؟", "SIMILARITY_NEEDS_PROFILE")
        self.conversation.preferences = {"similar_to": "Unknown fragrance", "max_price": 800, "gender": "unisex"}
        self.conversation.save(update_fields=["preferences"])
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={"scent_tags": ["fresh"]}), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.router.recommend", return_value=("اختيار فريش", "chosen")) as model:
            reply, context = route("فريش", [], self.store, self.conversation)
        model.assert_called_once()
        self.assertFalse(model.call_args.kwargs["intent"].get("similar_to"))
        self.assertEqual(model.call_args.kwargs["intent"]["max_price"], 800)
        self.assertEqual(context, "chosen")
