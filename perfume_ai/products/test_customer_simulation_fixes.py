"""Regression tests for defects found by the 20 adaptive customer simulations.

Each test names the scenario and turn in artifacts/customer_simulation_20/findings.md where the
defect was observed in the baseline run.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Product, ProductVariant, Store
from products.services.ai.prompts import get_system_prompt
from products.services.product_formatting import (
    ProductQuote, QUOTE_PROSE_INSTRUCTION, render_quotes, requested_quote_options,
)
from products.services.sales import scent_answers


class ScentQuestionAfterDontKnowTests(SimpleTestCase):
    """S01 t3-t4: "مش عارف" got the identical scripted scent question twice."""

    def history(self, question):
        return [{"role": "user", "content": "عايز برفان حلو"}, {"role": "assistant", "content": question}]

    def test_dont_know_gets_everyday_choices_instead_of_the_same_question(self):
        for answer in ("مش عارف والله", "يا عم مش فاهم الكلام ده", "مش عارفة", "معرفش"):
            with self.subTest(answer=answer):
                question = scent_answers.next_question(answer, self.history(scent_answers.PREFERENCE_QUESTION))
                self.assertEqual(question, scent_answers.SIMPLE_PREFERENCE_QUESTION)
                self.assertNotEqual(question, scent_answers.PREFERENCE_QUESTION)

    def test_other_unanswered_turns_keep_the_standard_question(self):
        self.assertEqual(scent_answers.next_question("عايز برفان حلو", []), scent_answers.PREFERENCE_QUESTION)

    def test_simple_question_is_pending_and_its_choices_are_understood(self):
        history = self.history(scent_answers.SIMPLE_PREFERENCE_QUESTION)
        self.assertTrue(scent_answers.question_pending(history))
        self.assertEqual(scent_answers.answer_updates("الأولى", history)["scent_tags"], ["fresh"])
        self.assertEqual(scent_answers.answer_updates("التانية", history)["scent_tags"], ["sweet"])

    def test_still_not_knowing_after_the_simple_question_delegates_the_choice(self):
        history = self.history(scent_answers.SIMPLE_PREFERENCE_QUESTION)
        for answer in ("مش عارف برضه", "معرفش", "اختارلي انت", "مش فارقة"):
            with self.subTest(answer=answer):
                self.assertTrue(scent_answers.answer_updates(answer, history).get("scent_open"))

    def test_simple_question_is_one_question(self):
        self.assertEqual(scent_answers.SIMPLE_PREFERENCE_QUESTION.count("؟"), 1)


class BareSizeInPriceQuestionTests(SimpleTestCase):
    """S04 t1-t2: "سوفاج 90 بكام؟" and "والـ50؟" quoted every size."""

    def test_bare_bottle_sizes_are_requested_sizes(self):
        for message, sizes in (("سوفاج 90 بكام؟", [90]), ("والـ50؟", [50]), ("الـ90 بكام", [90]),
                               ("50 ملي بكام", [50]), ("عايز الـ 100", [100])):
            with self.subTest(message=message):
                self.assertEqual(requested_quote_options(message)[0], sizes)

    def test_budgets_prices_and_quantities_are_not_sizes(self):
        for message in ("في حدود 700", "معايا 1600 جنيه بس", "عايز 2 برفان", "بـ 750 جنيه", "ميزانيتي 100 جنيه",
                        "around 800 keda", "لحد 1500"):
            with self.subTest(message=message):
                self.assertEqual(requested_quote_options(message)[0], [])


class NaturalPriceFormatTests(SimpleTestCase):
    """Every quoted price read "1000.00 جنيه" — a database decimal, not how a seller talks."""

    def test_whole_prices_render_without_decimals(self):
        quote = ProductQuote(product_id=1, variant_id=2, name="Sauvage", volume=90, bottle_type="normal",
                             price=Decimal("1000.00"), quantity=1)
        text = render_quotes([quote])
        self.assertIn("بـ1000 جنيه", text)
        self.assertNotIn("1000.00", text)

    def test_fractional_prices_keep_their_fraction(self):
        quote = ProductQuote(product_id=1, variant_id=2, name="X", volume=50, bottle_type="normal",
                             price=Decimal("642.50"), quantity=1)
        self.assertIn("642.50", render_quotes([quote]))


class NoUnkeepablePromisesInPromptTests(SimpleTestCase):
    """Pilot S11 t2: a known price was called "محتاج تأكيد من الفريق"; the persona told the model
    to answer unknowns with "هسأل وأرد عليك" although nothing follows up after a reply."""

    def test_persona_no_longer_scripts_a_follow_up_promise(self):
        prompt = get_system_prompt(None)
        self.assertNotIn('قول "هسأل وأرد عليك يا فندم"', prompt)
        self.assertIn("ممنوع توعد إنك هتسأل وترجعله", prompt)

    def test_withheld_prices_are_described_as_appended_not_unknown(self):
        self.assertIn("متقولش إن السعر", QUOTE_PROSE_INSTRUCTION)


class ScentPolarityTests(SimpleTestCase):
    """S10 t1: "مش عايز أي حاجة مسكرة ولا فيها عود" searched FOR sweet; S01 t6 excluded "light"."""

    def reconcile(self, message, raw):
        from products.services.sales.scent_preferences import reconcile_scent_intent
        return reconcile_scent_intent(dict(raw), message, require_evidence=True)

    def test_model_negative_evidence_with_any_is_kept(self):
        out = self.reconcile("عايز برفان رجالي، بس مش عايز أي حاجة مسكرة ولا فيها عود خالص", {
            "scent_evidence": [
                {"text": "مش عايز أي حاجة مسكرة", "tags": ["sweet"], "polarity": "negative"},
                {"text": "ولا فيها عود خالص", "tags": ["oud"], "polarity": "negative"}],
            "excluded_scent_tags": ["sweet", "oud"]})
        self.assertEqual(out.get("scent_tags") or [], [])
        self.assertEqual(sorted(out["excluded_scent_tags"]), ["oud", "sweet"])

    def test_negation_does_not_cross_into_a_stated_positive_phrase(self):
        out = self.reconcile("مش عايزها تقيلة، حاجة هادية تنفع للجامعة", {
            "scent_evidence": [
                {"text": "مش عايزها تقيلة", "tags": ["heavy"], "polarity": "negative"},
                {"text": "حاجة هادية", "tags": ["light"], "polarity": "positive"}]})
        self.assertEqual(out["excluded_scent_tags"], ["heavy"])
        self.assertEqual(out["scent_tags"], ["light"])

    def test_regex_only_path_reads_any_and_literal_exclusions(self):
        from products.services.sales.scent_preferences import explicit_scent_updates
        self.assertEqual(sorted(explicit_scent_updates("مش عايز أي حاجة مسكرة ولا فيها عود")["excluded_scent_tags"]),
                         ["oud", "sweet"])
        # A question about what a perfume contains is not a wish.
        self.assertNotIn("scent_tags", explicit_scent_updates("فيه عود؟"))


class ClaimNegationTests(SimpleTestCase):
    """S02 t1, S08 t3: correct drafts were rejected, so the customer got "ماقدرش أأكد المعلومة دي"."""

    def test_negated_scent_words_are_denials_not_claims(self):
        from products.services.sales.scent_preferences import explicit_scent_updates
        for draft, tag in (("Layton فاكهي وخشبي من غير تاج دخاني", "smoky"),
                           ("ريحته فريش، ومش مسجل إنه تقيل", "heavy")):
            with self.subTest(draft=draft):
                claims = explicit_scent_updates(draft, for_claims=True)
                self.assertNotIn(tag, claims.get("scent_tags", []))

    def test_note_layer_and_dry_down_questions_authorise_notes(self):
        from products.services.scent_description import notes_requested
        self.assertTrue(notes_requested("طب Layton الـ top/middle/base بتاعته إيه؟", product_names=("Layton",)))
        self.assertTrue(notes_requested("عايز أقارن الـ dry down بينهم"))

    def test_suitability_question_with_arabic_name_is_a_question(self):
        from products.services.scent_description import occasion_requested
        self.assertTrue(occasion_requested("طب وإيروس ينفع للشغل ولا تقيل؟", product_names=("Eros",)))
        self.assertFalse(occasion_requested("رشحلي عطر ينفع للشغل"))

    def test_denying_an_unrecorded_use_of_a_recorded_occasion_is_allowed(self):
        from products.services.scent_description import description_issues
        facts = {"Eros": {"scent_tags": ["fresh", "sweet", "woody"], "notes": [], "occasion": "Night Out & Hanging Out"}}
        issues = description_issues("Eros مناسبته المسجلة الخروجات بالليل، مش الشغل.", facts,
                                    message="طب وإيروس ينفع للشغل؟")
        self.assertFalse([i for i in issues if "occasion" in i], issues)


class NoteClaimNegationTests(SimpleTestCase):
    """S02 t2/t6 (after the first fixes): honest note answers were still rejected."""

    facts = {"Layton": {"notes": ["Apple, Bergamot, Lavender", "Jasmine, Violet, Geranium",
                                  "Vanilla, Pepper, Guaiac Wood, Patchouli"], "scent_tags": ["fruity", "woody", "sweet"]},
             "Aventus": {"notes": ["Pineapple, Bergamot", "Birch, Patchouli", "Musk, Oakmoss, Ambergris, Vanilla"],
                         "scent_tags": ["woody", "fruity", "smoky"]}}

    def issues(self, draft, name, message):
        from products.services.reply_sanitizer import product_claim_issues
        return [i for i in product_claim_issues(draft, {name: self.facts[name]}, message=message, history=[])
                if i.startswith("note:")]

    def test_predicate_negation_after_an_ingredient_is_a_denial(self):
        self.assertEqual(self.issues("الـCardamom مش موجود ضمن النوتات المسجلة لـLayton.", "Layton",
                                     "فيه cardamom؟"), [])

    def test_ambergris_is_not_amber(self):
        self.assertEqual(self.issues("الـBase notes: مسك وطحلب بلوط وعنبر رمادي وفانيليا.", "Aventus",
                                     "Aventus الـ base notes إيه؟"), [])

    def test_an_invented_ingredient_is_still_caught(self):
        self.assertEqual(self.issues("Layton فيه Cardamom.", "Layton", "فيه هيل؟"), ["note:layton:cardamom"])


class FrancoGenderTests(SimpleTestCase):
    """S13: "rigali" was never accepted and the gender question went out seven times."""

    def test_franco_category_words(self):
        from products.services.sales import gender
        self.assertEqual(gender.customer_choice("hi, 3ayez perfume rigali lel sef"), "male")
        self.assertEqual(gender.customer_choice("rigali ya basha"), "male")
        self.assertEqual(gender.customer_choice("7arimi"), "female")


class StatedPreferenceUpdateTests(TestCase):
    """S14 t2/t4: an exclusion and a budget said inside a question were never saved."""

    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Sim Store")
        self.conversation = Conversation.objects.create(store=self.store, preferences={
            "gender": "male", "scent_tags": ["fresh"], "scent_match": "all", "scent_open": False, "max_price": 1000})

    def test_exclusion_and_budget_statements(self):
        from products.services.sales.constraints import stated_preference_updates
        self.assertEqual(stated_preference_updates("طب ريحته عاملة إزاي؟ وعلى فكرة مش بحب العود خالص"),
                         {"excluded_scent_tags": ["oud"]})
        self.assertEqual(stated_preference_updates("سويت قد إيه؟ والميزانية خليها لحد 1500")["max_price"], 1500.0)
        for question in ("فيه عود؟", "الـ90 بكام؟", "عايز 50 ملي", "ريحته مسكرة؟"):
            with self.subTest(question=question):
                self.assertEqual(stated_preference_updates(question), {})

    def test_merge_keeps_saved_preferences_and_adds_the_statement(self):
        from products.services.conversation_service import merge_preferences
        merge_preferences(self.conversation, {"excluded_scent_tags": ["oud"]}, None, store=self.store)
        merge_preferences(self.conversation, {"max_price": 1500.0}, None, store=self.store)
        self.conversation.refresh_from_db()
        prefs = self.conversation.preferences
        self.assertEqual(prefs["gender"], "male")
        self.assertEqual(prefs["scent_tags"], ["fresh"])
        self.assertEqual(prefs["excluded_scent_tags"], ["oud"])
        self.assertEqual(prefs["max_price"], 1500.0)


class RecipientProfileTests(TestCase):
    """S15: shopping for his mother overwrote his own profile, and "نرجع لبتاعي" never restored it."""

    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Recipient Store")
        self.conversation = Conversation.objects.create(store=self.store)

    def merge(self, intent, message):
        from products.services.conversation_service import merge_preferences
        merge_preferences(self.conversation, intent, message, store=self.store)
        self.conversation.refresh_from_db()
        return self.conversation.preferences

    def test_recipients_keep_separate_profiles(self):
        mine = self.merge({"scent_tags": ["woody"]}, "رجالي ليا، بحب الريحة الخشبية")
        self.assertEqual((mine["gender"], mine["scent_tags"]), ("male", ["woody"]))
        mom = self.merge({"scent_tags": ["floral"], "reset_preferences": True},
                         "وكمان عايز حاجة لماما تكون هادية فيها ورد")
        self.assertEqual(mom.get("gender"), "female")
        self.assertNotIn("woody", mom.get("scent_tags", []))
        back = self.merge({}, "طب نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟")
        self.assertEqual((back["gender"], back["scent_tags"]), ("male", ["woody"]))

    def test_recipient_detection(self):
        from products.services.conversation_service import recipient_of
        self.assertEqual(recipient_of("عايز هدية لخطيبتي"), "fiancee")
        self.assertEqual(recipient_of("عايز حاجة لماما"), "mother")
        self.assertEqual(recipient_of("نرجع لبتاعي"), "self")
        self.assertIsNone(recipient_of("واحد ليا وواحد لأخويا"))
        self.assertIsNone(recipient_of("ريحته حلوة؟"))


class TotalBudgetLimitTests(SimpleTestCase):
    """S03 t3-t6: three bottles for 1600 got a generic "change your budget?" three times."""

    def test_reply_names_the_real_limitation_and_the_feasible_quantity(self):
        from products.services.ai.scent_recommendation import total_budget_limit_reply
        reply, context = total_budget_limit_reply(3, [Decimal("750")] * 5, Decimal("1600"))
        self.assertIn("2250", reply)
        self.assertIn("1600", reply)
        self.assertIn("2 زجاجات", reply)
        self.assertEqual(context, "NO_BUDGET_MATCH")


class CheckoutTests(TestCase):
    """S11/S19: no way to decline an alternate number; total shown without saying shipping is extra."""

    def setUp(self):
        from products.models import Cart, Conversation, StaticFAQ
        self.store = Store.objects.create(name="Checkout Store")
        StaticFAQ.objects.create(store=self.store, question="بكام مصاريف الشحن؟", keywords="شحن+بكام,شحن",
                                 answer="مصاريف الشحن 60 جنيه لجميع محافظات مصر", is_active=True)
        brand = Brand.objects.create(store=self.store, name="Dior")
        product = Product.objects.create(store=self.store, brand=brand, name="Sauvage", gender="male")
        self.variant = ProductVariant.objects.create(product=product, volume=90, price=Decimal("1000.00"),
                                                     bottle_type="normal")
        self.conversation = Conversation.objects.create(store=self.store)
        self.cart = Cart.objects.create(conversation=self.conversation, customer_name="كريم سامي",
                                        customer_phone="01011223344", shipping_address="12 شارع مصدق، الدقي")

    def test_declining_the_alternate_number_is_remembered(self):
        from products.services.order_service import _secondary_phone_declined
        self.assertFalse(_secondary_phone_declined(self.conversation, "كريم سامي 01011223344"))
        for message in ("معنديش رقم بديل، ينفع الأساسي بس؟", "مش معايا رقم بديل، كمل بالرقم ده"):
            with self.subTest(message=message):
                self.assertTrue(_secondary_phone_declined(self.conversation, message))
        self.conversation.refresh_from_db()
        self.assertTrue(_secondary_phone_declined(self.conversation, "تمام"))

    def checkout(self, message, **updates):
        import json
        from products.services.conversation_service import save_message
        from products.services.order_service import handle_order
        data = {"products": [{"name": "Sauvage", "volume": 90, "quantity": 1, "bottle_type": "normal"}],
                "customer_name": "كريم سامي", "customer_phone": "01011223344", "customer_secondary_phone": None,
                "shipping_address": "12 شارع مصدق، الدقي، الجيزة", "is_confirmed": False}
        data.update(updates)
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            reply, context = handle_order(message, [], self.store, self.conversation)
        save_message(self.conversation, "assistant", reply, internal_context=context)
        return reply

    def test_declined_alternate_number_still_completes_the_order(self):
        """Replay S19 t8: the declined number reached the NOT NULL column as None and the order failed."""
        from products.models import Order
        asked = self.checkout("كريم سامي، 01011223344، 12 شارع مصدق، الدقي، الجيزة")
        self.assertIn("بديل", asked)
        summary = self.checkout("معنديش رقم بديل، كمل بالأساسي")
        self.assertIn("💰 الإجمالي", summary)
        self.assertFalse(Order.objects.exists())
        confirmed = self.checkout("تمام أكد", is_confirmed=True)
        order = Order.objects.get(conversation=self.conversation)
        self.assertEqual(order.secondary_phone, "")
        self.assertIn(f"#{order.pk}", confirmed)

    def test_summary_total_says_shipping_is_separate_and_quotes_the_store_fee(self):
        from products.services.order_service import _quote_summary, CONFIRMATION_SUMMARY_MARKER
        items = [{"variant": self.variant, "quantity": 1, "price": self.variant.price, "bottle_type": "normal"}]
        summary = _quote_summary(self.conversation, self.cart, items)
        self.assertIn(CONFIRMATION_SUMMARY_MARKER, summary)
        self.assertIn("1000 جنيه (من غير الشحن)", summary)
        self.assertIn("مصاريف الشحن 60 جنيه", summary)

    def _order(self, total="1000"):
        from products.models import Order, OrderItem
        order = Order.objects.create(store=self.store, customer_name="كريم سامي", customer_phone="01011223344",
                                     shipping_address="12 شارع مصدق", total_price=Decimal(total),
                                     conversation=self.conversation)
        OrderItem.objects.create(order=order, variant=self.variant, quantity=1, bottle_type="normal",
                                 price_at_time_of_order=Decimal(total))
        return order

    def test_total_with_shipping_is_computed_from_the_order_and_the_flat_fee(self):
        """S11 attempt 3 t5: "بس الإجمالي 810 بالشحن صح؟" ended the confirmation with "تقصد أنهي عطر وحجم؟"."""
        from products.services.order_service import amount_with_shipping
        self._order()
        self.assertEqual(amount_with_shipping(self.conversation, "تمام أكد، بس الإجمالي 1060 بالشحن صح"),
                         "أيوه، الطلب 1000 جنيه + الشحن 60 جنيه = 1060 جنيه.")
        self.assertEqual(amount_with_shipping(self.conversation, "يعني الإجمالي 1100 بالشحن؟"),
                         "لأ، الطلب 1000 جنيه + الشحن 60 جنيه = 1060 جنيه.")
        self.assertEqual(amount_with_shipping(self.conversation, "الإجمالي كام بالشحن"),
                         "الطلب 1000 جنيه + الشحن 60 جنيه = 1060 جنيه.")
        self.assertIsNone(amount_with_shipping(self.conversation, "الشحن بياخد كام يوم"))

    def test_open_cart_is_the_total_being_asked_about(self):
        from products.models import CartItem
        from products.services.order_service import amount_with_shipping
        self._order(total="750")
        CartItem.objects.create(cart=self.cart, variant=self.variant, quantity=2, bottle_type="normal")
        self.assertEqual(amount_with_shipping(self.conversation, "المجموع بالشحن كام"),
                         "الطلب 2000 جنيه + الشحن 60 جنيه = 2060 جنيه.")

    def test_shipping_answer_with_two_amounts_is_quoted_not_summed(self):
        from products.models import StaticFAQ
        from products.services.order_service import amount_with_shipping
        answer = "الشحن 60 جنيه للقاهرة و80 جنيه لباقي المحافظات"
        StaticFAQ.objects.filter(store=self.store).update(answer=answer)
        self._order()
        reply = amount_with_shipping(self.conversation, "الإجمالي بالشحن كام")
        self.assertEqual(reply, "الطلب 1000 جنيه من غير الشحن.\n" + answer)
        self.assertNotIn("1060", reply)

    def test_confirmation_with_a_total_question_gets_the_sum_not_a_general_reply(self):
        from products.services import router
        self.checkout("كريم سامي، 01011223344، 12 شارع مصدق، الدقي، الجيزة، معنديش رقم بديل")
        message = "تمام أكد، بس الإجمالي 1060 بالشحن صح؟"
        confirmed = self.checkout(message, is_confirmed=True)
        self.assertIn("طلبك اتأكد", confirmed)
        with patch.object(router, "handle_general", side_effect=AssertionError("no general reply")):
            reply = router._with_side_answers(message, confirmed, [], self.store, self.conversation)
        self.assertTrue(reply.endswith("أيوه، الطلب 1000 جنيه + الشحن 60 جنيه = 1060 جنيه."))
        self.assertNotIn("تقصد", reply)

    def test_fee_question_beside_the_details_gets_the_store_answer_not_a_general_reply(self):
        """S11 final t4: "مفيش رقم بديل، والشحن كده 60؟" got a general reply asking for the details again."""
        from products.services import router
        message = "تمام، الاسم كريم سامي، الموبايل 01011223344، والعنوان 12 شارع مصدق. مفيش رقم بديل، والشحن كده 60؟"
        with patch.object(router, "handle_general", side_effect=AssertionError("no general reply")):
            reply = router._with_side_answers(message, "راجع معايا تفاصيل الطلب", [], self.store, self.conversation)
            again = router._with_side_answers(message, reply, [], self.store, self.conversation)
        self.assertEqual(reply, "راجع معايا تفاصيل الطلب\n\nمصاريف الشحن 60 جنيه لجميع محافظات مصر")
        self.assertEqual(again, reply)

    def test_general_reply_restating_the_customers_order_passes_the_price_check(self):
        """S11 attempt 3 t6: the confirmed order's own price was rejected for want of product rows."""
        from products.services import router
        self._order()
        draft = "تمام يا فندم، طلبك Sauvage 90 ملي بـ1000 جنيه متسجل، والإجمالي بالشحن 1060 جنيه."
        message = "أنا أكدته خلاص، Sauvage الـ90. هحوّل العربون وأبعتلكم السكرين."
        quiet = (patch.object(router.repetition, "repeated_sentences", return_value=[]),
                 patch.object(router.repetition, "repeated_frames", return_value=[]))
        with quiet[0], quiet[1]:
            without_order, _, _ = router._rephrased(draft, "", [], self.store, lambda hint: (draft, ""),
                                                    message=message)
            token = router._TURN_CONVERSATION.set(self.conversation)
            try:
                with_order, _, _ = router._rephrased(draft, "", [], self.store, lambda hint: (draft, ""),
                                                     message=message)
            finally:
                router._TURN_CONVERSATION.reset(token)
        self.assertIn("تقصد", without_order)
        self.assertEqual(with_order, draft)

    def test_turn_conversation_is_scoped_to_one_routed_reply(self):
        from products.services import router
        seen = []
        with patch.object(router, "_route", side_effect=lambda *a: (seen.append(router._TURN_CONVERSATION.get()) or ("تمام", ""))):
            router.route("تمام", [], self.store, self.conversation)
        self.assertEqual(seen, [self.conversation])
        self.assertIsNone(router._TURN_CONVERSATION.get())


class SmallRoutingFixesTests(TestCase):
    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Routing Store")
        brand = Brand.objects.create(store=self.store, name="House")
        for name, gender, price in (("Male One", "male", "750"), ("Female One", "female", "800")):
            product = Product.objects.create(store=self.store, brand=brand, name=name, gender=gender)
            ProductVariant.objects.create(product=product, volume=50, price=Decimal(price), bottle_type="normal")
        self.conversation = Conversation.objects.create(store=self.store, preferences={"gender": "male"})

    def test_ordinal_smell_question_is_not_a_price_question(self):
        """S12 t4: smell question after a shipping-fee reply got an unrequested price block."""
        from products.services.product_formatting import price_question
        self.assertFalse(price_question("طب الاولانى ريحته عامله ازاى", [], None))
        self.assertTrue(price_question("التانى بكام", [], None))

    def test_price_floor_follows_the_saved_gender(self):
        """S11 t2: the price objection needs the real floor to answer "غالي عليا" honestly."""
        from products.services.objection_service import _price_floor
        self.assertEqual(_price_floor(self.store, self.conversation), Decimal("750"))

    def test_seller_praise_and_closing(self):
        """S05 t6 / S01 t7: praise of the seller was ignored; S18 t5: thanks after declining."""
        from products.services.sales import appreciation
        self.assertIsNotNone(appreciation.detect("ذوقك حلو، فيه منه زجاجة أورجينال؟"))
        self.assertTrue(appreciation.thanks_or_praise("ترشيحاتك عجباني، تمام شكراً هفكر فيهم"))
        self.assertTrue(appreciation.closes_conversation("لا شكراً، كنت بتفرج بس. تمام شكراً"))
        self.assertFalse(appreciation.closes_conversation("شكرا بس بكام الـ50؟"))

    def test_identical_faq_answer_is_not_resent(self):
        """S16 t2/t4: a keyword hit re-sent the same authenticity answer to a different question."""
        from products.models import StaticFAQ
        from products.services.router import route
        answer = "العطور بتاعتنا كلها عطور تركيب."
        StaticFAQ.objects.create(store=self.store, question="أصلي ولا تركيب؟", keywords="اصلي+ولا", answer=answer,
                                 is_active=True)
        history = [{"role": "user", "content": "اصلي ولا تقليد؟"}, {"role": "assistant", "content": answer}]
        # The message reaches the objection branch; its resolver and model are mocked so the test never
        # calls the real API (it did, under a settings file that loaded the .env key).
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router._handle_general_raw", return_value=("رد جديد على السؤال", "")), \
             patch("products.services.objection_service.resolve_products", return_value=[]), \
             patch("products.services.objection_service.chat", return_value="رد جديد على السؤال"):
            reply, _ = route("يعني اصلي ولا لأ؟", history, self.store, self.conversation)
        self.assertNotEqual(reply, answer)


class ReplayFollowUpTests(SimpleTestCase):
    """Defects found in the first post-fix debug replay (S03, S05, S10)."""

    def test_a_size_inside_a_scent_question_is_not_a_price_request(self):
        from products.services.product_formatting import price_question
        self.assertFalse(price_question("طب الـ90 ملي ده فريش فعلاً ومش تقيل؟", [], None))
        self.assertTrue(price_question("الـ90 ملي بكام؟", [], None))
        self.assertTrue(price_question("عندك 50 ملي؟", [], None))

    def test_total_questions_in_a_combined_budget_are_recognised(self):
        from products.services.router import _TOTAL_QUESTION
        from products.services.static_faq_service import normalize_arabic
        for message in ("طب الإجمالي كده 1500 بالظبط؟", "ولو عايز 3 بنفس الـ1600 ينفع؟", "طب كده الاتنين هيطلعوا بكام؟"):
            with self.subTest(message=message):
                self.assertTrue(_TOTAL_QUESTION.search(normalize_arabic(message)))
        self.assertFalse(_TOTAL_QUESTION.search(normalize_arabic("Blue Jeans ريحته عاملة ازاي؟")))

    def test_contrast_prefix_is_read_as_a_scent_label(self):
        from products.services.scent_description import description_issues
        facts = {"Alexandria II": {"scent_tags": ["woody", "oud", "heavy"], "notes": [], "occasion": ""}}
        issues = description_issues("أما Alexandria II فخشبي وعودي وتقيل.", facts, message="إيه اللي يميزه؟")
        self.assertFalse([i for i in issues if "first three" in i], issues)


