"""A missing 9PM family is absence, not an invitation to keep retyping its name."""
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Cart, Conversation, Notification, Order, Product, ProductVariant, Store, StoreSettings
from products.services import product_info
from products.services.conversation_service import build_llm_history, save_message
from products.services.product_formatting import format_products, quote_evidence
from products.services.router import route
from products.services.sales import described


class Conversation1225Tests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Missing numeric family")
        StoreSettings.objects.create(store=self.store)
        self.brand = Brand.objects.create(store=self.store, name="Afnan")
        self.alternative = self.product("Wanted by Night")
        self.conv = Conversation.objects.create(store=self.store, platform="web")
        self.resolver = self.start_patch(
            "products.services.product_info.resolve_products",
            side_effect=AssertionError("Protected numeric names must not use a model guess"),
        )
        self.resolver_chat = self.start_patch(
            "products.services.product_resolver.chat",
            side_effect=AssertionError("Protected numeric names must not use a model guess"),
        )
        self.chat = self.start_patch("products.services.product_info.chat", return_value="رد للاختبار.")

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def product(self, name, *, active=True, store=None, brand=None):
        product = Product.objects.create(
            store=store or self.store, brand=brand or self.brand, name=name,
            is_active=active, gender="male", base_notes="Vanilla", longevity="8 hours",
        )
        ProductVariant.objects.create(product=product, volume=50, price=650)
        return product

    def info(self, message):
        self.chat.reset_mock()
        reply, context = product_info.get_product_info(
            message, build_llm_history(self.conv), self.store, self.conv,
        )
        self.resolver.assert_not_called()
        self.resolver_chat.assert_not_called()
        prompt = self.chat.call_args.args[0][-1]["content"] if self.chat.called else ""
        return reply, context, prompt

    def pending_question(self, context):
        return next(
            (line.removeprefix(described.PENDING_LOOKUP_MARKER).strip()
             for line in context.splitlines() if line.startswith(described.PENDING_LOOKUP_MARKER)),
            "",
        )

    def assert_absence(self, context, prompt, message):
        self.assertIn(product_info.ABSENCE_DENIED_MARKER, context)
        self.assertNotIn(product_info.NAME_UNREADABLE_MARKER, context)
        self.assertEqual(self.pending_question(context), message)
        self.assertIn(product_info._ABSENT_RULES, prompt)
        self.assertNotIn(product_info._UNREADABLE_NAME_RULES, prompt)
        self.assertFalse(quote_evidence(context).get("quotes"))

    def test_exact_replay_records_each_current_name_and_notifies_without_handoff(self):
        messages = ("في برفان 9 بي ام ؟", "9pm night out")
        self.chat.side_effect = ("صياغة الرد الأول للاختبار.", "المثال الثاني مختلف في عباراته.")
        for message in messages:
            history = build_llm_history(self.conv)
            save_message(self.conv, "user", message)
            with patch("products.services.router.classify", return_value="product_info"):
                reply, context = route(message, history, self.store, self.conv)
            self.assertTrue(self.chat.called)
            prompt = self.chat.call_args.args[0][-1]["content"]
            self.assert_absence(context, prompt, message)
            save_message(self.conv, "assistant", reply, internal_context=context)

        notifications = list(Notification.objects.filter(store=self.store).order_by("pk"))
        self.assertEqual(len(notifications), 2)
        self.assertIn("مش في الكتالوج", notifications[-1].title)
        self.assertIn("9pm night out", notifications[-1].message)
        self.conv.refresh_from_db()
        self.assertFalse(self.conv.needs_human)
        self.assertFalse(Cart.objects.filter(conversation=self.conv).exists())
        self.assertFalse(Order.objects.exists())
        self.resolver.assert_not_called()
        self.resolver_chat.assert_not_called()

    def test_numeric_spellings_and_product_questions_have_the_same_absence_decision(self):
        messages = [f"في برفان {spelling} ؟" for spelling in ("9 بي ام", "٩ بي إم", "9 pm", "9PM")]
        messages += ["9pm ٩٠ ملي زجاجة الستور بكام؟", "9pm بكام وثباته؟"]
        for message in messages:
            with self.subTest(message=message):
                _, context, prompt = self.info(message)
                self.assert_absence(context, prompt, message)

    def test_named_active_edition_quotes_only_that_edition(self):
        self.product("Afnan 9PM")
        night = self.product("Afnan 9PM Night Out")
        for spelling in ("9pm night out", "9 بي ام Night Out"):
            with self.subTest(spelling=spelling):
                _, context, prompt = self.info(spelling + " بكام؟")
                self.assertNotIn(product_info.ABSENCE_DENIED_MARKER, context)
                self.assertNotIn("NUMERIC_NAME_UNRESOLVED", context)
                self.assertEqual(
                    {quote["product_id"] for quote in quote_evidence(context)["quotes"]},
                    {night.pk},
                )
                self.assertIn(night.name, prompt)

    def test_inactive_exact_edition_is_not_replaced_by_an_active_flanker(self):
        night = self.product("Afnan 9PM Night Out", active=False)
        rebel = self.product("Afnan 9PM Rebel")
        reply, context, _ = self.info("Afnan 9PM Night Out")
        self.assertEqual(context, "NUMERIC_NAME_UNAVAILABLE")
        self.assertIn(night.name, reply)
        self.assertNotIn(rebel.name, reply)
        self.chat.assert_not_called()

    def test_ambiguous_and_unknown_editions_do_not_allow_a_model_guess(self):
        base = self.product("Afnan 9PM")
        rebel = self.product("Afnan 9PM Rebel")
        for message in ("9 بي ام", "Afnan 9PM Unknown"):
            with self.subTest(message=message):
                reply, context, _ = self.info(message)
                self.assertEqual(context, "NUMERIC_NAME_UNRESOLVED")
                self.assertIn(base.name, reply)
                self.assertIn(rebel.name, reply)
                self.chat.assert_not_called()

    def test_another_stores_family_does_not_make_this_store_ambiguous(self):
        other = Store.objects.create(name="Different catalogue")
        brand = Brand.objects.create(store=other, name="Afnan")
        self.product("Afnan 9PM", store=other, brand=brand)
        self.product("Afnan 9PM Night Out", store=other, brand=brand)
        message = "9pm night out"
        _, context, prompt = self.info(message)
        self.assert_absence(context, prompt, message)

    def test_previously_offered_perfume_is_labeled_as_an_alternative(self):
        save_message(self.conv, "assistant", self.alternative.name,
                     internal_context=format_products([self.alternative]))
        message = "9pm night out"
        _, context, prompt = self.info(message)
        self.assert_absence(context, prompt, message)
        self.assertIn(product_info._NOT_THE_PERFUME_ASKED_ABOUT, context)
        self.assertIn(self.alternative.name, context)

    def test_multiple_names_and_unverified_arabic_suffix_are_not_falsely_denied(self):
        self.product("Dior Sauvage")
        for message in (
            "9pm night out و Dior Sauvage",
            "9pm night out and Dior Sauvage",
            "9pm night out وDior Sauvage",
            "9pm night out, Dior Sauvage",
            "9pm night out Dior Sauvage",
            "9pm نايت اوت",
        ):
            with self.subTest(message=message):
                _, context, _ = self.info(message)
                self.assertNotIn(product_info.ABSENCE_DENIED_MARKER, context)
                self.assertFalse(quote_evidence(context).get("quotes"))
