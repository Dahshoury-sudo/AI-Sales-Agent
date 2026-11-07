"""Customer scent wording survives extraction, memory, and catalog filtering."""

import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store
from products.occasions import WORK
from products.scent_tags import normalize_scent_tags
from products.services.ai.intent import extract_intent
from products.services.conversation_service import merge_preferences
from products.services.reply_sanitizer import product_claim_issues
from products.services.sales.scent_preferences import normalize_scent_intent, reconcile_scent_intent
from products.services.search_service import search_products


HISTORY = [
    {"role": "user", "content": "رشحلي عطر رجالي"},
    {"role": "assistant", "content": "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه"},
]


def evidence(text, *tags, polarity="positive"):
    return {"text": text, "tags": list(tags), "polarity": polarity}


class ScentExtractionTests(SimpleTestCase):
    def extract(self, message, response=None, *, failure=None, history=None):
        payload = json.dumps(response or {}, ensure_ascii=False) if not isinstance(response, str) else response
        with patch("products.services.ai.intent.chat", return_value=payload, side_effect=failure):
            return extract_intent(message, history=HISTORY if history is None else history)

    def assert_scents(self, result, wanted=(), excluded=()):
        self.assertEqual(set(result.get("scent_tags") or []), set(wanted))
        self.assertEqual(set(result.get("excluded_scent_tags") or []), set(excluded))

    def test_known_arabic_answers_do_not_depend_on_the_model_remembering_labels(self):
        for message, wanted in (
            ("تبغي", {"smoky"}), ("تبغى", {"smoky"}),
            ("توابل", {"spicy"}), ("حمضي", {"citrus"}),
            ("تبغي وحمضي", {"smoky", "citrus"}),
            ("تبغي وتوابل", {"smoky", "spicy"}),
            ("فريش وصابوني", {"fresh", "clean"}),
            ("زهرية وحمضية ونضيفة", {"floral", "citrus", "clean"}),
        ):
            with self.subTest(message=message):
                self.assert_scents(self.extract(message), wanted)

    def test_known_tobacco_word_replaces_the_wrong_model_interpretation(self):
        result = self.extract("تبغي وحمضي", {
            "scent_tags": ["spicy", "citrus"],
            "scent_evidence": [evidence("تبغي", "spicy"), evidence("حمضي", "citrus")],
        })
        self.assert_scents(result, {"smoky", "citrus"})

    def test_sugary_preferences_mean_sweet_without_model_labels(self):
        for message in ("شوجري", "عايز برفان شوجري", "sugary", "I want a sugary perfume"):
            with self.subTest(message=message):
                self.assert_scents(self.extract(message), {"sweet"})

    def test_negation_survives_correct_model_extraction(self):
        result = self.extract("فريش ومش مسكر", {
            "scent_tags": ["fresh"], "excluded_scent_tags": ["sweet"],
            "scent_evidence": [evidence("فريش", "fresh"), evidence("مش مسكر", "sweet", polarity="negative")],
        })
        self.assert_scents(result, {"fresh"}, {"sweet"})

    def test_dislikes_lists_and_contrast_keep_their_polarity(self):
        for message, wanted, excluded in (
            ("مش بحب العود", set(), {"oud"}),
            ("مش بحب الحاجات المسكرة", set(), {"sweet"}),
            ("مش عايز حاجات مسكره", set(), {"sweet"}),
            ("مش عايز شوجري", set(), {"sweet"}),
            ("no sugary", set(), {"sweet"}),
            ("مش عايز عود ولا سويت", set(), {"oud", "sweet"}),
            ("مش بحب العود بس عايز فريش", {"fresh"}, {"oud"}),
            ("فريش ومش مسكر", {"fresh"}, {"sweet"}),
            ("حمضي ومش توابل", {"citrus"}, {"spicy"}),
            ("مش عايز تبغي", set(), {"smoky"}),
            ("without oud and sweet", set(), {"oud", "sweet"}),
            ("not too heavy", set(), {"heavy"}),
        ):
            with self.subTest(message=message):
                self.assert_scents(self.extract(message), wanted, excluded)

    def test_egyptian_dislike_spellings_exclude_the_scent(self):
        for message in ("مبحبش العود", "ما بحبش العود"):
            with self.subTest(message=message):
                self.assert_scents(self.extract(message), excluded={"oud"})

    def test_last_explicit_stance_wins_in_both_orders(self):
        for message, wanted, excluded in (
            ("عايز سويت بس مش مسكر", set(), {"sweet"}),
            ("مش عايز سويت بس عايز مسكر", {"sweet"}, set()),
        ):
            with self.subTest(message=message):
                result = self.extract(message, {"scent_tags": ["sweet"], "excluded_scent_tags": ["sweet"]})
                self.assert_scents(result, wanted, excluded)

    def test_explicit_no_preference_keeps_the_stated_exclusion(self):
        for message in ("أي ريحة بس مش عودي", "any scent except oud"):
            with self.subTest(message=message):
                result = self.extract(message)
                self.assertTrue(result["scent_open"])
                self.assert_scents(result, excluded={"oud"})

    def test_wrong_model_polarity_cannot_turn_a_dislike_into_a_preference(self):
        result = self.extract("مش بحب العود", {
            "scent_tags": ["oud"],
            "scent_evidence": [evidence("العود", "oud")],
        })
        self.assert_scents(result, excluded={"oud"})

    def test_plural_dislikes_keep_the_models_correct_negative_evidence(self):
        message = "مش بحب الحاجات المسكرة"
        result = self.extract(message, {
            "excluded_scent_tags": ["sweet"],
            "scent_evidence": [evidence(message, "sweet", polarity="negative")],
        })
        self.assert_scents(result, excluded={"sweet"})

    def test_negated_multiword_scent_keeps_a_connected_known_scent_negative(self):
        for phrase, tag in (("ريحة الملايات المغسولة", "clean"), ("زهور بيضا", "floral")):
            with self.subTest(phrase=phrase):
                result = self.extract(f"مش عايز {phrase} وفريش", {
                    "excluded_scent_tags": [tag, "fresh"],
                    "scent_evidence": [
                        evidence(phrase, tag, polarity="negative"),
                        evidence("فريش", "fresh", polarity="negative"),
                    ],
                })
                self.assert_scents(result, excluded={tag, "fresh"})

    def test_multiword_negation_stops_at_a_new_positive_request(self):
        result = self.extract("مش عايز ريحة الملايات المغسولة وفريش بس عايز توابل", {
            "scent_tags": ["spicy"], "excluded_scent_tags": ["clean", "fresh"],
            "scent_evidence": [
                evidence("ريحة الملايات المغسولة", "clean", polarity="negative"),
                evidence("فريش", "fresh", polarity="negative"), evidence("توابل", "spicy"),
            ],
        })
        self.assert_scents(result, {"spicy"}, {"clean", "fresh"})

    def test_malformed_or_unavailable_ai_preserves_known_positive_and_negative_words(self):
        for response in ("invalid JSON", "[]", '{"scent_tags":"sweet"}'):
            with self.subTest(response=response):
                self.assert_scents(self.extract("فريش ومش مسكر", response), {"fresh"}, {"sweet"})
        self.assert_scents(
            self.extract("فريش ومش مسكر", failure=RuntimeError("Simulated unavailable extractor")),
            {"fresh"}, {"sweet"},
        )

    def test_evidenced_unfamiliar_phrase_survives_alongside_a_known_alias(self):
        result = self.extract("فريش وإحساس الملايات المغسولة", {
            "scent_tags": ["fresh", "clean"],
            "scent_evidence": [evidence("فريش", "fresh"), evidence("إحساس الملايات المغسولة", "clean")],
        })
        self.assert_scents(result, {"fresh", "clean"})

    def test_unfamiliar_evidenced_phrase_survives_new_goal_preparation(self):
        result = self.extract("recommend a breezy perfume for work", {
            "scent_tags": ["fresh"], "occasion": WORK, "occasion_text": "work",
            "scent_evidence": [evidence("breezy", "fresh")],
        })
        self.assert_scents(result, {"fresh"})
        self.assertEqual(result["occasion"], WORK)

    def test_repeated_reconciliation_keeps_the_same_interpretation(self):
        message = "تبغي وإحساس الملايات المغسولة ومش مسكر"
        intent = {
            "scent_tags": ["spicy", "clean", "sweet"],
            "scent_evidence": [
                evidence("تبغي", "spicy"), evidence("إحساس الملايات المغسولة", "clean"),
                evidence("مش مسكر", "sweet", polarity="negative"),
            ],
        }
        reconciled = reconcile_scent_intent(intent, message, require_evidence=True)
        self.assert_scents(reconciled, {"smoky", "clean"}, {"sweet"})
        for _ in range(3):
            repeated = reconcile_scent_intent(reconciled, message)
            self.assertEqual(repeated, reconciled)
            reconciled = repeated

    def test_evidence_must_come_from_the_latest_message(self):
        result = self.extract("ميزانيتي 800", {
            "scent_tags": ["fresh", "sweet"],
            "scent_evidence": [evidence("فريش", "fresh"), evidence("مسكر", "sweet")],
        })
        self.assert_scents(result)

    def test_model_only_tags_require_usable_evidence(self):
        for supplied_evidence in (None, "clean", [{"text": "إحساس الملايات المغسولة", "tags": "clean"}]):
            with self.subTest(evidence=supplied_evidence):
                result = self.extract("إحساس الملايات المغسولة", {
                    "scent_tags": ["clean"], "scent_evidence": supplied_evidence,
                })
                self.assert_scents(result)

    def test_invalid_model_labels_do_not_remove_valid_labels_from_the_same_phrase(self):
        result = self.extract("إحساس الملايات المغسولة", {
            "scent_tags": ["clean", "invented"],
            "scent_evidence": [evidence("إحساس الملايات المغسولة", "clean", "invented")],
        })
        self.assert_scents(result, {"clean"})
        self.assertEqual(normalize_scent_intent({"scent_tags": ["clean", "invented"]})["scent_tags"], ["clean"])
        with self.assertRaises(ValidationError):
            normalize_scent_tags(["clean", "invented"])

    def test_explicit_alternatives_apply_to_known_and_model_interpreted_phrases(self):
        for connector, expected in ((" و", "all"), (" أو ", "any")):
            with self.subTest(connector=connector):
                result = self.extract(f"فريش{connector}إحساس الملايات المغسولة", {
                    "scent_tags": ["fresh", "clean"], "scent_match": expected,
                    "scent_evidence": [evidence("فريش", "fresh"), evidence("إحساس الملايات المغسولة", "clean")],
                })
                self.assert_scents(result, {"fresh", "clean"})
                self.assertEqual(result["scent_match"], expected)

    def test_paired_egyptian_alternatives_do_not_require_both_scents(self):
        for message, expected in (("يا فريش يا خشبي", "any"), ("فريش يا صاحبي وخشبي", "all")):
            with self.subTest(message=message):
                result = self.extract(message, {
                    "scent_tags": ["fresh", "woody"], "scent_match": expected,
                    "scent_evidence": [evidence("فريش", "fresh"), evidence("خشبي", "woody")],
                })
                self.assert_scents(result, {"fresh", "woody"})
                self.assertEqual(result["scent_match"], expected)

    def test_short_choices_resolve_against_the_actual_pending_question(self):
        for message, expected, match in (
            ("اول حاجة", {"fresh"}, "all"), ("أول واحدة", {"fresh"}, "all"),
            ("التانية", {"woody"}, "all"), ("التالتة", {"oud"}, "all"),
            ("آخر حاجة", {"sweet"}, "all"), ("الأول والتاني", {"fresh", "woody"}, "all"),
            ("الأول أو التاني", {"fresh", "woody"}, "any"), ("٢", {"woody"}, "all"),
        ):
            with self.subTest(message=message):
                result = self.extract(message)
                self.assert_scents(result, expected)
                self.assertEqual(result["scent_match"], match)
        self.assert_scents(self.extract("آخر حاجة", failure=RuntimeError("Unavailable extractor")), {"sweet"})

    def test_short_choices_do_not_use_a_stale_scent_question(self):
        for latest in (
            {"role": "assistant", "content": "تحب زجاجة 50 ولا 100 ملي؟"},
            {"role": "agent", "content": "اختار أول منتج أو آخر منتج"},
        ):
            with self.subTest(latest=latest):
                self.assert_scents(self.extract("آخر حاجة", history=[*HISTORY, latest]))
        self.assert_scents(self.extract("اول حاجة ميزانيتي 800"))

    def test_delegating_the_pending_scent_question_does_not_delegate_an_occasion(self):
        for message in ("على ذوقك", "اختارلي انت", "you choose"):
            with self.subTest(message=message):
                result = self.extract(message, {"occasion_delegated": True})
                self.assertTrue(result.get("scent_open"))
                self.assert_scents(result)
                self.assertFalse(result.get("occasion"))
                self.assertFalse(result.get("occasion_delegated"))
        self.assertFalse(self.extract("اختارلي المناسبة", {"occasion_delegated": True}).get("scent_open"))
        for message in ("اه", "مش عارف"):
            with self.subTest(message=message):
                self.assertFalse(self.extract(message).get("scent_open"))

    def test_a_quote_of_generic_praise_is_not_evidence_for_sweetness(self):
        for message in ("حاجة حلوة", "عايز عطر حلو", "nice", "something good"):
            with self.subTest(message=message):
                result = self.extract(message, {
                    "scent_tags": ["sweet"], "scent_evidence": [evidence(message, "sweet")],
                })
                self.assert_scents(result)

    def test_replacement_source_cannot_return_through_model_evidence(self):
        result = self.extract("بدل فريش خلينا سويت", {
            "scent_tags": ["fresh", "sweet"],
            "scent_evidence": [evidence("فريش", "fresh"), evidence("سويت", "sweet")],
        })
        self.assert_scents(result, {"sweet"})

    def test_clearing_the_old_style_does_not_erase_a_sourced_replacement(self):
        for message in ("بدل فريش خلينا سويت", "بدل طابع الفريش خلينا سويت", "sweet instead of fresh"):
            with self.subTest(message=message):
                result = self.extract(message, {
                    "scent_tags": ["sweet"], "clear_preferences": ["scent_tags"],
                    "scent_evidence": [evidence("sweet" if "sweet" in message else "سويت", "sweet")],
                })
                self.assert_scents(result, {"sweet"})
                self.assertNotIn("scent_tags", result.get("clear_preferences") or [])

    def test_vanilla_preference_means_sweet_without_an_ingredient_filter(self):
        for message in ("فانيليا", "عايز ريحة زي الفانيليا"):
            with self.subTest(message=message):
                result = self.extract(message, {"scent_tags": ["sweet"], "notes": ["vanilla"]})
                self.assert_scents(result, {"sweet"})
                self.assertFalse(result.get("notes"))
                self.assertFalse(result.get("similar_to"))
        self.assert_scents(self.extract("فانيليا", failure=RuntimeError("Unavailable")), {"sweet"})

    def test_explicit_ingredients_do_not_imply_a_style_requirement(self):
        for message, note, wrong_tag in (
            ("لازم يكون فيه فانيليا", "vanilla", "sweet"),
            ("لازم يكون فيه تبغ", "tobacco", "smoky"),
        ):
            with self.subTest(message=message):
                result = self.extract(message, {"notes": [note], "scent_tags": [wrong_tag]})
                self.assert_scents(result)
                self.assertEqual(result.get("notes"), [note])

    def test_literal_ingredient_exclusions_do_not_exclude_the_broader_style(self):
        for message, note, style in (
            ("مفيهوش فانيليا", "vanilla", "sweet"),
            ("مش عايز يكون فيه تبغ", "tobacco", "smoky"),
        ):
            with self.subTest(message=message):
                result = self.extract(message, {
                    "notes": [note], "scent_tags": [style], "excluded_scent_tags": [style],
                })
                self.assert_scents(result)
                self.assertFalse(result.get("notes"))
                self.assertEqual(result.get("avoid_notes"), [note])

    def test_named_reference_does_not_become_a_scent_or_ingredient_request(self):
        result = self.extract("عايز حاجة شبه Tobacco Vanilla", {
            "similar_to": "Tobacco Vanilla", "scent_tags": ["smoky", "sweet"],
            "notes": ["tobacco", "vanilla"],
            "scent_evidence": [evidence("Tobacco", "smoky"), evidence("Vanilla", "sweet")],
        })
        self.assert_scents(result)
        self.assertFalse(result.get("notes"))
        self.assertEqual(result.get("similar_to"), "Tobacco Vanilla")

    def test_explicit_exclusion_beside_a_named_reference_still_applies(self):
        result = self.extract("عايز حاجة شبه Tobacco Vanilla بس مش مسكر", {
            "similar_to": "Tobacco Vanilla", "scent_tags": ["smoky", "sweet"],
            "excluded_scent_tags": ["sweet"], "notes": ["tobacco", "vanilla"],
            "scent_evidence": [
                evidence("Tobacco", "smoky"), evidence("Vanilla", "sweet"),
                evidence("مش مسكر", "sweet", polarity="negative"),
            ],
        })
        self.assert_scents(result, excluded={"sweet"})
        self.assertFalse(result.get("notes"))
        self.assertEqual(result.get("similar_to"), "Tobacco Vanilla")

    def test_attached_exclusion_after_a_translated_reference_is_not_part_of_its_name(self):
        result = self.extract("عايز حاجة شبه توباكو فانيلا ومش مسكر", {
            "similar_to": "Tobacco Vanille", "excluded_scent_tags": ["sweet"],
            "scent_evidence": [evidence("مش مسكر", "sweet", polarity="negative")],
        })
        self.assert_scents(result, excluded={"sweet"})
        self.assertFalse(result.get("notes"))
        self.assertEqual(result.get("similar_to"), "Tobacco Vanille")

    def test_attached_preference_after_a_translated_reference_is_not_part_of_its_name(self):
        result = self.extract("عايز حاجة شبه توباكو فانيلا وفريش", {
            "similar_to": "Tobacco Vanille", "scent_tags": ["fresh"],
            "scent_evidence": [evidence("فريش", "fresh")],
        })
        self.assert_scents(result, {"fresh"})
        self.assertFalse(result.get("notes"))
        self.assertEqual(result.get("similar_to"), "Tobacco Vanille")

    def test_explicit_ingredient_after_a_translated_reference_is_preserved(self):
        result = self.extract("عايز حاجة شبه توباكو فانيلا وفيه فانيليا", {
            "similar_to": "Tobacco Vanille", "notes": ["tobacco", "vanilla"],
        })
        self.assert_scents(result)
        self.assertEqual(result.get("notes"), ["vanilla"])
        self.assertEqual(result.get("similar_to"), "Tobacco Vanille")


class ScentPreferenceFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Scent interpretation shop")
        cls.brand = Brand.objects.create(store=cls.store, name="House")

    def setUp(self):
        self.conversation = Conversation.objects.create(store=self.store, preferences={"gender": "male"})
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))

    def product(self, name, tags, notes=""):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="unisex",
            scent_tags=tags, top_notes=notes,
        )
        ProductVariant.objects.create(product=product, volume=50, price=400)
        return product

    def answer(self, message, response=None):
        with patch("products.services.ai.intent.chat", return_value=json.dumps(response or {}, ensure_ascii=False)):
            fresh = extract_intent(message, history=HISTORY, store=self.store)
        result = merge_preferences(self.conversation, fresh, message, history=HISTORY, store=self.store)
        self.conversation.refresh_from_db()
        return result

    def ids(self, intent):
        return set(search_products(intent, self.store)["products"].values_list("pk", flat=True))

    def test_sugary_request_reaches_gender_then_sweet_recommendations(self):
        from products.services.router import route

        self.product("Shohra Grey", ["woody", "spicy", "oud"])
        self.product("Lost Cherry", ["fruity"])
        sweet = self.product("Dessert Choice", ["sweet"])
        self.conversation.preferences = {}
        self.conversation.save(update_fields=["preferences"])
        message = "عايز برفان شوجري"
        with patch("products.services.ai.classifier.chat", return_value='{"intent":"recommendation"}'), \
             patch("products.services.ai.intent.chat", return_value="{}"), \
             patch("products.services.router.get_product_info", side_effect=AssertionError("Scent request entered product lookup")), \
             patch("products.services.ai.recommendation.chat", return_value=json.dumps({
                 "product_ids": [sweet.pk], "reply": "Dessert Choice ريحته مسكرة.",
             })):
            reply, context = route(message, [], self.store, self.conversation)
            self.assertEqual(context, "GENDER_REQUIRED")
            self.conversation.refresh_from_db()
            self.assertEqual(self.conversation.preferences["scent_tags"], ["sweet"])
            history = [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
            reply, context = route("رجالي", history, self.store, self.conversation)

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences["gender"], "male")
        self.assertEqual(self.ids(self.conversation.preferences), {sweet.pk})
        self.assertIn("Dessert Choice", reply)
        self.assertNotIn("Shohra Grey", reply + context)

    def test_named_shohra_grey_question_still_loads_the_product(self):
        from products.services.router import route

        self.product("Shohra Grey", ["woody", "spicy", "oud"])
        with patch("products.services.ai.classifier.chat", return_value='{"intent":"product_info"}'), \
             patch("products.services.product_info.chat", return_value="Shohra Grey متوفر عندنا."):
            reply, context = route("عندك Shohra Grey؟", [], self.store, self.conversation)
        self.assertIn("Name (الاسم الصحيح): Shohra Grey", context)
        self.assertIn("Shohra Grey", reply)

    def test_negated_sweetness_survives_saving_and_filters_real_candidates(self):
        dry = self.product("Dry Breeze", ["fresh"])
        self.product("Sugar Breeze", ["fresh", "sweet"])
        result = self.answer("فريش ومش مسكر", {
            "scent_tags": ["fresh"], "excluded_scent_tags": ["sweet"],
            "scent_evidence": [evidence("فريش", "fresh"), evidence("مش مسكر", "sweet", polarity="negative")],
        })
        self.assertEqual(result["excluded_scent_tags"], ["sweet"])
        self.assertEqual(self.conversation.preferences["excluded_scent_tags"], ["sweet"])
        self.assertEqual(self.ids(result), {dry.pk})

    def test_tobacco_citrus_request_prefers_smoky_overlap_without_requiring_spicy(self):
        wanted = self.product("Smoke Zest", ["smoky", "citrus"])
        pepper = self.product("Pepper Zest", ["spicy", "citrus"])
        plain = self.product("Plain Zest", ["citrus"])
        result = self.answer("تبغي وحمضي", {
            "scent_tags": ["spicy", "citrus"],
            "scent_evidence": [evidence("تبغي", "spicy"), evidence("حمضي", "citrus")],
        })
        self.assertEqual(set(result["scent_tags"]), {"smoky", "citrus"})
        self.assertEqual(list(search_products(result, self.store)["products"]), [wanted, pepper, plain])

    def test_vanilla_style_and_literal_ingredient_produce_different_candidate_sets(self):
        caramel = self.product("Caramel Dessert", ["sweet"], "Caramel")
        vanilla = self.product("Vanilla Dessert", ["sweet"], "Vanilla")
        fresh = self.product("Green Vanilla", ["fresh"], "Vanilla")
        result = self.answer("فانيليا", {"notes": ["vanilla"]})
        self.assertEqual(self.ids(result), {caramel.pk, vanilla.pk})

        self.conversation.preferences = {"gender": "male"}
        self.conversation.save(update_fields=["preferences"])
        result = self.answer("لازم يكون فيه فانيليا", {"notes": ["vanilla"]})
        self.assertEqual(self.ids(result), {vanilla.pk, fresh.pk})

    def test_vanilla_style_update_removes_a_previously_required_vanilla_ingredient(self):
        vanilla = self.product("Vanilla Rose", ["sweet"], "Vanilla, Rose")
        caramel = self.product("Caramel Rose", ["sweet"], "Caramel, Rose")
        self.product("Plain Caramel", ["sweet"], "Caramel")
        self.product("Tobacco Rose", ["sweet"], "Caramel, Rose, Tobacco")
        result = self.answer("لازم يكون فيه فانيليا وورد ومفيهوش تبغ", {
            "notes": ["vanilla", "rose"], "avoid_notes": ["tobacco"],
        })
        self.assertEqual(self.ids(result), {vanilla.pk})
        self.assertEqual(set(self.conversation.preferences["notes"]), {"vanilla", "rose"})

        result = self.answer("فانيليا", {"scent_tags": ["sweet"]})
        self.assertEqual(result["scent_tags"], ["sweet"])
        self.assertEqual(result.get("notes"), ["rose"])
        self.assertEqual(result.get("avoid_notes"), ["tobacco"])
        self.assertEqual(self.conversation.preferences.get("notes"), ["rose"])
        self.assertEqual(self.conversation.preferences.get("avoid_notes"), ["tobacco"])
        self.assertEqual(self.ids(result), {vanilla.pk, caramel.pk})

    def test_null_note_updates_preserve_saved_literal_requirements_and_exclusions(self):
        wanted = self.product("Vanilla Dessert", ["sweet"], "Vanilla")
        self.product("Tobacco Dessert", ["sweet"], "Vanilla, Tobacco")
        self.product("Caramel Dessert", ["sweet"], "Caramel")
        self.answer("لازم يكون فيه فانيليا ومفيهوش تبغ", {
            "notes": ["vanilla"], "avoid_notes": ["tobacco"],
        })

        result = self.answer("ميزانيتي 800", {"max_price": 800, "notes": None, "avoid_notes": None})
        self.assertEqual(result["notes"], ["vanilla"])
        self.assertEqual(result["avoid_notes"], ["tobacco"])
        self.assertEqual(self.conversation.preferences["notes"], ["vanilla"])
        self.assertEqual(self.conversation.preferences["avoid_notes"], ["tobacco"])
        self.assertEqual(self.ids(result), {wanted.pk})

    def test_opening_the_style_keeps_saved_and_new_exclusions(self):
        self.answer("فريش مش مسكر")
        result = self.answer("أي ريحة بس مش عودي")
        self.assertTrue(result["scent_open"])
        self.assertFalse(result.get("scent_tags"))
        self.assertEqual(set(result["excluded_scent_tags"]), {"sweet", "oud"})
        self.assertEqual(set(self.conversation.preferences["excluded_scent_tags"]), {"sweet", "oud"})

    def test_followups_preserve_add_replace_and_reverse_saved_preferences(self):
        self.answer("فريش مش عودي")
        result = self.answer("ميزانيتي 800", {"max_price": 800})
        self.assertEqual(result["scent_tags"], ["fresh"])
        self.assertEqual(result["excluded_scent_tags"], ["oud"])

        result = self.answer("وصابوني كمان")
        self.assertEqual(set(result["scent_tags"]), {"fresh", "clean"})
        result = self.answer("بدل فريش خلينا سويت")
        self.assertEqual(result["scent_tags"], ["sweet"])
        self.assertEqual(result["max_price"], 800)

        result = self.answer("دلوقتي عايز عودي")
        self.assertEqual(result["scent_tags"], ["oud"])
        self.assertFalse(result.get("excluded_scent_tags"))

    def test_clear_and_recipient_reset_are_not_undone_by_reconciliation(self):
        self.answer("فريش مش عودي", {"max_price": 800})
        result = self.answer("شيل شرط طابع الريحة", {
            "clear_preferences": ["scent_tags"], "scent_tags": ["fresh"],
            "scent_evidence": [evidence("فريش", "fresh")],
        })
        self.assertFalse(result.get("scent_tags"))
        self.assertFalse(self.conversation.preferences.get("scent_tags"))
        self.assertEqual(result["excluded_scent_tags"], ["oud"])

        result = self.answer("دلوقتي لخطيبتي عايز سويت", {"reset_preferences": True, "gender": "female"})
        self.assertEqual(result["scent_tags"], ["sweet"])
        self.assertEqual(result["gender"], "female")
        self.assertFalse(result.get("excluded_scent_tags"))
        self.assertNotIn("max_price", self.conversation.preferences)

    def test_clearing_the_old_style_saves_the_replacement_and_keeps_other_constraints(self):
        self.answer("فريش وصابوني مش عودي", {"max_price": 800})
        result = self.answer("بدل فريش خلينا سويت", {
            "scent_tags": ["sweet"], "clear_preferences": ["scent_tags"],
            "scent_evidence": [evidence("سويت", "sweet")],
        })
        self.assertEqual(result["scent_tags"], ["sweet"])
        self.assertEqual(self.conversation.preferences["scent_tags"], ["sweet"])
        self.assertEqual(self.conversation.preferences["excluded_scent_tags"], ["oud"])
        self.assertEqual(self.conversation.preferences["max_price"], 800)

    def test_model_evidence_never_becomes_durable_customer_state(self):
        result = self.answer("فريش وإحساس الملايات المغسولة", {
            "scent_tags": ["fresh", "clean"],
            "scent_evidence": [evidence("فريش", "fresh"), evidence("إحساس الملايات المغسولة", "clean")],
        })
        self.assertEqual(set(result["scent_tags"]), {"fresh", "clean"})
        self.assertNotIn("scent_evidence", self.conversation.preferences)
        self.assertNotIn("scent_evidence", self.conversation.sales_state)
        result = self.answer("800", {"max_price": 800})
        self.assertEqual(set(result["scent_tags"]), {"fresh", "clean"})

    def test_and_or_wording_preserves_preferences_but_accepts_one_matching_tag(self):
        smoky = self.product("Smoke", ["smoky"])
        citrus = self.product("Citrus", ["citrus"])
        both = self.product("Smoke Citrus", ["smoky", "citrus"])
        self.assertEqual(self.ids(self.answer("تبغي وحمضي")), {smoky.pk, citrus.pk, both.pk})
        result = self.answer("تبغي أو حمضي")
        self.assertEqual(result["scent_match"], "any")
        self.assertEqual(self.ids(result), {smoky.pk, citrus.pk, both.pk})

    def test_a_short_scent_choice_is_saved_and_filters_the_catalog(self):
        wanted = self.product("Cedar Choice", ["woody"])
        self.product("Breeze Choice", ["fresh"])
        self.product("Dessert Choice", ["sweet"])
        result = self.answer("التانية")
        self.assertEqual(self.conversation.preferences["scent_tags"], ["woody"])
        self.assertEqual(self.ids(result), {wanted.pk})

    def test_delegating_scent_preserves_budget_gender_and_exclusions(self):
        self.answer("فريش مش عودي", {"max_price": 800})
        result = self.answer("على ذوقك", {"occasion_delegated": True})
        self.assertTrue(result["scent_open"])
        self.assertFalse(result.get("scent_tags"))
        self.assertEqual(self.conversation.preferences["excluded_scent_tags"], ["oud"])
        self.assertEqual(self.conversation.preferences["max_price"], 800)
        self.assertEqual(self.conversation.preferences["gender"], "male")
        self.assertNotIn("occasion", self.conversation.preferences)

    def test_egyptian_alternatives_accept_products_with_either_style(self):
        fresh = self.product("Breeze Choice", ["fresh"])
        woody = self.product("Cedar Choice", ["woody"])
        both = self.product("Cedar Breeze", ["fresh", "woody"])
        self.product("Dessert Choice", ["sweet"])
        result = self.answer("يا فريش يا خشبي")
        self.assertEqual(result["scent_match"], "any")
        self.assertEqual(self.ids(result), {fresh.pk, woody.pk, both.pk})


