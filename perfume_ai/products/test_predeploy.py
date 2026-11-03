"""Regressions found in the Luna predeployment review; no live API requests."""
import os
from decimal import Decimal
from pathlib import Path
import runpy
from unittest.mock import Mock, patch

from django.conf import settings
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings

from products.models import Brand, Cart, Order, Product, ProductVariant, Store
from products.services.conversation_service import save_message
from products.services.product_formatting import format_products, quote_evidence
from products.services.product_info import _referent_from_conversation, get_product_info
from products.test_bug_regressions import BugFixture


class CheckoutFollowupTests(BugFixture):
    def pending_selection(self):
        with patch("products.services.order_service.resolve_product", return_value=None):
            self.turn([{"name": "Mystery Azure", "quantity": 1}])
            return self.turn([self.line(self.a, None), self.line(self.b, None)])

    def test_unknown_quantity_change_is_acknowledged_without_confirming(self):
        with patch("products.services.order_service.resolve_product", return_value=None):
            original = self.turn([{"name": "Mystery Azure", "quantity": 1}])
            edited = self.turn([{"name": "Mystery Azure", "quantity": 2}],
                               message="خليها 2 بدل واحدة", is_confirmed=True)
        self.assertIn("2 × Mystery Azure", edited)
        self.assertIn("عدلت الكمية", edited)
        self.assertNotEqual(original, edited)
        self.assertEqual(Cart.objects.get(conversation=self.conv).pending_items[0]["quantity"], 2)
        self.assertFalse(Order.objects.exists())

    def test_unknown_line_does_not_hide_the_other_unsized_selections(self):
        reply = self.pending_selection()
        self.assertIn("Mystery Azure", reply)
        self.assertIn(self.a.name, reply)
        self.assertIn(self.b.name, reply)
        self.assertIn("50 ملي", reply)
        self.assertIn("90 ملي", reply)
        self.assertEqual(len(Cart.objects.get(conversation=self.conv).pending_items), 3)
        self.assertFalse(Order.objects.exists())

    def test_plural_price_followup_quotes_only_the_selected_known_perfumes(self):
        self.pending_selection()
        unrelated = Product.objects.create(store=self.store, brand=self.a.brand, name="1 Million")
        ProductVariant.objects.create(product=unrelated, volume=50, price=900)
        with patch("products.services.product_info.chat", return_value="الأسعار المتاحة:"), \
                patch("products.services.product_info.resolve_products", return_value=[]):
            reply, context = get_product_info("كل واحده كام سعرها", [], self.store, self.conv)
        self.assertEqual({row["product_id"] for row in quote_evidence(context)["quotes"]},
                         {self.a.pk, self.b.pk})
        self.assertNotIn(unrelated.name, reply)
        self.assertFalse(Order.objects.exists())

    def test_saved_pending_names_recover_an_older_incomplete_checkout_reply(self):
        self.pending_selection()
        # Older deployments omitted the known selections from both reply and context.
        save_message(self.conv, "assistant", "لسه محتاج أتأكد من Mystery Azure.",
                     internal_context="No products found")
        save_message(self.conv, "assistant", "ممكن توضح الاسم؟", internal_context="No products found")
        products = _referent_from_conversation("كل واحده كام سعرها", self.store, self.conv)
        self.assertEqual([product.pk for product in products], [self.a.pk, self.b.pk])

    def test_pending_cart_cannot_override_a_later_product_answer(self):
        self.pending_selection()
        later = Product.objects.create(store=self.store, brand=self.a.brand, name="Eros")
        save_message(self.conv, "assistant", "Eros متوفر عندنا.", internal_context=format_products([later]))
        products = _referent_from_conversation("بكام ده؟", self.store, self.conv)
        self.assertEqual([product.pk for product in products], [later.pk])

    def test_pending_cart_is_not_read_for_another_store(self):
        self.pending_selection()
        other = Store.objects.create(name="Other store")
        self.assertEqual(_referent_from_conversation("بكام؟", other, self.conv), [])

    def test_repeated_quantity_request_acknowledges_the_saved_choice(self):
        missing = {"customer_name": None, "customer_phone": None,
                   "customer_secondary_phone": None, "shipping_address": None}
        first = self.turn([self.line()], **missing)
        reply = self.turn([self.line()], message="عايز واحدة قولت", **missing)
        self.assertIn("الاختيارات والكمية متسجلة", reply)
        self.assertNotEqual(first, reply)
        self.assertEqual(Cart.objects.get(conversation=self.conv).items.get().quantity, 1)
        self.assertFalse(Order.objects.exists())