class ReplayFollowUpTwoTests(TestCase):
    """Defects found later in the first post-fix replay (S11, S16, S18)."""

    def test_exact_price_question_is_not_a_strict_budget(self):
        from products.services.sales.constraints import explicit_updates
        self.assertNotIn("budget_strict", explicit_updates("غالي عليا، الـ50 بكام بالظبط؟"))
        self.assertTrue(explicit_updates("معايا 700 بالظبط")["budget_strict"])

    def test_duration_written_with_a_glued_article_is_a_duration_question(self):
        from products.services.performance_policy import request_for
        request = request_for("طب الـ٨ ساعات دي على الجلد ولا الهدوم؟", [])
        self.assertTrue(request.longevity and request.hours)

    def test_general_prompt_carries_a_grounded_catalogue_overview(self):
        store = Store.objects.create(name="Overview Store")
        brand = Brand.objects.create(store=store, name="House")
        product = Product.objects.create(store=store, brand=brand, name="Cedar", gender="male", perfume_type="western")
        ProductVariant.objects.create(product=product, volume=50, price=Decimal("750.00"), bottle_type="normal")
        ProductVariant.objects.create(product=product, volume=90, price=Decimal("1000.00"), bottle_type="normal")
        prompt = get_system_prompt(store)
        self.assertIn("ملخص الكتالوج الحالي", prompt)
        self.assertIn("مفيش بيانات مبيعات", prompt)
        # Owner decision 2026-11-12: the price range comes from the business facts only.
        self.assertNotIn("750 لـ 1000", prompt)

    def test_store_figures_in_the_prompt_come_from_business_facts_only(self):
        """Owner decision 2026-11-12: the business facts are the only source of store figures. They now
        say 95% and 750-2000; an FAQ entry restating an older figure must not reach the model."""
        from products.models import StaticFAQ, StoreSettings
        store = Store.objects.create(name="Figures Store")
        StoreSettings.objects.create(store=store, business_facts="- نسبة التطابق حوالي 95%. الاسعار من 750 لحد 2000")
        StaticFAQ.objects.create(store=store, question="أصلي ولا تركيب؟", keywords="اصلي", is_active=True,
                                 answer="كلها عطور تركيب وتطابق بيوصل لـ 90%")
        StaticFAQ.objects.create(store=store, question="الاستبدال؟", keywords="استبدال", is_active=True,
                                 answer="الاستبدال خلال يومين من الاستلام")
        prompt = get_system_prompt(store)
        self.assertIn("95%", prompt)
        self.assertIn("750 لحد 2000", prompt)
        self.assertNotIn("90%", prompt)
        self.assertIn("الاستبدال خلال يومين من الاستلام", prompt)

    def test_last_resort_without_product_data_offers_the_team(self):
        from products.services.router import _rephrased
        store = Store.objects.create(name="Fallback Two")
        bad = "Imaginary Oud ريحته عود وثباته 48 ساعة."
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
             patch("products.services.router.repetition.repeated_frames", return_value=[]), \
             patch("products.services.router.product_claim_issues", create=True, return_value=[]), \
             patch("products.services.reply_sanitizer.product_claim_issues", return_value=["note:x:y"]):
            reply, _, _ = _rephrased(bad, "", [], store, lambda hint: (bad, ""), message="الجلد ولا الهدوم؟")
        self.assertIn("فريق خدمة العملاء", reply)
        self.assertNotIn("أنهي عطر", reply)


class ReplayTwoTests(TestCase):
    """Defects found in the second post-fix debug replay (S05, S06, S10, S11)."""

    def test_cannot_guarantee_is_not_a_price_objection(self):
        from products.services.sales.objection import detect
        self.assertIsNone(detect("لو مش قادر تضمن إنه مش مسكر رشحلي حاجة تانية"))
        self.assertEqual(detect("غالي، مش قادر").kind, "price")
        self.assertEqual(detect("مش قادر أدفع 1000").kind, "price")

    def test_original_bottle_question_is_answered_by_its_quote(self):
        from products.services.product_formatting import price_question, requested_quote_options
        self.assertTrue(price_question("ذوقك حلو، فيه منه زجاجة أورجينال؟", [], None))
        self.assertEqual(requested_quote_options("فيه منه زجاجة أورجينال؟")[1], "original")
        self.assertFalse(price_question("الزجاجة الاوريجنال ريحتها زي التركيب؟", [], None))

    def test_order_total_in_a_confirmation_is_not_a_combined_budget(self):
        from products.services.sales.constraints import explicit_updates
        self.assertNotIn("budget_scope", explicit_updates("تمام أكد، والإجمالي 810 شامل الشحن."))
        self.assertEqual(explicit_updates("معايا 1000 جنيه إجمالي للاتنين مع بعض")["budget_scope"], "total")

    def test_unrecorded_use_states_the_recorded_one_and_needs_confirmation(self):
        from products.services.price_reply import requested_product_details
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues
        store = Store.objects.create(name="Occasion Store")
        brand = Brand.objects.create(store=store, name="Cassilia")
        product = Product.objects.create(store=store, brand=brand, name="Cassilia", gender="female",
                                         scent_tags=["floral", "fruity", "light"], occasion="Beach, Summer & Gym")
        message = "طب هو مناسب للاستخدام اليومي ولا لأ؟"
        line = "\n".join(requested_product_details([product], message))
        self.assertIn("مناسبته المسجلة للبحر والصيف والجيم", line)
        self.assertIn("محتاجة تأكيد", line)
        self.assertEqual(product_claim_issues(line, product_claim_facts([product]), message=message, history=[]), [])

    def test_contained_negator_spelled_mafihash(self):
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Bmw": {"notes": ["Lemon, Bergamot", "Lavender", "Cedar"], "scent_tags": ["citrus", "fresh", "woody"]}}
        self.assertEqual([i for i in product_claim_issues("Bmw ريحته فريش ومفيهاش عود.", facts, message="فيه عود؟",
                                                          history=[]) if i.startswith("note:")], [])

    def test_price_fallback_respects_the_stated_budget(self):
        from products.services.router import _rephrased
        from products.services.product_formatting import format_products
        store = Store.objects.create(name="Budget Fallback")
        brand = Brand.objects.create(store=store, name="CH")
        product = Product.objects.create(store=store, brand=brand, name="NYC Man", gender="male",
                                         scent_tags=["fresh", "citrus", "musky"])
        for volume, price, bottle in ((50, "750.00", "normal"), (90, "1000.00", "normal"), (100, "1375.00", "original")):
            ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type=bottle, stock=5)
        context = format_products([product])
        bad = "NYC Man بـ500 جنيه."
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
             patch("products.services.router.repetition.repeated_frames", return_value=[]):
            reply, _, _ = _rephrased(bad, context, [], store, lambda hint: (bad, context),
                                     message="غالي عليا، مفيش حاجة أرخص؟", budget=700)
        self.assertIn("750", reply)
        self.assertNotIn("1375", reply)
        self.assertNotIn("1000", reply)


class SingledOutReferentTests(TestCase):
    """S10 replay 3: "سعره كام؟" after singling out K By Dolce was answered for both offered perfumes."""

    def test_singular_pointer_follows_the_perfume_the_customer_last_named(self):
        from products.models import Conversation
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        from products.services.product_info import _referent_from_conversation
        store = Store.objects.create(name="Referent Store")
        brand = Brand.objects.create(store=store, name="House")
        first = Product.objects.create(store=store, brand=brand, name="K By Dolce", gender="male",
                                       scent_tags=["fresh", "woody", "citrus"])
        second = Product.objects.create(store=store, brand=brand, name="212 NYC Man", gender="male",
                                        scent_tags=["fresh", "citrus", "musky"])
        for product in (first, second):
            ProductVariant.objects.create(product=product, volume=50, price=Decimal("750"), bottle_type="normal")
        conversation = Conversation.objects.create(store=store)
        save_message(conversation, "user", "عايز برفان رجالي مش مسكر")
        save_message(conversation, "assistant", "K By Dolce ريحته فريش. و212 NYC Man فريش ومسكي.",
                     internal_context=format_products([first, second]))
        save_message(conversation, "user", "طب K By Dolce مفيهوش عود؟")
        save_message(conversation, "assistant", "لا، مفيهوش عود.", internal_context=format_products([first]))
        save_message(conversation, "user", "طب سعره كام؟")
        self.assertEqual([p.name for p in _referent_from_conversation("طب سعره كام؟", store, conversation)],
                         ["K By Dolce"])
        self.assertEqual(len(_referent_from_conversation("بكام الاتنين؟", store, conversation)), 2)


class TargetedReplayTests(TestCase):
    """Defects found in the targeted replay after the second debug run (S05, S06)."""

    def setUp(self):
        from products.models import Conversation
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        self.store = Store.objects.create(name="Targeted Store")
        brand = Brand.objects.create(store=self.store, name="House")
        self.her = Product.objects.create(store=self.store, brand=brand, name="Her", gender="female",
                                          scent_tags=["fruity", "floral", "sweet"])
        self.jumana = Product.objects.create(store=self.store, brand=brand, name="Jumana", gender="female",
                                             scent_tags=["floral", "fruity", "sweet"])
        for product in (self.her, self.jumana):
            for volume, price in ((50, "750"), (90, "1000")):
                ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type="normal")
        self.conversation = Conversation.objects.create(store=self.store)
        save_message(self.conversation, "user", "عايزة حاجة شبه Cassilia فيها ورد")
        save_message(self.conversation, "assistant", "Her فيه فواكه وزهور، وJumana فيها زهور وفواكه.",
                     internal_context=format_products([self.her, self.jumana]))

    def test_deferring_checkout_is_acknowledged_instead_of_asking_again(self):
        """S05 targeted t6-t8: "هكمل الأوردر بعدين" got the same missing-detail question three times."""
        import json
        from products.services.order_service import handle_order
        data = {"products": [{"name": "Her", "volume": None, "quantity": 1, "bottle_type": "normal"}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            asked, _ = handle_order("هاخد Her", [], self.store, self.conversation)
            deferred, _ = handle_order("تمام، هاخده. هكمل الأوردر بعدين.", [], self.store, self.conversation)
        self.assertIn("تحب 50 ملي ولا 90 ملي؟", asked)
        self.assertIn("لما تحب تكمل الطلب", deferred)
        self.assertNotIn("تحب 50 ملي ولا 90 ملي؟", deferred)

    def test_choose_for_me_compares_the_perfumes_on_the_table(self):
        """S06 targeted t7: "خلاص اختارلي انت" was answered "تقصدي أنهي عطر بالظبط؟ اكتبيلي اسمه"."""
        from products.services import comparison_service
        sent = []
        with patch.object(comparison_service, "resolve_products", side_effect=AssertionError("resolver first")), \
             patch("products.services.product_info.get_product_info", side_effect=AssertionError("no product info")), \
             patch.object(comparison_service, "chat", side_effect=lambda messages, **kw: sent.append(messages) or "Her أنسب."):
            reply, context = comparison_service.compare_products("خلاص اختارلي انت", [], self.store, self.conversation)
        self.assertEqual(reply, "Her أنسب.")
        self.assertIn("Her", context)
        self.assertIn("Jumana", context)
        self.assertEqual(len(sent), 1)


class FinalRunOneFixesTests(TestCase):
    """Defects found in the first final verification run (final/attempt-1: S03, S04, S08)."""

    def setUp(self):
        self.store = Store.objects.create(name="Final One Store")
        self.brands = {}
        self.products = {}
        for name, house, tags in (("Sauvage", "Dior", ["fresh", "spicy", "woody"]),
                                  ("Bleu de Chanel", "Chanel", ["woody", "citrus", "fresh"]),
                                  ("Eros", "Versace", ["fresh", "sweet", "woody"])):
            brand = Brand.objects.create(store=self.store, name=house)
            product = Product.objects.create(store=self.store, brand=brand, name=name, gender="male", scent_tags=tags)
            for volume, price in ((50, "750"), (90, "1000")):
                ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type="normal")
            self.products[name] = product

    def _priced(self, message, prose):
        from products.services.price_reply import compose_price_reply
        from products.services.product_formatting import format_products, product_quotes, quote_context
        product = self.products["Sauvage"]
        context = quote_context(format_products([product], show_prices=False), product_quotes([product], message),
                                append=True, request=message)
        return compose_price_reply(prose, context)[0]

    def test_availability_asked_with_the_price_is_stated_by_code(self):
        """S04 final t1: "سوفاج 90 بكام؟ ومتوفر؟" got the quote and no answer about availability."""
        for prose in ("متوفر.", ""):
            with self.subTest(prose=prose):
                reply = self._priced("سوفاج 90 بكام؟ ومتوفر؟", prose)
                self.assertIn("متوفر.", reply)
                self.assertIn("90 ملي بـ1000 جنيه", reply)
                self.assertNotIn("750", reply)
        self.assertEqual(self._priced("سوفاج 90 بكام؟ ومتوفر؟", "متوفر يا فندم.").count("متوفر"), 1)
        self.assertNotIn("متوفر", self._priced("سوفاج 90 بكام؟", ""))

    def test_hypothetical_count_within_the_same_budget_is_read_literally(self):
        """S03 final t3: "لو عايز 3 بنفس الـ1600" was ignored and the two-bottle answer re-sent verbatim."""
        from products.services.sales.constraints import explicit_updates
        self.assertEqual(explicit_updates("الإجمالي كده كام بالظبط؟ طب لو عايز 3 بنفس الـ1600 ينفع؟")["purchase_quantity"], 3)
        self.assertEqual(explicit_updates("ينفع 3 في حدود 1600؟")["purchase_quantity"], 3)
        for message in ("لو عايز 3 ملي بنفس السعر", "لو عايز 50 بنفس السعر", "لو عايز 3 هتعملي خصم؟"):
            with self.subTest(message=message):
                self.assertNotIn("purchase_quantity", explicit_updates(message))

    def test_arabic_named_comparison_keeps_its_perfumes_on_the_table(self):
        """S08 final t2: after a comparison written in Arabic, "أنهي فيهم؟" found nothing under discussion."""
        from products.models import Conversation
        from products.services import comparison_service
        from products.services.conversation_service import save_message
        from products.services.sales.described import offered_in_order, under_discussion
        conversation = Conversation.objects.create(store=self.store)
        message = "إيه الفرق بين بلو دي شانيل وسوفاج وإيروس؟"
        save_message(conversation, "user", message)
        trio = [self.products[name] for name in ("Bleu de Chanel", "Sauvage", "Eros")]
        arabic = "بلو دي شانيل خشبي وحمضيات، وسوفاج فيه توابل، وإيروس فيه ريحة مسكرة."
        with patch("products.services.product_info._named_in_message", return_value=[]), \
             patch.object(comparison_service, "resolve_products", return_value=trio), \
             patch.object(comparison_service, "chat", return_value=arabic):
            reply, context = comparison_service.compare_products(message, [], self.store, conversation)
        self.assertEqual(reply, arabic)
        save_message(conversation, "assistant", reply, internal_context=context)
        self.assertEqual(set(under_discussion(conversation, self.store)), {"Bleu de Chanel", "Sauvage", "Eros"})
        self.assertEqual(offered_in_order(conversation, self.store), ["Bleu de Chanel", "Sauvage", "Eros"])

    def test_a_marker_never_adds_a_perfume_without_its_row(self):
        from products.services.sales.described import COMPARED_MARKER, supported_offered_names
        from products.services.product_formatting import format_products
        context = format_products([self.products["Sauvage"]]) + f"\n{COMPARED_MARKER} Sauvage | Eros"
        self.assertEqual(supported_offered_names("سوفاج أهدى", context, list(self.products)), ["Sauvage"])


class FinalRunTwoFixesTests(TestCase):
    """Defects found in the second final verification run (final/attempt-2: S02, S05)."""

    def test_a_bare_negation_rejects_the_bottle_it_names(self):
        """S02 final 2 t6: "Aventus الأصلي 100 ملي، مش زجاجة الستور" → "المطلوبين مش متاحين" (it was in stock)."""
        from products.services.bottle_wording import explicit_bottle_type
        from products.services.product_formatting import product_quotes
        self.assertIsNone(explicit_bottle_type("مش زجاجة الستور"))
        self.assertIsNone(explicit_bottle_type("مش عايز زجاجة الستور"))
        self.assertEqual(explicit_bottle_type("عايز زجاجة الستور"), "normal")
        self.assertEqual(explicit_bottle_type("مش زجاجة الستور، زجاجة اوريجنال"), "original")
        self.assertEqual(requested_quote_options("أنا بسأل على سعر Aventus الأصلي 100 ملي، مش زجاجة الستور"), ([100], None))
        store = Store.objects.create(name="Bottle Store")
        brand = Brand.objects.create(store=store, name="Creed")
        product = Product.objects.create(store=store, brand=brand, name="Aventus", gender="male")
        for volume, price, bottle in ((90, "2000", "normal"), (100, "2400", "original")):
            ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type=bottle, stock=5)
        quotes = product_quotes([product], "أنا بسأل على سعر Aventus الأصلي 100 ملي، مش زجاجة الستور")
        self.assertEqual([(q.volume, q.bottle_type, q.price) for q in quotes], [(100, "original", Decimal("2400"))])

    def test_naming_one_perfume_compares_it_with_the_other_on_the_table(self):
        """S05 final 2 t4: "إيه اللي يميز Alexandria II عن التاني؟" described Alexandria II alone."""
        from products.models import Conversation
        from products.services import comparison_service
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        store = Store.objects.create(name="Other One Store")
        brand = Brand.objects.create(store=store, name="House")
        oud = Product.objects.create(store=store, brand=brand, name="Stronger With You Oud", gender="male",
                                     scent_tags=["oud", "sweet", "woody"])
        alexandria = Product.objects.create(store=store, brand=brand, name="Alexandria II", gender="male",
                                            scent_tags=["woody", "oud", "heavy"])
        for product in (oud, alexandria):
            ProductVariant.objects.create(product=product, volume=50, price=Decimal("1400"), bottle_type="normal")
        conversation = Conversation.objects.create(store=store)
        save_message(conversation, "user", "خشبي أو عود للسهرات")
        save_message(conversation, "assistant", "Stronger With You Oud عودي ومسكر. وAlexandria II خشبي وعودي.",
                     internal_context=format_products([oud, alexandria]))
        message = "إيه اللي يميز Alexandria II عن التاني؟"
        with patch.object(comparison_service, "resolve_products", return_value=[alexandria]), \
             patch("products.services.product_info.get_product_info", side_effect=AssertionError("one perfume")), \
             patch.object(comparison_service, "chat", return_value="Alexandria II أتقل."):
            reply, context = comparison_service.compare_products(message, [], store, conversation)
        self.assertEqual(reply, "Alexandria II أتقل.")
        self.assertIn("Stronger With You Oud", context)


class SecondRoundBatchOneTests(TestCase):
    """Second round (2026-11-12): the owner's decisions and the first batch of fixes."""

    def _store(self):
        store = Store.objects.create(name="Second Round Store")
        brands = {name: Brand.objects.create(store=store, name=name) for name in ("Lancome", "Dior", "Chanel")}
        rows = {}
        for name, brand, gender in (("La Nuit", "Lancome", "female"), ("Sauvage", "Dior", "male"),
                                    ("Bleu de Chanel", "Chanel", "male"), ("Chance", "Chanel", "female")):
            rows[name] = Product.objects.create(store=store, brand=brands[brand], name=name, gender=gender)
            for volume, price in ((50, "750"), (90, "1000")):
                ProductVariant.objects.create(product=rows[name], volume=volume, price=Decimal(price), bottle_type="normal")
        return store, rows

    def test_an_empty_completion_is_retried_once(self):
        """S12 t5, S15 t7: one empty provider answer left the customer with no reply."""
        from types import SimpleNamespace
        from products.services.ai import client as ai_client

        def completion(text):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        create = ai_client.client.chat.completions
        with patch.object(create, "create", side_effect=[completion(""), completion("تمام")]) as call:
            self.assertEqual(ai_client.chat([{"role": "user", "content": "x"}]), "تمام")
        self.assertEqual(call.call_count, 2)
        with patch.object(create, "create", side_effect=[ai_client.EmptyCompletion("empty_output"), completion("أهلاً")]):
            self.assertEqual(ai_client.chat([{"role": "user", "content": "x"}]), "أهلاً")
        with patch.object(create, "create", side_effect=[completion(None), completion("")]) as call:
            self.assertEqual(ai_client.chat([{"role": "user", "content": "x"}]), "")
        self.assertEqual(call.call_count, 2)
        with patch.object(create, "create", return_value=completion("رد")) as call:
            ai_client.chat([{"role": "user", "content": "x"}])
        self.assertEqual(call.call_count, 1)

    def test_owner_approved_order_sentence_is_not_an_unsupported_promise(self):
        """Owner decision 1: the exact order-success sentence is approved; other promises still count."""
        from eval_harness import checks
        kwargs = dict(customer_text="تمام أكد", previous_reply="", turn_index=5, scenario_assert={},
                      needs_human_after=False, order_delta=1, tag_map={}, recommended_names=[],
                      active_budget_known=False, active_exclusions=set())
        approved = "طلبك اتأكد يا فندم، ورقمه #1.\nفريق المبيعات هيتواصل معاك قريب."
        self.assertNotIn("unsupported_promise", [f[0] for f in checks.check_behavior(approved, **kwargs)])
        self.assertIn("unsupported_promise", [f[0] for f in checks.check_behavior("فريقنا هيتواصل معاك بكرة.", **kwargs)])

    def test_an_unrecorded_trait_is_not_confirmed_absent(self):
        """Owner decision 4: "مش مسكر" is not inferred from a missing sweet label (S07 t5)."""
        from products.services.scent_description import description_issues
        facts = {"K By Dolce": {"scent_tags": ["fresh", "woody", "citrus"], "notes": ["Lemon", "Juniper", "Cedar"],
                                "occasion": ""}}
        message = "ده مش مسكر صح؟"
        self.assertTrue(description_issues("لا، K By Dolce مش مسكر.", facts, message=message))
        self.assertEqual(description_issues("K By Dolce طابعه فريش وخشبي، والحلاوة فيه مش مؤكدة عندي.", facts,
                                            message=message), [])
        light = {"Cassilia": {"scent_tags": ["floral", "fruity", "light"], "notes": [], "occasion": ""}}
        self.assertEqual(description_issues("لا، Cassilia مش تقيل.", light, message="ده تقيل؟"), [])
        self.assertEqual(description_issues("K By Dolce مفيهوش عود.", facts, message="فيه عود؟"), [])

    def test_the_store_price_range_from_business_facts_is_supported(self):
        """Owner decision 3 + S18: the range comes from the business facts, written as bare numbers."""
        from products.services.product_formatting import product_price_issues
        facts = ["- الاسعار عندنا بتبدأ من اول 750 لحد 2000 وفي حاجات اغلي شويه لو ازايز اوريجنال"]
        self.assertEqual(product_price_issues("الأسعار عندنا بتبدأ من 750 جنيه لحد 2000 جنيه.", [],
                                              monetary_sources=facts, catalogue=[]), [])
        self.assertTrue(product_price_issues("الأسعار بتبدأ من 300 جنيه.", [], monetary_sources=facts, catalogue=[]))
        self.assertTrue(product_price_issues("سعره 2000 جنيه.", [], monetary_sources=facts, catalogue=[]))

    def test_a_latin_name_that_says_more_than_a_stocked_row_is_not_that_row(self):
        """S09: "عندكو Club de Nuit Intense؟" was answered "La Nuit: متوفر."."""
        from products.services.product_info import _named_in_message
        from products.services.sales import naming
        store, rows = self._store()
        self.assertEqual(naming.latin_disagreement("عندكو Club de Nuit Intense؟", rows["La Nuit"]), "Club de Nuit Intense")
        self.assertEqual(_named_in_message("عندكو Club de Nuit Intense؟", store), [])
        for text, name in (("عندكو La Nuit؟", "La Nuit"), ("Dior Sauvage بكام؟", "Sauvage"),
                           ("Sauvage Elixir", "Sauvage"), ("3ayez a3raf si3r Sauvage", "Sauvage")):
            with self.subTest(text=text):
                self.assertEqual(naming.latin_disagreement(text, rows[name]), "")
        self.assertEqual([p.name for p in _named_in_message("عندكو La Nuit؟", store)], ["La Nuit"])
        # Production backtest (read-only): promotion words and a clipped house name are not new words.
        from types import SimpleNamespace
        essential = SimpleNamespace(name="Essential", brand=SimpleNamespace(name="Lacoste"))
        stronger = SimpleNamespace(name="Stronger With You", brand=SimpleNamespace(name="Emporio Armani"))
        le_male = SimpleNamespace(name="Le Male", brand=SimpleNamespace(name="Jean Paul Gaultier"))
        self.assertEqual(naming.latin_disagreement("Lacost essential", essential), "")
        self.assertEqual(naming.latin_disagreement("Bundle stronger with you", stronger), "")
        self.assertEqual(naming.latin_disagreement("Ultra Male", le_male), "Ultra Male")

    def test_the_resolver_reports_a_misplaced_latin_name_as_unplaced(self):
        import json
        from products.services import product_resolver
        store, _ = self._store()
        extraction = json.dumps({"perfumes": ["La Nuit"], "unplaced": []})
        with patch.object(product_resolver, "chat", return_value=extraction), \
             patch.object(product_resolver, "confirm_placement", return_value=None):
            result = product_resolver.resolve_products("عندكو Club de Nuit Intense؟", [], store)
        self.assertEqual(list(result), [])
        self.assertEqual(tuple(result.unplaced), ("Club de Nuit Intense",))
        with patch.object(product_resolver, "chat", return_value=extraction), \
             patch.object(product_resolver, "confirm_placement", side_effect=lambda span, shortlist, store=None: shortlist[0]):
            result = product_resolver.resolve_products("عندكو Club de Nuit Intense؟", [], store)
        self.assertEqual([p.name for p in result], ["La Nuit"])

    def _promotion_history(self):
        from products.services import promotion_service as promotions
        quote = "1- اشتري برفانين 90 ملي وخد التالت هدية"
        return quote, [{"role": "assistant", "content": "تفاصيل العرض:\n" + quote + "\n\n" + promotions.LIMITATION
                        + "\n\n" + promotions.INVITATION}]

    def test_an_open_transfer_offer_is_not_repeated_on_questions_about_the_offer(self):
        """S03, S18, S20: the same limitation and transfer question went out every turn."""
        from products.services import promotion_service as promotions
        from products.services.handoff import has_handoff_offer
        quote, history = self._promotion_history()
        with patch.object(promotions, "store_facts", return_value=quote), \
             patch.object(promotions, "chat", return_value=json.dumps({"quotes": [quote]})), \
             patch.object(promotions, "record_llm_message"):
            first, _ = promotions.reply("طب العرض ده بكام للـ3؟", history)
            later = history + [{"role": "user", "content": "طب العرض ده بكام للـ3؟"}, {"role": "assistant", "content": first}]
            second, _ = promotions.reply("طب إجماليه كام؟", later)
        self.assertEqual(first, promotions.NO_MORE_DETAILS)
        self.assertEqual(second, promotions.NO_MORE_DETAILS_AGAIN)
        for reply in (first, second):
            self.assertNotIn(promotions.INVITATION, reply)
            self.assertFalse(has_handoff_offer([{"role": "assistant", "content": reply}]))

    def test_a_checkout_attempt_after_an_open_offer_gets_the_offer_in_other_words(self):
        from products.services import promotion_service as promotions
        from products.services.handoff import has_handoff_offer
        _, history = self._promotion_history()
        reply, _ = promotions.reply("خلاص أكدلي الأوردر دلوقتي", history, explain=False)
        self.assertIn(promotions.LIMITATION, reply)
        self.assertIn(promotions.REPEAT_INVITATION, reply)
        self.assertNotIn(promotions.INVITATION, reply)
        self.assertTrue(has_handoff_offer([{"role": "assistant", "content": reply}]))
        again, _ = promotions.reply("أكد بقى", history + [{"role": "user", "content": "x"},
                                                          {"role": "assistant", "content": reply}], explain=False)
        self.assertTrue(again.endswith(promotions.INVITATION))
        self.assertTrue(has_handoff_offer([{"role": "assistant", "content": again}]))

    def test_a_price_range_question_after_a_declined_offer_is_not_the_offer_script(self):
        """S18 t5: "لا عادي، كنت بهزر… طب مفيش رينج للأسعار؟" got the offer script again."""
        from products.services import promotion_service as promotions
        self.assertTrue(promotions.information_requested("لا عادي، كنت بهزر 😂 طب مفيش رينج للأسعار حتى تقريبًا؟"))

    def test_a_house_line_beside_carted_perfumes_is_asked_about(self):
        """S19 t1: "2 سوفاج 90 ملي وواحد بلو دي شانيل 50 ملي" lost the Chanel line in silence."""
        from products.models import Conversation
        from products.services.order_service import handle_order
        store, _ = self._store()
        conversation = Conversation.objects.create(store=store)
        data = {"products": [{"name": "Sauvage", "volume": 90, "quantity": 2, "bottle_type": "normal"},
                             {"name": "سوفاج بلو شانيل", "volume": None, "quantity": 1}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)), \
             patch("products.services.order_service.resolve_product", return_value=None):
            reply, _ = handle_order("عايز أطلب 2 سوفاج 90 ملي وواحد بلو دي شانيل 50 ملي", [], store, conversation)
        self.assertNotIn("ناقصني للطلب", reply)
        self.assertTrue("Bleu de Chanel" in reply or "لسه محتاج أتأكد" in reply, reply)

    def test_a_pending_line_is_not_renamed_into_a_duplicate_of_another_line(self):
        """S19 t2-t4: the unanswered line became a phantom second Sauvage and the quantity flipped back."""
        from products.models import Cart, CartItem, Conversation
        from products.services.order_service import _merge_lines
        store, rows = self._store()
        conversation = Conversation.objects.create(store=store)
        cart = Cart.objects.create(conversation=conversation,
                                   pending_items=[{"name": "سوفاج بلو شانيل", "line_id": "B", "quantity": 1}])
        variant = rows["Sauvage"].variants.get(volume=90)
        CartItem.objects.create(cart=cart, variant=variant, quantity=1, line_id="A")
        lines = _merge_lines(cart, {"products": [{"name": "Sauvage", "line_id": "A", "quantity": 1, "volume": 90},
                                                 {"name": "Sauvage", "line_id": "B", "quantity": 1}]})
        names = {line["line_id"]: line["name"] for line in lines}
        self.assertEqual(names["B"], "سوفاج بلو شانيل")
        self.assertEqual(names["A"], "Sauvage")

    def test_the_rename_guard_holds_for_the_extractor_shapes_seen_in_s19(self):
        from products.models import Cart, CartItem, Conversation
        from products.services.order_service import _merge_lines
        store, rows = self._store()
        variant = rows["Sauvage"].variants.get(volume=90)
        shapes = (
            [{"name": "Sauvage", "line_id": "B", "quantity": 1}],
            [{"name": "Sauvage", "line_id": "{pk}", "quantity": 1, "volume": 90},
             {"name": "Sauvage", "line_id": "B", "quantity": 1}],
            [{"name": "Sauvage", "quantity": 1, "volume": 90}, {"name": "Sauvage", "line_id": "B"}],
        )
        for shape in shapes:
            with self.subTest(shape=shape):
                conversation = Conversation.objects.create(store=store)
                cart = Cart.objects.create(conversation=conversation,
                                           pending_items=[{"name": "سوفاج بلو شانيل", "line_id": "B", "quantity": 1}])
                item = CartItem.objects.create(cart=cart, variant=variant, quantity=2)
                products = [{**entry, "line_id": entry["line_id"].format(pk=item.pk)} if "line_id" in entry else dict(entry)
                            for entry in shape]
                lines = _merge_lines(cart, {"products": products})
                pending = [line for line in lines if line.get("line_id") == "B"]
                self.assertEqual([line["name"] for line in pending], ["سوفاج بلو شانيل"], lines)

    def test_a_total_question_classified_as_checkout_goes_to_the_totals_flow(self):
        """S03 final 2 t3: "لو عايز 3 بنفس الـ1600 ينفع؟" → "تحب تطلب أنهي عطر؟"."""
        from products.models import Cart, Conversation
        from products.services.router import _total_question_needs_recommendation
        from products.services.static_faq_service import normalize_arabic
        store, _ = self._store()
        conversation = Conversation.objects.create(store=store, preferences={"budget_scope": "total", "max_price": 1600})
        message = normalize_arabic("الإجمالي كده 1500 بالظبط؟ طب لو عايز 3 بنفس الـ1600 ينفع؟")
        empty_cart = Cart.objects.create(conversation=conversation)
        self.assertTrue(_total_question_needs_recommendation("order", message, conversation, empty_cart))
        self.assertTrue(_total_question_needs_recommendation("faq", message, conversation, None))
        self.assertFalse(_total_question_needs_recommendation("order", normalize_arabic("هاخدهم الاتنين، الإجمالي كام؟"),
                                                              conversation, None))
        empty_cart.pending_items = [{"name": "Sauvage", "line_id": "x", "quantity": 1}]
        self.assertFalse(_total_question_needs_recommendation("order", message, conversation, empty_cart))


