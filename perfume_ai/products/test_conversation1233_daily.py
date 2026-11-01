"""Resolve a daily-perfume pointer from suitable earlier offers, not the last name."""
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services.conversation_service import build_llm_history, save_message
from products.services.product_formatting import format_products
from products.services.product_info import get_product_info, occasion_reference_requested


class Conversation1233DailyTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Daily reference test")
        StoreSettings.objects.create(store=self.store)
        self.brand = Brand.objects.create(store=self.store, name=self.store.name)
        self.bonded = self.product("Bonded", "Office")
        self.showmix = self.product("Showmix", "Casual")
        self.elite = self.product("Elite", "Evening")
        self.dark = self.product("Dark Aura", "Evening")
        self.conversation = Conversation.objects.create(store=self.store)

    def product(self, name, occasion):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="male",
            occasion=occasion, longevity="8 hours", projection="Moderate",
        )
        ProductVariant.objects.create(product=product, volume=50, price=750)
        return product

    def offer(self, products, *, content=None, **kwargs):
        return save_message(
            self.conversation, "assistant", content or "، ".join(product.name for product in products),
            internal_context=format_products(products), **kwargs,
        )

    def test_daily_pointer_asks_between_daily_options_beyond_history_window(self):
        self.offer([self.bonded, self.showmix])
        for _ in range(5):
            save_message(self.conversation, "user", "تمام")
            save_message(self.conversation, "assistant", "متاح عندنا.")
        self.offer([self.elite, self.dark])
        history = build_llm_history(self.conversation)
        self.assertFalse(any(self.bonded.name in row["content"] for row in history))
        with patch("products.services.product_info.resolve_products") as resolver, patch(
            "products.services.product_info.chat"
        ) as chat:
            reply, context = get_product_info("واعطيني العطر اليومي", history, self.store, self.conversation)
        self.assertIn(self.bonded.name, reply)
        self.assertIn(self.showmix.name, reply)
        self.assertIn("تقصد أنهي", reply)
        self.assertIn("OCCASION_REFERENCE_UNRESOLVED", context)
        self.assertNotIn(self.elite.name, context)
        self.assertNotIn(self.dark.name, context)
        resolver.assert_not_called()
        chat.assert_not_called()

    def test_unique_daily_offer_is_the_only_product_given_to_response_model(self):
        self.offer([self.bonded])
        self.offer([self.elite, self.dark])
        with patch("products.services.product_info.resolve_products") as resolver, patch(
            "products.services.product_info.chat", return_value="Bonded هو العطر اليومي اللي اتكلمنا عنه."
        ) as chat:
            reply, context = get_product_info("اديني العطر النهاري", [], self.store, self.conversation)
        self.assertIn(self.bonded.name, reply)
        self.assertIn(self.bonded.name, context)
        self.assertNotIn(self.elite.name, context)
        self.assertNotIn(self.dark.name, context)
        self.assertIn(self.bonded.name, chat.call_args.args[0][-1]["content"])
        resolver.assert_not_called()

    def test_newest_suitable_offer_wins_over_older_daily_options(self):
        self.offer([self.bonded])
        self.offer([self.showmix])
        self.offer([self.elite, self.dark])
        with patch("products.services.product_info.chat", return_value="Showmix مناسب للنهار."):
            _, context = get_product_info("العطر اليومي", [], self.store, self.conversation)
        self.assertIn(self.showmix.name, context)
        self.assertNotIn(self.bonded.name, context)

    def test_evening_pointer_looks_past_a_daytime_offer(self):
        self.offer([self.elite])
        self.offer([self.bonded])
        with patch("products.services.product_info.resolve_products") as resolver, patch(
            "products.services.product_info.chat", return_value="Elite مناسب للسهرات."
        ):
            _, context = get_product_info("العطر المسائي", [], self.store, self.conversation)
        self.assertIn(self.elite.name, context)
        self.assertNotIn(self.bonded.name, context)
        resolver.assert_not_called()

    def test_context_only_and_undelivered_daily_perfumes_are_not_referents(self):
        self.offer([self.bonded, self.elite], content=self.elite.name)
        self.offer([self.showmix], delivery_status="pending")
        with patch("products.services.product_info.resolve_products") as resolver, patch(
            "products.services.product_info.chat"
        ) as chat:
            reply, context = get_product_info("واعطيني العطر اليومي", [], self.store, self.conversation)
        self.assertEqual(context, "OCCASION_REFERENCE_UNRESOLVED")
        self.assertNotIn(self.elite.name, reply)
        self.assertNotIn(self.bonded.name, reply)
        self.assertNotIn(self.showmix.name, reply)
        resolver.assert_not_called()
        chat.assert_not_called()

    def test_inactive_or_unsellable_daytime_offer_cannot_resolve_pointer(self):
        for change in ("inactive", "unsellable"):
            with self.subTest(change=change):
                self.offer([self.bonded])
                self.bonded.is_active = change != "inactive"
                self.bonded.save(update_fields=["is_active"])
                if change == "unsellable":
                    self.bonded.variants.update(bottle_type="original", stock=0)
                with patch("products.services.product_info.resolve_products") as resolver:
                    _, context = get_product_info("العطر اليومي", [], self.store, self.conversation)
                self.assertEqual(context, "OCCASION_REFERENCE_UNRESOLVED")
                resolver.assert_not_called()

    def test_other_store_conversation_cannot_supply_a_referent(self):
        self.offer([self.bonded])
        other = Store.objects.create(name="Another store")
        reply, context = get_product_info("العطر اليومي", [], other, self.conversation)
        self.assertEqual(context, "OCCASION_REFERENCE_UNRESOLVED")
        self.assertNotIn(self.bonded.name, reply)

    def test_named_product_question_and_new_recommendation_are_not_pointers(self):
        for message in (
            "هل Elite ينفع يومي؟", "عايز عطر يومي", "عايز حاجه للنهار",
            "مش عايز العطر اليومي", "اديني العطر اليومي Sauvage", "ضيف العطر اليومي 90 ملي",
        ):
            with self.subTest(message=message):
                self.assertFalse(occasion_reference_requested(message))
        self.offer([self.bonded])
        with patch("products.services.product_info.chat", return_value="Elite مسجل للسهرات."):
            _, context = get_product_info("هل Elite ينفع يومي؟", [], self.store, self.conversation)
        self.assertIn(self.elite.name, context)
        self.assertNotIn(self.bonded.name, context)

    def test_history_only_and_english_pointer_keep_grounded_subject(self):
        history = [
            {"role": "assistant", "content": self.bonded.name},
            {"role": "assistant", "content": self.elite.name},
        ]
        with patch("products.services.product_info.resolve_products") as resolver, patch(
            "products.services.product_info.chat", return_value="Bonded is the daily perfume."
        ):
            _, context = get_product_info("give me the daily perfume", history, self.store)
        self.assertIn(self.bonded.name, context)
        self.assertNotIn(self.elite.name, context)
        resolver.assert_not_called()

    def test_history_only_skips_failed_delivery_and_unknown_occasion(self):
        self.bonded.occasion = ""
        self.bonded.save(update_fields=["occasion"])
        history = [
            {"role": "assistant", "content": self.bonded.name},
            {"role": "assistant", "content": self.showmix.name, "delivery_status": "failed"},
        ]
        _, context = get_product_info("العطر اليومي", history, self.store)
        self.assertEqual(context, "OCCASION_REFERENCE_UNRESOLVED")
