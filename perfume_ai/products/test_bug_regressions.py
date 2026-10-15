"""Regressions for artifacts/bug_reproductions; all providers are simulated."""
import copy
import json
from decimal import Decimal
from unittest.mock import patch, Mock

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


class Conversation1069Tests(BugFixture):
    def setUp(self):
        super().setUp()
        self.a.name = "Ambero"
        self.a.save()
        self.b.name = "Afnan 9PM"
        self.b.save()
        self.av.price = Decimal("642")
        self.av.save()
        self.bv.price = Decimal("508")
        self.bv.save()
        self.b90.price = Decimal("1077")
        self.b90.save()
        self.b_original = ProductVariant.objects.create(product=self.b, volume=100, price=881, bottle_type="original", stock=1)

    def offered(self, products=None, content=None, **extra):
        from products.services.product_formatting import format_products
        products = products or [self.a, self.b]
        return save_message(self.conv, "assistant", content or "، ".join(p.name for p in products),
                            internal_context=format_products(products), **extra)

    def info(self, message, history=None):
        from products.services.product_info import get_product_info
        with patch("products.services.product_info.chat", return_value="وصف بسيط."), patch("products.services.product_info.resolve_products", return_value=[]):
            reply, context = get_product_info(message, history or [], self.store, self.conv)
        save_message(self.conv, "assistant", reply, internal_context=context)
        return reply, context

    def test_1069_replay_and_arabic_checkout(self):
        from products.services.product_formatting import quote_evidence
        self.offered()
        first, _ = self.info("الأول بكام؟")
        self.assertIn("Ambero", first)
        second, context = self.info("طب والتاني")
        self.assertIn("Afnan 9PM", second)
        for price in ("508", "1077", "881"):
            self.assertIn(price, second)
        self.assertNotIn("642", second)
        self.assertEqual({q["product_id"] for q in quote_evidence(context)["quotes"]}, {self.b.pk})
        self.assertFalse(Order.objects.exists())

    def test_list_order_survives_followups_and_undelivered_turns(self):
        from products.services.sales.described import resolve_ordinal
        self.offered([self.b, self.a])
        self.offered([self.b])
        self.offered([self.b])
        self.offered([self.a, self.b], delivery_status="pending")
        for word in ("التاني", "الثاني", "التانية", "الأخير"):
            self.assertEqual(resolve_ordinal(word, self.conv, self.store), self.a.name)
        self.assertEqual(resolve_ordinal("الأول", self.conv, self.store), self.b.name)
        self.offered([Product.objects.create(store=self.store, brand=self.a.brand, name="New Topic")])
        self.assertIsNone(resolve_ordinal("التاني", self.conv, self.store))

    def test_cart_summary_is_not_a_recommendation_and_window_is_bounded(self):
        from products.services.sales.described import resolve_ordinal
        self.offered()
        save_message(self.conv, "assistant", "Afnan 9PM، Ambero", internal_context="Afnan 9PM (90 ملي) x 1 (1077.00 EGP)")
        self.assertEqual(resolve_ordinal("التاني", self.conv, self.store), self.b.name)
        for _ in range(8):
            save_message(self.conv, "assistant", "تحت أمرك")
        self.assertIsNone(resolve_ordinal("التاني", self.conv, self.store))

    def test_explicit_name_beats_ordinal_and_missing_position_asks(self):
        self.offered()
        reply, _ = self.info("التاني Ambero بكام")
        self.assertIn("Ambero", reply)
        self.assertNotIn("9PM", reply)
        self.conv.messages.all().delete()
        reply, _ = self.info("الثاني بكام")
        self.assertIn("تقصد", reply)

    def test_numeric_names_sizes_flankers_and_store_isolation(self):
        from products.services.product_resolver import resolve_order_name
        from products.services.sales.naming import tokens
        for spelling in ("9 بي ام", "٩ بي إم", "9 pm", "9PM"):
            self.assertIn("9pm", tokens(spelling))
            self.assertEqual(resolve_order_name("عايز " + spelling + " ٩٠ ملي", self.store), self.b)
        other = Store.objects.create(name="Other")
        self.assertIsNone(resolve_order_name("9 بي ام", other))
        flanker = Product.objects.create(store=self.store, brand=self.b.brand, name="Afnan 9PM Rebel")
        self.assertIsNone(resolve_order_name("9 بي ام", self.store))
        self.assertEqual(resolve_order_name("9 بي ام Rebel", self.store), flanker)
        from products.services.product_info import get_product_info
        with patch("products.services.product_info.resolve_products", return_value=[self.b]) as resolver:
            reply, _ = get_product_info("9 بي ام بكام", [], self.store, self.conv)
        self.assertIn("أنهي إصدار", reply)
        resolver.assert_not_called()
        self.b.is_active = False
        self.b.save()
        self.assertEqual(resolve_order_name("Afnan 9PM", self.store), self.b)

    def test_numeric_price_question_respects_brand_bottle_wording(self):
        reply, context = self.info("9 بي ام ٩٠ ملي زجاجة البراند بكام؟")
        self.assertIn("Afnan 9PM", reply)
        self.assertIn("1077", reply)
        self.assertNotIn("881", reply)
        self.assertNotIn("508", reply)

    def test_ambiguous_numeric_objections_do_not_accept_a_model_guess(self):
        from products.services.objection_service import handle_objection, handle_price_dispute
        from products.services.sales.objection import detect
        Product.objects.create(store=self.store, brand=self.b.brand, name="Afnan 9PM Rebel")
        with patch("products.services.objection_service.resolve_products", return_value=[self.b]) as resolver:
            reply, _ = handle_objection("9 بي ام غالي", detect("غالي"), [], self.store, self.conv)
            self.assertIn("أنهي إصدار", reply)
            reply, _ = handle_price_dispute("9 بي ام، إنت قلت سعر تاني", self.store, self.conv)
            self.assertIn("أنهي إصدار", reply)
        resolver.assert_not_called()

    def test_inactive_numeric_product_is_not_replaced_by_an_active_flanker(self):
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        self.b.is_active = False
        self.b.save()
        Product.objects.create(store=self.store, brand=self.b.brand, name="Afnan 9PM Rebel")
        reply, _ = self.info("Afnan 9PM 90 ملي بكام؟")
        self.assertIn("مش متاح", reply)
        self.assertNotIn("Rebel", reply)
        with patch("products.services.objection_service.resolve_products") as resolver:
            reply, _ = handle_objection("Afnan 9PM غالي", detect("غالي"), [], self.store, self.conv)
        resolver.assert_not_called()
        self.assertIn("مش متاح", reply)
        self.assertNotIn("Rebel", reply)

    def test_unsupported_and_wrong_variant_drafts_fail_one_correction(self):
        from products.services.router import _rephrased
        from products.services.product_formatting import format_products
        context = format_products([self.b, self.a])
        for draft in ("Afnan 9PM 90 ملي بـ990 جنيه", "Afnan 9PM 50 ملي بـ642 جنيه",
                      "Afnan 9PM 90 ملي بـ508 جنيه", "Afnan 9PM 100 ملي أوريجينال بـ1077 جنيه"):
            retry = Mock(return_value=(draft, context))
            reply, _, _ = _rephrased(draft, context, [{"role": "assistant", "content": draft}], self.store, retry)
            retry.assert_called_once()
            self.assertNotIn("990", reply)
            self.assertNotIn("642", reply)
            self.assertIn("1077", reply)
        retry = Mock(return_value=("بـ990 جنيه", ""))
        reply, _, _ = _rephrased("بـ990 جنيه", "", [], self.store, retry)
        self.assertIn("تقصد", reply)

    def test_combined_question_attaches_code_quote_after_bad_rewrite(self):
        from products.services.router import route
        with patch("products.services.router.classify", return_value="product_info"), patch("products.services.product_info.chat", return_value="Afnan 9PM 90 ملي بـ990 جنيه، ثباته 6 ساعات."):
            reply, context = route("Afnan 9PM 90 ملي بكام وثباته؟", [], self.store, self.conv)
        self.assertNotIn("990", reply)
        self.assertIn("1077", reply)
        self.assertNotIn("508", reply)

    def test_requested_original_stock_and_size_are_respected(self):
        reply, _ = self.info("Afnan 9PM 100 ملي أوريجينال بكام")
        self.assertIn("881", reply)
        self.assertNotIn("508", reply)
        self.b_original.stock = 0
        self.b_original.save()
        reply, _ = self.info("Afnan 9PM 100 ملي أوريجينال بكام")
        self.assertNotIn("881", reply)
        self.assertIn("مش متاح", reply)

    def test_disputes_override_general_and_acknowledge_recorded_error(self):
        from products.services.router import route
        self.offered([self.b], "Afnan 9PM 90 ملي بـ990 جنيه")
        for message in ("مش قولت بـ990؟", "إنت قلت سعر تاني", "ليه السعر اتغير؟"):
            with patch("products.services.router.classify", return_value="general"), patch("products.services.objection_service.resolve_products", return_value=[]):
                reply, _ = route(message, [], self.store, self.conv)
            self.assertIn("1077", reply)
            self.assertIn("كان غلط", reply)
            self.assertNotIn("990", reply)

    def test_dispute_cart_priority_ambiguity_and_real_price_change(self):
        from products.services.objection_service import handle_price_dispute
        from products.services.product_formatting import ProductQuote, quote_context, render_quotes
        self.turn([self.line(self.b, 90)])
        self.offered([self.a], "Ambero 50 ملي بـ642 جنيه")
        reply, _ = handle_price_dispute("ليه السعر اتغير؟", self.store, self.conv)
        self.assertIn("1077", reply)
        self.turn([self.line(self.a)])
        reply, _ = handle_price_dispute("ليه السعر اتغير؟", self.store, self.conv)
        self.assertIn("تقصد", reply)
        self.conv.cart.delete()
        q = ProductQuote.from_variant(self.b90)
        save_message(self.conv, "assistant", render_quotes([q]), internal_context=quote_context("", [q]))
        self.b90.price = Decimal("1100")
        self.b90.save()
        reply, _ = handle_price_dispute("ليه السعر اتغير؟", self.store, self.conv)
        self.assertIn("1100", reply)
        self.assertNotIn("كان غلط", reply)

    def test_priced_recommendations_and_objections_use_verified_quotes(self):
        from products.services.ai.recommendation import recommend
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        from products.services.router import _rephrased
        from products.services.product_formatting import quote_evidence
        draft = "Afnan 9PM 90 ملي بـ990 جنيه"
        with patch("products.services.ai.recommendation.chat", return_value=draft):
            reply, context = recommend("رجالي سويت في حدود 1200", Product.objects.filter(pk=self.b.pk), [], store=self.store,
                                       intent={"max_price": 1200, "gender": "male"})
            retry = Mock(return_value=(reply, context))
            final, _, _ = _rephrased(reply, context, [], self.store, retry)
        self.assertTrue(quote_evidence(context)["quotes"])
        self.assertNotIn("990", final)
        self.assertIn("1077", final)
        with patch("products.services.objection_service.chat", return_value="فاهم إن السعر عالي."), patch(
            "products.services.objection_service.resolve_products", side_effect=AssertionError("An explicit name must resolve from the catalogue")
        ):
            reply, context = handle_objection("Afnan 9PM غالي", detect("غالي"), [], self.store, self.conv)
        self.assertIn("508", reply)
        self.assertTrue(quote_evidence(context)["quotes"])

    def test_nonproduct_money_is_scoped_to_its_recorded_subject(self):
        from products.services.product_formatting import product_price_issues
        from products.services.router import _rephrased
        config = self.store.settings
        config.business_facts = "الشحن 75 جنيه. عرض خصم 100 جنيه."
        config.save()
        reply = "الشحن 75 جنيه. عرض خصم 100 جنيه. ميزانيتك 900 جنيه."
        retry = Mock(return_value=(reply, ""))
        final, _, _ = _rephrased(reply, "", [{"role": "user", "content": "ميزانيتي 900 جنيه"}], self.store, retry)
        self.assertEqual(reply, final)
        retry.assert_not_called()
        self.assertTrue(product_price_issues("الشحن 100 جنيه", [], monetary_sources=[config.business_facts]))
        self.assertTrue(product_price_issues("Afnan 9PM 90 ملي بـ75 جنيه", [self.b], monetary_sources=[config.business_facts]))

    def test_quote_evidence_cannot_swap_variant_relationships(self):
        from products.services.product_formatting import ProductQuote, quote_context, quote_evidence, product_price_issues
        evidence = quote_evidence(quote_context("", [ProductQuote.from_variant(self.b90)]))
        evidence["quotes"][0]["variant_id"] = self.bv.pk
        self.assertTrue(product_price_issues(evidence["text"], [self.b], evidence=evidence))

    def test_explicit_cancel_and_handoff_survive_a_dispute(self):
        from products.services.router import route
        with patch("products.services.router.classify", return_value="order_cancel"), patch("products.services.objection_service.handle_price_dispute") as dispute:
            route("مش قولت بـ990؟ الغي الطلب", [], self.store, self.conv)
            dispute.assert_not_called()
        with patch("products.services.router.classify", return_value="handoff"), patch("products.services.router.notify_handoff"), patch("products.services.objection_service.handle_price_dispute") as dispute, patch(
            "products.services.general_service.chat", return_value="حولت المحادثة لفريق خدمة العملاء."
        ):
            route("إنت قلت سعر تاني، عايز اكلم حد حقيقي", [], self.store, self.conv)
            dispute.assert_not_called()
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_human)

    def test_ambiguous_and_unknown_numeric_flankers_ask(self):
        from products.services.product_resolver import resolve_order_name
        self.assertIsNone(resolve_order_name("Afnan 9 بي ام Unknown", self.store))
        from products.services.objection_service import handle_price_dispute
        self.offered([self.b], "Afnan 9PM 50 ملي بـ508 جنيه، 90 ملي بـ1077 جنيه")
        reply, _ = handle_price_dispute("ليه السعر اتغير؟", self.store, self.conv)
        self.assertIn("تقصد", reply)


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

    def test_an_inactive_name_is_never_swapped_for_a_stocked_perfume(self):
        """Live P2: 'امبيرو' was answered with Stronger With You Intensely and confirmed."""
        Product.objects.create(store=self.store, brand=self.a.brand, name="Ambero", is_active=False)
        with patch("products.services.order_service.resolve_product", return_value=self.a) as fallback:
            reply = self.turn([{"name": "Ambero", "volume": 90, "quantity": 1, "bottle_type": "normal"}],
                              message="عايز اطلب امبيرو 90 ملي")
        fallback.assert_not_called()
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.count(), 0)
        self.assertEqual([p["name"] for p in cart.pending_items], ["Ambero"])
        self.assertIn("مش متاح", reply)
        self.turn(is_confirmed=True)
        self.assertFalse(Order.objects.exists())

    def test_a_referent_cannot_override_an_inactive_name_the_customer_typed(self):
        """'هاخد ده Ambero' must not sell the perfume we had just offered."""
        Product.objects.create(store=self.store, brand=self.a.brand, name="Ambero", is_active=False)
        save_message(self.conv, "assistant", self.a.name, internal_context=self.a.name)
        reply = self.turn([self.line()], message="هاخد ده Ambero")
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.count(), 0)
        self.assertEqual([p["name"] for p in cart.pending_items], ["Ambero"])
        self.assertIn("مش متاح", reply)

    def test_an_unknown_spelling_still_reaches_the_resolver(self):
        """The other side of the two guards above: a name matching NO catalogue row must still go
        to `resolve_product`, or Arabic spellings like 'سوفاج' stop resolving in checkout."""
        with patch("products.services.order_service.resolve_product", return_value=self.a) as fallback:
            self.turn([{"name": "سوفاج", "volume": 50, "quantity": 1, "bottle_type": "normal"}],
                      message="عايز سوفاج")
        fallback.assert_called_once()
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.get().variant_id, self.av.pk)
        self.assertFalse(cart.pending_items)

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


