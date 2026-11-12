"""Compact quotations preserve each catalogue fact and validation evidence."""

from copy import deepcopy
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store
from products.services.product_formatting import (
    ProductQuote,
    append_quotation,
    product_price_issues,
    product_quotes,
    quote_context,
    quote_evidence,
    render_quotes,
)
from products.services.reply_sanitizer import sanitize_reply


class CompactQuoteRenderingTests(SimpleTestCase):
    def quote(self, volume, price, *, product_id=1, name="Dior Sauvage",
              bottle_type="normal", quantity=1):
        return ProductQuote(product_id, volume, name, volume, bottle_type,
                            quantity, Decimal(price))

    def test_consecutive_sizes_share_a_label_and_keep_their_input_order(self):
        quotes = [self.quote(90, "180.00"), self.quote(50, "123.45"),
                  self.quote(100, "190.00")]

        text = render_quotes(iter(quotes))

        self.assertEqual(text.count("Dior Sauvage"), 1)
        self.assertEqual(text.count("زجاجة الستور الخاصة بينا"), 1)
        self.assertEqual(text.split("، و"), [
            "Dior Sauvage\nزجاجة الستور الخاصة بينا 90 ملي بـ180 جنيه",
            "50 ملي بـ123.45 جنيه",
            "100 ملي بـ190 جنيه.",
        ])

    def test_interleaved_products_share_one_heading_and_keep_each_products_quote_order(self):
        quotes = [self.quote(50, "123.45"), self.quote(90, "180.00"),
                  self.quote(50, "299.99", bottle_type="original"),
                  self.quote(90, "499.00", bottle_type="original"),
                  self.quote(50, "100.00", product_id=2, name="Afnan 9PM"),
                  self.quote(90, "150.00", product_id=2, name="Afnan 9PM"),
                  self.quote(100, "190.00")]

        text = render_quotes(iter(quotes))

        self.assertEqual(text, (
            "Dior Sauvage\n"
            "زجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه، و90 ملي بـ180 جنيه.\n"
            "زجاجة اوريجنال 50 ملي بـ299.99 جنيه، و90 ملي بـ499 جنيه.\n"
            "زجاجة الستور الخاصة بينا 100 ملي بـ190 جنيه.\n\n"
            "Afnan 9PM\n"
            "زجاجة الستور الخاصة بينا 50 ملي بـ100 جنيه، و90 ملي بـ150 جنيه."
        ))
        self.assertEqual(text.count("Dior Sauvage"), 1)
        self.assertEqual(text.count("Afnan 9PM"), 1)

    def test_interleaved_adjacent_bottle_quotes_join_inside_their_product_section(self):
        quotes = [self.quote(90, "180.00"),
                  self.quote(50, "100.00", product_id=2, name="Afnan 9PM"),
                  self.quote(50, "123.45"), self.quote(100, "190.00")]

        self.assertEqual(render_quotes(quotes), (
            "Dior Sauvage\n"
            "زجاجة الستور الخاصة بينا 90 ملي بـ180 جنيه، و50 ملي بـ123.45 جنيه، و100 ملي بـ190 جنيه.\n\n"
            "Afnan 9PM\nزجاجة الستور الخاصة بينا 50 ملي بـ100 جنيه."
        ))

    def test_overlapping_names_keep_their_own_sections_and_all_bottle_types(self):
        quotes = [self.quote(50, "123.45"),
                  self.quote(90, "400.00", product_id=2, name="Dior Sauvage Elixir"),
                  self.quote(90, "499.00", bottle_type="original"),
                  self.quote(50, "700.00", product_id=2, name="Dior Sauvage Elixir",
                             bottle_type="original")]

        self.assertEqual(render_quotes(quotes).split("\n\n"), [
            "Dior Sauvage\nزجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه.\n"
            "زجاجة اوريجنال 90 ملي بـ499 جنيه.",
            "Dior Sauvage Elixir\nزجاجة الستور الخاصة بينا 90 ملي بـ400 جنيه.\n"
            "زجاجة اوريجنال 50 ملي بـ700 جنيه.",
        ])

    def test_identical_names_do_not_merge_distinct_catalogue_products(self):
        quotes = [self.quote(50, "123.45"),
                  self.quote(90, "180.00", product_id=2)]

        self.assertEqual(render_quotes(quotes).split("\n\n"), [
            "Dior Sauvage\nزجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه.",
            "Dior Sauvage\nزجاجة الستور الخاصة بينا 90 ملي بـ180 جنيه.",
        ])

    def test_grouped_quantities_keep_exact_decimal_totals(self):
        text = render_quotes([self.quote(50, "123.4567", quantity=2),
                              self.quote(90, "180.00", quantity=3)])

        self.assertEqual(text.count("Dior Sauvage"), 1)
        self.assertIn("50 ملي × 2 بـ246.9134 جنيه", text)
        self.assertIn("90 ملي × 3 بـ540 جنيه", text)
        self.assertNotIn("246.91 جنيه", text)

    def test_budget_warning_stays_with_each_near_budget_size(self):
        quotes = [self.quote(50, "90.00"), self.quote(90, "105.00"),
                  self.quote(100, "95.00"), self.quote(120, "110.00")]

        clauses = render_quotes(quotes, Decimal("100.00")).split("، و")

        self.assertEqual(len(clauses), 4)
        self.assertNotIn("أعلى حاجة بسيطة", clauses[0])
        self.assertIn("90 ملي بـ105 جنيه (أعلى حاجة بسيطة من ميزانيتك)", clauses[1])
        self.assertNotIn("أعلى حاجة بسيطة", clauses[2])
        self.assertIn("120 ملي بـ110 جنيه (أعلى حاجة بسيطة من ميزانيتك)", clauses[3])

    def test_quantity_does_not_change_existing_per_unit_budget_tiering(self):
        text = render_quotes([self.quote(50, "90.00", quantity=2),
                              self.quote(90, "105.00", quantity=2)], Decimal("100.00"))

        clauses = text.split("، و")
        self.assertEqual(len(clauses), 2)
        self.assertNotIn("أعلى حاجة بسيطة", clauses[0])
        self.assertIn("90 ملي × 2 بـ210 جنيه (أعلى حاجة بسيطة من ميزانيتك)", clauses[1])

    def test_omitting_names_still_keeps_separate_product_and_bottle_groups(self):
        quotes = [self.quote(50, "123.45"), self.quote(90, "180.00"),
                  self.quote(50, "299.99", bottle_type="original"),
                  self.quote(50, "100.00", product_id=2, name="Afnan 9PM")]

        text = render_quotes(quotes, include_name=False)
        sections = text.split("\n\n")
        lines = [line for line in text.splitlines() if line]

        self.assertEqual(len(sections), 2)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "زجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه، و90 ملي بـ180 جنيه.")
        self.assertEqual(lines[1], "زجاجة اوريجنال 50 ملي بـ299.99 جنيه.")
        self.assertEqual(lines[2], "زجاجة الستور الخاصة بينا 50 ملي بـ100 جنيه.")
        self.assertNotIn("Dior Sauvage", "\n".join(lines))
        self.assertNotIn("Afnan 9PM", "\n".join(lines))

    def test_single_quote_keeps_quantity_and_warning_below_its_optional_heading(self):
        quote = self.quote(90, "105.00", quantity=2, bottle_type="original")
        for include_name, expected in (
            (True, "Dior Sauvage\nزجاجة اوريجنال 90 ملي × 2 بـ210 جنيه. أعلى حاجة بسيطة من ميزانيتك."),
            (False, "زجاجة اوريجنال 90 ملي × 2 بـ210 جنيه. أعلى حاجة بسيطة من ميزانيتك."),
        ):
            with self.subTest(include_name=include_name):
                self.assertEqual(render_quotes([quote], Decimal("100.00"), include_name=include_name), expected)

    def test_empty_quotes_preserve_empty_rendering_and_context(self):
        self.assertEqual(render_quotes([]), "")
        self.assertEqual(render_quotes(iter([]), include_name=False), "")
        self.assertEqual(quote_context("existing facts", [], append=True), "existing facts")


class CompactQuoteEvidenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        store = Store.objects.create(name="Compact quotation test")
        brand = Brand.objects.create(store=store, name="Dior")
        cls.product = Product.objects.create(store=store, brand=brand, name="Dior Sauvage", gender="male")
        cls.small = ProductVariant.objects.create(product=cls.product, volume=50, price=Decimal("123.45"))
        cls.medium = ProductVariant.objects.create(product=cls.product, volume=90, price=Decimal("180.00"))
        cls.large = ProductVariant.objects.create(product=cls.product, volume=100, price=Decimal("190.00"))
        cls.original = ProductVariant.objects.create(product=cls.product, volume=50, price=Decimal("299.99"), bottle_type="original", stock=2)
        cls.elixir = Product.objects.create(store=store, brand=brand, name="Dior Sauvage Elixir", gender="male")
        cls.elixir_small = ProductVariant.objects.create(product=cls.elixir, volume=50, price=Decimal("160.00"))
        cls.elixir_original = ProductVariant.objects.create(product=cls.elixir, volume=90, price=Decimal("199.99"), bottle_type="original", stock=1)

    def test_interleaved_sections_preserve_exact_evidence_through_sanitizing_and_reappend(self):
        quotes = [ProductQuote.from_variant(self.small, quantity=2),
                  ProductQuote.from_variant(self.elixir_small),
                  ProductQuote.from_variant(self.original),
                  ProductQuote.from_variant(self.large),
                  ProductQuote.from_variant(self.elixir_original)]
        context = quote_context("catalogue facts", quotes, append=True,
                                max_price=Decimal("180.00"))
        evidence = quote_evidence(context)
        expected_block = (
            "Dior Sauvage\n"
            "زجاجة الستور الخاصة بينا 50 ملي × 2 بـ246.90 جنيه.\n"
            "زجاجة اوريجنال 50 ملي بـ299.99 جنيه.\n"
            "زجاجة الستور الخاصة بينا 100 ملي بـ190 جنيه. أعلى حاجة بسيطة من ميزانيتك.\n\n"
            "Dior Sauvage Elixir\n"
            "زجاجة الستور الخاصة بينا 50 ملي بـ160 جنيه.\n"
            "زجاجة اوريجنال 90 ملي بـ199.99 جنيه. أعلى حاجة بسيطة من ميزانيتك."
        )
        self.assertEqual(evidence["text"], expected_block)
        self.assertEqual([record["variant_id"] for record in evidence["quotes"]], [
            self.small.pk, self.elixir_small.pk, self.original.pk,
            self.large.pk, self.elixir_original.pk,
        ])
        self.assertEqual([(record["price"], record["quantity"]) for record in evidence["quotes"]], [
            ("123.45", 2), ("160.00", 1), ("299.99", 1), ("190.00", 1), ("199.99", 1),
        ])
        conversation = Conversation.objects.create(
            store=self.product.store, preferences={"max_price": "180.00"},
        )
        reply = append_quotation("", context)

        cleaned = sanitize_reply(reply, conversation)

        self.assertEqual(cleaned, expected_block)
        self.assertEqual(append_quotation(cleaned, context), cleaned)
        self.assertEqual(product_price_issues(cleaned, [self.product, self.elixir], evidence=evidence), [])

    def test_selection_order_and_evidence_survive_compact_append_and_validation(self):
        quotes = product_quotes([self.product], max_price=Decimal("180.00"))
        self.assertEqual([q.variant_id for q in quotes], [self.medium.pk, self.small.pk, self.large.pk])
        context = quote_context("catalogue facts", quotes, append=True, max_price=Decimal("180.00"))
        evidence = quote_evidence(context)

        self.assertEqual(evidence["quotes"], [
            {"product_id": self.product.pk, "variant_id": self.medium.pk, "name": "Dior Sauvage", "volume": 90, "bottle_type": "normal", "quantity": 1, "price": "180.00"},
            {"product_id": self.product.pk, "variant_id": self.small.pk, "name": "Dior Sauvage", "volume": 50, "bottle_type": "normal", "quantity": 1, "price": "123.45"},
            {"product_id": self.product.pk, "variant_id": self.large.pk, "name": "Dior Sauvage", "volume": 100, "bottle_type": "normal", "quantity": 1, "price": "190.00"},
        ])
        self.assertEqual(evidence["budget"], "180.00")
        self.assertTrue(evidence["append"])
        self.assertEqual(evidence["text"].count("Dior Sauvage"), 1)
        reply = append_quotation("متاح يا فندم.", context)
        self.assertEqual(reply.count(evidence["text"]), 1)
        self.assertEqual(append_quotation(reply, context), reply)
        self.assertEqual(product_price_issues(reply, [self.product], evidence=evidence), [])

    def test_compact_quantity_totals_validate_against_unchanged_unit_price_evidence(self):
        quotes = [ProductQuote.from_variant(self.small, quantity=2),
                  ProductQuote.from_variant(self.medium, quantity=3)]
        evidence = quote_evidence(quote_context("", quotes, append=True))

        self.assertEqual(evidence["text"].count("Dior Sauvage"), 1)
        self.assertIn("50 ملي × 2 بـ246.90 جنيه", evidence["text"])
        self.assertIn("90 ملي × 3 بـ540 جنيه", evidence["text"])
        self.assertEqual([(q["price"], q["quantity"]) for q in evidence["quotes"]], [("123.45", 2), ("180.00", 3)])
        self.assertEqual(product_price_issues(evidence["text"], [self.product], evidence=evidence), [])

    def test_validation_still_rejects_stale_evidence_and_unverified_extra_prices(self):
        quotes = [ProductQuote.from_variant(self.small), ProductQuote.from_variant(self.medium)]
        evidence = quote_evidence(quote_context("", quotes, append=True))
        stale = deepcopy(evidence)
        stale["quotes"][1]["price"] = "179.99"

        self.assertIn("price: quotation no longer matches its catalogue variant",
                      product_price_issues(stale["text"], [self.product], evidence=stale))
        extra_price_reply = "Dior Sauvage 50 ملي بـ1 جنيه.\n" + evidence["text"]
        self.assertTrue(product_price_issues(extra_price_reply, [self.product], evidence=evidence))
        self.assertEqual(append_quotation("", quote_context("", quotes, append=False)), "")

    def test_sanitizer_preserves_mixed_budget_groups_and_exact_evidence_in_both_orders(self):
        conversation = Conversation.objects.create(
            store=self.product.store, preferences={"max_price": "180.00"},
        )
        for first, second, quantity, expected_block in (
            (self.small, self.large, 1,
             "Dior Sauvage\nزجاجة الستور الخاصة بينا 50 ملي بـ123.45 جنيه، و100 ملي بـ190 جنيه (أعلى حاجة بسيطة من ميزانيتك)."),
            (self.large, self.small, 1,
             "Dior Sauvage\nزجاجة الستور الخاصة بينا 100 ملي بـ190 جنيه (أعلى حاجة بسيطة من ميزانيتك)، و50 ملي بـ123.45 جنيه."),
            (self.small, self.large, 2,
             "Dior Sauvage\nزجاجة الستور الخاصة بينا 50 ملي × 2 بـ246.90 جنيه، و100 ملي × 2 بـ380 جنيه (أعلى حاجة بسيطة من ميزانيتك)."),
            (self.large, self.small, 2,
             "Dior Sauvage\nزجاجة الستور الخاصة بينا 100 ملي × 2 بـ380 جنيه (أعلى حاجة بسيطة من ميزانيتك)، و50 ملي × 2 بـ246.90 جنيه."),
        ):
            with self.subTest(first=first.volume, quantity=quantity):
                quotes = [ProductQuote.from_variant(first, quantity=quantity),
                          ProductQuote.from_variant(second, quantity=quantity)]
                context = quote_context("catalogue facts", quotes, append=True,
                                        max_price=Decimal("180.00"))
                evidence = quote_evidence(context)
                self.assertEqual(evidence["text"], expected_block)
                reply = append_quotation("متاح يا فندم.", context)

                cleaned = sanitize_reply(reply, conversation)

                self.assertEqual(cleaned, "متاح يا فندم.\n\n" + expected_block)
                self.assertEqual(cleaned.count(evidence["text"]), 1)
                self.assertEqual(cleaned.count("(أعلى حاجة بسيطة من ميزانيتك)"), 1)
                self.assertEqual(append_quotation(cleaned, context), cleaned)
                self.assertEqual(product_price_issues(cleaned, [self.product], evidence=evidence), [])
