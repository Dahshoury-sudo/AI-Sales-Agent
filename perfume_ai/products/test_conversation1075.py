"""Contextual checkout regressions; customer text is independent of model output."""
import copy
import json
from decimal import Decimal
from unittest.mock import patch

from products.test_bug_regressions import BugFixture
from products.models import Cart, Order, Product, ProductVariant, Store, Message
from products.services.conversation_service import save_message, build_llm_history
from products.services.order_service import _ground_order_lines


class Conversation1075Tests(BugFixture):
    purchase = "هات واحده اوريجنال 100 ملي"

    def setUp(self):
        super().setUp()
        self.a.name = "Dior Sauvage"
        self.a.save()
        self.av.price = Decimal("642")
        self.av.save()
        self.original.price = Decimal("815")
        self.original.save()

    def offer(self, products=None, **extra):
        from products.services.product_formatting import format_products
        products = products or [self.a]
        return save_message(self.conv, "assistant", "، ".join(p.name for p in products),
                            internal_context=format_products(products), **extra)

    def ground(self, text, products):
        cart, _ = Cart.objects.get_or_create(conversation=self.conv)
        data = {"products": copy.deepcopy(products), "is_confirmed": False}
        _ground_order_lines(data, text, cart, self.conv, self.store)
        return data["products"]

    def routed(self, text, products=None, intent="order", **changes):
        from products.services.router import route
        from products.services.reply_sanitizer import sanitize_reply
        data = {"products": products or [], "is_confirmed": False}
        data.update(changes)
        history = build_llm_history(self.conv)
        save_message(self.conv, "user", text)
        with patch("products.services.router.classify", return_value=intent), \
             patch("products.services.order_service.chat", return_value=json.dumps(data)), \
             patch("products.services.product_info.resolve_products", return_value=[self.a]), \
             patch("products.services.product_info.chat", return_value="Dior Sauvage متوفر."), \
             patch("products.services.general_service.chat", return_value="تمام، هحوّلك لمندوب يساعدك."), \
             patch("products.services.product_resolver.chat", return_value=json.dumps({"name": self.a.name})):
            reply, context = route(text, history, self.store, self.conv)
            reply = sanitize_reply(reply, self.conv)
        save_message(self.conv, "assistant", reply, internal_context=context)
        return reply

    def assert_cart(self, quantity=1, variant=None):
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.count(), 1)
        item = cart.items.get()
        self.assertEqual(item.variant_id, (variant or self.original).pk)
        self.assertEqual(item.quantity, quantity)
        self.assertFalse(cart.pending_items)
        self.assertFalse(Order.objects.exists())
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 2)
        return item

    def legacy_pending(self, count=1):
        # The recorded incident already had a verified price answer to this name.
        save_message(self.conv, "user", "بكام سوفاج")
        self.offer()
        lines = [{"name": self.purchase, "source_text": self.purchase, "resolution_status": "ambiguous",
                  "line_id": "legacy-1075-" + str(i), "volume": 100, "bottle_type": "original", "quantity": 1}
                 for i in range(count)]
        Cart.objects.create(conversation=self.conv, pending_items=lines)
        save_message(self.conv, "assistant", "ممكن تكتب اسم العطر تاني أو تختار بديل؟ مش هأكد الطلب قبل ما نحدده.",
                     internal_context="No products found")
        return lines

    def test_incident_replay_preserves_single_original_bottle(self):
        self.routed("بكام سوفاج", intent="product_info")
        line = {**self.line(self.a, 100, bottle_type="original"), "source_text": self.purchase}
        reply = self.routed(self.purchase, [line])
        self.assert_cart()
        self.assertIn("815", reply)
        self.assertNotIn("اسم العطر", reply)
        self.routed("سوفاج", intent="product_info")
        repeated = self.routed("عايز واحده قولت", [line], intent="recommendation")
        self.assert_cart()
        self.assertNotEqual(repeated, reply)
        self.assertIn("مسجل لك", repeated)

    def test_implicit_purchase_cannot_be_routed_to_product_info_or_recommendations(self):
        self.offer()
        for intent in ("product_info", "recommendation", "faq"):
            with self.subTest(intent=intent):
                reply = self.routed(self.purchase, [], intent=intent)
                self.assert_cart()
                self.assertIn("815", reply)
                self.assertNotIn("اسم العطر", reply)

    def test_product_name_and_implicit_purchase_do_not_supply_contact_details(self):
        self.routed("بكام سوفاج", intent="product_info")
        self.routed(self.purchase, customer_name="Invented", customer_phone="01000000000")
        self.routed("سوفاج", intent="order", customer_name="سوفاج", shipping_address="Invented address")
        cart = Cart.objects.get(conversation=self.conv)
        self.assertFalse(cart.customer_name)
        self.assertFalse(cart.customer_phone)
        self.assertFalse(cart.shipping_address)
        self.assert_cart()

    def test_repeated_selection_keeps_the_budget_warning(self):
        self.conv.preferences = {"max_price": 900}
        self.conv.save()
        self.offer()
        self.routed("هات اتنين اوريجنال 100 ملي")
        reply = self.routed("عايز اتنين قولت")
        self.assert_cart(quantity=2)
        self.assertIn("1630", reply)
        self.assertIn("900", reply)
        self.assertIn("⚠️", reply)

    def test_context_does_not_depend_on_extractor_source_or_name(self):
        self.offer()
        for source in (self.purchase, None, "", "not in the message"):
            for name in (self.a.name, self.b.name, self.purchase):
                with self.subTest(source=source, name=name):
                    lines = self.ground(self.purchase, [{"name": name, "source_text": source, "quantity": 9,
                                                        "volume": 50, "bottle_type": "normal"}])
                    self.assertEqual(len(lines), 1)
                    self.assertEqual(lines[0]["product_id"], self.a.pk)
                    self.assertEqual((lines[0]["quantity"], lines[0]["volume"], lines[0]["bottle_type"]), (1, 100, "original"))
                    self.assertEqual(lines[0]["source_text"], self.purchase)

    def test_empty_or_multiple_extractor_products_do_not_change_selection(self):
        self.offer()
        for products in ([], None, [self.line(self.a), self.line(self.b)]):
            with self.subTest(products=products):
                lines = self.ground(self.purchase, products)
                self.assertEqual(len(lines), 1)
                self.assertEqual(lines[0]["product_id"], self.a.pk)

    def test_arabic_spelling_digits_and_quantity(self):
        self.offer()
        for text, quantity in (("هات واحدة أوريجينال ١٠٠ مل", 1), ("عاوز واحده اورجينال 100 ملي", 1),
                               ("هات 2 زجاجة اوريجنال ١٠٠ ملي", 2), ("عايز اتنين اوريجنال 100 ملي", 2)):
            with self.subTest(text=text):
                lines = self.ground(text, [])
                self.assertEqual(lines[0]["product_id"], self.a.pk)
                self.assertEqual((lines[0]["quantity"], lines[0]["volume"]), (quantity, 100))

    def test_legacy_clarification_updates_same_line_even_if_extractor_omits_it(self):
        lines = self.legacy_pending()
        self.routed("سوفاج", [], intent="product_info")
        item = self.assert_cart()
        self.assertEqual(item.line_id, lines[0]["line_id"])

    def test_legacy_retry_uses_recent_verified_offer(self):
        self.offer()
        lines = self.legacy_pending()
        self.routed("عايز واحده قولت", lines)
        self.assertEqual(self.assert_cart().line_id, lines[0]["line_id"])

    def test_multiple_pending_lines_are_not_replaced_by_one_name(self):
        self.legacy_pending(2)
        self.routed("سوفاج", [self.line(self.a, 100, bottle_type="original")], intent="product_info")
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.count(), 0)
        self.assertEqual(len(cart.pending_items), 2)

    def test_multiple_offers_require_identity_and_retain_options(self):
        self.offer([self.a, self.b])
        reply = self.routed(self.purchase, [self.line(self.a)])
        cart = Cart.objects.get(conversation=self.conv)
        self.assertFalse(cart.items.exists())
        self.assertEqual(len(cart.pending_items), 1)
        self.assertEqual(cart.pending_items[0]["volume"], 100)
        self.assertNotIn("1 × " + self.purchase, reply)

    def test_no_offer_or_stale_offer_never_accepts_model_identity(self):
        for offered in (False, True):
            with self.subTest(offered=offered):
                if offered:
                    self.offer()
                    for _ in range(3):
                        save_message(self.conv, "assistant", "تحت أمرك")
                lines = self.ground(self.purchase, [self.line(self.a)])
                self.assertIsNone(lines[0].get("product_id"))

    def test_undelivered_and_other_store_offers_are_ignored(self):
        self.offer([self.b], delivery_status="pending")
        other = Store.objects.create(name="Other 1075")
        foreign = Product.objects.create(store=other, brand=self.a.brand, name="Foreign scent")
        self.offer([foreign])
        lines = self.ground(self.purchase, [self.line(foreign)])
        self.assertIsNone(lines[0].get("product_id"))

    def test_explicit_unknown_name_is_not_replaced_by_the_offer(self):
        self.offer()
        lines = self.ground("هات Mystery Azure 100 ملي", [self.line(self.a, 100)])
        self.assertIsNone(lines[0].get("product_id"))
        self.assertNotEqual(lines[0]["name"], self.a.name)

    def test_explicit_inactive_name_is_preserved(self):
        self.offer()
        self.b.is_active = False
        self.b.save()
        lines = self.ground("هات Leatherio 100 ملي", [{**self.line(self.a), "source_text": "Leatherio"}])
        self.assertEqual(lines[0]["product_id"], self.b.pk)
        self.assertEqual(lines[0]["resolution_status"], "unavailable")

    def test_questions_do_not_repair_or_mutate_pending_cart(self):
        original = self.legacy_pending()
        for text in ("سوفاج بكام؟", "سوفاج ريحته ايه؟", "سوفاج متوفر؟"):
            with self.subTest(text=text):
                self.routed(text, intent="product_info")
                self.assertEqual(Cart.objects.get(conversation=self.conv).pending_items, original)

    def test_bare_name_browsing_does_not_start_checkout(self):
        self.routed("سوفاج", intent="product_info")
        self.assertFalse(Cart.objects.filter(conversation=self.conv).exists())

    def test_original_stock_shortage_does_not_substitute_packaging(self):
        self.offer()
        self.original.stock = 0
        self.original.save()
        self.routed(self.purchase, [])
        cart = Cart.objects.get(conversation=self.conv)
        self.assertFalse(cart.items.exists())
        self.assertEqual(cart.pending_items[0]["bottle_type"], "original")
        self.assertEqual(cart.pending_items[0]["volume"], 100)

    def test_repaired_line_does_not_authorize_confirmation(self):
        self.legacy_pending()
        self.routed("سوفاج", [], intent="product_info", is_confirmed=True)
        self.assert_cart()

    def test_closed_checkout_offer_is_not_reused(self):
        self.offer()
        save_message(self.conv, "assistant", "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية؟")
        lines = self.ground(self.purchase, [self.line(self.a)])
        self.assertIsNone(lines[0].get("product_id"))

    def test_clarification_from_multiple_offers_preserves_requested_variant(self):
        self.offer([self.a, self.b])
        self.routed(self.purchase)
        pending = Cart.objects.get(conversation=self.conv).pending_items[0]
        self.routed("Dior Sauvage", [], intent="product_info")
        self.assertEqual(self.assert_cart().line_id, pending["line_id"])

    def test_alias_history_never_bypasses_new_flanker_ambiguity(self):
        self.legacy_pending()
        Product.objects.create(store=self.store, brand=self.a.brand, name="Dior Sauvage Elixir")
        self.routed("سوفاج", [], intent="product_info")
        self.assertFalse(Cart.objects.get(conversation=self.conv).items.exists())

    def test_confirmation_is_never_read_as_a_name_from_history(self):
        from products.services.order_service import _checkout_name_from_history
        save_message(self.conv, "user", "بكام")
        self.offer()
        self.assertIsNone(_checkout_name_from_history("تمام", self.conv, self.store))

    def test_unknown_clarification_does_not_accept_extractor_guess(self):
        self.legacy_pending()
        self.routed("Mystery Azure", [self.line(self.a)], intent="product_info")
        cart = Cart.objects.get(conversation=self.conv)
        self.assertFalse(cart.items.exists())
        self.assertEqual(cart.pending_items[0]["name"], "Mystery Azure")

    def test_existing_named_unavailable_line_is_not_replaced_by_context(self):
        self.offer()
        self.b.is_active = False
        self.b.save()
        Cart.objects.create(conversation=self.conv, pending_items=[{
            "name": self.b.name, "source_text": self.b.name, "resolution_status": "unavailable",
            "quantity": 1, "line_id": "unavailable", "volume": 100, "bottle_type": "original"}])
        self.routed(self.purchase, [self.line(self.a)])
        cart = Cart.objects.get(conversation=self.conv)
        self.assertFalse(cart.items.exists())
        self.assertEqual(cart.pending_items[0]["name"], self.b.name)
        self.assertEqual(cart.pending_items[0]["resolution_status"], "unavailable")

    def test_explicit_ordinal_selection_still_uses_the_requested_product(self):
        self.offer([self.b, self.a])
        self.routed("هات التاني اوريجنال 100 ملي", [])
        self.assert_cart()

    def test_missing_bottle_type_is_not_invented_from_model_output(self):
        self.offer()
        # The same volume exists in both bottle types, so size alone cannot choose.
        ProductVariant.objects.create(product=self.a, volume=100, bottle_type="normal", price=700)
        self.routed("هات واحدة 100 ملي", [self.line(self.a, 100, bottle_type="original")])
        cart = Cart.objects.get(conversation=self.conv)
        self.assertFalse(cart.items.exists())
        self.assertIsNone(cart.pending_items[0].get("bottle_type"))

    def test_size_edit_keeps_identity_and_does_not_add_a_line(self):
        self.offer()
        self.routed(self.purchase)
        identifier = self.assert_cart().line_id
        self.routed("50 ملي زجاجة البراند", [])
        self.assertEqual(self.assert_cart(variant=self.av).line_id, identifier)

    def test_pending_cancellation_and_handoff_keep_their_routes(self):
        self.legacy_pending()
        self.routed("الغي الطلب", intent="order_cancel")
        self.assertFalse(Cart.objects.filter(conversation=self.conv).exists())
        self.legacy_pending()
        with patch("products.services.router.notify_handoff"):
            self.routed("عايز اكلم مندوب", intent="handoff")
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_human)
        self.assertFalse(Cart.objects.get(conversation=self.conv).items.exists())

    def test_prices_and_stock_are_checked_again_before_confirmation(self):
        self.offer()
        self.routed(self.purchase)
        self.assert_cart()
        details = {"customer_name": "Test", "customer_phone": "01000000000",
                   "customer_secondary_phone": "01100000000", "shipping_address": "Test address"}
        self.routed("اسمي Test", **details)
        self.assertFalse(Order.objects.exists())
        self.original.price = Decimal("900")
        self.original.save()
        reply = self.routed("تمام اكد الطلب", is_confirmed=True)
        self.assertIn("900", reply)
        self.assertFalse(Order.objects.exists())
        self.routed("تمام اكد الطلب", is_confirmed=True)
        order = Order.objects.get(conversation=self.conv)
        self.assertEqual(order.total_price, Decimal("900"))
        self.assertEqual(order.items.get().variant_id, self.original.pk)
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 1)
        self.routed("تمام اكد الطلب", is_confirmed=True)
        self.assertEqual(Order.objects.filter(conversation=self.conv).count(), 1)
        self.original.refresh_from_db()
        self.assertEqual(self.original.stock, 1)
        # A new vague request cannot reopen this completed purchase.
        self.routed(self.purchase)
        self.assertFalse(Cart.objects.get(conversation=self.conv).items.exists())

    def test_duplicate_web_event_does_not_duplicate_the_cart(self):
        from products.views import signer
        self.offer()
        payload = {"message": self.purchase, "client_message_id": "1075-purchase",
                   "conversation_id": signer.sign_object(self.conv.pk)}
        with patch("products.services.router.classify", return_value="order"), \
             patch("products.services.order_service.chat", return_value='{"products": [], "is_confirmed": false}') as extractor:
            first = self.client.post("/api/chat/", payload)
            second = self.client.post("/api/chat/", payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data, second.data)
        self.assertEqual(extractor.call_count, 1)
        self.assert_cart()

    def test_evaluation_rejects_pending_only_or_wrong_variant_cart(self):
        from eval_harness.checks import check_expected_cart
        expected = {"items": [{"name": self.a.name, "volume": 100, "bottle_type": "original", "quantity": 1}],
                    "pending_count": 0, "order_count": 0}
        state = {"items": copy.deepcopy(expected["items"]), "pending": [], "order_count": 0}
        self.assertFalse(check_expected_cart(state, expected))
        for field, value in (("name", self.b.name), ("volume", 50), ("bottle_type", "normal"), ("quantity", 2)):
            broken = copy.deepcopy(state)
            broken["items"][0][field] = value
            self.assertTrue(check_expected_cart(broken, expected))
        self.assertTrue(check_expected_cart({"items": [], "pending": [{"quantity": 1}], "order_count": 0}, expected))
        self.assertTrue(check_expected_cart({**state, "order_count": 1}, expected))
        self.assertTrue(check_expected_cart({**state, "reply_kind": "other"}, {**expected, "reply_kind": "checkout"}))
        self.assertTrue(check_expected_cart({**state, "contact_fields": ["customer_name"]}, {**expected, "contact_fields": []}))
