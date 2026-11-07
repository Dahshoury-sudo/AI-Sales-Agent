"""Nearest recommendations retain the scent minimum and firm purchase limits."""
import json
from decimal import Decimal
from itertools import combinations
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services.ai.recommendation import recommend
from products.services.router import route
from products.services.product_formatting import quote_evidence
from products.services.search_service import product_matches_request, search_products


class NearestCatalogMixin:
    def setUp(self):
        self.store = Store.objects.create(name="Nearest shop")
        StoreSettings.objects.create(store=self.store)
        self.brand = Brand.objects.create(store=self.store, name="House")

    def perfume(self, name, tags=("fresh",), *, price=400, **fields):
        attrs = {"gender": "unisex", "season": "Summer", "occasion": "Office", **fields}
        product = Product.objects.create(store=self.store, brand=self.brand, name=name,
                                         scent_tags=list(tags), **attrs)
        ProductVariant.objects.create(product=product, volume=50, bottle_type="normal", price=price)
        return product


class NearestEligibilityTests(NearestCatalogMixin, TestCase):
    def test_one_shared_tag_satisfies_new_and_saved_all_requests(self):
        fresh = self.perfume("Fresh Day")
        woody = self.perfume("Cedar Walk", ["woody"])
        self.perfume("Sweet Evening", ["sweet"])
        self.perfume("Unlabelled", [], top_notes="Bergamot, Cedar")
        for mode in (None, "all", "any"):
            with self.subTest(mode=mode):
                intent = {"scent_tags": ["fresh", "woody"], "scent_match": mode}
                result = search_products(intent, self.store)
                self.assertEqual(list(result["products"]), [fresh, woody])
                self.assertTrue(product_matches_request(fresh, intent, self.store))
                self.assertTrue(product_matches_request(woody, intent, self.store))

    def test_season_and_occasion_mismatches_do_not_drop_a_scent_match(self):
        product = self.perfume("Winter Night", season="Winter", occasion="Evening")
        intent = {"scent_tags": ["fresh"], "season": "summer", "occasion": "work"}
        result = search_products(intent, self.store, keep={product.name})
        self.assertEqual(list(result["products"]), [product])
        self.assertEqual(result["keeping"], [product.name])
        self.assertEqual(result["dropped"], {})
        self.assertIsNone(result["relax_key"])

    def test_season_match_outranks_additional_scent_tags_and_occasion(self):
        winter = self.perfume("Winter Cedar", ["fresh", "woody"], season="Winter")
        summer = self.perfume("Summer Breeze", occasion="Evening")
        self.perfume("Unrelated Summer", ["sweet"])
        result = search_products({"scent_tags": ["fresh", "woody"], "season": "summer",
                                  "occasion": "work"}, self.store)
        self.assertEqual(list(result["products"]), [summer, winter])

    def test_additional_tags_then_occasion_break_season_ties(self):
        one = self.perfume("One Tag")
        two_night = self.perfume("Two Night", ["fresh", "woody"], occasion="Evening")
        two_work = self.perfume("Two Work", ["fresh", "woody"])
        result = search_products({"scent_tags": ["fresh", "woody"], "season": "summer",
                                  "occasion": "work"}, self.store)
        self.assertEqual(list(result["products"]), [two_work, two_night, one])

    def test_missing_season_receives_no_bonus_and_all_seasons_matches(self):
        unknown = self.perfume("No Season", season="")
        all_seasons = self.perfume("Every Season", season="All Seasons")
        summer = self.perfume("Warm Day", season="Spring/Summer")
        result = search_products({"scent_tags": ["fresh"], "season": "الصيف"}, self.store)
        self.assertEqual(list(result["products"]), [all_seasons, summer, unknown])
        self.assertEqual(list(search_products({"scent_tags": ["fresh"]}, self.store)["products"]),
                         [unknown, all_seasons, summer])

    def test_overlap_uses_tags_beyond_the_description_window(self):
        product = self.perfume("Fourth Tag", ["fresh", "clean", "light", "woody"])
        result = search_products({"scent_tags": ["woody", "smoky"]}, self.store)
        self.assertEqual(list(result["products"]), [product])

    def test_similarity_requires_only_one_permitted_reference_tag(self):
        reference = self.perfume("Reference", ["fresh", "woody", "heavy"])
        alternative = self.perfume("Alternative", ["woody"], season="Winter", occasion="Evening")
        self.perfume("Excluded Style", ["fresh", "heavy"])
        self.perfume("Unrelated", ["sweet"])
        result = search_products({"similar_to": reference.name, "excluded_scent_tags": ["heavy"],
                                  "season": "summer", "occasion": "work"}, self.store)
        self.assertEqual(list(result["products"]), [alternative])
        self.assertEqual(result["effective_intent"]["scent_tags"], ["fresh", "woody"])

    def test_excluding_every_reference_tag_requires_a_new_style(self):
        reference = self.perfume("Reference", ["fresh"])
        self.perfume("Unrelated", ["sweet"])
        result = search_products({"similar_to": reference.name, "excluded_scent_tags": ["fresh"]}, self.store)
        self.assertFalse(result["products"].exists())
        self.assertTrue(result["needs_scent_reference"])

    def test_unknown_occasion_does_not_prevent_a_scent_recommendation(self):
        product = self.perfume("Fresh Day")
        intent = {"scent_tags": ["fresh"], "occasion": "unrecognized", "gender": "unisex"}
        result = search_products(intent, self.store)
        self.assertEqual(list(result["products"]), [product])
        self.assertFalse(result["needs_occasion"])
        conversation = Conversation.objects.create(store=self.store)
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value=intent), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.product_resolver.confirm_unplaced", return_value=None), \
             patch("products.services.ai.recommendation.chat", return_value=json.dumps({
                 "product_ids": [product.pk], "reply": "Fresh Day: ريحته فريش ومنعش ومناسب للشغل والمكتب.",
             })):
            reply, context = route("recommend a fresh unisex perfume", [], self.store, conversation)
        self.assertIn(product.name, reply)
        self.assertNotIn("OCCASION_REQUIRED", context)

    def test_zero_shared_tags_never_offers_automatic_scent_relaxation(self):
        self.perfume("Sweet Evening", ["sweet"])
        intent = {"scent_tags": ["fresh"], "season": "summer", "occasion": "work"}
        result = search_products(intent, self.store)
        self.assertFalse(result["products"].exists())
        reply, context = recommend("عايز فريش", result["products"], store=self.store, intent=intent, search=result)
        self.assertNotIn("Sweet Evening", reply)
        self.assertNotIn("scent_tags=offered", context)
        self.assertNotIn("كل الشروط", reply)


