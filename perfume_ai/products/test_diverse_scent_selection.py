"""Equally near perfumes are picked so they smell different beyond the requested scent.

Owner request (2026-11-20): "عايز برفان رجالي فريش" got two fresh perfumes that were both woody and
spicy, while the store has dozens of fresh perfumes with other characters. Every pick keeps the
requested scent; among equally near picks, the next one shares as few of the earlier picks' other
labels as possible. Nearest fit and the customer's stated preferences still come first.
"""
import json
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, ProductVariant
from products.services.ai.recommendation import recommend
from products.services.sales.scent_matching import distinct_selection_issue
from products.services.sales.scent_selection import nearest_selection
from products.services.scent_description import describe_scent
from products.services.search_service import search_products
from products.test_nearest_recommendations import NearestCatalogMixin


class DiverseCatalogMixin(NearestCatalogMixin):
    def pick(self, intent, count=2, **kwargs):
        candidates = list(search_products(intent, self.store)["products"])
        return [p.name for p in nearest_selection(candidates, intent, count, **kwargs)]

    def same_smell_catalog(self, **third):
        self.cedar_spice = self.perfume("Cedar Spice", ["fresh", "woody", "spicy"])
        self.cedar_pepper = self.perfume("Cedar Pepper", ["fresh", "woody", "spicy"])
        self.sea_lemon = self.perfume("Sea Lemon", ["fresh", "citrus", "aquatic"], **third)


class DiverseSelectionTests(DiverseCatalogMixin, TestCase):
    def test_second_pick_smells_different_beyond_the_requested_scent(self):
        self.same_smell_catalog()
        self.assertEqual(self.pick({"scent_tags": ["fresh"]}), ["Cedar Spice", "Sea Lemon"])

    def test_fewest_shared_labels_when_none_is_completely_different(self):
        self.perfume("Cedar Spice", ["fresh", "woody", "spicy"])
        self.perfume("Cedar Pepper", ["fresh", "woody", "spicy"])
        self.perfume("Cedar Lemon", ["fresh", "woody", "citrus"])
        self.assertEqual(self.pick({"scent_tags": ["fresh"]}), ["Cedar Spice", "Cedar Lemon"])

    def test_three_picks_avoid_every_earlier_pick(self):
        self.perfume("Cedar Spice", ["fresh", "woody", "spicy"])
        self.perfume("Sea Lemon", ["fresh", "citrus", "aquatic"])
        self.perfume("Light Cedar", ["fresh", "woody", "light"])
        self.perfume("Clean Musk", ["fresh", "musky", "clean"])
        self.assertEqual(self.pick({"scent_tags": ["fresh"]}, count=3),
                         ["Cedar Spice", "Sea Lemon", "Clean Musk"])

    def test_season_still_comes_before_a_different_smell(self):
        self.same_smell_catalog(season="Winter")
        self.assertEqual(self.pick({"scent_tags": ["fresh"], "season": "summer"}),
                         ["Cedar Spice", "Cedar Pepper"])

    def test_more_requested_labels_still_come_before_a_different_smell(self):
        self.same_smell_catalog()
        self.assertEqual(self.pick({"scent_tags": ["fresh", "woody"]}), ["Cedar Spice", "Cedar Pepper"])

    def test_previously_offered_still_comes_after_a_new_perfume(self):
        self.same_smell_catalog()
        self.assertEqual(self.pick({"scent_tags": ["fresh"]}, offered=("Sea Lemon",)),
                         ["Cedar Spice", "Cedar Pepper"])

    def test_refused_sweetness_still_comes_before_a_different_smell(self):
        self.same_smell_catalog(top_notes="Vanilla, Lemon")
        self.assertEqual(self.pick({"scent_tags": ["fresh"], "excluded_scent_tags": ["sweet"]}),
                         ["Cedar Spice", "Cedar Pepper"])

    def test_preferred_brand_still_comes_before_a_different_smell(self):
        self.same_smell_catalog()
        self.sea_lemon.brand = Brand.objects.create(store=self.store, name="Elsewhere")
        self.sea_lemon.save()
        candidates = list(search_products({"scent_tags": ["fresh"]}, self.store)["products"])
        chosen = nearest_selection(candidates, {"scent_tags": ["fresh"], "brand": "House"}, 2)
        self.assertEqual([p.name for p in chosen], ["Cedar Spice", "Cedar Pepper"])

    def test_combined_budget_bottles_smell_different(self):
        self.same_smell_catalog()
        intent = {"scent_tags": ["fresh"], "budget_scope": "total", "max_price": 1000, "purchase_quantity": 2}
        self.assertEqual(self.pick(intent), ["Cedar Spice", "Sea Lemon"])

    def test_the_message_names_only_the_repeated_other_labels(self):
        self.same_smell_catalog()
        candidates = [self.cedar_spice, self.cedar_pepper, self.sea_lemon]
        issue = distinct_selection_issue([self.cedar_spice, self.cedar_pepper], candidates, {"scent_tags": ["fresh"]})
        self.assertIn("woody, spicy", issue)
        self.assertNotIn("fresh,", issue)
        self.assertIn(f"Sea Lemon (id {self.sea_lemon.pk})", issue)
        self.assertIsNone(distinct_selection_issue([self.cedar_spice, self.sea_lemon], candidates,
                                                   {"scent_tags": ["fresh"]}))


