"""Catalog descriptions use saved character labels, never inferred ingredients."""

import importlib
import importlib.util

from django.test import SimpleTestCase

from products.occasions import ACTIVE, DAILY, NIGHT_OUT, ROMANTIC, WORK


class ScentDescriptionTests(SimpleTestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("products.services.scent_description"),
            "Shared scent description helper must exist",
        )
        self.helper = importlib.import_module("products.services.scent_description")
        self.facts = {
            "Alpha": {
                "scent_tags": ["fresh", "woody", "sweet", "light", "clean"],
                "notes": ["Bergamot", "Rose", "Vanilla, Musk"],
                "occasion": f"{WORK}, {DAILY}",
            },
        }

    def test_selects_first_three_recognized_unique_saved_tags_without_mutation(self):
        tags = ["unknown", " Fresh ", "woody", "fresh", None, "sweet", "light"]
        original = list(tags)
        self.assertEqual(self.helper.selected_tags(tags), ["fresh", "woody", "sweet"])
        self.assertEqual(tags, original)

    def test_zero_one_and_two_tags_do_not_invent_character(self):
        for tags, expected in (
            ([], "ريحته محتاجة تأكيد"),
            (["fresh"], "ريحته فريش ومنعش"),
            (["woody", "sweet"], "ريحته خشبي ومسكر"),
        ):
            with self.subTest(tags=tags):
                self.assertEqual(self.helper.describe_scent(tags), expected)

    def test_all_saved_vocabulary_has_simple_arabic_rendering(self):
        examples = {
            "fresh": "فريش ومنعش", "sweet": "مسكر", "oud": "عودي",
            "woody": "خشبي", "citrus": "فيه ريحة حمضيات", "floral": "فيه ريحة زهور",
            "fruity": "فيه ريحة فواكه", "spicy": "فيه ريحة توابل", "musky": "مسكي",
            "clean": "نضيف", "powdery": "فيه ريحة بودرة", "smoky": "فيه ريحة دخان",
            "leather": "ريحته جلدية", "aquatic": "ريحته بحرية", "light": "خفيف",
            "heavy": "تقيل",
        }
        for tag, phrase in examples.items():
            with self.subTest(tag=tag):
                self.assertIn(phrase, self.helper.describe_scent([tag]))

    def test_rendering_limits_tags_and_omits_saved_and_preferred_occasions(self):
        self.assertEqual(
            self.helper.describe_scent(["fresh", "woody", "sweet", "light"], f"{WORK}, {DAILY}"),
            "ريحته فريش ومنعش وخشبي ومسكر",
        )
        self.assertEqual(
            self.helper.describe_scent(["sweet"], f"{WORK}, {DAILY}", preferred_occasion=DAILY),
            "ريحته مسكر",
        )

    def test_occasion_default_follows_recorded_order_and_longest_phrases(self):
        for recorded, expected in (
            (f"{WORK}, {DAILY}", WORK), (f"{DAILY}, {WORK}", DAILY),
            (f"{ROMANTIC}, {NIGHT_OUT}", ROMANTIC), (ACTIVE, ACTIVE),
            ("للشغل وللجامعة", WORK), ("unknown", ""),
        ):
            with self.subTest(recorded=recorded):
                self.assertEqual(self.helper.selected_occasion(recorded), expected)

    def test_preferred_occasion_must_be_recorded(self):
        self.assertEqual(self.helper.selected_occasion(f"{WORK}, {DAILY}", "الجامعة"), DAILY)
        self.assertEqual(self.helper.selected_occasion(f"{WORK}, {DAILY}", ROMANTIC), WORK)
        self.assertEqual(self.helper.selected_occasion("", DAILY), "")

    def test_explicit_notes_questions_and_named_ingredients_are_authorized(self):
        for question in (
            "ايه نوتاته؟", "مكوناته ايه؟", "الافتتاحية والقلب والقاعدة؟",
            "What are its notes?", "Tell me the ingredients", "top middle and base notes?",
            "فيه فانيليا؟", "does it contain vanilla?", "فيه برغموت؟", "Is there bergamot?",
        ):
            with self.subTest(question=question):
                self.assertTrue(self.helper.notes_requested(question))

    def test_ordinary_scent_details_and_performance_do_not_authorize_notes(self):
        for question in ("ريحته عاملة ازاي؟", "اوصفهولي", "تفاصيله ايه؟", "ثباته قد ايه؟", "What does it smell like?", "Tell me about it"):
            with self.subTest(question=question):
                self.assertFalse(self.helper.notes_requested(question))

    def test_product_names_containing_ingredients_do_not_authorize_notes(self):
        self.assertFalse(self.helper.notes_requested(
            "Tobacco Vanille ريحته ايه؟", product_names=("Tobacco Vanille",),
        ))
        self.assertFalse(self.helper.notes_requested(
            "عود روز سعره كام؟", product_names=("عود روز",),
        ))
        self.assertTrue(self.helper.notes_requested(
            "Tobacco Vanille فيه tobacco؟", product_names=("Tobacco Vanille",),
        ))

    def test_previous_notes_permission_does_not_carry_to_current_unrelated_turn(self):
        history = [{"role": "user", "content": "ايه نوتاته؟"}, {"role": "assistant", "content": "فانيليا"}]
        self.assertFalse(self.helper.notes_requested("سعره كام؟", history))
        self.assertFalse(self.helper.notes_requested("ريحته عاملة ازاي؟", history))

    def test_scent_requests_cover_description_opinion_and_mixed_price(self):
        for question in ("ريحته عاملة ازاي؟", "اوصفهولي", "ايه رأيك في Alpha؟", "Alpha ريحته ايه وسعره كام؟", "What does Alpha smell like?", "Describe Alpha", "Tell me about Alpha"):
            with self.subTest(question=question):
                self.assertTrue(self.helper.scent_requested(question, product_names=("Alpha",)))

    def test_notes_only_price_only_and_trait_only_do_not_request_full_description(self):
        for question in ("ايه نوتاته؟", "فيه فانيليا؟", "سعره كام؟", "ثباته قد ايه؟", "هو خفيف؟", "is it woody?", "Alpha مسكر ولا لا؟"):
            with self.subTest(question=question):
                self.assertFalse(self.helper.scent_requested(question, product_names=("Alpha",)))

    def test_valid_natural_description_accepts_saved_tags_without_occasion(self):
        self.assertEqual(self.helper.description_issues(
            "Alpha ريحته فريش ومنعش، خشبي ومسكر.", self.facts,
            message="ريحته ايه؟",
        ), [])

    def test_missing_or_wrong_selected_tags_are_rejected(self):
        for reply in (
            "Alpha ريحته فريش وخشبي",
            "Alpha ريحته فريش وخشبي ومسكي",
        ):
            with self.subTest(reply=reply):
                issues = self.helper.description_issues(reply, self.facts, message="ريحته ايه؟")
                self.assertTrue(issues)
                self.assertTrue(all(issue.startswith("description:") for issue in issues))

    def test_extra_fourth_saved_tag_and_unrecorded_trait_are_rejected_in_prose(self):
        for extra in ("وخفيف", "ونضيف", "ودخاني"):
            with self.subTest(extra=extra):
                self.assertTrue(self.helper.description_issues(
                    "Alpha ريحته فريش وخشبي ومسكر " + extra + "", self.facts,
                    message="ريحته عاملة ازاي؟",
                ))

    def test_unsolicited_occasions_are_rejected_in_any_description_position(self):
        for reply in (
            "Alpha ريحته فريش وخشبي ومسكر ومناسب للشغل",
            "Alpha ريحته فريش وخشبي ومسكر ومناسب للجامعة",
            "Alpha ريحته فريش وخشبي ومسكر ومناسب للشغل وللجامعة",
            "Alpha مناسب للشغل وريحته فريش وخشبي ومسكر",
            "Alpha ريحته فريش وخشبي ومسكر. ينفع للشغل.",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(self.helper.description_issues(reply, self.facts, message="اوصفهولي"))

    def test_saved_occasion_does_not_require_an_occasion_in_the_description(self):
        self.assertEqual(self.helper.description_issues(
            "Alpha ريحته فريش وخشبي ومسكر", self.facts,
            message="ريحته ايه؟", preferred_occasion=DAILY,
        ), [])

    def test_one_product_followup_may_omit_name(self):
        self.assertEqual(self.helper.description_issues(
            "ريحته فريش وخشبي ومسكر", self.facts,
            message="ريحته ايه؟",
        ), [])

    def test_comparison_cannot_borrow_other_products_tags(self):
        facts = {**self.facts, "Beta": {"scent_tags": ["oud", "heavy"], "notes": ["", "", ""], "occasion": NIGHT_OUT}}
        good = "Alpha ريحته فريش وخشبي ومسكر. Beta ريحته عودي وتقيل."
        self.assertEqual(self.helper.description_issues(good, facts, message="قارن ريحتهم", required=True), [])
        bad = "Alpha ريحته عودي وتقيل. Beta ريحته فريش وخشبي ومسكر."
        self.assertTrue(self.helper.description_issues(bad, facts, message="قارن ريحتهم", required=True))

    def test_fourth_tag_specific_question_remains_scoped(self):
        self.assertEqual(self.helper.description_issues(
            "أيوه Alpha خفيف", self.facts, message="Alpha خفيف؟",
        ), [])
        self.assertEqual(self.helper.description_issues(
            "أيوه خفيف", self.facts, message="هو خفيف؟",
        ), [])

    def test_unsolicited_recorded_vanilla_note_is_rejected_and_explicit_question_allowed(self):
        reply = "Alpha ريحته فريش وخشبي ومسكر، وفيه فانيليا"
        self.assertTrue(self.helper.description_issues(reply, self.facts, message="ريحته ايه؟"))
        self.assertEqual(self.helper.description_issues(
            "أيوه Alpha فيه فانيليا", self.facts, message="فيه فانيليا؟",
        ), [])

    def test_family_smell_wording_does_not_require_ingredient_evidence(self):
        facts = {"Beta": {"scent_tags": ["fresh", "citrus", "floral"], "notes": ["", "", ""], "occasion": DAILY}}
        self.assertEqual(self.helper.description_issues(
            "Beta فريش ومنعش وفيه ريحة حمضيات وزهور", facts,
            message="ريحته ايه؟",
        ), [])

    def test_price_only_response_and_legacy_validation_are_unchanged(self):
        self.assertEqual(self.helper.description_issues("Alpha سعره 500 جنيه", self.facts, message="سعره كام؟"), [])
        self.assertEqual(self.helper.description_issues("Alpha فيه فانيليا", self.facts), [])

    def test_recommendation_requires_description_but_price_turn_does_not(self):
        self.assertTrue(self.helper.description_issues("Alpha سعره 500 جنيه", self.facts, message="رشحلي حاجة", required=True))

    def test_empty_tags_and_unknown_occasions_accept_honest_fallback(self):
        facts = {"Beta": {"scent_tags": [], "notes": ["", "", ""], "occasion": "unknown"}}
        for reply in ("Beta ريحته محتاجة تأكيد", "Beta وصف ريحته محتاج تأكيد"):   # the wording before 2026-11-20
            with self.subTest(reply=reply):
                self.assertEqual(self.helper.description_issues(
                    reply, facts, message="ريحته ايه؟", required=True,
                ), [])
        self.assertEqual(self.helper.describe_scent([], "unknown"), "ريحته محتاجة تأكيد")

    def test_known_tags_with_unknown_occasion_do_not_require_invented_use(self):
        facts = {"Beta": {"scent_tags": ["sweet"], "notes": ["", "", ""], "occasion": "unknown"}}
        self.assertEqual(self.helper.description_issues("Beta ريحته مسكر", facts, message="ريحته ايه؟"), [])

    def test_unattributed_multi_product_reply_is_not_guessed(self):
        facts = {**self.facts, "Beta": {"scent_tags": ["oud"], "notes": [], "occasion": DAILY}}
        self.assertTrue(self.helper.description_issues(
            "ريحته فريش وخشبي ومسكر", facts, message="قارن بينهم", required=True,
        ))

    def test_actual_scent_prose_enforces_description_even_on_price_question(self):
        self.assertTrue(self.helper.description_issues(
            "Alpha سعره 500 وريحته مسكر", self.facts, message="سعره كام؟",
        ))

    def test_short_scent_and_arabic_notes_spelling_are_understood(self):
        self.assertTrue(self.helper.scent_requested("ريحته؟"))

    def test_arabic_notes_spelling_is_understood(self):
        self.assertTrue(self.helper.notes_requested("النوتس بتاعته ايه؟"))

    def test_every_generated_description_satisfies_its_own_catalog_facts(self):
        # Exercise the renderer/validator boundary, including material-like styles.
        for tag in ("fresh", "sweet", "oud", "woody", "citrus", "floral", "fruity", "spicy", "musky", "clean", "powdery", "smoky", "leather", "aquatic", "light", "heavy"):
            with self.subTest(tag=tag):
                facts = {"Beta": {"scent_tags": [tag], "notes": [], "occasion": DAILY}}
                reply = self.helper.describe_scent([tag], DAILY)
                self.assertEqual(self.helper.description_issues(reply, facts, message="ريحته ايه؟", required=True), [])

    def test_tag_negation_cannot_count_as_selected_character(self):
        self.assertTrue(self.helper.description_issues(
            "Alpha فريش وخشبي ومش مسكر", self.facts,
            message="ريحته ايه؟",
        ))

    def test_material_wording_requires_notes_permission_even_for_saved_oud(self):
        facts = {"Beta": {"scent_tags": ["oud"], "notes": ["Oud", "", ""], "occasion": DAILY}}
        self.assertEqual(self.helper.description_issues(
            "Beta ريحته عود", facts, message="ريحته ايه؟",
        ), [])
        self.assertTrue(self.helper.description_issues(
            "Beta فيه عود", facts, message="ريحته ايه؟",
        ))

    def test_known_product_name_is_removed_from_reply_ingredient_checks(self):
        facts = {"Tobacco Vanille": {"scent_tags": ["sweet"], "notes": [], "occasion": DAILY}}
        self.assertEqual(self.helper.description_issues(
            "Tobacco Vanille ريحته مسكر", facts,
            message="Tobacco Vanille ريحته ايه؟",
        ), [])

    def test_note_labels_do_not_substitute_for_a_selected_character_family(self):
        facts = {"Beta": {"scent_tags": ["floral"], "notes": ["Rose", "", ""], "occasion": DAILY}}
        self.assertTrue(self.helper.description_issues(
            "Beta ريحته ورد", facts, message="ريحته ايه؟",
        ))

    def test_selected_tag_coverage_accepts_natural_verbal_order(self):
        self.assertEqual(self.helper.description_issues(
            "Alpha ريحته مسكر وخشبي وفريش", self.facts,
            message="ريحته ايه؟",
        ), [])

    def test_brief_mixed_price_and_scent_question_requests_description(self):
        for question in ("بكام وريحته؟", "price and scent?"):
            with self.subTest(question=question):
                self.assertTrue(self.helper.scent_requested(question))

    def test_negated_preferred_occasion_does_not_override_recorded_order(self):
        self.assertEqual(self.helper.selected_occasion(f"{DAILY}, {WORK}", "مش للشغل"), DAILY)
        self.assertEqual(self.helper.selected_occasion(f"{DAILY}, {WORK}", "مش يومي ولا للشغل"), DAILY)

    def test_recorded_raw_note_cannot_leak_when_outside_known_aliases(self):
        facts = {"Beta": {"scent_tags": ["sweet"], "notes": ["Dragon Fruit", "Pink Lotus", ""], "occasion": DAILY}}
        self.assertTrue(self.helper.description_issues(
            "Beta ريحته مسكر وفيه pink lotus", facts,
            message="ريحته ايه؟",
        ))

    def test_unsolicited_negative_ingredient_claim_is_also_out_of_scope(self):
        self.assertTrue(self.helper.description_issues(
            "Alpha فريش وخشبي ومسكر ومن غير فانيليا", self.facts,
            message="ريحته ايه؟",
        ))

    def test_negative_trait_clause_does_not_negate_following_positive_character(self):
        self.assertEqual(self.helper.description_issues(
            "Alpha مش تقيل بس فريش وخشبي ومسكر", self.facts,
            message="ريحته ايه؟",
        ), [])

    def test_raw_recorded_ingredient_question_authorizes_that_answer(self):
        facts = {"Beta": {"scent_tags": ["sweet"], "notes": ["Pink Lotus", "", ""], "occasion": DAILY}}
        self.assertEqual(self.helper.description_issues(
            "أيوه Beta فيه pink lotus", facts, message="فيه pink lotus؟",
        ), [])

    def test_generic_description_verbs_in_notes_only_question_preserve_notes_scope(self):
        for question in ("Tell me the ingredients", "Describe the notes", "Tell me about vanilla", "اوصف النوتات"):
            with self.subTest(question=question):
                self.assertFalse(self.helper.scent_requested(question))
        self.assertTrue(self.helper.scent_requested("Describe its scent and ingredients"))
        self.assertTrue(self.helper.scent_requested("ريحته عاملة ازاي وايه نوتاته؟"))

    def test_plural_arabic_notes_questions_are_authorized(self):
        for question in ("Alpha وBeta بكام ومكوناتهم إيه؟", "نوتاتهم ايه؟", "مكوناتهن ايه؟"):
            with self.subTest(question=question):
                self.assertTrue(self.helper.notes_requested(question, product_names=("Alpha", "Beta")))

    def test_explicit_notes_opt_out_does_not_authorize_notes(self):
        for question in ("مش عايز تفاصيل النوتات", "من غير نوتات", "without notes", "don't mention the ingredients", "بلاش فانيليا", "ريحته ايه من غير مكونات؟"):
            with self.subTest(question=question):
                self.assertFalse(self.helper.notes_requested(question))
        self.assertTrue(self.helper.notes_requested("نوتاته ايه بس مش عايز وصف ريحته"))

    def test_current_occasion_preference_overrides_saved_preference(self):
        self.assertEqual(self.helper.occasion_preference("Alpha للشغل", product_names=("Alpha",), saved=DAILY), WORK)
        self.assertEqual(self.helper.occasion_preference("ريحته ايه؟", saved=DAILY), DAILY)
        self.assertEqual(self.helper.occasion_preference("مش للشغل", saved=DAILY), DAILY)
        self.assertEqual(self.helper.occasion_preference("ريحته ايه؟"), "")

    def test_occasion_preference_ignores_occasion_words_in_product_names(self):
        self.assertEqual(self.helper.occasion_preference(
            "Summer Night ريحته ايه؟", product_names=("Summer Night",), saved=DAILY,
        ), DAILY)
        self.assertEqual(self.helper.occasion_preference(
            "Alpha وSummer Night مناسب للشغل؟", product_names=("Alpha", "Summer Night"), saved=DAILY,
        ), WORK)

    def test_attached_conjunction_on_product_name_does_not_authorize_ingredients(self):
        self.assertFalse(self.helper.notes_requested(
            "Alpha وTobacco Vanille ريحتهم ايه؟", product_names=("Alpha", "Tobacco Vanille"),
        ))

    def test_explicit_scent_opt_out_does_not_require_character(self):
        for question in ("Alpha ثباته إيه من غير وصف ريحته؟", "نوتاته ايه بس مش عايز وصف ريحته", "longevity without a scent description"):
            with self.subTest(question=question):
                self.assertFalse(self.helper.scent_requested(question, product_names=("Alpha",)))
                self.assertEqual(self.helper.description_issues(
                    "Alpha ثباته ممتاز", self.facts, message=question,
                ), [])

    def test_authorized_ingredient_labels_are_not_extra_character(self):
        facts = {"Alpha": {"scent_tags": ["fresh", "citrus", "light"],
                           "occasion": WORK, "notes": ["Woody Notes", "Vanilla"]}}
        for reply in (
            "Alpha فريش وحمضي وخفيف. نوتاته Woody Notes وVanilla.",
            "Alpha نوتاته Woody Notes وVanilla وريحته فريش وحمضي وخفيف.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self.helper.description_issues(
                    reply, facts, message="Alpha ريحته ايه ونوتاته ايه؟",
                ), [])

    def test_notes_permission_belongs_to_the_product_asked_about(self):
        facts = {"Alpha": {"scent_tags": ["fresh"], "occasion": WORK, "notes": ["Vanilla"]},
                 "Beta": {"scent_tags": ["oud"], "occasion": NIGHT_OUT, "notes": ["Oud"]}}
        issues = self.helper.description_issues(
            "Alpha فريش ونوتاته فانيليا. وBeta نوتاته Oud وريحته عودي.",
            facts, message="Alpha ريحته ايه ونوتاته ايه وBeta ريحته ايه؟",
        )
        self.assertTrue(any(issue.startswith("description:Beta:") for issue in issues))
        self.assertFalse(any(issue.startswith("description:Alpha:") for issue in issues))

    def test_notes_only_for_one_product_does_not_require_its_scent(self):
        facts = {"Alpha": {"scent_tags": ["fresh"], "occasion": WORK, "notes": ["Vanilla"]},
                 "Beta": {"scent_tags": ["oud"], "occasion": NIGHT_OUT, "notes": ["Oud"]}}
        self.assertEqual(self.helper.description_issues(
            "Alpha نوتاته Vanilla. وBeta ريحته عودي.", facts,
            message="Alpha نوتاته ايه وBeta ريحته ايه؟",
        ), [])

    def test_grouped_common_question_applies_to_both_named_products(self):
        facts = {"Alpha": {"scent_tags": ["fresh"], "occasion": WORK, "notes": ["Vanilla"]},
                 "Beta": {"scent_tags": ["oud"], "occasion": NIGHT_OUT, "notes": ["Oud"]}}
        self.assertEqual(self.helper.description_issues(
            "Alpha فريش ونوتاته Vanilla. وBeta ريحته عودي ونوتاته Oud.",
            facts, message="Alpha وBeta ريحتهم ايه ونوتاتهم ايه؟",
        ), [])

    def test_attached_conjunction_preserves_reply_product_attribution(self):
        facts = {**self.facts, "Beta": {"scent_tags": ["oud", "heavy"], "occasion": NIGHT_OUT, "notes": []}}
        self.assertEqual(self.helper.description_issues(
            "Alpha فريش وخشبي ومسكر، وBeta عودي وتقيل.",
            facts, message="قارن ريحتهم",
        ), [])

    def test_recorded_occasion_must_be_positive_and_not_also_denied(self):
        for reply in (
            "Alpha فريش وخشبي ومسكر ومش مناسب للشغل.",
            "Alpha فريش وخشبي ومسكر بس مش مناسب للمكتب.",
            "Alpha مش مناسب للشغل.",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(self.helper.description_issues(reply, self.facts, message="مناسب للشغل؟"))

    def test_occasion_only_claims_require_a_current_question(self):
        for message in ("سعره كام؟", "ثباته ايه؟", "فيه فانيليا؟", "هو خفيف؟"):
            with self.subTest(message=message):
                self.assertTrue(self.helper.description_issues(
                    "Alpha مناسب للشغل.", self.facts, message=message,
                ))

    def test_explicit_occasion_questions_allow_recorded_suitability(self):
        for message in (
            "ينفع للشغل؟", "هو مناسب للشغل؟", "مناسب لإيه؟", "مناسبته إيه؟",
            "البسه امتى؟", "Is it suitable for work?", "Is it good for the office?",
            "What occasions is it good for?", "When should I wear it?", "Tell me its occasions",
            "Would Alpha be suitable for work?", "ممكن ألبس Alpha للشغل؟",
        ):
            with self.subTest(message=message):
                self.assertEqual(self.helper.description_issues(
                    "Alpha مناسب للشغل.", self.facts, message=message,
                ), [])

    def test_recommendation_occasion_preferences_do_not_authorize_suitability_prose(self):
        for message in (
            "رشحلي عطر للشغل", "رشحلي عطر مناسب للشغل", "عايز حاجة مناسبة للشغل",
            "Recommend a perfume for work", "Recommend a perfume suitable for work",
            "What perfume is suitable for work?", "ايه العطر المناسب للشغل؟", "قولي عطر مناسب للشغل",
            "ريحته ايه للشغل؟",
        ):
            with self.subTest(message=message):
                self.assertTrue(self.helper.description_issues(
                    "Alpha فريش وخشبي ومسكر ومناسب للشغل.", self.facts,
                    message=message, preferred_occasion=WORK, required=True,
                ))

    def test_combined_description_and_occasion_property_request_keeps_both_scopes(self):
        self.assertEqual(self.helper.description_issues(
            "Alpha ريحته فريش وخشبي ومسكر ومناسب للشغل.", self.facts,
            message="Describe Alpha and its occasions",
        ), [])

    def test_previous_occasion_question_does_not_authorize_current_description(self):
        self.assertTrue(self.helper.description_issues(
            "Alpha فريش وخشبي ومسكر ومناسب للشغل.", self.facts,
            message="ريحته ايه؟", preferred_occasion=WORK,
            history=[{"role": "user", "content": "مناسب للشغل؟"}],
        ))

    def test_occasion_opt_out_does_not_authorize_an_occasion_answer(self):
        for message in ("ريحته ايه من غير مناسبات؟", "Describe it without mentioning occasions"):
            with self.subTest(message=message):
                self.assertTrue(self.helper.description_issues(
                    "Alpha فريش وخشبي ومسكر ومناسب للشغل.", self.facts, message=message,
                ))

    def test_authorized_notes_cannot_hide_unsolicited_occasion_claims(self):
        self.assertTrue(self.helper.description_issues(
            "Alpha نوتاته Vanilla ومناسب للشغل.", self.facts, message="نوتاته ايه؟",
        ))

    def test_occasion_permission_belongs_to_the_product_asked_about(self):
        facts = {**self.facts, "Beta": {"scent_tags": ["oud"], "notes": [], "occasion": NIGHT_OUT}}
        message = "Alpha مناسب للشغل وBeta ريحته ايه؟"
        self.assertEqual(self.helper.description_issues(
            "Alpha مناسب للشغل. Beta ريحته عودي.", facts, message=message,
        ), [])
        issues = self.helper.description_issues(
            "Alpha مناسب للشغل. Beta ريحته عودي ومناسب للسهرات.", facts, message=message,
        )
        self.assertTrue(any(issue.startswith("description:Beta:") for issue in issues))
        self.assertFalse(any(issue.startswith("description:Alpha:") for issue in issues))

    def test_occasion_dimension_before_next_product_preserves_question_scope(self):
        facts = {**self.facts, "Beta": {"scent_tags": ["oud"], "notes": [], "occasion": NIGHT_OUT}}
        self.assertEqual(self.helper.description_issues(
            "Alpha فريش وخشبي ومسكر. Beta مناسب للسهرات.", facts,
            message="Alpha ريحته ايه ومناسبة Beta ايه؟",
        ), [])

    def test_occasion_claims_are_grounded_even_when_tags_are_missing(self):
        facts = {"Beta": {"scent_tags": [], "notes": [], "occasion": WORK}}
        self.assertTrue(self.helper.description_issues(
            "Beta مناسب للسهرات.", facts, message="مناسب لايه؟",
        ))
        facts["Beta"]["occasion"] = ""
        self.assertTrue(self.helper.description_issues(
            "Beta مناسب للشغل.", facts, message="ينفع للشغل؟",
        ))
        self.assertEqual(self.helper.description_issues(
            "Beta مناسبته محتاجة تأكيد.", facts, message="ينفع للشغل؟",
        ), [])
        self.assertTrue(self.helper.description_issues(
            "Beta مش مناسب للشغل.", facts, message="ينفع للشغل؟",
        ))

    def test_missing_scent_does_not_authorize_an_occasion_claim(self):
        facts = {"Beta": {"scent_tags": [], "notes": [], "occasion": WORK}}
        for reply in (
            "Beta وصف ريحته محتاج تأكيد ومناسب للشغل.",
            "Beta وصف ريحته محتاج تأكيد. مناسبته محتاجة تأكيد.",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(self.helper.description_issues(reply, facts, message="ريحته ايه؟"))

    def test_uncertain_suitability_is_scoped_to_an_explicit_question(self):
        facts = {"Beta": {"scent_tags": [], "notes": [], "occasion": ""}}
        reply = "Beta مناسبته للشغل محتاجة تأكيد."
        self.assertEqual(self.helper.description_issues(reply, facts, message="ينفع للشغل؟"), [])
        self.assertTrue(self.helper.description_issues(reply, facts, message="سعره كام؟"))

    def test_occasion_words_in_product_names_do_not_authorize_suitability(self):
        facts = {"Office Night": {"scent_tags": ["fresh"], "notes": [], "occasion": WORK}}
        self.assertEqual(self.helper.description_issues(
            "Office Night ريحته فريش.", facts, message="Office Night ريحته ايه؟",
        ), [])
        self.assertTrue(self.helper.description_issues(
            "Office Night ريحته فريش ومناسب للشغل.", facts, message="Office Night ريحته ايه؟",
        ))

    def test_season_answers_and_preference_questions_are_not_occasion_claims(self):
        for reply, message in (
            ("Alpha فريش وخشبي ومسكر. موسمه الصيف.", "ريحته ايه وموسمه ايه؟"),
            ("Alpha فريش وخشبي ومسكر. تحب تستخدمه للشغل ولا للخروج؟", "رشحلي عطر"),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self.helper.description_issues(reply, self.facts, message=message), [])

    def test_valid_character_prose_is_rejected_when_description_was_excluded(self):
        self.assertTrue(self.helper.description_issues(
            "Alpha فريش وخشبي ومسكر وثباته ممتاز", self.facts,
            message="Alpha ثباته إيه من غير وصف ريحته؟",
        ))

    def test_recommendation_requirement_does_not_override_description_opt_out(self):
        self.assertEqual(self.helper.description_issues(
            "Alpha ثباته ممتاز", self.facts,
            message="رشحلي فريش وقولي ثباته من غير وصف ريحته", required=True,
        ), [])

    def test_notes_dimension_before_later_name_belongs_to_that_product(self):
        facts = {"Alpha": {"scent_tags": ["fresh"], "occasion": WORK, "notes": ["Vanilla"]},
                 "Beta": {"scent_tags": ["oud"], "occasion": NIGHT_OUT, "notes": ["Oud"]}}
        self.assertEqual(self.helper.description_issues(
            "Alpha نوتاته Vanilla. Beta نوتاته Oud.", facts,
            message="نوتات Alpha ايه ونوتات Beta ايه؟",
        ), [])

    def test_later_prefix_dimension_does_not_leak_back_to_first_product(self):
        facts = {"Alpha": {"scent_tags": ["fresh"], "occasion": WORK, "notes": ["Vanilla"]},
                 "Beta": {"scent_tags": ["oud"], "occasion": NIGHT_OUT, "notes": ["Oud"]}}
        message = "Alpha ريحته ايه ونوتات Beta ايه؟"
        self.assertEqual(self.helper.description_issues(
            "Alpha فريش. Beta نوتاته Oud.", facts, message=message,
        ), [])
        self.assertTrue(self.helper.description_issues(
            "Alpha فريش ونوتاته Vanilla. Beta نوتاته Oud.", facts, message=message,
        ))

    def test_dotted_catalog_name_preserves_comparison_attribution(self):
        facts = {"No. 5": {"scent_tags": ["fresh"], "occasion": WORK, "notes": []},
                 "Beta": {"scent_tags": ["sweet"], "occasion": NIGHT_OUT, "notes": []}}
        for reply in (
            "No. 5 فريش. Beta مسكر.",
            "Beta مسكر، وNo. 5 فريش.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self.helper.description_issues(reply, facts, message="قارن ريحتهم"), [])
