"""Regressions for artifacts/bug_reproductions; all providers are simulated."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from products.models import (Store, StoreSettings, Brand, Product, ProductVariant,
    Conversation, Cart, Order, Message, StaticFAQ, InboundEvent, Notification)
from products.services.conversation_service import save_message, merge_preferences, receive_event, build_llm_history
from products.services.order_service import handle_order, change_order_status
from django.db import connection, close_old_connections
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event


class BugFixture(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Regression Store")
        StoreSettings.objects.create(store=self.store)
        brand = Brand.objects.create(store=self.store, name="Dior")
        self.a = Product.objects.create(store=self.store, brand=brand, name="Vanilo", gender="male", longevity="8 hours", projection="Strong")
        self.b = Product.objects.create(store=self.store, brand=brand, name="Leatherio", gender="male", longevity="6 hours", projection="Moderate")
        self.av = ProductVariant.objects.create(product=self.a, volume=50, price=607)
        self.a90 = ProductVariant.objects.create(product=self.a, volume=90, price=944)
        self.bv = ProductVariant.objects.create(product=self.b, volume=50, price=645)
        self.b90 = ProductVariant.objects.create(product=self.b, volume=90, price=700)
        self.original = ProductVariant.objects.create(product=self.a, volume=100, price=2000, bottle_type="original", stock=2)
        self.conv = Conversation.objects.create(store=self.store)
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.store.api_key)

    def turn(self, products=None, message="...", **changes):
        data = {"products": products or [], "is_confirmed": False,
            "customer_name": "Test", "customer_phone": "01000000000",
            "customer_secondary_phone": "01100000000", "shipping_address": "Test address"}
        data.update(changes)
        with patch("products.services.order_service.chat", return_value=json.dumps(data)):
            reply, context = handle_order(message, [], self.store, self.conv)
        save_message(self.conv, "assistant", reply, internal_context=context)
        return reply

    def line(self, product=None, volume=50, quantity=1, bottle_type="normal"):
        return {"name": (product or self.a).name, "volume": volume, "quantity": quantity, "bottle_type": bottle_type}


class CartBugTests(BugFixture):
    def test_L01_all_unsized_quantities_survive_partial_resolution(self):
        self.turn([self.line(self.a, None, 2), self.line(self.b, None, 3)])
        self.assertEqual(len(self.conv.cart.pending_items), 2)
        self.turn([self.line(self.a, 90, 2)])
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.get().quantity, 2)
        self.assertEqual(cart.pending_items[0]["name"], self.b.name)
        self.assertEqual(cart.pending_items[0]["quantity"], 3)
        self.assertFalse(Order.objects.exists())

    def test_L02_unknown_name_survives_and_blocks_confirmation(self):
        with patch("products.services.order_service.resolve_product", return_value=None):
            self.turn([self.line(), {"name": "Mystery Azure", "volume": 50, "quantity": 1}])
            reply = self.turn(is_confirmed=True)
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.pending_items[0]["name"], "Mystery Azure")
        self.assertIn("Mystery Azure", reply)
        self.assertFalse(Order.objects.exists())

    def test_unavailable_size_does_not_discard_other_lines(self):
        self.turn([self.line(self.a, 25), self.line(self.b)])
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.get().variant_id, self.bv.pk)
        self.assertEqual(cart.pending_items[0]["volume"], 25)

    def test_explicit_removal_keeps_other_lines(self):
        self.turn([self.line(), self.line(self.b)])
        cart = Cart.objects.get(conversation=self.conv)
        self.turn(removed_line_ids=[cart.items.get(variant=self.av).line_id])
        self.assertEqual(Cart.objects.get(conversation=self.conv).items.get().variant_id, self.bv.pk)

    def test_L03_edit_and_confirmation_requires_another_approval(self):
        self.turn([self.line()])
        reply = self.turn([self.line(quantity=2)], is_confirmed=True)
        self.assertIn("1214", reply)
        self.assertFalse(Order.objects.exists())
        self.turn(is_confirmed=True)
        self.assertEqual(Order.objects.get().total_price, Decimal("1214"))

    def test_identical_variants_keep_combined_quantity(self):
        self.turn([self.line(quantity=2), self.line(quantity=3)])
        self.assertEqual(Cart.objects.get(conversation=self.conv).items.get().quantity, 5)
        self.turn(is_confirmed=True)
        self.assertEqual(Order.objects.get().total_price, Decimal("3035"))

    def test_price_change_at_commit_returns_approvable_revised_quote(self):
        from products.services.order_service import create_order_in_db
        self.turn([self.line()])
        def reprice(*args, **kwargs):
            ProductVariant.objects.filter(pk=self.av.pk).update(price=700)
            return create_order_in_db(*args, **kwargs)
        with patch("products.services.order_service.create_order_in_db", side_effect=reprice):
            reply = self.turn(is_confirmed=True)
        self.assertIn("700", reply)
        self.assertFalse(Order.objects.exists())
        self.turn(is_confirmed=True)
        self.assertEqual(Order.objects.get().total_price, Decimal("700"))

    def test_C01_changed_price_requires_new_summary(self):
        self.turn([self.line()])
        ProductVariant.objects.filter(pk=self.av.pk).update(price=700)
        reply = self.turn(is_confirmed=True)
        self.assertFalse(Order.objects.exists())
        self.assertIn("700", reply)
        self.turn(is_confirmed=True)
        self.assertEqual(Order.objects.get().total_price, Decimal("700"))

    def test_C02_string_false_never_confirms_or_clears(self):
        self.turn([self.line()])
        for changes in ({"is_confirmed": "false"}, {"cart_cleared": "false"}):
            self.turn(**changes)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Cart.objects.get(conversation=self.conv).items.count(), 1)

    def test_invalid_quantity_preserves_cart(self):
        self.turn([self.line()])
        for quantity in (0, -1, True, 1.5, "2"):
            self.turn([self.line(quantity=quantity)], is_confirmed=True)
        self.assertEqual(Cart.objects.get(conversation=self.conv).items.get().quantity, 1)
        self.assertFalse(Order.objects.exists())

    def test_C04_notification_failure_cannot_report_failed_order(self):
        self.turn([self.line()])
        with patch("products.services.order_service.notify_new_order", side_effect=RuntimeError("injected")):
            with self.captureOnCommitCallbacks(execute=True):
                reply = self.turn(is_confirmed=True)
        self.assertIn("تم تأكيد", reply)
        order = Order.objects.get()
        self.assertTrue(order.notification_pending)
        self.assertFalse(Cart.objects.filter(conversation=self.conv).exists())
        from products.services.order_service import deliver_order_notification
        deliver_order_notification(order.pk)
        deliver_order_notification(order.pk)
        self.assertEqual(Notification.objects.filter(type="new_order").count(), 1)

    def test_C05_cancellation_is_final_and_restores_once(self):
        self.turn([self.line(volume=100, bottle_type="original")])
        self.turn(is_confirmed=True)
        order = Order.objects.get()
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 1)
        change_order_status(order.pk, self.store, "cancelled")
        change_order_status(order.pk, self.store, "cancelled")
        with self.assertRaises(ValueError):
            change_order_status(order.pk, self.store, "pending")
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 2)


class RecommendationBugTests(BugFixture):
    def test_total_followup_cannot_invent_a_different_bottle_type(self):
        from products.services.ai.intent import extract_intent
        merge_preferences(self.conv, {"requested_volume": 90, "bottle_type": "normal", "budget_scope": "total", "max_price": 1000})
        with patch("products.services.ai.intent.chat", return_value=json.dumps({"bottle_type": "original", "max_price": 1000})):
            intent = extract_intent("اختارلي الاتنين وقولي السعر النهائي للاتنين مع بعض، الحد الأقصى ألف جنيه.")
        merged = merge_preferences(self.conv, intent)
        self.assertEqual(merged["bottle_type"], "normal")
        self.assertEqual(merged["requested_volume"], 90)

    def test_L04_total_budget_cannot_be_used_per_bottle(self):
        from products.services.search_service import search_products
        from products.services.ai.recommendation import recommend
        intent = {"max_price": 1000, "budget_scope": "total", "purchase_quantity": 2, "requested_volume": 90}
        found = search_products(intent, self.store)
        reply, _ = recommend("", found["products"], intent=intent, store=self.store)
        self.assertIn("إجمالية 1000", reply)
        self.assertNotIn("944", reply)
        self.assertNotIn("700", reply)

    def test_L04_literal_total_is_not_divided_by_bottle_count(self):
        from products.services.ai.intent import extract_intent
        for amount in ("1000", "1,000", "١٠٠٠"):
            with patch("products.services.ai.intent.chat", return_value=json.dumps({"max_price": 500, "purchase_quantity": 2})):
                intent = extract_intent(f"معايا {amount} جنيه إجمالي للاتنين مع بعض، مش لكل واحدة.")
            self.assertEqual(intent["max_price"], 1000)
            self.assertEqual(intent["purchase_quantity"], 2)
            self.assertEqual(intent["budget_scope"], "total")

    def test_L05_requested_size_and_exact_ceiling_filter_same_variant(self):
        from products.services.search_service import search_products
        from products.services.ai.recommendation import recommend
        intent = {"max_price": 900, "budget_strict": True, "requested_volume": 90, "bottle_type": "normal"}
        found = search_products(intent, self.store)
        self.assertEqual(list(found["products"].values_list("name", flat=True)), [self.b.name])
        reply, _ = recommend("", found["products"], intent=intent)
        self.assertIn("700", reply)
        self.assertNotIn("50 مل", reply)
        self.assertNotIn("944", reply)

    def test_approximate_budget_keeps_disclosed_flexibility(self):
        from products.services.ai.recommendation import recommend
        reply, _ = recommend("", [self.a], intent={"requested_volume": 90, "max_price": 900, "budget_strict": False})
        self.assertIn("944", reply)
        self.assertIn("أعلى حاجة بسيطة", reply)

    def test_L06_no_unverified_sweetness_winner(self):
        from products.services.comparison_service import compare_products
        reply, _ = compare_products("Vanilo وLeatherio أنهي أقل حلاوة؟", store=self.store)
        self.assertIn("ماقدرش أجزم", reply)

    def test_L07_all_products_dimensions_and_computed_value(self):
        from products.services.comparison_service import compare_products
        reply, _ = compare_products("قارن Vanilo وLeatherio سعر 50 مل زجاجة البراند والثبات والفوحان وسعر الملي", store=self.store)
        for expected in ("Vanilo", "Leatherio", "607", "645", "12.14", "12.90", "8 hours", "6 hours", "Strong", "Moderate"):
            self.assertIn(expected, reply)

    def test_L09_explicit_false_replaces_true_and_survives_omission(self):
        merge_preferences(self.conv, {"wants_uncommon": True})
        merge_preferences(self.conv, {"wants_uncommon": False, "clear_preferences": ["wants_uncommon"]})
        merged = merge_preferences(self.conv, {"gender": "male"})
        self.assertIs(merged["wants_uncommon"], False)

    def test_L10_new_recipient_clears_previous_requirements(self):
        merge_preferences(self.conv, {"max_price": 600, "projection": "strong", "gender": "male"})
        merged = merge_preferences(self.conv, {"reset_preferences": True, "gender": "female", "season": "summer"})
        self.conv.refresh_from_db()
        self.assertNotIn("max_price", self.conv.preferences)
        self.assertNotIn("projection", merged)

    def test_L08_L13_formulation_control_uses_store_facts(self):
        from products.services.router import route
        StaticFAQ.objects.create(store=self.store, question="Formulation", keywords="اصليه", answer="العطور كلها تركيب، شكل الزجاجة مختلف بس.")
        reply, _ = route("قولي نوع السائل ومين اللي مصنّعه؟", [], self.store, self.conv)
        self.assertIn("العطور كلها تركيب", reply)
        self.assertIn("محتاج تأكيد", reply)

    def test_two_recommendations_do_not_mean_two_purchased_bottles(self):
        from products.services.sales.constraints import explicit_updates
        updates = explicit_updates("عايز عطر رجالي في حدود 1000 جنيه. رشحلي اختيارين بأسعارهم.")
        self.assertEqual(updates["budget_scope"], "per_item")
        self.assertIsNone(updates["purchase_quantity"])

    def test_L11_ordered_reference_survives_four_faqs(self):
        from products.services.sales.described import offered_in_order
        save_message(self.conv, "assistant", "Vanilo ثم Leatherio", internal_context="Vanilo Leatherio")
        for _ in range(4):
            save_message(self.conv, "assistant", "التوصيل خلال أربعة أيام")
        self.assertEqual(offered_in_order(self.conv, self.store), ["Vanilo", "Leatherio"])

    def test_L12_checkout_details_before_faq(self):
        from products.services.router import route
        self.turn([self.line()], customer_phone=None, customer_secondary_phone=None)
        StaticFAQ.objects.create(store=self.store, question="shipping", keywords="بتوصل", answer="بنوصّل لكل المحافظات")
        data = {"products": [], "customer_phone": "01000000000", "customer_secondary_phone": "01100000000"}
        with patch("products.services.order_service.chat", return_value=json.dumps(data)), patch("products.services.router.record_llm_message"):
            reply, _ = route("رقم موبايلي 01000000000 والرقم البديل 01100000000. وبتوصلوا للمحافظات؟", [], self.store, self.conv)
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.secondary_phone, "01100000000")
        save_message(self.conv, "assistant", reply)
        self.turn(is_confirmed=True)
        self.assertEqual(Order.objects.get().secondary_phone, "01100000000")
        self.assertIn("بنوصّل", reply)

    def test_E01_other_products_price_is_still_wrong(self):
        from eval_harness.checks import build_ground_truth, check_variant_prices
        truth = build_ground_truth(self.store)
        wrong = check_variant_prices("Vanilo — الـ50 مل بـ645 جنيه.", truth, "Vanilo 50 مل زجاجة البراند بكام؟")
        self.assertEqual(wrong[0][0], "wrong_variant_price")
        self.assertFalse(check_variant_prices("Vanilo — 50 مل بـ607 جنيه؛ سعر المل: 12.14 جنيه", truth))


class MessageBugTests(BugFixture):
    def test_generation_failure_rolls_back_state_then_retries_once(self):
        def fail(text, history, store, conversation):
            Cart.objects.create(conversation=conversation, customer_name="Uncommitted")
            raise RuntimeError("injected generation failure")
        payload = {"message": "hello", "client_message_id": "retry-generation"}
        with patch("products.views.route", side_effect=fail):
            self.assertEqual(self.client.post("/api/chat/", payload).status_code, 500)
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Cart.objects.exists())
        with patch("products.views.route", return_value=("reply", "")):
            self.assertEqual(self.client.post("/api/chat/", payload).status_code, 200)
        self.assertEqual(InboundEvent.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)

    def test_stale_sending_is_flagged_without_sending_again(self):
        from products.tasks import process_incoming_message
        from products.services.conversation_service import generate_event
        event = receive_event(self.store, "messenger", "customer", "hello", "stale-send")
        generate_event(event, lambda *args: ("reply", ""), lambda reply, _: reply)
        Message.objects.filter(pk=event.reply_message_id).update(delivery_status="sending")
        with patch("products.tasks.rate_limit.hit_all", return_value=(True, 0, None)), patch("products.tasks.send_platform_message") as send:
            process_incoming_message.run(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
            process_incoming_message.run(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
        send.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.reply_message.delivery_status, "uncertain")
        self.assertEqual(Notification.objects.filter(type="delivery_failed").count(), 1)

    def test_partial_image_delivery_keeps_outcome_and_never_resends(self):
        from products.tasks import process_incoming_message
        from products.services.meta_service import UncertainDelivery
        StoreSettings.objects.filter(store=self.store).update(messenger_access_token="test", facebook_page_id="test", bottle_image_url="https://example.test/bottle.png")
        event = receive_event(self.store, "messenger", "customer", "bottles", "partial-send")
        with patch("products.tasks.rate_limit.hit_all", return_value=(True, 0, None)), patch("products.tasks.route", return_value=("[SEND_BOTTLE_IMAGE] bottles", "")), patch("products.services.meta_service.send_messenger_image", return_value={"message_id": "image-id"}) as image, patch("products.services.meta_service.send_messenger_message", side_effect=UncertainDelivery()) as send:
            process_incoming_message.run(self.store.pk, "messenger", "customer", "bottles", event_id=event.pk)
            process_incoming_message.run(self.store.pk, "messenger", "customer", "bottles", event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(image.call_count, 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(event.reply_message.delivery_parts["image"]["status"], "sent")
        self.assertEqual(event.reply_message.delivery_parts["text"]["status"], "uncertain")
        self.assertTrue(event.conversation.needs_human)

    def test_unsent_recommendation_keeps_previous_delivered_choices(self):
        from products.services.sales.described import offered_in_order
        from products.services.conversation_service import remember_offered
        save_message(self.conv, "assistant", "Vanilo, Leatherio", internal_context="Vanilo Leatherio")
        pending = save_message(self.conv, "assistant", "Leatherio, Vanilo", internal_context="Vanilo Leatherio", delivery_status="pending")
        for _ in range(4):
            save_message(self.conv, "assistant", "FAQ answer")
        self.assertEqual(offered_in_order(self.conv, self.store), ["Vanilo", "Leatherio"])
        pending.delivery_status = "sent"
        pending.save(update_fields=["delivery_status"])
        remember_offered(self.conv, pending)
        self.assertEqual(offered_in_order(self.conv, self.store), ["Leatherio", "Vanilo"])

    def test_legacy_deferral_keeps_original_delivery_identity(self):
        from products.tasks import process_incoming_message
        with patch("products.tasks.rate_limit.hit_all", return_value=(False, 10, "sender")), patch.object(process_incoming_message, "apply_async") as enqueue:
            process_incoming_message.run(self.store.pk, "messenger", "customer", "hello", source_id="original-source")
        self.assertEqual(enqueue.call_args.kwargs["kwargs"]["source_id"], "original-source")

    def test_D01_first_request_is_deduplicated_without_token(self):
        with patch("products.views.route", return_value=("reply", "")) as mocked:
            first = self.client.post("/api/chat/", {"message": "hello", "client_message_id": "same"})
            second = self.client.post("/api/chat/", {"message": "hello", "client_message_id": "same"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data, second.data)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(self.client.post("/api/chat/", {"message": "changed", "client_message_id": "same"}).status_code, 409)

    def test_D02_accepted_events_use_complete_ordered_history(self):
        from products.tasks import process_incoming_message
        first = receive_event(self.store, "messenger", "customer", "one", "1")
        second = receive_event(self.store, "messenger", "customer", "two", "2")
        histories = []
        def answer(text, history, *args):
            histories.append(history)
            return text + " reply", ""
        with patch("products.tasks.rate_limit.hit_all", return_value=(True, 0, None)), patch("products.tasks.route", side_effect=answer), patch("products.tasks.send_platform_message", return_value=True):
            process_incoming_message.run(self.store.pk, "messenger", "customer", "two", event_id=second.pk)
        self.assertEqual([m["content"] for m in histories[1]], ["one", "one reply"])
        self.assertEqual(list(Message.objects.order_by("id").values_list("content", flat=True)), ["one", "one reply", "two", "two reply"])

    def test_D03_uncertain_send_never_recreates_or_resends_turn(self):
        from products.tasks import process_incoming_message
        event = receive_event(self.store, "messenger", "customer", "hello", "external-id")
        with patch("products.tasks.rate_limit.hit_all", return_value=(True, 0, None)), patch("products.tasks.route", return_value=("reply", "")) as generate, patch("products.tasks.send_platform_message", side_effect=TimeoutError()) as send:
            process_incoming_message.run(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
            process_incoming_message.run(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(Message.objects.count(), 2)
        event.refresh_from_db()
        self.assertEqual(event.reply_message.delivery_status, "uncertain")
        self.assertTrue(event.conversation.needs_human)
        self.assertEqual(build_llm_history(event.conversation), [{"role": "user", "content": "hello"}])

    def test_D04_public_cursor_returns_staff_reply_only_for_owner_token(self):
        from products.views import signer
        self.conv.needs_human = True
        self.conv.save()
        staff = save_message(self.conv, "agent", "Staff reply", internal_context="private")
        token = signer.sign_object(self.conv.pk)
        result = self.client.get("/api/chat/messages/", {"conversation_id": token})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["messages"][0]["content"], "Staff reply")
        self.assertNotIn("internal_context", result.data["messages"][0])
        self.assertEqual(self.client.get("/api/chat/messages/", {"conversation_id": token, "after_id": staff.pk}).data["messages"], [])
        other = Store.objects.create(name="Other")
        self.client.credentials(HTTP_X_API_KEY=other.api_key)
        self.assertEqual(self.client.get("/api/chat/messages/", {"conversation_id": token}).status_code, 404)


@skipUnless(connection.vendor == "postgresql", "Row/advisory locks require PostgreSQL")
class ConcurrentBugTests(TransactionTestCase):
    setUp = BugFixture.setUp
    turn = BugFixture.turn
    line = BugFixture.line

    def test_concurrent_first_web_retry_generates_one_turn(self):
        barrier = Barrier(2)
        def post():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_X_API_KEY=self.store.api_key)
                barrier.wait(timeout=10)
                response = client.post("/api/chat/", {"message": "hello", "client_message_id": "same-first-request"})
                return response.status_code, response.data
            finally:
                close_old_connections()
        with patch("products.views.route", return_value=("reply", "")) as generate:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: post(), range(2)))
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[0][0], 200)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(InboundEvent.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)

    def test_C03_two_approvals_create_one_order(self):
        self.turn([self.line(volume=100, bottle_type="original")])
        barrier = Barrier(2)
        extracted = {"products": [], "is_confirmed": True}
        def confirm():
            close_old_connections()
            try:
                conv = Conversation.objects.get(pk=self.conv.pk)
                store = Store.objects.get(pk=self.store.pk)
                barrier.wait(timeout=10)
                return handle_order("تمام", [], store, conv)[0]
            finally:
                close_old_connections()
        with patch("products.services.order_service.chat", return_value=json.dumps(extracted)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                replies = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(replies[0], replies[1])
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 1)

    def test_concurrent_cancellations_restore_stock_once(self):
        self.turn([self.line(volume=100, bottle_type="original")])
        self.turn(is_confirmed=True)
        order = Order.objects.get()
        barrier = Barrier(2)
        def cancel():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                change_order_status(order.pk, self.store, "cancelled")
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: cancel(), range(2)))
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 2)

    def test_D02_concurrent_web_messages_wait_for_previous_reply(self):
        from products.views import signer
        started, release = Event(), Event()
        histories = []
        def answer(text, history, *args):
            histories.append((text, history))
            if text == "first":
                started.set()
                self.assertTrue(release.wait(timeout=10))
            return text + " reply", ""
        token = signer.sign_object(self.conv.pk)
        def post(text):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_X_API_KEY=self.store.api_key)
                return client.post("/api/chat/", {"message": text, "client_message_id": text, "conversation_id": token}).status_code
            finally:
                close_old_connections()
        with patch("products.views.route", side_effect=answer):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(post, "first")
                self.assertTrue(started.wait(timeout=10))
                second = pool.submit(post, "second")
                release.set()
                self.assertEqual([first.result(), second.result()], [200, 200])
        self.assertEqual([h[0] for h in histories], ["first", "second"])
        self.assertEqual(histories[1][1][-1]["content"], "first reply")