class SecondRoundBatchTwoTests(TestCase):
    """Second round, batch 2: memory, claims, comparisons and the replay findings of batch 1."""

    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Batch Two Store")
        self.brand = Brand.objects.create(store=self.store, name="House")
        self.conversation = Conversation.objects.create(store=self.store)

    def product(self, name, tags, notes=("", "", ""), gender="male"):
        product = Product.objects.create(store=self.store, brand=self.brand, name=name, gender=gender, scent_tags=list(tags),
                                         top_notes=notes[0], middle_notes=notes[1], base_notes=notes[2])
        for volume, price in ((50, "750"), (90, "1000")):
            ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type="normal")
        return product

    def say(self, role, content, rows=()):
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        save_message(self.conversation, role, content, internal_context=format_products(list(rows)) if rows else "")

    def test_two_single_offers_in_a_row_keep_their_order_for_ordinals(self):
        """S06 final t7: "هو الأولاني كان إيه؟" after Her then Cassilia answered with Cassilia's row."""
        from products.services.sales.described import resolve_ordinal
        her, cassilia = self.product("Her", ["fruity", "floral", "sweet"]), self.product("Cassilia", ["floral", "fruity", "light"])
        self.say("user", "عايزة حاجة رقيقة فيها ورد")
        self.say("assistant", "Her فيه فواكه وزهور.", [her])
        self.say("user", "فيه اختيار تاني رقيق؟")
        self.say("assistant", "Cassilia فيها زهور وفواكه وخفيف.", [cassilia])
        self.say("user", "الاتنين حلوين")
        self.say("assistant", "لو محتارة، Cassilia أنسب لطلبك.", [cassilia])
        self.assertEqual(resolve_ordinal("هو الأولاني كان إيه تاني؟", self.conversation, self.store), "Her")
        self.assertEqual(resolve_ordinal("والتاني؟", self.conversation, self.store), "Cassilia")

    def test_an_excluded_material_is_excluded_as_an_ingredient(self):
        """S10: musk excluded, yet BMW / For Him / CK One came back with musk in their notes."""
        from products.services.search_service import avoided_notes, product_matches_request
        bmw = self.product("Bmw", ["citrus", "fresh", "woody"], ("Lemon", "Lavender", "White Musk, Cedar"))
        clean = self.product("Clean One", ["citrus", "fresh"], ("Lemon", "Neroli", "Cedar"))
        intent = {"gender": "male", "excluded_scent_tags": ["sweet", "musky"]}
        self.assertEqual(avoided_notes(intent), ["musk"])
        self.assertFalse(product_matches_request(bmw, intent, self.store))
        self.assertTrue(product_matches_request(clean, intent, self.store))

    def test_a_trait_said_about_a_named_perfume_is_not_a_wish(self):
        """S10 final 1 t6 and S14 final 2 t7: complaints and questions about a perfume were saved as wishes."""
        from products.services.sales.scent_preferences import explicit_scent_updates
        complaint = explicit_scent_updates("For Him فيه مسك! و212 VIB تقيل وتوابله كتير.. مش ده اللي طلبته")
        self.assertNotIn("heavy", complaint.get("scent_tags") or [])
        self.assertNotIn("musk", complaint.get("notes") or [])
        self.assertNotIn("leather", explicit_scent_updates("طب Pure XS فيه جلد؟").get("notes") or [])
        self.assertIn("fresh", explicit_scent_updates("hi, 3ayez perfume rigali, 7aga fresh").get("scent_tags") or [])
        self.assertIn("fresh", explicit_scent_updates("عايز حاجة فريش ومش تقيلة").get("scent_tags") or [])
        self.assertIn("heavy", explicit_scent_updates("عايز حاجة فريش ومش تقيلة").get("excluded_scent_tags") or [])
        # S14 final 2 t2/t5: "X ولا Y؟" about a named perfume excluded citrus and spicy through its "ولا".
        for question in ("طب K ريحته الخشبية باينة قوي ولا الحمضيات أكتر؟",
                         "طب Black XS ريحته مسكرة أكتر ولا التوابل باينة؟"):
            with self.subTest(question=question):
                self.assertEqual(explicit_scent_updates(question).get("excluded_scent_tags") or [], [])
        self.assertIn("fresh", explicit_scent_updates("هو فيه حاجة فريش؟").get("scent_tags") or [])
        self.assertIn("light", explicit_scent_updates("Sauvage حلو بس عايز حاجة خفيفة").get("scent_tags") or [])

    def test_a_budget_change_ending_a_sentence_is_saved(self):
        """S14 final 2 t5: "والميزانية خليها لحد 1500. طب…" kept filtering at the old 1000."""
        from products.services.sales.constraints import stated_preference_updates
        self.assertEqual(stated_preference_updates("حلو، والميزانية خليها لحد 1500. طب Black XS ريحته مسكرة أكتر؟")
                         .get("max_price"), 1500.0)
        self.assertNotIn("max_price", stated_preference_updates("الـ100 ملي بكام؟"))

    def test_character_words_no_label_supports_are_unsupported_claims(self):
        """S07 t3 "أدفى" and S14 t5 "دافية مش حادة" were invented character claims."""
        from products.services.scent_description import description_issues
        facts = {"Wanted by Night": {"scent_tags": ["spicy", "sweet", "heavy"], "notes": [], "occasion": ""}}
        message = "والتوابل بتبقى حادة ولا دافية؟"
        self.assertTrue(description_issues("Wanted by Night التوابل فيه دافية، مش حادة.", facts, message=message))
        self.assertEqual(description_issues("Wanted by Night فيه توابل، وهل هي دافية ولا حادة مش متسجل عندي.",
                                            facts, message=message), [])

    def test_naming_one_perfume_against_the_other_routes_to_comparison(self):
        """S05 t4: "إيه اللي يميز Alexandria II عن التاني؟" was answered with one perfume."""
        from products.services.router import _COMPARES_WITH_THE_OTHER
        from products.services.static_faq_service import normalize_arabic
        for message in ("إيه اللي يميز Alexandria II عن التاني؟", "Body أخف من التانية؟", "الفرق بينه وبين التاني إيه؟"):
            with self.subTest(message=message):
                self.assertTrue(_COMPARES_WITH_THE_OTHER.search(normalize_arabic(message)))
        self.assertFalse(_COMPARES_WITH_THE_OTHER.search(normalize_arabic("Alexandria II بكام؟")))

    def test_a_comparison_object_is_not_the_singled_out_perfume(self):
        """S05 t6: "إيه اللي يميزه عن Stronger With You Oud؟" made Stronger the referent of "منه"."""
        from products.services.product_info import _subject_names
        names = ["Alexandria II", "Stronger With You Oud"]
        self.assertEqual(_subject_names("إيه اللي يميزه عن Stronger With You Oud غير إنه تقيل؟", names), [])
        self.assertEqual(_subject_names("إيه اللي يميز Alexandria II عن التاني؟", names), ["Alexandria II"])

    def test_praise_beside_a_question_is_thanked_whatever_wrote_the_answer(self):
        from products.services.router import _thanked
        self.assertTrue(_thanked("تمام، ذوقك حلو. إيه اللي يميزه؟", "Alexandria II أتقل.", []).startswith("تسلم يا فندم."))
        self.assertEqual(_thanked("إيه اللي يميزه؟", "Alexandria II أتقل.", []), "Alexandria II أتقل.")
        self.assertEqual(_thanked("ذوقك حلو", "تسلم يا فندم، مبسوطين.", []), "تسلم يا فندم، مبسوطين.")

    def test_a_pick_fallback_names_the_difference_from_the_labels(self):
        """S13 final 1 t4: the fallback repeated both descriptions and chose nothing."""
        from products.services.router import _label_difference
        k_by = self.product("K By Dolce", ["fresh", "woody", "citrus"])
        nyc = self.product("212 NYC Man", ["fresh", "citrus", "musky"])
        self.assertEqual(_label_difference(k_by, nyc), "الفرق في الطابع المسجل: K By Dolce خشبي، و212 NYC Man مسكي.")

    def test_a_fallback_that_repeats_a_recent_reply_is_replaced(self):
        """S09 round 2 t6-t7: the same Ultra Male description went out three times."""
        from products.services.router import _not_a_repeat
        history = [{"role": "assistant", "content": "Ultra Male: ريحته مسكر وفيه ريحة فواكه وتوابل."}]
        same = "Ultra Male: ريحته مسكر وفيه ريحة فواكه وتوابل."
        self.assertEqual(_not_a_repeat(same, "خلاص هفكر شوية، تسلم", history), "تمام، خد وقتك، ولو احتجت حاجة أنا موجود.")
        self.assertNotEqual(_not_a_repeat(same, "طب هو قريب منه فعلاً؟", history), same)
        self.assertEqual(_not_a_repeat("Sauvage: فريش.", "طب Sauvage؟", history), "Sauvage: فريش.")

    def test_a_confirmed_absence_keeps_the_denial_in_the_fallback(self):
        """S09 round 2 t1: the fallback answered "1 Million: متوفر." with no word about the denial."""
        from products.services.router import _rephrased
        million = self.product("1 Million", ["spicy", "leather", "sweet"])
        from products.services.product_formatting import format_products
        context = "ABSENCE_DENIED\n═══ بدائل مقترحة متوفرة في المتجر ═══\n" + format_products([million])
        bad = "للأسف مش عندنا. 1 Million ريحته فيها توابل وجلد."
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
             patch("products.services.router.repetition.repeated_frames", return_value=[]):
            reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context),
                                     message="عندكو Club de Nuit Intense؟")
        self.assertTrue(reply.startswith("للأسف العطر ده مش موجود عندنا."), reply)
        self.assertIn("1 Million", reply)
        self.assertNotIn("متوفر.", reply.replace("متوفرة", ""))

    def test_misplaced_latin_names_are_withdrawn_even_when_two_rows_were_placed(self):
        """S09 round 2: "Club de Nuit Intense" came back as 1 Million and 1 Million Elixir."""
        from products.services import product_resolver
        self.product("1 Million", ["spicy"]), self.product("1 Million Elixir", ["sweet"])
        self.product("La Nuit", ["sweet"]), self.product("Jazz Club", ["woody"]), self.product("Dior Homme Intense", ["powdery"])
        extraction = json.dumps({"perfumes": ["1 Million", "1 Million Elixir"], "unplaced": []})
        with patch.object(product_resolver, "chat", return_value=extraction), \
             patch.object(product_resolver, "confirm_placement", return_value=None):
            result = product_resolver.resolve_products("عندكو Club de Nuit Intense؟", [], self.store)
        self.assertEqual(list(result), [])
        self.assertEqual(tuple(result.unplaced), ("Club de Nuit Intense",))
        both = json.dumps({"perfumes": ["Jazz Club", "La Nuit"], "unplaced": []})
        with patch.object(product_resolver, "chat", return_value=both), \
             patch.object(product_resolver, "confirm_placement", side_effect=AssertionError("both were named")):
            result = product_resolver.resolve_products("عندكو Jazz Club و La Nuit؟", [], self.store)
        self.assertEqual(sorted(p.name for p in result), ["Jazz Club", "La Nuit"])

    def test_the_store_price_range_is_answered_from_the_business_facts(self):
        """S18: "الأسعار بتبدأ من كام تقريبًا؟" got "مش متاح عندي سعر بداية مؤكد" four times."""
        from products.models import StoreSettings
        from products.services.router import _store_price_range
        StoreSettings.objects.create(store=self.store, business_facts="- الشحن 60\n- الاسعار عندنا بتبدأ من اول 750 لحد 2000")
        self.product("Sauvage", ["fresh"])
        self.assertEqual(_store_price_range("بحبها هادية، والأسعار بتبدأ من كام تقريبًا؟", self.store, self.conversation),
                         "الاسعار عندنا بتبدأ من اول 750 لحد 2000.")
        self.assertEqual(_store_price_range("Sauvage الأسعار بتاعته بكام؟", self.store, self.conversation), "")
        self.assertEqual(_store_price_range("الشحن بكام؟", self.store, self.conversation), "")

    def test_thanks_at_the_end_of_an_offer_joke_closes_without_a_transfer_question(self):
        """S18 round 2 t5: "…هتعملولي خصم؟ 😂 تمام شكراً" got "تحب أحولك لمندوب يساعدك؟"."""
        from products.services import promotion_service as promotions
        reply, _ = promotions.reply("طب لو اشتريت كل حاجة هتعملولي خصم؟ 😂 تمام شكراً", [], explain=False)
        self.assertIn(promotions.LIMITATION, reply)
        self.assertNotIn(promotions.INVITATION, reply)
        self.assertTrue(reply.endswith("ولو احتجت حاجة أنا موجود."))

    def test_a_pending_line_is_not_resolved_into_a_perfume_another_line_holds(self):
        """S19 round 2 t2: the merged "سوفاج بلو شانيل" line was resolved into a second Sauvage."""
        from products.models import Cart, CartItem
        from products.services.order_service import handle_order
        sauvage = self.product("Sauvage", ["fresh"])
        cart = Cart.objects.create(conversation=self.conversation,
                                   pending_items=[{"name": "سوفاج بلو شانيل", "line_id": "B", "quantity": 1}])
        item = CartItem.objects.create(cart=cart, variant=sauvage.variants.get(volume=90), quantity=1, line_id="A")
        data = {"products": [{"name": "Sauvage", "line_id": "A", "quantity": 1, "volume": 90, "bottle_type": "normal"},
                             {"name": "سوفاج بلو شانيل", "line_id": "B", "quantity": 1}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)), \
             patch("products.services.order_service.resolve_product", return_value=sauvage):
            reply, _ = handle_order("لا خلي السوفاج واحد بس", [], self.store, self.conversation)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)
        self.assertIn("لسه محتاج أتأكد من", reply)

    def test_a_gendered_recipient_and_a_combined_budget_carry_into_the_new_profile(self):
        """S03 round 2 t4: switching to "أخويا" asked "رجالي ولا حريمي؟" again."""
        from products.services.conversation_service import _switch_recipient
        self.conversation.preferences = {"gender": "male", "max_price": 1600, "budget_scope": "total", "purchase_quantity": 2}
        state = {}
        restored = _switch_recipient(self.conversation, "بس أخويا عايز حاجة سويت مش فريش", state)
        self.assertEqual(restored["gender"], "male")
        self.assertEqual(restored["max_price"], 1600)
        self.assertEqual(state["recipient"], "brother")
        mother = _switch_recipient(self.conversation, "وكمان عايز حاجة لماما", {})
        self.assertEqual(mother["gender"], "female")

    def test_a_note_placed_in_the_wrong_layer_is_unsupported(self):
        """S02 round 2 t4/t7: agreed pepper was a top note (stored in the base); a labelled dry down."""
        from products.services.reply_sanitizer import product_claim_issues
        layton = {"Layton": {"scent_tags": ["fruity", "woody", "sweet"],
                             "notes": ["Apple, Bergamot, Lavender", "Jasmine, Violet, Geranium",
                                       "Vanilla, Pepper, Guaiac Wood, Patchouli"]}}
        message = "استنى، فلفل في الـbase؟ اللي أعرفه إنه في الـtop"

        def layer_issues(reply, facts, question):
            return [i for i in product_claim_issues(reply, facts, message=question) if i.startswith("note-layer:")]
        self.assertTrue(layer_issues("أيوه، معاك حق إن الفلفل موجود في الـTop Notes.", layton, message))
        self.assertEqual(layer_issues("حسب المسجل عندنا، الـBase Notes فيها فانيليا وفلفل وباتشولي.", layton, message), [])
        aventus = {"Aventus": {"scent_tags": ["woody", "fruity", "smoky"],
                               "notes": ["Pineapple, Bergamot", "Birch, Patchouli", "Musk, Oakmoss, Ambergris, Vanilla"]}}
        question = "الـdry down بيبقى smoky ولا الخشب؟"
        self.assertTrue(layer_issues("الـdry down طابعه خشبي وفيه ريحة فواكه ودخان.", aventus, question))
        self.assertEqual(layer_issues("الـTop Notes: أناناس، برغموت. الـMiddle Notes: بتولا، باتشولي. "
                                      "والـBase Notes: مسك، طحلب البلوط وفانيليا.", aventus, question), [])

    def test_a_budget_fit_question_is_a_price_question(self):
        """S07 round 2 t6-t7: "هو ينفع في حدود الألف؟" got no price, then "سعره مش متاح"."""
        from products.services.product_formatting import price_question
        for message in ("هو ينفع في حدود الألف؟", "يدخل في الميزانية؟", "ينفع بـ1000؟"):
            with self.subTest(message=message):
                self.assertTrue(price_question(message, [], None))
        self.assertFalse(price_question("ريحته عاملة إزاي؟", [], None))


