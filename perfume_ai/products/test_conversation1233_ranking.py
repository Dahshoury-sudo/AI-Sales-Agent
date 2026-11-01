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
        attributes = dict(gender="male", longevity="8 hours", projection="Strong")
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

    def test_daytime_fit_precedes_louder_evening_perfumes_already_offered(self):
        offered = {p.name for p in (self.elite, self.dark, self.bonded)}
        results = search_products(
            {"gender": "male", "occasion": "daytime", "projection": "strong",
             "longevity": "long-lasting"},
            self.store, keep=offered, offered=offered,
        )
        self.assertEqual(list(results["products"])[0], self.bonded)
        self.assertEqual(results["keeping"], [self.bonded.name])
        for perfume in (self.elite, self.dark):
            self.assertIn("Evening", results["dropped"][perfume.name])
            self.assertNotIn("العميل بيتكلم عنه بالفعل", results["ranked"][perfume.pk].reasons)
        # The quieter daytime scent's tradeoff must remain explicit.
        self.assertIn("فوحانه أقل", " ".join(results["ranked"][self.bonded.pk].mismatches))
        note = described.continuity_note(results["keeping"], results["dropped"], converge=True)
        self.assertIn("الكلام دلوقتي على: Bonded", note)
        self.assertNotIn("الكلام دلوقتي على: Elite", note)

    def test_evening_request_also_reassesses_a_previous_daytime_suggestion(self):
        results = search_products(
            {"gender": "male", "occasion": "night"}, self.store,
            keep={self.bonded.name}, offered={self.bonded.name},
        )
        self.assertEqual(results["keeping"], [])
        self.assertIn("Casual/Office/Everyday", results["dropped"][self.bonded.name])
        self.assertNotEqual(list(results["products"])[0], self.bonded)

    def test_blank_occasion_remains_unknown_without_false_withdrawal(self):
        unknown = self.product("Unknown occasion")
        results = search_products(
            {"gender": "male", "occasion": "morning"}, self.store,
            keep={unknown.name}, offered={unknown.name},
        )
        self.assertEqual(results["keeping"], [unknown.name])
        self.assertNotIn(unknown.name, results["dropped"])
        self.assertFalse(results["ranked"][unknown.pk].occasion_conflict)

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

    def test_similarity_reference_replaces_unrelated_continuity_anchors(self):
        reference = self.product(
            "Terre d'Hermes", top_notes="Orange, Grapefruit", middle_notes="Pepper",
            base_notes="Vetiver, Cedar, Patchouli", occasion="Daily",
        )
        close = self.product(
            "Vetiver citrus", top_notes="Orange, Grapefruit", middle_notes="Pepper",
            base_notes="Vetiver, Cedar", occasion="Daily",
        )
        intent = {"gender": "male", "similar_to": reference.name}
        results = search_products(
            intent, self.store, keep={self.elite.name, self.dark.name},
            offered={self.elite.name, self.dark.name},
        )
        self.assertEqual(list(results["products"])[0], close)
        self.assertEqual(results["keeping"], [])
        self.assertTrue(results["similarity"]["has_close_match"])
        self.assertIn(reference.name, results["dropped"][self.elite.name])
        # Rejected as lookalikes, not falsely declared unavailable.
        self.assertIn(self.elite, results["products"])
        self.assertNotIn(reference, results["products"])
        self.assertEqual(results["reference_product"], reference)

    def test_close_similarity_match_stays_above_fresh_unrelated_options(self):
        reference = similarity.reference_from_product(self.bonded)
        results = ranking.rank(
            [self.elite, self.bonded], {"gender": "male", "projection": "strong"},
            reference=reference, keep={self.elite.name}, offered={self.bonded.name},
        )
        self.assertEqual(results[0].product, self.bonded)
        self.assertFalse(results[1].result.is_close)
        self.assertNotIn("العميل بيتكلم عنه بالفعل", results[1].reasons)

    def test_no_close_match_has_no_forced_previous_choice(self):
        results = search_products(
            {"gender": "male", "similar_to": "Unstocked rose fragrance",
             "similar_to_notes": ["rose", "peony"]},
            self.store, keep={self.elite.name}, offered={self.elite.name},
        )
        self.assertFalse(results["similarity"]["has_close_match"])
        self.assertEqual(results["keeping"], [])
        self.assertTrue(results["products"].exists())

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
