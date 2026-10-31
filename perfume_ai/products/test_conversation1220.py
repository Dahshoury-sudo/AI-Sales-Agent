"""Conversation 1220: numeric product selection and accepted price follow-ups."""
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Cart, Conversation, Order, Product, ProductVariant, Store, StoreSettings
from products.services.conversation_service import build_llm_history, save_message
from products.services.product_formatting import format_products, price_question, quote_evidence
from products.services.product_info import get_product_info
from products.services.router import route
from products.services.sales.followups import accepts_price_offer, numeric_reference


class Conversation1220Tests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Numeric perfume test")
        StoreSettings.objects.create(store=self.store)
        self.brand = Brand.objects.create(store=self.store, name="Carolina Herrera")
        self.dior = Brand.objects.create(store=self.store, name="Dior")
        self.vip = self.product("212 VIB Man")
        self.nyc = self.product("212 NYC Man")
        self.black = self.product("Black XS")
        self.elixir = self.product("Sauvage Elixir", self.dior)
        self.fahrenheit = self.product("Fahrenheit", self.dior)
        self.conv = Conversation.objects.create(store=self.store)

    def product(self, name, brand=None):
        product = Product.objects.create(
            store=self.store, brand=brand or self.brand, name=name, gender="male",
            longevity="8 hours", projection="Strong", base_notes="Amber, Woody Notes",
        )
        ProductVariant.objects.create(product=product, volume=50, price=750)
        ProductVariant.objects.create(product=product, volume=90, price=1000)
        return product

    def offer(self, products, content=None, **kwargs):
        return save_message(self.conv, "assistant", content or "، ".join(p.name for p in products),
                            internal_context=format_products(products), **kwargs)

    def test_numeric_name_uses_latest_offer_in_both_scripts(self):
        self.offer([self.vip, self.black])
        for message in ("212", "٢١٢"):
            with self.subTest(message=message):
                result = numeric_reference(message, self.store, self.conv)
                self.assertEqual(result.status, "resolved")
                self.assertEqual(result.product, self.vip)
                with patch("products.services.product_info.resolve_products") as resolver, patch(
                    "products.services.product_info.chat", return_value="212 VIB Man متوفر."
                ):
                    reply, context = get_product_info(message, [], self.store, self.conv)
                resolver.assert_not_called()
                self.assertEqual({q["product_id"] for q in quote_evidence(context)["quotes"]}, {self.vip.pk})
                self.assertIn("750", reply)
                self.assertNotIn(self.nyc.name, context)

    def test_number_cannot_override_product_info_into_recommendations(self):
        self.offer([self.vip, self.black])
        with patch("products.services.router.classify", return_value="product_info"), patch(
            "products.services.router.extract_intent"
        ) as extract, patch("products.services.product_info.chat", return_value="212 VIB Man متوفر."):
            reply, context = route("212", build_llm_history(self.conv), self.store, self.conv)
        extract.assert_not_called()
        self.assertIn(self.vip.name, reply)
        self.assertNotIn("مرجع المقارنة", context)
        self.conv.refresh_from_db()
        self.assertNotIn("similar_to", self.conv.preferences)
        self.assertFalse(Cart.objects.filter(conversation=self.conv).exists())
        self.assertFalse(Order.objects.exists())

    def test_ambiguous_version_asks_without_model_guess(self):
        self.offer([self.vip, self.nyc])
        with patch("products.services.product_info.resolve_products") as resolver, patch(
            "products.services.product_info.chat"
        ) as chat:
            reply, context = get_product_info("212", [], self.store, self.conv)
        self.assertEqual(context, "NUMERIC_NAME_UNRESOLVED")
        self.assertIn(self.vip.name, reply)
        self.assertIn(self.nyc.name, reply)
        resolver.assert_not_called()
        chat.assert_not_called()

    def test_first_message_numeric_name_does_not_depend_on_classifier(self):
        with patch("products.services.router.classify", return_value="recommendation"), patch(
            "products.services.router.extract_intent"
        ) as extract:
            reply, context = route("212", [], self.store, self.conv)
        self.assertEqual(context, "NUMERIC_NAME_UNRESOLVED")
        self.assertIn(self.vip.name, reply)
        extract.assert_not_called()

    def test_inactive_version_is_not_replaced_by_active_sibling(self):
        self.offer([self.vip])
        self.vip.is_active = False
        self.vip.save(update_fields=["is_active"])
        reply, context = get_product_info("212", [], self.store, self.conv)
        self.assertEqual(context, "NUMERIC_NAME_UNAVAILABLE")
        self.assertIn(self.vip.name, reply)
        self.assertNotIn(self.nyc.name, reply)

    def test_currency_budget_and_size_are_not_product_references(self):
        self.offer([self.vip])
        for message in ("212 جنيه", "٢١٢ ج", "212 ملي", "1200"):
            self.assertEqual(numeric_reference(message, self.store, self.conv).status, "no_match")
        save_message(self.conv, "assistant", "ميزانيتك في حدود كام؟")
        self.assertEqual(numeric_reference("212", self.store, self.conv).status, "no_match")

    def test_bare_budget_fallback_and_active_checkout_are_preserved(self):
        self.offer([self.black])
        with patch("products.services.router.classify", return_value="order"), patch(
            "products.services.router.extract_intent", return_value={"max_price": 1200, "gender": "male"}
        ), patch("products.services.router.recommend", return_value=(self.black.name, format_products([self.black]))), patch(
            "products.services.router.handle_order"
        ) as order:
            route("1200", build_llm_history(self.conv), self.store, self.conv)
        order.assert_not_called()
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.preferences["max_price"], 1200)
        Cart.objects.create(conversation=self.conv)
        with patch("products.services.router.classify", return_value="order"), patch(
            "products.services.router.handle_order", return_value=("الكمية اتحدثت", "")
        ) as order:
            route("50", build_llm_history(self.conv), self.store, self.conv)
        order.assert_called_once()

    def test_undelivered_offers_and_other_stores_do_not_choose_version(self):
        self.offer([self.vip])
        self.offer([self.nyc], delivery_status="pending")
        self.assertEqual(numeric_reference("212", self.store, self.conv).product, self.vip)
        other = Store.objects.create(name="Other")
        self.assertEqual(numeric_reference("212", other, self.conv).status, "no_match")
        self.assertEqual(numeric_reference("212", other).status, "no_match")

    def test_unique_numeric_catalogue_name_and_history_only_selection(self):
        product = self.product("Baccarat Rouge 540")
        self.assertEqual(numeric_reference("540", self.store).product, product)
        history = [{"role": "assistant", "content": "212 VIB Man مناسب ليك"}]
        self.assertEqual(numeric_reference("212", self.store, history=history).product, self.vip)
        self.assertEqual(numeric_reference("212", self.store).status, "ambiguous")

    def test_price_offer_acceptance_is_bound_to_latest_question(self):
        history = [{"role": "assistant", "content": "Sauvage Elixir وFahrenheit. تحب أقول لك الأسعار والأحجام؟"}]
        self.assertTrue(accepts_price_offer("تمام", history))
        self.assertTrue(price_question("تمام", history))
        for message in ("لا", "تمام بس بكرة", "تمام هاخد واحد", "تمام والشحن كام"):
            self.assertFalse(accepts_price_offer(message, history))
        for latest in ("الـ50 ملي بـ750 جنيه. تحب تطلب؟", "تحب أحولك لخدمة العملاء؟", "تم تأكيد طلبك بنجاح",
                       "الـ50 ملي بـ750 جنيه، تحب أطلبه بالسعر ده؟", "تحب تطلب بسعر 750 جنيه؟"):
            self.assertFalse(accepts_price_offer("تمام", history + [{"role": "assistant", "content": latest}]))
        self.assertFalse(accepts_price_offer("تمام", history + [{"role": "agent", "content": "تحب أقولك الأسعار؟"}]))

    def test_accepting_checkout_with_a_price_stays_checkout(self):
        self.offer([self.vip], "212 VIB Man الـ50 ملي بـ750 جنيه، تحب أطلبه بالسعر ده؟")
        with patch("products.services.router.classify", return_value="order"), patch(
            "products.services.router.handle_order", return_value=("أنهي حجم تحب؟", "")
        ) as order, patch("products.services.router.get_product_info") as info:
            route("تمام", build_llm_history(self.conv), self.store, self.conv)
        order.assert_called_once()
        info.assert_not_called()

    def test_replay_selection_brand_change_and_accepted_prices(self):
        save_message(self.conv, "user", "تقيله وخشبية")
        self.offer([self.vip, self.black])
        with patch("products.services.router.classify", return_value="product_info"), patch(
            "products.services.product_info.chat", return_value="212 VIB Man متوفر."
        ):
            reply, context = route("212", build_llm_history(self.conv), self.store, self.conv)
        save_message(self.conv, "user", "212")
        save_message(self.conv, "assistant", reply, internal_context=context)
        dior_reply = "Sauvage Elixir وFahrenheit مناسبين. تحب أقول لك الأسعار والأحجام؟"
        with patch("products.services.router.classify", return_value="recommendation"), patch(
            "products.services.router.extract_intent",
            return_value={"brand": "Dior", "gender": "male", "similar_to": self.vip.name},
        ), patch("products.services.router.recommend", return_value=(dior_reply, format_products([self.elixir, self.fahrenheit]))) as recommend:
            reply, context = route("ديور", build_llm_history(self.conv), self.store, self.conv)
        self.assertNotIn("similar_to", recommend.call_args.kwargs["intent"])
        self.assertIsNone(recommend.call_args.kwargs["search"]["similarity"])
        save_message(self.conv, "user", "ديور")
        save_message(self.conv, "assistant", reply, internal_context=context)
        with patch("products.services.router.classify", return_value="order"), patch(
            "products.services.product_info.chat", return_value="دي أسعار الأحجام المتاحة."
        ), patch("products.services.router.handle_order") as order:
            reply, context = route("تمام", build_llm_history(self.conv), self.store, self.conv)
        order.assert_not_called()
        self.assertEqual({q["product_id"] for q in quote_evidence(context)["quotes"]}, {self.elixir.pk, self.fahrenheit.pk})
        self.assertNotIn("212", reply)
        self.assertNotIn("212", context)