class SecondRoundBatchThreeTests(TestCase):
    """Second round, batch 3: the failures of round-2 full run 1 and the fixes written during it."""

    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Batch Three Store")
        self.brand = Brand.objects.create(store=self.store, name="House")
        self.conversation = Conversation.objects.create(store=self.store)

    def product(self, name, tags, notes=("", "", ""), gender="male", brand=None):
        product = Product.objects.create(store=self.store, brand=brand or self.brand, name=name, gender=gender,
                                         scent_tags=list(tags), top_notes=notes[0], middle_notes=notes[1],
                                         base_notes=notes[2])
        for volume, price in ((50, "750"), (90, "1000")):
            ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type="normal")
        return product

    def say(self, role, content, rows=()):
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        save_message(self.conversation, role, content, internal_context=format_products(list(rows)) if rows else "")

    def test_an_adjacent_trait_shares_the_negation(self):
        """S10 round 2 t3: "ومش خشبية تقيلة" saved heavy as a wish next to an excluded woody."""
        from products.services.sales.scent_preferences import explicit_scent_updates
        updates = explicit_scent_updates("طيب مفيش حاجة فريش وحمضيات أكتر ومش خشبية تقيلة؟")
        self.assertNotIn("heavy", updates.get("scent_tags") or [])
        self.assertEqual(sorted(updates.get("excluded_scent_tags") or []), ["heavy", "woody"])
        self.assertIn("fresh", updates.get("scent_tags") or [])
        # An adjective after a WANTED trait is still wanted.
        self.assertIn("light", explicit_scent_updates("عايز حاجة فريش خفيفة").get("scent_tags") or [])

    def test_a_question_about_the_described_perfume_is_not_a_wish(self):
        """S14 round 2 t3: "يعني ريحته حمضيات وخشب أكتر؟" saved citrus beside the new sweet wish."""
        from products.services.sales.scent_preferences import explicit_scent_updates
        updates = explicit_scent_updates("يعني ريحته حمضيات وخشب أكتر؟ لا استنى، بدل فريش خليها حاجة سويت للسهرات")
        self.assertEqual(updates.get("scent_tags"), ["sweet"])
        for question in ("طب الميزانية خليها لحد 1500، وScandal ريحته مسكرة قوي ولا سويت خفيف؟",
                         "طب Black XS ده سويت للسهرات ولا فريش أكتر؟"):
            with self.subTest(question=question):
                self.assertEqual(explicit_scent_updates(question).get("excluded_scent_tags") or [], [])

    def test_a_house_that_ends_a_product_name_is_not_a_bare_house(self):
        """S19 round 2 t1: "وواحد بلو دي شانيل 50 ملي" became "which Chanel?" and lost its 50 ml."""
        from products.services.sales import naming
        chanel = Brand.objects.create(store=self.store, name="Chanel")
        self.product("Bleu de Chanel", ["woody"], brand=chanel)
        self.product("Sauvage", ["fresh"])
        self.assertIsNone(naming.house_mentioned_alone("عايز أطلب 2 سوفاج 90 ملي وواحد بلو دي شانيل 50 ملي", self.store))
        self.assertIsNone(naming.house_mentioned_alone("عايز كوكو شانيل", self.store))
        for message in ("عايز سوفاج 90 ملي وواحد شانيل 50 ملي", "عايز شانيل 50", "هاخد ده شانيل"):
            with self.subTest(message=message):
                self.assertEqual(naming.house_mentioned_alone(message, self.store), chanel)

    def test_both_lines_of_a_two_perfume_order_reach_the_cart(self):
        """S19 round 2 t1, end to end: the Bleu de Chanel 50 ml line is carted beside the Sauvage."""
        from products.models import Cart
        from products.services.order_service import handle_order
        chanel = Brand.objects.create(store=self.store, name="Chanel")
        bleu = self.product("Bleu de Chanel", ["woody"], brand=chanel)
        sauvage = self.product("Sauvage", ["fresh"])
        data = {"products": [{"name": "Sauvage", "volume": 90, "quantity": 2, "bottle_type": None},
                             {"name": "بلو دي شانيل", "volume": 50, "quantity": 1, "bottle_type": None}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)), \
             patch("products.services.order_service.resolve_product", side_effect=lambda message, store: bleu):
            reply, _ = handle_order("عايز أطلب 2 سوفاج 90 ملي وواحد بلو دي شانيل 50 ملي", [], self.store,
                                    self.conversation)
        cart = Cart.objects.get(conversation=self.conversation)
        lines = sorted((item.variant.product.name, item.variant.volume, item.quantity) for item in cart.items.all())
        self.assertEqual(lines, [("Bleu de Chanel", 50, 1), ("Sauvage", 90, 2)], reply)
        self.assertNotIn("تحب أنهي واحد", reply)
        self.assertEqual(sauvage.pk, cart.items.get(variant__product__name="Sauvage").variant.product_id)

    def test_asking_for_the_regular_bottle_when_it_is_already_the_store_bottle_is_explained(self):
        """S19 round 2 batch 2 t6-t9: "عايز زجاجة الستور العادية مش الخاصة" got the same summary three times."""
        from products.services.bottle_wording import NORMAL_BOTTLE
        from products.services.order_service import _bottle_note
        items = [{"bottle_type": "normal"}]
        for message in ("لا، عايز زجاجة الستور العادية مش الخاصة", "لسه مكتوب زجاجة الستور الخاصة، عايز العادية",
                        "مش هأكد طالما لسه مكتوب الخاصة، عدلها للعادية الأول"):
            with self.subTest(message=message):
                self.assertTrue(_bottle_note(message, items).startswith(NORMAL_BOTTLE + " هي الزجاجة العادية"))
        self.assertEqual(_bottle_note("تمام أكد", items), "")
        self.assertEqual(_bottle_note("عايز زجاجة الستور العادية", [{"bottle_type": "original"}]), "")
        self.assertEqual(_bottle_note("تمام، " + NORMAL_BOTTLE + " حلوة", items), "")

    def test_asking_for_cheaper_when_nothing_is_cheaper_gets_a_direct_answer(self):
        """S11 round 2 batch 2 t5: "يعني مفيش أي حاجة أرخص من 750؟" resent the same two-perfume block."""
        from products.services.router import _nothing_cheaper
        from products.services.static_faq_service import normalize_arabic
        self.conversation.preferences = {"gender": "male"}
        self.conversation.save(update_fields=["preferences"])
        nyc, eternity = self.product("212 NYC Man", ["fresh"]), self.product("Eternity", ["fresh", "woody"])
        block = ("212 NYC Man\nريحته فريش.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.\n\n"
                 "Eternity\nريحته فريش وخشبي.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.")
        history = [{"role": "user", "content": "طب مفيش حاجة أرخص شوية؟"}, {"role": "assistant", "content": block}]
        question = normalize_arabic("يعني مفيش أي حاجة أرخص من 750؟")
        self.assertEqual(_nothing_cheaper(block, question, history, [nyc, eternity], self.store, self.conversation),
                         "212 NYC Man وEternity بـ750 جنيه للـ50 ملي، ودي أقل أسعار عندنا؛ مفيش حاجة أرخص منها حاليًا.")
        self.assertEqual(_nothing_cheaper(block, question, [], [nyc, eternity], self.store, self.conversation), block)
        cheap = self.product("Budget Fresh", ["fresh"])
        cheap.variants.filter(volume=50).update(price=Decimal("500"))
        self.assertEqual(_nothing_cheaper(block, question, history, [nyc, eternity], self.store, self.conversation), block)

    def test_the_summary_states_the_total_with_a_flat_shipping_fee(self):
        """S11 round 2 batch 2 t7: "750 (من غير الشحن)" and "60" with no 810 read as a summary without its total."""
        from products.models import Cart, StaticFAQ, StoreSettings
        from products.services.order_service import _quote_summary
        StaticFAQ.objects.create(store=self.store, question="بكام مصاريف الشحن؟", answer="مصاريف الشحن 60 جنيه لجميع محافظات مصر",
                                 keywords="شحن+بكام, شحن+مصاريف, شحن", priority=3)
        StoreSettings.objects.create(store=self.store, promotions="2- الشحن مجاني لاي اوردر فوق ال ١٥٠٠")
        variant = self.product("212 NYC Man", ["fresh"]).variants.get(volume=50)
        cart = Cart.objects.create(conversation=self.conversation, customer_name="كريم", customer_phone="01011223344",
                                   shipping_address="الدقي، الجيزة")
        one = [{"variant": variant, "quantity": 1, "price": Decimal("750"), "bottle_type": "normal"}]
        self.assertIn("الإجمالي مع الشحن: 810 جنيه.", _quote_summary(self.conversation, cart, one))
        three = [{"variant": variant, "quantity": 3, "price": Decimal("750"), "bottle_type": "normal"}]
        self.assertNotIn("الإجمالي مع الشحن", _quote_summary(self.conversation, cart, three))

    def _faqs(self):
        from products.models import StaticFAQ
        answers = {"delivery": "التوصيل بيوصل خلال 2 لـ 4 أيام عمل.", "payment": "دفع كاش عند الاستلام أو انستاباي.",
                   "fee": "مصاريف الشحن 60 جنيه."}
        StaticFAQ.objects.create(store=self.store, question="كم مدة التوصيل؟", priority=10, answer=answers["delivery"],
                                 keywords="توصيل+مدة, شحن+مدة, شحن+ايام, يوصل+امتى")
        StaticFAQ.objects.create(store=self.store, question="بكام مصاريف الشحن؟", priority=3, answer=answers["fee"],
                                 keywords="شحن+بكام, شحن+مصاريف,شحن")
        StaticFAQ.objects.create(store=self.store, question="طرق الدفع", priority=2, answer=answers["payment"],
                                 keywords="طرق+الدفع, دفع+استلام")
        return answers

    def test_a_delivery_time_question_gets_the_delivery_time_answer(self):
        """S14 round 2 t5: "الشحن بياخد قد إيه؟" matched no delivery-time keyword."""
        from products.services.router import _faq_lookup
        answers = self._faqs()
        self.assertEqual(_faq_lookup("طب الشحن بياخد قد إيه؟", self.store)["answer"], answers["delivery"])
        self.assertEqual(_faq_lookup("الشحن بكام؟", self.store)["answer"], answers["fee"])

    def test_every_store_question_in_one_message_is_answered_in_order(self):
        """S14 round 2 t5: shipping time and cash on delivery asked together; only payment came back."""
        from products.services.router import _faq_answers
        answers = self._faqs()
        message = "طب الشحن بياخد قد إيه؟ والدفع عند الاستلام متاح؟"
        reply = _faq_answers(message, self.store, answers["payment"])
        self.assertEqual(reply, answers["delivery"] + "\n\n" + answers["payment"])
        self.assertEqual(_faq_answers("الدفع عند الاستلام متاح؟", self.store, answers["payment"]), answers["payment"])
        self.assertEqual(_faq_answers("السلام عليكم؟ الشحن بكام؟", self.store, answers["fee"]), answers["fee"])

    def test_an_exchange_question_in_other_words_gets_the_store_policy(self):
        """S07 round 2 t3-t5: "لو مجاش على ذوقها أعمل إيه؟" / "ينفع أبدلها؟" missed the exchange FAQ."""
        from products.models import StaticFAQ
        from products.services.router import _faq_lookup
        policy = "ضمان الاستبدال لمدة يومين من وقت الاستلام.\nفي حالة الاستبدال: يتم دفع شحن مرتين + 150ج للبوكس الجديد."
        StaticFAQ.objects.create(store=self.store, question="في استرجاع أو استبدال؟", priority=10, answer=policy,
                                 keywords="استرجاع, استبدال, ترجيع")
        for message in ("طب ولو مجاش على ذوقها أعمل إيه؟", "قصدي لو جبتلها عطر ومطلعش على ذوقها ينفع أبدله؟",
                        "ممكن ارجعه لو معجبنيش؟"):
            with self.subTest(message=message):
                self.assertEqual((_faq_lookup(message, self.store) or {}).get("answer"), policy)
        for message in ("عايز ابدل السوفاج بالبلو", "ينفع أغير العنوان؟",
                        "طب أنهي واحدة فيهم أخف؟ ولو مجتش على ذوقها أعمل إيه؟"):
            with self.subTest(message=message):
                self.assertIsNone(_faq_lookup(message, self.store))

    def test_the_exchange_fee_is_a_store_amount_under_its_own_subject(self):
        """S07 round 2: "بتدفع 150 جنيه للبوكس الجديد" was an unattributed price; the store wrote "150ج"."""
        from products.services.product_formatting import product_price_issues
        policy = ["ضمان الاستبدال لمدة يومين.\n في حالة الاستبدال: يتم دفع شحن مرتين + 150ج للبوكس الجديد، أو مجانًا داخل الستور."]
        self.assertEqual(product_price_issues("الاستبدال خلال يومين، وبتدفع 150 جنيه للبوكس الجديد مع الشحن مرتين.",
                                              [], monetary_sources=policy, catalogue=[]), [])
        self.assertTrue(product_price_issues("الاستبدال هيكلفك 270 جنيه.", [], monetary_sources=policy, catalogue=[]))

    def test_a_policy_answer_with_one_bad_figure_keeps_the_rest(self):
        """S07 round 2 t3-t4: a policy question fell back to "تقصد أنهي عطر وحجم؟… أقولك السعر"."""
        from products.services.router import _rephrased
        draft = "تقدر تستبدل العطر خلال يومين من الاستلام. هتدفع الشحن مرتين يعني 120 جنيه."
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
             patch("products.services.router.repetition.repeated_frames", return_value=[]):
            reply, _, _ = _rephrased(draft, "", [], self.store, lambda hint: (draft, ""),
                                     message="طب ولو مجاش على ذوقها أعمل إيه؟")
        self.assertEqual(reply, "تقدر تستبدل العطر خلال يومين من الاستلام.")
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
             patch("products.services.router.repetition.repeated_frames", return_value=[]):
            asked, _, _ = _rephrased("ده بـ999 جنيه.", "", [], self.store, lambda hint: ("ده بـ999 جنيه.", ""),
                                     message="ده بكام؟")
        self.assertIn("تقصد أنهي عطر", asked)

    def test_a_budget_said_beside_a_store_question_is_saved_and_acknowledged(self):
        """S14 round 2 batch 2 t6: "والميزانية خليها لحد 1500. والتوصيل بياخد قد إيه؟" lost the budget."""
        from products.services.router import route
        answers = self._faqs()
        self.conversation.preferences = {"gender": "male", "max_price": 1000}
        self.conversation.save(update_fields=["preferences"])
        with patch("products.services.router.classify", return_value="faq"):
            reply, _ = route("تمام، طب والميزانية خليها لحد 1500. والتوصيل بياخد قد إيه؟", [], self.store,
                             self.conversation)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.preferences.get("max_price"), 1500)
        self.assertTrue(reply.startswith("تمام، الميزانية بقت لحد 1500 جنيه."), reply)
        self.assertIn(answers["delivery"], reply)

    def test_a_question_about_a_named_perfume_after_an_article_is_not_a_wish(self):
        """S14 round 2 batch 2 t5: "طب والـ Black XS حلاوته واضحة ولا التوابل…" excluded spicy."""
        from products.services.sales.scent_preferences import explicit_scent_updates
        self.assertEqual(explicit_scent_updates("طب والـ Black XS حلاوته واضحة ولا التوابل والخشب غالبين؟"), {})
        self.assertEqual(explicit_scent_updates("طب رشحلي تاني حاجة كده، وريحة Pure XS الجلدية دي عاملة إزاي؟"), {})
        self.assertIn("spicy", explicit_scent_updates("ومش عايز توابل").get("excluded_scent_tags") or [])

    def test_the_recipients_taste_said_with_she_likes_is_a_preference(self):
        """S07 round 2 batch 2 t2: "هي بتحب الحاجات الخفيفة ومش المسكرة أوي…" was dropped as a described perfume."""
        from products.services.router import _actionable_request
        from products.services.sales.scent_preferences import explicit_scent_updates
        worried = "هي بتحب الحاجات الخفيفة ومش المسكرة أوي، بس أنا قلقان أختار حاجة متعجبهاش.. إيه رأيك؟"
        updates = explicit_scent_updates(worried)
        self.assertEqual(updates.get("scent_tags"), ["light"])
        self.assertEqual(updates.get("excluded_scent_tags"), ["sweet"])
        self.assertEqual(explicit_scent_updates("هي هادية وكلاسيك، ومش بتحب الحاجات المسكرة أوي.. أختارلها إيه؟"),
                         {"excluded_scent_tags": ["sweet"]})
        self.assertTrue(_actionable_request(updates, worried))

    def test_a_new_pick_and_a_question_about_one_on_the_table_get_both_answers(self):
        """S14 round 2 batch 2 t7: "رشحلي تاني حاجة كده، وريحة Pure XS … عاملة إزاي؟" got only the second half."""
        from products.services.router import route
        pure = self.product("Pure XS", ["spicy", "sweet", "leather"])
        black = self.product("Black XS", ["spicy", "sweet", "woody"])
        fresh_pick = self.product("Night Pick", ["sweet", "woody"])
        self.conversation.preferences = {"gender": "male", "scent_tags": ["sweet"], "scent_match": "all",
                                         "scent_open": False}
        self.conversation.save(update_fields=["preferences"])
        self.say("user", "رشحلي حاجة سويت")
        self.say("assistant", "Black XS فيه توابل ومسكر. وPure XS فيه توابل ومسكر وجلد.", [black, pure])
        captured = {}

        def recommend(message, products, history, **kwargs):
            captured["names"] = [p.name for p in products]
            captured["search"] = {k: v for k, v in (kwargs.get("search") or {}).items() if k != "products"}
            if not products:
                raise AssertionError(f"no candidates: {captured['search']}")
            return products[0].name + " ريحته مسكر وخشبي.", "CTX"
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.router.extract_intent", return_value={}), \
             patch("products.services.router.recommend", side_effect=recommend):
            reply, _ = route("طب رشحلي تاني حاجة كده، وريحة Pure XS الجلدية دي عاملة إزاي؟", [], self.store,
                             self.conversation)
        self.assertNotIn("Pure XS", captured["names"])
        self.assertNotIn("Black XS", captured["names"])
        self.assertIn(fresh_pick.name, reply)
        self.assertIn("Pure XS:", reply)

    def test_a_policy_question_beside_a_product_question_gets_the_store_answer(self):
        """S07 round 2 batch 3 t3, S14 t4-t5: the exchange/delivery half went to the model or unanswered."""
        from products.models import StaticFAQ
        from products.services.router import _policy_side_questions
        answers = self._faqs()
        policy = "ضمان الاستبدال لمدة يومين من وقت الاستلام."
        StaticFAQ.objects.create(store=self.store, question="في استرجاع أو استبدال؟", priority=10, answer=policy,
                                 keywords="استرجاع, استبدال")
        self.assertEqual(_policy_side_questions("طب هتعجبها أكيد؟ ولو مجتش على ذوقها أعمل إيه؟", self.store),
                         ([policy], "طب هتعجبها أكيد؟", True))
        self.assertEqual(_policy_side_questions("تمام، والشحن بياخد قد إيه؟ طب رشحلي كمان حاجة سويت للسهرات", self.store),
                         ([answers["delivery"]], "تمام طب رشحلي كمان حاجة سويت للسهرات", True))
        # Round 2 full 5: a lone policy question is answered by the store too, whichever branch gets it.
        self.assertEqual(_policy_side_questions("ولو مجتش على ذوقها أعمل إيه؟", self.store), ([policy], "", False))
        self.assertEqual(_policy_side_questions("Sauvage بكام؟ وريحته عاملة إزاي؟", self.store)[0], [])

    def test_a_bottle_count_under_a_combined_budget_is_not_an_offer_question(self):
        """S03 round 2 batch 3 t3: "طب لو عايز 3 بنفس الـ1600 ينفع؟" was classified as a promotion."""
        from products.services.router import _count_within_total
        self.conversation.preferences = {"budget_scope": "total", "max_price": 1600}
        self.conversation.save(update_fields=["preferences"])
        self.assertTrue(_count_within_total("الإجمالي كده 1500 بالظبط؟ طب لو عايز 3 بنفس الـ1600 ينفع؟", self.conversation))
        self.conversation.preferences = {"max_price": 1600}
        self.assertFalse(_count_within_total("طب لو عايز 3 بنفس الـ1600 ينفع؟", self.conversation))

    def test_a_recorded_label_is_never_called_unconfirmed(self):
        """S14 round 2 batch 3 t6: "الجلدية مش مؤكدة… والتوابل مش مؤكدة" about Pure XS (leather, spicy)."""
        from products.services.scent_description import asked_attribute_answers, description_issues
        pure = self.product("Pure XS", ["spicy", "sweet", "leather"], ("Mint", "Cinnamon, Leather", "Vanilla"))
        facts = {"Pure XS": {"scent_tags": ["spicy", "sweet", "leather"], "notes": ["Mint", "Cinnamon, Leather", "Vanilla"],
                             "occasion": ""}}
        question = "طب Pure XS ريحته الجلدية باينة قوي ولا التوابل أكتر؟"
        self.assertTrue(description_issues("Pure XS: الجلدية مش مؤكدة في طابعه، والتوابل مش مؤكدة.", facts, message=question))
        self.assertEqual(description_issues("Pure XS طابعه جلدي وفيه توابل، بس مقدرش أحدد أنهي أوضح.", facts,
                                            message=question), [])
        self.assertEqual(asked_attribute_answers(question, pure)[:2], ["طابعه المسجل ريحته جلدية", "طابعه المسجل فيه ريحة توابل"])

    def test_thinking_about_one_perfume_while_asking_for_another_pick_is_not_a_close(self):
        """S14 round 2 batch 3 t9: "رشحلي حاجة سويت… وأنا هفكر في Wanted by Night كمان" got "خد وقتك"."""
        from products.services.router import route
        self.conversation.preferences = {"gender": "male", "scent_tags": ["sweet"], "scent_match": "all", "scent_open": False}
        self.conversation.save(update_fields=["preferences"])
        self.product("Night Sweet", ["sweet", "woody"])
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={}), \
             patch("products.services.router.recommend", return_value=("Night Sweet ريحته مسكر وخشبي.", "CTX")), \
             patch("products.services.objection_service.chat", side_effect=AssertionError("closed")):
            reply, _ = route("تمام، طب رشحلي حاجة سويت للسهرات بس مش تقيلة أوي، وأنا هفكر في Wanted by Night كمان",
                             [], self.store, self.conversation)
        self.assertIn("Night Sweet", reply)

    def _recommend_names(self):
        def recommend(message, products, history, **kwargs):
            return " و".join(p.name for p in list(products)[:2]) + " ريحتهم فريش.", "CTX"
        return recommend

    def test_the_same_request_twice_gets_the_next_perfumes_not_the_same_block(self):
        """S10 round 2 batch 3 t6-t7: the pair from t4 came back word for word; the complaint went unanswered."""
        from products.services.router import route
        self.conversation.preferences = {"gender": "male", "scent_tags": ["fresh"], "scent_match": "all",
                                         "scent_open": False}
        self.conversation.save(update_fields=["preferences"])
        for name in ("Alpha Fresh", "Beta Fresh", "Gamma Fresh", "Delta Fresh"):
            self.product(name, ["fresh"])
        earlier = "Alpha Fresh وBeta Fresh ريحتهم فريش."
        history = [{"role": "user", "content": "رشحلي حاجة فريش"}, {"role": "assistant", "content": earlier}]
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={}), \
             patch("products.services.router.recommend", side_effect=self._recommend_names()):
            again, _ = route("طب رشحلي حاجة تكون متأكد إنها فريش", history, self.store, self.conversation)
            complaint, _ = route("إنت كده بتكرر نفس الترشيحات! رشحلي حاجة فريش", history, self.store, self.conversation)
        self.assertNotIn("Alpha Fresh", again)
        self.assertIn("Gamma Fresh", again)
        self.assertTrue(complaint.startswith("حقك عليا."), complaint)

    def test_first_in_a_goodbye_is_not_the_first_perfume(self):
        """S10 round 2 batch 3 t10: "هاجي أشمه في الفرع الأول وأقرر بعدين. شكراً" got a product description."""
        from products.services.router import route
        ck = self.product("CK One 1", ["fresh", "citrus"])
        self.say("user", "رشحلي حاجة فريش")
        self.say("assistant", "CK One 1 ريحته فريش.", [ck])
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router.handle_general", side_effect=AssertionError("described")):
            reply, _ = route("تمام، هاجي أشمه في الفرع الأول وأقرر بعدين. شكراً", [], self.store, self.conversation)
        self.assertEqual(reply, "العفو يا فندم، ولو احتجت أي حاجة أنا موجود.")

    def test_a_budget_fit_question_beside_hesitation_gets_the_price(self):
        """S07 round 2 batch 3 t4: "هي في حدود ١٠٠٠؟ وهفكر أطلبه" got "سعرها محتاج تأكيد"."""
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        cassilia = self.product("Cassilia", ["floral", "light"], gender="female")
        self.say("user", "عايزة حاجة خفيفة")
        self.say("assistant", "Cassilia فيها زهور وخفيفة.", [cassilia])
        message = "تمام كده اطمنت شوية، هي في حدود ١٠٠٠؟ وهفكر أطلبه"
        with patch("products.services.objection_service.resolve_products", return_value=[cassilia]), \
             patch("products.services.objection_service.chat", return_value="أيوه، في حدود ميزانيتك."):
            reply, _ = handle_objection(message, detect(message, []), [], self.store, self.conversation)
        self.assertIn("750", reply)
        with patch("products.services.objection_service.resolve_products", return_value=[cassilia]), \
             patch("products.services.objection_service.chat", return_value="فاهم إحساسك."):
            complaint, _ = handle_objection("ليه سعرها غالي كده؟", detect("ليه سعرها غالي كده؟", []), [], self.store,
                                            self.conversation)
        self.assertNotIn("750", complaint)

    def test_a_price_question_gets_no_unrequested_notes_list(self):
        """S02 round 2 full 2 t7: "طب سعر الـ90 من Aventus كام؟" got the notes list above the price."""
        from products.services.price_reply import compose_price_reply
        from products.services.product_formatting import format_products, product_quotes, quote_context
        aventus = self.product("Aventus", ["woody", "fruity", "smoky"], ("Pineapple, Bergamot", "Birch", "Musk, Vanilla"))

        def priced(message, prose):
            context = quote_context(format_products([aventus], show_prices=False), product_quotes([aventus], message),
                                    append=True, request=message)
            return compose_price_reply(prose, context)[0]
        reply = priced("طب سعر الـ90 من Aventus كام؟", "Aventus نوتاته Pineapple, Bergamot، Birch، Musk, Vanilla.")
        self.assertNotIn("Pineapple", reply)
        self.assertIn("90 ملي بـ1000 جنيه", reply)
        asked = priced("سعر الـ90 من Aventus كام؟ ونوتاته إيه؟", "Aventus نوتاته Pineapple, Bergamot، Birch، Musk, Vanilla.")
        self.assertIn("Pineapple", asked)

    def test_asking_for_the_authentic_one_not_the_blend_is_a_liquid_requirement(self):
        """S02 round 2 full 3 t8: "كنت بسأل عن الأصلي مش التركيب" got the original-bottle price with no caveat."""
        from products.services.sales.constraints import original_liquid_update
        self.assertIs(original_liquid_update("تمام كده، أنا كنت بسأل عن الأصلي مش التركيب. هفكر شوية، تسلم."), True)
        self.assertIs(original_liquid_update("الأصلي مش تركيب"), True)
        self.assertIsNone(original_liquid_update("فيه زجاجة أصلية؟"))

    def test_wanting_something_uncommon_puts_niche_first_and_says_so(self):
        """S05 round 2 full 3 t3: "حاجة مميزة ومش منتشرة" got a mainstream Armani beside a niche Xerjoff."""
        from products.services.router import _uncommon_note
        from products.services.search_service import search_products
        mainstream = self.product("Oud Mainstream", ["woody", "oud"])
        niche = self.product("Oud Niche", ["woody", "oud"])
        niche.perfume_type = "niche"
        niche.save(update_fields=["perfume_type"])
        intent = {"gender": "male", "scent_tags": ["woody", "oud"], "scent_match": "any", "wants_uncommon": True}
        names = [p.name for p in search_products(intent, self.store)["products"]]
        self.assertEqual(names[:2], ["Oud Niche", "Oud Mainstream"])
        plain = [p.name for p in search_products({**intent, "wants_uncommon": False}, self.store)["products"]]
        self.assertEqual(plain[0], "Oud Mainstream")
        reply = "Oud Niche ريحته خشبي وعودي. وOud Mainstream عودي."
        self.assertEqual(_uncommon_note(reply, [mainstream, niche], intent), reply + "\n\nOud Niche من فئة النيش.")
        self.assertEqual(_uncommon_note(reply, [mainstream, niche], {"wants_uncommon": False}), reply)

    def test_choose_for_me_ends_in_one_pick(self):
        """S06 round 2 full 3 t6: "خلاص اختارلي انت" got both descriptions and their difference, no pick."""
        from products.services.router import _pick_for_customer, _rephrased
        bloom = self.product("Bloom", ["floral", "fresh", "clean"], gender="female")
        her = self.product("Her", ["fruity", "floral", "sweet"], gender="female")
        self.conversation.preferences = {"gender": "female", "excluded_scent_tags": ["sweet"]}
        self.conversation.save(update_fields=["preferences"])
        self.assertTrue(_pick_for_customer(her, bloom, self.conversation).startswith("لو هختارلك: Bloom"))
        self.assertTrue(_pick_for_customer(bloom, her, None).startswith("لو هختارلك: Bloom"))
        from products.services.product_formatting import format_products
        from products.services.router import _TURN_CONVERSATION
        context = format_products([bloom, her])
        bad = "Bloom ريحته دافية. وHer ريحته ناعمة."
        token = _TURN_CONVERSATION.set(self.conversation)
        try:
            with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
                 patch("products.services.router.repetition.repeated_frames", return_value=[]):
                reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context),
                                         message="أنا محتارة أكتر من الأول 😅 خلاص اختارلي انت")
        finally:
            _TURN_CONVERSATION.reset(token)
        self.assertIn("لو هختارلك: Bloom", reply)

    def test_a_closing_message_about_a_pick_gets_a_short_close(self):
        """S06 round 2 full 3 t7: "خلاص هختار Bloom… هطلبه بعدين، شكراً" got Bloom's description again."""
        from products.services.router import route
        self.product("Bloom", ["floral", "fresh"], gender="female")
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.product_info.chat", side_effect=AssertionError("described")):
            reply, _ = route("خلاص هختار Bloom، حاساه أنسب ليا للاستخدام اليومي 😅 هطلبه بعدين، شكراً", [], self.store,
                             self.conversation)
        self.assertEqual(reply, "تمام يا فندم، ولما تحب تطلبه ابعتلي.")

    def test_a_demand_for_a_guarantee_gets_what_can_and_cannot_be_guaranteed(self):
        """S10 round 2 full 3 t6-t7: "رشحلي حاجة تضمن إنها مش مسكرة" got unconfirmed options with no word."""
        from products.services.router import _guarantee_note
        from products.services.static_faq_service import normalize_arabic
        clean = self.product("Clean Citrus", ["fresh", "citrus"], ("Lemon", "Lavender", "Cedar"))
        vanilla = self.product("Vanilla Fresh", ["fresh"], ("Lemon", "Iris", "Vanilla"))
        intent = {"excluded_scent_tags": ["sweet", "oud"]}
        message = normalize_arabic("طالما مش مضمون يبقى رشحلي حاجة تضمن إنها فريش ومش مسكرة")
        reply = _guarantee_note("Clean Citrus فريش. وVanilla Fresh فريش.", [clean, vanilla], intent, message)
        self.assertTrue(reply.startswith("مقدرش أضمن 100% إن عطر مش مسكر"), reply)
        self.assertIn("Clean Citrus: الحلاوة مش ضمن تصنيفها", reply)
        self.assertNotIn("Vanilla Fresh: الحلاوة", reply)
        self.assertTrue(reply.endswith("جربها في الستور قبل ما تطلب."))
        self.assertEqual(_guarantee_note("x", [clean], {"excluded_scent_tags": ["oud"]}, message), "x")
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues
        self.assertEqual(product_claim_issues(reply, product_claim_facts([clean, vanilla])), [])

    def test_only_this_size_is_unsupported_when_two_sizes_are_stocked(self):
        """S11 round 2 full 3 t3: "المتاح منه هو الحجم ده بس" about a perfume stocked in 50 and 90 ml."""
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues
        nyc = self.product("212 NYC Man", ["fresh"])
        facts = product_claim_facts([nyc])
        self.assertEqual(facts["212 NYC Man"]["sizes"], [50, 90])
        self.assertTrue([i for i in product_claim_issues("212 NYC Man: للأسف المتاح منه هو الحجم ده بس.", facts)
                         if i.startswith("sizes:")])
        self.assertEqual([i for i in product_claim_issues("212 NYC Man: الـ50 مفيش منه سعر أقل.", facts)
                          if i.startswith("sizes:")], [])

    def test_a_heaviness_and_evening_question_answers_both(self):
        """S14 round 2 full 3 t7: "Wanted by Night تقيل أوي ولا ينفع للسهرات؟" got the heaviness only."""
        from products.services.price_reply import requested_product_details
        wanted = self.product("Wanted by Night", ["spicy", "sweet", "heavy"])
        wanted.occasion = "Night Out & Hanging Out"
        wanted.save(update_fields=["occasion"])
        [line] = requested_product_details([wanted], "Wanted by Night تقيل أوي ولا ينفع للسهرات؟")
        self.assertIn("تقيل", line)
        self.assertIn("مناسب للخروجات والسهرات", line)

    def test_back_to_mine_recalls_what_was_offered_for_me(self):
        """S15 round 2 full 3 t5: "نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟" got a perfume never mentioned."""
        from products.services.conversation_service import _switch_recipient
        from products.services.router import _recalled_offer
        black = self.product("Black XS", ["spicy", "sweet", "woody"])
        bad_boy = self.product("Bad Boy", ["spicy", "sweet", "woody"])
        self.say("user", "عايز برفان ليا خشبي للخروج")
        self.say("assistant", "Black XS فيه توابل ومسكر وخشبي، وBad Boy كمان.", [black, bad_boy])
        state = {}
        _switch_recipient(self.conversation, "وكمان عايز حاجة لماما", state)
        self.assertEqual(state["recipient_offers"]["self"], ["Black XS", "Bad Boy"])
        state["recipient"] = "self"
        self.conversation.sales_state = state
        reply, context = _recalled_offer("تمام، طب نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟", self.conversation, self.store)
        self.assertTrue(reply.startswith("كنت رشحتلك Black XS وBad Boy:"), reply)
        self.assertIn("Black XS", context)
        self.assertIsNone(_recalled_offer("رشحلي حاجة خشبية", self.conversation, self.store))

    def test_a_fragment_matches_store_keywords_only_as_whole_words(self):
        """S16 round 2 full 3 t2: "إيه الفرق اللي بيدفعني أشتريه؟" got the payment FAQ through "بيدفعني"."""
        from products.services.router import _faq_answers
        answers = self._faqs()
        from products.models import StaticFAQ
        StaticFAQ.objects.filter(store=self.store, question="طرق الدفع").update(keywords="طرق+الدفع, دفع+استلام, دفع")
        authenticity = "العطور بتاعتنا كلها عطور تركيب."
        reply = _faq_answers("طب إزاي ده بـ1000 والأصلي بآلاف؟ إيه الفرق اللي بيدفعني أشتريه؟", self.store, authenticity)
        self.assertEqual(reply, authenticity)
        self.assertIn(answers["payment"], _faq_answers("الشحن بكام؟ والدفع عند الاستلام متاح؟", self.store, answers["fee"]))

    def test_general_skeptic_questions_name_no_perfume(self):
        """S16 round 2 full 3 t3-t4: general questions were asked to retype a perfume name."""
        from products.services.sales import naming
        for message in ("يعني زي الأصلي 100%؟ وبتقعد قد إيه بجد؟ متقوليش كلام بياعين",
                        "أنا لسه بسأل بشكل عام، مش اسم عطر معين. يعني مش 100%؟ أكيد هتقولي 48 ساعة زي الكل 🙄"):
            with self.subTest(message=message):
                self.assertFalse(naming.may_name_a_perfume(message))
        self.assertTrue(naming.may_name_a_perfume("عايز حاجة زي Sauvage"))

    def test_a_price_question_with_no_name_is_about_the_perfume_on_the_table(self):
        """S02 round 2 full 4 t6: "2000 للـ90؟ ده سعر الستور ولا الأصلي؟" was answered for Aventus, not Layton."""
        from products.services.objection_service import handle_objection
        from products.services.sales import naming
        from products.services.sales.objection import detect
        message = "2000 للـ90؟ ده سعر الستور بتاعكم ولا الأصلي؟ وبالنسبة للـnotes لسه مش مقتنع بصراحة، هفكر فيها شوية."
        self.assertFalse(naming.may_name_a_perfume(message))
        layton = self.product("Layton", ["fruity", "woody", "sweet"])
        self.say("user", "طب سعر الـ90 من Layton كام؟")
        self.say("assistant", "Layton\nزجاجة الستور الخاصة بينا 90 ملي بـ1000 جنيه.", [layton])
        with patch("products.services.objection_service.resolve_products", side_effect=AssertionError("resolver")), \
             patch("products.services.objection_service.chat", return_value="ده سعر زجاجة الستور.") as chat:
            handle_objection(message, detect(message, []), [], self.store, self.conversation)
        self.assertIn("Layton", chat.call_args.args[0][-1]["content"])

    def test_a_second_deferral_gets_a_short_acknowledgement(self):
        """S05 round 2 full 4 t7: "هبعتلك البيانات لما أبقى جاهز أكمل" got the recap and the details ask again."""
        from products.models import Cart, CartItem
        from products.services.order_service import handle_order
        alexandria = self.product("Alexandria II", ["woody", "oud"])
        cart = Cart.objects.create(conversation=self.conversation)
        CartItem.objects.create(cart=cart, variant=alexandria.variants.get(volume=90), quantity=1)
        self.say("user", "تمام، هاخده. هكمل الأوردر بعدين.")
        self.say("assistant", "🛍️ الطلب لحد دلوقتي:\n- 1 × Alexandria II (90ml)\n\nتمام، اختياراتك محفوظة في السلة. "
                              "لما تكون جاهز ابعتلي الاسم ورقم الموبايل ورقم بديل والعنوان.")
        data = {"products": [{"name": "Alexandria II", "volume": 90, "quantity": 1, "bottle_type": "normal"}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            reply, _ = handle_order("تمام، هبعتلك البيانات لما أبقى جاهز أكمل.", [], self.store, self.conversation)
        self.assertEqual(reply, "تمام يا فندم، مستنيك.")

    def test_one_fitting_perfume_leads_a_choice_for_an_occasion(self):
        """S08 round 2 full 4 t2: "أنهي فيهم أنسب للشغل؟" said "needs confirmation" for two of three."""
        from products.services.price_reply import requested_product_details
        from products.occasions import OCCASION_DESCRIPTIONS
        rows = []
        for name, occasion in (("Bleu Office", "Work & Office"), ("Sauvage Daily", "Daily & Casual"),
                               ("Eros Night", "Night Out & Hanging Out")):
            product = self.product(name, ["fresh"])
            product.occasion = occasion
            product.save(update_fields=["occasion"])
            rows.append(product)
        lines = requested_product_details(rows, "أنهي فيهم أنسب للشغل؟")
        self.assertEqual(lines[0], f"الأنسب {OCCASION_DESCRIPTIONS['Work & Office']}: Bleu Office.")
        self.assertNotIn("محتاجة تأكيد", "\n".join(lines))
        [single] = requested_product_details(rows[1:2], "Sauvage Daily ينفع للشغل؟")
        self.assertIn("محتاجة تأكيد", single)

    def test_smoky_in_arabic_letters_is_the_smoky_label(self):
        """S09 round 2 full 4 t4: "فيها سموكي وفاكهة" kept fruit and lost smoky."""
        # The after-denial path reads the absent perfume's described labels with `_tag_mentions`.
        from products.services.scent_description import _tag_mentions
        from products.services.static_faq_service import normalize_arabic
        described = [tag for tag, _, _, negative in _tag_mentions(normalize_arabic("فيها سموكي وفاكهة، إيه أقرب حاجة عندك؟"))
                     if not negative]
        self.assertEqual(sorted(described), ["fruity", "smoky"])

    def test_an_irritated_does_it_not_exist_gets_an_answer_before_the_gender_question(self):
        """S09 round 2 full 4 t2: "يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟" got the bare gender question."""
        from products.models import Message
        from products.services.router import route
        from products.services.sales import gender as sales_gender
        self.say("user", "عندكو Club de Nuit Intense؟")
        self.say("assistant", "للأسف العطر ده مش موجود عندنا.")
        Message.objects.filter(conversation=self.conversation, role="assistant").update(internal_context="ABSENCE_DENIED")
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={}):
            reply, context = route("يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟", [], self.store, self.conversation)
        self.assertTrue(reply.startswith("أيوه للأسف مش عندنا"), reply)
        self.assertTrue(reply.endswith(sales_gender.QUESTION))
        self.assertEqual(context, sales_gender.REQUIRED_MARKER)

    def test_which_trait_leads_is_answered_honestly(self):
        """S14 round 2 full 4 t5/t7: "المسكر أوضح في طابعه" (critical) and no answer to which is clearer."""
        from products.services.scent_description import asked_attribute_answers, description_issues
        pure = self.product("Pure XS", ["spicy", "sweet", "leather"], ("Ginger", "Leather", "Vanilla"))
        question = "طب Pure XS ريحته الجلدية واضحة ولا المسكر أكتر؟"
        facts = {"Pure XS": {"scent_tags": ["spicy", "sweet", "leather"], "notes": ["Ginger", "Leather", "Vanilla"], "occasion": ""}}
        self.assertTrue(description_issues("المسكر أوضح في طابعه، والجلدي موجود كمان.", facts, message=question))
        self.assertEqual(description_issues("الاتنين في طابعه، ومقدرش أحدد أنهي أوضح فيهم.", facts, message=question), [])
        self.assertIn("ومقدرش أحدد أنهي أوضح فيهم من البيانات اللي عندي", asked_attribute_answers(question, pure))

    def test_how_long_it_lasts_in_other_words_gets_the_policy(self):
        """S16 round 2 full 4 t3-t5: "بيقعد قد إيه؟", "يعني عمليًا…", "قولّي تقدير عملي…" got no hours."""
        from products.services.performance_policy import request_for
        from products.services.router import route
        hist = [{"role": "user", "content": "أكيد هتقولي 48 ساعة زي الكل 🙄 يعني عمليًا بيقعد قد إيه؟"},
                {"role": "assistant", "content": "المعلومة دي محتاجة تأكيد من فريق خدمة العملاء."}]
        self.assertTrue(request_for("أكيد هتقولي 48 ساعة زي الكل 🙄 يعني عمليًا بيقعد قد إيه؟").hours)
        self.assertTrue(request_for("لا، قولّي تقدير عملي حتى لو مش مضمون. مش عايز أحوّل لحد تاني عشان أعرف مدة تقريبية.",
                                    hist).hours)
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.product_info.chat", side_effect=AssertionError("model")):
            reply, _ = route("تمام، طب بيقعد قد إيه بجد؟ متقوليش كلام بياعين", [], self.store, self.conversation)
        self.assertTrue(reply.startswith("كل عطورنا ثباتها أكتر من 8 ساعات"), reply)

    def test_cheaper_when_nothing_fits_the_budget_is_answered_plainly(self):
        """S11 round 2 full 2 t4: "مفيش حاجة أرخص؟" under a 700 budget got the 750 quote and nothing else."""
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        nyc = self.product("212 NYC Man", ["fresh"])
        self.conversation.preferences = {"gender": "male", "max_price": 700}
        self.conversation.save(update_fields=["preferences"])
        message = "يعني الـ50 بـ750؟ غالي عليا بصراحة، مفيش حاجة أرخص؟"
        with patch("products.services.objection_service.resolve_products", return_value=[nyc]), \
             patch("products.services.objection_service.chat", return_value=""):
            reply, _ = handle_objection(message, detect(message, []), [], self.store, self.conversation)
        # Round 2 batch 5: said without the amount, which the routed price check rejects
        # (`SecondRoundBatchFiveTests.test_cheaper_when_the_quote_is_the_floor_survives_the_price_check`).
        self.assertIn("مفيش حاجة أرخص منه عندنا حاليًا؛ ده أقل سعر عندنا.", reply)
        self.assertIn("750", reply)

    def test_too_expensive_and_ill_think_gets_space_not_a_pitch(self):
        """S09 round 2 full 2 t6: "غالي أوي كده 😅 هفكر وأشوف" got a smaller-size pitch and a price defence."""
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        message = "غالي أوي كده 😅 هفكر وأشوف"
        with patch("products.services.objection_service.chat", side_effect=AssertionError("pitched")), \
             patch("products.services.objection_service.resolve_products", return_value=[]):
            reply, context = handle_objection(message, detect(message, []), [], self.store, self.conversation)
        self.assertEqual(context, "CUSTOMER_DEFERRED")
        self.assertNotIn("أصغر", reply)
        with patch("products.services.objection_service.chat", return_value="فاهمك.") as chat, \
             patch("products.services.objection_service.resolve_products", return_value=[]):
            handle_objection("غالي عليا، الـ50 بكام بالظبط؟", detect("غالي عليا، الـ50 بكام بالظبط؟", []), [],
                             self.store, self.conversation)
        chat.assert_called_once()

    def test_asking_about_an_excluded_trait_does_not_turn_it_into_a_wish(self):
        """S10 round 2 full 2 t4: "أنا مش بسأل على العود بس…" moved oud from excluded to wanted."""
        from products.services.ai.prompts import get_system_prompt
        from products.services.sales.scent_preferences import explicit_scent_updates
        updates = explicit_scent_updates("أنا مش بسأل على العود بس، قلتلك مش عايز حاجة مسكرة! رشحلي حاجة فريش وحمضيات")
        self.assertNotIn("oud", updates.get("scent_tags") or [])
        self.assertIn("sweet", updates.get("excluded_scent_tags") or [])
        self.assertIn("ومتعتذرش عن غلطة محصلتش", get_system_prompt(self.store))

    def test_than_before_is_not_the_first_perfume(self):
        """S06 round 2 full 2 t7: "أنا محتارة أكتر من الأول… اختارلي انت" became a one-perfume lookup."""
        from products.services.sales.described import ordinal_position
        self.assertIsNone(ordinal_position("أنا محتارة أكتر من الأول 😅 خلاص اختارلي انت"))
        self.assertIsNone(ordinal_position("عايزة حاجة زي الأول"))
        self.assertEqual(ordinal_position("هو الأولاني كان إيه تاني؟"), 0)
        self.assertEqual(ordinal_position("عايز من الأول 50 ملي"), 0)

    def test_a_pick_for_an_occasion_names_each_perfumes_recorded_occasion(self):
        """S08 round 2 full 2 t2: "أنهي فيهم أنسب للشغل؟" gave two of three perfumes' occasions."""
        from products.services.comparison_service import compare_products
        for name, house in (("Bleu de Chanel", "Chanel"), ("Sauvage", "Dior"), ("Eros", "Versace")):
            self.product(name, ["fresh"], brand=Brand.objects.create(store=self.store, name=house))
        with patch("products.services.comparison_service.chat", return_value="Bleu de Chanel أنسب للشغل.") as chat, \
             patch("products.services.comparison_service.resolve_products", return_value=[]):
            compare_products("أنهي أنسب للشغل: Bleu de Chanel ولا Sauvage ولا Eros؟", [], self.store, self.conversation)
        self.assertIn("قول المناسبة المسجلة لكل عطر منهم", chat.call_args.args[0][-1]["content"])

    def test_like_the_original_is_an_authenticity_question_not_a_likeness_request(self):
        """S16 round 2 batch 3 t3-t4: "يعني زي الأصلي 100% ولا لأ؟" got the gender and reference questions."""
        from products.services.sales.reference import similarity_requested
        self.assertFalse(similarity_requested("يعني زي الأصلي 100% ولا لأ؟ عايز أعرف بالظبط أنا بدفع في إيه"))
        self.assertTrue(similarity_requested("عايز حاجة زي Sauvage"))

    def test_a_short_name_means_the_longer_perfume_just_offered(self):
        """S14 round 2 t7: "طب Black XS ده سويت؟" after Black XS L'Exces answered for the plain Black XS."""
        from products.services.product_info import offered_extensions
        plain = self.product("Black XS", ["spicy", "sweet", "woody"])
        exces = self.product("Black XS L'Exces", ["fresh", "woody", "citrus"])
        self.say("user", "رشحلي حاجة تانية")
        self.say("assistant", "Black XS L'Exces ريحته فريش وخشبي.", [exces])
        question = "طب Black XS ده سويت للسهرات ولا فريش أكتر؟"
        self.assertEqual(offered_extensions([plain], question, self.store, self.conversation), [exces])
        self.assertEqual(offered_extensions([plain], "وBlack XS العادي ريحته إيه؟", self.store, self.conversation), [plain])
        self.assertEqual(offered_extensions([plain], "Black XS بكام؟", self.store, self.conversation), [plain])
        self.assertEqual(offered_extensions([plain, exces], "قارن Black XS و Black XS L'Exces", self.store,
                                            self.conversation), [plain, exces])
        self.say("assistant", "Black XS فيه توابل، وBlack XS L'Exces فريش.", [plain, exces])
        self.assertEqual(offered_extensions([plain], question, self.store, self.conversation), [plain])

    def test_confirming_with_nothing_chosen_says_why_instead_of_the_opening_script(self):
        """S20 round 2 t3: "أكدلي الأوردر دلوقتي" with no products got the turn-1 reply word for word."""
        from products.models import Cart, CartItem
        from products.services import promotion_service as promotions
        from products.services.handoff import has_handoff_offer
        first = promotions.UNKNOWN_DETAILS + "\n\n" + promotions.LIMITATION + "\n\n" + promotions.INVITATION
        second = "تفاصيل العرض:\n- عرض\n\n" + promotions.LIMITATION + "\n\n" + promotions.REPEAT_INVITATION
        history = [{"role": "user", "content": "عايز خصم 20%"}, {"role": "assistant", "content": first},
                   {"role": "user", "content": "إيه العرض؟"}, {"role": "assistant", "content": second}]
        message = "إيه كل الكلام ده؟ أنا عايز خصم وخلاص، انت بوت غبي ولا إيه؟ أكدلي الأوردر دلوقتي وخلصني"
        reply, context = promotions.reply(message, history, conversation=self.conversation)
        self.assertTrue(reply.startswith(promotions.NOTHING_TO_CONFIRM), reply)
        self.assertIn(promotions.LIMITATION, reply)
        self.assertTrue(has_handoff_offer([{"role": "assistant", "content": reply}]))
        self.assertEqual(context, promotions.PROMOTION_CONTEXT)
        cart = Cart.objects.create(conversation=self.conversation)
        CartItem.objects.create(cart=cart, variant=self.product("Sauvage", ["fresh"]).variants.first(), quantity=1)
        carted, _ = promotions.reply(message, history, conversation=self.conversation)
        self.assertNotIn(promotions.NOTHING_TO_CONFIRM, carted)

    def test_a_joking_discount_question_gets_a_light_touch_before_the_boundary(self):
        """S18 round 2 full 2 t4: "ولو اشتريت كل حاجة هتعملولي خصم؟ 😂" got the procedure alone."""
        from products.services import promotion_service as promotions
        from products.services.handoff import has_handoff_offer
        reply, _ = promotions.reply("تمام، ولو اشتريت كل حاجة هتعملولي خصم؟ 😂", [], explain=False)
        self.assertTrue(reply.startswith(promotions.PLAYFUL_ACK), reply)
        self.assertIn(promotions.LIMITATION, reply)
        # Round 2 full 5: no transfer offer for a browsing joke (SecondRoundBatchFourTests).
        self.assertFalse(has_handoff_offer([{"role": "assistant", "content": reply}]))
        serious, _ = promotions.reply("عايز خصم على 3 برفانات", [], explain=False)
        self.assertFalse(serious.startswith(promotions.PLAYFUL_ACK))

    def test_genuine_brand_bottles_and_extra_sizes_are_unsupported_claims(self):
        """S16 round 2 full 2 t5/t7: "زجاجة اوريجنال بأحجام مختلفة" and "الزجاجة نفسها أصلية من Dior"."""
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues
        sauvage = self.product("Sauvage", ["fresh", "spicy"])
        ProductVariant.objects.create(product=sauvage, volume=100, price=Decimal("1500"), bottle_type="original", stock=3)
        facts = product_claim_facts([sauvage])
        self.assertEqual(facts["Sauvage"]["original_sizes"], [100])
        self.assertTrue([i for i in product_claim_issues("Sauvage متاح كمان في زجاجة اوريجنال بأحجام مختلفة.", facts)
                         if i.startswith("sizes:")])
        self.assertTrue([i for i in product_claim_issues("Sauvage: الزجاجة نفسها أصلية من Dior، والعطر تركيب.", facts)
                         if i.startswith("authenticity:")])
        self.assertEqual([i for i in product_claim_issues("Sauvage: الزجاجة الأوريجنال بشكل زجاجة البراند، والعطر تركيب.",
                                                          facts) if i.startswith(("authenticity:", "sizes:"))], [])

    def test_a_rose_question_keeps_the_word_rose(self):
        """S15 round 2 full 2 t3: "فيها ورد" was answered "مفيش رد ضمن نوتاته"."""
        from products.services.scent_description import asked_attribute_answers
        party = self.product("Party", ["floral", "fruity"], ("Bergamot", "Rose, Jasmine", "Musk"), gender="female")
        self.assertEqual(asked_attribute_answers("عايز حاجة لماما، تكون هادية فيها ورد", party), ["فيه ورد في نوتاته"])

    def test_a_promotion_reply_is_never_sent_twice_word_for_word(self):
        from products.services import promotion_service as promotions
        first = promotions.LIMITATION + "\n\n" + promotions.INVITATION
        second = "تفاصيل العرض:\n- عرض\n\n" + promotions.LIMITATION + "\n\n" + promotions.REPEAT_INVITATION
        history = [{"role": "user", "content": "عايز الباندل"}, {"role": "assistant", "content": first},
                   {"role": "user", "content": "إيه العرض؟"}, {"role": "assistant", "content": second}]
        reply, _ = promotions.reply("أكدلي الأوردر", history, explain=False)
        self.assertNotIn(reply, (first, second))
        self.assertIn(promotions.LIMITATION, reply)

    def test_a_name_inside_a_longer_word_is_not_said(self):
        """S10 round 2 t4/t6: "Passionfruit… Spices… Leather" counted as recommending Si, Pi and Her."""
        from products.services.sales.naming import names_in
        notes = "نوتاته Lime, Passionfruit، Spices, Vodka, Amber, Leather, Woody Notes."
        self.assertEqual(names_in(notes, ["Si", "Pi", "Her", "Amber"]), ["Amber"])
        self.assertEqual(names_in("جرب وSauvage أو لـHer", ["Sauvage", "Her"]), ["Sauvage", "Her"])
        self.assertEqual(names_in("Stronger With You Intensely", ["Stronger With You", "Stronger With You Intensely"]),
                         ["Stronger With You Intensely"])

    def test_a_yes_no_ingredient_and_trait_question_is_answered_directly(self):
        """S10 round 2 t4: "فيه عود أو ريحته مسكرة؟" got every recorded note and no answer."""
        from products.services.price_reply import requested_product_details
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues
        vib = self.product("212 VIB Man", ["spicy", "fresh", "citrus"],
                           ("Lime, Caviar, Pepper", "Spices, Vodka, Mint", "Amber, Leather, Woody Notes"))
        question = "طب 212 VIB Man فيه عود أو ريحته مسكرة؟"
        [line] = requested_product_details([vib], question)
        self.assertIn("مفيش عود ضمن نوتاته المسجلة", line)
        self.assertIn("الحلاوة مش مؤكدة في طابعه المسجل", line)
        self.assertNotIn("Caviar", line)
        facts = product_claim_facts([vib])
        self.assertEqual(product_claim_issues(line, facts, message=question), [])
        self.assertEqual([i for i in product_claim_issues(line, facts) if i.startswith(("scent:", "note:"))], [])
        [listed] = requested_product_details([vib], "نوتاته إيه؟")
        self.assertIn("Caviar", listed)
        [leather] = requested_product_details([vib], "فيه جلد؟")
        self.assertIn("فيه جلد في نوتاته", leather)

    def test_a_layer_question_gets_the_notes_by_layer(self):
        """S02 round 2 t4: "فلفل في الـbase؟ اللي أعرفه إنه في الـtop" needs the layers, not one list."""
        from products.services.price_reply import requested_product_details
        from products.services.product_formatting import product_claim_facts
        from products.services.reply_sanitizer import product_claim_issues
        layton = self.product("Layton", ["fruity", "woody", "sweet"],
                              ("Apple, Bergamot, Lavender", "Jasmine, Violet, Geranium",
                               "Vanilla, Pepper, Guaiac Wood, Patchouli"))
        question = "استنى، فلفل في الـbase؟ اللي أعرفه إنه في الـtop"
        [line] = requested_product_details([layton], question)
        self.assertIn("مسجل عندنا في الـBase Notes: Vanilla, Pepper", line)
        facts = product_claim_facts([layton])
        self.assertEqual(product_claim_issues(line, facts, message=question), [])
        dry_down = "طب الـdry down بتاعه إيه؟"
        [layered] = requested_product_details([layton], dry_down)
        self.assertIn("الـTop Notes: Apple", layered)
        self.assertIn("الـBase Notes: Vanilla, Pepper", layered)
        self.assertEqual([i for i in product_claim_issues(layered, facts, message=dry_down)
                          if i.startswith("note-layer:")], [])
        [plain] = requested_product_details([layton], "نوتاته إيه؟")
        self.assertTrue(plain.startswith("Layton: نوتاته Apple"), plain)

    def test_an_excluded_sweet_label_ranks_sweet_notes_last(self):
        """S10 round 2 batch 2 t6-t8: Fresh Vibe ("Sweet Orange") was offered first to "واضح إنها مش مسكرة".

        Labels still decide eligibility (`test_requested_and_negative_labels_use_assigned_style`); the notes
        only order perfumes with the same label fit."""
        from products.services.search_service import search_products
        vibe = self.product("Fresh Vibe", ["citrus", "fresh"], ("Mandarin Orange, Sweet Orange", "Ginger", "Musk"))
        clean = self.product("Clean Citrus", ["citrus", "fresh"], ("Lemon, Bergamot", "Lavender", "Cedar"))
        names = lambda intent: [p.name for p in search_products(intent, self.store)["products"]]
        avoiding = names({"gender": "male", "scent_tags": ["fresh", "citrus"], "excluded_scent_tags": ["sweet"]})
        self.assertEqual(avoiding, [clean.name, vibe.name])
        self.assertIn(vibe.name, avoiding)

    def test_a_recommendation_answers_an_excluded_ingredient_without_the_notes_list(self):
        """S10 round 2 batch 2 t4/t8: "مش مسكرة ولا فيها عود" got every recommended perfume's full notes."""
        from products.services.price_reply import requested_product_details
        clean = self.product("Clean Citrus", ["citrus", "fresh"], ("Lemon, Bergamot", "Lavender", "Cedar, Amber"))
        [line] = requested_product_details([clean], "رشحلي حاجة فريش مش مسكرة ولا فيها عود",
                                           description_required=True)
        self.assertIn("مفيش عود ضمن نوتاته المسجلة", line)
        self.assertNotIn("Bergamot", line)
        [both] = requested_product_details([clean], "ريحته ايه ونوتاته ايه؟", description_required=True)
        self.assertIn("Bergamot", both)

    def test_turning_down_everything_on_the_table_is_recognised(self):
        """S10 round 2 batch 2 t3/t5/t8: rejections of both perfumes brought the same two back."""
        from products.services.router import _REJECTS_THE_TABLE
        from products.services.static_faq_service import normalize_arabic
        for message in ("أنا مش عايز حاجة حلاوتها مش مؤكدة! رشحلي حاجة واضح إنها مش مسكرة ومفيهاش عود.",
                        "ولا واحد فيهم واضح إنه مش مسكر! رشحلي حاجة فريش",
                        "طب ما ترشحليش حاجة حلاوتها مش مؤكدة! عايز حاجة واضح إنها مش مسكرة",
                        "برضه الاتنين مش واضح إنهم مش مسكرين!"):
            with self.subTest(message=message):
                self.assertTrue(_REJECTS_THE_TABLE.search(normalize_arabic(message)))
        for message in ("الاتنين حلوين، أنهي أحسن؟", "مش عايز حاجة مسكرة", "عايز حاجة واضحة الريحة"):
            with self.subTest(message=message):
                self.assertFalse(_REJECTS_THE_TABLE.search(normalize_arabic(message)))

    def test_turning_down_the_perfume_just_discussed_excludes_it(self):
        """S10 round 2 t6: "…مش عايزه، رشحلي حاجة مضمونة" got 212 VIB Man again, first."""
        from products.services.router import _rejected_subject
        vib = self.product("212 VIB Man", ["spicy", "fresh", "citrus"])
        self.say("user", "طب 212 VIB Man فيه عود؟")
        self.say("assistant", "212 VIB Man: مفيش عود ضمن نوتاته المسجلة.", [vib])
        message = "طيب طالما الحلاوة مش مؤكدة مش عايزه، رشحلي حاجة مضمونة مش مسكرة ولا فيها عود!"
        self.assertEqual(_rejected_subject(message, self.conversation, self.store), ["212 VIB Man"])
        self.assertEqual(_rejected_subject("مش عايزه حاجة مسكرة، رشحلي حاجة فريش", self.conversation, self.store), [])
        self.assertEqual(_rejected_subject("بلاش ده، فيه غيره؟", self.conversation, self.store), ["212 VIB Man"])

    def test_the_comparison_prompt_answers_store_policy_from_the_approved_answers(self):
        """S07 round 2: the comparison prompt made the exchange policy "needs confirmation"."""
        from products.services.comparison_service import compare_products
        self.product("Sauvage", ["fresh", "spicy"]), self.product("Bleu Night", ["woody"])
        with patch("products.services.comparison_service.chat", return_value="Sauvage فريش وBleu Night خشبي.") as chat:
            compare_products("قارن Sauvage و Bleu Night، ولو مجاش مناسب ينفع أستبدله؟", [], self.store, self.conversation)
        self.assertIn("سياسة الستور", chat.call_args.args[0][-1]["content"])

    def test_nothing_in_budget_drops_the_generic_price_defence(self):
        """S11 round 2: "بنستخدم زيوت عطرية بجودة عالية… بسعر أوفر" against a budget nothing fits."""
        from products.services.objection_service import without_value_defence
        reply = "حقك يا فندم. بنستخدم زيوت عطرية بجودة عالية بسعر أوفر. أقرب سعر عندنا مكتوب تحت."
        self.assertEqual(without_value_defence(reply), "حقك يا فندم. أقرب سعر عندنا مكتوب تحت.")
        self.assertEqual(without_value_defence("الخامات ممتازة."), "الخامات ممتازة.")

    def test_a_likeness_to_a_perfume_without_data_is_unsupported(self):
        """S09 round 2 t7: "Aventus أقرب لـ Club de Nuit" with nothing recorded to compare."""
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Aventus": {"scent_tags": ["fruity", "smoky"], "notes": ["Pineapple"]}}
        self.assertTrue([i for i in product_claim_issues("Aventus أقرب لـ Club de Nuit من غيره.", facts)
                         if i.startswith("similarity:")])
        self.assertEqual([i for i in product_claim_issues("مقدرش أأكد إن Aventus قريب من Club de Nuit.", facts)
                          if i.startswith("similarity:")], [])

    def test_after_a_denial_a_likeness_request_asks_for_the_scent_once(self):
        """S09 round 2: "قريبة من ريحته" after "مش عندنا" offered our own alternatives as references."""
        from products.services.router import _recently_denied
        self.assertFalse(_recently_denied(self.conversation))
        self.say("assistant", "للأسف Club de Nuit مش عندنا.")
        from products.models import Message
        Message.objects.filter(conversation=self.conversation).update(internal_context="ABSENCE_DENIED")
        self.assertTrue(_recently_denied(self.conversation))

    def test_a_closing_message_that_mentions_the_price_gets_a_short_close(self):
        """S09 round 2 t8: "تمام، هفكر… عشان سعره مناسب. تسلم يا باشا" got a smaller-size pitch."""
        from products.services.router import _route
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router.handle_general", side_effect=AssertionError("pitched")), \
             patch("products.services.objection_service.chat", side_effect=AssertionError("pitched")):
            reply, _ = _route("تمام، هفكر في الـ Cigar عشان سعره مناسب. تسلم يا باشا", [], self.store, self.conversation)
        self.assertEqual(reply, "تسلم، خد وقتك.")

    def test_a_pure_close_on_the_faq_branch_gets_a_short_close(self):
        from products.services.router import _route
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router.handle_general", side_effect=AssertionError("pitched")):
            reply, _ = _route("تمام كده، شكراً يا باشا", [], self.store, self.conversation)
        self.assertEqual(reply, "العفو يا فندم، ولو احتجت أي حاجة أنا موجود.")

    def test_a_general_longevity_question_gets_the_store_policy(self):
        """S16 round 2: "طب بيقعد قد إيه بجد؟" was handed to customer service."""
        from products.services.router import route
        for message in ("طب البرفانات عندكم بتقعد قد إيه بجد؟", "طب بيقعد قد إيه بجد؟ متقوليش كلام بياعين"):
            with self.subTest(message=message), patch("products.services.router.classify", return_value="faq"), \
                 patch("products.services.router.handle_general", side_effect=AssertionError("deferred")):
                reply, _ = route(message, [], self.store, self.conversation)
                self.assertTrue(reply.startswith("كل عطورنا"), reply)

    def test_agreeing_with_the_pick_is_not_a_yes_to_a_side_transfer_offer(self):
        """S07 round 2 t4: "تمام، Chance شكله أقربلها.. هتعجبها أكيد؟" ended the sale in a transfer."""
        from products.services.handoff import handoff_offer_reply, has_handoff_offer
        offer = [{"role": "assistant", "content": "Chance أقرب لطلبك. ولو مجتش على ذوقها، أقدر أحولك لفريق خدمة "
                                                  "العملاء لو حابب."}]
        self.assertTrue(has_handoff_offer(offer))
        self.assertIs(handoff_offer_reply("تمام، Chance شكله أقربلها.. هتعجبها أكيد؟", offer), False)
        self.assertIs(handoff_offer_reply("تمام", offer), True)
        self.assertIs(handoff_offer_reply("تمام بس هو هيكلمني امتى؟", offer), True)
        self.assertIs(handoff_offer_reply("ok thanks", offer), True)

    def test_one_message_with_a_scent_for_each_person_is_split(self):
        """S03 round 2: "الفريش ليا للشغل، والسويت لأخويا للخروج" became one fresh-and-sweet request."""
        from products.services.router import _per_recipient_scents
        self.assertEqual(_per_recipient_scents("الفريش ليا للشغل، والسويت لأخويا للخروج"),
                         {"self": ["fresh"], "brother": ["sweet"]})
        self.assertEqual(_per_recipient_scents("عايز حاجة فريش وسويت"), {})
        # S03 round 2 batch 2 t2: the speaker marked with أنا.
        self.assertEqual(_per_recipient_scents("أنا بحب الفريش للشغل، وأخويا بيحب السويت للخروج"),
                         {"self": ["fresh"], "brother": ["sweet"]})
        self.assertEqual(_per_recipient_scents("أنا عايز حاجة فريش وسويت"), {})

    def test_keeping_the_count_after_the_budget_question_is_not_checkout(self):
        """S03 round 2 batch 2 t4: "خليهم اتنين…" after "تحب نخليها 2 زجاجات…؟" carted a perfume nobody chose."""
        from products.services.router import _KEEPS_THE_COUNT, _asked_to_keep_the_count
        from products.services.static_faq_service import normalize_arabic
        asked = [{"role": "assistant", "content": "أرخص 3 زجاجات بالطابع ده إجماليهم 2250 جنيه، وده أعلى من 1600 جنيه. "
                                                  "تحب نخليها 2 زجاجات في نفس الميزانية، ولا نزود الميزانية؟"}]
        self.assertTrue(_asked_to_keep_the_count(asked))
        self.assertFalse(_asked_to_keep_the_count([{"role": "assistant", "content": "تحب أنهي حجم؟"}]))
        self.assertTrue(_KEEPS_THE_COUNT.search(normalize_arabic("خليهم اتنين، بس التاني مش المفروض يبقى سويت لأخويا؟")))
        self.assertFalse(_KEEPS_THE_COUNT.search(normalize_arabic("خلي السوفاج واحد بس")))

    def test_a_total_question_answering_with_the_same_block_gets_the_total_alone(self):
        """S03 round 2 t4/t5/t7: "الإجمالي كده كام بالظبط؟" resent the identical recommendation block."""
        from products.services.router import _total_only_if_repeat
        from products.services.static_faq_service import normalize_arabic
        block = ("Blue Jeans\nريحته فريش.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.\n\n"
                 "Black XS\nفيه ريحة توابل ومسكر.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.\n\n"
                 "إجمالي الـ2 زجاجات: 1500 جنيه.")
        history = [{"role": "user", "content": "الفريش ليا والسويت لأخويا"}, {"role": "assistant", "content": block}]
        question = normalize_arabic("طب الإجمالي كده كام بالظبط؟")
        self.assertEqual(_total_only_if_repeat(block, question, history),
                         "إجمالي الـ2 زجاجات: 1500 جنيه، والمبلغ ده من غير مصاريف الشحن.")
        self.assertEqual(_total_only_if_repeat(block, question, []), block)
        self.assertEqual(_total_only_if_repeat(block, normalize_arabic("فيه حاجة تانية؟"), history), block)

    def test_each_person_gets_a_perfume_from_their_own_scent(self):
        from products.services.router import _pair_recommendation
        fresh = self.product("Cool Fresh", ["fresh", "citrus"])
        sweet = self.product("Night Sweet", ["sweet", "woody"])
        self.product("Both", ["fresh", "sweet"])
        captured = {}

        def recommend(message, products, history, **kwargs):
            captured["products"] = [p.name for p in products]
            return " و".join(p.name for p in products), "CTX"
        with patch("products.services.router.recommend", side_effect=recommend):
            reply, _ = _pair_recommendation("الفريش ليا، والسويت لأخويا", {"self": ["fresh"], "brother": ["sweet"]},
                                            {"gender": "male"}, [], self.store, self.conversation)
        self.assertEqual(captured["products"], [fresh.name, sweet.name])
        self.assertTrue(reply.startswith("Cool Fresh ليك، وNight Sweet لـأخوك."), reply)
        # A combined budget is shared: a sweet perfume that alone eats most of 1600 is not picked.
        pricey = self.product("Sweet Reserve", ["sweet"])
        pricey.variants.update(price=Decimal("1400"))
        with patch("products.services.router.recommend", side_effect=recommend):
            _pair_recommendation("الفريش ليا، والسويت لأخويا", {"self": ["fresh"], "brother": ["sweet"]},
                                 {"gender": "male", "max_price": 1600, "budget_scope": "total", "purchase_quantity": 2},
                                 [], self.store, self.conversation)
        self.assertNotIn("Sweet Reserve", captured["products"])
        # S03 round 2 full 2 t4: keeping the count kept the picks, even beside a better-ranked newcomer;
        # asking for other options frees them.
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.sales_state["recipient_picks"]["self"]["name"], "Cool Fresh")
        office = self.product("Office Fresh", ["fresh"])
        office.occasion = "Work & Office"
        office.save(update_fields=["occasion"])
        intent = {"gender": "male", "occasion": "Work & Office"}
        with patch("products.services.router.recommend", side_effect=recommend):
            _pair_recommendation("خليهم 2، بس كده الإجمالي 1500؟", {"self": ["fresh"], "brother": ["sweet"]}, intent,
                                 [], self.store, self.conversation)
            self.assertEqual(captured["products"][0], "Cool Fresh")
            _pair_recommendation("عايز حاجة تانية ليا", {"self": ["fresh"], "brother": ["sweet"]}, intent,
                                 [], self.store, self.conversation)
            self.assertEqual(captured["products"][0], "Office Fresh")