class ScentClaimCompatibilityTests(SimpleTestCase):
    def test_recorded_ingredients_do_not_imply_catalog_scent_labels(self):
        facts = {"Cedar Day": {"scent_tags": ["woody"], "notes": ["Vanilla, Tobacco"]}}
        for reply in ("Cedar Day فيه فانيليا.", "Cedar Day فيه تبغ."):
            with self.subTest(reply=reply):
                self.assertFalse(product_claim_issues(reply, facts))

    def test_fixed_negation_does_not_reject_a_supported_description(self):
        facts = {"Dry Breeze": {"scent_tags": ["fresh"], "notes": []}}
        self.assertFalse(product_claim_issues("Dry Breeze فريش ومش مسكر.", facts))
        self.assertTrue(product_claim_issues("Dry Breeze طابعه تبغي.", facts))

    def test_style_mapping_does_not_license_unrecorded_ingredient_claims(self):
        facts = {"Sweet Smoke": {"scent_tags": ["sweet", "smoky"], "notes": ["Caramel"]}}
        for reply in ("Sweet Smoke فيه فانيليا.", "Sweet Smoke فيه تبغ."):
            with self.subTest(reply=reply):
                issues = product_claim_issues(reply, facts)
                self.assertTrue(any(issue.startswith("note:") for issue in issues), issues)
