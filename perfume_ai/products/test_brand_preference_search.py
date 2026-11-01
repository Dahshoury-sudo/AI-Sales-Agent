"""Requested brands guide ties; product suitability decides soft-brand recommendations."""
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Product, ProductVariant, Store
from products.services.sales import ranking
from products.services.search_service import search_products


class BrandPreferenceSearchTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Perfamix")
        self.own = Brand.objects.create(store=self.store, name="Perfamix")
        self.dior = Brand.objects.create(store=self.store, name="Dior")
        self.chanel = Brand.objects.create(store=self.store, name="Chanel")
        self.mild = self.product("Daily blend", self.own, projection="Moderate")
        self.strong = self.product("Daytime fragrance", self.dior)
        self.intent = {
            "brand": "STORE_BRAND_EXCLUSIVE", "gender": "male", "occasion": "daytime",
            "projection": "strong", "longevity": "long-lasting",
        }

    def product(self, name, brand, *, price=750, volume=50, bottle_type="normal", stock=None, **fields):
        attrs = {"gender": "male", "occasion": "Office", "season": "All Seasons",
                 "perfume_type": "western", "projection": "Strong", "longevity": "8 hours",
                 "top_notes": "Bergamot", "base_notes": "Cedar", **fields}
        product = Product.objects.create(store=brand.store, brand=brand, name=name, **attrs)
        ProductVariant.objects.create(product=product, volume=volume, price=price,
                                      bottle_type=bottle_type, stock=stock)
        return product

    @staticmethod
    def selected(result):
        return list(result["products"]) or list(result.get("alternatives") or ())

    def test_better_fit_leads_despite_brand_continuity_and_repeat_penalty(self):
        result = search_products(self.intent, self.store, keep={self.mild.name}, offered={self.strong.name})
        self.assertEqual(self.selected(result)[0], self.strong)
        self.assertIn(self.mild.name, result["keeping"])
        self.assertEqual(result["preferred_brand"], "Perfamix")
        self.assertFalse(result["brand_strict"])
        self.assertEqual(result["cross_brand_ids"], [self.strong.pk])
        own_entry, other_entry = result["ranked"][self.mild.pk], result["ranked"][self.strong.pk]
        self.assertLess(other_entry.score, own_entry.score)
        self.assertLess(ranking.preference_fit_key(other_entry), ranking.preference_fit_key(own_entry))

    def test_equally_suitable_preferred_brand_wins_before_continuity(self):
        self.mild.projection = "Strong"
        self.mild.save(update_fields=["projection"])
        result = search_products(self.intent, self.store, keep={self.strong.name}, offered={self.mild.name})
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertEqual(result["ranked"][self.mild.pk].fit_score, result["ranked"][self.strong.pk].fit_score)

    def test_ordinary_named_house_is_also_a_soft_preference(self):
        better = self.product("Longer daytime", self.chanel, longevity="12 hours")
        intent = {**self.intent, "brand": "Dior"}
        result = search_products(intent, self.store, keep={self.strong.name})
        self.assertEqual(self.selected(result)[0], better)
        self.assertEqual(result["preferred_brand"], "Dior")
        self.assertIn(better.pk, result["cross_brand_ids"])
        self.assertNotIn(self.strong.pk, result["cross_brand_ids"])

    def test_explicit_only_brand_stays_a_filter(self):
        for brand, expected in (("STORE_BRAND_EXCLUSIVE", self.mild), ("Dior", self.strong)):
            with self.subTest(brand=brand):
                result = search_products({**self.intent, "brand": brand, "brand_strict": True}, self.store)
                self.assertEqual(self.selected(result), [expected])
                self.assertTrue(result["brand_strict"])
                self.assertEqual(result["cross_brand_ids"], [])

    def test_strict_brand_with_no_products_does_not_silently_broaden(self):
        result = search_products({"gender": "male", "brand": "Chanel", "brand_strict": True}, self.store)
        self.assertEqual(self.selected(result), [])
        self.assertEqual(result["preferred_brand"], "Chanel")
        self.assertTrue(result["brand_strict"])
        self.assertEqual(result["cross_brand_ids"], [])

    def test_soft_brand_does_not_falsely_withdraw_a_foreign_product(self):
        result = search_products(self.intent, self.store, keep={self.strong.name})
        self.assertIn(self.strong.name, result["keeping"])
        self.assertNotIn(self.strong.name, result["dropped"])
        result = search_products({**self.intent, "brand_strict": True}, self.store, keep={self.strong.name})
        self.assertIn("البراند", result["dropped"][self.strong.name])

    def test_no_brand_keeps_existing_continuity_ranking_and_metadata(self):
        intent = {k: v for k, v in self.intent.items() if k != "brand"}
        result = search_products(intent, self.store, keep={self.mild.name})
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertFalse(result["preferred_brand"])
        self.assertEqual(result["cross_brand_ids"], [])
        self.assertFalse(result["brand_strict"])

    def test_excluded_unavailable_and_other_store_products_never_enter_pool(self):
        unavailable = self.product("Unavailable", self.chanel, bottle_type="original", stock=0, longevity="12 hours")
        inactive = self.product("Inactive", self.chanel, is_active=False, longevity="12 hours")
        other_store = Store.objects.create(name="Other store")
        other_brand = Brand.objects.create(store=other_store, name="Chanel")
        foreign = self.product("Foreign store", other_brand, longevity="12 hours")
        result = search_products({**self.intent, "exclude_brands": ["Dior"]}, self.store)
        self.assertEqual(self.selected(result), [self.mild])
        self.assertEqual(result["cross_brand_ids"], [])
        self.assertFalse({unavailable.pk, inactive.pk, foreign.pk} & set(result.get("ranked", {})))

    def test_soft_brand_keeps_gender_type_season_and_same_variant_requirements(self):
        eligible = self.product("Eligible other brand", self.chanel, volume=90, price=800)
        self.product("Wrong gender", self.chanel, volume=90, gender="female")
        self.product("Wrong category", self.chanel, volume=90, perfume_type="oriental")
        self.product("Wrong season", self.chanel, volume=90, season="Winter")
        self.product("Over budget", self.chanel, volume=90, price=801)
        mismatched = self.product("Split variant eligibility", self.chanel, volume=50, price=500)
        ProductVariant.objects.create(product=mismatched, volume=90, price=800, bottle_type="original", stock=0)
        intent = {**self.intent, "season": "summer", "perfume_type": "western",
                  "requested_volume": 90, "bottle_type": "normal", "max_price": 800, "budget_strict": True}
        result = search_products(intent, self.store)
        self.assertEqual(self.selected(result), [eligible])
        self.assertEqual(result["cross_brand_ids"], [eligible.pk])

    def test_alternatives_branch_also_exposes_foreign_brand_and_fit_order(self):
        result = search_products({**self.intent, "notes": ["oud"]}, self.store, keep={self.mild.name})
        self.assertFalse(result["products"].exists())
        self.assertEqual(list(result["alternatives"])[0], self.strong)
        self.assertEqual(result["cross_brand_ids"], [self.strong.pk])

    def test_preferred_brand_only_request_uses_brand_tie_break(self):
        self.mild.variants.update(price=900)
        result = search_products({"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE"}, self.store)
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertEqual(result["preferred_brand"], "Perfamix")

    def test_preferred_brand_is_considered_beyond_cheapest_candidate_cap(self):
        self.mild.variants.update(price=900)
        with patch("products.services.search_service.MAX_CANDIDATES_TO_SCORE", 1):
            result = search_products({"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE"}, self.store)
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertIn(self.strong, self.selected(result))

    def test_a_foreign_close_scent_match_beats_unrelated_requested_brand(self):
        reference = self.product("Reference", self.chanel, top_notes="Orange, Grapefruit", base_notes="Vetiver, Patchouli")
        close = self.product("Close fragrance", self.dior, top_notes=reference.top_notes, base_notes=reference.base_notes)
        result = search_products({**self.intent, "similar_to": reference.name}, self.store, keep={self.mild.name})
        self.assertEqual(self.selected(result)[0], close)
        self.assertTrue(result["similarity"]["has_close_match"])
        self.assertNotIn(reference, self.selected(result))
        self.assertEqual(result["reference_product"], reference)

    def test_evening_perfume_cannot_win_daytime_request_on_brand_or_strength(self):
        evening = self.product("Evening blend", self.own, occasion="Evening", longevity="12 hours")
        result = search_products(self.intent, self.store, keep={evening.name})
        self.assertEqual(self.selected(result)[0], self.strong)
        self.assertTrue(result["ranked"][evening.pk].occasion_conflict)

    def test_no_signal_empty_result_still_has_brand_metadata(self):
        result = search_products({"gender": "female"}, self.store)
        self.assertEqual(self.selected(result), [])
        self.assertEqual(result["cross_brand_ids"], [])
        self.assertFalse(result["preferred_brand"])
        self.assertFalse(result["brand_strict"])

    def test_soft_brand_is_described_as_preference_not_a_filter_to_relax(self):
        from products.services.sales import constraints, described

        for brand, wording in (("Dior", "بيفضل Dior"), ("STORE_BRAND_EXCLUSIVE", "بيفضل تركيباتنا الخاصة")):
            with self.subTest(brand=brand):
                intent = {"brand": brand}
                self.assertIn(wording, constraints.describe(intent))
                self.assertEqual(constraints.describe_filters(intent), [])
                self.assertEqual(described.relax_offer_block(intent), "")
                strict = {**intent, "brand_strict": True}
                self.assertTrue(constraints.describe_filters(strict))
                self.assertIn("brand=" + brand, described.relax_offer_block(strict))

    def test_excluded_brands_remain_hard_in_relaxation_context(self):
        from products.services.sales import constraints, described

        intent = {"brand": "Dior", "exclude_brands": ["Chanel"]}
        self.assertEqual(constraints.describe_filters(intent), ["من غير Chanel"])
        block = described.relax_offer_block(intent)
        self.assertIn("exclude_brands=Chanel", block)
        self.assertNotIn("brand=Dior", block)