class DiverseRecommendationTests(DiverseCatalogMixin, TestCase):
    def answer(self, *products):
        return json.dumps({"product_ids": [p.pk for p in products],
                           "reply": " ".join(f"{p.name}: {describe_scent(p.scent_tags)}." for p in products)},
                          ensure_ascii=False)

    def generate(self, intent, *answers):
        search = search_products(intent, self.store)
        with patch("products.services.ai.recommendation.chat", side_effect=list(answers)) as model:
            result = recommend("عايز برفان فريش", search["products"], store=self.store, intent=intent, search=search)
        return result, model

    def test_a_same_smell_pair_is_retried_with_the_different_perfume_named(self):
        self.same_smell_catalog()
        (reply, context), model = self.generate(
            {"scent_tags": ["fresh"]},
            self.answer(self.cedar_spice, self.cedar_pepper), self.answer(self.cedar_spice, self.sea_lemon))
        self.assertEqual(model.call_count, 2)
        retry = model.call_args_list[1].args[0][-1]["content"]
        self.assertIn("woody, spicy", retry)
        self.assertIn("Sea Lemon", retry)
        self.assertIn("Sea Lemon", reply)
        self.assertNotIn("Cedar Pepper", reply + context)

    def test_a_repeated_same_smell_pair_falls_back_to_different_perfumes(self):
        self.same_smell_catalog()
        same = self.answer(self.cedar_spice, self.cedar_pepper)
        (reply, context), model = self.generate({"scent_tags": ["fresh"]}, same, same)
        self.assertEqual(model.call_count, 2)
        self.assertIn("Cedar Spice", reply)
        self.assertIn("Sea Lemon", reply)
        self.assertNotIn("Cedar Pepper", reply + context)

    def test_any_equally_near_first_perfume_is_accepted(self):
        self.same_smell_catalog()
        for first, second in ((self.cedar_pepper, self.sea_lemon), (self.sea_lemon, self.cedar_spice)):
            with self.subTest(first=first.name):
                (reply, _), model = self.generate({"scent_tags": ["fresh"]}, self.answer(first, second))
                self.assertEqual(model.call_count, 1)
                self.assertIn(first.name, reply)
                self.assertIn(second.name, reply)

    def test_a_single_pick_is_never_refused_for_its_smell(self):
        self.same_smell_catalog()
        (reply, _), model = self.generate({"scent_tags": ["fresh"], "recommendation_count": 1},
                                          self.answer(self.cedar_pepper))
        self.assertEqual(model.call_count, 1)
        self.assertIn("Cedar Pepper", reply)