class EscalationNoticeTests(TestCase):
    """S03 replay t7: the conversation was handed to staff silently; the customer got no notice."""

    def test_escalation_reports_the_handoff(self):
        from products.models import Conversation
        from products.services.router import _escalate_absent_name
        store = Store.objects.create(name="Escalation Store")
        conversation = Conversation.objects.create(store=store)
        history = [{"role": "user", "content": "الاتنين هيطلعوا بكام؟"}, {"role": "assistant", "content": "x"},
                   {"role": "user", "content": "الاتنين هيطلعوا بكام؟"}, {"role": "assistant", "content": "y"}]
        self.assertIs(_escalate_absent_name(conversation, store, "", "الاتنين هيطلعوا بكام؟", history), True)
        conversation.refresh_from_db()
        self.assertTrue(conversation.needs_human)


class SoftObjectionDoesNotHijackRecommendationTests(SimpleTestCase):
    """S07 t2: a request rich in preferences was answered with "what perfume are you thinking of?"."""

    def test_actionable_requests(self):
        from products.services.router import _actionable_request
        self.assertTrue(_actionable_request({"excluded_scent_tags": ["sweet"]}, "مش متأكد بصراحة"))
        self.assertTrue(_actionable_request({}, "مش متأكد.. ترشحلي حاجة آمنة كهدية؟"))
        self.assertFalse(_actionable_request({"gender": "female"}, "حريمي، بس مش عارفة أختار"))


