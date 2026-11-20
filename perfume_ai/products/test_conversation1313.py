"""Conversation 1313: "انهي احسن فيهم" right after "بكام دول" was answered with the price block again.

Three words long, it was read as a bare continuation of the price question, so the comparison was
forced into the price layout, the layout rejected a sentence naming both perfumes, and the fallback
had nothing to say about "which is better" except the prices. The next turn ("؟") compared them.
"""
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store, StoreSettings
from products.services.comparison_service import compare_products
from products.services.conversation_service import build_llm_history, save_message
from products.services.price_reply import composition_issues, compose_price_reply
from products.services.product_formatting import (format_products, price_question, product_quotes,
                                                  quote_context, quote_evidence, render_quotes)
from products.services.router import _rephrased

# Turn 12 of conversation 1313: the answer the customer got one turn late.
COMPARISON = ("Stronger With You Oud طابعه عودي ومسكر وخشبي، أما Harsh Lahab فطابعه متبّل وتقيل وعودي، "
              "والحلاوة مش ضمن طابعه. لو بتحب الحلاوة اختار الأول، ولو بتحب التوابل والطابع التقيل اختار التاني.")


class Conversation1313Tests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name="Conversation 1313")
        StoreSettings.objects.create(store=self.store)
        self.oud = self.product("Stronger With You Oud", "Armani", ["oud", "sweet", "woody"], 1500)
        self.lahab = self.product("Harsh Lahab", "Al ezz", ["spicy", "heavy", "oud"], 2000)
        self.pair = [self.oud, self.lahab]
        self.conv = Conversation.objects.create(store=self.store, preferences={"gender": "male"})

    def product(self, name, brand, tags, original_price):
        product = Product.objects.create(
            store=self.store, brand=Brand.objects.create(store=self.store, name=brand), name=name,
            gender="male", longevity="ممتاز", projection="قوي", scent_tags=tags,
            top_notes="Lavender", middle_notes="Agarwood (Oud)", base_notes="Vanilla",
        )
        ProductVariant.objects.create(product=product, volume=50, price=750)
        ProductVariant.objects.create(product=product, volume=90, price=1000)
        ProductVariant.objects.create(product=product, volume=100, price=original_price, bottle_type="original", stock=5)
        return product

    def price_turns(self):
        """The conversation up to "انهي احسن فيهم": an offer of both, then their price block."""
        save_message(self.conv, "user", "العود")
        save_message(self.conv, "assistant", "Stronger With You Oud وHarsh Lahab.",
                     internal_context=format_products(self.pair, show_prices=False))
        save_message(self.conv, "user", "بكام دول")
        quotes = product_quotes(self.pair)
        block = render_quotes(quotes)
        save_message(self.conv, "assistant", block,
                     internal_context=quote_context(format_products(self.pair), quotes, rendered=block))
        return build_llm_history(self.conv)

    def test_a_short_choice_question_does_not_inherit_the_price_question(self):
        history = self.price_turns()
        for message in ("انهي احسن فيهم", "أنهي فيهم؟", "تختارلي انهي"):
            with self.subTest(message=message):
                self.assertFalse(price_question(message, history, self.conv))
        # Still prices: a comparison on price, a bare continuation, or a price word of its own.
        for message in ("انهي ارخص", "مين فيهم اوفر", "والتاني؟", "و90؟", "طب والاوريجنال", "انهي احسن فيهم وبكام"):
            with self.subTest(message=message):
                self.assertTrue(price_question(message, history, self.conv))

    def test_which_is_better_after_the_prices_is_answered_with_the_comparison(self):
        history = self.price_turns()
        with patch("products.services.comparison_service.chat", return_value=COMPARISON), \
             patch("products.services.comparison_service.resolve_products", return_value=[]):
            reply, context = compare_products("انهي احسن فيهم", history, self.store, self.conv)
        self.assertEqual(quote_evidence(context), {})
        self.assertEqual(reply, COMPARISON)
        self.assertNotIn("جنيه", reply)

    def test_a_failed_which_and_price_draft_keeps_each_perfumes_character(self):
        message = "انهي احسن فيهم وبكام"
        base = quote_context(format_products(self.pair, show_prices=False), product_quotes(self.pair),
                             append=True, request=message)
        draft, context = compose_price_reply(COMPARISON, base)
        self.assertTrue(composition_issues(draft, context))
        reply, final_context, _ = _rephrased(draft, context, [], self.store,
                                             lambda hint: compose_price_reply(COMPARISON, base), message=message)
        first, second = reply.split("Harsh Lahab")
        self.assertIn("عودي ومسكر وخشبي", first)
        self.assertIn("1500", first)
        self.assertIn("توابل", second)
        self.assertIn("2000", second)
        self.assertEqual(composition_issues(reply, final_context), [])
