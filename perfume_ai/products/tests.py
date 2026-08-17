import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from products.models import Brand, Product, ProductVariant, Store, StoreSettings
from products.services.ai.recommendation import _coerce_budget, _format_products
from products.services.search_service import (
    MAX_PRODUCTS_IN_CONTEXT,
    search_products,
)


class ProductContextCapTests(TestCase):
    """The prompt-size cap on how many products reach the AI.

    Without it, the "no exact match" branch of search_products handed the entire
    filtered catalogue to recommend(), which serialises ~15 lines of prompt text
    per product into a single request.
    """

    PRODUCT_COUNT = MAX_PRODUCTS_IN_CONTEXT * 2

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Perfamix Test")
        cls.brand = Brand.objects.create(store=cls.store, name="Dior")

        for i in range(cls.PRODUCT_COUNT):
            product = Product.objects.create(
                store=cls.store,
                brand=cls.brand,
                name=f"Test Perfume {i}",
                gender="male",
                perfume_type="western",
                season="All Seasons",
                occasion="Casual",
                longevity="8 hours",
                projection="Moderate",
                top_notes="Citrus",
                middle_notes="Jasmine",
                base_notes="Cedar",
                description="A test perfume.",
                # Varied so the ordering has something to sort on.
                oil_stock_grams=100 + i,
                concentration_percentage=30,
            )
            ProductVariant.objects.create(
                product=product, volume=50, price=500, bottle_type="normal"
            )

    def test_exact_match_path_is_capped(self):
        """A broad intent that matches everything still yields a bounded list."""
        results = search_products({"gender": "male"}, store=self.store)

        self.assertIsNone(results["alternatives"])
        self.assertEqual(len(results["products"]), MAX_PRODUCTS_IN_CONTEXT)

    def test_alternatives_path_is_capped(self):
        """The regression: no exact match used to return the whole catalogue."""
        # A note no product has forces exact to come back empty while base stays full.
        results = search_products(
            {"gender": "male", "notes": ["unobtainium"]}, store=self.store
        )

        self.assertFalse(results["products"].exists())
        self.assertEqual(len(results["alternatives"]), MAX_PRODUCTS_IN_CONTEXT)

    def test_shortlist_is_deterministic(self):
        """The prompts tell the model to stay on one perfume once the customer
        shows interest, so the shortlist must not reshuffle between turns."""
        intent = {"gender": "male", "notes": ["unobtainium"]}

        first = [p.id for p in search_products(intent, store=self.store)["alternatives"]]
        second = [p.id for p in search_products(intent, store=self.store)["alternatives"]]

        self.assertEqual(first, second)

    def test_shortlist_prefers_products_with_more_oil_stock(self):
        """Leading with empty shelves wastes the shortlist, since the prompts
        instruct the model to skip anything marked out of stock."""
        results = search_products({"gender": "male"}, store=self.store)
        stocks = [p.oil_stock_grams for p in results["products"]]

        self.assertEqual(stocks, sorted(stocks, reverse=True))
        # The lowest-stock products should not have made the cut at all.
        self.assertGreater(min(stocks), 100)

    def test_formatter_caps_an_unsliced_queryset(self):
        """Defensive net: a caller bypassing search_products can't blow up the
        prompt either."""
        every_product = Product.objects.filter(store=self.store)
        self.assertEqual(every_product.count(), self.PRODUCT_COUNT)

        context = _format_products(every_product)

        self.assertEqual(context.count("Name (الاسم الصحيح):"), MAX_PRODUCTS_IN_CONTEXT)