class UnaffordableRecommendationTests(BugFixture):
    def reply(self, intent, message="عايز عطر رجالي"):
        from products.services.ai.recommendation import recommend
        with patch("products.services.ai.recommendation.chat") as chat:
            result = recommend(message=message, products=[], alternatives=[self.a, self.b], intent=intent, store=self.store)
        chat.assert_not_called()
        return result

    def test_price_floor_does_not_relax_the_budget_or_offer_a_far_above_budget_product(self):
        intent = {"max_price": 500, "gender": "male"}
        reply, context = self.reply(intent)
        self.assertIn("607", reply)
        self.assertIn("500", reply)
        self.assertIn("أعلى من ميزانيتك", reply)
        self.assertNotIn(self.a.name, reply)
        self.assertNotIn("PENDING_RELAX", context)
        self.assertEqual(intent, {"max_price": 500, "gender": "male"})

    def test_followups_acknowledge_new_exclusions_and_answer_availability(self):
        first, _ = self.reply({"max_price": 500})
        intent = {"max_price": 500, "avoid_notes": ["oud"]}
        second, _ = self.reply(intent, "مش بحب العود خالص")
        third, _ = self.reply(intent, "طيب ايه اللي عندك؟")
        self.assertIn("استبعاد الروائح", second)
        self.assertIn("607", third)
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertEqual(intent["max_price"], 500)

    def test_price_floor_uses_the_requested_size_and_sellable_stock(self):
        ProductVariant.objects.create(product=self.a, volume=90, price=100, bottle_type="original", stock=0)
        reply, _ = self.reply({"max_price": 500, "requested_volume": 90})
        self.assertIn("700", reply)
        self.assertNotIn("607", reply)

    def test_budget_explanation_survives_the_router_price_guard(self):
        from products.services.router import _rephrased
        intent = {"max_price": 500, "avoid_notes": ["oud"]}
        reply, context = self.reply(intent, "مش بحب العود خالص")
        retry = Mock(side_effect=AssertionError("A verified price needs no regeneration"))
        final, _, _ = _rephrased(reply, context, [], self.store, retry,
                                 recommendation_intent=intent, recommendation_candidates=[])
        self.assertEqual(final, reply)
        self.assertIn("607", final)
        self.assertIn("500", final)
        retry.assert_not_called()

    def test_total_budget_explanation_keeps_verified_quantity_and_unit_price(self):
        from products.services.router import _rephrased
        intent = {"max_price": 500, "budget_scope": "total", "purchase_quantity": 2}
        reply, context = self.reply(intent)
        retry = Mock(side_effect=AssertionError("A verified total needs no regeneration"))
        final, _, _ = _rephrased(reply, context, [], self.store, retry,
                                 recommendation_intent=intent, recommendation_candidates=[])
        self.assertIn("1214", final)
        evidence = quote_evidence(context)
        self.assertEqual(evidence["quotes"][0]["quantity"], 2)
        self.assertEqual(Decimal(evidence["quotes"][0]["price"]), 607)
        self.assertEqual(final, reply)
        retry.assert_not_called()