class RephrasedFallbackTests(TestCase):
    """S06 t5, S08 t5, S16 t5: both drafts failed checks and the reply was a non-answer."""

    def setUp(self):
        self.store = Store.objects.create(name="Fallback Store")
        brand = Brand.objects.create(store=self.store, name="Burberry")
        self.product = Product.objects.create(store=self.store, brand=brand, name="Her", gender="female",
                                              scent_tags=["fruity", "floral", "sweet"], occasion="Daily & Casual")
        ProductVariant.objects.create(product=self.product, volume=50, price=Decimal("750"), bottle_type="normal")

    def _quiet(self):
        return (patch("products.services.router.repetition.repeated_sentences", return_value=[]),
                patch("products.services.router.repetition.repeated_frames", return_value=[]))

    def test_general_question_about_the_turns_perfume_falls_back_to_its_description(self):
        from products.services.router import _rephrased
        context = "Name (الاسم الصحيح): Her\nBrand: Burberry\n"
        bad = "Her ريحته خشبية وتقيلة ومناسبة للشغل."
        first, second = self._quiet()
        with first, second:
            reply, _, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context),
                                     message="هو الأولاني كان إيه تاني؟")
        self.assertNotIn("ماقدرش أأكد", reply)
        self.assertIn("Her", reply)

    def test_general_longevity_question_gets_the_store_policy(self):
        from products.services.performance_policy import performance_turn
        from products.services.router import _rephrased
        bad = "عطورنا بتقعد 48 ساعة."
        first, second = self._quiet()
        with performance_turn("الثبات بيقعد قد إيه عموماً؟", []), first, second:
            reply, _, _ = _rephrased(bad, "", [], self.store, lambda hint: (bad, ""),
                                     message="الثبات بيقعد قد إيه عموماً؟")
        self.assertIn("كل عطورنا", reply)
        self.assertNotIn("48", reply)


class SecondRoundBatchFourTests(TestCase):
    """Second round, batch 4: the failures of round-2 full run 5."""

    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Batch Four Store")
        self.brand = Brand.objects.create(store=self.store, name="House")
        self.conversation = Conversation.objects.create(store=self.store)

    def product(self, name, tags, notes=("", "", ""), gender="male"):
        product = Product.objects.create(store=self.store, brand=self.brand, name=name, gender=gender,
                                         scent_tags=list(tags), top_notes=notes[0], middle_notes=notes[1],
                                         base_notes=notes[2])
        for volume, price in ((50, "750"), (90, "1000")):
            ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type="normal")
        return product

    def shipping(self):
        from products.models import StaticFAQ, StoreSettings
        StaticFAQ.objects.create(store=self.store, question="بكام مصاريف الشحن؟", answer="مصاريف الشحن 60 جنيه لاي مكان في مصر",
                                 keywords="شحن+بكام, شحن+مصاريف, شحن", priority=3)
        StoreSettings.objects.create(store=self.store, promotions="2- الشحن مجاني لاي اوردر فوق ال ١٥٠٠")

    def exchange_policy(self):
        from products.models import StaticFAQ
        policy = ("احنا بنتعامل مع شركة شحن QP غير مسموح بفتح الأوردر عند الاستلام.\n"
                  "ضمان الاستبدال لمدة يومين من وقت الاستلام.\n"
                  " في حالة الاستبدال: يتم دفع شحن مرتين + 150ج للبوكس الجديد، أو مجانًا داخل الستور.\n\n"
                  "وفي الحالات التالية:\n"
                  "• الميكسات غير قابلة للاستبدال أو الاسترجاع لأنها Customized علي طلب العميل.\n\n"
                  "شكرًا لتفهمكم لسياسة الشركة")
        StaticFAQ.objects.create(store=self.store, question="في استرجاع أو استبدال؟", priority=10, answer=policy,
                                 keywords="استرجاع, استبدال")
        return policy

    def test_both_recipients_in_one_message_switch_to_the_one_asked_for(self):
        """S15 round 2 full 5 t3: "Aventus شكله مناسب ليا، وكمان عايز حاجة لماما" kept his profile for her."""
        from products.services.conversation_service import merge_preferences, recipient_of
        message = "Aventus شكله مناسب ليا، وكمان عايز حاجة لماما تكون هادية فيها ورد"
        self.assertEqual(recipient_of(message), "mother")
        self.assertEqual(recipient_of("لماما خلاص اخترنا، عايز حاجة ليا"), "self")
        self.assertIsNone(recipient_of("عايز 2 برفان رجالي، واحد ليا وواحد لأخويا"))

        def merge(intent, text):
            merge_preferences(self.conversation, intent, text, store=self.store)
            self.conversation.refresh_from_db()
            return self.conversation.preferences

        mine = merge({"gender": "male", "scent_tags": ["woody"]}, "رجالي ليا، بحب الريحة الخشبية")
        self.assertEqual((mine["gender"], mine["scent_tags"]), ("male", ["woody"]))
        # The extractor reads the history and brings his woody back on her request.
        mom = merge({"gender": "female", "scent_tags": ["woody", "floral"]}, message)
        self.assertEqual(mom.get("gender"), "female")
        self.assertEqual(mom.get("scent_tags"), ["floral"])
        back = merge({}, "طب نرجع لبتاعي، كنت قلت إيه أحسن واحد ليا؟")
        self.assertEqual((back["gender"], back["scent_tags"]), ("male", ["woody"]))

    def test_a_label_said_again_for_the_new_recipient_carries_over(self):
        """The S15 guard drops only what the customer did not say for the new recipient."""
        from products.services.conversation_service import merge_preferences
        merge_preferences(self.conversation, {"gender": "male", "scent_tags": ["woody"]}, "رجالي ليا، بحب الخشبي",
                          store=self.store)
        merge_preferences(self.conversation, {"scent_tags": ["woody", "floral"]},
                          "وكمان عايز حاجة لماما خشبي وفيها ورد", store=self.store)
        self.conversation.refresh_from_db()
        self.assertEqual(sorted(self.conversation.preferences.get("scent_tags")), ["floral", "woody"])

    def test_a_missing_perfume_gets_no_promised_lookalike(self):
        """S09 round 2 full 5 t2: "أقدر ألاقيلك حاجة قريبة منه" failed the honest-similarity criterion."""
        from products.models import Message
        from products.services.conversation_service import save_message
        from products.services.router import route
        from products.services.sales import gender as sales_gender
        save_message(self.conversation, "user", "عندكو Club de Nuit Intense؟")
        save_message(self.conversation, "assistant", "للأسف العطر ده مش موجود عندنا.")
        Message.objects.filter(conversation=self.conversation, role="assistant").update(internal_context="ABSENCE_DENIED")
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent", return_value={}):
            reply, _ = route("يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟", [], self.store, self.conversation)
        self.assertTrue(reply.startswith("أيوه للأسف مش عندنا"), reply)
        self.assertIn("معنديش بيانات عن ريحته", reply)
        self.assertNotIn("قريبة منه", reply)
        self.assertTrue(reply.endswith(sales_gender.QUESTION))

    def test_a_browsing_joke_about_a_discount_gets_no_transfer_offer(self):
        """S18 round 2 full 5 t4: "لو اشتريت كل حاجة هتعملولي خصم؟ 😂" ended on "تحب أحولك لمندوب؟"."""
        from products.models import Cart, CartItem
        from products.services import promotion_service as promotions
        from products.services.handoff import has_handoff_offer
        reply, _ = promotions.reply("طب لو اشتريت كل حاجة هتعملولي خصم؟ 😂", [], explain=False,
                                    conversation=self.conversation)
        self.assertTrue(reply.startswith(promotions.PLAYFUL_ACK), reply)
        self.assertIn(promotions.LIMITATION, reply)
        self.assertTrue(reply.endswith(promotions.PLAYFUL_CLOSE), reply)
        self.assertFalse(has_handoff_offer([{"role": "assistant", "content": reply}]))
        # With perfumes in the cart the joke is about a real purchase, and the team can act on it.
        cart = Cart.objects.create(conversation=self.conversation)
        CartItem.objects.create(cart=cart, variant=self.product("Sauvage", ["fresh"]).variants.first(), quantity=1)
        carted, _ = promotions.reply("طب لو اشتريت كل حاجة هتعملولي خصم؟ 😂", [], explain=False,
                                     conversation=self.conversation)
        self.assertTrue(has_handoff_offer([{"role": "assistant", "content": carted}]))

    def test_a_closing_restatement_is_not_answered_with_the_faq(self):
        """S02 round 2 full 5 t8: "كده النوتات دي للتركيبة… هفكر وأقارن شوية، شكراً" got the تركيب pitch."""
        from products.models import StaticFAQ
        from products.services.router import route
        pitch = "العطور بتاعتنا كلها عطور تركيب بخامات عالية الجودة."
        StaticFAQ.objects.create(store=self.store, question="أصلي ولا تركيب؟", keywords="تركيبه, تركيب", answer=pitch,
                                 is_active=True)
        message = "تمام، كده النوتات دي للتركيبة عندكم مش نوتات رسمية من البراند. هفكر وأقارن شوية، شكراً."
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router._handle_general_raw", return_value=("رد عام", "")), \
             patch("products.services.objection_service.resolve_products", return_value=[]), \
             patch("products.services.objection_service.chat", return_value="تمام، خد وقتك."):
            reply, _ = route(message, [], self.store, self.conversation)
        self.assertNotIn(pitch, reply)
        with patch("products.services.router.classify", return_value="faq"):
            asked, _ = route("هو العطر تركيب؟", [], self.store, self.conversation)
        self.assertIn(pitch, asked)

    def test_a_repeated_total_question_states_the_shipping(self):
        """S03 round 2 full 5 t4: "1500 جنيه، من غير مصاريف الشحن" was read as "no shipping charge"."""
        from products.services.router import _total_only_if_repeat
        from products.services.static_faq_service import normalize_arabic
        self.shipping()
        block = "212 NYC Man ليك.\n\nإجمالي الـ2 زجاجات: 1500 جنيه."
        history = [{"role": "user", "content": "عايز اتنين"}, {"role": "assistant", "content": block}]
        question = normalize_arabic("خليهم 2، بس عايز أتأكد إن الإجمالي 1500 ومفيش أي مصاريف تانية؟")
        self.assertEqual(_total_only_if_repeat(block, question, history, self.store),
                         "إجمالي الـ2 زجاجات: 1500 جنيه. وفيه مصاريف شحن 60 جنيه، فالإجمالي مع الشحن 1560 جنيه.")
        over = block.replace("1500", "2000")
        self.assertIn("شحن مجاني لأي أوردر فوق 1500 جنيه",
                      _total_only_if_repeat(over, question, [{"role": "assistant", "content": over}], self.store))

    def test_the_running_cart_total_states_the_shipping_when_asked(self):
        """S03 round 2 full 5 t7: "والإجمالي يفضل 1500؟" was confirmed at 1500 without the 60 EGP fee."""
        from products.services.order_service import _cart_recap
        self.shipping()
        variant = self.product("212 NYC Man", ["fresh"]).variants.get(volume=50)
        items = [{"variant": variant, "quantity": 2, "price": Decimal("750"), "bottle_type": "normal"}]
        asked = _cart_recap(self.conversation, items, Decimal("1500"), "50 ملي للاتنين، والإجمالي يفضل 1500؟")
        self.assertIn("المجموع: 1500 جنيه.", asked)
        self.assertIn("وفيه مصاريف شحن 60 جنيه، فالإجمالي مع الشحن 1560 جنيه.", asked)
        self.assertNotIn("💰 الإجمالي:", asked)
        self.assertNotIn("شحن", _cart_recap(self.conversation, items, Decimal("1500"), "50 ملي للاتنين"))

    def test_taking_the_quoted_bottles_as_they_are_needs_no_size_question(self):
        """S03 round 2 full 5 t5-t6: "هاخدهم كده" after one quoted bottle each re-asked bottle and size."""
        from products.models import Cart
        from products.services.conversation_service import save_message
        from products.services.order_service import handle_order
        from products.services.product_formatting import ProductQuote, quote_context
        nyc = self.product("212 NYC Man", ["fresh"])
        sexy = self.product("212 Sexy man", ["sweet"])
        quotes = [ProductQuote.from_variant(product.variants.get(volume=50)) for product in (nyc, sexy)]
        save_message(self.conversation, "user", "واحد ليا وواحد لأخويا ب1600")
        save_message(self.conversation, "assistant", "212 NYC Man ليك، و212 Sexy man لـأخوك.",
                     internal_context=quote_context("", quotes, append=True))
        data = {"products": [{"name": "212 NYC Man", "volume": None, "quantity": 1, "bottle_type": None},
                             {"name": "212 Sexy man", "volume": None, "quantity": 1, "bottle_type": None}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            reply, _ = handle_order("تمام، هاخدهم كده. شكراً", [], self.store, self.conversation)
        cart = Cart.objects.get(conversation=self.conversation)
        lines = sorted((item.variant.product.name, item.variant.volume, item.variant.bottle_type)
                       for item in cart.items.all())
        self.assertEqual(lines, [("212 NYC Man", 50, "normal"), ("212 Sexy man", 50, "normal")], reply)
        self.assertNotIn("نوع الزجاجة", reply)

    def test_a_quote_listing_two_sizes_still_gets_the_size_question(self):
        """The S03 fill applies only to an exact quotation of one variant, taken "كده"."""
        from products.services.conversation_service import save_message
        from products.services.order_service import _single_quoted_variant
        from products.services.product_formatting import ProductQuote, quote_context
        nyc = self.product("212 NYC Man", ["fresh"])
        both = [ProductQuote.from_variant(variant) for variant in nyc.variants.all()]
        save_message(self.conversation, "assistant", "212 NYC Man", internal_context=quote_context("", both, append=True))
        self.assertIsNone(_single_quoted_variant(self.conversation, nyc, "هاخده كده"))
        one = [ProductQuote.from_variant(nyc.variants.get(volume=90))]
        save_message(self.conversation, "assistant", "212 NYC Man 90", internal_context=quote_context("", one, append=True))
        self.assertEqual(_single_quoted_variant(self.conversation, nyc, "هاخده كده").volume, 90)
        self.assertIsNone(_single_quoted_variant(self.conversation, nyc, "هاخده"))

    def test_close_to_her_taste_is_not_a_likeness_request(self):
        """S07 round 2 full 5 t4: "في حاجة تانية قريبة من ذوقها؟" got "تقصد عطر شبه أنهي عطر أو إصدار؟"."""
        from products.services.sales.reference import similarity_requested
        self.assertFalse(similarity_requested("في حاجة تانية قريبة من ذوقها؟ والميزانية لحد 1000"))
        self.assertFalse(similarity_requested("عايز حاجة قريبة من اللي بتحبه"))
        self.assertTrue(similarity_requested("عايز حاجة قريبة من Aventus"))
        self.assertTrue(similarity_requested("فيه حاجة شبه Sauvage؟"))

    def test_an_exchange_question_with_a_remark_gets_the_store_policy_alone(self):
        """S07 round 2 full 5 t5-t7: "ينفع أبدله لو مجاش على ذوقها؟" got the 50 ml price, twice identical."""
        from products.services.router import _policy_side_questions
        policy = self.exchange_policy()
        answers, rest, asks = _policy_side_questions(
            "تمام، هفكر في الـ50 ملي وأطلبه إن شاء الله، بس ينفع أبدله لو مجاش على ذوقها؟", self.store)
        self.assertEqual((answers, asks), ([policy], False))
        answers, rest, asks = _policy_side_questions(
            "طب الـ50 ملي ينفع أبدله لو مجاش على ذوقها؟ هفكر أطلبه إن شاء الله", self.store)
        self.assertEqual((answers, asks), ([policy], False))
        # A request beside it still gets its answer, and its budget clause stays with it.
        answers, rest, asks = _policy_side_questions(
            "معرفش هي بتحب أنهي عطر بالاسم، عايز حاجة هادية وخفيفة في حدود 1000. لو مجاش على ذوقها ينفع أبدله؟",
            self.store)
        self.assertEqual((answers, asks), ([policy], True))
        self.assertIn("في حدود 1000", rest)
        answers, rest, asks = _policy_side_questions(
            "في حاجة تانية قريبة من ذوقها؟ والميزانية لحد 1000، ولو مجاش على ذوقها أعمل إيه؟", self.store)
        self.assertEqual((answers, asks), ([policy], True))
        self.assertIn("والميزانية لحد 1000", rest)

    def test_going_back_or_swapping_a_size_is_not_an_exchange_question(self):
        """The clause split must not send "ينفع نرجع للأولاني؟" to the exchange policy."""
        from products.services.router import _is_exchange_question, _policy_side_questions
        from products.services.static_faq_service import normalize_arabic
        self.exchange_policy()
        for message in ("ينفع نرجع للأولاني؟", "ممكن أبدل الـ50 بالـ90؟", "ينفع ابدلها بال Sauvage؟"):
            with self.subTest(message=message):
                self.assertFalse(_is_exchange_question(normalize_arabic(message)))
                self.assertEqual(_policy_side_questions(message, self.store)[0], [])
        self.assertTrue(_is_exchange_question(normalize_arabic("ينفع ارجعه لو مجاش على ذوقها؟")))

    def test_a_policy_asked_again_is_shortened_not_resent(self):
        """S07 round 2 full 5 t3-t7: the exchange policy repeated in full, then an identical reply."""
        from products.services.router import _policy_answer
        policy = self.exchange_policy()
        self.assertEqual(_policy_answer(policy, []), policy)
        history = [{"role": "assistant", "content": "مقدرش أضمن إنها هتعجبها.\n\n" + policy}]
        brief = _policy_answer(policy, history)
        self.assertTrue(brief.startswith("زي ما قلتلك:"), brief)
        self.assertIn("ضمان الاستبدال لمدة يومين من وقت الاستلام.", brief)
        self.assertNotIn("QP", brief)
        history.append({"role": "assistant", "content": brief})
        shorter = _policy_answer(policy, history)
        self.assertEqual(shorter, "زي ما قلتلك، ضمان الاستبدال لمدة يومين من وقت الاستلام.")

    def test_not_sweet_is_never_inferred_from_a_missing_label(self):
        """S07 round 2 full 5 t2: "Cassilia… ومش حلو زيادة" with no record of its sweetness (owner decision 4)."""
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Cassilia": {"scent_tags": ["floral", "fruity", "light"], "notes": ["Pear", "Rose", "Musk"],
                              "season": "", "occasion": ""}}
        message = "هي بتحب الحاجات الخفيفة أكتر ومش مسكرة أوي، ترشحلي إيه؟"
        for reply in ("Cassilia ريحته فيها زهور وفواكه وخفيف، ومش حلو زيادة.", "Cassilia مش مسكرة أوي."):
            with self.subTest(reply=reply):
                self.assertTrue([i for i in product_claim_issues(reply, facts, message=message) if "not sweet" in i])
                # A legacy direct call (no current message) keeps its original scope
                # (`test_scent_interpretation`'s fixed-negation test).
                self.assertEqual([i for i in product_claim_issues(reply, facts) if "not sweet" in i], [])
        for reply in ("Cassilia ريحته فيها زهور وفواكه وخفيف.",
                      "مقدرش أضمن 100% إن عطر مش مسكر من البيانات اللي عندي. "
                      "Cassilia: الحلاوة مش ضمن تصنيفها، ومفيش فانيليا ولا عسل في نوتاتها."):
            with self.subTest(reply=reply):
                self.assertEqual([i for i in product_claim_issues(reply, facts, message=message) if "not sweet" in i], [])

    def test_a_recorded_note_is_not_retracted_or_denied(self):
        """S02 round 2 full 5 t4: "أنا ذكرت الفانيليا في قاعدة Aventus بالغلط" about a stored base note."""
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Aventus": {"scent_tags": ["woody", "fruity", "smoky"],
                             "notes": ["Pineapple, Bergamot", "Birch, Patchouli", "Musk, Oak moss, Ambergris, Vanilla"],
                             "season": "", "occasion": ""},
                 "Layton": {"scent_tags": ["fruity", "sweet"], "notes": ["Apple", "Jasmine", "Vanilla, Pepper"],
                            "season": "", "occasion": ""}}
        retracted = product_claim_issues("معاك حق؛ أنا ذكرت الفانيليا في قاعدة Aventus بالغلط. نوتات Layton: تفاح.", facts)
        self.assertIn("retraction:aventus:vanilla is in the stored notes; it was not a mistake", retracted)
        self.assertTrue([i for i in product_claim_issues("Aventus مفيهوش فانيليا.", facts) if "denied vanilla" in i])
        for reply in ("Aventus: الفانيليا موجودة فعلًا في الـBase Notes المسجلة عندي، ومش غلط.",
                      "Aventus: الـCardamom مش موجود ضمن النوتات المسجلة.",
                      "Aventus: الفانيليا مش ضمن الـTop Notes، هي في الـBase Notes."):
            with self.subTest(reply=reply):
                issues = product_claim_issues(reply, facts)
                self.assertEqual([i for i in issues if i.startswith("retraction") or "denied" in i], [], issues)

    def test_an_answered_strength_question_is_dropped(self):
        """S06 round 2 full 5 t5: "مش تقيلة" was followed two turns later by "تحبي الحاجة أخف ولا أقوى شوية؟"."""
        from products.services.router import _without_answered_strength_question
        reply = "فاهمة إنك محتارة 😅 الأولاني كان Cassilia، طابعه زهري وفاكهي وخفيف. تحبي الحاجة أخف ولا أقوى شوية؟"
        prefs = {"scent_tags": ["light", "floral"], "avoid_traits": ["heavy"], "excluded_scent_tags": ["heavy"]}
        self.assertEqual(_without_answered_strength_question(reply, prefs),
                         "فاهمة إنك محتارة 😅 الأولاني كان Cassilia، طابعه زهري وفاكهي وخفيف.")
        self.assertEqual(_without_answered_strength_question(reply, {"scent_tags": ["floral"]}), reply)
        other = "الأولاني كان Cassilia. تحبي أقولك سعره؟"
        self.assertEqual(_without_answered_strength_question(other, prefs), other)

    def test_is_the_price_fixed_is_not_a_longevity_question(self):
        """S11 round 2 full 5 t3-t4: "لو السعر ثابت قولّي بصراحة" got "ثباته ممتاز" twice."""
        from products.services.performance_policy import request_for
        for message in ("يعني الـ50 بـ750؟ لو السعر ثابت قولّي بصراحة، مش عايز أطلب وبعدين يطلع فيه زيادة",
                        "أنا بسأل السعر ثابت 750 ولا فيه زيادة؟"):
            with self.subTest(message=message):
                self.assertFalse(request_for(message).longevity)
        self.assertTrue(request_for("هو ثابت قد إيه؟").longevity)
        self.assertTrue(request_for("ثباته عامل إزاي؟").longevity)

    def test_is_the_price_fixed_gets_the_store_answer(self):
        """S11 round 2 full 5 t3-t4: the concern itself ("يطلع فيه زيادة") was never answered."""
        from products.services.router import _fixed_price_note
        self.shipping()
        self.assertEqual(
            _fixed_price_note("أنا بسأل السعر ثابت 750 ولا فيه زيادة؟", self.store),
            "السعر ده ثابت زي ما هو، ومفيش أي زيادة عليه غير مصاريف الشحن 60 جنيه، "
            "والشحن مجاني لأي أوردر فوق 1500 جنيه.")
        self.assertEqual(_fixed_price_note("الـ50 بكام؟", self.store), "")
        bare = Store.objects.create(name="No Shipping Facts")
        self.assertEqual(_fixed_price_note("لو السعر ثابت قولّي", bare),
                         "السعر ده ثابت زي ما هو، والزيادة الوحيدة عليه هي مصاريف الشحن.")

    def test_the_brand_bottle_shape_is_not_the_store_bottle(self):
        """S16 round 2 full 5 t1: "زجاجة اوريجنال بنفس شكل زجاجة البراند" became "…شكل زجاجة الستور الخاصة بينا"."""
        from products.services.bottle_wording import NORMAL_BOTTLE, normalize_bottle_wording
        text = "ومتاح لبعضها زجاجة اوريجنال بنفس شكل زجاجة البراند، لكن السائل تركيب من عندنا."
        rendered = normalize_bottle_wording(text)
        self.assertIn("بنفس شكل زجاجة البراند الأصلي", rendered)
        self.assertNotIn(NORMAL_BOTTLE, rendered)
        self.assertEqual(normalize_bottle_wording(rendered), rendered)
        self.assertEqual(normalize_bottle_wording("عايز زجاجة البراند 50 ملي"), "عايز " + NORMAL_BOTTLE + " 50 ملي")

    def test_a_closing_remark_is_not_a_new_search(self):
        """S02 round 2 full 5 t8: the closing remark read as a new goal by `request_mode`."""
        from products.services.router import route
        message = "تمام، كده النوتات دي للتركيبة عندكم مش نوتات رسمية من البراند. هفكر وأقارن شوية، شكراً."
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router.extract_intent") as extract, \
             patch("products.services.router._handle_general_raw", return_value=("رد عام", "")), \
             patch("products.services.objection_service.resolve_products", return_value=[]), \
             patch("products.services.objection_service.chat", return_value="تمام، خد وقتك."):
            reply, _ = route(message, [], self.store, self.conversation)
        extract.assert_not_called()
        self.assertNotIn("رجالي ولا حريمي", reply)

    def test_like_it_after_a_denial_is_not_our_alternative(self):
        """S09 round 2 batch 4 t2: "طب عندك حاجة شبهه؟" after a denial listing one alternative became
        "similar to 1 Million"."""
        from products.models import Message
        from products.services.conversation_service import save_message
        from products.services.sales.reference import prepare_similarity
        self.product("1 Million", ["spicy", "leather", "sweet"])
        save_message(self.conversation, "user", "عندكو Club de Nuit Intense؟")
        save_message(self.conversation, "assistant", "للأسف العطر ده مش موجود عندنا. كبدائل متوفرة:\n1 Million: فيه ريحة توابل.")
        Message.objects.filter(conversation=self.conversation, role="assistant").update(
            internal_context="ABSENCE_DENIED\nName (الاسم الصحيح): 1 Million")
        message = "يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟"
        prepared = prepare_similarity(self.conversation, {"similar_to": "Club de Nuit Intense"}, message, [], self.store)
        self.assertNotEqual(prepared.intent.get("similar_to"), "1 Million")
        self.assertNotEqual(prepared.preferences.get("similar_to"), "1 Million")
        # Without the denial, "شبهه" right after one offered perfume still means that perfume.
        Message.objects.filter(conversation=self.conversation, role="assistant").update(
            internal_context="Name (الاسم الصحيح): 1 Million")
        prepared = prepare_similarity(self.conversation, {}, "طب عندك حاجة شبهه؟", [], self.store)
        self.assertEqual(prepared.intent.get("similar_to"), "1 Million")

    def test_keeping_the_count_does_not_resend_the_pair(self):
        """S03 round 2 batch 4 t5: "خلاص خليهم اتنين، بس مش هزود عن 1600 بالشحن" re-sent the identical pair."""
        from products.services.router import _total_only_if_repeat
        from products.services.static_faq_service import normalize_arabic
        self.shipping()
        block = ("212 NYC Man ليك، و212 Sexy man لـأخوك.\n\n212 NYC Man\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.\n\n"
                 "212 Sexy man\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.\n\nإجمالي الـ2 زجاجات: 1500 جنيه.")
        history = [{"role": "user", "content": "عايز اتنين"}, {"role": "assistant", "content": block}]
        message = normalize_arabic("خلاص خليهم اتنين، بس مش هزود عن 1600 بالشحن.")
        self.assertEqual(_total_only_if_repeat(block, message, history, self.store),
                         "تمام، 212 NYC Man ليك، و212 Sexy man لـأخوك.\nإجمالي الـ2 زجاجات: 1500 جنيه. "
                         "وفيه مصاريف شحن 60 جنيه، فالإجمالي مع الشحن 1560 جنيه.")
        self.assertEqual(_total_only_if_repeat(block, normalize_arabic("طب رشحلي حاجة تانية"), history, self.store), block)

    def test_a_challenge_is_not_answered_with_an_invented_mistake(self):
        """S11 round 2 batch 4 t3: "طيب ليه قلت مفيش حاجة تحت 700؟" got "أنا غلطت لما قلت مفيش حاجة تحت 700"."""
        from products.services.router import _challenge_retraction_issues
        message = "طيب ليه قلت مفيش حاجة تحت 700؟ الـ50 بكام بالظبط؟"
        self.assertTrue(_challenge_retraction_issues("معاك حق، أنا غلطت لما قلت مفيش حاجة تحت 700.", message))
        self.assertEqual(_challenge_retraction_issues("لأن أقل سعر عندنا 750 جنيه للـ50 ملي، فمفيش حاجة تحت 700.",
                                                      message), [])
        self.assertEqual(_challenge_retraction_issues("معاك حق، أنا غلطت.", "الـ50 بكام؟"), [])

    def test_a_purchase_message_is_not_swallowed_by_the_repetition_redirect(self):
        """S11 round 2 batch 4 t6-t7: after four near-identical price answers, "خلاص هاخد الـ50 من
        الأولاني، الشحن بكام؟" went to the general model; the cart stayed empty and the next turn asked
        "تحب تطلب أنهي عطر؟"."""
        from products.models import Cart
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        from products.services.router import route
        self.shipping()
        nyc = self.product("212 NYC Man", ["fresh"])
        answer = "212 NYC Man\nفعلاً عندك حق، الـ50 أعلى من ميزانيتك.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه."
        for _ in range(4):
            save_message(self.conversation, "user", "طيب الـ50 بكام بالظبط؟")
            save_message(self.conversation, "assistant", answer, internal_context=format_products([nyc]))
        history = [{"role": row.role, "content": row.content} for row in self.conversation.messages.order_by("id")]
        data = {"products": [{"name": "212 NYC Man", "volume": 50, "quantity": 1, "bottle_type": None}],
                "customer_name": None, "customer_phone": None, "customer_secondary_phone": None,
                "shipping_address": None, "is_confirmed": False}
        with patch("products.services.router.classify", return_value="order"), \
             patch("products.services.order_service.chat", return_value=json.dumps(data)), \
             patch("products.services.router.handle_general") as general:
            reply, _ = route("خلاص هاخد الـ50 من الأولاني، الشحن بكام؟", history, self.store, self.conversation)
        general.assert_not_called()
        cart = Cart.objects.get(conversation=self.conversation)
        self.assertEqual([(item.variant.product.name, item.variant.volume) for item in cart.items.all()],
                         [("212 NYC Man", 50)], reply)
        self.assertIn("مصاريف الشحن 60 جنيه", reply)

    def test_a_kind_of_perfume_said_with_fih_is_a_style_wish(self):
        """S09 round 2 batch 4 t3: "بحب الريحة اللي فيها سموكي وفاكهة" lost both labels (the model's too)."""
        from products.services.sales.scent_preferences import explicit_scent_updates, reconcile_scent_intent
        message = "رجالي، بحب الريحة اللي فيها سموكي وفاكهة"
        self.assertEqual(explicit_scent_updates(message).get("scent_tags"), ["smoky", "fruity"])
        kept = reconcile_scent_intent({"scent_tags": ["smoky", "fruity"], "scent_open": False}, message)
        self.assertEqual(kept.get("scent_tags"), ["smoky", "fruity"])
        self.assertEqual(explicit_scent_updates("عايز حاجة فيها فواكه").get("scent_tags"), ["fruity"])
        self.assertEqual(explicit_scent_updates("مش عايز حاجة فيها سموكي").get("excluded_scent_tags"), ["smoky"])
        # A question about a perfume is not a wish, and a real ingredient stays an ingredient.
        self.assertFalse(explicit_scent_updates("Aventus فيه سموكي؟").get("scent_tags"))
        self.assertFalse(explicit_scent_updates("فيه فواكه؟").get("scent_tags"))
        self.assertFalse(explicit_scent_updates("عايز عطر فيه عود").get("scent_tags"))
        self.assertEqual(explicit_scent_updates("عايز حاجة فيها فانيليا").get("notes"), ["vanilla"])

    def test_after_a_denial_the_described_scent_is_recommended(self):
        """S09 round 2 batch 4 t2-t3, end to end: "شبهه" then "رجالي، بحب الريحة اللي فيها سموكي وفاكهة"
        searched for 1 Million lookalikes (and, with that fixed, would have re-asked the scent)."""
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        from products.services.router import route
        rows = {name: self.product(name, tags) for name, tags in (
            ("Aventus", ["woody", "fruity", "smoky"]), ("Cigar", ["woody", "smoky", "fruity"]),
            ("1 Million", ["spicy", "leather", "sweet"]), ("Pure XS", ["spicy", "sweet", "leather"]))}
        save_message(self.conversation, "user", "عندكو Club de Nuit Intense؟")
        save_message(self.conversation, "assistant", "للأسف العطر ده مش موجود عندنا. كبدائل متوفرة:\n1 Million: فيه ريحة توابل.",
                     internal_context="ABSENCE_DENIED\n" + format_products([rows["1 Million"]]))
        searched = {}

        def recommend(message, products, history, **kwargs):
            searched["names"] = [product.name for product in products]
            return "ترشيح", format_products(list(products)[:2])

        def turn(message, intent):
            history = [{"role": row.role, "content": row.content} for row in self.conversation.messages.order_by("id")]
            with patch("products.services.router.classify", return_value="recommendation"), \
                 patch("products.services.router.extract_intent", return_value=dict(intent)), \
                 patch("products.services.router.recommend", side_effect=recommend):
                reply, context = route(message, history, self.store, self.conversation)
            save_message(self.conversation, "user", message)
            save_message(self.conversation, "assistant", reply, internal_context=context or "")
            self.conversation.refresh_from_db()
            return reply

        first = turn("يعني مفيش؟ 🙄 طب عندك حاجة شبهه؟", {"similar_to": "Club de Nuit Intense"})
        self.assertIn("معنديش بيانات عن ريحته", first)
        self.assertNotEqual((self.conversation.preferences or {}).get("similar_to"), "1 Million")
        turn("رجالي، بحب الريحة اللي فيها سموكي وفاكهة",
             {"scent_open": False, "similar_to": "Club de Nuit Intense", "gender": "male",
              "scent_evidence": [{"text": "سموكي", "tags": ["smoky"], "polarity": "positive"},
                                 {"text": "فاكهة", "tags": ["fruity"], "polarity": "positive"}]})
        self.assertEqual(sorted(searched.get("names", [])), ["Aventus", "Cigar"])

    def _denied(self):
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        rows = {name: self.product(name, tags) for name, tags in (
            ("Aventus", ["woody", "fruity", "smoky"]), ("Cigar", ["woody", "smoky", "fruity"]),
            ("1 Million", ["spicy", "leather", "sweet"]), ("La Nuit", ["spicy", "sweet"]))}
        save_message(self.conversation, "user", "عندكو Club de Nuit Intense؟")
        save_message(self.conversation, "assistant", "للأسف العطر ده مش موجود عندنا. كبدائل متوفرة:\n1 Million: فيه ريحة توابل.",
                     internal_context="ABSENCE_DENIED\n" + format_products([rows["1 Million"]]))
        return rows

    def test_the_missing_perfumes_described_scent_is_the_wish(self):
        """S09 round 2 batch 4 attempt 3, t3: "اللي عاجبني في Club de Nuit ريحته فيها سموكي وفاكهة" got the
        scent question, and the customer had to say it again."""
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        from products.services.router import route
        from products.services.sales import gender as sales_gender
        self._denied()
        save_message(self.conversation, "user", "طب عندك حاجة شبهه؟")
        save_message(self.conversation, "assistant", "أيوه للأسف مش عندنا. " + sales_gender.QUESTION,
                     internal_context=sales_gender.REQUIRED_MARKER)
        searched = {}

        def recommend(message, products, history, **kwargs):
            searched["names"] = [product.name for product in products]
            return "ترشيح", format_products(list(products)[:2])

        message = "رجالي، اللي عاجبني في Club de Nuit ريحته فيها سموكي وفاكهة"
        history = [{"role": row.role, "content": row.content} for row in self.conversation.messages.order_by("id")]
        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent",
                   return_value={"gender": "male", "scent_open": False, "similar_to": "Club de Nuit Intense"}), \
             patch("products.services.router.recommend", side_effect=recommend):
            reply, _ = route(message, history, self.store, self.conversation)
        self.assertEqual(sorted(searched.get("names", [])), ["Aventus", "Cigar"], reply)
        self.conversation.refresh_from_db()
        self.assertEqual(sorted(self.conversation.preferences.get("scent_tags") or []), ["fruity", "smoky"])

    def test_which_is_closer_to_a_missing_perfume_is_answered_honestly(self):
        """S09 round 2 batch 4 attempt 3, t5: "أنهي واحد فيهم أقرب لـ Club de Nuit؟ وسعره كام؟" got two price
        blocks, read as picking the first."""
        from products.services.router import _closer_to_a_missing_perfume
        from products.services.static_faq_service import normalize_arabic
        question = "طب أنهي واحد فيهم أقرب لـ Club de Nuit؟ وسعره كام؟"
        # Before any denial nothing is assumed missing.
        self.product("Sauvage", ["fresh"])
        self.assertEqual(_closer_to_a_missing_perfume(question, normalize_arabic(question), self.conversation, self.store), "")
        self._denied()
        self.assertEqual(_closer_to_a_missing_perfume(question, normalize_arabic(question), self.conversation, self.store),
                         "معنديش بيانات عن ريحة Club de Nuit أقارن بيها، فمقدرش أقول أنهي أقرب له.")
        stocked = "طب أنهي أقرب لـ Aventus؟"
        self.assertEqual(_closer_to_a_missing_perfume(stocked, normalize_arabic(stocked), self.conversation, self.store), "")
        price = "طب Club de Nuit كان بكام؟"
        self.assertEqual(_closer_to_a_missing_perfume(price, normalize_arabic(price), self.conversation, self.store), "")


