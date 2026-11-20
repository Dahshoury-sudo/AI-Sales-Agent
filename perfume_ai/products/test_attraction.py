"""Customers who want a perfume that makes other people like them (owner decisions, 2026-11-20).

Conversation 1345: "عايز برفان يخلي البنات تحبني" read "البنات" as the wearer and offered a man 212 Sexy
Women. Conversation 1346: the goal was dropped and only the scent question went out. Conversation 1263:
"Sauvage هيخلي البنات تحبني؟" was answered with a lecture, and 659 with invented appeal.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services.ai import intent as intent_module
from products.services.conversation_service import save_message
from products.services.product_formatting import product_claim_facts
from products.services.reply_sanitizer import product_claim_issues
from products.services.router import route
from products.services.sales import attraction, gender, scent_answers
from products.services.sales.scent_matching import order_candidates


class DetectTests(SimpleTestCase):
    def test_stored_requests_are_recognised_with_their_audience(self):
        for message, audience in (
            ("عايز برفان يخلي البنات تحبني", "women"),
            ("عايز برفان رجالي يخلي البنات تحبني", "women"),
            ("عايز برفان يخلي البنات تتهبل عليا", "women"),
            ("هيخلي النسوان تحبني ؟", "women"),
            ("دول هيخلو البنات تعجب بيا ؟", "women"),
            ("دول هيعجبو البنات معايا فالشعل ؟", "women"),
            ("بتاع افنان ده هيخلي البنات تموت فيا ؟", "women"),
            ("عايزه برفان يخلي الرجالة يحبوني", "men"),
            ("عايز برفان يخلي الناس تحبني", "people"),
            ("عايز برفان فحدود 600 يكون بيلفت الانظار وشتوي", "people"),
            ("حاجه للشتا وتكون زي رجاله المافيا وثابته وملفته للنتباه وجذابه", "people"),
            ("انا عريس جديد وعايز برفان سكسي", "people"),
            ("بتاع افنان ده هيعجب الناس", "people"),
        ):
            with self.subTest(message=message):
                self.assertEqual(attraction.detect(message), audience)

    def test_recipients_tastes_and_perfume_names_are_not_this_goal(self):
        for message in ("عايز برفان للبنات", "البرفيوم الحريمي دي اوريجنال", "طب قولي الحريمي الي من عندكو",
                        "البنت بتاعتي بتحب الفانيليا", "ماشي بس انا عايز 90 ملي من سكسي ومن ده",
                        "عايز برفان 212 سكسي", "حاجه للشتا وتكون زي رجاله المافيا", "عايز حاجه فريش للشغل"):
            with self.subTest(message=message):
                self.assertIsNone(attraction.detect(message))


class GenderTests(SimpleTestCase):
    """Owner decision 1: the audience never sets the gender; with none stated, the question is asked."""

    def test_the_audience_is_not_the_wearer(self):
        for message, expected in (
            ("عايز برفان يخلي البنات تحبني", None),
            ("عايزه برفان يخلي الرجالة يحبوني", None),
            ("هيخلي البنات تحبني ؟", None),
            ("عايز برفان رجالي يخلي البنات تحبني", "male"),
            ("عايز برفان حريمي يخلي الرجالة يحبوني", "female"),
            ("عايز حاجه تلفت الانظار حريمي وفيها ورد", "female"),
            ("عايز برفان للبنات", "female"),
            ("البنات", "female"),
        ):
            with self.subTest(message=message):
                self.assertEqual(gender.customer_choice(message), expected)
                self.assertEqual(gender.from_words(message), expected)

    def test_the_audience_does_not_count_as_a_stated_gender(self):
        self.assertFalse(gender.mentioned_in_words("عايز برفان يخلي البنات تحبني"))
        self.assertTrue(gender.mentioned_in_words("عايز برفان للبنات"))


class QuestionDetectionTests(SimpleTestCase):
    """The acknowledgement goes in front of a question that later turns are matched against."""

    def test_acknowledgement_once_and_only_for_this_goal(self):
        question = scent_answers.PREFERENCE_QUESTION
        opened = attraction.with_acknowledgement(question, "عايز برفان يخلي البنات تحبني")
        self.assertEqual(opened, attraction.ACKNOWLEDGEMENT + "\n" + question)
        history = [{"role": "assistant", "content": opened}]
        self.assertEqual(attraction.with_acknowledgement(question, "عايز برفان يخلي البنات تحبني", history), question)
        self.assertEqual(attraction.with_acknowledgement(question, "عايز حاجه فريش"), question)

    def test_questions_behind_the_acknowledgement_are_still_pending(self):
        scent = [{"role": "assistant", "content": attraction.ACKNOWLEDGEMENT + "\n" + scent_answers.PREFERENCE_QUESTION}]
        self.assertEqual(scent_answers.pending_question(scent), "standard")
        self.assertEqual(scent_answers.answer_updates("اول حاجة", scent).get("scent_tags"), ["fresh"])
        asked = [{"role": "assistant", "content": attraction.ACKNOWLEDGEMENT + "\n" + gender.QUESTION}]
        self.assertTrue(gender.question_pending(asked))
        self.assertTrue(gender.pending_answer("رجالي", asked))

    def test_the_goal_comes_from_the_customer_not_the_model(self):
        with patch("products.services.ai.intent.chat", return_value='{"appeal_goal": "women"}'):
            self.assertNotIn(attraction.GOAL_KEY, intent_module.extract_intent("عايز حاجه فريش"))
        with patch("products.services.ai.intent.chat", return_value="{}"):
            self.assertEqual(intent_module.extract_intent("عايز برفان يخلي البنات تحبني")[attraction.GOAL_KEY], "women")
        with patch("products.services.ai.intent.chat", return_value="not json"):
            self.assertEqual(intent_module.extract_intent("عايز برفان يخلي البنات تحبني")[attraction.GOAL_KEY], "women")


class PromiseTests(SimpleTestCase):
    def test_promises_about_other_people_are_refused_unless_hedged(self):
        for reply in ("Sauvage هيخلي البنات تحبك يا فندم", "ريحته جذابة جداً للبنات", "Sauvage بيعجب الستات"):
            with self.subTest(reply=reply):
                self.assertTrue(attraction.promise_issues(reply))
        for reply in ("مقدرش أضمن إنه هيخلي البنات تحبك", "مفيش عطر يضمن إن البنات تحبك",
                      "مش متأكد إنه هيعجب البنات", "ريحته فريش وخشبية"):
            with self.subTest(reply=reply):
                self.assertEqual(attraction.promise_issues(reply), [])


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Attraction")
        StoreSettings.objects.create(store=cls.store)
        brand = Brand.objects.create(store=cls.store, name="House")

        def perfume(name, tags, description="", gender_value="male"):
            product = Product.objects.create(store=cls.store, brand=brand, name=name, gender=gender_value,
                                             scent_tags=tags, description=description)
            ProductVariant.objects.create(product=product, volume=50, price=400)
            return product

        cls.plain = perfume("Plain Sweet", ["sweet", "woody"], "A warm sweet-woody scent.")
        cls.appealing = perfume("Velvet Night", ["sweet", "woody"], "A warm sweet-woody scent with a seductive touch.")
        cls.spicy_plain = perfume("Spice Route", ["sweet", "spicy"], "A sweet-spicy scent.")
        cls.fresh = perfume("Fresh Man", ["fresh"], "A clean fresh scent.")
        cls.named = perfume("212 Sexy Test", ["sweet"], "212 Sexy Test is a sweet scent.")
        cls.women = perfume("Women Sweet", ["sweet", "woody"], "A sweet scent.", "female")

    def test_appeal_comes_from_the_description_not_the_name(self):
        self.assertTrue(attraction.describes_appeal(self.appealing))
        self.assertFalse(attraction.describes_appeal(self.plain))
        self.assertFalse(attraction.describes_appeal(self.named))

    def test_appealing_perfumes_lead_equal_fits_but_never_a_better_fit(self):
        pair = [self.plain, self.appealing]
        sweet = {"scent_tags": ["sweet"], "gender": "male"}
        self.assertEqual(order_candidates(pair, sweet)[0], self.plain)
        self.assertEqual(order_candidates(pair, {**sweet, attraction.GOAL_KEY: "women"})[0], self.appealing)
        both = {"scent_tags": ["sweet", "spicy"], "gender": "male", attraction.GOAL_KEY: "women"}
        self.assertEqual(order_candidates([self.appealing, self.spicy_plain], both)[0], self.spicy_plain)

    def test_attraction_claims_need_the_description_on_this_goal(self):
        facts = product_claim_facts([self.plain, self.appealing])
        goal = "عايز برفان يخلي البنات تحبني"

        def appeal_issues(reply, message):
            return [issue for issue in product_claim_issues(reply, facts, message=message) if issue.startswith("appeal")]

        self.assertTrue(appeal_issues("Plain Sweet ريحته جذابة.", goal))
        self.assertEqual(appeal_issues("Velvet Night ريحته جذابة.", goal), [])
        self.assertEqual(appeal_issues("Plain Sweet مش موصوف عندنا إنه جذاب.", goal), [])
        # Unchanged on other turns: only the goal turns are held to the description.
        self.assertEqual(appeal_issues("Plain Sweet ريحته جذابة.", "عايز حاجه مسكره"), [])
        self.assertTrue(product_claim_issues("Plain Sweet هيخلي البنات تحبك", facts, appeal_goal=True))

    def test_the_note_names_only_appealing_offers(self):
        intent = {attraction.GOAL_KEY: "women"}
        reply = "Velvet Night ريحته مسكرة وخشبية، وPlain Sweet برضه مسكر وخشبي."
        noted = attraction.appeal_note(reply, [self.plain, self.appealing], intent)
        # Said plainly: the old "موصوف عندنا إنه جذاب" came back from the model as "ووصفه عندنا جذاب".
        self.assertTrue(noted.endswith("\n\nVelvet Night ريحته جذابة."), noted)
        self.assertEqual(attraction.appealing_line(["A", "B"]), "A وB ريحتهم جذابة.")
        self.assertEqual(attraction.appeal_note(reply, [self.plain, self.appealing], {}), reply)
        self.assertEqual(attraction.appeal_note("Plain Sweet ريحته مسكرة.", [self.plain], intent),
                         "Plain Sweet ريحته مسكرة.")

    def test_named_perfume_question_gets_the_rules(self):
        from products.services.product_info import get_product_info

        conversation = Conversation.objects.create(store=self.store)
        with patch("products.services.product_info.resolve_products", return_value=[self.appealing]), \
             patch("products.services.product_info.chat", return_value="ok") as chat_call:
            get_product_info("Velvet Night هيخلي البنات تحبني؟", [], self.store, conversation)
        prompt = "\n".join(message["content"] for message in chat_call.call_args[0][0])
        self.assertIn(attraction.named_rules([self.appealing]), prompt)
        self.assertIn("Velvet Night", attraction.named_rules([self.appealing]))
        self.assertIn("ممنوع تقول عن أي عطر هنا إنه جذاب", attraction.named_rules([self.plain]))
        for perfumes in ([self.appealing], [self.plain]):
            self.assertNotIn("موصوف", attraction.named_rules(perfumes).replace("مسجلة أو موصوفة عندنا", ""))
        self.assertIn("ممنوع تنصحه", attraction.named_rules([self.plain]))


class RecommendationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Attraction flow")
        StoreSettings.objects.create(store=cls.store)
        brand = Brand.objects.create(store=cls.store, name="House")
        for name, tags, description, choice in (
            ("Plain Sweet", ["sweet", "woody"], "A warm sweet-woody scent.", "male"),
            ("Velvet Night", ["sweet", "woody"], "A warm sweet-woody scent with a seductive touch.", "male"),
            ("Fresh Man", ["fresh"], "A clean fresh scent.", "male"),
            ("Women Sweet", ["sweet", "woody"], "A sweet scent with a seductive touch.", "female"),
        ):
            product = Product.objects.create(store=cls.store, brand=brand, name=name, gender=choice,
                                             scent_tags=tags, description=description)
            ProductVariant.objects.create(product=product, volume=50, price=400)

    def setUp(self):
        self.conversation = Conversation.objects.create(store=self.store)
        self.history = []
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))
        self.enterContext(patch("products.services.router.confident_catalogue_match", return_value=None))

    def turn(self, message, updates=None, classification="recommendation"):
        def extract(text, history=None, store=None):
            return intent_module._with_appeal_goal(dict(updates or {}), text)

        with patch("products.services.router.classify", return_value=classification), \
             patch("products.services.router.extract_intent", side_effect=extract), \
             patch("products.services.router.recommend", return_value=("اختيار مناسب", "selected")) as recommender:
            result = route(message, list(self.history), self.store, self.conversation)
        save_message(self.conversation, "user", message)
        save_message(self.conversation, "assistant", *result)
        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": result[0]}]
        return result, recommender

    def test_conversation_1345_asks_the_gender_and_ranks_appealing_perfumes_first(self):
        result, model = self.turn("عايز برفان يخلي البنات تحبني")
        self.assertEqual(result, (attraction.ACKNOWLEDGEMENT + "\n" + gender.QUESTION, gender.REQUIRED_MARKER))
        model.assert_not_called()
        self.conversation.refresh_from_db()
        self.assertNotIn("gender", self.conversation.preferences)
        self.assertEqual(self.conversation.preferences[attraction.GOAL_KEY], "women")

        result, model = self.turn("رجالي", classification="faq")
        self.assertEqual(result, (scent_answers.PREFERENCE_QUESTION, "SCENT_PREFERENCE_REQUIRED"))

        result, model = self.turn("مسكر", {"scent_tags": ["sweet"]})
        self.assertEqual(result[1], "selected")
        intent = model.call_args.kwargs["intent"]
        self.assertEqual(intent["gender"], "male")
        self.assertEqual(intent[attraction.GOAL_KEY], "women")
        self.assertEqual([p.name for p in model.call_args.args[1]][:2], ["Velvet Night", "Plain Sweet"])

    def test_conversation_1346_acknowledges_and_still_reads_the_scent_answer(self):
        result, _ = self.turn("عايز برفان رجالي يخلي البنات تحبني")
        self.assertEqual(result, (attraction.ACKNOWLEDGEMENT + "\n" + scent_answers.PREFERENCE_QUESTION,
                                  "SCENT_PREFERENCE_REQUIRED"))
        result, model = self.turn("اول حاجة")
        self.assertEqual(result[1], "selected")
        self.assertEqual(model.call_args.kwargs["intent"]["scent_tags"], ["fresh"])