class NearestSelectionTests(NearestCatalogMixin, TestCase):
    def generate(self, intent, data, *, message="رشحلي عطر"):
        search = search_products(intent, self.store)
        response = data if callable(data) else lambda *args, **kwargs: json.dumps(data, ensure_ascii=False)
        with patch("products.services.ai.recommendation.chat", side_effect=response) as model:
            result = recommend(message, search["products"], store=self.store, intent=intent, search=search)
        return result, model

    def test_lower_fit_model_choice_retries_then_recommends_the_nearest(self):
        winter = self.perfume("Winter Day", season="Winter")
        summer = self.perfume("Summer Day")
        (reply, context), model = self.generate(
            {"scent_tags": ["fresh"], "season": "summer", "recommendation_count": 1},
            {"product_ids": [winter.pk], "reply": "Winter Day: ريحته فريش ومنعش ومناسب للشغل والمكتب."},
        )
        self.assertEqual(model.call_count, 2)
        self.assertIn(summer.name, reply)
        self.assertNotIn(winter.name, context)

    def test_selection_cannot_skip_a_nearer_second_perfume(self):
        first = self.perfume("First Summer")
        second = self.perfume("Second Summer")
        winter = self.perfume("Winter Day", season="Winter")
        (reply, context), _ = self.generate(
            {"scent_tags": ["fresh"], "season": "summer"},
            {"product_ids": [first.pk, winter.pk], "reply":
             "First Summer: ريحته فريش ومنعش ومناسب للشغل والمكتب. "
             "Winter Day: ريحته فريش ومنعش ومناسب للشغل والمكتب."},
        )
        self.assertIn(first.name, reply)
        self.assertIn(second.name, reply)
        self.assertNotIn(winter.name, context)

    def test_a_partial_match_is_valid_without_rewriting_its_actual_occasion(self):
        product = self.perfume("Night Breeze", season="Winter", occasion="Evening")
        (reply, _), model = self.generate(
            {"scent_tags": ["fresh", "woody"], "scent_match": "all", "season": "summer", "occasion": "work"},
            {"product_ids": [product.pk], "reply": "Night Breeze: ريحته فريش ومنعش ومناسب للخروجات والسهرات."},
        )
        self.assertEqual(model.call_count, 1)
        self.assertIn(product.name, reply)
        self.assertNotIn("الشغل", reply)
        payload = json.loads(model.call_args.args[0][-1]["content"])
        self.assertIn("match", payload["eligible_products"][0])
        self.assertEqual(payload["eligible_products"][0]["match"]["matched_scent_tags"], ["fresh"])
        self.assertFalse(payload["eligible_products"][0]["match"]["occasion_match"])

    def test_season_changes_during_generation_reorder_recovery(self):
        previous = self.perfume("Previous Summer")
        current = self.perfume("Current Summer", season="Winter")

        def changed_catalog(*args, **kwargs):
            Product.objects.filter(pk=previous.pk).update(season="Winter")
            Product.objects.filter(pk=current.pk).update(season="Summer")
            return json.dumps({"product_ids": [previous.pk], "reply":
                               "Previous Summer: ريحته فريش ومنعش ومناسب للشغل والمكتب."})

        (reply, _), model = self.generate(
            {"scent_tags": ["fresh"], "season": "summer", "recommendation_count": 1}, changed_catalog)
        self.assertEqual(model.call_count, 2)
        self.assertIn(current.name, reply)
        self.assertNotIn(previous.name, reply)

    def test_partial_matches_cannot_claim_an_unsupported_season(self):
        product = self.perfume("Fresh Day")
        for season in ("Summer", "", "unrecognized"):
            with self.subTest(season=season):
                Product.objects.filter(pk=product.pk).update(season=season)
                (reply, _), model = self.generate(
                    {"scent_tags": ["fresh"], "season": "winter", "recommendation_count": 1},
                    {"product_ids": [product.pk], "reply":
                     "Fresh Day: ريحته فريش ومنعش ومناسب للشغل والمكتب. مناسب للشتاء."},
                )
                self.assertIn(product.name, reply)
                self.assertNotIn("مناسب للشتاء", reply)
                self.assertEqual(model.call_count, 2)

    def test_season_claims_use_each_products_recorded_seasons(self):
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues

        summer = self.perfume("Summer Day")
        winter = self.perfume("Winter Day", season="Fall/Winter")
        every_season = self.perfume("Every Day", season="All Seasons")
        cases = [
            ("Summer Day مناسب للشتا.", True),
            ("Summer Day عطر شتوي.", True),
            ("Summer Day is suitable for winter.", True),
            ("Summer Day مناسب لكل الفصول.", True),
            ("Summer Day مناسب للصيف. Winter Day مناسب للشتاء.", False),
            ("Summer Day مناسب للشتاء. Winter Day مناسب للصيف.", True),
            ("Winter Day ينفع للخريف والشتاء.", False),
            ("Every Day مناسب للصيف والشتاء.", False),
            ("Every Day مناسب لكل الفصول.", False),
            ("Summer Day مش مناسب للشتا.", False),
            ("Summer Day: أنت طلبت عطر للشتاء، ده ريحته فريش.", False),
        ]
        facts = product_claim_facts([summer, winter, every_season])
        for reply, rejected in cases:
            with self.subTest(reply=reply):
                seasonal_issues = [issue for issue in product_claim_issues(reply, facts)
                                   if issue.startswith("season:")]
                self.assertEqual(bool(seasonal_issues), rejected)

    def test_recovery_keeps_the_nearest_perfume_in_a_feasible_total_budget(self):
        first_cheap = self.perfume("Cheap Winter", season="Winter", price=100)
        self.perfume("Other Winter", season="Winter", price=110)
        nearest = self.perfume("Summer Day", price=500)
        (reply, context), _ = self.generate(
            {"scent_tags": ["fresh"], "season": "summer", "max_price": 650,
             "budget_scope": "total", "purchase_quantity": 2},
            {"product_ids": [999999], "reply": "Invalid"},
        )
        quotes = quote_evidence(context)["quotes"]
        self.assertEqual([q["product_id"] for q in quotes], [nearest.pk, first_cheap.pk])
        self.assertLessEqual(sum(Decimal(q["price"]) for q in quotes), Decimal("650"))
        self.assertIn(nearest.name, reply)

    def test_total_budget_ties_reserve_money_for_the_nearest_remaining_choice(self):
        self.perfume("Expensive Cedar", ["fresh", "woody"], price=400)
        best = self.perfume("Affordable Cedar", ["fresh", "woody"], price=100)
        next_best = self.perfume("Summer Day", price=200)
        self.perfume("Winter Day", season="Winter", price=50)
        (_, context), _ = self.generate(
            {"scent_tags": ["fresh", "woody"], "season": "summer", "max_price": 450,
             "budget_scope": "total", "purchase_quantity": 2},
            {"product_ids": [999999], "reply": "Invalid"},
        )
        self.assertEqual([q["product_id"] for q in quote_evidence(context)["quotes"]], [best.pk, next_best.pk])

    def test_model_cannot_add_a_relaxation_question_to_an_eligible_offer(self):
        product = self.perfume("Fresh Day")
        (reply, context), model = self.generate(
            {"scent_tags": ["fresh"], "occasion": "work"},
            {"product_ids": [product.pk], "reply":
             "Fresh Day: ريحته فريش ومنعش ومناسب للشغل والمكتب. "
             "مش لاقي ترشيح يطابق كل الشروط دي مع بعض. تحب نغير المناسبة؟"},
        )
        self.assertEqual(model.call_count, 2)
        self.assertIn(product.name, reply)
        self.assertNotIn("كل الشروط", reply)
        self.assertNotIn("نغير المناسبة", reply)
        self.assertNotIn("PENDING_RELAX", context)

    def test_actual_size_blocker_does_not_ask_to_change_scent(self):
        self.perfume("Fresh Day")
        intent = {"scent_tags": ["fresh"], "requested_volume": 100}
        result = search_products(intent, self.store)
        reply, context = recommend("عايز فريش 100 ملي", result["products"], store=self.store,
                                   intent=intent, search=result)
        self.assertIn("حجم", reply)
        self.assertNotIn("طابع ريحة تاني", reply)
        self.assertNotIn("scent_tags=offered", context)

    def test_season_is_not_offered_as_a_requirement_to_remove(self):
        from products.services.sales import constraints, described

        intent = {"season": "summer"}
        self.assertEqual(constraints.describe_filters(intent), [])
        self.assertEqual(described.relax_offer_block(intent), "")

    def test_combined_budget_choices_agree_with_a_small_exhaustive_oracle(self):
        from products.services.sales.scent_selection import nearest_selection

        fixtures = [
            (self.perfume("Rich Cedar", ["fresh", "woody"], price=400), 0, 400),
            (self.perfume("Simple Cedar", ["fresh", "woody"], price=100), 0, 100),
            (self.perfume("Cedar Evening", ["fresh", "woody"], price=200, occasion="Evening"), 1, 200),
            (self.perfume("Fresh Office", price=150), 2, 150),
            (self.perfume("Fresh Evening", price=250, occasion="Evening"), 3, 250),
            (self.perfume("Fresh Winter", price=50, season="Winter"), 4, 50),
        ]
        tiers = {p.pk: tier for p, tier, _ in fixtures}
        prices = {p.pk: price for p, _, price in fixtures}
        for budget in (150, 250, 450, 600, 800, 1200):
            for count in (1, 2, 3, 4):
                with self.subTest(budget=budget, count=count):
                    intent = {"scent_tags": ["fresh", "woody"], "season": "summer", "occasion": "work",
                              "budget_scope": "total", "max_price": budget, "purchase_quantity": count}
                    feasible = [combo for combo in combinations(fixtures, count)
                                if sum(price for _, _, price in combo) <= budget]
                    selected = nearest_selection(list(search_products(intent, self.store)["products"]), intent, count)
                    if not feasible:
                        self.assertEqual(selected, [])
                    else:
                        expected = min(tuple(sorted(tier for _, tier, _ in combo)) for combo in feasible)
                        self.assertEqual(tuple(tiers[p.pk] for p in selected), expected)
                        self.assertLessEqual(sum(prices[p.pk] for p in selected), budget)


