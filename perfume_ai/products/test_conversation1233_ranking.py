"""Changed scent/occasion requests must reassess previously offered perfumes."""
from django.test import TestCase

from products.models import Brand, Product, ProductVariant, Store
from products.services.sales import described, ranking, similarity
from products.services.search_service import search_products


class Conversation1233RankingTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Occasion test")
        self.brand = Brand.objects.create(store=self.store, name=self.store.name)
        self.elite = self.product(
            "Elite", occasion="Evening", top_notes="Grapefruit, Mint",
            middle_notes="Cardamom, Lavender, Cinnamon", base_notes="Amber, Sandalwood",
        )
        self.dark = self.product(
            "Dark Aura", occasion="Evening/Formal", base_notes="Amber, Vanilla, Leather",
        )
        self.bonded = self.product(
            "Bonded", occasion="Casual/Office/Everyday", projection="Moderate",
            top_notes="Bergamot", base_notes="Musk, Cedar",
        )

    def product(self, name, **fields):
        attributes = dict(gender="male", longevity="8 hours", projection="Strong", scent_tags=["fresh"])
        attributes.update(fields)
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, **attributes,
        )
        ProductVariant.objects.create(product=product, volume=50, price=750)
        return product

    def test_morning_and_daytime_aliases_recognize_matching_and_conflicting_data(self):
        for asked in ("daytime", "morning", "day", "daily", "نهار", "الصبح"):
            with self.subTest(asked=asked):
                entries = ranking.rank([self.elite, self.bonded], {"occasion": asked})
                self.assertEqual(entries[0].product, self.bonded)
                self.assertIn("مناسب للمناسبة اللي قالها", entries[0].reasons)
                self.assertTrue(entries[1].occasion_conflict)
                self.assertIn("Evening", " ".join(entries[1].mismatches))

    def test_occasion_orders_matches_while_projection_does_not_filter(self):
        offered = {p.name for p in (self.elite, self.dark, self.bonded)}
        results = search_products({"gender": "male", "occasion": "daytime", "projection": "strong",
                                   "longevity": "long-lasting", "scent_tags": ["fresh"]},
                                  self.store, keep=offered, offered=offered)
        self.assertEqual(list(results["products"]), [self.bonded, self.elite, self.dark])
        self.assertEqual(results["keeping"], sorted(offered))
        self.assertIsNone(results["relax_key"])
        self.assertEqual(results["dropped"], {})

    def test_evening_request_also_reassesses_a_previous_daytime_suggestion(self):
        results = search_products(
            {"gender": "male", "occasion": "night"}, self.store,
            keep={self.bonded.name}, offered={self.bonded.name},
        )
        self.assertEqual(results["keeping"], [self.bonded.name])
        self.assertEqual(results["dropped"], {})
        self.assertNotEqual(list(results["products"])[0], self.bonded)

    def test_blank_occasion_keeps_the_product_below_matching_occasions(self):
        unknown = self.product("Unknown occasion")
        result = search_products({"occasion": "morning"}, self.store, keep={unknown.name})
        self.assertIn(unknown, result["products"])
        self.assertEqual(list(result["products"])[0], self.bonded)
        self.assertEqual(result["keeping"], [unknown.name])
        self.assertEqual(result["dropped"], {})

    def test_matching_morning_catalogue_value_satisfies_daily_request(self):
        morning = self.product("Morning scent", occasion="Morning")
        entry = ranking.rank([morning], {"occasion": "daily"})[0]
        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)
        self.assertFalse(entry.occasion_conflict)

    def test_day_alias_does_not_turn_holiday_or_birthday_into_daytime(self):
        for asked in ("day", "daily", "morning"):
            with self.subTest(asked=asked):
                self.assertEqual(ranking._occasion_verdict("Holiday", asked)[0], ranking.UNKNOWN)
                self.assertEqual(ranking._occasion_verdict("Birthday party", asked)[0], ranking.CONFLICT)

    def test_reference_labels_replace_unrelated_continuity_anchors(self):
        reference = self.product("Terre d'Hermes", scent_tags=["citrus", "woody"])
        match = self.product("Vetiver citrus", scent_tags=["citrus", "woody"])
        result = search_products({"similar_to": reference.name}, self.store, keep={self.elite.name, self.dark.name})
        self.assertEqual(list(result["products"]), [match])
        self.assertEqual(result["keeping"], [])
        self.assertIn(reference.name, result["dropped"][self.elite.name])
        self.assertEqual(result["reference_product"], reference)
        self.assertIsNone(result["similarity"])

    def test_close_similarity_match_stays_above_fresh_unrelated_options(self):
        reference = similarity.reference_from_product(self.bonded)
        results = ranking.rank(
            [self.elite, self.bonded], {"gender": "male", "projection": "strong"},
            reference=reference, keep={self.elite.name}, offered={self.bonded.name},
        )
        self.assertEqual(results[0].product, self.bonded)
        self.assertFalse(results[1].result.is_close)
        self.assertNotIn("العميل بيتكلم عنه بالفعل", results[1].reasons)

    def test_unknown_reference_cannot_force_a_previous_choice(self):
        result = search_products({"similar_to": "Unstocked rose fragrance", "similar_to_notes": ["rose"]},
                                  self.store, keep={self.elite.name}, offered={self.elite.name})
        self.assertTrue(result["needs_scent_reference"])
        self.assertFalse(result["products"].exists())
        self.assertEqual(result["keeping"], [])

    def test_budget_refinement_preserves_a_compatible_perfume(self):
        results = search_products(
            {"gender": "male", "occasion": "daily", "max_price": 800},
            self.store, keep={self.bonded.name}, offered={self.bonded.name},
        )
        self.assertEqual(results["keeping"], [self.bonded.name])
        self.assertEqual(results["dropped"], {})
        self.assertEqual(list(results["products"])[0], self.bonded)

    def test_new_short_requests_are_not_forced_into_convergence(self):
        history = [{"role": "assistant", "content": "تحب أجهزلك Elite؟"}]
        for message in ("عايز حاجة شبه Terre d'Hermes", "رشحلي حاجة للنهار", "recommend something for morning"):
            with self.subTest(message=message):
                self.assertFalse(described.is_follow_up(message, history, {self.elite.name}))
        self.assertTrue(described.is_follow_up("بالنهار أكتر", history, {self.elite.name}))
        self.assertTrue(described.is_follow_up("ترشحلي أنهي أحسن منهم", history, {self.elite.name}))