class SecondRoundBatchFiveTests(TestCase):
    """Second round, batch 5: the three failures of round-2 full run 6 (17/20)."""

    def setUp(self):
        from products.models import Conversation
        self.store = Store.objects.create(name="Batch Five Store")
        self.brand = Brand.objects.create(store=self.store, name="House")
        self.conversation = Conversation.objects.create(store=self.store)

    def _s11(self):
        from products.models import StaticFAQ
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        StaticFAQ.objects.create(store=self.store, question="بكام مصاريف الشحن؟", answer="مصاريف الشحن 60 جنيه لجميع محافظات مصر",
                                 keywords="شحن+بكام, شحن+مصاريف, شحن", priority=3)
        product = Product.objects.create(store=self.store, brand=self.brand, name="212 NYC Man", gender="male",
                                         scent_tags=["fresh"])
        for volume, price in ((50, "750"), (90, "1000")):
            ProductVariant.objects.create(product=product, volume=volume, price=Decimal(price), bottle_type="normal")
        self.conversation.preferences = {"gender": "male", "max_price": 700, "budget_strict": False}
        self.conversation.save(update_fields=["preferences"])
        save_message(self.conversation, "user", "عايز رجالي للشغل في حدود 700 جنيه")
        save_message(self.conversation, "assistant", "212 NYC Man\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.",
                     internal_context=format_products([product]))
        return product

    def _objection_turn(self, message, product, model_reply):
        """The whole routed turn, so the claim and price checks of `_rephrased` run on the reply."""
        from products.services.router import route
        history = [{"role": row.role, "content": row.content} for row in self.conversation.messages.order_by("id")]
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.objection_service.chat", return_value=model_reply), \
             patch("products.services.objection_service.resolve_products", return_value=[product]):
            reply, _ = route(message, history, self.store, self.conversation)
        return reply

    def test_nothing_in_budget_is_said_precisely_and_survives_the_price_check(self):
        """S11 round 2 full 6 t2: "مفيش عندنا اختيار أقل من ميزانيتك" leaves "at exactly 700" open (criterion
        unmet). A first fix wrote the amounts into the sentence; the price checker rejected it and the
        customer got the bare price block (S11 round 2 batch 5, t2). This test runs the routed turn."""
        from products.services.objection_service import NOTHING_IN_BUDGET
        product = self._s11()
        reply = self._objection_turn(
            "غالي عليا شوية، الـ50 بكام بالظبط؟", product,
            "فعلاً عندك حق، السعر أعلى من ميزانيتك، ومفيش عندنا اختيار أقل من ميزانيتك حاليًا.")
        self.assertIn(NOTHING_IN_BUDGET + "، وده أقل سعر عندنا.", reply)
        self.assertIn("50 ملي بـ750 جنيه", reply)
        self.assertNotIn("اختيار أقل من ميزانيتك", reply)

    def test_an_objection_to_the_total_with_shipping_gets_the_shipping_fact(self):
        """S11 round 2 full 6 t5: "الشحن 60؟ كده الإجمالي 810، غالي عليا شوية" got only "فاهم إن مصاريف
        الشحن زوّدت الإجمالي عليك" (criterion unmet)."""
        from products.services.objection_service import NOTHING_IN_BUDGET
        product = self._s11()
        reply = self._objection_turn("الشحن 60؟ كده الإجمالي 810، غالي عليا شوية.", product,
                                     "فاهم إن مصاريف الشحن زوّدت الإجمالي عليك.")
        self.assertIn(NOTHING_IN_BUDGET, reply)
        self.assertIn("مصاريف الشحن 60 جنيه لجميع محافظات مصر، ومفيش عندي شحن أقل من كده.", reply)
        self.assertNotIn("زوّدت الإجمالي", reply)

    def test_cheaper_when_the_quote_is_the_floor_survives_the_price_check(self):
        """S11 round 2 full 2 t4 again: the "مفيش حاجة أرخص من 750 جنيه" line carried an amount, so the
        routed turn dropped it; it now says it without one."""
        from products.services.objection_service import NOTHING_CHEAPER, _nothing_in_budget_reply
        product = self._s11()
        reply = self._objection_turn("يعني الـ50 بـ750؟ غالي عليا بصراحة، مفيش حاجة أرخص؟", product, "")
        self.assertIn(NOTHING_CHEAPER + "؛ ده أقل سعر عندنا.", reply)
        self.assertIn("50 ملي بـ750 جنيه", reply)
        # Said once already: not the same sentence again, and other model sentences are kept.
        first = _nothing_in_budget_reply("غالي عليا", "", self.store, [], at_floor=True)
        again = _nothing_in_budget_reply("برضه غالي عليا", "تقدر تجربه في الستور قبل ما تقرر.", self.store,
                                         [{"role": "assistant", "content": first}], at_floor=True)
        self.assertTrue(again.startswith("زي ما قلتلك، ده أقل سعر عندنا"), again)
        self.assertIn("تقدر تجربه في الستور", again)
        # Not the cheapest perfume we sell: "this is our lowest price" is not claimed.
        self.assertNotIn("أقل سعر", _nothing_in_budget_reply("غالي عليا", "", self.store, [], at_floor=False))

    def test_giving_up_after_a_transfer_offer_is_not_consent(self):
        """S20 round 2 full 6 t5: "ماشي خلاص، مش هطلب حاجة. سيبك" was read as yes to "تحب أحولك لمندوب؟" and
        the conversation was handed to staff."""
        from products.services import promotion_service as promotions
        from products.services.handoff import handoff_offer_reply, walks_away
        from products.services.router import route
        offer = promotions.NOTHING_TO_CONFIRM + "\n\n" + promotions.LIMITATION + "\n\n" + promotions.INVITATION
        history = [{"role": "user", "content": "أكدلي الأوردر"}, {"role": "assistant", "content": offer}]
        message = "ماشي خلاص، مش هطلب حاجة. سيبك"
        self.assertIs(handoff_offer_reply(message, history), False)
        self.assertTrue(walks_away(message))
        with patch("products.services.router._handoff") as handoff, \
             patch("products.services.router.classify") as classify:
            reply, _ = route(message, history, self.store, self.conversation)
        handoff.assert_not_called()
        classify.assert_not_called()
        self.assertEqual(reply, "تمام يا فندم، ولو احتجت أي حاجة أنا موجود.")
        # A plain yes, and a yes with a question for the team, are still consent.
        self.assertIs(handoff_offer_reply("ماشي", history), True)
        self.assertIs(handoff_offer_reply("ماشي حولني وخليهم يكلموني بكرة", history), True)
        self.assertFalse(walks_away("مش عايز باندل، عايز 3 برفانات وخصم 20%، انت بوت غبي ولا إيه؟"))

    def test_skin_or_clothes_is_answered_as_not_recorded(self):
        """S16 round 2 full 6 t4-t5: "الثبات ده على الجلد ولا الهدوم؟" got "اكتبلي اسم العطر تاني" and then
        "ده كل اللي متسجل عندي عن ده"."""
        from products.services.router import (SKIN_OR_CLOTHES_AGAIN, SKIN_OR_CLOTHES_ANSWER, _skin_or_clothes_answer,
                                              route)
        from products.services.handoff import has_handoff_offer
        from products.services.static_faq_service import normalize_arabic
        first = "أكيد هتقولي 48 ساعة زي الكل 🙄 الثبات ده على الجلد ولا الهدوم؟"
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.router.get_product_info") as info:
            reply, _ = route(first, [{"role": "assistant", "content": "كل عطورنا ثباتها أكتر من 8 ساعات."}],
                             self.store, self.conversation)
        info.assert_not_called()
        self.assertEqual(reply, SKIN_OR_CLOTHES_ANSWER)
        self.assertNotIn("48", reply)
        history = [{"role": "user", "content": first}, {"role": "assistant", "content": SKIN_OR_CLOTHES_ANSWER}]
        again = _skin_or_clothes_answer(normalize_arabic("بسألك الثبات اللي قلت أكتر من 8 ساعات، ده على الجلد ولا الهدوم؟"),
                                        history)
        self.assertEqual(again, SKIN_OR_CLOTHES_AGAIN)
        self.assertTrue(has_handoff_offer([{"role": "assistant", "content": again}]))
        # Not a longevity question: nothing is assumed.
        self.assertEqual(_skin_or_clothes_answer(normalize_arabic("ينفع أرشه على الهدوم؟"), []), "")

    def test_retype_the_name_is_dropped_when_no_name_was_given(self):
        """S16 round 2 full 5 t3 and full 6 t4: "اكتبلي اسمه تاني" to a customer who had named no perfume."""
        from products.services.router import _without_retype_request
        message = "وبيفضل ثابت قد إيه بجد؟ متقوليش كلام بياعين"
        reply = "التطابق بيوصل لحوالي 95%، مش 100%. وثبات عطورنا أكتر من 8 ساعات، بس عشان أجاوبك عن عطر بعينه اكتبلي اسمه تاني يا فندم."
        self.assertEqual(_without_retype_request(reply, message, ""),
                         "التطابق بيوصل لحوالي 95%، مش 100%. وثبات عطورنا أكتر من 8 ساعات.")
        whole = "الثبات عندنا ممتاز. اكتبلي اسم العطر تاني أو بطريقة تانية عشان أتأكد من المقصود."
        self.assertEqual(_without_retype_request(whole, message, ""), "الثبات عندنا ممتاز.")
        # A name the bot could not read keeps the request.
        self.assertEqual(_without_retype_request(whole, "عندكو Zxqv؟", ""), whole)
        self.assertEqual(_without_retype_request(whole, message, "NAME_UNREADABLE"), whole)

    def test_perfume_talk_around_a_latin_name_does_not_unplace_it(self):
        """S02 round 2 full 6 t3: "طب والـ Layton top/middle/base بتاعته إيه؟" lost Layton to the Latin-name
        disagreement rule (a round-2 change of mine) and the customer was offered the team for stored notes."""
        from products.services.product_info import _named_in_message
        for name in ("Aventus", "Layton", "La Nuit"):
            product = Product.objects.create(store=self.store, brand=self.brand, name=name, gender="male")
            ProductVariant.objects.create(product=product, volume=50, price=Decimal("750"), bottle_type="normal")

        def named(message):
            return [product.name for product in _named_in_message(message, self.store)]

        self.assertEqual(sorted(named("تمام، دي النوتات المعروفة للـ Aventus. طب والـ Layton top/middle/base بتاعته إيه؟")),
                         ["Aventus", "Layton"])
        self.assertEqual(named("Layton notes إيه؟"), ["Layton"])
        self.assertEqual(named("طب Layton base notes فيها إيه؟"), ["Layton"])
        # The rule it must not weaken: a different perfume that shares one word is still not placed.
        self.assertEqual(named("عندكو Club de Nuit Intense؟"), [])

    def test_an_exchange_question_beside_a_thinking_remark_gets_the_store_policy(self):
        """S07 round 2 full 6 t5: "هفكر أطلبه إن شاء الله. بس لو مجاش على ذوقها ينفع أبدله؟" went to the
        objection path, whose paraphrase left out the fees and the free in-store exchange."""
        from products.models import StaticFAQ
        from products.services.router import route
        policy = ("ضمان الاستبدال لمدة يومين من وقت الاستلام.\n"
                  " في حالة الاستبدال: يتم دفع شحن مرتين + 150ج للبوكس الجديد، أو مجانًا داخل الستور.")
        StaticFAQ.objects.create(store=self.store, question="في استرجاع أو استبدال؟", priority=10, answer=policy,
                                 keywords="استرجاع, استبدال")
        message = "تمام أميل لـ Fleur Musc، هفكر أطلبه إن شاء الله. بس لو مجاش على ذوقها ينفع أبدله؟"
        with patch("products.services.router.classify", return_value="product_info"), \
             patch("products.services.router.handle_objection") as objection, \
             patch("products.services.router.get_product_info") as info:
            reply, _ = route(message, [], self.store, self.conversation)
        objection.assert_not_called()
        info.assert_not_called()
        self.assertEqual(reply, policy)

    def test_a_cheaper_question_at_the_lowest_price_gets_the_direct_answer(self):
        """S11 round 2 batch 5 t4-t8: with 750 the lowest price, cheaper questions got a similarity question
        (twice), the same 750 perfumes, and "تقصد أنهي عطر وحجم؟"."""
        from products.services.router import _nothing_cheaper_exists, route
        from products.services.static_faq_service import normalize_arabic
        product = self._s11()
        history = [{"role": row.role, "content": row.content} for row in self.conversation.messages.order_by("id")]
        line = "مفيش حاجة أرخص من 750 جنيه عندنا حاليًا؛ ده أقل سعر عندنا."
        for message in ("طب في بديل أرخص شوية ويكون فريش ومناسب للشغل؟",
                        "مش عارف اسم معين، رشحلي أي حاجة رجالي فريش للشغل وأقل من 750",
                        "دول برضه بـ750؟ مفيش حاجة أرخص؟"[len("دول برضه بـ750؟ "):],
                        "بس ده لسه بـ750، مفيش حاجة أرخص منه؟"):
            with self.subTest(message=message):
                self.assertEqual(_nothing_cheaper_exists(message, normalize_arabic(message), history,
                                                         self.conversation, self.store), line)
        # Through the router, whatever the classifier says, and with no model call.
        for label in ("recommendation", "product_info"):
            with self.subTest(route=label), patch("products.services.router.classify", return_value=label), \
                 patch("products.services.router.extract_intent") as extract, \
                 patch("products.services.router.get_product_info") as info:
                reply, _ = route("بس ده لسه بـ750، مفيش حاجة أرخص منه؟", history, self.store, self.conversation)
                extract.assert_not_called()
                info.assert_not_called()
                self.assertEqual(reply, line)
        # Said already: not the same sentence again.
        again = _nothing_cheaper_exists("مفيش أرخص خالص؟", normalize_arabic("مفيش أرخص خالص؟"),
                                        history + [{"role": "assistant", "content": line}], self.conversation, self.store)
        self.assertTrue(again.startswith("زي ما قلتلك، 750 جنيه هو أقل سعر عندنا"), again)
        # Something cheaper than the price asked about exists: the normal flow answers.
        cheap = Product.objects.create(store=self.store, brand=self.brand, name="Budget Fresh", gender="male")
        ProductVariant.objects.create(product=cheap, volume=50, price=Decimal("500"), bottle_type="normal")
        self.assertEqual(_nothing_cheaper_exists("مفيش حاجة أرخص؟", normalize_arabic("مفيش حاجة أرخص؟"), history,
                                                 self.conversation, self.store), "")
        self.assertEqual(product.variants.count(), 2)

    def test_a_cheaper_alternative_is_not_a_likeness_request(self):
        """S11 round 2 batch 5 t4-t5: "في بديل أرخص شوية؟" and "مش فارق شبه أنهي عطر" both got "تقصد عطر شبه
        أنهي عطر أو إصدار؟"."""
        from products.services.sales.reference import similarity_requested
        self.assertFalse(similarity_requested("طب في بديل أرخص شوية ويكون فريش ومناسب للشغل؟"))
        self.assertFalse(similarity_requested("مش فارق شبه أنهي عطر، عايز حاجة رجالي فريش"))
        self.assertTrue(similarity_requested("عايز بديل لـ Sauvage"))
        self.assertTrue(similarity_requested("عايز أي حاجة شبه Sauvage"))

    def test_the_reference_question_is_not_asked_twice_in_a_row(self):
        """S11 round 2 batch 5 t5: the same "تقصد عطر شبه أنهي عطر أو إصدار؟" on consecutive turns."""
        from products.services.product_formatting import format_products
        from products.services.router import route
        product = self._s11()
        question = "تقصد عطر شبه أنهي عطر أو إصدار؟"
        history = [{"role": "user", "content": "عايز حاجة شبهه"}, {"role": "assistant", "content": question}]
        searched = {}

        def recommend(message, products, history, **kwargs):
            searched["names"] = [row.name for row in products]
            return "ترشيح", format_products(list(products)[:1])

        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent",
                   return_value={"gender": "male", "scent_tags": ["fresh"], "similarity_needs_reference": True}), \
             patch("products.services.router.merge_preferences",
                   return_value={"gender": "male", "scent_tags": ["fresh"], "scent_open": False,
                                 "similarity_needs_reference": True}), \
             patch("products.services.router.recommend", side_effect=recommend):
            reply, _ = route("عايز حاجة فريش وخلاص", history, self.store, self.conversation)
        self.assertNotEqual(reply.strip(), question)
        self.assertEqual(searched.get("names"), [product.name])