class NearestAlternativeTests(NearestCatalogMixin, TestCase):
    def unavailable_turn(self, preferences):
        from products.services.product_info import get_product_info
        from products.services.product_resolver import Resolution

        conversation = Conversation.objects.create(store=self.store, preferences=preferences)
        with patch("products.services.product_info.resolve_products", return_value=Resolution([], ["Unknown Fragrance"])), \
             patch("products.services.product_info.confirm_unplaced", return_value=None), \
             patch("products.services.product_info.chat", return_value="العطر مش متاح حالياً."):
            return get_product_info("عندكم Unknown Fragrance؟", [], self.store, conversation)

    def test_contextual_alternatives_keep_one_tag_matches_and_prefer_season(self):
        unrelated = self.perfume("Cheap Sweet", ["sweet"], price=50)
        winter = self.perfume("Winter Cedar", ["fresh", "woody"], price=100, season="Winter")
        summer = self.perfume("Summer Fresh", price=500, occasion="Evening")
        _, context = self.unavailable_turn({"gender": "unisex", "scent_tags": ["fresh", "woody"],
                                            "season": "summer", "occasion": "work"})
        self.assertIn(summer.name, context)
        self.assertIn(winter.name, context)
        self.assertNotIn(unrelated.name, context)
        self.assertLess(context.index(summer.name), context.index(winter.name))

    def test_contextual_alternatives_keep_current_ingredient_affinity_when_fits_tie(self):
        from products.services.fallback import suggest_alternatives

        plain = self.perfume("Plain Fresh", base_notes="Cedar")
        incense = self.perfume("Incense Fresh", base_notes="Incense")
        intent = {"gender": "unisex", "scent_tags": ["fresh"]}
        self.assertEqual(suggest_alternatives(self.store, notes=("incense",), limit=1,
                                              intent=intent), [incense])
        self.assertEqual(suggest_alternatives(self.store, limit=1, intent=intent), [plain])
        self.assertEqual(intent, {"gender": "unisex", "scent_tags": ["fresh"]})

    def test_ingredient_affinity_does_not_override_nearer_fit_or_combined_budget(self):
        from products.services.fallback import suggest_alternatives

        summer = self.perfume("Plain Summer", price=100)
        self.perfume("Incense Winter", price=100, base_notes="Incense", season="Winter")
        incense = self.perfume("Incense Summer", price=400, base_notes="Incense")
        self.perfume("Expensive Incense", price=500, base_notes="Incense")
        intent = {"gender": "unisex", "scent_tags": ["fresh"], "season": "summer",
                  "budget_scope": "total", "max_price": 500, "purchase_quantity": 2}
        self.assertEqual(suggest_alternatives(self.store, notes=("incense",), intent=intent),
                         [incense, summer])

    def test_contextual_total_budget_quotes_only_the_selected_affordable_sizes(self):
        first = self.perfume("Cedar One", price=100)
        second = self.perfume("Cedar Two", price=100)
        for product in (first, second):
            ProductVariant.objects.create(product=product, volume=100, price=400)
        preferences = {"gender": "unisex", "scent_tags": ["fresh"], "budget_scope": "total",
                       "max_price": 500, "purchase_quantity": 2}
        draft = "Cedar One 100 ملي بـ 400 جنيه. Cedar Two 100 ملي بـ 400 جنيه. الإجمالي 800 جنيه."
        for entrypoint in ("unavailable", "repetition"):
            with self.subTest(entrypoint=entrypoint):
                conversation = Conversation.objects.create(store=self.store, preferences=preferences)
                if entrypoint == "unavailable":
                    from products.services.product_resolver import Resolution
                    with patch("products.services.router.classify", return_value="product_info"), \
                         patch("products.services.router.confident_catalogue_match", return_value=None), \
                         patch("products.services.product_info.resolve_products",
                               return_value=Resolution([], ["Unknown Fragrance"])), \
                         patch("products.services.product_info.confirm_unplaced", return_value=None), \
                         patch("products.services.product_info.chat", return_value=draft):
                        reply, context = route("عندكم Unknown Fragrance؟", [], self.store, conversation)
                else:
                    with patch("products.services.router.classify", return_value="faq"), \
                         patch("products.services.router._detect_semantic_repetition", return_value=3), \
                         patch("products.services.router.confident_catalogue_match", return_value=None), \
                         patch("products.services.router.handle_general", return_value=(draft, "context")):
                        reply, context = route("ممكن معلومات؟", [], self.store, conversation)
                evidence = quote_evidence(context)
                self.assertEqual([(q["product_id"], q["volume"], Decimal(q["price"]))
                                  for q in evidence.get("quotes", [])],
                                 [(first.pk, 50, Decimal("100")), (second.pk, 50, Decimal("100"))])
                self.assertIn("200", reply)
                self.assertNotIn("400", reply)
                self.assertNotIn("800", reply)
                self.assertNotIn("100 ملي", reply)

    def test_contextual_alternatives_respect_saved_exclusions_size_and_budget(self):
        sweet = self.perfume("Sweet Fresh", ["fresh", "sweet"], price=100)
        wrong_size = self.perfume("Small Fresh", price=100)
        expensive = self.perfume("Expensive Fresh", price=100)
        ProductVariant.objects.create(product=expensive, volume=100, price=501)
        match = self.perfume("Larger Fresh", price=100)
        ProductVariant.objects.create(product=match, volume=100, price=500)
        _, context = self.unavailable_turn({"gender": "unisex", "scent_tags": ["fresh"],
                                            "excluded_scent_tags": ["sweet"], "requested_volume": 100,
                                            "max_price": 500, "budget_strict": True})
        self.assertIn(match.name, context)
        self.assertIn("100 ملي", context)
        self.assertNotIn("50 ملي", context)
        for rejected in (sweet, wrong_size, expensive):
            self.assertNotIn(rejected.name, context)

    def test_repetition_suggestions_retain_saved_scent_and_gender(self):
        wrong_scent = self.perfume("Cheap Sweet", ["sweet"], price=50)
        wrong_gender = self.perfume("Ladies Fresh", price=100, gender="female")
        match = self.perfume("Fresh Day", price=400, gender="male")
        conversation = Conversation.objects.create(store=self.store, preferences={"gender": "male", "scent_tags": ["fresh"]})
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router._detect_semantic_repetition", return_value=3), \
             patch("products.services.router.confident_catalogue_match", return_value=None), \
             patch("products.services.router.handle_general", side_effect=lambda message, *args: (message, "context")):
            reply, _ = route("ممكن معلومات؟", [], self.store, conversation)
        self.assertIn(match.name, reply)
        self.assertNotIn(wrong_scent.name, reply)
        self.assertNotIn(wrong_gender.name, reply)