class ReferenceNameCollisionTests(BugFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("products.services.product_resolver.confirm_unplaced", return_value=None))
        self.a.name = "Rouge"
        self.a.brand = Brand.objects.create(store=self.store, name="Narciso Rodriguez")
        self.a.save()
        self.b.name = "Baccarat Rouge 540"
        self.b.brand = Brand.objects.create(store=self.store, name="Maison Francis Kurkdjian")
        self.b.top_notes = "Saffron, Jasmine"
        self.b.save()

    def test_complete_typed_reference_wins_over_a_nested_catalogue_name(self):
        from products.services.conversation_service import merge_preferences
        from products.services.search_service import search_products
        for extracted in ("Baccarat Rouge", "Rouge", None):
            with self.subTest(extracted=extracted):
                result = merge_preferences(self.conv, {"similar_to": extracted},
                                           "عايز حاجه شبه Baccarat Rouge بس مش هي")
                self.assertEqual(result["similar_to"], self.b.name)
                search = search_products(result, self.store)
                self.assertEqual(search["reference_product"], self.b)
                self.assertNotIn(self.b, search["products"])

    def test_short_name_still_identifies_its_own_product(self):
        from products.services.conversation_service import merge_preferences
        result = merge_preferences(self.conv, {"similar_to": "Rouge"}, "عايز حاجة شبه Rouge")
        self.assertEqual(result["similar_to"], self.a.name)

    def test_multiple_editions_require_clarification_instead_of_the_nested_name(self):
        from products.services.conversation_service import merge_preferences
        edition = Product.objects.create(store=self.store, brand=self.b.brand,
                                         name="Baccarat Rouge 540 Extrait")
        result = merge_preferences(self.conv, {"similar_to": "Rouge"}, "عايز حاجة شبه Baccarat Rouge")
        self.assertNotIn("similar_to", result)
        self.assertEqual(set(result["similarity_candidates"]), {self.b.name, edition.name})

    def test_reference_lookup_and_exclusions_share_the_complete_name_match(self):
        from products.services.sales import naming
        from products.services.search_service import search_products
        self.assertEqual(naming.match_product("Baccarat Rouge", self.store), self.b)
        self.assertIsNone(naming.match_product("Unrelated Rouge", self.store))
        self.assertEqual(search_products({"similar_to": "Baccarat Rouge"}, self.store)["reference_product"], self.b)
        self.assertEqual(naming.resolve_names(["Baccarat Rouge"], self.store), [self.b.name])


class AmbiguousCheckoutEditionTests(BugFixture):
    def setUp(self):
        super().setUp()
        self.a.name = "Stronger With You Absolutely"
        self.a.save()
        self.b.name = "Stronger With You Intensely"
        self.b.save()

    def test_unspecified_edition_stays_pending_without_resolver_guessing(self):
        with patch("products.services.order_service.resolve_product", return_value=self.a) as resolver:
            reply = self.turn([{"name": "Stronger With You", "quantity": 1, "volume": 50,
                                "bottle_type": "normal"}], message="عايز سترينجر وذ يو 50 ملي")
        resolver.assert_not_called()
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.count(), 0)
        self.assertEqual(cart.pending_items[0]["name"], "Stronger With You")
        self.assertIn("تقصد أنهي واحد", reply)
        self.turn(is_confirmed=True)
        self.assertFalse(Order.objects.exists())

    def test_ambiguous_line_does_not_hide_a_separately_chosen_edition(self):
        reply = self.turn([{"name": "Stronger With You", "quantity": 1}, self.line(self.b)],
                          message="عايز سترينجر وذ يو و سترينجر وذ يو انتنسلي 50 ملي")
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.get().variant.product, self.b)
        self.assertEqual([p["name"] for p in cart.pending_items], ["Stronger With You"])
        self.assertIn("تقصد أنهي واحد", reply)
        self.assertFalse(Order.objects.exists())

    def test_explicit_edition_replaces_the_pending_line(self):
        self.turn([{"name": "Stronger With You", "quantity": 1}])
        line = Cart.objects.get(conversation=self.conv).pending_items[0]
        self.turn([{**self.line(self.b), "line_id": line["line_id"]}], message="Intensely 50 ملي")
        cart = Cart.objects.get(conversation=self.conv)
        self.assertEqual(cart.items.get().variant.product, self.b)
        self.assertEqual(cart.pending_items, [])