class ThirdRoundTests(TestCase):
    """Owner decisions of 2026-11-12 (third brief): confirmation after a side question, store figures,
    the two remaining medium issues, and the hard budget rules."""

    SHIPPING_FACT = "الشحن بيكون داخل مصر فقط في خلال من يومين ل 5 ايام عمل ب 60 جنيه لاي مكان في مصر"

    def setUp(self):
        from products.models import Cart, Conversation, StoreSettings
        self.store = Store.objects.create(name="Third Round Store")
        # Shipping lives in the business facts, with no FAQ entry: the Perfamix layout since 2026-11-12.
        StoreSettings.objects.create(store=self.store, business_facts=(
            "- نسبة التطابق مع العطور الأصلية توصل لحوالي 95%\n"
            "- الاسعار عندنا بتبدأ من اول 750 لحد 2000\n\n"
            "-" + self.SHIPPING_FACT + "\n"
            "ولو العميل عايز شحن فنفس اليوم متاح بنبعت الاوردر في اوبر بس العميل بيدفع تمن الاوردر كامل قبل الشحن"))
        brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(store=self.store, brand=brand, name="Sauvage", gender="male",
                                              scent_tags=["fresh"])
        self.small = ProductVariant.objects.create(product=self.product, volume=50, price=Decimal("750.00"),
                                                   bottle_type="normal")
        self.large = ProductVariant.objects.create(product=self.product, volume=90, price=Decimal("1000.00"),
                                                   bottle_type="normal")
        self.conversation = Conversation.objects.create(store=self.store)
        self.cart = Cart.objects.create(conversation=self.conversation, customer_name="كريم سامي",
                                        customer_phone="01011223344", secondary_phone="01099887766",
                                        shipping_address="12 شارع مصدق، الدقي، الجيزة")

    def checkout(self, message, **updates):
        """One order turn with the extractor stubbed; the reply is saved as it would be delivered."""
        from products.services.conversation_service import save_message
        from products.services.order_service import handle_order
        data = {"products": [{"name": "Sauvage", "volume": 90, "quantity": 1, "bottle_type": "normal"}],
                "customer_name": "كريم سامي", "customer_phone": "01011223344",
                "customer_secondary_phone": "01099887766", "shipping_address": "12 شارع مصدق، الدقي، الجيزة",
                "is_confirmed": False}
        data.update(updates)
        save_message(self.conversation, "user", message)
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            reply, context = handle_order(message, [], self.store, self.conversation)
        save_message(self.conversation, "assistant", reply, internal_context=context)
        return reply

    # --- decision 1: confirmation -------------------------------------------------------------

    def test_confirmation_after_a_side_question_places_the_order(self):
        """S11 round 2 batch 5 attempt 2, t6-t8: summary, "الشحن بكام؟", then "تمام أكد" resent the summary.
        The extractor still says not confirmed here; the summary for this exact cart was sent."""
        from products.models import Order
        from products.services.conversation_service import save_message
        summary = self.checkout("ابعت الطلب")
        self.assertIn("راجع معايا تفاصيل الطلب", summary)
        self.assertFalse(Order.objects.exists())
        save_message(self.conversation, "user", "الشحن بكام؟")
        save_message(self.conversation, "assistant", "الشحن بـ60 جنيه، والإجمالي مع الطلب 1060 جنيه.")
        confirmed = self.checkout("تمام أكد")
        order = Order.objects.get(conversation=self.conversation)
        self.assertIn(f"#{order.pk}", confirmed)
        self.assertEqual([(item.variant_id, item.quantity) for item in order.items.all()], [(self.large.pk, 1)])

    def test_confirmation_after_a_cart_change_needs_a_new_summary(self):
        """Safeguard 1: the summary covered the 90 ml bottle; a changed cart is not confirmed by "أكد"."""
        from products.models import Order
        self.checkout("ابعت الطلب")
        changed = self.checkout("خليه 50 ملي وأكد",
                                products=[{"name": "Sauvage", "volume": 50, "quantity": 1, "bottle_type": "normal"}])
        self.assertFalse(Order.objects.exists())
        self.assertIn("راجع معايا تفاصيل الطلب", changed)
        self.assertIn("(50ml)", changed)
        # A changed delivery detail counts as a changed cart too.
        moved = self.checkout("غير العنوان وأكد",
                              products=[{"name": "Sauvage", "volume": 50, "quantity": 1, "bottle_type": "normal"}],
                              shipping_address="5 شارع التحرير، القاهرة")
        self.assertFalse(Order.objects.exists())
        self.assertIn("5 شارع التحرير", moved)
        # The new summary, once sent, is what "تمام أكد" confirms.
        self.checkout("تمام أكد", products=[{"name": "Sauvage", "volume": 50, "quantity": 1, "bottle_type": "normal"}],
                      shipping_address="5 شارع التحرير، القاهرة")
        order = Order.objects.get(conversation=self.conversation)
        self.assertEqual([(item.variant_id, item.quantity) for item in order.items.all()], [(self.small.pk, 1)])
        self.assertIn("5 شارع التحرير", order.shipping_address)

    def test_confirmation_with_no_summary_sent_shows_the_summary_first(self):
        """Safeguard 2: nothing confirms without a summary that was actually sent."""
        from products.models import Cart, Order
        from products.services.order_service import handle_order
        first = self.checkout("تمام أكد")
        self.assertFalse(Order.objects.exists())
        self.assertIn("راجع معايا تفاصيل الطلب", first)
        # A summary that was composed but never delivered does not count either.
        other = type(self.conversation).objects.create(store=self.store)
        Cart.objects.create(conversation=other, customer_name="كريم سامي", customer_phone="01011223344",
                            secondary_phone="01099887766", shipping_address="12 شارع مصدق، الدقي، الجيزة")
        data = {"products": [{"name": "Sauvage", "volume": 90, "quantity": 1, "bottle_type": "normal"}],
                "customer_name": "كريم سامي", "customer_phone": "01011223344",
                "customer_secondary_phone": "01099887766", "shipping_address": "12 شارع مصدق، الدقي، الجيزة",
                "is_confirmed": False}
        for message in ("ابعت الطلب", "تمام أكد"):
            with patch("products.services.order_service.chat", return_value=json.dumps(data)):
                reply, _ = handle_order(message, [], self.store, other)   # replies never saved as sent
        self.assertFalse(Order.objects.filter(conversation=other).exists())
        self.assertIn("راجع معايا تفاصيل الطلب", reply)

    def test_only_the_confirm_verb_counts_as_an_explicit_confirmation(self):
        from products.models import Order
        from products.services.conversation_service import save_message
        from products.services.order_service import explicit_confirmation
        for message in ("تمام أكد", "أكد الطلب", "اكدلي", "أيوه نأكد", "confirm"):
            with self.subTest(message=message):
                self.assertTrue(explicit_confirmation(message))
        for message in ("تمام", "أكيد", "مش متأكد", "اتأكد من السعر الأول", "مش هأكد دلوقتي", "قبل ما أكد عايز أسأل",
                        "أأكد إزاي؟", "أكد بعدين", "لما أكد هقولك"):
            with self.subTest(message=message):
                self.assertFalse(explicit_confirmation(message))
        # A bare "تمام" after a side question stays the extractor's call: no order from this rule.
        self.checkout("ابعت الطلب")
        save_message(self.conversation, "user", "الشحن بكام؟")
        save_message(self.conversation, "assistant", "الشحن بـ60 جنيه.")
        self.checkout("تمام")
        self.assertFalse(Order.objects.exists())

    # --- decision 2: store figures and where they live ------------------------------------------

    def test_no_similarity_figure_is_hardcoded_and_the_store_figure_is_the_only_one(self):
        """Owner decision: 95% lives in the business facts; no "90%" may reach the customer from code."""
        bare = Store.objects.create(name="No Facts Store")
        from products.models import StoreSettings
        StoreSettings.objects.create(store=bare)
        prompt = get_system_prompt(bare)
        self.assertNotIn("90%", prompt)
        self.assertNotIn("95%", prompt)
        configured = get_system_prompt(self.store)
        self.assertIn("95%", configured)
        self.assertNotIn("90%", configured)
        self.assertIn("750 لحد 2000", configured)

    def test_shipping_is_read_from_the_business_facts_when_no_faq_answers_it(self):
        """The owner moved shipping out of the FAQ: the summary lost its shipping line and its total."""
        from products.models import StaticFAQ
        from products.services.order_service import (_quote_summary, flat_shipping_fee, shipping_answer,
                                                     shipping_line)
        self.assertEqual(shipping_answer(self.store), self.SHIPPING_FACT)
        self.assertEqual(flat_shipping_fee(self.store), Decimal("60"))
        self.assertEqual(shipping_line(self.store, Decimal("750")),
                         "وفيه مصاريف شحن 60 جنيه، فالإجمالي مع الشحن 810 جنيه.")
        items = [{"variant": self.small, "quantity": 1, "price": self.small.price, "bottle_type": "normal"}]
        summary = _quote_summary(self.conversation, self.cart, items)
        self.assertIn("750 جنيه (من غير الشحن)", summary)
        self.assertIn("🚚 " + self.SHIPPING_FACT, summary)
        self.assertIn("الإجمالي مع الشحن: 810 جنيه.", summary)
        self.assertNotIn("اوبر", summary)
        # An active FAQ entry still wins.
        StaticFAQ.objects.create(store=self.store, question="بكام مصاريف الشحن؟", answer="مصاريف الشحن 60 جنيه لجميع محافظات مصر",
                                 keywords="شحن+بكام, شحن+مصاريف, شحن", priority=3)
        self.assertEqual(shipping_answer(self.store), "مصاريف الشحن 60 جنيه لجميع محافظات مصر")

    def test_shipping_questions_get_the_owners_sentence_when_no_faq_answers(self):
        """"الشحن بياخد قد إيه؟" and "الشحن بكام؟" had FAQ entries; with those off they get the facts line."""
        from products.services.router import _faq_lookup, _policy_side_questions
        answer = self.SHIPPING_FACT + "."
        for question in ("الشحن بياخد قد إيه؟", "الشحن بكام؟", "مصاريف التوصيل كام؟"):
            with self.subTest(question=question):
                self.assertEqual((_faq_lookup(question, self.store) or {}).get("answer"), answer)
        self.assertIsNone(_faq_lookup("ريحته عاملة إزاي؟", self.store))
        answers, rest, asks = _policy_side_questions("تمام، والشحن بياخد قد إيه؟ طب رشحلي كمان حاجة سويت", self.store)
        self.assertEqual((answers, asks), ([answer], True))
        self.assertIn("رشحلي", rest)

    # --- decision 3: the two medium issues -----------------------------------------------------

    def test_the_near_budget_sentence_is_said_once_per_price(self):
        """S11 round 2 full 5/6: "أعلى حاجة بسيطة من ميزانيتك" on four consecutive replies, twice in one."""
        from products.services.router import NEAR_BUDGET, _near_budget_once
        first = "212 NYC Man\nريحته فريش.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه. " + NEAR_BUDGET + "."
        self.assertEqual(_near_budget_once(first, "عايز رجالي في حدود 700", []), first)
        history = [{"role": "user", "content": "عايز رجالي في حدود 700"}, {"role": "assistant", "content": first}]
        again = "212 NYC Man\nأيوه يا فندم، ده سعر الـ50 ملي، و" + NEAR_BUDGET + ".\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه. " + NEAR_BUDGET + "."
        self.assertEqual(_near_budget_once(again, "طب الـ50 بكام بالظبط؟", history),
                         "212 NYC Man\nأيوه يا فندم، ده سعر الـ50 ملي.\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه.")
        # Twice in one first-time reply: the price keeps it, the prose copy goes.
        doubled = "212 NYC Man\nالسعر " + NEAR_BUDGET + ".\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه. " + NEAR_BUDGET + "."
        self.assertEqual(_near_budget_once(doubled, "عايز رجالي", []),
                         "212 NYC Man\nزجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه. " + NEAR_BUDGET + ".")
        # Asked about the budget, the sentence is the answer and stays; a new price keeps its own.
        self.assertEqual(_near_budget_once(first, "هو في حدود ميزانيتي؟", history), first)
        other = "Eternity\nزجاجة الستور الخاصة بينا 50 ملي بـ800 جنيه. " + NEAR_BUDGET + "."
        self.assertEqual(_near_budget_once(other, "والتاني بكام؟", history), other)
        grouped = "زجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه (" + NEAR_BUDGET + ")، و90 ملي بـ1000 جنيه."
        self.assertEqual(_near_budget_once(grouped, "والأحجام؟", history),
                         "زجاجة الستور الخاصة بينا 50 ملي بـ750 جنيه، و90 ملي بـ1000 جنيه.")

    def test_a_difference_in_feel_between_perfumes_is_an_unsupported_claim(self):
        """S09 round 2 batch 4 attempt 3, t6: "…بس ترتيب الإحساس مختلف شوية" between two stocked perfumes."""
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Cigar": {"scent_tags": ["woody", "smoky", "fruity"], "notes": ["", "", ""], "season": "", "occasion": ""}}
        said = "فعلاً عندك حق، السيجار قريب من أڤنتوس في الطابع الخشبي والسموكي والفاكهي، بس ترتيب الإحساس مختلف شوية."
        self.assertTrue([issue for issue in product_claim_issues(said, facts) if issue.startswith("difference:")])
        self.assertTrue([issue for issue in product_claim_issues("الاتنين مختلفين في الإحساس.", {})
                         if issue.startswith("difference:")])
        for honest in ("السيجار وأڤنتوس الاتنين مسجلين خشبي وسموكي وفاكهي.",
                       "مقدرش أقول الإحساس مختلف ولا لأ من البيانات اللي عندي."):
            with self.subTest(reply=honest):
                self.assertEqual([issue for issue in product_claim_issues(honest, facts) if issue.startswith("difference:")], [])

    # --- decision 5: budget rules --------------------------------------------------------------

    def test_the_meter_stops_at_the_first_429(self):
        """Owner rule: stop immediately if the provider returns 429. The spend-limit incident sent
        twelve rejected calls, each charged at its maximum."""
        import httpx
        import openai
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest import mock
        from scripts.evaluate_agent_budgeted import BudgetStop, Meter
        with TemporaryDirectory(prefix="perfume_meter_429_") as directory:
            meter = Meter(Decimal("0.50"), Path(directory))
            rejected = openai.RateLimitError(
                "spend limit", response=httpx.Response(429, request=httpx.Request("POST", "http://127.0.0.1:9/v1")),
                body=None)
            provider = mock.Mock(side_effect=rejected)
            request = {"model": "gpt-6-luna", "reasoning_effort": "none",
                       "messages": [{"role": "user", "content": "x"}]}
            with self.assertRaises(openai.RateLimitError):
                meter.wrap(provider)(**request)
            with self.assertRaises(BudgetStop):
                meter.wrap(provider)(**request)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(len(meter.calls), 1)

    def test_the_task_cap_is_the_owners_three_fifty(self):
        import importlib.util
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent / "scripts" / "simulate_customers.py").read_text(encoding="utf-8")
        self.assertIn('TASK_CAP = Decimal("3.50")', source)

    # --- found in the replay of this round ------------------------------------------------------

    def _table(self):
        """Aventus (1400) on the table; Cigar (750/1000) shares its labels and fits a 1000 budget."""
        from products.services.conversation_service import save_message
        from products.services.product_formatting import format_products
        brand = Brand.objects.create(store=self.store, name="House")
        rows = {}
        for name, tags, prices in (("Aventus", ["woody", "fruity", "smoky"], ("1400", "2000")),
                                   ("Cigar", ["woody", "smoky", "fruity"], ("750", "1000"))):
            rows[name] = Product.objects.create(store=self.store, brand=brand, name=name, gender="male", scent_tags=tags)
            for volume, price in zip((50, 90), prices):
                ProductVariant.objects.create(product=rows[name], volume=volume, price=Decimal(price), bottle_type="normal")
        save_message(self.conversation, "user", "سعر Aventus كام؟")
        save_message(self.conversation, "assistant", "Aventus\nزجاجة الستور الخاصة بينا 90 ملي بـ2000 جنيه، و50 ملي بـ1400 جنيه.",
                     internal_context=format_products([rows["Aventus"]]))
        return rows

    def test_a_price_objection_that_asks_for_an_alternative_is_recommended_on(self):
        """S09 round 3 replay t5: "غالي أوي بصراحة، مفيش حاجة شبهه في حدود ١٠٠٠؟" got the 1400 perfume
        described again, with the budget and the request already understood."""
        from products.services.product_formatting import format_products
        from products.services.router import route
        self._table()
        self.conversation.preferences = {"gender": "male"}      # said earlier in the conversation
        self.conversation.save(update_fields=["preferences"])
        history = [{"role": row.role, "content": row.content} for row in self.conversation.messages.order_by("id")]
        searched = {}

        def recommend(message, products, history, **kwargs):
            searched["names"] = [product.name for product in products]
            return "ترشيح", format_products(list(products)[:1])

        with patch("products.services.router.classify", return_value="recommendation"), \
             patch("products.services.router.extract_intent",
                   return_value={"gender": "male", "max_price": 1000, "scent_tags": ["smoky", "fruity"]}), \
             patch("products.services.router.handle_objection") as objection, \
             patch("products.services.router.recommend", side_effect=recommend):
            route("غالي أوي بصراحة، مفيش حاجة شبهه في حدود ١٠٠٠؟", history, self.store, self.conversation)
        objection.assert_not_called()
        self.assertIn("Cigar", searched.get("names", []))
        self.assertNotIn("Aventus", searched.get("names", []))

    def test_a_price_objection_with_nothing_in_budget_keeps_its_own_answer(self):
        """The S11 case must not be rerouted: with nothing at or under the budget, the objection path's
        "nothing in your budget; this is the lowest price" is the answer."""
        from products.services.router import _ASKS_FOR_AN_ALTERNATIVE, _something_fits_the_budget
        from products.services.static_faq_service import normalize_arabic
        self._table()
        self.conversation.preferences = {"gender": "male"}
        self.assertTrue(_ASKS_FOR_AN_ALTERNATIVE.search(normalize_arabic("غالي عليا، أنا قلت في حدود 700")))
        self.assertFalse(_ASKS_FOR_AN_ALTERNATIVE.search(normalize_arabic("غالي أوي بصراحة")))
        self.assertFalse(_something_fits_the_budget(700, self.conversation, self.store))    # floor is 750
        self.assertTrue(_something_fits_the_budget(1000, self.conversation, self.store))
        self.assertTrue(_something_fits_the_budget(None, self.conversation, self.store))

    # --- the four failures of the round-3 full run ----------------------------------------------

    def test_a_plain_shipping_question_gets_the_owners_sentence(self):
        """S12 t3 and S13 t4, round 3 full run: with the shipping FAQ entry off, "الشحن بيوصل اسكندريه؟"
        and "el sha7n b kam?" went to the model and got "تقصد أنهي عطر وحجم؟"."""
        from products.services.router import _faq_lookup, route
        answer = self.SHIPPING_FACT + "."
        for question in ("الشحن بيوصل اسكندريه؟", "tmam 😂 el sha7n b kam?", "بتشحنوا للمحافظات؟", "shipping kam?"):
            with self.subTest(question=question):
                self.assertEqual((_faq_lookup(question, self.store) or {}).get("answer"), answer)
        # Same-day delivery and free shipping are in other lines of the facts: the model answers those.
        for question in ("ينفع شحن في نفس اليوم؟", "الشحن مجاني؟"):
            with self.subTest(question=question):
                self.assertIsNone(_faq_lookup(question, self.store))
        with patch("products.services.router.classify", return_value="faq"), \
             patch("products.services.router._handle_general_raw") as general:
            reply, _ = route("الشحن بيوصل اسكندريه؟", [], self.store, self.conversation)
        general.assert_not_called()
        self.assertEqual(reply, answer)

    def test_a_shipping_draft_that_fails_the_price_check_falls_back_to_the_store_sentence(self):
        """The same failure from the other side: never "which perfume and size?" for a shipping question."""
        from products.services.router import _rephrased
        bad = "الشحن 60 جنيه، يعني الإجمالي 810 جنيه."
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), \
             patch("products.services.router.repetition.repeated_frames", return_value=[]):
            reply, _, _ = _rephrased(bad, "", [], self.store, lambda hint: (bad, ""),
                                     message="tmam 😂 el sha7n b kam?")
        self.assertEqual(reply, self.SHIPPING_FACT + ".")

    def test_a_leaked_drafting_instruction_is_removed(self):
        """S02 round 3 full run t7 (critical): "سعر الـ90 ملي هيتضاف تلقائيًا." above the price itself."""
        from products.services.router import _without_drafting_leak
        reply = ("Layton\nالـCardamom مش موجود ضمن النوتات المسجلة عندنا.\nسعر الـ90 ملي هيتضاف تلقائيًا.\n"
                 "زجاجة الستور الخاصة بينا 90 ملي بـ2000 جنيه.")
        self.assertEqual(_without_drafting_leak(reply),
                         "Layton\nالـCardamom مش موجود ضمن النوتات المسجلة عندنا.\nزجاجة الستور الخاصة بينا 90 ملي بـ2000 جنيه.")
        inline = "السعر هيتضاف تلقائياً تحت. Layton نوتاته فيها تفاح."
        self.assertEqual(_without_drafting_leak(inline), "Layton نوتاته فيها تفاح.")
        clean = "Layton\n\nزجاجة الستور الخاصة بينا 90 ملي بـ2000 جنيه."
        self.assertEqual(_without_drafting_leak(clean), clean)

    def test_a_doubt_that_closes_with_ill_think_gets_the_short_close(self):
        """S02 round 3 full run t8 (high): "مش مطمن لقائمة النوتات… هفكر وأراجعها قبل ما أقرر، تسلم." got a
        recap of the notes and an invitation to the branch."""
        from products.services.objection_service import handle_objection
        from products.services.sales import objection as sales_objection
        message = "تمام، كده مش مطمن لقائمة النوتات عندكم بصراحة. هفكر وأراجعها قبل ما أقرر، تسلم."
        objection = sales_objection.detect(message, [])
        self.assertIsNotNone(objection)
        with patch("products.services.objection_service.chat") as chat, \
             patch("products.services.objection_service.resolve_products") as resolve:
            reply, context = handle_objection(message, objection, [], self.store, self.conversation)
        chat.assert_not_called()
        resolve.assert_not_called()
        self.assertEqual((reply, context), ("تسلم، خد وقتك.", "CUSTOMER_DEFERRED"))

    def test_a_guarantee_question_gets_the_store_policy_not_a_paraphrase(self):
        """S07 round 3 full run t4 (critical): "والضمان ده ينفع لو فتحته وجربته؟" went to the model, which
        invented "لو فتحتيه وجربتيه، مش هينفع استبداله أو استرجاعه"."""
        from products.models import StaticFAQ
        from products.services.router import _is_exchange_question, _policy_side_questions
        from products.services.static_faq_service import normalize_arabic
        policy = ("ضمان الاستبدال لمدة يومين من وقت الاستلام.\n"
                  " في حالة الاستبدال: يتم دفع شحن مرتين + 150ج للبوكس الجديد، أو مجانًا داخل الستور.")
        StaticFAQ.objects.create(store=self.store, question="في استرجاع أو استبدال؟", priority=10, answer=policy,
                                 keywords="استرجاع, استبدال, ضمان")
        answers, rest, asks = _policy_side_questions(
            "تمام، هو سعره في حدود كام؟ والضمان ده ينفع لو فتحته وجربته؟", self.store)
        self.assertEqual((answers, asks), ([policy], True))
        self.assertIn("سعره في حدود كام", rest)
        self.assertNotIn("الضمان", rest)
        # A promise is not the policy.
        for message in ("تضمن إنها مش مسكرة؟", "هو مضمون يعجبها؟"):
            with self.subTest(message=message):
                self.assertFalse(_is_exchange_question(normalize_arabic(message)))

    # --- found in the replay of the full run's failures ----------------------------------------

    def test_a_complaint_that_the_notes_keep_coming_is_not_a_request_for_them(self):
        """S02 round 3 replay 2, t6 (high): "لسه بتكرر نفس النوتات… الـ90 مل من Layton بكام؟" got the whole
        notes list again above the price."""
        from products.services.scent_description import notes_requested
        self.assertFalse(notes_requested("لسه بتكرر نفس النوتات من غير مصدر 😅 خلينا في اللي متأكدين منه—الـ90 مل من Layton بكام؟",
                                         product_names=("Layton",)))
        self.assertFalse(notes_requested("انت كررت النوتات تاني، السعر كام؟"))
        self.assertTrue(notes_requested("طب Layton الـ top notes والـ middle والـ base إيه؟", product_names=("Layton",)))
        self.assertTrue(notes_requested("نوتاته إيه؟"))

    def test_where_the_notes_came_from_is_answered_once_and_honestly(self):
        """S02 round 3 replay 2, t3-t5: "جبتها منين؟" / "عندك مصدر للنوتات دي؟" got the list again, then "no
        source" beside the same list."""
        from products.services.router import NOTES_SOURCE, NOTES_SOURCE_AGAIN, _notes_source_note
        from products.services.static_faq_service import normalize_arabic

        def note(message, history=()):
            return _notes_source_note(normalize_arabic(message), list(history))

        for question in ("استنى بس، الفانيليا في الـbase بتاعة Aventus؟ دي مش من النوتات الرسمية اللي أعرفها، جبتها منين؟",
                         "عندك مصدر للنوتات دي؟",
                         "وهل النوتات دي من مصدر رسمي ولا دي تركيبة الستور؟",
                         "الـcardamom من النوتات المعروفة في Layton. إنتوا معتمدين على قايمة إيه؟",
                         "أغلب الـ note breakdowns اللي شفتها حاطة الـ pepper في الـ top. إنت متأكد من المصدر؟"):
            with self.subTest(question=question):
                self.assertEqual(note(question), NOTES_SOURCE)
        said = [{"role": "assistant", "content": NOTES_SOURCE + "\n\nAventus: ..."}]
        self.assertEqual(note("عندك مصدر للنوتات دي؟", said), NOTES_SOURCE_AGAIN)
        said.append({"role": "assistant", "content": NOTES_SOURCE_AGAIN})
        self.assertEqual(note("عندك مصدر للنوتات دي؟", said), "")
        # A complaint beside a price question, and a question about something else, do not ask for it.
        self.assertEqual(note("لسه بتكرر نفس النوتات من غير مصدر 😅 خلينا في اللي متأكدين منه—الـ90 مل من Layton بكام؟"), "")
        self.assertEqual(note("طب Layton نوتاته إيه؟"), "")