class IntentExtractionIsLazyTests(TestCase):
    """extract_intent is only needed by the recommendation branch.

    It used to run on every message: the router fired it alongside classify in a
    ThreadPoolExecutor, and because the `with` block exits via
    shutdown(wait=True) it blocked on both calls and then discarded the intent on
    every branch except recommendation.
    """

    def _route(self, classification, message="عايز عطر"):
        """Run the router with the classifier stubbed, returning the intent mock."""
        with mock.patch("products.services.router.classify", return_value=classification), \
             mock.patch("products.services.router.extract_intent") as intent, \
             mock.patch("products.services.router.handle_general", return_value=("ok", "")), \
             mock.patch("products.services.router.compare_products", return_value=("ok", "")), \
             mock.patch("products.services.router.get_product_info", return_value=("ok", "")):
            intent.return_value = {"gender": "male", "max_price": 500.0}
            from products.services.router import route
            route(message, history=[], store=None, conversation=None)
        return intent

    def test_greeting_does_not_extract_intent(self):
        self.assertFalse(self._route("greeting").called)

    def test_faq_does_not_extract_intent(self):
        self.assertFalse(self._route("faq").called)

    def test_out_of_domain_does_not_extract_intent(self):
        self.assertFalse(self._route("out_of_domain").called)

    def test_product_info_does_not_extract_intent(self):
        self.assertFalse(self._route("product_info").called)

    def test_comparison_does_not_extract_intent(self):
        self.assertFalse(self._route("comparison").called)

    def test_recommendation_does_extract_intent(self):
        """The one branch that must still call it."""
        with mock.patch("products.services.router.classify", return_value="recommendation"), \
             mock.patch("products.services.router.extract_intent") as intent, \
             mock.patch("products.services.router.search_products") as search, \
             mock.patch("products.services.router.recommend", return_value=("ok", "")):
            intent.return_value = {"gender": "male", "max_price": 500.0}
            search.return_value = {"products": Product.objects.none(), "alternatives": None}
            from products.services.router import route
            route("رشحلي عطر رجالي بـ 500", history=[], store=None, conversation=None)

        self.assertTrue(intent.called)


