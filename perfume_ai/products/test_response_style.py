"""Reworded questions keep their historical answers and checkout safeguards."""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Cart, Conversation, Order, Product, ProductVariant, Store, StoreSettings
from products.services.ai.intent import extract_intent
from products.services.conversation_service import save_message
from products.services.handoff import handoff_offer_reply
from products.services.order_service import _already_asked_for_details, _ask_which_perfume, handle_order
from products.services.product_formatting import format_products
from products.services.router import route
from products.services.router import _rephrased
from products.services.performance_policy import performance_turn
from products.services import promotion_service
from products.services.sales import gender, scent_answers
from products.services.sales.occasion_preferences import delegates_occasion


GENDER_QUESTIONS = (
    "حضرتك محتاج برفان رجالي ولا حريمي",
    "محتاج برفان رجالي ولا حريمي؟",
)
MULTIPLE_QUESTIONS = (
    "خلينا نختارهم واحد واحد عشان أقدر أركز معاك. تحب نبدأ بالرجالي ولا الحريمي؟",
    "نختار واحد واحد. نبدأ بالرجالي ولا الحريمي؟",
)
SCENT_QUESTIONS = (
    "قولي ذوقك. يعني بتحب الحاجات الفريش ولا الحاجات الخشبية ولا العود ولا الحاجات المسكره عشان اشوفلك حاجه مناسبه",
    "بتحب الفريش، الخشبي، العود، ولا المسكر؟",
)
PRODUCT_QUESTIONS = (
    "تمام، بس مش واضحلي عايز تطلب أنهي عطر. ممكن تقولي اسم العطر اللي عايزه؟",
    "تحب تطلب أنهي عطر؟ قولي اسمه.",
)
PROMOTION_LIMITATIONS = (
    "تسجيل طلبات الباندلز والكومبوهات أو تطبيق العروض والخصومات والكوبونات والهدايا الترويجية مش بقدر أعمله هنا؛ ده لازم يتم مع فريق خدمة العملاء.",
    "طلبات الباندلز وتطبيق العروض والخصومات بيتابعها فريق خدمة العملاء، مش بقدر أنفذها هنا.",
)


def question_history(question):
    return [{"role": "assistant", "content": question}]


class HistoricalQuestionAnswersTests(SimpleTestCase):
    def test_gender_answers_and_short_confirmations_stay_pending_for_both_questions(self):
        for question in (*GENDER_QUESTIONS, *MULTIPLE_QUESTIONS):
            history = question_history(question)
            for answer, expected in (("رجالي", "male"), ("حريمي", "female"), ("للجنسين", "unisex")):
                with self.subTest(question=question, answer=answer):
                    self.assertTrue(gender.pending_answer(answer, history))
                    self.assertEqual(gender.customer_choice(answer), expected)
            for answer in ("تمام", "ماشي", "اختارلي"):
                with self.subTest(question=question, answer=answer):
                    self.assertTrue(gender.pending_answer(answer, history))
                    self.assertIsNone(gender.customer_choice(answer))

    def test_scent_ordinals_keep_all_four_options_in_order(self):
        for question in SCENT_QUESTIONS:
            history = question_history(question)
            for answer, expected in (("الأول", ["fresh"]), ("التاني", ["woody"]),
                                     ("٣", ["oud"]), ("الرابع", ["sweet"]),
                                     ("آخر واحد", ["sweet"]), ("الأول والتالت", ["fresh", "oud"])):
                with self.subTest(question=question, answer=answer):
                    self.assertEqual(scent_answers.answer_updates(answer, history).get("scent_tags"), expected)

    def test_scent_extraction_preserves_named_answers_and_ordinals_when_model_fails(self):
        for question in SCENT_QUESTIONS:
            for answer, expected in (("الفريش", ["fresh"]), ("خشبي", ["woody"]),
                                     ("عود", ["oud"]), ("مسكر", ["sweet"]), ("التاني", ["woody"])):
                with self.subTest(question=question, answer=answer), \
                     patch("products.services.ai.intent.chat", return_value="invalid JSON"):
                    self.assertEqual(extract_intent(answer, history=question_history(question)).get("scent_tags"), expected)

    def test_scent_delegation_and_confirmations_do_not_select_an_occasion(self):
        for question in SCENT_QUESTIONS:
            history = question_history(question)
            with self.subTest(question=question):
                self.assertEqual(scent_answers.answer_updates("اختارلي", history).get("scent_open"), True)
                self.assertFalse(delegates_occasion("اختارلي", history))
                self.assertEqual(scent_answers.answer_updates("تمام", history), {})

    def test_later_questions_or_agent_messages_end_pending_questions(self):
        for question in (*GENDER_QUESTIONS, *SCENT_QUESTIONS):
            for boundary in ({"role": "assistant", "content": "تحب أنهي حجم؟"},
                             {"role": "agent", "content": question}):
                history = question_history(question) + [boundary]
                with self.subTest(question=question, boundary=boundary):
                    self.assertFalse(gender.pending_answer("تمام", history))
                    self.assertEqual(scent_answers.answer_updates("الأول", history), {})

    def test_short_approvals_to_handoff_do_not_apply_to_gender_or_scent(self):
        for question in ("تحب أحولك لفريق خدمة العملاء؟", "حابب تتكلم مع حد من الفريق؟"):
            with self.subTest(question=question):
                self.assertTrue(handoff_offer_reply("ماشي", question_history(question)))
                self.assertFalse(handoff_offer_reply("لا شكرا", question_history(question)))
        for question in (*GENDER_QUESTIONS, *SCENT_QUESTIONS):
            with self.subTest(question=question):
                self.assertFalse(handoff_offer_reply("ماشي", question_history(question)))

    def test_declined_promotion_transfer_is_preserved_for_historical_limitations(self):
        for limitation in PROMOTION_LIMITATIONS:
            history = question_history(limitation + "\n\nلو حابب تشتري عطور بالسعر العادي من غير العرض، قولي.")
            with self.subTest(limitation=limitation):
                self.assertTrue(promotion_service.blocks_checkout("اسمي أحمد ورقمي 01000000000", history))
                reply, context = promotion_service.reply("اسمي أحمد", history, explain=False)
                self.assertEqual(context, "PROMOTION_PENDING")
                self.assertNotIn("تحب أحولك", reply)
                self.assertIn("بالسعر العادي", reply)


class FixedReplyCheckoutCompatibilityTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Fixed reply compatibility")
        StoreSettings.objects.create(store=self.store, payment_instructions="تعليمات الدفع المحفوظة\nحساب المتجر: TEST")
        self.brand = Brand.objects.create(store=self.store, name="House")
        self.product = Product.objects.create(store=self.store, brand=self.brand, name="Shared Breeze", gender="unisex")
        self.variant = ProductVariant.objects.create(product=self.product, volume=50, price=Decimal("420.25"))
        self.conversation = Conversation.objects.create(store=self.store)

    def turn(self, **updates):
        data = {"products": [{"name": self.product.name, "volume": 50, "quantity": 2, "bottle_type": "normal"}],
                "customer_name": "أحمد", "customer_phone": "01000000000", "customer_secondary_phone": "01100000000",
                "shipping_address": "القاهرة، شارع النيل، عمارة 12", "is_confirmed": False}
        data.update(updates)
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            result = handle_order("تمام", [], self.store, self.conversation)
        save_message(self.conversation, "assistant", *result)
        return result[0]

    def test_unresolved_product_prompt_names_earlier_offer_for_old_and_new_wording(self):
        save_message(self.conversation, "assistant", "Shared Breeze مناسب ليك.",
                     internal_context=format_products([self.product]))
        for question in PRODUCT_QUESTIONS:
            with self.subTest(question=question):
                save_message(self.conversation, "assistant", question)
                reply = _ask_which_perfume(self.conversation, self.store)
                self.assertIn("Shared Breeze", reply)
                self.assertIn("الحجم", reply)

    def test_delivered_old_and_new_contact_questions_are_recognized(self):
        for question in ("تمام، عشان أأكدلك الطلب ناقصني بس الاسم ورقم الموبايل.",
                         "ناقصني للطلب الاسم ورقم الموبايل.", "ولسه ناقص الاسم ورقم الموبايل."):
            with self.subTest(question=question):
                save_message(self.conversation, "assistant", question)
                self.assertTrue(_already_asked_for_details(self.conversation))

    def test_unsent_question_cannot_replace_a_delivered_contact_request(self):
        save_message(self.conversation, "assistant", "ناقصني للطلب الاسم ورقم الموبايل.")
        save_message(self.conversation, "assistant", "تحب أنهي حجم؟", delivery_status="failed")
        self.assertTrue(_already_asked_for_details(self.conversation))

    def test_contact_collection_keeps_both_phones_and_address_required(self):
        reply = self.turn(customer_name=None, customer_phone=None, customer_secondary_phone=None, shipping_address=None,
                          is_confirmed=True)
        for field in ("الاسم", "بديل", "المحافظة", "المنطقة", "الشارع", "المنزل"):
            self.assertIn(field, reply)
        self.assertFalse(Order.objects.exists())
        reply = self.turn(customer_secondary_phone=None, is_confirmed=True)
        self.assertIn("بديل", reply)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Cart.objects.get(conversation=self.conversation).items.get().quantity, 2)

    def test_full_summary_precedes_confirmation_and_preserves_payment_instructions(self):
        summary = self.turn(is_confirmed=True)
        for fact in ("أحمد", "01000000000", "01100000000", "القاهرة، شارع النيل، عمارة 12",
                     "2 × Shared Breeze", "50ml", "840.50", "نأكد الطلب"):
            self.assertIn(fact, summary)
        self.assertFalse(Order.objects.exists())
        self.assertIn("message_id", Cart.objects.get(conversation=self.conversation).quote)
        reply = self.turn(is_confirmed=True)
        self.assertTrue(Order.objects.filter(conversation=self.conversation).exists(), reply)
        order = Order.objects.get(conversation=self.conversation)
        self.assertEqual(order.total_price, Decimal("840.50"))
        self.assertEqual(order.items.get().quantity, 2)
        self.assertIn(f"#{order.pk}", reply)
        self.assertIn("تعليمات الدفع المحفوظة\nحساب المتجر: TEST", reply)

    def test_delivered_legacy_gender_prompt_routes_short_confirmation_back_to_gender(self):
        for question in GENDER_QUESTIONS:
            with self.subTest(question=question):
                save_message(self.conversation, "assistant", question)
                with patch("products.services.router.classify", return_value="order"), \
                     patch("products.services.router.extract_intent", return_value={}), \
                     patch("products.services.router.confident_catalogue_match", return_value=None), \
                     patch("products.services.product_resolver.confirm_unplaced", return_value=None), \
                     patch("products.services.order_service.chat", return_value='{"products": []}'):
                    reply, context = route("تمام", [], self.store, self.conversation)
                self.assertEqual(context, "GENDER_REQUIRED")
                self.assertIn("رجالي", reply)
                self.assertIn("حريمي", reply)
                self.assertFalse(Cart.objects.filter(conversation=self.conversation).exists())

    def test_recommendation_fallback_honors_requested_count_and_omits_unsolicited_performance(self):
        products = [self.product]
        for name, scent in (("Forest Path", "woody"), ("Oud Night", "oud")):
            products.append(Product.objects.create(store=self.store, brand=self.brand, name=name,
                                                    gender="unisex", scent_tags=[scent]))
        context = format_products(products)
        draft = "Shared Breeze. Prompt says mention these notes."
        with performance_turn("رشحلي 3 عطور"):
            reply, _, _ = _rephrased(draft, context, [], self.store, lambda hint: (draft, context),
                                     recommendation_intent={"recommendation_mode": "new_goal", "recommendation_count": 3},
                                     recommendation_candidates=products)
        for name in ("Shared Breeze", "Forest Path", "Oud Night"):
            self.assertIn(name, reply)
        self.assertIn("خشبي", reply)
        self.assertIn("عودي", reply)
        self.assertNotIn("ثبات", reply)
        self.assertNotIn("فوحان", reply)

    def test_recommendation_fallback_answers_requested_performance_once(self):
        product = Product.objects.create(store=self.store, brand=self.brand, name="Forest Path", gender="unisex",
                                         scent_tags=["woody"])
        products = [self.product, product]
        context = format_products(products)
        draft = "Shared Breeze. Prompt says mention these notes."
        with performance_turn("رشحلي عطرين وثباتهم وفوحانهم إيه؟"):
            reply, _, _ = _rephrased(draft, context, [], self.store, lambda hint: (draft, context),
                                     recommendation_intent={"recommendation_mode": "new_goal"},
                                     recommendation_candidates=products)
        self.assertIn("Shared Breeze", reply)
        self.assertIn("Forest Path", reply)
        self.assertEqual(reply.count("ثبات"), 1)
        self.assertEqual(reply.count("فوحان"), 1)
