"""Promotional purchases never become regular-price carts or orders."""
import json
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase
from rest_framework.test import APIClient

from products.models import (Store, StoreSettings, Brand, Product, ProductVariant,
                             Conversation, Cart, CartItem, Order, Notification, StaticFAQ)
from products.services import promotion_service as promotions
from products.services.conversation_service import save_message, build_llm_history
from products.services.handoff import HANDOFF_CONFIRMATION, has_handoff_offer
from products.services.order_service import handle_order
from products.services.router import route


def bot(content):
    return {"role": "assistant", "content": content}


OFFER = promotions.LIMITATION + "\n\n" + promotions.INVITATION
FACT = "بوكس الصيف: اشتري 3 حجم 90 مل عليهم 1 هدية بسعر 3000 جنيه."


class PromotionDetectionTests(SimpleTestCase):
    def test_quote_verification_changes_only_whitespace(self):
        from eval_harness.scenarios_conv1153 import BUNDLE_ENTRIES
        original = BUNDLE_ENTRIES[0]
        flattened = " ".join(original.split())
        self.assertEqual(promotions._source_quote(flattened, original), original)
        self.assertEqual(promotions._source_quote(flattened, original.replace("\n", "\r\n\t")),
                         original.replace("\n", "\r\n\t"))
        for invalid in (flattened.replace("3000", "1000"), flattened.replace("Private Flame", "Private Fire"),
                        flattened.replace("4500", "450"), "الاربعة ب 300", None, "", {"quote": flattened}):
            with self.subTest(candidate=invalid):
                self.assertIsNone(promotions._source_quote(invalid, original))

    def test_explicit_requests(self):
        for message in (
            "عايز أطلب الباندل", "ضيف الباكدج", "الباكيدج بكام؟", "عايزة الكومبو",
            "ضيف سوفاج بخصم 20%", "اسمي محمد للعرض", "ايه الباكدجات؟", "عايزه بسعر خاص",
            "طبق كوبون SAVE20", "عايز سوفاج وبلو بالعرض", "عندكم عروض؟",
            "اشتري اتنين والتالت هدية", "اشتري 2 وخد 1 هدية", "الهدية المجانية مع الطلب",
            "Order the bundle", "Add the combo", "apply a discount code",
            "buy two get one free", "Do you have offers?",
        ):
            with self.subTest(message=message):
                self.assertTrue(promotions.blocks_checkout(message))

    def test_ordinary_messages_and_negative_offer_references(self):
        for message in (
            "عايز سوفاج وبلو دي شانيل", "عايز عطر هدية", "شكل الباكدج ايه؟",
            "صورة البوكس", "اعرض عليا الأحجام", "What's the packaging like?",
            "I want two perfumes as a gift", "خلاص من غير العرض عايز سوفاج 50 مل",
            "Buy Sauvage without the discount", "الغي الاوردر كله",
        ):
            with self.subTest(message=message):
                self.assertFalse(promotions.blocks_checkout(message))

    def test_removing_one_offer_does_not_allow_another(self):
        self.assertTrue(promotions.blocks_checkout("من غير العرض بس طبق كوبون SAVE"))

    def test_followups_and_topic_boundaries(self):
        history = [bot(OFFER)]
        for message in ("عايز اطلبه", "هاخد التاني", "01012345678", "اسمي محمد", "شارع النصر"):
            self.assertTrue(promotions.blocks_checkout(message, history), message)
        for message in ("ريحة سوفاج ايه؟", "الشحن بكام؟", "خلاص من غير العرض هات سوفاج", "الغي الطلب كله"):
            self.assertFalse(promotions.blocks_checkout(message, history), message)
        history += [{"role": "user", "content": "خلاص من غير العرض هات سوفاج"}, bot("تحب انهي حجم؟")]
        self.assertFalse(promotions.blocks_checkout("ضيف واحدة", history))
        self.assertFalse(promotions.has_promotion_context([bot(OFFER), {"role": "agent", "content": "اهلا"}]))


class PromotionRoutingTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Promotion regression")
        self.settings = StoreSettings.objects.create(store=self.store)
        self.conversation = Conversation.objects.create(store=self.store)
        brand = Brand.objects.create(store=self.store, name="Test")
        self.product = Product.objects.create(store=self.store, brand=brand, name="Sauvage")
        self.variant = ProductVariant.objects.create(product=self.product, volume=50, price=500, stock=10)
        self.addCleanup(patch.stopall)
        self.classifier = patch("products.services.router.classify", return_value="order").start()
        self.facts_model = patch("products.services.promotion_service.chat", return_value='{"quotes": []}').start()
        self.usage = patch("products.services.router.record_llm_message").start()
        self.facts_usage = patch("products.services.promotion_service.record_llm_message").start()

    def configure(self, facts=FACT, field="system_prompt"):
        setattr(self.settings, field, facts)
        self.settings.save()

    def make_cart(self):
        cart = Cart.objects.create(conversation=self.conversation, customer_name="Original",
                                   customer_phone="01012345678", quote={"total": "500"})
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=1)
        return cart

    def snapshot(self):
        return (list(Cart.objects.values()), list(CartItem.objects.values()),
                list(Order.objects.values()), list(ProductVariant.objects.values()))

    def assert_boundary(self, reply):
        self.assertIn(promotions.LIMITATION, reply)
        self.assertTrue(has_handoff_offer([bot(reply)]))
        self.assertNotIn("تم تأكيد", reply)
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.needs_human)
        self.assertFalse(Notification.objects.exists())

    def test_explicit_bundle_cannot_be_classified_into_checkout(self):
        with patch("products.services.router.handle_order", side_effect=AssertionError("checkout")):
            reply, context = route("عايز اطلب الباندل", [], self.store, self.conversation)
        self.assert_boundary(reply)
        self.assertEqual(context, promotions.PROMOTION_CONTEXT)
        self.classifier.assert_not_called()
        self.facts_model.assert_not_called()
        self.assertFalse(Cart.objects.exists())

    def test_existing_cart_phone_faq_and_price_dispute_cannot_bypass(self):
        self.make_cart()
        StaticFAQ.objects.create(store=self.store, question="offer", keywords="باندل", answer="wrong")
        before = self.snapshot()
        for message in ("ضيف الباندل ورقمي 01000000000", "خلي سوفاج مع خصم 20%",
                        "سعر العرض غلط", "طبق الكوبون على الاوردر"):
            with self.subTest(message=message), patch("products.services.router.handle_order", side_effect=AssertionError("checkout")):
                reply, _ = route(message, [], self.store, self.conversation)
                self.assert_boundary(reply)
                self.assertEqual(before, self.snapshot())

    def test_order_handler_itself_preserves_cart_and_stock(self):
        self.make_cart()
        before = self.snapshot()
        with patch("products.services.order_service.chat", side_effect=AssertionError("order extraction")):
            for message, history in (("عايز باندل", []), ("01099999999", [bot(OFFER)]),
                                     ("سوفاج", [bot(OFFER)])):
                reply, _ = handle_order(message, history, self.store, self.conversation)
                self.assert_boundary(reply)
        self.assertEqual(before, self.snapshot())

    def test_pending_bundle_survives_an_informational_detour(self):
        self.make_cart()
        before = self.snapshot()
        history = [bot(OFFER), {"role": "user", "content": "الشحن بكام؟"}, bot("الشحن 50 جنيه.")]
        with patch("products.services.router.handle_order", side_effect=AssertionError("checkout")):
            reply, _ = route("عنواني شارع النصر", history, self.store, self.conversation)
        self.assert_boundary(reply)
        self.assertEqual(before, self.snapshot())

    def test_configured_name_and_ambiguous_name_before_checkout(self):
        self.configure(FACT + "\nعرض باسم فرحة الصيف: عطرين بسعر 900 جنيه.")
        self.make_cart()
        before = self.snapshot()
        reply, _ = route("عايز بوكس الصيف", [], self.store, self.conversation)
        self.assert_boundary(reply)
        self.classifier.assert_not_called()
        self.classifier.return_value = "promotion"
        reply, _ = route("ضيف فرحة الصيف ورقمي 01099999999", [], self.store, self.conversation)
        self.assert_boundary(reply)
        self.assertIn("فرحة الصيف", self.classifier.call_args.kwargs["promotion_context"])
        self.assertEqual(before, self.snapshot())

    def test_named_set_does_not_require_the_word_offer_in_configuration(self):
        self.configure("بوكس الصيف: عطرين بسعر 900 جنيه.")
        response, _ = route("عايز بوكس الصيف", [], self.store, self.conversation)
        self.assert_boundary(response)
        self.classifier.assert_not_called()
        self.assertFalse(Cart.objects.exists())

    def test_known_details_are_verbatim_from_either_config_field(self):
        for field in ("system_prompt", "business_facts"):
            with self.subTest(field=field):
                self.configure(field=field)
                self.facts_model.return_value = json.dumps({"quotes": [FACT]}, ensure_ascii=False)
                reply, _ = route("تفاصيل بوكس الصيف ايه؟", [], self.store, self.conversation)
                self.assertIn(FACT, reply)
                self.assert_boundary(reply)

    def test_conversation_1153_lists_all_six_bundles_from_source(self):
        from eval_harness.scenarios_conv1153 import BUSINESS_FACTS, BUNDLE_ENTRIES, BUNDLE_NAMES, HISTORY, QUESTION
        self.configure(BUSINESS_FACTS, field="business_facts")
        self.facts_model.return_value = json.dumps({"quotes": [" ".join(entry.split()) for entry in BUNDLE_ENTRIES]})
        reply, context = route(QUESTION, HISTORY, self.store, self.conversation)
        for entry in BUNDLE_ENTRIES:
            self.assertIn(entry, reply)
        for name in BUNDLE_NAMES:
            self.assertEqual(reply.count(name), 1)
        self.assertNotIn(promotions.UNKNOWN_DETAILS, reply)
        self.assertEqual(context, promotions.PROMOTION_CONTEXT)
        self.assert_boundary(reply)
        self.assertFalse(Cart.objects.exists())
        self.facts_model.assert_not_called()

    def test_named_multiline_bundle_accepts_flattened_model_quote(self):
        from eval_harness.scenarios_conv1153 import BUSINESS_FACTS, BUNDLE_ENTRIES, HISTORY
        self.configure(BUSINESS_FACTS, field="business_facts")
        self.facts_model.return_value = json.dumps({"quotes": [" ".join(BUNDLE_ENTRIES[2].split())]})
        reply, _ = route("تفاصيل باندل Sable Reserve", HISTORY, self.store, self.conversation)
        self.assertIn(BUNDLE_ENTRIES[2], reply)
        self.assertNotIn(promotions.UNKNOWN_DETAILS, reply)
        self.assert_boundary(reply)

    def test_bundle_quote_keeps_only_verified_facts_and_deduplicates_formatting(self):
        from eval_harness.scenarios_conv1153 import BUSINESS_FACTS, BUNDLE_ENTRIES, HISTORY, QUESTION
        self.configure(BUSINESS_FACTS, field="business_facts")
        original = BUNDLE_ENTRIES[0]
        self.facts_model.return_value = json.dumps({"quotes": [original, " ".join(original.split()),
                                                                BUNDLE_ENTRIES[1].replace("3000", "1000")]})
        reply, _ = route("تفاصيل باندل Private Flame", HISTORY, self.store, self.conversation)
        self.assertEqual(reply.count("Private Flame"), 1)
        self.assertNotIn("Smoked Velvet", reply)
        self.assertNotIn("1000", reply)
        self.assert_boundary(reply)

    def test_unknown_and_fabricated_details_do_not_deny_offers_or_invent_prices(self):
        for facts, generated in (("", '{"quotes": []}'), ("عنواننا القاهرة", '{"quotes": []}'),
                                 (FACT, '{"quotes": ["bundle costs 100 pounds"]}'), (FACT, 'not json')):
            with self.subTest(facts=facts, generated=generated):
                self.configure(facts)
                self.facts_model.return_value = generated
                reply, _ = route("عندكم عروض؟", [], self.store, self.conversation)
                self.assertIn(promotions.UNKNOWN_DETAILS, reply)
                self.assertNotIn("مفيش عروض", reply)
                self.assertNotIn("100", reply)
                self.assert_boundary(reply)

    def test_transfer_requires_consent_and_notifies_once(self):
        reply, context = route("عايز اطلب الباندل", [], self.store, self.conversation)
        save_message(self.conversation, "assistant", reply, internal_context=context)
        history = build_llm_history(self.conversation)
        for _ in range(2):
            response, _ = route("ماشي", history, self.store, self.conversation)
            self.assertEqual(response, HANDOFF_CONFIRMATION)
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.needs_human)
        self.assertEqual(Notification.objects.filter(type="handoff").count(), 1)
        self.assertFalse(Cart.objects.exists())

    def test_explicit_human_request_beats_bundle_and_contact_details(self):
        self.make_cart()
        before = self.snapshot()
        response, _ = route("عايز اكلم مندوب عشان الباندل ورقمي 01000000000", [], self.store, self.conversation)
        self.assertEqual(response, HANDOFF_CONFIRMATION)
        self.assertEqual(before, self.snapshot())

    def test_refusal_and_insistence_do_not_transfer_or_repeat_invitation(self):
        history = [bot(OFFER)]
        for message in ("لا مش عايز مندوب", "انت نفذلي الطلب"):
            response, _ = route(message, history, self.store, self.conversation)
            self.assertIn(promotions.LIMITATION, response)
            self.assertNotIn(promotions.INVITATION, response)
            self.assertFalse(has_handoff_offer([bot(response)]))
            history += [{"role": "user", "content": message}, bot(response)]
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Cart.objects.exists())

    def test_do_it_after_declining_is_not_transfer_consent(self):
        history = [bot(OFFER), {"role": "user", "content": "no human"},
                   bot(promotions.LIMITATION + "\n" + promotions.DECLINED)]
        response, _ = route("please do it", history, self.store, self.conversation)
        self.assertIn(promotions.LIMITATION, response)
        self.assertNotIn(promotions.INVITATION, response)
        self.assertFalse(Notification.objects.exists())

    def test_refusal_in_first_bundle_request_does_not_offer_transfer(self):
        response, _ = route("مش عايز مندوب، عايز الباندل", [], self.store, self.conversation)
        self.assertIn(promotions.LIMITATION, response)
        self.assertNotIn(promotions.INVITATION, response)
        self.facts_model.assert_not_called()
        self.assertFalse(Notification.objects.exists())

    def test_decline_with_product_question_answers_the_question(self):
        self.classifier.return_value = "product_info"
        with patch("products.services.router.get_product_info", return_value=("ريحة فريش", "")) as info:
            response, _ = route("لا قولي ريحة سوفاج ايه", [bot(OFFER)], self.store, self.conversation)
        info.assert_called_once()
        self.assertNotIn(promotions.LIMITATION, response)
        self.assertFalse(Notification.objects.exists())

    def test_regular_purchase_releases_boundary_and_requires_normal_summary(self):
        data = {"products": [{"name": "Sauvage", "volume": 50, "quantity": 1, "bottle_type": "normal"}],
                "customer_name": "Customer", "customer_phone": "01012345678", "customer_secondary_phone": "01112345678",
                "shipping_address": "Cairo, Nasr City, building 5", "is_confirmed": False}
        message = "خلاص من غير العرض عايز Sauvage 50 مل"
        with patch("products.services.order_service.chat", return_value=json.dumps(data)), \
             patch("products.services.order_service.resolve_product", return_value=self.product):
            reply, _ = route(message, [bot(OFFER)], self.store, self.conversation)
        self.assertNotIn(promotions.LIMITATION, reply)
        self.assertTrue(CartItem.objects.filter(variant=self.variant).exists())
        self.assertIn("500", reply)
        self.assertFalse(Order.objects.exists())

    def test_ordinary_multi_item_order_and_cancellation_still_work(self):
        with patch("products.services.router.handle_order", return_value=("normal checkout", "")) as order:
            response, _ = route("عايز سوفاج وبلو دي شانيل", [], self.store, self.conversation)
            self.assertEqual(response, "normal checkout")
            order.assert_called_once()
        self.make_cart()
        self.classifier.return_value = "order_cancel"
        response, _ = route("الغي الاوردر كله", [bot(OFFER)], self.store, self.conversation)
        self.assertIn("إلغاء", response)
        self.assertFalse(CartItem.objects.exists())

    def test_unsent_promotion_and_staff_boundary_do_not_block_normal_orders(self):
        save_message(self.conversation, "assistant", OFFER, delivery_status="failed")
        self.assertFalse(promotions.has_promotion_context(build_llm_history(self.conversation)))
        save_message(self.conversation, "assistant", OFFER)
        save_message(self.conversation, "agent", "اهلا")
        history = promotions.context_history(build_llm_history(self.conversation), self.conversation)
        self.assertFalse(promotions.has_promotion_context(history))
        with patch("products.services.router.handle_order", return_value=("normal checkout", "")):
            response, _ = route("هاخد سوفاج", build_llm_history(self.conversation), self.store, self.conversation)
        self.assertEqual(response, "normal checkout")

    def test_full_chat_response_keeps_limitation_and_invitation(self):
        from products.views import signer
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=self.store.api_key)
        response = client.post("/api/chat/", {"conversation_id": signer.sign_object(self.conversation.pk),
                                            "message": "عايز أطلب الباندل"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_boundary(response.data["reply"])
