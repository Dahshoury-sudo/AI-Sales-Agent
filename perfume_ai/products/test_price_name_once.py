"""Price replies give each perfume one heading without weakening grounding."""

from decimal import Decimal
import json
from unittest.mock import patch

from django.test import TestCase

from products.models import Brand, Conversation, Product, ProductVariant, Store
from products.services.product_info import get_product_info
from products.services.comparison_service import compare_products
from products.services.product_formatting import (
    append_quotation, product_price_issues, quote_evidence, product_claim_facts,
)
from products.services.reply_sanitizer import product_claim_issues, sanitize_reply
from products.services.router import _rephrased
from products.services.sales import naming


class PriceNameOnceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="One heading")
        cls.brand = Brand.objects.create(store=cls.store, name="House")
        cls.a = Product.objects.create(store=cls.store, brand=cls.brand,
                                      name="Stronger With You", gender="male",
                                      top_notes="برغموت", base_notes="مسك")
        cls.b = Product.objects.create(store=cls.store, brand=cls.brand,
                                      name="Stronger With You Intensely", gender="male",
                                      top_notes="فانيليا", base_notes="عنبر")
        cls.numeric = Product.objects.create(store=cls.store, brand=cls.brand,
                                            name="Baccarat Rouge 540", gender="unisex")
        for product in (cls.a, cls.b, cls.numeric):
            ProductVariant.objects.create(product=product, volume=50, price=Decimal("123.45"))
            ProductVariant.objects.create(product=product, volume=90, price=Decimal("180.00"))
            ProductVariant.objects.create(product=product, volume=50, price=Decimal("299.99"),
                                          bottle_type="original", stock=2)

    def info(self, message, draft):
        by_name = {p.name: p for p in (self.a, self.b, self.numeric)}
        products = [by_name[name] for name in naming.names_in(message, by_name)]
        with patch("products.services.product_info._named_in_message", return_value=products), \
             patch("products.services.product_info.chat", return_value=draft) as chat:
            reply, context = get_product_info(message, [], self.store)
        return reply, context, chat

    def assert_grounded(self, reply, context, products):
        from products.services.price_reply import composition_issues
        evidence = quote_evidence(context)
        self.assertEqual(product_price_issues(reply, products, evidence=evidence), [])
        self.assertEqual(product_claim_issues(reply, product_claim_facts(products), context=context), [])
        self.assertEqual(composition_issues(reply, context), [])
        self.assertEqual(append_quotation(reply, context), reply)
        conversation = Conversation.objects.create(store=self.store)
        self.assertEqual(sanitize_reply(reply, conversation), reply)

    def test_duplicate_price_opener_cannot_repeat_name_or_hide_bottle_types(self):
        reply, context, _ = self.info("Stronger With You بكام؟", "عندنا Stronger With You متوفر.")
        self.assertEqual(reply.count(self.a.name), 1)
        self.assertNotIn("متوفر", reply)
        for fact in ("123.45", "180.00", "299.99", "زجاجة الستور الخاصة بينا", "زجاجة اوريجنال"):
            self.assertIn(fact, reply)
        self.assert_grounded(reply, context, [self.a])

    def test_arabic_alias_price_request_omits_redundant_bottle_and_size_openers(self):
        drafts = (
            f"{self.a.name}\nزجاجة الستور الخاصة بينا متاحة، وكمان زجاجة اوريجنال.",
            f"{self.a.name}\nزجاجة الستور الخاصة بينا 50 أو 90 ملي، وزجاجة اوريجنال 50 ملي.",
            f"{self.a.name}\nزجاجة الستور الخاصة بينا متوفرة بحجمين، وزجاجة اوريجنال متوفرة بحجم 50 ملي.",
            f"{self.a.name}\nزجاجة الستور الخاصة بينا متوفرة بحجمين، وزجاجة اوريجنال متوفرة بحجم واحد.",
        )
        for draft in drafts:
            with self.subTest(draft=draft), \
                 patch("products.services.product_info._named_in_message", return_value=[self.a]), \
                 patch("products.services.product_info.chat", return_value=draft):
                reply, context = get_product_info("سترونجر بكام؟", [], self.store)
            self.assertEqual(reply.count("زجاجة الستور الخاصة بينا"), 1)
            self.assertEqual(reply.count("زجاجة اوريجنال"), 1)
            self.assertNotIn("متاحة", reply)
            self.assert_grounded(reply, context, [self.a])

    def test_mixed_price_performance_and_notes_stay_below_one_heading(self):
        draft = "Stronger With You متوفر. Stronger With You ثباته ممتاز وفوحانه ممتاز، وفيه برغموت ومسك."
        reply, context, chat = self.info("Stronger With You بكام وثباته وفوحانه ومكوناته إيه؟", draft)
        self.assertEqual(reply.count(self.a.name), 1)
        self.assertIn("ثباته ممتاز", reply)
        self.assertIn("برغموت ومسك", reply)
        self.assertLess(reply.index(self.a.name), reply.index("برغموت"))
        self.assertNotIn("برغموت", quote_evidence(context)["text"])
        self.assertEqual(chat.call_count, 1)
        self.assert_grounded(reply, context, [self.a])

    def test_multiple_overlapping_names_keep_each_products_notes(self):
        message = "Stronger With You وStronger With You Intensely بكام ومكوناتهم إيه؟"
        draft = "Stronger With You فيه برغموت ومسك. Stronger With You Intensely فيه فانيليا وعنبر."
        reply, context, chat = self.info(message, draft)
        self.assertEqual(naming.names_in(reply, [self.a.name, self.b.name]), [self.a.name, self.b.name])
        self.assertEqual(reply.count(self.b.name), 1)
        self.assertEqual(reply.count(self.a.name), 2)  # The base spelling is inside the flanker.
        self.assertIn("برغموت ومسك", reply)
        self.assertIn("فانيليا وعنبر", reply)
        self.assertEqual(chat.call_count, 1)
        self.assert_grounded(reply, context, [self.a, self.b])

    def test_comparison_mixed_notes_is_not_reduced_to_supported_dimensions(self):
        message = "قارن Stronger With You وStronger With You Intensely في السعر والمكونات"
        with patch("products.services.product_info._named_in_message", return_value=[self.a, self.b]), \
             patch("products.services.comparison_service.chat", return_value=
                   "Stronger With You فيه برغموت. Stronger With You Intensely فيه فانيليا.") as chat:
            reply, context = compare_products(message, [], self.store)
        self.assertEqual(chat.call_count, 1)
        self.assertIn("برغموت", reply)
        self.assertIn("فانيليا", reply)
        self.assertIn("123.45", reply)
        self.assertEqual(reply.count(self.b.name), 1)
        self.assert_grounded(reply, context, [self.a, self.b])

    def test_price_comparison_keeps_gender_and_unknown_requested_dimensions(self):
        from products.services.price_reply import supported_facts_question
        Product.objects.filter(pk=self.b.pk).update(gender="female")
        for other_question, draft, answer in (
            ("أنهي رجالي وأنهي حريمي", f"{self.a.name} رجالي. {self.b.name} حريمي.", "حريمي"),
            ("التقييمات", "مش عندي تقييمات عملاء مسجلة.", "تقييمات"),
        ):
            message = f"قارن {self.a.name} و{self.b.name} في السعر و{other_question}"
            self.assertFalse(supported_facts_question(message, [self.a, self.b]))
            with self.subTest(question=other_question), \
                 patch("products.services.product_info._named_in_message", return_value=[self.a, self.b]), \
                 patch("products.services.comparison_service.chat", return_value=draft) as chat:
                reply, _ = compare_products(message, [], self.store)
            self.assertEqual(chat.call_count, 1)
            self.assertIn(answer, reply)
            self.assertIn("123.45", reply)

    def test_catalogue_gender_survives_a_failed_mixed_reply_retry(self):
        Product.objects.filter(pk=self.b.pk).update(gender="female")
        message = f"{self.a.name} و{self.b.name} بكام وأنهي رجالي وأنهي حريمي ومكوناتهم إيه؟"
        bad, context, _ = self.info(message, f"{self.a.name} فيه فانيليا. {self.b.name} فيه مسك.")
        reply, context, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context))
        before, after = reply.split(self.b.name)
        self.assertIn("رجالي", before)
        self.assertIn("حريمي", after)
        self.assert_grounded(reply, context, [self.a, Product.objects.get(pk=self.b.pk)])

    def test_authenticity_fallback_still_answers_liquid_question(self):
        bad, context, _ = self.info(f"{self.a.name} بكام وهل أصلي ولا تركيب؟", f"{self.a.name} عطر أصلي.")
        reply, context, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context))
        self.assertIn("كلها تركيب", reply)
        self.assertIn("مش متوفر عندنا عطر أصلي", reply)
        self.assert_grounded(reply, context, [self.a])

    def test_original_or_compounded_contrast_is_not_a_pure_price_selection(self):
        reply, context, _ = self.info(f"{self.a.name} بكام واوريجنال ولا تركيب؟", "كل العطور تركيب من عندنا.")
        self.assertIn("كل العطور تركيب", reply)
        self.assert_grounded(reply, context, [self.a])
        reply, context, _ = self.info(f"{self.a.name} بكام واوريجنال ولا تركيب؟", "زجاجة اوريجنال بس تركيب.")
        self.assertIn("زجاجة اوريجنال بس تركيب", reply)
        self.assert_grounded(reply, context, [self.a])

    def test_short_product_name_is_not_matched_inside_a_note(self):
        product = Product.objects.create(store=self.store, brand=self.brand, name="Pi", gender="male", top_notes="Pink Pepper")
        ProductVariant.objects.create(product=product, volume=50, price=Decimal("123.45"))
        with patch("products.services.product_info._named_in_message", return_value=[product]), \
             patch("products.services.product_info.chat", return_value="Pi فيه Pink Pepper."):
            reply, context = get_product_info("Pi بكام ومكوناته إيه؟", [], self.store)
        self.assertTrue(reply.startswith("Pi\n"))
        self.assertIn("فيه Pink Pepper.", reply)
        self.assert_grounded(reply, context, [product])

    def test_numeric_perfume_name_is_not_mistaken_for_a_generated_price(self):
        reply, context, _ = self.info("Baccarat Rouge 540 بكام؟", "Baccarat Rouge 540 متوفر.")
        self.assertEqual(reply.count(self.numeric.name), 1)
        self.assert_grounded(reply, context, [self.numeric])

    def test_generated_extra_price_and_note_remain_untrusted(self):
        reply, context, _ = self.info("Stronger With You بكام ومكوناته إيه؟",
                                      "Stronger With You فيه فانيليا، و50 ملي بـ1 جنيه.")
        self.assertTrue(product_price_issues(reply, [self.a], evidence=quote_evidence(context)))
        self.assertTrue(product_claim_issues(reply, product_claim_facts([self.a]), context=context))

    def test_retry_keeps_requested_notes_with_one_name(self):
        message = "Stronger With You بكام ومكوناته إيه؟"
        bad, context, _ = self.info(message, "Stronger With You فيه فانيليا.")
        good, good_context, _ = self.info(message, "Stronger With You فيه برغموت ومسك.")
        regenerate = lambda hint: (good, good_context)
        reply, final_context, _ = _rephrased(bad, context, [], self.store, regenerate)
        self.assertEqual(reply.count(self.a.name), 1)
        self.assertIn("برغموت ومسك", reply)
        self.assert_grounded(reply, final_context, [self.a])

    def test_failed_retry_fallback_keeps_requested_notes_and_prices(self):
        bad, context, _ = self.info("Stronger With You بكام ومكوناته إيه؟", "Stronger With You فيه فانيليا.")
        def failed(hint):
            raise RuntimeError("provider failed")
        reply, final_context, _ = _rephrased(bad, context, [], self.store, failed)
        self.assertEqual(reply.count(self.a.name), 1)
        self.assertIn("برغموت", reply)
        self.assertIn("مسك", reply)
        self.assertIn("123.45", reply)
        self.assert_grounded(reply, final_context, [self.a])

    def test_identical_generated_quote_rows_under_wrong_heading_stay_untrusted(self):
        from products.services.product_formatting import product_quotes, render_quotes
        wrong_rows = render_quotes(product_quotes([self.b]), include_name=False)
        draft = f"{self.a.name}\nفيه برغموت.\n{wrong_rows}\n{self.b.name}\nفيه فانيليا."
        reply, context, _ = self.info(f"{self.a.name} و{self.b.name} بكام ومكوناتهم إيه؟", draft)
        issues = product_price_issues(reply, [self.a, self.b], evidence=quote_evidence(context))
        self.assertTrue(issues)
        self.assertEqual(reply.count(wrong_rows.splitlines()[0]), 3)

    def test_embedded_names_are_repaired_by_existing_retry_without_misattribution(self):
        message = f"{self.a.name} و{self.b.name} بكام ومكوناتهم إيه؟"
        bad, context, _ = self.info(message, f"الأول فيه فانيليا زي {self.b.name} مقارنة بـ{self.a.name}.")
        good, good_context, _ = self.info(message, f"{self.a.name} فيه برغموت. {self.b.name} فيه فانيليا.")
        retry = lambda hint: (good, good_context)
        reply, final_context, _ = _rephrased(bad, context, [], self.store, retry)
        self.assertEqual(reply.count(self.b.name), 1)
        self.assertIn("برغموت", reply)
        self.assertIn("فانيليا", reply)
        self.assert_grounded(reply, final_context, [self.a, self.b])

    def test_ambiguous_details_fallback_does_not_reassign_notes(self):
        message = f"{self.a.name} و{self.b.name} بكام ومكوناتهم إيه؟"
        bad, context, _ = self.info(message, f"مكوناته فانيليا زي {self.b.name} و{self.a.name}.")
        reply, final_context, _ = _rephrased(bad, context, [], self.store, lambda hint: (bad, context))
        self.assertEqual(reply.count(self.b.name), 1)
        before, after = reply.split(self.b.name)
        self.assertIn("برغموت", before)
        self.assertNotIn("فانيليا", before)
        self.assertIn("فانيليا", after)
        self.assert_grounded(reply, final_context, [self.a, self.b])

    def test_value_comparison_verdict_references_heading_without_repeating_name(self):
        from products.services.comparison_service import requested_facts
        reply, _ = requested_facts("قارن السعر وسعر الملي", [self.a, self.b])
        self.assertEqual(reply.count(self.b.name), 1)
        self.assertEqual(reply.count(self.a.name), 2)
        self.assertIn("الأوفر", reply)

    def test_recommendation_with_prices_keeps_reason_under_one_heading(self):
        from products.services.ai.recommendation import recommend
        from products.services.search_service import search_products
        Product.objects.filter(pk=self.a.pk).update(scent_tags=["fresh"])
        intent = {"scent_tags": ["fresh"], "max_price": 400}
        search = search_products(intent, self.store)
        answer = json.dumps({"product_ids": [self.a.pk], "reply": f"أرشحلك {self.a.name} طابعه فريش."})
        with patch("products.services.ai.recommendation.chat", return_value=answer):
            reply, context = recommend("عايز عطر فريش في حدود 400", search["products"],
                                       store=self.store, intent=intent, search=search)
        self.assertEqual(reply.count(self.a.name), 1)
        self.assertIn("فريش", reply)
        self.assert_grounded(reply, context, [Product.objects.get(pk=self.a.pk)])

    def test_recommendation_total_footer_keeps_verified_total_and_unique_names(self):
        from products.services.ai.recommendation import recommend
        from products.services.search_service import search_products
        Product.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(scent_tags=["fresh"])
        intent = {"scent_tags": ["fresh"], "max_price": 400, "budget_scope": "total", "purchase_quantity": 2}
        search = search_products(intent, self.store)
        answer = json.dumps({"product_ids": [self.a.pk, self.b.pk],
                             "reply": f"{self.a.name} طابعه فريش. {self.b.name} طابعه فريش."})
        with patch("products.services.ai.recommendation.chat", return_value=answer):
            reply, context = recommend("عايز عطرين فريش في حدود 400", search["products"],
                                       store=self.store, intent=intent, search=search)
        self.assertEqual(reply.count(self.b.name), 1)
        self.assertIn("إجمالي الـ2 زجاجات: 246.90 جنيه.", reply)
        self.assert_grounded(reply, context, [Product.objects.get(pk=self.a.pk), Product.objects.get(pk=self.b.pk)])

    def test_recommendation_embedded_name_is_checked_before_direct_return(self):
        from products.services.ai.recommendation import recommend
        from products.services.search_service import search_products
        Product.objects.filter(pk=self.a.pk).update(scent_tags=["fresh"])
        intent = {"scent_tags": ["fresh"], "max_price": 400}
        search = search_products(intent, self.store)
        answer = json.dumps({"product_ids": [self.a.pk],
                             "reply": f"أقرب اختيار لطلبك هو {self.a.name} لأنه فريش."})
        with patch("products.services.ai.recommendation.chat", return_value=answer) as chat:
            reply, context = recommend("عايز فريش في حدود 400", search["products"],
                                       store=self.store, intent=intent, search=search)
        self.assertLessEqual(chat.call_count, 2)
        self.assertEqual(reply.count(self.a.name), 1)
        self.assertIn("فريش", reply)
        self.assert_grounded(reply, context, [Product.objects.get(pk=self.a.pk)])