class AgentEvaluationFixTests(BugFixture):
    def test_unrelated_confirmation_cannot_authorize_wrong_product(self):
        from products.services.product_resolver import resolve_order_name
        with patch("products.services.product_resolver.chat", return_value='{"name":"Vanilo"}'):
            self.assertIsNone(resolve_order_name("امبيرو", self.store))

    def test_authenticity_survives_followups_and_blocks_original_bottle(self):
        from products.services.router import route
        with patch("products.services.router.classify") as classify:
            reply, _ = route("مش عايز تركيبات بتاعتكم، عايز براندات أصلية", [], self.store, self.conv)
            self.assertIn("كلها تركيب", reply)
            classify.assert_not_called()
        with patch("products.services.router.classify", return_value="recommendation"), patch("products.services.router.extract_intent", return_value={"exclude_brands":["Dior"]}):
            second, _ = route("وبلاش ديور", [], self.store, self.conv)
            third, _ = route("طب ايه احسن حاجة", [], self.store, self.conv)
        self.assertNotEqual(second, third)
        self.assertEqual(self.conv.preferences["exclude_brands"], ["Dior"])
        reply = self.turn([self.line(volume=100, bottle_type="original")], message="هاخد Vanilo 100 ملي")
        self.assertIn("كلها تركيب", reply)
        self.assertFalse(Order.objects.exists())
        merge_preferences(self.conv, {}, "خلاص تركيب تمام")
        self.assertIs(self.conv.preferences["requires_original_liquid"], False)
        self.turn([self.line()], message="هاخد Vanilo")
        self.assertEqual(Cart.objects.get(conversation=self.conv).items.count(), 1)

    def test_bottle_and_exclusive_rejection_are_not_liquid_requirements(self):
        from products.services.sales.constraints import original_liquid_update
        for message in ("عايز زجاجة أوريجينال", "مش عايز حاجة من تصميمكم", "بلاش براندكم"):
            self.assertIsNone(original_liquid_update(message))

    def test_nice_is_not_sweet_but_saved_sweetness_survives(self):
        from products.services.ai.intent import extract_intent
        with patch("products.services.ai.intent.chat", return_value='{"notes":["sweet"],"requested_volume":90}'):
            intent = extract_intent("عايز عطر حلو")
            self.assertEqual(intent["notes"], [])
            self.assertNotIn("requested_volume", intent)
            intent = extract_intent("عايز حاجة حلوة", [{"role":"user", "content":"بحب المسكر"}])
            self.assertEqual(intent["notes"], ["sweet"])

    def test_unknown_similarity_asks_without_recommendation(self):
        from products.services.router import route
        with patch("products.services.router.classify", return_value="recommendation"), patch("products.services.router.extract_intent", return_value={"similar_to":"Lattafa Khamrah"}), patch("products.services.router.recommend") as recommend:
            reply, _ = route("عايز عطر زي Lattafa Khamrah", [], self.store, self.conv)
        recommend.assert_not_called()
        self.assertIn("Khamrah", reply)
        self.assertIn("؟", reply)
        self.assertNotIn("Vanilo", reply)

    def test_inactive_reference_uses_catalogue_notes(self):
        from products.services.search_service import _resolve_reference
        self.a.is_active = False
        self.a.top_notes = "Vanilla"
        self.a.save()
        reference = _resolve_reference({"similar_to":"Vanilo"}, self.store)
        self.assertEqual(reference.product.pk, self.a.pk)

    def test_thinking_has_no_product_lookup_or_pitch(self):
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        message = "طيب هفكر وارجعلك"
        with patch("products.services.objection_service.resolve_products") as resolve, patch("products.services.objection_service.chat") as chat:
            reply, _ = handle_objection(message, detect(message), store=self.store)
        resolve.assert_not_called()
        chat.assert_not_called()
        self.assertNotIn("؟", reply)
        self.assertNotIn("جنيه", reply)

    def test_best_followup_is_not_the_same_constrained_list(self):
        from products.services.ai.recommendation import recommend
        intent = {"requested_volume":90, "max_price":1000}
        first, _ = recommend("رشحلي", [self.a, self.b], intent=intent)
        best, _ = recommend("طب ايه احسن حاجة", [self.a, self.b], intent=intent)
        self.assertNotEqual(first, best)
        self.assertIn("أرشحلك Vanilo", best)
        self.assertNotIn("Leatherio", best)
        self.assertNotIn("Strong", best)

    def test_factual_guard_retries_once_then_uses_recorded_facts(self):
        from products.services.router import _rephrased
        from products.services.product_formatting import format_products
        invalid = "Vanilo فيه عود وثباته 48 ساعة."
        with patch("products.services.router.repetition.repeated_sentences", return_value=[]), patch("products.services.router.repetition.repeated_frames", return_value=[]):
            regenerate = Mock(return_value=(invalid, format_products([self.a])))
            reply, _, _ = _rephrased(invalid, format_products([self.a]), [], self.store, regenerate)
        self.assertEqual(regenerate.call_count, 1)
        self.assertNotIn("عود", reply)
        self.assertNotIn("48", reply)
        self.assertIn("حوالي 8", reply)

    def test_claims_are_per_product_and_negation_is_respected(self):
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Vanilo":{"notes":["Vanilla, Amber"],"longevity":"8 hours"}, "Oud Wood":{"notes":["Oud"],"longevity":"6 hours"}}
        self.assertTrue(product_claim_issues("Vanilo فيه عود. Oud Wood فيه عود.", facts))
        self.assertFalse(product_claim_issues("Vanilo فيه فانيليا وعنبر، مش عود. Oud Wood فيه عود.", facts))
        self.assertFalse(product_claim_issues("Vanilo في زجاجة أوريجينال بس السائل تركيب.", facts))
        self.assertTrue(product_claim_issues("عطر رجالي أصلي من البراندات", facts))
        self.assertTrue(product_claim_issues("Oud Wood فيه عود وباتشولي", facts))
        self.assertTrue(product_claim_issues("Oud Wood ريحته نادرة وفيه وردة", facts))

    def test_unknown_reference_answer_supplies_a_profile(self):
        from products.services.router import route
        from products.services.search_service import search_products
        merge_preferences(self.conv, {"similar_to": "Mystery Perfume"})
        history = [{"role": "assistant", "content": "إيه أكتر حاجة بتحبها في Mystery Perfume، الحلاوة ولا التوابل ولا طابع تاني؟"}]
        self.a.base_notes = "Vanilla, Tonka Bean"
        self.a.save()
        with patch("products.services.router.classify", return_value="recommendation"), patch("products.services.router.extract_intent", return_value={"notes":["sweet"]}), patch("products.services.router.recommend", return_value=("Vanilo", "Vanilo")) as recommend:
            route("بحب الحلاوة", history, self.store, self.conv)
        self.assertEqual(self.conv.preferences["similar_to_notes"], ["sweet"])
        recommend.assert_called_once()

    def test_evaluation_accepts_subtotals_but_not_wrong_unit_prices(self):
        from eval_harness.checks import build_ground_truth, check_variant_prices
        truth = build_ground_truth(self.store)
        self.assertFalse(check_variant_prices("2 × Vanilo (50ml) بـ 1214 جنيه", truth))
        self.assertTrue(check_variant_prices("Vanilo 50ml بـ 1214 جنيه", truth))

    def test_projection_claim_must_match_its_product(self):
        from products.services.reply_sanitizer import product_claim_issues
        facts = {"Vanilo": {"projection": "Strong"}}
        self.assertTrue(product_claim_issues("Vanilo فوحانه خفيف", facts))
        self.assertFalse(product_claim_issues("Vanilo فوحانه قوي", facts))

    def test_inactive_reference_preserves_gender(self):
        from products.services.sales.gender import resolve
        self.a.is_active = False
        self.a.save()
        self.assertEqual(resolve({"similar_to":"Vanilo"}, "نفس الجو", store=self.store), "male")

    def test_budget_followup_does_not_start_an_order(self):
        from products.services.router import route
        history = [{"role":"assistant", "content":"Vanilo مناسب ليك"}]
        with patch("products.services.router.classify", return_value="order"), patch("products.services.router.extract_intent", return_value={"max_price":1200}), patch("products.services.router.recommend", return_value=("Vanilo", "Vanilo")), patch("products.services.router.handle_order") as order:
            route("1200", history, self.store, self.conv)
        order.assert_not_called()
        self.assertEqual(self.conv.preferences["max_price"], 1200)

    def test_impossible_longevity_does_not_become_a_price_upsell(self):
        from products.services.ai.recommendation import recommend
        with patch("products.services.ai.recommendation.chat") as chat:
            reply, _ = recommend("عايز نيش ثباته يومين", [self.a], intent={"max_price":300})
        chat.assert_not_called()
        self.assertIn("حتى لو زودت الميزانية", reply)
        self.assertNotIn("607", reply)

    def test_supported_rounding_of_price_per_ml(self):
        from eval_harness.checks import build_ground_truth, check_variant_prices
        self.a90.price = 1200
        self.a90.save()
        self.assertFalse(check_variant_prices("Vanilo سعر الملي 13.3 جنيه", build_ground_truth(self.store)))

    def test_old_size_mention_cannot_authorize_a_new_size(self):
        from products.services.ai.intent import extract_intent
        history = [{"role":"user", "content":"عايز 50 ملي"}]
        with patch("products.services.ai.intent.chat", return_value='{"requested_volume":90}'):
            intent = extract_intent("أحسن حاجة؟", history)
        self.assertNotIn("requested_volume", intent)

    def test_price_objection_uses_two_real_offers(self):
        from products.services.objection_service import handle_objection
        from products.services.sales.objection import detect
        message = "ليه ادفع 944 في Vanilo وانا ممكن اجيب حاجه ب 700؟"
        with patch("products.services.objection_service.resolve_products", return_value=[self.a]), patch("products.services.objection_service.chat") as chat:
            reply, _ = handle_objection(message, detect(message), store=self.store)
        chat.assert_not_called()
        for expected in ("Vanilo", "944", "Leatherio", "645"):
            self.assertIn(expected, reply)
        self.assertNotIn("غالباً", reply)

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
