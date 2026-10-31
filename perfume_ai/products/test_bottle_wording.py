"""Packaging copy must not change product selection or claims about the liquid."""
from django.test import SimpleTestCase

from products.services.bottle_wording import normalize_bottle_wording
from products.services.reply_sanitizer import sanitize_reply
from products.services.sales.constraints import explicit_updates
from products.services.sales.naming import tokens


class BottleWordingTests(SimpleTestCase):
    def test_legacy_copy_is_normalized_once(self):
        cases = (
            ("زجاجة البراند", "زجاجة الستور الخاصة بينا"),
            ("زجاجه البراند الخاصة بينا", "زجاجة الستور الخاصة بينا"),
            ("زجاجات البراند بتاعنا", "زجاجات الستور الخاصة بينا"),
            ("متاح بزجاجة الاستور", "متاح بزجاجة الستور الخاصة بينا"),
            ("زجاجة أوريجينال", "زجاجة اوريجنال"),
            ("زجاجة بشكل الأوريجينال", "زجاجة اوريجنال"),
            ("الزجاجات الأوريجينال", "زجاجات اوريجنال"),
            ("وزجاجة اوريجنال", "وزجاجة اوريجنال"),
        )
        for old, expected in cases:
            with self.subTest(old=old):
                self.assertEqual(normalize_bottle_wording(old), expected)
                self.assertEqual(normalize_bottle_wording(expected), expected)
                self.assertEqual(sanitize_reply(old), expected)

    def test_manufacturer_names_and_liquid_claims_are_preserved(self):
        for text in (
            "زجاجة البراند الأصلي Dior Sauvage",
            "زجاجة البراند العالمي Dior",
            "العطر تركيب من عندنا، مش أصلي من تصنيع البراند.",
            "اسم البراند Dior. الموقع: https://example.com/original-bottle",
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_bottle_wording(text), text)

    def test_bottle_aliases_keep_their_type_without_requiring_original_liquid(self):
        for label, kind in (
            ("زجاجة البراند", "normal"),
            ("زجاجة الاستور", "normal"),
            ("زجاجة الإستور", "normal"),
            ("زجاجة المحل", "normal"),
            ("زجاجة الستور", "normal"),
            ("زجاجة الستور الخاصة بينا", "normal"),
            ("زجاجة اوريجنال", "original"),
            ("زجاجة أوريجينال", "original"),
            ("زجاجة بشكل الأوريجينال", "original"),
        ):
            with self.subTest(label=label):
                updates = explicit_updates(f"هاخد Dior Sauvage 50 مل {label}")
                self.assertEqual(updates["bottle_type"], kind)
                self.assertNotIn("requires_original_liquid", updates)
                self.assertEqual(tokens(f"Dior Sauvage {label}"), tokens("Dior Sauvage"))
        self.assertNotIn("bottle_type", explicit_updates("عايز البراند بتاعكم"))
        self.assertNotIn("bottle_type", explicit_updates("الفرق بين زجاجة الستور وزجاجة اوريجنال؟"))
        self.assertNotIn("bottle_type", explicit_updates("مش عايز زجاجة اوريجنال"))
        self.assertEqual(explicit_updates("مش عايز زجاجة اوريجنال، هاخد زجاجة الستور")["bottle_type"], "normal")

    def test_variant_price_check_understands_new_and_legacy_labels(self):
        from eval_harness.checks import check_variant_prices

        truth = {"variant_prices": {"Dior Sauvage": [
            {"volume": 50, "price": "400", "bottle_type": "normal"},
            {"volume": 50, "price": "900", "bottle_type": "original"},
        ]}}
        for label, correct, wrong in (
            ("زجاجة الستور الخاصة بينا", 400, 900),
            ("زجاجة البراند", 400, 900),
            ("زجاجة اوريجنال", 900, 400),
            ("زجاجة أوريجينال", 900, 400),
        ):
            with self.subTest(label=label):
                self.assertFalse(check_variant_prices(f"Dior Sauvage {label} 50 مل بـ{correct} جنيه", truth))
                self.assertTrue(check_variant_prices(f"Dior Sauvage {label} 50 مل بـ{wrong} جنيه", truth))
