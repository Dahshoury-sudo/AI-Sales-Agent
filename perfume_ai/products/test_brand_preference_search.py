"""Direct eligibility preserves soft brand preferences and strict brand restrictions."""

from django.test import TestCase

from products.models import Brand, Product, ProductVariant, Store
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
            "projection": "strong", "longevity": "long-lasting", "scent_tags": ["fresh"],
        }

    def product(self, name, brand, *, price=750, volume=50, bottle_type="normal", stock=None, **fields):
        attrs = {"gender": "male", "occasion": "Office, Daily", "season": "All Seasons",
                 "perfume_type": "western", "projection": "Strong", "longevity": "8 hours",
                 "top_notes": "Bergamot", "base_notes": "Cedar", "scent_tags": ["fresh", "woody"], **fields}
        product = Product.objects.create(store=brand.store, brand=brand, name=name, **attrs)
        ProductVariant.objects.create(product=product, volume=volume, price=price,
                                      bottle_type=bottle_type, stock=stock)
        return product

    @staticmethod
    def selected(result):
        return list(result["products"]) or list(result.get("alternatives") or ())

    def test_performance_requirement_preserves_previously_discussed_perfumes(self):
        result = search_products(self.intent, self.store, keep={self.mild.name}, offered={self.strong.name})
        self.assertEqual(set(self.selected(result)), {self.mild, self.strong})
        self.assertIn(self.mild.name, result["keeping"])
        self.assertEqual(result["preferred_brand"], "Perfamix")
        self.assertFalse(result["brand_strict"])
        self.assertEqual(result["cross_brand_ids"], [self.strong.pk])
        self.assertNotIn(self.mild.name, result["dropped"])
        self.assertEqual(result["ranked"], {})

    def test_all_matching_brands_remain_available_for_ai_selection(self):
        self.mild.projection = "Strong"
        self.mild.save(update_fields=["projection"])
        result = search_products(self.intent, self.store, keep={self.strong.name}, offered={self.mild.name})
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertEqual(set(self.selected(result)), {self.mild, self.strong})
        self.assertEqual(result["keeping"], [self.strong.name])

    def test_ordinary_named_house_is_also_a_soft_preference(self):
        better = self.product("Longer daytime", self.chanel, longevity="12 hours")
        intent = {**self.intent, "brand": "Dior"}
        result = search_products(intent, self.store, keep={self.strong.name})
        self.assertEqual(set(self.selected(result)), {self.mild, self.strong, better})
        self.assertEqual(result["preferred_brand"], "Dior")
        self.assertIn(better.pk, result["cross_brand_ids"])
        self.assertNotIn(self.strong.pk, result["cross_brand_ids"])

    def test_explicit_only_brand_stays_a_filter(self):
        for brand, expected in (("STORE_BRAND_EXCLUSIVE", self.mild), ("Dior", self.strong)):
            with self.subTest(brand=brand):
                result = search_products({**self.intent, "projection": None, "brand": brand, "brand_strict": True}, self.store)
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

    def test_no_brand_uses_the_same_uniform_performance_policy(self):
        intent = {k: v for k, v in self.intent.items() if k != "brand"}
        result = search_products(intent, self.store, keep={self.mild.name})
        self.assertEqual(set(self.selected(result)), {self.mild, self.strong})
        self.assertFalse(result["preferred_brand"])
        self.assertEqual(result["cross_brand_ids"], [])
        self.assertFalse(result["brand_strict"])

    def test_excluded_unavailable_and_other_store_products_never_enter_pool(self):
        unavailable = self.product("Unavailable", self.chanel, bottle_type="original", stock=0, longevity="12 hours")
        inactive = self.product("Inactive", self.chanel, is_active=False, longevity="12 hours")
        other_store = Store.objects.create(name="Other store")
        other_brand = Brand.objects.create(store=other_store, name="Chanel")
        foreign = self.product("Foreign store", other_brand, longevity="12 hours")
        result = search_products({**self.intent, "projection": None, "exclude_brands": ["Dior"]}, self.store)
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

    def test_no_match_does_not_offer_products_missing_a_required_ingredient(self):
        result = search_products({**self.intent, "notes": ["oud"]}, self.store, keep={self.mild.name})
        self.assertFalse(result["products"].exists())
        self.assertIsNone(result["alternatives"])
        self.assertEqual(result["cross_brand_ids"], [])

    def test_brand_preference_does_not_filter_other_brands(self):
        self.mild.variants.update(price=900)
        result = search_products({"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE"}, self.store)
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertIn(self.strong, self.selected(result))
        self.assertEqual(result["preferred_brand"], "Perfamix")

    def test_preferred_brand_is_considered_beyond_cheapest_candidate_cap(self):
        self.mild.variants.update(price=900)
        for index in range(65):
            self.product(f"Other option {index}", self.dior, price=100)
        result = search_products({"gender": "male", "brand": "STORE_BRAND_EXCLUSIVE"}, self.store)
        self.assertEqual(self.selected(result)[0], self.mild)
        self.assertIn(self.strong, self.selected(result))
        self.assertEqual(len(self.selected(result)), 67)

    def test_a_foreign_close_scent_match_beats_unrelated_requested_brand(self):
        reference = self.product("Reference", self.chanel, scent_tags=["citrus", "woody"])
        close = self.product("Close fragrance", self.dior, scent_tags=["citrus", "woody"])
        result = search_products({**self.intent, "scent_tags": [], "similar_to": reference.name}, self.store, keep={self.mild.name})
        self.assertEqual(self.selected(result)[0], close)
        self.assertIsNone(result["similarity"])
        self.assertNotIn(reference, self.selected(result))
        self.assertEqual(result["reference_product"], reference)

    def test_evening_perfume_cannot_win_daytime_request_on_brand_or_strength(self):
        evening = self.product("Evening blend", self.own, occasion="Evening", longevity="12 hours")
        result = search_products(self.intent, self.store, keep={evening.name})
        self.assertEqual(set(self.selected(result)), {self.mild, self.strong})
        self.assertNotIn(evening, self.selected(result))
        self.assertIn("Evening", result["dropped"][evening.name])

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