class MetaWebhookSignatureTests(TestCase):
    """Comment webhooks used to skip signature verification entirely.

    The `feed` (Facebook) and `comments` (Instagram) branches never called
    verify_signature, so anyone who knew the page ID — public information — could
    post fake comment events and make the bot reply publicly and send DMs.
    """

    APP_SECRET = "test-app-secret"

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.settings_obj = StoreSettings.objects.create(
            store=self.store,
            facebook_page_id="PAGE123",
            instagram_account_id="IG456",
            meta_app_secret=self.APP_SECRET,
        )
        self.url = reverse("meta-webhook")

    def _sign(self, body: bytes) -> str:
        return "sha256=" + hmac.new(
            self.APP_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()

    def _fb_comment_payload(self):
        return {
            "object": "page",
            "entry": [{
                "id": "PAGE123",
                "changes": [{
                    "field": "feed",
                    "value": {
                        "item": "comment", "verb": "add",
                        "comment_id": "C1", "sender_id": "USER9",
                        "message": "بكام العطر ده؟", "post_id": "P1",
                    },
                }],
            }],
        }

    def _ig_comment_payload(self):
        return {
            "object": "instagram",
            "entry": [{
                "id": "IG456",
                "changes": [{
                    "field": "comments",
                    "value": {
                        "id": "C2", "text": "بكام؟",
                        "from": {"id": "USER9"}, "media": {"id": "M1"},
                    },
                }],
            }],
        }

    def _post(self, payload, signature=None):
        body = json.dumps(payload).encode()
        headers = {}
        if signature is not None:
            headers["HTTP_X_HUB_SIGNATURE_256"] = signature
        return self.client.post(
            self.url, data=body, content_type="application/json", **headers
        )

    def test_fb_comment_without_signature_is_rejected(self):
        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(self._fb_comment_payload())

        self.assertEqual(response.status_code, 403)
        task.assert_not_called()

    def test_fb_comment_with_bad_signature_is_rejected(self):
        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(self._fb_comment_payload(), signature="sha256=deadbeef")

        self.assertEqual(response.status_code, 403)
        task.assert_not_called()

    def test_fb_comment_with_valid_signature_is_processed(self):
        payload = self._fb_comment_payload()
        body = json.dumps(payload).encode()
        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(payload, signature=self._sign(body))

        self.assertEqual(response.status_code, 200)
        task.assert_called_once()

    def test_ig_comment_without_signature_is_rejected(self):
        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(self._ig_comment_payload())

        self.assertEqual(response.status_code, 403)
        task.assert_not_called()

    def test_ig_comment_with_valid_signature_is_processed(self):
        payload = self._ig_comment_payload()
        body = json.dumps(payload).encode()
        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(payload, signature=self._sign(body))

        self.assertEqual(response.status_code, 200)
        task.assert_called_once()

    @override_settings(META_REQUIRE_WEBHOOK_SIGNATURE=False)
    def test_store_without_secret_is_allowed_during_rollout_step_one(self):
        self.settings_obj.meta_app_secret = ""
        self.settings_obj.save()

        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(self._fb_comment_payload())

        self.assertEqual(response.status_code, 200)
        task.assert_called_once()

    @override_settings(META_REQUIRE_WEBHOOK_SIGNATURE=True)
    def test_store_without_secret_is_rejected_once_enforcement_is_on(self):
        self.settings_obj.meta_app_secret = ""
        self.settings_obj.save()

        with mock.patch("products.tasks.process_comment_async") as task:
            response = self._post(self._fb_comment_payload())

        self.assertEqual(response.status_code, 403)
        task.assert_not_called()


class BudgetLabellingTests(TestCase):
    """Sizes are labelled against the customer's stated budget.

    search_products matches a product when ANY variant is within budget, which is
    correct — a 500 EGP customer genuinely can afford a cheap 50ml. But the
    formatter then printed every size with its price and no affordability signal,
    so a customer who said 500 could be shown a 3800 EGP bottle as a normal
    option.
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Perfamix Test")
        cls.brand = Brand.objects.create(store=cls.store, name="Dior")
        cls.product = Product.objects.create(
            store=cls.store,
            brand=cls.brand,
            name="Dior Sauvage",
            gender="male",
            oil_stock_grams=1000,
            concentration_percentage=30,
        )
        # 400 is affordable on a 500 budget, 550 is a plausible upsell (10% over),
        # 3800 is not something to put in front of that customer.
        ProductVariant.objects.create(
            product=cls.product, volume=50, price=400, bottle_type="normal"
        )
        ProductVariant.objects.create(
            product=cls.product, volume=90, price=550, bottle_type="normal"
        )
        ProductVariant.objects.create(
            product=cls.product, volume=100, price=3800, bottle_type="original", stock=5
        )

    def _context(self, max_price):
        return _format_products(
            Product.objects.filter(pk=self.product.pk), max_price=max_price
        )

    def _line_for(self, context, price):
        return [line for line in context.splitlines() if price in line][0]

    def test_in_budget_size_is_marked_affordable(self):
        self.assertIn("✅", self._line_for(self._context(500), "400"))

    def test_slightly_over_budget_size_is_marked_as_an_upsell(self):
        """budget_note explicitly asks for near-budget sizes to be offered."""
        self.assertIn("⚠️", self._line_for(self._context(500), "550"))

    def test_far_over_budget_size_is_marked_do_not_offer(self):
        """The regression: this line used to be indistinguishable from the 400 one."""
        self.assertIn("❌", self._line_for(self._context(500), "3800"))

    def test_no_budget_means_no_labels(self):
        """Customers who never stated a budget must see the unchanged format."""
        context = self._context(None)

        for marker in ("داخل الميزانية", "أعلى شوية من الميزانية", "أعلى من الميزانية بكتير"):
            self.assertNotIn(marker, context)

    def test_every_size_is_still_listed(self):
        """Labelling, not hiding — the model still needs prices to answer questions."""
        context = self._context(500)

        for price in ("400", "550", "3800"):
            self.assertIn(price, context)

    def test_boundary_price_equal_to_budget_is_in_budget(self):
        self.assertIn("✅", self._line_for(self._context(400), "400"))


class BudgetCoercionTests(TestCase):
    """The intent schema asks for a float, but models return strings too.

    int("500.0") raises ValueError, which would have 500ed the recommendation
    branch on a budget the LLM formatted slightly differently.
    """

    def test_accepts_numbers(self):
        self.assertEqual(_coerce_budget(500), 500)
        self.assertEqual(_coerce_budget(500.0), 500)

    def test_accepts_numeric_strings(self):
        self.assertEqual(_coerce_budget("500"), 500)
        self.assertEqual(_coerce_budget("500.0"), 500)

    def test_rejects_unusable_values(self):
        for value in (None, "", "غالي", "abc", [], {}):
            self.assertIsNone(_coerce_budget(value))

    def test_rejects_zero_and_negative(self):
        """Zero or negative is meaningless, and the falsy checks downstream already
        treat it as 'no budget given'."""
        self.assertIsNone(_coerce_budget(0))
        self.assertIsNone(_coerce_budget(-100))