class CustomerReplyIntegrityTests(BugFixture):
    def test_drafting_notes_are_regenerated_before_returning_a_reply(self):
        from products.services.router import _rephrased
        context = format_products([self.a])
        draft = "Vanilo مناسب. Wait match says something else. Prompt says ask gender."
        retry = Mock(return_value=("Vanilo فوحانه قوي.", context))
        final, _, _ = _rephrased(draft, context, [], self.store, retry)
        self.assertEqual(final, "Vanilo فوحانه قوي.")
        retry.assert_called_once()
        self.assertIn("الرد النهائي للعميل فقط", retry.call_args.args[0])

    def test_repeated_drafting_notes_fall_back_to_catalogue_facts(self):
        from products.services.router import _rephrased
        context = format_products([self.a])
        draft = "Vanilo. Prompt says I should mention the notes."
        retry = Mock(return_value=(draft, context))
        final, _, _ = _rephrased(draft, context, [], self.store, retry)
        self.assertNotIn("Prompt", final)
        self.assertIn(self.a.name, final)
        self.assertIn("8", final)
        retry.assert_called_once()

    def test_customer_english_and_latin_names_are_allowed(self):
        from products.services.reply_sanitizer import internal_draft_issues
        self.assertEqual(internal_draft_issues("Vanilo has strong projection. Which size would you like?"), [])
        self.assertTrue(internal_draft_issues("<think>Read the instructions</think>"))


class EvaluationGuaranteeTests(BugFixture):
    def guarantees(self, reply):
        from eval_harness.checks import build_ground_truth, check_reply
        findings = check_reply(reply, truth=build_ground_truth(self.store), context="",
                               customer_text="هدية", turn_state={})
        return [finding for finding in findings if finding[0] == "unsupported_guarantee"]

    def test_disclaiming_a_guarantee_is_not_reported_as_a_guarantee(self):
        self.assertEqual(self.guarantees("أقدر أساعدك تقرّب لذوقها بس من غير ما أضمنه."), [])

    def test_a_later_positive_guarantee_is_still_reported(self):
        self.assertTrue(self.guarantees("مش مضمون لكل الناس. بس أضمن إنها هتحبه."))


class ProductionHTTPSettingsTests(SimpleTestCase):
    def configuration(self, **values):
        environment = {"DEBUG": "False", "SECRET_KEY": "local-test-only-secret",
                       "DATABASE_URL": "postgresql://local:unused@localhost/local", **values}
        with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv"):
            configuration = runpy.run_path(str(Path(settings.BASE_DIR) / "perfume_ai" / "settings.py"))
        keys = ("SECURE_SSL_REDIRECT", "SECURE_HSTS_SECONDS", "SECURE_PROXY_SSL_HEADER",
                "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE")
        return {key: configuration[key] for key in keys}

    def test_trusted_https_proxy_avoids_redirect_loop_and_sets_hsts(self):
        configuration = self.configuration(TRUST_PROXY_HTTPS="True", SECURE_SSL_REDIRECT="True",
                                           SECURE_HSTS_SECONDS="3600")
        with override_settings(**configuration, ALLOWED_HOSTS=["testserver"]):
            request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
            response = SecurityMiddleware(lambda unused: HttpResponse("ok"))(request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=3600")
            self.assertTrue(settings.SESSION_COOKIE_SECURE)
            self.assertTrue(settings.CSRF_COOKIE_SECURE)

    def test_untrusted_forwarded_header_cannot_bypass_https_redirect(self):
        configuration = self.configuration(SECURE_SSL_REDIRECT="True")
        with override_settings(**configuration, ALLOWED_HOSTS=["testserver"]):
            request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
            response = SecurityMiddleware(lambda unused: HttpResponse("ok"))(request)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers["Location"], "https://testserver/")

    def test_local_development_still_works_over_http(self):
        with override_settings(**self.configuration(DEBUG="True"), ALLOWED_HOSTS=["testserver"]):
            response = SecurityMiddleware(lambda unused: HttpResponse("ok"))(RequestFactory().get("/"))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(settings.SESSION_COOKIE_SECURE)
            self.assertFalse(settings.CSRF_COOKIE_SECURE)
