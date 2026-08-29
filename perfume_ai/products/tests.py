import base64
import hashlib
import hmac
import inspect
import json
from decimal import Decimal
from unittest import mock

from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.conf import settings
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from products.models import (
    Brand,
    Cart,
    CartItem,
    Conversation,
    Message,
    Notification,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    StaticFAQ,
    Store,
    StoreMonthlyUsage,
    StoreSettings,
)
from products.admin import OrderAdmin
from products.encryption import normalize_phone, phone_blind_index
from products.throttles import ChatThrottle, LoginThrottle, StoreKeyThrottle
from products.services.ai import client as ai_client
from products.services.ai.classifier import classify
from products.services.ai.intent import extract_intent
from products.services.ai.prompts import get_system_prompt
from products.services.ai.recommendation import (
    _coerce_budget,
    _format_products,
)
from products.services.comparison_service import compare_products
from products.services.conversation_service import (
    build_llm_history,
    get_or_create_platform_conversation,
    merge_preferences,
    save_message,
)
from products.services.general_service import handle_general
from products.services.meta_service import (
    conversation_platform_for,
    send_platform_message,
)
from products.services.product_formatting import (
    format_product,
    format_products,
    value_pick_note,
)
from products.services.product_info import get_product_info
from products.services import identification_service
from products.services.product_resolver import resolve_products
from products.services.reply_sanitizer import (
    sanitize_reply,
    soften_marketing_language,
    strip_premature_closing,
)
from products.services.sales import (
    constraints as sales_constraints,
    naming as sales_naming,
    notes as sales_notes,
    objection as sales_objection,
    ranking as sales_ranking,
    similarity as sales_similarity,
    stage as sales_stage,
    value as sales_value,
)
from products.services.usage_service import (
    messages_used_this_month,
    monthly_cap,
    record_llm_message,
)
from products.services.order_service import (
    PAYMENT_FALLBACK,
    _cart_context,
    _looks_like_phone,
    create_order_in_db,
    get_cart,
    handle_order,
)
from products.services.router import (
    _count_recent_repetitions,
    _detect_semantic_repetition,
    _finalize,
    _is_goodbye_loop,
    _is_repetitive,
    _was_already_handed_off,
    route,
)
from products.services.search_service import (
    MAX_PRODUCTS_IN_CONTEXT,
    search_products,
)
from products.tasks import (
    COMMENT_REPLY_DELAY_RANGE,
    process_comment_async,
    process_comment_task,
    process_incoming_message,
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
            )
            ProductVariant.objects.create(
                # Varied so the cheapest-first ordering has something to sort on. This
                # used to be a varied oil_stock_grams, back when the shortlist led with
                # the most bulk oil.
                product=product, volume=50, price=500 + i, bottle_type="normal"
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

    def test_shortlist_leads_with_the_cheapest_brand_bottle(self):
        """Replaces test_shortlist_prefers_products_with_more_oil_stock.

        That test asserted the shortlist led with the most bulk oil, which was the
        ordering before oil tracking was removed. Something still has to order the
        shortlist deterministically — the prompts tell the model to stay on a perfume once
        the customer shows interest — and cheapest-brand-bottle-first is the deliberate
        replacement for an ordering by inventory depth, which was commercially arbitrary.
        """
        results = search_products({"gender": "male"}, store=self.store)
        prices = [
            min(
                variant.price
                for variant in product.variants.all()
                if variant.bottle_type == "normal"
            )
            for product in results["products"]
        ]

        self.assertEqual(prices, sorted(prices))

    def test_formatter_caps_an_unsliced_queryset(self):
        """Defensive net: a caller bypassing search_products can't blow up the
        prompt either."""
        every_product = Product.objects.filter(store=self.store)
        self.assertEqual(every_product.count(), self.PRODUCT_COUNT)

        context = _format_products(every_product)

        self.assertEqual(context.count("Name (الاسم الصحيح):"), MAX_PRODUCTS_IN_CONTEXT)


class ExcludeNamesTests(TestCase):
    """Asking for alternatives must not return the same perfumes again.

    This is the only "don't repeat that recommendation" mechanism in the system, and
    it is the right one: ai/intent.py tells the model to fill exclude_names *only*
    when the customer asks for something else, and search_products drops those from
    the queryset — so the model cannot mention them rather than being asked not to.
    recommend() previously carried a second, prompt-level version of this that
    excluded any perfume merely *mentioned* earlier, including one the customer had
    just shown interest in. That one is gone; this is what replaced it, so it needs
    to actually work.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        brand = Brand.objects.create(store=self.store, name="YSL")
        for name in ("Black Opium", "Good Girl", "Libre"):
            product = Product.objects.create(
                store=self.store, brand=brand, name=name, gender="female",
                )
            ProductVariant.objects.create(
                product=product, volume=50, price=600, bottle_type="normal"
            )

    def _names_for(self, intent):
        return sorted(p.name for p in search_products(intent, store=self.store)["products"])

    def test_an_excluded_perfume_is_not_offered_again(self):
        names = self._names_for({"gender": "female", "exclude_names": ["Black Opium"]})

        self.assertNotIn("Black Opium", names)
        self.assertEqual(names, ["Good Girl", "Libre"])

    def test_several_exclusions_apply_together(self):
        names = self._names_for(
            {"gender": "female", "exclude_names": ["Black Opium", "Libre"]}
        )

        self.assertEqual(names, ["Good Girl"])

    def test_exclusion_is_case_insensitive(self):
        """The model echoes names back from history, not from the database."""
        names = self._names_for({"gender": "female", "exclude_names": ["black opium"]})

        self.assertNotIn("Black Opium", names)

    def test_the_legacy_singular_key_still_works(self):
        names = self._names_for({"gender": "female", "exclude_name": "Good Girl"})

        self.assertNotIn("Good Girl", names)

    def test_no_exclusions_returns_everything(self):
        self.assertEqual(
            self._names_for({"gender": "female"}),
            ["Black Opium", "Good Girl", "Libre"],
        )


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


class CartPersistenceTests(TestCase):
    """The order cart survives a truncated conversation history.

    get_conversation_messages caps history at 8 messages, but the extractor prompt
    used to demand the model "reconstruct the FULL pending shopping cart every
    time" from that history. Past four turns the cart silently emptied: a perfume
    chosen early, or a name given five turns back, was simply gone.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store,
            brand=self.brand,
            name="Dior Sauvage",
            gender="male",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _turn(self, extracted):
        """Run one order turn with the extractor's JSON stubbed.

        The reply is persisted the way products/tasks.py does it. That matters for
        confirmation: order_service only honours is_confirmed if this conversation
        already contains the summary carrying the total, so a helper that dropped the
        reply could never reach a legitimate confirmation.
        """
        payload = {
            "customer_name": None, "customer_phone": None,
            "customer_secondary_phone": None, "shipping_address": None,
            "products": [], "is_confirmed": False,
        }
        payload.update(extracted)
        with mock.patch(
            "products.services.order_service.chat", return_value=json.dumps(payload)
        ):
            reply, context = handle_order("...", [], self.store, self.conversation)

        save_message(self.conversation, "assistant", reply, internal_context=context)
        return reply, context

    ALL_DETAILS = {
        "customer_name": "محمد",
        "customer_phone": "01000000000",
        "customer_secondary_phone": "01100000000",
        "shipping_address": "القاهرة، المعادي، ٥ شارع النصر",
    }

    def _confirm(self, **extra):
        """The real two-step confirmation: summary first, then the customer agrees."""
        self._turn({"products": self.ONE_PERFUME, **self.ALL_DETAILS, **extra})
        return self._turn(
            {"products": self.ONE_PERFUME, **self.ALL_DETAILS, "is_confirmed": True, **extra}
        )

    ONE_PERFUME = [{"name": "Dior Sauvage", "quantity": 1, "volume": 50, "bottle_type": "normal"}]

    def test_chosen_perfume_is_saved_to_the_cart(self):
        self._turn({"products": self.ONE_PERFUME})

        cart = Cart.objects.get(conversation=self.conversation)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().variant, self.variant)

    def test_cart_survives_a_turn_that_mentions_no_products(self):
        """The regression. Turn 2 simulates a truncated window: the extractor can
        no longer see the perfume, so it returns an empty product list."""
        self._turn({"products": self.ONE_PERFUME})

        reply, _ = self._turn({"customer_name": "محمد", "products": []})

        cart = Cart.objects.get(conversation=self.conversation)
        self.assertEqual(cart.items.count(), 1, "the cart was emptied")
        self.assertNotIn("أنهي عطر", reply, "bot asked which perfume again")

    def test_customer_details_accumulate_across_turns(self):
        """Name given early must still be there when the address arrives late."""
        self._turn({"products": self.ONE_PERFUME, "customer_name": "محمد"})
        self._turn({"products": self.ONE_PERFUME, "customer_phone": "01000000000"})
        self._turn({"products": self.ONE_PERFUME, "shipping_address": "القاهرة"})

        cart = Cart.objects.get(conversation=self.conversation)
        self.assertEqual(cart.customer_name, "محمد")
        self.assertEqual(cart.customer_phone, "01000000000")
        self.assertEqual(cart.shipping_address, "القاهرة")

    def test_changing_the_size_does_not_duplicate_the_line(self):
        big = ProductVariant.objects.create(
            product=self.product, volume=90, price=600, bottle_type="normal"
        )
        self._turn({"products": self.ONE_PERFUME})
        self._turn({"products": [
            {"name": "Dior Sauvage", "quantity": 1, "volume": 90, "bottle_type": "normal"}
        ]})

        cart = Cart.objects.get(conversation=self.conversation)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().variant, big)

    def test_confirming_creates_an_order_and_clears_the_cart(self):
        reply, _ = self._confirm()

        self.assertIn("تم تأكيد طلبك بنجاح", reply)
        order = Order.objects.get(store=self.store)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.customer_name, "محمد")
        self.assertFalse(
            Cart.objects.filter(conversation=self.conversation).exists(),
            "cart should be gone so a second order starts clean",
        )

    def test_a_summary_is_shown_before_the_order_is_created(self):
        """The turn where every detail first arrives must summarise, not confirm."""
        reply, _ = self._turn({"products": self.ONE_PERFUME, **self.ALL_DETAILS})

        self.assertIn("💰 الإجمالي:", reply)
        self.assertFalse(Order.objects.exists())

    def test_is_confirmed_alone_cannot_create_an_order(self):
        """A spurious true from the extractor must not move stock. The model runs on
        a reasoning model at its default temperature, so true/false can flip between
        runs on identical input — the summary having gone out is the real evidence."""
        reply, _ = self._turn(
            {"products": self.ONE_PERFUME, **self.ALL_DETAILS, "is_confirmed": True}
        )

        self.assertFalse(
            Order.objects.exists(), "an order was confirmed without a summary being sent"
        )
        self.assertIn("💰 الإجمالي:", reply)

    def test_an_earlier_orders_summary_cannot_confirm_a_later_one(self):
        """clear_cart drops the row on completion and get_cart makes a fresh one, so
        the guard is scoped to summaries at or after the current cart's creation."""
        self._confirm()
        self.assertEqual(Order.objects.count(), 1)

        # Second order in the same conversation: the first summary is still in the
        # thread, but it predates this cart.
        reply, _ = self._turn(
            {"products": self.ONE_PERFUME, **self.ALL_DETAILS, "is_confirmed": True}
        )

        self.assertEqual(Order.objects.count(), 1, "the stale summary confirmed a new order")
        self.assertIn("💰 الإجمالي:", reply)

    def test_confirming_a_brand_bottle_takes_no_tracked_stock(self):
        """Replaces test_confirming_decrements_stock.

        A brand bottle is compounded to order, so confirming one consumes nothing the
        system counts. The oil ledger this used to assert against is gone — the order
        itself is the record. Original bottles still decrement; that is covered by
        OriginalBottleStockTests.
        """
        self._confirm()

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)

    def test_carts_are_isolated_per_conversation(self):
        other = Conversation.objects.create(store=self.store)
        self._turn({"products": self.ONE_PERFUME})

        self.assertEqual(Cart.objects.get(conversation=self.conversation).items.count(), 1)
        self.assertFalse(Cart.objects.filter(conversation=other).exists())


class CartCancellationTests(TestCase):
    """Cancelling an order that was never confirmed.

    An in-progress order lives in a Cart and has taken no stock, so cancelling it
    is just dropping the cart. Before carts existed the router only looked for a
    pending Order, so "الغي الاوردر" mid-flow silently did nothing.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage",
            gender="male", )
        self.variant = ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _cancel(self):
        with mock.patch("products.services.router.classify", return_value="order_cancel"):
            from products.services.router import route
            return route("الغي الاوردر", [], self.store, self.conversation)

    def test_cancelling_an_unconfirmed_cart_clears_it(self):
        cart = Cart.objects.create(conversation=self.conversation)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=1, bottle_type="normal")

        reply, _ = self._cancel()

        self.assertFalse(Cart.objects.filter(conversation=self.conversation).exists())
        self.assertIn("إلغاء", reply)

    def test_cancelling_a_confirmed_order_restores_original_bottles(self):
        """Replaces the oil-restoring version.

        Only originals hold stock now, so an original is what a cancellation has to give
        back. A cancelled brand bottle restores nothing because it consumed nothing.
        """
        original = ProductVariant.objects.create(
            product=self.product, volume=100, price=800,
            bottle_type="original", stock=4,
        )
        order = Order.objects.create(
            store=self.store, customer_name="محمد", customer_phone="0100",
            shipping_address="القاهرة", total_price=800, status="pending",
            conversation=self.conversation,
        )
        OrderItem.objects.create(
            order=order, variant=original, quantity=1,
            bottle_type="original", price_at_time_of_order=800,
        )

        self._cancel()

        order.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(original.stock, 5)

    def test_cancelling_a_brand_bottle_order_restores_nothing(self):
        order = Order.objects.create(
            store=self.store, customer_name="محمد", customer_phone="0100",
            shipping_address="القاهرة", total_price=400, status="pending",
            conversation=self.conversation,
        )
        OrderItem.objects.create(
            order=order, variant=self.variant, quantity=1,
            bottle_type="normal", price_at_time_of_order=400,
        )

        self._cancel()

        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")

    def test_cancelling_with_nothing_active_says_so(self):
        reply, _ = self._cancel()

        self.assertIn("مفيش طلب نشط", reply)


class OrderGuardTests(TestCase):
    """Guards against the extractor's output becoming a bad Order.

    A real end-to-end run confirmed two orders with no contact details: the cart
    context printed "(مش متوفر)" for unknown fields, the model echoed it back as
    the customer's name, and a non-empty string passed the missing-field checks.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage",
            gender="male", )
        self.variant = ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    ONE_PERFUME = [{"name": "Dior Sauvage", "quantity": 1, "volume": 50, "bottle_type": "normal"}]

    def _turn(self, extracted):
        payload = {
            "customer_name": None, "customer_phone": None,
            "customer_secondary_phone": None, "shipping_address": None,
            "products": [], "is_confirmed": False,
        }
        payload.update(extracted)
        with mock.patch(
            "products.services.order_service.chat", return_value=json.dumps(payload)
        ):
            return handle_order("...", [], self.store, self.conversation)

    def test_cart_context_never_shows_placeholder_text(self):
        """The source of the bug: readable placeholders got echoed back as data."""
        cart = get_cart(self.conversation)
        context = _cart_context(cart)

        self.assertNotIn("مش متوفر", context)
        self.assertIn("null", context, "unknown fields should read as JSON null")

    def test_non_numeric_phone_is_treated_as_missing(self):
        reply, _ = self._turn({
            "products": self.ONE_PERFUME,
            "customer_name": "محمد",
            "customer_phone": "(مش متوفر)",
            "customer_secondary_phone": "(مش متوفر)",
            "shipping_address": "القاهرة",
            "is_confirmed": True,
        })

        self.assertFalse(
            Order.objects.exists(), "an order was created without a real phone number"
        )
        self.assertIn("موبايل", reply, "bot should ask for the phone number")

    def test_confirmation_without_details_does_not_create_an_order(self):
        """The exact turn-6 failure: 'تمام' on a summary full of placeholders."""
        self._turn({"products": self.ONE_PERFUME})
        reply, _ = self._turn({"products": self.ONE_PERFUME, "is_confirmed": True})

        self.assertFalse(Order.objects.exists())
        self.assertIn("ناقصني", reply)

    def test_phone_with_spaces_and_dashes_is_accepted(self):
        """The guard must not reject numbers a real customer would type."""
        self.assertTrue(_looks_like_phone("0100 000 0000"))
        self.assertTrue(_looks_like_phone("010-1234-567"))
        self.assertFalse(_looks_like_phone("(مش متوفر)"))
        self.assertFalse(_looks_like_phone("غير معروف"))
        self.assertFalse(_looks_like_phone(""))
        self.assertFalse(_looks_like_phone(None))


class PerStorePaymentInstructionsTests(TestCase):
    """Payment details come from the store, not from a hardcoded string.

    create_order_in_db used to emit one store's InstaPay link and handle to every
    store's customers. With two active stores in the database, the second store's
    first order would have directed that customer to pay the first store.
    """

    LEAKED = "perfamix2"

    def setUp(self):
        self.store = Store.objects.create(name="Misk Fragrance")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage",
            gender="male", )
        self.variant = ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _confirm_order(self):
        return create_order_in_db(
            store=self.store, name="محمد", phone="01000000000",
            secondary_phone="01100000000", address="القاهرة",
            total_price=400,
            items_to_create=[{
                "variant": self.variant, "quantity": 1,
                "price": self.variant.price, "bottle_type": "normal",
            }],
            context_str="", conversation=self.conversation,
        )

    def test_store_payment_instructions_are_used(self):
        StoreSettings.objects.create(
            store=self.store,
            payment_instructions="💳 فودافون كاش: 01234567890",
        )

        reply, _ = self._confirm_order()

        self.assertIn("فودافون كاش: 01234567890", reply)
        self.assertNotIn(self.LEAKED, reply)

    def test_store_without_instructions_gets_the_safe_fallback(self):
        """The regression. Must never hand out another store's payment account."""
        StoreSettings.objects.create(store=self.store)

        reply, _ = self._confirm_order()

        self.assertNotIn(self.LEAKED, reply, "another store's payment account leaked")
        self.assertNotIn("إنستاباي", reply)
        self.assertIn(PAYMENT_FALLBACK, reply)

    def test_store_with_no_settings_row_still_confirms(self):
        """A store missing StoreSettings entirely must not break the order."""
        reply, _ = self._confirm_order()

        self.assertIn("تم تأكيد طلبك بنجاح", reply)
        self.assertIn(PAYMENT_FALLBACK, reply)
        self.assertNotIn(self.LEAKED, reply)

    def test_whitespace_only_instructions_count_as_unset(self):
        StoreSettings.objects.create(store=self.store, payment_instructions="   \n  ")

        reply, _ = self._confirm_order()

        self.assertIn(PAYMENT_FALLBACK, reply)

    def test_order_number_and_confirmation_still_present(self):
        StoreSettings.objects.create(store=self.store, payment_instructions="ادفع كذا")

        reply, _ = self._confirm_order()
        order = Order.objects.get(store=self.store)

        self.assertIn("تم تأكيد طلبك بنجاح", reply)
        self.assertIn(f"#{order.id}", reply)


class PerStorePromptFactsTests(TestCase):
    """Store-specific claims must come from the store, not the shared prompt.

    prompts.py asserted one store's business model as universal truth to every
    store's customers: the 28-36g oil ratio, "brand bottles come in 50ml and 90ml
    only", a ~90% match claim, and "we have a physical branch, come visit".
    """

    def setUp(self):
        self.store = Store.objects.create(name="Misk Fragrance")

    def test_store_without_facts_makes_no_factual_claims(self):
        StoreSettings.objects.create(store=self.store)

        prompt = get_system_prompt(self.store)

        for leaked in ("28 إلي 36", "16 إلى 20", "90%", "فرع وستور على أرض الواقع"):
            self.assertNotIn(leaked, prompt, f"leaked another store's claim: {leaked}")

    def test_store_facts_are_injected_when_set(self):
        StoreSettings.objects.create(
            store=self.store, business_facts="- الأحجام: 30 ملي و 60 ملي بس."
        )

        self.assertIn("الأحجام: 30 ملي و 60 ملي بس.", get_system_prompt(self.store))

    def test_store_with_no_settings_row_still_builds_a_prompt(self):
        prompt = get_system_prompt(self.store)

        self.assertIn(self.store.name, prompt)
        self.assertNotIn("28 إلي 36", prompt)

    def test_no_bottle_image_means_the_bot_is_told_not_to_offer_photos(self):
        StoreSettings.objects.create(store=self.store)

        self.assertIn("ممنوع تكتب [SEND_BOTTLE_IMAGE]", get_system_prompt(self.store))

    def test_configured_bottle_image_enables_the_image_rules(self):
        StoreSettings.objects.create(
            store=self.store, bottle_image_url="https://example.com/bottles.jpg"
        )

        prompt = get_system_prompt(self.store)

        self.assertIn("واكتب الكلمة السرية دي في ردك: [SEND_BOTTLE_IMAGE]", prompt)

    def test_custom_system_prompt_still_applies(self):
        """The pre-existing per-store hook must keep working."""
        StoreSettings.objects.create(store=self.store, system_prompt="اتكلم بالفصحى.")

        self.assertIn("اتكلم بالفصحى.", get_system_prompt(self.store))


class PerStoreBottleImageTests(TestCase):
    """The bottle photo comes from the store, not a single global env var.

    settings.BOTTLE_IMAGE_URL was one value for every store, so a second store's
    customers were sent the first store's bottles and packaging.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Misk Fragrance")
        self.settings_obj = StoreSettings.objects.create(
            store=self.store,
            facebook_page_id="PAGE123",
            instagram_account_id="IG456",
            messenger_access_token="tok",
        )
        self.conversation = Conversation.objects.create(
            store=self.store, platform="instagram", platform_sender_id="USER9"
        )

    def _send(self, text="دي صور الزجاجات: [SEND_BOTTLE_IMAGE]"):
        with mock.patch("products.services.meta_service.send_instagram_image") as img, \
             mock.patch("products.services.meta_service.send_instagram_message") as msg:
            send_platform_message(self.conversation, text)
        return img, msg

    def test_no_configured_image_sends_text_only(self):
        """The regression: this used to send the other store's packaging."""
        img, msg = self._send()

        img.assert_not_called()
        msg.assert_called_once()
        self.assertNotIn("[SEND_BOTTLE_IMAGE]", msg.call_args[0][2])

    def test_configured_image_is_sent(self):
        self.settings_obj.bottle_image_url = "https://example.com/misk.jpg"
        self.settings_obj.save()

        img, _ = self._send()

        img.assert_called_once()
        self.assertEqual(img.call_args[0][2], "https://example.com/misk.jpg")

    def test_instagram_images_go_through_the_facebook_page_id(self):
        """Instagram Messaging sends via the Page ID. This passed the IG account
        ID, so image sends failed."""
        self.settings_obj.bottle_image_url = "https://example.com/misk.jpg"
        self.settings_obj.save()

        img, msg = self._send()

        self.assertEqual(img.call_args[0][0], "PAGE123")
        self.assertEqual(img.call_args[0][0], msg.call_args[0][0])


# ── Router heuristics ───────────────────────────────────────────────────────
# The layer that runs before classification on every message. Previously
# untested, and every bug found in this codebase surfaced through execution
# rather than review.

def _bot(content):
    return {"role": "assistant", "content": content}


def _user(content):
    return {"role": "user", "content": content}


# What the musk (router.py:353), promotion (:403) and handoff (:491) branches
# instruct the bot to say. Wording varies because those branches also tell it to
# vary — the shared substrings are what matter.
SCRIPTED_HANDOFF_REPLIES = [
    "معلش يا فندم، حولت المحادثة لفريق خدمة العملاء وهيتواصلوا معاك في أقرب وقت.",
    "أنا حولت رسالتك لفريق المبيعات وهيتواصلوا معاك قريب. تحت أمرك في أي حاجة تانية.",
    "تم، حولت طلبك لفريق خدمة العملاء وهيتواصلوا معاك حالاً.",
]


class SemanticRepetitionDetectorTests(TestCase):
    """_detect_semantic_repetition counted the router's own scripted output.

    "حولت طلبك", "فريق خدمة العملاء" and "هيتواصلوا معاك" were treated as evidence
    the bot was stuck, but three branches instruct it to say exactly that. Three
    such replies in the last four bot turns pushed the count to 3 and route()
    hijacked the turn with random products — reachable by asking about offers, then
    musk, then for a human.
    """

    def test_scripted_handoff_replies_are_not_counted_as_being_stuck(self):
        """The regression. Fails before the fix with a count of 3."""
        history = []
        for reply in SCRIPTED_HANDOFF_REPLIES:
            history.append(_user("عايز اكلم حد"))
            history.append(_bot(reply))

        self.assertLess(
            _detect_semantic_repetition(history), 3,
            "scripted handoff wording still trips the stuck-in-a-loop threshold",
        )

    def test_genuinely_repeated_vague_question_is_still_caught(self):
        """The detector must keep doing its actual job."""
        history = []
        for _ in range(3):
            history.append(_user("مش عارف"))
            history.append(_bot("قولي بتحب الفريش ولا التقيل؟"))

        self.assertGreaterEqual(_detect_semantic_repetition(history), 3)

    def test_needs_at_least_three_bot_messages(self):
        history = [_user("هاي"), _bot("قولي بتحب الفريش ولا التقيل؟")]

        self.assertEqual(_detect_semantic_repetition(history), 0)

    def test_empty_history_is_zero(self):
        self.assertEqual(_detect_semantic_repetition([]), 0)
        self.assertEqual(_detect_semantic_repetition(None), 0)

    def test_only_the_last_four_bot_messages_count(self):
        """An old vague question shouldn't be held against the bot forever."""
        history = [_bot("قولي بتحب الفريش ولا التقيل؟")]
        for i in range(4):
            history.append(_bot(f"ريحته حلوة ومناسبة للشتا، رقم {i}"))

        self.assertEqual(_detect_semantic_repetition(history), 0)


class HandoffLoopDetectionTests(TestCase):
    """_was_already_handed_off is what actually prevents repeat handoffs.

    This is why the semantic detector doesn't need to police handoff wording.
    """

    def test_detects_a_previous_handoff(self):
        for reply in SCRIPTED_HANDOFF_REPLIES:
            self.assertTrue(
                _was_already_handed_off([_bot(reply)]),
                f"missed a handoff in: {reply[:40]}",
            )

    def test_ordinary_replies_are_not_handoffs(self):
        history = [_bot("ريحته فريش وثباته حوالي 8 ساعات."), _user("تمام")]

        self.assertFalse(_was_already_handed_off(history))

    def test_a_user_saying_it_does_not_count(self):
        """Only the assistant's messages mean a handoff happened."""
        history = [_user("انتو حولت طلبي لفريق خدمة العملاء ومحدش رد")]

        self.assertFalse(_was_already_handed_off(history))

    def test_empty_history(self):
        self.assertFalse(_was_already_handed_off([]))
        self.assertFalse(_was_already_handed_off(None))


class TextRepetitionTests(TestCase):
    """_is_repetitive and _count_recent_repetitions, the SequenceMatcher checks."""

    def test_near_identical_response_is_repetitive(self):
        history = [_bot("ريحته فريش وثباته حوالي 8 ساعات ومناسب للصيف.")]

        self.assertTrue(
            _is_repetitive("ريحته فريش وثباته حوالي 8 ساعات ومناسب للصيف!", history)
        )

    def test_different_response_is_not_repetitive(self):
        history = [_bot("ريحته فريش وثباته حوالي 8 ساعات ومناسب للصيف.")]

        self.assertFalse(_is_repetitive("تمام، الـ 90 ملي بـ 600 جنيه.", history))

    def test_no_history_is_never_repetitive(self):
        self.assertFalse(_is_repetitive("أي كلام", []))
        self.assertFalse(_is_repetitive("أي كلام", None))

    def test_consecutive_run_is_counted(self):
        same = "تحت أمرك يا فندم، أقدر أساعدك إزاي؟"
        history = [_bot(same), _bot(same), _bot(same)]

        self.assertEqual(_count_recent_repetitions(history), 2)

    def test_run_stops_at_the_first_different_message(self):
        same = "تحت أمرك يا فندم، أقدر أساعدك إزاي؟"
        history = [_bot(same), _bot("الـ 90 ملي بـ 600 جنيه."), _bot(same)]

        self.assertEqual(_count_recent_repetitions(history), 0)

    def test_single_message_has_no_run(self):
        self.assertEqual(_count_recent_repetitions([_bot("أهلاً")]), 0)
        self.assertEqual(_count_recent_repetitions([]), 0)


class GoodbyeLoopTests(TestCase):
    """_is_goodbye_loop short-circuits route() before any LLM call."""

    def test_two_consecutive_goodbyes_is_a_loop(self):
        history = [_user("سلام"), _user("سلام")]

        self.assertTrue(_is_goodbye_loop(history))

    def test_one_goodbye_is_not_a_loop(self):
        self.assertFalse(_is_goodbye_loop([_user("سلام")]))

    def test_a_real_question_breaks_the_run(self):
        history = [_user("سلام"), _user("عايز اعرف سعر سوفاج")]

        self.assertFalse(_is_goodbye_loop(history))

    def test_long_message_starting_with_a_goodbye_word_is_not_a_goodbye(self):
        """The 30-character limit exists so a real question isn't mistaken for one."""
        history = [
            _user("شكرا جدا على المساعدة بس عايز اسأل عن حاجة تانية مهمة"),
            _user("شكرا جدا على المساعدة بس عايز اسأل عن حاجة تانية مهمة"),
        ]

        self.assertFalse(_is_goodbye_loop(history))

    def test_empty_history(self):
        self.assertFalse(_is_goodbye_loop([]))
        self.assertFalse(_is_goodbye_loop(None))


class RouterHijackTests(TestCase):
    """route() diverts to an anti-repetition prompt when the detectors fire.

    router.py:154 pulls 3 random products and tells the bot to change the subject.
    Correct when the bot really is looping; wrong when it was the router's own
    scripted handoff wording that tripped it.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        for i in range(3):
            product = Product.objects.create(
                store=self.store, brand=self.brand, name=f"Perfume {i}",
                gender="male", )
            ProductVariant.objects.create(
                product=product, volume=50, price=400, bottle_type="normal"
            )

    def _route_with(self, history, classification="faq"):
        """Returns the prompt handle_general received, to see which path ran."""
        with mock.patch("products.services.router.classify", return_value=classification), \
             mock.patch("products.services.router.handle_general", return_value=("ok", "")) as general:
            from products.services.router import route
            route("طيب", history, self.store, None)
        return general.call_args[0][0]

    def test_scripted_handoffs_do_not_trigger_the_hijack(self):
        """The regression, at the route() level rather than the detector's."""
        history = []
        for reply in SCRIPTED_HANDOFF_REPLIES:
            history.append(_user("عايز اكلم حد"))
            history.append(_bot(reply))

        prompt = self._route_with(history)

        self.assertNotIn("منتجات متوفرة يمكنك اقتراحها", prompt)
        self.assertNotIn("استخدمت نفس العبارات", prompt)

    def test_a_real_loop_still_triggers_the_hijack(self):
        same = "قولي بتحب الفريش ولا التقيل؟"
        history = [_bot(same), _bot(same), _bot(same)]

        prompt = self._route_with(history)

        self.assertIn("استخدمت نفس العبارات", prompt)
        # Products must come from the database, never invented.
        self.assertIn("منتجات متوفرة يمكنك اقتراحها", prompt)
        self.assertIn("Perfume", prompt)


class MessengerDMWebhookTests(TestCase):
    """The `messaging` branch — Messenger and Instagram DMs.

    This carries most real traffic (626 conversations across messenger, instagram
    and whatsapp) and had no coverage, despite sharing the verify_signature that
    was changed to add the enforcement flag.
    """

    APP_SECRET = "test-app-secret"

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        StoreSettings.objects.create(
            store=self.store,
            facebook_page_id="PAGE123",
            instagram_account_id="IG456",
            meta_app_secret=self.APP_SECRET,
        )
        self.url = reverse("meta-webhook")

    def _post(self, payload, sign=True):
        body = json.dumps(payload).encode()
        headers = {}
        if sign:
            headers["HTTP_X_HUB_SIGNATURE_256"] = "sha256=" + hmac.new(
                self.APP_SECRET.encode(), body, hashlib.sha256
            ).hexdigest()
        with mock.patch("products.tasks.process_message_async") as task:
            response = self.client.post(
                self.url, data=body, content_type="application/json", **headers
            )
        return response, task

    def _dm(self, recipient_id, message=None):
        return {
            "object": "page",
            "entry": [{
                "id": recipient_id,
                "messaging": [{
                    "sender": {"id": "USER9"},
                    "recipient": {"id": recipient_id},
                    "message": message if message is not None else {"text": "عندكم سوفاج؟"},
                }],
            }],
        }

    def test_messenger_dm_is_routed_as_messenger(self):
        response, task = self._post(self._dm("PAGE123"))

        self.assertEqual(response.status_code, 200)
        task.assert_called_once()
        store_id, platform, sender_id, text = task.call_args[0]
        self.assertEqual((platform, sender_id, text), ("messenger", "USER9", "عندكم سوفاج؟"))
        self.assertEqual(store_id, self.store.id)

    def test_instagram_dm_is_routed_as_instagram(self):
        """Resolved by falling through to instagram_account_id."""
        response, task = self._post(self._dm("IG456"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(task.call_args[0][1], "instagram")

    def test_unsigned_dm_is_rejected(self):
        response, task = self._post(self._dm("PAGE123"), sign=False)

        self.assertEqual(response.status_code, 403)
        task.assert_not_called()

    def test_unknown_recipient_is_skipped_without_erroring(self):
        response, task = self._post(self._dm("SOMEONE_ELSE"))

        self.assertEqual(response.status_code, 200)
        task.assert_not_called()

    def test_echo_of_our_own_message_is_ignored(self):
        """Without this the bot would answer itself in a loop."""
        response, task = self._post(
            self._dm("PAGE123", {"text": "أهلاً يا فندم", "is_echo": True})
        )

        self.assertEqual(response.status_code, 200)
        task.assert_not_called()

    def test_delivery_and_read_receipts_are_ignored(self):
        for noise in ("delivery", "read"):
            payload = {
                "object": "page",
                "entry": [{
                    "id": "PAGE123",
                    "messaging": [{
                        "sender": {"id": "USER9"},
                        "recipient": {"id": "PAGE123"},
                        noise: {"watermark": 1},
                    }],
                }],
            }
            response, task = self._post(payload)

            self.assertEqual(response.status_code, 200)
            task.assert_not_called()

    def test_non_text_message_is_ignored(self):
        """An image or sticker with no text must not reach the router."""
        response, task = self._post(
            self._dm("PAGE123", {"attachments": [{"type": "image"}]})
        )

        self.assertEqual(response.status_code, 200)
        task.assert_not_called()


class UnknownVersusUnclearTests(TestCase):
    """A clear question the bot can't answer must not be called unclear.

    Three separate questions all returned "مش فاهم قصد حضرتك" — "عندكم 90 ملي؟",
    "بتحطوا كام جرام زيت؟" and "العطر أصلي ولا تركيب؟". All are perfectly clear;
    they just name no perfume, so resolve_products found nothing and the not-found
    branch treated that as an unintelligible message. Telling a customer you don't
    understand a clear question reads as a brush-off.

    These pin the prompt wording, since the behaviour itself depends on the model.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Misk Fragrance")

    def _not_found_instructions(self):
        """The instructions used when no product resolves from the message."""
        with mock.patch(
            "products.services.product_info.resolve_products", return_value=[]
        ), mock.patch(
            "products.services.product_info.chat", return_value="ok"
        ) as chat_mock:
            get_product_info("عندكم 200 ملي؟", [], self.store)
        return chat_mock.call_args[0][0][-1]["content"]

    def test_clear_store_questions_are_a_distinct_case(self):
        instructions = self._not_found_instructions()

        self.assertIn("سؤال واضح عن الستور", instructions)
        for example in ("عندكم 90 ملي؟", "بتحطوا كام جرام زيت؟", "العطر أصلي ولا تركيب؟"):
            self.assertIn(example, instructions)

    def test_saying_i_dont_understand_to_a_clear_question_is_forbidden(self):
        instructions = self._not_found_instructions()

        self.assertIn("ممنوع تماماً ترد على السؤال ده بـ \"مش فاهم قصد حضرتك\"", instructions)

    def test_unknown_answers_defer_instead_of_claiming_confusion(self):
        self.assertIn("هسأل وأرد عليك", self._not_found_instructions())

    def test_unavailable_size_should_be_answered_with_what_is_available(self):
        self.assertIn("مش متوفرة واذكرله المتاح فعلاً", self._not_found_instructions())

    def test_genuinely_unintelligible_messages_still_get_clarification(self):
        """The narrow case where "مش فاهم" remains correct."""
        instructions = self._not_found_instructions()

        self.assertIn("الرسالة نفسها غير مفهومة فعلاً", instructions)
        self.assertIn("حروف عشوائية", instructions)

    def test_base_prompt_separates_not_knowing_from_not_understanding(self):
        prompt = get_system_prompt(self.store)

        self.assertIn("هسأل وأرد عليك", prompt)
        # The persona was reworded during consolidation; the distinction it protects
        # is unchanged — "مش فاهم" is only for genuinely unintelligible messages,
        # never for a clear question whose answer the bot does not have.
        self.assertIn('ممنوع تقول "مش فاهم" لسؤال واضح', prompt)
        self.assertIn("للرسائل المش مفهومة فعلاً بس", prompt)


class CommentReplyDelayTests(TestCase):
    """The comment reply delay must not occupy a worker.

    process_comment_task used to time.sleep(20-40) inside the worker. There is no
    queue routing in this project, so comment tasks share the default queue and
    the --concurrency=2 pool with customer DMs and WhatsApp messages: two comments
    held both slots for up to 40s and live conversations queued behind them.
    """

    def test_the_task_no_longer_sleeps(self):
        """The regression. A sleeping task holds an execution slot outright."""
        self.assertNotIn("time.sleep", inspect.getsource(process_comment_task))

    def test_the_delay_is_scheduled_as_a_countdown(self):
        with mock.patch.object(process_comment_task, "apply_async") as scheduled:
            process_comment_async(1, "facebook", "C1", "USER9", "بكام؟", "P1")

        scheduled.assert_called_once()
        countdown = scheduled.call_args.kwargs["countdown"]
        low, high = COMMENT_REPLY_DELAY_RANGE
        self.assertGreaterEqual(countdown, low)
        self.assertLessEqual(countdown, high)

    def test_the_delay_is_short_enough_not_to_crowd_out_messages(self):
        """countdown frees the execution slot but still holds a broker
        reservation, and worker_prefetch_multiplier is 1 — so the window has to
        stay small, not merely move."""
        low, high = COMMENT_REPLY_DELAY_RANGE

        self.assertGreater(low, 0, "some delay is wanted, replies shouldn't look instant")
        self.assertLessEqual(high, 10, "long delays crowd out customer messages")

    def test_all_task_arguments_are_forwarded(self):
        with mock.patch.object(process_comment_task, "apply_async") as scheduled:
            process_comment_async(7, "instagram", "C2", "USER1", "عندكم مسك؟", "P9")

        self.assertEqual(
            scheduled.call_args.kwargs["args"],
            [7, "instagram", "C2", "USER1", "عندكم مسك؟", "P9"],
        )


class HandoffReplyDeliveryTests(TestCase):
    """Every platform a conversation can have must actually reach the customer.

    views_meta.py stores platform="facebook" for conversations that began as a
    comment on a Page post, but that value was in neither PLATFORM_CHOICES nor
    send_platform_message's dispatch. Replying from the handoff dashboard saved the
    message, returned {"status": "Message sent"}, logged "Unknown platform
    'facebook'", and sent nothing. Confirmed against the endpoint before fixing.
    """

    def setUp(self):
        self.owner = User.objects.create_user("owner", "owner@example.com", "pw")
        self.store = Store.objects.create(name="Perfamix Test", owner=self.owner)
        StoreSettings.objects.create(
            store=self.store,
            facebook_page_id="PAGE123",
            instagram_account_id="IG456",
            whatsapp_phone_number_id="WA789",
            messenger_access_token="tok",
            meta_access_token="tok",
        )
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION="Bearer " + str(RefreshToken.for_user(self.owner).access_token)
        )

    def _reply_on(self, platform):
        """Reply through the real endpoint; returns which send API was used."""
        conversation = Conversation.objects.create(
            store=self.store, platform=platform,
            platform_sender_id="USER9", needs_human=True,
        )
        with mock.patch("products.services.meta_service.send_messenger_message") as messenger, \
             mock.patch("products.services.meta_service.send_instagram_message") as instagram, \
             mock.patch("products.services.meta_service.send_whatsapp_message") as whatsapp:
            response = self.client.post(
                f"/api/handoff/conversations/{conversation.id}/reply/",
                {"message": "اهلا يا فندم، معاك خدمة العملاء"},
                format="json",
            )
        used = (
            "messenger" if messenger.called
            else "instagram" if instagram.called
            else "whatsapp" if whatsapp.called
            else None
        )
        return response, used, conversation

    def test_facebook_comment_conversation_is_delivered(self):
        """The regression: this sent nothing at all."""
        response, used, _ = self._reply_on("facebook")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            used, "messenger",
            "a private reply to a Facebook commenter goes out through the Page Send API",
        )

    def test_facebook_is_a_recognised_platform_value(self):
        """views_meta.py has always written it, so the model should list it."""
        self.assertIn("facebook", dict(Conversation.PLATFORM_CHOICES))

    def test_messenger_instagram_and_whatsapp_still_route_correctly(self):
        for platform, expected in [
            ("messenger", "messenger"),
            ("instagram", "instagram"),
            ("whatsapp", "whatsapp"),
        ]:
            with self.subTest(platform=platform):
                _, used, _ = self._reply_on(platform)
                self.assertEqual(used, expected)

    def test_web_conversations_send_nothing_externally(self):
        """Web chat is polled by the widget; there is no channel to push to."""
        _, used, _ = self._reply_on("web")

        self.assertIsNone(used)

    def test_the_reply_is_saved_and_the_conversation_is_flagged(self):
        _, _, conversation = self._reply_on("facebook")

        self.assertEqual(conversation.messages.count(), 1)
        message = conversation.messages.first()
        # "agent", not "assistant": this is a human colleague speaking. Stored as
        # "assistant" it read back to the bot as its own prior output once the
        # handoff was resolved, so the bot adopted the human's voice and claims.
        self.assertEqual(message.role, "agent")
        conversation.refresh_from_db()
        self.assertTrue(conversation.needs_human)


class CommentAndDMShareOneConversationTests(TestCase):
    """A Facebook comment and a later DM from the same person are one thread.

    get_or_create_platform_conversation keys on (store, platform, sender_id).
    Comments were filed under "facebook" and DMs under "messenger", so the same
    customer got two separate conversations and the bot lost the comment context
    the moment the chat moved to DMs. Their sender ids are the same page-scoped id
    — confirmed against real data — so one label joins them.
    """

    SENDER_ID = "24812345678901234"

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        StoreSettings.objects.create(
            store=self.store, facebook_page_id="PAGE123",
            instagram_account_id="IG456", messenger_access_token="tok",
        )

    def test_facebook_comments_are_filed_under_messenger(self):
        self.assertEqual(conversation_platform_for("facebook"), "messenger")

    def test_other_sources_are_unchanged(self):
        for source in ("messenger", "instagram", "whatsapp", "web"):
            self.assertEqual(conversation_platform_for(source), source)

    def test_instagram_is_deliberately_not_remapped(self):
        """IG comment ids and IG DM ids are different id spaces, so relabelling
        would merge nothing — it needs the Send API's recipient_id instead."""
        self.assertEqual(conversation_platform_for("instagram"), "instagram")

    def test_a_comment_then_a_dm_reuse_the_same_conversation(self):
        """The regression: this used to create two separate conversations."""
        # Comment arrives first.
        comment_conversation, _ = get_or_create_platform_conversation(
            self.store, conversation_platform_for("facebook"), self.SENDER_ID
        )
        # The same person then sends a Messenger DM, as the webhook would file it.
        dm_conversation, created = get_or_create_platform_conversation(
            self.store, "messenger", self.SENDER_ID
        )

        self.assertEqual(comment_conversation.id, dm_conversation.id)
        self.assertFalse(created, "the DM started a second conversation")
        self.assertEqual(Conversation.objects.filter(store=self.store).count(), 1)

    def test_the_comment_task_files_the_conversation_under_messenger(self):
        """End to end through the task, with the Meta calls mocked."""
        with mock.patch("products.tasks.fetch_post_content", return_value=""), \
             mock.patch("products.tasks.reply_to_comment"), \
             mock.patch("products.tasks.send_private_reply"), \
             mock.patch("products.tasks.route", return_value=("رد البوت", "")):
            result = process_comment_task.apply(
                args=[self.store.id, "facebook", "C1", self.SENDER_ID, "بكام؟", "P1"]
            )

        self.assertTrue(result.successful(), result.result)
        conversation = Conversation.objects.get(store=self.store)
        self.assertEqual(conversation.platform, "messenger")
        self.assertEqual(conversation.platform_sender_id, self.SENDER_ID)

    def test_the_public_comment_reply_still_uses_the_facebook_endpoint(self):
        """Remapping the conversation must not change which endpoint replies."""
        with mock.patch("products.tasks.fetch_post_content", return_value=""), \
             mock.patch("products.tasks.reply_to_comment") as facebook_reply, \
             mock.patch("products.tasks.reply_to_ig_comment") as instagram_reply, \
             mock.patch("products.tasks.send_private_reply"), \
             mock.patch("products.tasks.route", return_value=("رد البوت", "")):
            process_comment_task.apply(
                args=[self.store.id, "facebook", "C1", self.SENDER_ID, "بكام؟", "P1"]
            )

        self.assertTrue(facebook_reply.called)
        self.assertFalse(instagram_reply.called)


class DeliveryOutcomeTests(TestCase):
    """A rejected send must never look like a delivered one.

    The send_* helpers catch their errors, log, and return None. Nothing checked,
    so the handoff dashboard answered "Message sent" whether or not the platform
    accepted the message — its try/except could never fire, because the helpers
    swallow everything. Meta rejects any recipient without an app role while the
    app is in development mode, so delivery is partial and silent.
    """

    def setUp(self):
        self.owner = User.objects.create_user("owner2", "owner2@example.com", "pw")
        self.store = Store.objects.create(name="Perfamix Test", owner=self.owner)
        StoreSettings.objects.create(
            store=self.store, facebook_page_id="PAGE123",
            messenger_access_token="tok", meta_access_token="tok",
        )
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION="Bearer " + str(RefreshToken.for_user(self.owner).access_token)
        )

    def _conversation(self, platform="messenger"):
        return Conversation.objects.create(
            store=self.store, platform=platform,
            platform_sender_id="USER9", needs_human=True,
        )

    def test_accepted_send_reports_true(self):
        with mock.patch(
            "products.services.meta_service.send_messenger_message",
            return_value={"message_id": "m1"},
        ):
            self.assertIs(send_platform_message(self._conversation(), "اهلا"), True)

    def test_rejected_send_reports_false(self):
        """The helpers return None on failure; that must surface as False."""
        with mock.patch(
            "products.services.meta_service.send_messenger_message", return_value=None
        ):
            self.assertIs(send_platform_message(self._conversation(), "اهلا"), False)

    def test_web_reports_none_not_false(self):
        """Web needs no send at all — that is not a delivery failure."""
        self.assertIsNone(send_platform_message(self._conversation("web"), "اهلا"))

    def test_missing_token_reports_false(self):
        settings_obj = self.store.settings
        settings_obj.messenger_access_token = ""
        settings_obj.meta_access_token = ""
        settings_obj.save()

        self.assertIs(send_platform_message(self._conversation(), "اهلا"), False)

    def test_dashboard_reports_failure_instead_of_message_sent(self):
        """The regression: this returned 200 {"status": "Message sent"}."""
        conversation = self._conversation()
        with mock.patch(
            "products.services.meta_service.send_messenger_message", return_value=None
        ):
            response = self.client.post(
                f"/api/handoff/conversations/{conversation.id}/reply/",
                {"message": "اهلا يا فندم"}, format="json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["status"], "not_delivered")
        # Still saved, so the owner can see what they tried to send.
        self.assertEqual(conversation.messages.count(), 1)

    def test_dashboard_reports_success_when_accepted(self):
        conversation = self._conversation()
        with mock.patch(
            "products.services.meta_service.send_messenger_message",
            return_value={"message_id": "m1"},
        ):
            response = self.client.post(
                f"/api/handoff/conversations/{conversation.id}/reply/",
                {"message": "اهلا يا فندم"}, format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Message sent")

    def test_web_conversations_are_reported_as_sent(self):
        """Saving IS delivery for web — the widget polls the thread."""
        conversation = self._conversation("web")
        response = self.client.post(
            f"/api/handoff/conversations/{conversation.id}/reply/",
            {"message": "اهلا يا فندم"}, format="json",
        )

        self.assertEqual(response.status_code, 200)


class CartClearedTests(TestCase):
    """Removing your only perfume must not put it straight back.

    The cart-restore fallback treats an empty product list as "the extractor lost
    track" and reinstates the saved items — which is what makes the cart survive a
    truncated history. It could not tell that apart from a deliberate removal, so
    "شيله" on a single-item cart re-added the item. The extractor now says which
    one it meant via cart_cleared.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage",
            gender="male", )
        self.variant = ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    ONE_PERFUME = [{"name": "Dior Sauvage", "quantity": 1, "volume": 50, "bottle_type": "normal"}]

    def _turn(self, extracted):
        payload = {
            "customer_name": None, "customer_phone": None,
            "customer_secondary_phone": None, "shipping_address": None,
            "products": [], "is_confirmed": False, "cart_cleared": False,
        }
        payload.update(extracted)
        with mock.patch(
            "products.services.order_service.chat", return_value=json.dumps(payload)
        ):
            return handle_order("...", [], self.store, self.conversation)

    def test_clearing_the_cart_empties_it(self):
        """The regression: the item used to come straight back."""
        self._turn({"products": self.ONE_PERFUME})
        self.assertEqual(get_cart(self.conversation).items.count(), 1)

        reply, _ = self._turn({"products": [], "cart_cleared": True})

        self.assertEqual(get_cart(self.conversation).items.count(), 0)
        self.assertIn("شلت الطلب", reply)

    def test_an_empty_list_without_the_flag_still_restores_the_cart(self):
        """The truncation fallback must keep working — that's why it exists."""
        self._turn({"products": self.ONE_PERFUME})

        self._turn({"products": [], "cart_cleared": False})

        self.assertEqual(get_cart(self.conversation).items.count(), 1)

    def test_clearing_takes_no_stock(self):
        self._turn({"products": self.ONE_PERFUME})
        self._turn({"products": [], "cart_cleared": True})

        self.assertFalse(Order.objects.exists())


class UnifiedProductFormattingTests(TestCase):
    """One renderer for product data, instead of four copies.

    The block was duplicated across the recommendation prompt, both branches of
    product_info, and the comparison prompt — and had drifted: only product_info
    told the model a perfume's type, and comparison used a bare "Name:" instead of
    "Name (الاسم الصحيح):", the label that tells it to use the database spelling.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage",
            gender="male", perfume_type="western", season="All Seasons",
            occasion="Casual", longevity="8 hours", projection="Strong",
            top_notes="Bergamot", middle_notes="Pepper", base_notes="Ambroxan",
            description="Fresh spicy", )
        # A brand bottle is compounded to order, so it is always available.
        ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        # The out-of-stock case is now an original with no units left. It used to be a
        # 200ml brand bottle needing 60g of oil against 20g in stock — but brand bottles
        # can no longer be out of stock at all, so an original is the only thing that can
        # still land in the out-of-stock block.
        ProductVariant.objects.create(
            product=self.product, volume=200, price=900,
            bottle_type="original", stock=0,
        )
        self.queryset = Product.objects.filter(pk=self.product.pk)

    def test_every_field_is_rendered(self):
        context = format_products(self.queryset)

        for field in (
            "Name (الاسم الصحيح): Dior Sauvage", "Brand: Dior", "Stock Status:",
            "Original Bottle:", "Available Sizes & Prices:", "Out of Stock Sizes",
            "Gender: male", "Perfume Type:", "Season: All Seasons", "Occasion: Casual",
            "Longevity: 8 hours", "Projection: Strong", "Top Notes: Bergamot",
            "Middle Notes: Pepper", "Base Notes: Ambroxan", "Description: Fresh spicy",
        ):
            self.assertIn(field, context)

    def test_perfume_type_is_no_longer_missing_from_recommendations(self):
        """The drift: recommendation and comparison omitted it."""
        self.assertIn("Perfume Type: عطور غربية", _format_products(self.queryset))

    def test_comparison_uses_the_same_name_label(self):
        """It used a bare "Name:", losing the use-the-database-spelling hint."""
        context = format_products(self.queryset)

        self.assertIn("Name (الاسم الصحيح):", context)

    def test_out_of_stock_sizes_are_separated_from_available_ones(self):
        context = format_products(self.queryset)
        available_block = context.split("Out of Stock Sizes")[0]

        self.assertIn("50 ملي", available_block)
        self.assertNotIn("200 ملي", available_block)
        self.assertIn("200 ملي", context.split("Out of Stock Sizes")[1])

    def test_brief_form_drops_the_detail_fields(self):
        """Used for alternatives, where the model only needs to name something."""
        context = format_products(self.queryset, brief=True)

        self.assertIn("Name (الاسم الصحيح):", context)
        self.assertIn("Perfume Type:", context)
        for omitted in ("Stock Status:", "Out of Stock Sizes", "Top Notes:", "Season:"):
            self.assertNotIn(omitted, context)

    def test_budget_labels_only_appear_when_a_budget_is_given(self):
        self.assertNotIn("داخل الميزانية", format_products(self.queryset))
        self.assertIn("داخل الميزانية", format_products(self.queryset, max_price=500))

    def test_the_limit_caps_the_number_of_products(self):
        for i in range(5):
            product = Product.objects.create(
                store=self.store, brand=self.brand, name=f"Extra {i}",
                gender="male", )
            ProductVariant.objects.create(
                product=product, volume=50, price=400, bottle_type="normal"
            )

        context = format_products(Product.objects.filter(store=self.store), limit=2)

        self.assertEqual(context.count("Name (الاسم الصحيح):"), 2)


class HotLookupIndexTests(TestCase):
    """Indexes on the columns every inbound message touches."""

    def test_webhook_store_lookup_columns_are_indexed(self):
        indexed = {
            field.name
            for field in StoreSettings._meta.get_fields()
            if getattr(field, "db_index", False)
        }

        for column in (
            "facebook_page_id", "instagram_account_id",
            "whatsapp_phone_number_id", "meta_verify_token",
        ):
            self.assertIn(column, indexed, f"{column} is looked up on every webhook")

    def test_conversation_lookup_index_matches_the_query(self):
        """get_or_create_platform_conversation filters on these three and takes
        the newest."""
        index_fields = [index.fields for index in Conversation._meta.indexes]

        self.assertIn(
            ["store", "platform", "platform_sender_id", "-created_at"], index_fields
        )

    def test_message_history_index_matches_the_query(self):
        """get_conversation_messages orders by -created_at within a conversation."""
        index_fields = [index.fields for index in Message._meta.indexes]

        self.assertIn(["conversation", "-created_at"], index_fields)


class UndeliveredAutoReplyTests(TestCase):
    """A bot reply the platform refused must not pass as answered.

    process_incoming_message saved the reply and called send_platform_message
    ignoring the result, so a rejection left the customer waiting while Celery
    logged success — and the bot's history kept a turn it never delivered, which
    then fed the next message's context as though it had.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        StoreSettings.objects.create(
            store=self.store, facebook_page_id="PAGE123",
            messenger_access_token="tok", meta_access_token="tok",
        )

    def _incoming(self, delivered):
        """Run one inbound message with the send outcome forced."""
        with mock.patch("products.tasks.route", return_value=("رد البوت", "")), \
             mock.patch("products.tasks.send_platform_message", return_value=delivered):
            result = process_incoming_message.apply(
                args=[self.store.id, "messenger", "USER9", "عندكم سوفاج؟"]
            )
        self.assertTrue(result.successful(), result.result)
        return Conversation.objects.get(store=self.store)

    def test_rejected_reply_flags_the_conversation_for_a_human(self):
        """The regression: nothing surfaced this at all."""
        conversation = self._incoming(delivered=False)

        self.assertTrue(conversation.needs_human)

    def test_rejected_reply_creates_a_dashboard_notification(self):
        self._incoming(delivered=False)

        notification = Notification.objects.get(store=self.store)
        self.assertEqual(notification.type, "delivery_failed")
        self.assertIn("مستني", notification.message)

    def test_rejected_reply_keeps_the_message_saved(self):
        """It is the record of what the bot tried to say."""
        conversation = self._incoming(delivered=False)

        roles = list(conversation.messages.order_by("created_at").values_list("role", flat=True))
        self.assertEqual(roles, ["user", "assistant"])

    def test_accepted_reply_changes_nothing(self):
        conversation = self._incoming(delivered=True)

        self.assertFalse(conversation.needs_human)
        self.assertFalse(Notification.objects.filter(type="delivery_failed").exists())

    def test_nothing_to_send_is_not_a_failure(self):
        """send_platform_message returns None for web — that is not a rejection."""
        conversation = self._incoming(delivered=None)

        self.assertFalse(conversation.needs_human)
        self.assertFalse(Notification.objects.filter(type="delivery_failed").exists())

    def test_delivery_failed_is_a_recognised_notification_type(self):
        self.assertIn("delivery_failed", dict(Notification.TYPE_CHOICES))


class RemovedDeadCodeTests(TestCase):
    """Guards against the dead code coming back.

    HomeView / TermsView / PrivacyView were unrouted and pointed at templates that
    do not exist — /, /terms/ and /privacy/ are RedirectViews to the marketing site.
    BOTTLE_IMAGE_URL became unused once migration 0025 moved the value onto
    StoreSettings.bottle_image_url, where it belongs per-store.
    """

    def test_unrouted_template_views_are_gone(self):
        import products.views as views

        for name in ("HomeView", "TermsView", "PrivacyView"):
            self.assertFalse(
                hasattr(views, name),
                f"{name} is unrouted and its template does not exist",
            )

    def test_global_bottle_image_setting_is_gone(self):
        """It is per-store now; a global value showed one store's packaging to all."""
        from django.conf import settings as django_settings

        self.assertFalse(hasattr(django_settings, "BOTTLE_IMAGE_URL"))


class ChatProfileTests(TestCase):
    """chat() resolves model and sampling from named profiles.

    OPENAI_SMART_MODEL was configured in .env but read nowhere, and the single
    OPENAI_TEMPERATURE (0.3) was applied to every call including the JSON
    extractors. Profiles put that strategy in one dict so the ten call sites
    declare intent instead of implementation.
    """

    def _request_kwargs(self, profile):
        """Call chat() with a stubbed transport and return the request kwargs."""
        response = mock.Mock()
        response.choices = [mock.Mock(message=mock.Mock(content="ok"))]

        with mock.patch(
            "products.services.ai.client.client.chat.completions.create",
            return_value=response,
        ) as create:
            ai_client.chat([{"role": "user", "content": "hi"}], profile=profile)

        return create.call_args.kwargs

    def test_extract_profile_is_deterministic(self):
        """0.3 on a JSON extractor makes identical input yield different routes."""
        kwargs = self._request_kwargs("extract")

        self.assertEqual(kwargs["model"], settings.OPENAI_MODEL)
        self.assertEqual(kwargs["temperature"], 0)

    @override_settings(OPENAI_TEMPERATURE=0.3)
    def test_converse_profile_keeps_the_tuned_temperature(self):
        kwargs = self._request_kwargs("converse")

        self.assertEqual(kwargs["model"], settings.OPENAI_MODEL)
        self.assertEqual(kwargs["temperature"], 0.3)

    @override_settings(OPENAI_SMART_MODEL="gpt-5-mini")
    def test_reason_profile_sends_no_temperature(self):
        """The reasoning family accepts only its default and 400s on any value."""
        kwargs = self._request_kwargs("reason")

        self.assertEqual(kwargs["model"], "gpt-5-mini")
        self.assertNotIn("temperature", kwargs)

    @override_settings(OPENAI_SMART_MODEL=None)
    def test_reason_falls_back_to_the_base_model_at_temperature_zero(self):
        """Unset must not crash, and must not inherit the API default of 1.0 —
        this profile decides whether an order is written and stock decremented."""
        kwargs = self._request_kwargs("reason")

        self.assertEqual(kwargs["model"], settings.OPENAI_MODEL)
        self.assertEqual(kwargs["temperature"], 0)

    def test_response_format_still_passes_through(self):
        kwargs = self._request_kwargs("extract")
        self.assertNotIn("response_format", kwargs)

        response = mock.Mock()
        response.choices = [mock.Mock(message=mock.Mock(content="{}"))]
        with mock.patch(
            "products.services.ai.client.client.chat.completions.create",
            return_value=response,
        ) as create:
            ai_client.chat([], profile="extract", response_format={"type": "json_object"})

        self.assertEqual(create.call_args.kwargs["response_format"], {"type": "json_object"})

    def test_unknown_profile_raises_rather_than_silently_defaulting(self):
        with self.assertRaises(ValueError):
            ai_client.chat([], profile="smart")


class CallSiteProfileTests(TestCase):
    """Each call site asks for the profile matching what it actually does."""

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.conversation = Conversation.objects.create(store=self.store)

        # Nothing here may reach the network. Patching one module's chat is not
        # enough: several entry points fan out to services that call chat()
        # through their own module (get_product_info -> resolve_products,
        # compare_products -> resolve_product), so the transport is stubbed too.
        response = mock.Mock()
        response.choices = [mock.Mock(message=mock.Mock(content="{}"))]
        guard = mock.patch(
            "products.services.ai.client.client.chat.completions.create",
            return_value=response,
        )
        guard.start()
        self.addCleanup(guard.stop)

    def _profile_used(self, target, call, **stub):
        """Patch chat() where the module imported it and return the profile asked for."""
        with mock.patch(target, **stub) as chat_mock:
            try:
                call()
            except Exception:
                pass  # only the profile argument is under test

        self.assertTrue(chat_mock.called, f"{target} was never called")
        return chat_mock.call_args.kwargs.get("profile")

    def test_order_extractor_asks_for_reason(self):
        """Highest stakes in the system: it writes orders and moves stock."""
        payload = json.dumps({
            "customer_name": None, "customer_phone": None,
            "customer_secondary_phone": None, "shipping_address": None,
            "products": [], "is_confirmed": False,
        })
        profile = self._profile_used(
            "products.services.order_service.chat",
            lambda: handle_order("...", [], self.store, self.conversation),
            return_value=payload,
        )

        self.assertEqual(profile, "reason")

    def test_extractors_ask_for_extract(self):
        cases = [
            ("products.services.ai.classifier.chat",
             lambda: classify("hi", []), '{"intent": "general"}'),
            ("products.services.ai.intent.chat",
             lambda: extract_intent("hi", [], self.store), '{}'),
            ("products.services.product_resolver.chat",
             lambda: resolve_products("hi", [], self.store), '{"perfumes": []}'),
            # comparison_service is deliberately absent: it no longer has an extractor of
            # its own. It had a private one that was never given the catalogue, so it
            # transliterated Arabic names blind — "اوداورا" resolved to *Dark Aura*, a
            # different real perfume the customer never named, and the bot compared that.
            # Resolution now goes through product_resolver, which is covered above and
            # does inject the product list.
        ]
        for target, call, payload in cases:
            with self.subTest(target=target):
                profile = self._profile_used(target, call, return_value=payload)
                self.assertEqual(profile, "extract")

    def test_comparison_resolves_through_the_shared_resolver(self):
        """Comparison must not re-derive perfume names with a catalogue-blind prompt."""
        from products.services import comparison_service

        self.assertNotIn("perfume_1", inspect.getsource(comparison_service))
        self.assertIn("resolve_products", inspect.getsource(comparison_service))

    def test_prose_calls_ask_for_converse(self):
        cases = [
            ("products.services.general_service.chat",
             lambda: handle_general("hi", [], self.store)),
            ("products.services.product_info.chat",
             lambda: get_product_info("hi", [], self.store)),
        ]
        for target, call in cases:
            with self.subTest(target=target):
                profile = self._profile_used(target, call, return_value="ok")
                self.assertEqual(profile, "converse")


class SalesQualityTests(TestCase):
    """Sales behaviour that used to depend on the model following a prompt rule.

    Diagnosed from conv_651.txt. The prompt already forbade what the bot did — a
    banned closing question closed three replies, a banned joke went out, and prices
    were invented with an empty context block — so the mechanical parts are enforced
    in code here instead of asked for again.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        # 30% concentration: a 50ml brand bottle needs 15g of oil, a 90ml needs 27g.
        self.product = Product.objects.create(
            store=self.store,
            brand=self.brand,
            name="Dior Sauvage",
            gender="male",
        )
        # The real prices from the transcript: 90ml is 80% more perfume for 47% more.
        self.small = ProductVariant.objects.create(
            product=self.product, volume=50, price=642, bottle_type="normal"
        )
        self.large = ProductVariant.objects.create(
            product=self.product, volume=90, price=944, bottle_type="normal"
        )

    def _variants(self):
        return list(self.product.variants.all())

    def test_value_pick_names_the_better_value_size_with_the_numbers(self):
        note = value_pick_note(self.product, self._variants())

        self.assertIn("90 ملي", note)
        self.assertIn("80%", note)   # (90-50)/50
        self.assertIn("302", note)   # 944-642

    def test_value_pick_is_silent_when_there_is_only_one_size(self):
        self.large.delete()

        self.assertEqual(value_pick_note(self.product, self._variants()), "")

    def test_value_pick_ignores_sizes_over_the_stated_budget(self):
        """Upselling to a price the customer already ruled out is not an upsell."""
        self.assertEqual(
            value_pick_note(self.product, self._variants(), max_price=Decimal("700")), ""
        )

    def test_value_pick_prefers_the_cheaper_per_ml_size(self):
        """A larger bottle is not automatically the better value."""
        self.large.price = 2000  # 22.2/ml vs the 50ml's 12.8/ml
        self.large.save()

        self.assertEqual(value_pick_note(self.product, self._variants()), "")

    def test_value_pick_reaches_the_prompt_block(self):
        self.assertIn("💡 Value Pick", format_product(self.product))

    def test_brand_bottles_never_claim_scarcity(self):
        """Replaces test_brand_bottles_show_scarcity_when_the_oil_is_nearly_out.

        A brand bottle is compounded to order, so there is no count to report. The old
        behaviour derived one from bulk oil, and because that counter only ever went down
        it drifted from reality and turned into a false urgency claim.
        """
        self.large.delete()

        self.assertNotIn("زجاجة فقط", format_product(self.product))

    def test_original_bottles_still_show_a_real_low_count(self):
        """Originals are discrete units, so a low count there is a fact worth saying."""
        ProductVariant.objects.create(
            product=self.product, volume=100, price=800,
            bottle_type="original", stock=2,
        )

        self.assertIn("2 زجاجة فقط", format_product(self.product))

    def test_store_exclusive_carries_a_selling_instruction(self):
        """The ⭐ marker existed but told the model nothing, so a request for نيش got
        "not available" while three store-exclusive blends sat in context."""
        own_brand = Brand.objects.create(store=self.store, name=self.store.name)
        exclusive = Product.objects.create(
            store=self.store, brand=own_brand, name="Citrolo", gender="female",
            )
        ProductVariant.objects.create(
            product=exclusive, volume=50, price=598, bottle_type="normal"
        )

        for label, block in (
            ("full", format_product(exclusive)),
            ("brief", format_product(exclusive, brief=True)),
        ):
            with self.subTest(mode=label):
                self.assertIn("نيش", block)
                self.assertIn("ممنوع تقول مفيش", block)

    def test_a_global_brand_gets_no_exclusive_note(self):
        self.assertNotIn("مش موجود عند أي حد تاني", format_product(self.product))

    def test_a_brand_bottle_is_available_for_any_active_product(self):
        """Replaces test_a_zero_concentration_product_offers_no_brand_bottles.

        That test pinned the oil arithmetic's treatment of a misconfigured product. With
        oil tracking gone, a brand bottle of an active product is always offerable — which
        is the whole point of the change: products no longer slide out of the catalogue
        because a counter drifted to zero.
        """
        block = format_product(self.product)

        self.assertNotIn("غير متوفر حالياً بجميع أحجامه", block)
        self.assertIn("الـ 50 ملي", block)
        self.assertNotEqual(value_pick_note(self.product, self._variants()), "")

    def test_a_zero_volume_variant_is_not_offered(self):
        self.small.volume = 0
        self.small.save()

        # It still appears under "Out of Stock Sizes", which the block itself labels
        # DO NOT OFFER — what matters is that it is not in the available list.
        available = format_product(self.product).split("Out of Stock Sizes")[0]

        self.assertNotIn("الـ 0 ملي", available)
        self.assertIn("الـ 90 ملي", available)


class PreferenceMemoryTests(TestCase):
    """A budget or gender stated once must survive the history window.

    extract_intent rebuilds all ten criteria from the last 8 messages only, so a budget
    given five turns back disappeared. That is not a cosmetic loss: with max_price gone,
    recommendation.py flips price_instruction to "ممنوع تذكر الأسعار" so the bot stops
    quoting prices mid-conversation, search_products drops its price filter and starts
    offering perfumes over budget, and the router asks for a budget already given.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.conversation = Conversation.objects.create(store=self.store)

    def test_a_budget_survives_a_turn_that_does_not_mention_it(self):
        """The regression: turn 2 is what a truncated window looks like."""
        merge_preferences(self.conversation, {"gender": "male", "max_price": 700})

        later = merge_preferences(self.conversation, {"gender": "male"})

        self.assertEqual(later["max_price"], 700)

    def test_a_new_value_overrides_the_saved_one(self):
        """The extractor prompt's own rule: a customer changing their mind wins."""
        merge_preferences(self.conversation, {"max_price": 700, "brand": "Dior"})

        later = merge_preferences(self.conversation, {"max_price": 1500})

        self.assertEqual(later["max_price"], 1500)
        self.assertEqual(later["brand"], "Dior", "unrelated preferences should persist")

    def test_preferences_accumulate_across_several_turns(self):
        merge_preferences(self.conversation, {"gender": "female"})
        merge_preferences(self.conversation, {"perfume_type": "oriental"})
        final = merge_preferences(self.conversation, {"max_price": 900})

        self.assertEqual(final["gender"], "female")
        self.assertEqual(final["perfume_type"], "oriental")
        self.assertEqual(final["max_price"], 900)

    def test_exclude_names_is_never_persisted(self):
        """Per-request by design. Persisting it would blacklist a perfume the customer
        merely mentioned — the over-broad exclusion bug, reintroduced by the back door."""
        merge_preferences(self.conversation, {"exclude_names": ["Black Opium"]})

        self.conversation.refresh_from_db()
        self.assertNotIn("exclude_names", self.conversation.preferences)
        self.assertEqual(merge_preferences(self.conversation, {}).get("exclude_names"), None)

    def test_the_multiple_gender_signal_is_not_persisted(self):
        """"multiple" means "he wants one of each, ask which first" — a transient state.
        Saved, it would re-ask that question on every later turn omitting a gender."""
        merge_preferences(self.conversation, {"gender": "multiple", "max_price": 800})

        self.conversation.refresh_from_db()
        self.assertNotIn("gender", self.conversation.preferences)
        self.assertEqual(self.conversation.preferences["max_price"], 800)

    def test_empty_values_do_not_overwrite_real_ones(self):
        merge_preferences(self.conversation, {"notes": ["vanilla"], "gender": "female"})

        later = merge_preferences(self.conversation, {"notes": [], "gender": None})

        self.assertEqual(later["notes"], ["vanilla"])
        self.assertEqual(later["gender"], "female")

    def test_no_conversation_is_a_no_op(self):
        """The web widget can route before a conversation row exists."""
        self.assertEqual(merge_preferences(None, {"gender": "male"}), {"gender": "male"})

    def test_the_router_merges_before_it_decides_anything(self):
        """The whole point of merging at the top of the branch: the budget prompt and
        search_products must both see the restored value, not the truncated intent."""
        self.conversation.preferences = {"gender": "male", "max_price": 700}
        self.conversation.save()

        with mock.patch(
            "products.services.router.classify", return_value="recommendation"
        ), mock.patch(
            "products.services.router.extract_intent", return_value={"brand": "Dior"}
        ), mock.patch(
            "products.services.router.search_products",
            return_value={"products": Product.objects.none(), "alternatives": None},
        ) as search, mock.patch(
            "products.services.router.recommend", return_value=("ok", "")
        ):
            route("عايز حاجة من ديور", [], self.store, self.conversation)

        intent_used = search.call_args[0][0]
        self.assertEqual(intent_used["max_price"], 700, "budget was lost before search")
        self.assertEqual(intent_used["gender"], "male")
        self.assertEqual(intent_used["brand"], "Dior")


class UsageMeteringTests(TestCase):
    """Counting the messages that actually cost money.

    Nothing counted anything before this. throttles.py limits requests per *minute* and
    only on the DRF views — the Messenger and Instagram path runs views_meta → Celery
    and never touches them — so the traffic generating the OpenAI bill had no per-store
    limit and the subscription tiers were unenforceable.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.settings_row = StoreSettings.objects.create(
            store=self.store, monthly_message_cap=10
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _route(self, message, classification="faq"):
        with mock.patch(
            "products.services.router.classify", return_value=classification
        ), mock.patch(
            "products.services.router.handle_general", return_value=("ok", "")
        ):
            return route(message, [], self.store, self.conversation)

    def _count(self):
        usage = StoreMonthlyUsage.objects.filter(store=self.store).first()
        return usage.llm_messages if usage else 0

    def test_a_classified_message_is_counted_once(self):
        self._route("عندكم سوفاج؟")

        self.assertEqual(self._count(), 1)

    def test_a_static_faq_answer_costs_nothing(self):
        """It returns before classify(), so it must never be billed — that free path is
        the single biggest cost saving in the system."""
        StaticFAQ.objects.create(
            store=self.store, question="الشحن بكام؟", keywords="شحن",
            answer="الشحن 60 جنيه.",
        )

        reply, _ = self._route("الشحن بكام؟")

        self.assertEqual(reply, "الشحن 60 جنيه.")
        self.assertEqual(self._count(), 0)

    def test_a_goodbye_shortcut_costs_nothing(self):
        history = [
            {"role": "user", "content": "سلام"},
            {"role": "assistant", "content": "نورتنا"},
            {"role": "user", "content": "سلام"},
        ]
        with mock.patch("products.services.router.classify") as classify_mock:
            route("سلام", history, self.store, self.conversation)

        self.assertFalse(classify_mock.called, "the shortcut should return before classify")
        self.assertEqual(self._count(), 0)

    def test_counts_accumulate_within_the_month(self):
        for _ in range(3):
            self._route("عندكم حاجة؟")

        self.assertEqual(self._count(), 3)

    def test_usage_is_bucketed_per_store(self):
        other = Store.objects.create(name="Rival")
        other_conversation = Conversation.objects.create(store=other)

        self._route("عندكم حاجة؟")
        with mock.patch(
            "products.services.router.classify", return_value="faq"
        ), mock.patch(
            "products.services.router.handle_general", return_value=("ok", "")
        ):
            route("عندكم حاجة؟", [], other, other_conversation)

        self.assertEqual(self._count(), 1)
        self.assertEqual(
            StoreMonthlyUsage.objects.get(store=other).llm_messages, 1
        )

    def test_the_owner_is_warned_once_at_eighty_percent(self):
        for _ in range(8):  # cap is 10
            self._route("عندكم حاجة؟")

        warnings = Notification.objects.filter(store=self.store, type="usage_warning")
        self.assertEqual(warnings.count(), 1)
        self.assertIn("قربت على الحد", warnings.first().title)

    def test_the_owner_is_warned_again_at_the_cap_but_not_repeatedly(self):
        for _ in range(13):  # well past the cap of 10
            self._route("عندكم حاجة؟")

        warnings = Notification.objects.filter(store=self.store, type="usage_warning")
        self.assertEqual(warnings.count(), 2, "one warning at 80%, one at the cap")

    def test_going_over_the_cap_never_stops_the_bot(self):
        """A bot that goes quiet mid-sale costs the store more than the overage."""
        for _ in range(15):
            reply, _ = self._route("عندكم حاجة؟")

        self.assertEqual(reply, "ok", "the reply was withheld once over the cap")
        self.assertEqual(self._count(), 15, "counting stopped at the cap")

    def test_an_uncapped_store_is_counted_but_never_warned(self):
        self.settings_row.monthly_message_cap = None
        self.settings_row.save()

        for _ in range(20):
            self._route("عندكم حاجة؟")

        self.assertEqual(self._count(), 20)
        self.assertFalse(
            Notification.objects.filter(store=self.store, type="usage_warning").exists()
        )

    def test_a_store_with_no_settings_row_is_treated_as_uncapped(self):
        bare = Store.objects.create(name="No Settings")

        self.assertIsNone(monthly_cap(bare))
        record_llm_message(bare)

        self.assertEqual(StoreMonthlyUsage.objects.get(store=bare).llm_messages, 1)

    def test_each_month_gets_its_own_bucket(self):
        from datetime import date

        record_llm_message(self.store, today=date(2026, 7, 15))
        record_llm_message(self.store, today=date(2026, 8, 3))
        record_llm_message(self.store, today=date(2026, 8, 27))

        july = StoreMonthlyUsage.objects.get(store=self.store, period=date(2026, 7, 1))
        august = StoreMonthlyUsage.objects.get(store=self.store, period=date(2026, 8, 1))

        self.assertEqual(july.llm_messages, 1)
        self.assertEqual(august.llm_messages, 2)

    def test_reading_usage_never_writes_a_row(self):
        """The analytics endpoint is a GET. It first used usage_for, which
        get_or_creates, so every dashboard page load wrote a row — including for months
        the store had never sent a message."""
        self.assertEqual(messages_used_this_month(self.store), 0)

        self.assertFalse(
            StoreMonthlyUsage.objects.filter(store=self.store).exists(),
            "reading usage created a row",
        )

    def test_reading_usage_reports_what_was_counted(self):
        self._route("عندكم حاجة؟")
        self._route("عندكم حاجة؟")

        self.assertEqual(messages_used_this_month(self.store), 2)


class ReplySanitizerTests(TestCase):
    """Banned phrasing is stripped rather than asked about again.

    "تحب تعرف أسعارهم والأحجام؟" closed three replies in conv_651 despite being
    quoted inside the persona as forbidden.
    """

    def test_the_transcripts_banned_closer_is_removed(self):
        reply = (
            "🔹 Black Opium: ريحته فيها قهوة وفانيليا تجنن\n\n"
            "تحب تعرف أسعارهم والأحجام؟"
        )

        cleaned = sanitize_reply(reply)

        self.assertNotIn("تحب تعرف", cleaned)
        self.assertIn("Black Opium", cleaned)

    def test_the_personas_own_wording_is_removed_too(self):
        cleaned = sanitize_reply("أه متوفر عندنا. تحب تعرف الأسعار والأحجام المتاحة؟")

        self.assertNotIn("تحب تعرف", cleaned)
        self.assertIn("أه متوفر عندنا", cleaned)

    def test_empty_filler_questions_are_removed(self):
        for banned in ("عايز حاجة تانية؟", "محتاج مساعدة؟", "عطر معين في بالك؟"):
            with self.subTest(banned=banned):
                cleaned = sanitize_reply(f"الـ 90 ملي بـ 944 جنيه. {banned}")

                self.assertNotIn(banned.rstrip("؟"), cleaned)
                self.assertIn("944", cleaned)

    def test_a_reply_that_is_only_a_banned_question_is_left_alone(self):
        """Sending nothing is worse than sending a weak reply."""
        only_banned = "تحب تعرف الأسعار والأحجام؟"

        self.assertEqual(sanitize_reply(only_banned), only_banned)

    def test_a_legitimate_sales_question_survives(self):
        reply = "الـ 90 ملي أوفر بكتير. أجيبلك الـ 90 ولا الـ 50؟"

        self.assertEqual(sanitize_reply(reply), reply)

    def test_empty_and_none_are_passed_through(self):
        self.assertEqual(sanitize_reply(""), "")
        self.assertIsNone(sanitize_reply(None))


class OrphanedConnectorTests(TestCase):
    """A stripped closer must not leave the reply ending mid-word.

    Evaluation scenario M3 sent "...أما Tom Ford فهو أقوى وأغنى شوية. ل" to a customer. The
    optional connector `(?:و\\s*)?` in front of every closer pattern matched the و *inside*
    لو ("if") — both are word characters, so the group started mid-word — and stripped from
    there, orphaning the ل.

    Fixing it with `\\bو` is not enough: that leaves an orphaned لو, a reply ending in "if".
    The lead alternation has to consume the whole conditional opener.
    """

    HEAD = "أما Tom Ford فهو أقوى وأغنى شوية. "

    def test_the_transcripts_orphaned_lam_is_gone(self):
        """The exact string that went out to a customer."""
        cleaned = sanitize_reply(self.HEAD + "لو تحب تعرف الأسعار؟")

        self.assertEqual(cleaned, "أما Tom Ford فهو أقوى وأغنى شوية.")

    def test_no_banned_closer_leaves_a_dangling_connector(self):
        for closer in (
            "لو تحب تعرف الأسعار؟",
            "ولو تحب أعرفك الأسعار والأحجام؟",
            "لو تحب أقولك الأسعار؟",
            "لو تحب أعرفك أكتر عن الأحجام دي؟",
            "لو عايز حاجة تانية؟",
            "لو محتاج مساعدة؟",
            "لو حابب أساعدك في حاجة تانية، تحت أمرك.",
            "لو فيه عطر معين في بالك؟",
            "لو أقدر أساعدك في إيه؟",
        ):
            with self.subTest(closer=closer):
                cleaned = sanitize_reply(self.HEAD + closer)

                self.assertTrue(cleaned.endswith("شوية."), cleaned)
                self.assertIn("Tom Ford", cleaned)

    def test_no_premature_closer_leaves_a_dangling_connector(self):
        for closer in (
            "لو تحب أساعدك في الطلب؟",
            "لو تحب أساعدك تطلب واحد؟",
            "لو تحب أجهزلك واحد؟",
            "لو تحب تطلب؟",
            "لو أجيبلك الـ90 ولا الـ50؟",
            "لو نسجل الطلب؟",
            "لو تحب نكمل الطلب؟",
        ):
            with self.subTest(closer=closer):
                cleaned = strip_premature_closing(self.HEAD + closer)

                self.assertTrue(cleaned.endswith("شوية."), cleaned)

    def test_the_order_verb_is_not_stranded_either(self):
        """"تحب نكمل الطلب؟" left "تحب" behind: the bare نسجل/نكمل pattern is tried first
        and consumed the tail before the "تحب نكمل" pattern could match the whole thing."""
        cleaned = strip_premature_closing(self.HEAD + "تحب نكمل الطلب؟")

        self.assertEqual(cleaned, "أما Tom Ford فهو أقوى وأغنى شوية.")

    def test_the_statement_form_of_an_order_close_is_stripped(self):
        """Every PREMATURE_CLOSERS pattern required a question mark, so a close phrased as a
        statement walked through: "لو تحب أساعدك في الطلب ... تحت أمرك." went out at stage
        'discovery' in evaluation scenario F1."""
        cleaned = strip_premature_closing(
            "أنصحك بـ Stronger With You 90 ملي بـ 700 جنيه. "
            "لو تحب أساعدك في الطلب أو تحب تجرب العطور في الستور تحت أمرك."
        )

        self.assertNotIn("أساعدك في الطلب", cleaned)
        self.assertIn("700", cleaned)
        self.assertTrue(cleaned.endswith("جنيه."), cleaned)

    def test_the_interrogative_particle_is_not_stranded(self):
        """A live reply ended on "...1100 جنيه. هل" — هل introduces the question that was just
        removed and can never end an Arabic sentence."""
        head = "🔹 Dior Homme Sport 90 ملي بـ 1100 جنيه. "
        for closer in ("هل تحب أساعدك في الطلب؟", "هل تحب تطلب؟", "وهل تحب نكمل الطلب؟"):
            with self.subTest(closer=closer):
                self.assertTrue(
                    strip_premature_closing(head + closer).rstrip().endswith("جنيه."),
                    strip_premature_closing(head + closer),
                )
        for closer in ("هل تحب تعرف الأسعار؟", "هل عايز حاجة تانية؟"):
            with self.subTest(closer=closer):
                self.assertTrue(
                    sanitize_reply(head + closer).rstrip().endswith("جنيه."),
                    sanitize_reply(head + closer),
                )

    def test_a_sentence_final_word_is_not_mistaken_for_a_connector(self):
        """"بس" and "كمان" end ordinary Egyptian sentences and must survive."""
        for reply in ("دي الأسعار بس", "وفيه حجم 50 ملي كمان"):
            with self.subTest(reply=reply):
                self.assertEqual(sanitize_reply(reply), reply)

    def test_nothing_is_trimmed_when_no_closer_was_stripped(self):
        """The trim is a repair for a strip, not a general rewrite."""
        reply = "الـ 90 ملي بـ 944 جنيه و"

        self.assertEqual(sanitize_reply(reply), reply)


class AvoidTraitVocabularyTests(TestCase):
    """A trait the customer never stated must not reach the ranker.

    Evaluation scenario M1: "مش عايز حاجه منتشره" (not widely owned) came back as
    avoid_traits ["loud", "mainstream"] and persisted for the whole conversation. Popularity
    is not intensity. "loud" is in vocabulary and ranking.py reads it as heaviness, so seven
    of eleven candidates were penalised -3.0 — including the longest-lasting ones, one turn
    before the customer said longevity was their top priority.

    Enforced in code as well as in the prompt because avoid_traits is the only extracted
    field scored as a penalty, so guessing it is worse than leaving it empty.
    """

    def test_an_out_of_vocabulary_trait_is_dropped(self):
        from products.services.ai.intent import _sanitize

        cleaned = _sanitize({"avoid_traits": ["loud", "mainstream"]})

        self.assertEqual(cleaned["avoid_traits"], ["loud"])

    def test_every_documented_trait_survives(self):
        from products.services.ai.intent import _sanitize

        traits = ["heavy", "suffocating", "sweet", "loud", "strong", "old"]

        self.assertEqual(_sanitize({"avoid_traits": traits})["avoid_traits"], traits)

    def test_case_and_whitespace_are_tolerated(self):
        from products.services.ai.intent import _sanitize

        cleaned = _sanitize({"avoid_traits": [" Heavy ", "SWEET"]})

        self.assertEqual(cleaned["avoid_traits"], [" Heavy ", "SWEET"])

    def test_other_fields_are_untouched(self):
        from products.services.ai.intent import _sanitize

        intent = {"gender": "male", "max_price": 700, "notes": ["oud"], "wants_uncommon": True}

        self.assertEqual(_sanitize(dict(intent)), intent)

    def test_a_missing_or_malformed_field_does_not_raise(self):
        from products.services.ai.intent import _sanitize

        self.assertEqual(_sanitize({}), {})
        self.assertEqual(_sanitize({"avoid_traits": None})["avoid_traits"], None)
        self.assertEqual(_sanitize("not a dict"), {})

    def test_an_unknown_trait_is_not_echoed_into_the_arabic_prompt(self):
        """The fallback rendered ["mainstream"] as the literal "مش mainstream"."""
        from products.services.sales.constraints import describe

        phrases = describe({"avoid_traits": ["mainstream"]})

        self.assertEqual([p for p in phrases if "mainstream" in p], [])

    def test_a_known_trait_still_renders(self):
        from products.services.sales.constraints import describe

        self.assertIn("مش تقيل", describe({"avoid_traits": ["heavy"]}))

    def test_wanting_uncommon_does_not_also_claim_an_avoided_trait(self):
        """"مش منتشرة" is one statement and must produce one phrase, not two."""
        from products.services.sales.constraints import describe

        phrases = describe({"wants_uncommon": True, "avoid_traits": ["mainstream"]})

        self.assertIn("حاجة مش منتشرة", phrases)
        self.assertEqual(len(phrases), 1)

    def test_popularity_is_not_intensity_in_the_prompt(self):
        """The extractor prompt must say so explicitly — this is what the model got wrong."""
        import inspect

        from products.services.ai import intent as intent_module

        source = inspect.getsource(intent_module.extract_intent)

        self.assertIn("منتشر", source)
        self.assertIn("POPULARITY IS NOT INTENSITY", source)
        self.assertIn("avoid_traits", source)

    def test_the_zero_hallucination_rule_names_avoid_traits(self):
        """It listed every inferable field except the one that costs -3.0."""
        import inspect

        from products.services.ai import intent as intent_module

        source = inspect.getsource(intent_module.extract_intent)
        rule = source[source.index("ZERO HALLUCINATION"):]
        rule = rule[: rule.index("\n")]

        self.assertIn("avoid_traits", rule)


class ScriptedRepliesSurviveSanitizingTests(TestCase):
    """Hardcoded replies must reach the customer byte-for-byte.

    Every reply route() returns is sanitized, scripted ones included — tasks.py and
    views.py cannot tell a generated reply from a hardcoded one. A regex that clipped
    the order summary or the cancellation line would do it silently and in production
    only, so the exact strings are pinned here.

    Known and accepted: a StaticFAQ answer a store writes ending in one of the banned
    questions would also be trimmed. The answer survives, only the trailing filler
    goes, so this is left as-is rather than making sanitizing route-aware.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage", gender="male",
            )
        self.variant = ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def test_the_order_summary_is_untouched(self):
        """It ends in "ولا تحب تعدل حاجة؟", which sits close to a banned pattern."""
        payload = json.dumps({
            "products": [{
                "name": "Dior Sauvage", "quantity": 1, "volume": 50,
                "bottle_type": "normal",
            }],
            "customer_name": "محمد", "customer_phone": "01000000000",
            "customer_secondary_phone": "01100000000", "shipping_address": "القاهرة",
            "is_confirmed": False,
        })
        with mock.patch(
            "products.services.order_service.chat", return_value=payload
        ):
            summary, _ = handle_order("...", [], self.store, self.conversation)

        self.assertIn("💰 الإجمالي:", summary)
        self.assertEqual(sanitize_reply(summary), summary)

    def test_the_cart_cancellation_reply_is_untouched(self):
        """Contains "تحب تشوف حاجة تانية" — near-miss on the filler pattern."""
        scripted = "تمام، شلت الطلب خلاص. تحب تشوف حاجة تانية أو أرشحلك عطر؟"

        self.assertEqual(sanitize_reply(scripted), scripted)

    def test_the_goodbye_reply_is_untouched(self):
        scripted = (
            "نورتنا يا فندم! 😊 لو احتجت أي حاجة في المستقبل، إحنا هنا في خدمتك "
            "24 ساعة. يوم سعيد!"
        )

        self.assertEqual(sanitize_reply(scripted), scripted)

    def test_a_static_faq_answer_is_untouched(self):
        faq = StaticFAQ.objects.create(
            store=self.store, question="الشحن بكام؟", keywords="شحن, توصيل",
            answer="الشحن 60 جنيه لكل محافظات مصر، والتوصيل من 2 لـ 4 أيام.",
        )

        self.assertEqual(sanitize_reply(faq.answer), faq.answer)

    def test_the_payment_fallback_is_untouched(self):
        self.assertEqual(sanitize_reply(PAYMENT_FALLBACK), PAYMENT_FALLBACK)


class HumanAgentVoiceTests(TestCase):
    """A human colleague's words must not come back as the bot's own.

    conv_651 lines 631-634 show an agent's "معاك محمد / انا شغال في الاستور" saved as
    role=assistant. Once the handoff was resolved the bot read them as its own prior
    output and imitated them.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.conversation = Conversation.objects.create(store=self.store)

    def test_agent_messages_are_kept_out_of_the_models_history(self):
        Message.objects.create(conversation=self.conversation, role="user", content="هاي")
        Message.objects.create(
            conversation=self.conversation, role="agent", content="اتفضل يا فندم معاك محمد"
        )
        Message.objects.create(
            conversation=self.conversation, role="assistant", content="أهلاً يا فندم"
        )

        history = build_llm_history(self.conversation)

        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertNotIn("محمد", " ".join(m["content"] for m in history))

    def test_only_roles_the_api_accepts_are_emitted(self):
        Message.objects.create(conversation=self.conversation, role="agent", content="x")

        for message in build_llm_history(self.conversation):
            self.assertIn(message["role"], ("user", "assistant"))

    def test_the_dashboard_saves_a_handoff_reply_as_agent(self):
        """views.HandoffReplyAPIView used to save these as assistant."""
        import products.views as views

        source = inspect.getsource(views.HandoffReplyAPIView)

        self.assertIn('save_message(conv, "agent"', source)
        self.assertNotIn('save_message(conv, "assistant"', source)


class NoProductDataGuardTests(TestCase):
    """The one branch with no product context invented prices anyway.

    conv_651 line 815 answered "في بلو دي شانيل؟" with fabricated prices for Dior
    Homme Sport and an empty context block.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")

    def _system_prompt_sent(self, history=None):
        with mock.patch(
            "products.services.general_service.chat", return_value="ok"
        ) as chat_mock:
            handle_general("في بلو دي شانيل؟", history or [], self.store)
        return chat_mock.call_args[0][0][0]["content"]

    def test_the_no_price_guard_is_present(self):
        prompt = self._system_prompt_sent()

        self.assertIn("مفيش أي بيانات منتجات مبعوتة لك", prompt)
        self.assertIn("ممنوع تذكر سعر أو اسم عطر من ذاكرتك", prompt)

    def test_configured_store_offers_can_still_be_relayed(self):
        """The promotion branch routes through here and must be able to quote the
        store's real configured offer prices — only invented ones are banned."""
        prompt = self._system_prompt_sent()

        self.assertIn("مسموح بس تنقل الأسعار أو العروض المكتوبة حرفياً", prompt)

    def test_repetition_context_demands_a_different_move_not_new_wording(self):
        """Five "هاي" produced four rewordings of the same greeting move."""
        history = [
            {"role": "user", "content": "هاي"},
            {"role": "assistant", "content": "أهلاً يا فندم، تحت أمرك في أي استفسار"},
            {"role": "user", "content": "هاي"},
            {"role": "assistant", "content": "أهلاً بحضرتك، جاهز أساعدك"},
        ]

        prompt = self._system_prompt_sent(history)

        self.assertIn("مش كفاية تغير الكلمات", prompt)
        self.assertIn("أهلاً بحضرتك، جاهز أساعدك", prompt)


class RouterBranchPromptTests(TestCase):
    """What the scripted router branches actually send to the model.

    The router has ~20 branches and almost none assert on the prompt they build, which
    is how a blanket "never mention a price" guard added to general_service came within
    one reading of gagging the promotion branch — that branch routes through
    handle_general and asks the model to relay the store's configured offers, prices
    included. Nothing would have failed. These pin the instruction each branch depends
    on, and that the no-product-data guard still permits configured prices.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        StoreSettings.objects.create(
            store=self.store,
            system_prompt="عروضنا: بوكس الصيف اشتري 3 حجم 90 مل عليهم 1 هدية 3000 جنيه",
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _prompt_for(self, classification, message, history=None):
        """Route a message and return everything handle_general sent to the model.

        The branches put their instructions in the user message and the persona plus
        guards in the system message, so both are joined — what matters is whether the
        model was told a thing, not which slot carried it.
        """
        with mock.patch(
            "products.services.router.classify", return_value=classification
        ), mock.patch(
            "products.services.general_service.chat", return_value="ok"
        ) as chat_mock:
            route(message, history or [], self.store, self.conversation)

        self.assertTrue(chat_mock.called, f"{classification} did not reach handle_general")
        return "\n".join(m["content"] for m in chat_mock.call_args[0][0])

    def test_promotion_can_still_quote_the_stores_configured_offers(self):
        """The regression I nearly shipped: the guard must ban invented prices only."""
        prompt = self._prompt_for("promotion", "عندكم عروض؟")

        self.assertIn("بوكس الصيف", prompt, "the store's own offer text was withheld")
        self.assertIn("مسموح بس تنقل الأسعار أو العروض المكتوبة حرفياً", prompt)
        self.assertIn("مش بتقدر تطبق", prompt)

    def test_promotion_insistence_refuses_firmly(self):
        prompt = self._prompt_for("promotion", "لا انا عايزك انت تنفذه")

        self.assertIn("مش في إمكانياتك", prompt)
        self.assertIn("ممنوع توهمه", prompt)

    def test_musk_and_mix_defers_to_a_human_rep(self):
        prompt = self._prompt_for("musk_mix_product", "عندكم مسكات؟")

        self.assertIn("المندوب البشري", prompt)
        self.assertIn("ممنوع تحاول تجاوب", prompt)

    def test_a_second_handoff_is_told_not_to_repeat_itself(self):
        history = [
            {"role": "user", "content": "عايز اكلم حد"},
            {"role": "assistant", "content": "حولت المحادثة لفريق خدمة العملاء"},
        ]

        prompt = self._prompt_for("handoff", "لسه محدش رد عليا", history)

        self.assertIn("اتحول لخدمة العملاء قبل كده", prompt)
        self.assertIn('ممنوع تقوله "حولت طلبك"', prompt)

    def test_every_general_branch_carries_the_no_invented_price_guard(self):
        """None of these branches gets product data, so all of them need it."""
        for classification, message in (
            ("greeting", "هاي"),
            ("faq", "انت مين"),
            ("promotion", "عندكم عروض؟"),
            ("musk_mix_product", "عندكم مسكات؟"),
        ):
            with self.subTest(classification=classification):
                prompt = self._prompt_for(classification, message)

                self.assertIn("ممنوع تذكر سعر أو اسم عطر من ذاكرتك", prompt)


class PersonaConsolidationTests(TestCase):
    """The rewritten persona must keep its hard rules and lose its contradiction."""

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")

    def test_the_cta_contradiction_is_gone(self):
        """Persona said "CTA ~1 in 2-3 replies" while recommendation.py said "always
        end with a question". The model resolved that arbitrarily every turn."""
        from products.services.ai import recommendation

        self.assertNotIn(
            "لازم تختم الترشيح بسؤال", inspect.getsource(recommendation)
        )

    def test_the_sales_plays_the_transcript_was_missing_are_present(self):
        prompt = get_system_prompt(self.store)

        for play, marker in (
            ("price objection", "غالي"),
            ("soft rejection", "هفكر وأرجعلك"),
            ("diagnose rejection", "مش عاجبني"),
            ("niche pivot", "نيش"),
            ("value pick", "Value Pick"),
            ("no dead-end availability", "رد ميت"),
        ):
            with self.subTest(play=play):
                self.assertIn(marker, prompt)

    def test_the_hard_money_rules_survived_the_rewrite(self):
        prompt = get_system_prompt(self.store)

        self.assertIn("ممنوع تخترع سعر", prompt)
        self.assertIn("ممنوع تأكد أي طلب", prompt)
        self.assertIn("أسرار المهنة", prompt)

    def test_the_exhaustive_price_list_mandate_is_gone(self):
        """This rule is what flattened the price reply into a receipt."""
        self.assertNotIn("كل الأحجام والأسعار المتاحة", get_system_prompt(self.store))

    def test_rule_marker_count_stays_bounded(self):
        """The failure mode was ~60 competing absolute markers. This is a ratchet:
        if it trips, consolidate rather than raising the number."""
        prompt = get_system_prompt(self.store)

        self.assertLess(prompt.count("🔴"), 35)

    def test_every_rule_from_the_pre_rewrite_persona_survived(self):
        """Audit ratchet for the consolidation.

        Comparing the rewritten persona against the pre-rewrite file rule by rule
        turned up two that had been dropped: obey the (Original Bottle) field's
        dictated wording, and avoid fusha / literal-English phrasing. Both are back.
        Each entry below is a distinct rule from the old prompt, so a future
        consolidation that loses one fails here instead of in production.
        """
        prompt = get_system_prompt(self.store)

        for rule, marker in (
            ("identity: store's perfumes only", "خبرتك محصورة"),
            ("no inventing prices", "ممنوع تخترع سعر"),
            ("product not in data = absent", "مش في البيانات"),
            ("never change a price", "ممنوع تغيره"),
            ("no confirm without a summary", "ممنوع تأكد أي طلب"),
            ("no invented attributes", "ممنوع تخترع مواصفات"),
            ("no unbacked similarity claims", 'ممنوع تقول عطر "شبه"'),
            ("prices in EGP", "بالجنيه المصري"),
            ("obey the Original Bottle wording", "خانة (Original Bottle)"),
            ("no fusha or literal translation", "ترجمة حرفية"),
            ("banned florid words", "عبير"),
            ("banned robotic phrases", "يسعدني مساعدتك"),
            ("store not محل", 'ممنوع كلمة "محل"'),
            ("1-4 short sentences", "4 جمل قصيرة"),
            ("no spec-list dumps", "ممنوع تسرد"),
            ("max 2-3 recommendations", "ترشيحين أو تلاتة"),
            ("no (تركيب) label on sizes", '(تركيب)'),
            ("only in-stock sizes offered", "الأحجام المتوفرة بس"),
            ("brand vs original same juice", "نفس التركيبة بالظبط"),
            ("don't re-ask stated preferences", "ممنوع تسأل عليها تاني"),
            ("stay on the chosen perfume", "خليه الأساس"),
            ("no empty questions", "الأسئلة الفاضية"),
            ("no promises it cannot keep", "ممنوع توعد"),
            ("cheapest-perfume handling", "أرخص عطر"),
            ("respect a refusal", "احترم الرد"),
            ("trade secrets refused", "أسرار المهنة"),
            ("no business consulting", "استشارات تجارية"),
            ("keyword hand-off for store info", "الكلمة المفتاحية"),
            ("medical caution", "يستشير طبيب"),
            ("handoff only once", "متكررش جملة التحويل"),
            ("no jokes back", "ممنوع ترد بهزار"),
            ("musk/mix defers to a human", "تخصص المندوب البشري"),
            ("no repeating a reply or its idea", "ممنوع تكرر نفس الجملة"),
        ):
            with self.subTest(rule=rule):
                self.assertIn(marker, prompt)


# A valid Fernet key (32 url-safe base64 bytes). settings_test deliberately leaves
# FIELD_ENCRYPTION_KEY unset so the default suite exercises the no-key path, so any test
# that actually needs encryption has to supply one.
TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"perfume-ai-test-key-32-bytes!!!!").decode()


@override_settings(FIELD_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class CustomerPIIEncryptionTests(TestCase):
    """Phones and addresses are encrypted at rest and still usable.

    Fernet is non-deterministic, which is what breaks naive encryption of a queried
    column: an admin icontains search encrypts the term into ciphertext matching
    nothing, and a DISTINCT counts every row as a separate customer. Both are served by
    the blind index instead.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")

    def _order(self, phone="01000000000", **overrides):
        fields = {
            "store": self.store,
            "customer_name": "محمد",
            "customer_phone": phone,
            "secondary_phone": "01100000000",
            "shipping_address": "القاهرة، المعادي، ٥ شارع النصر",
            "total_price": 400,
        }
        fields.update(overrides)
        return Order.objects.create(**fields)

    def _raw(self, order, column):
        """The column exactly as stored, bypassing the field's decryption."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {column} FROM products_order WHERE id = %s", [order.id]
            )
            return cursor.fetchone()[0]

    def test_pii_round_trips_through_the_orm(self):
        order = self._order()

        reloaded = Order.objects.get(pk=order.pk)

        self.assertEqual(reloaded.customer_phone, "01000000000")
        self.assertEqual(reloaded.secondary_phone, "01100000000")
        self.assertEqual(reloaded.shipping_address, "القاهرة، المعادي، ٥ شارع النصر")

    def test_the_stored_columns_are_not_plaintext(self):
        """The actual point of the change — verified against the raw column, because
        reading through the ORM would decrypt and prove nothing."""
        order = self._order()

        for column in ("customer_phone", "secondary_phone", "shipping_address"):
            with self.subTest(column=column):
                stored = self._raw(order, column)
                self.assertNotIn("01000000000", stored or "")
                self.assertNotIn("المعادي", stored or "")

    def test_the_customer_name_stays_queryable(self):
        """Left plaintext on purpose: admin name search is a substring lookup, and no
        blind index can serve that."""
        self._order()

        self.assertTrue(
            Order.objects.filter(customer_name__icontains="محم").exists()
        )

    def test_one_phone_written_three_ways_is_one_customer(self):
        """This also fixes a bug older than encryption: _looks_like_phone never
        normalized, so these three already counted as three customers."""
        self._order(phone="01000000000")
        self._order(phone="0100 000 0000")
        self._order(phone="+201000000000")

        distinct = (
            Order.objects.filter(store=self.store)
            .values("customer_phone_hash")
            .distinct()
            .count()
        )

        self.assertEqual(distinct, 1)

    def test_different_phones_stay_distinct(self):
        self._order(phone="01000000000")
        self._order(phone="01222222222")

        self.assertEqual(
            Order.objects.values("customer_phone_hash").distinct().count(), 2
        )

    def test_the_hash_is_rewritten_when_the_phone_changes(self):
        order = self._order(phone="01000000000")
        original = order.customer_phone_hash

        order.customer_phone = "01222222222"
        order.save()

        self.assertNotEqual(order.customer_phone_hash, original)
        self.assertEqual(
            order.customer_phone_hash, phone_blind_index("01222222222")
        )

    def test_admin_phone_search_finds_an_encrypted_order(self):
        order = self._order(phone="01000000000")
        order_admin = OrderAdmin(Order, site)
        request = RequestFactory().get("/admin/products/order/")
        request.user = User.objects.create_superuser("root", "r@e.com", "pw")

        found, _ = order_admin.get_search_results(
            request, Order.objects.all(), "01000000000"
        )

        self.assertIn(order, found)

    def test_admin_phone_search_tolerates_a_differently_typed_number(self):
        order = self._order(phone="01000000000")
        order_admin = OrderAdmin(Order, site)
        request = RequestFactory().get("/admin/products/order/")
        request.user = User.objects.create_superuser("root", "r@e.com", "pw")

        found, _ = order_admin.get_search_results(
            request, Order.objects.all(), "+20 100 000 0000"
        )

        self.assertIn(order, found)

    def test_admin_name_search_still_works(self):
        order = self._order()
        order_admin = OrderAdmin(Order, site)
        request = RequestFactory().get("/admin/products/order/")
        request.user = User.objects.create_superuser("root", "r@e.com", "pw")

        found, _ = order_admin.get_search_results(
            request, Order.objects.all(), "محمد"
        )

        self.assertIn(order, found)

    def test_the_cart_encrypts_the_same_fields(self):
        conversation = Conversation.objects.create(store=self.store)
        cart = Cart.objects.create(
            conversation=conversation, customer_phone="01000000000",
            shipping_address="القاهرة",
        )

        reloaded = Cart.objects.get(pk=cart.pk)

        self.assertEqual(reloaded.customer_phone, "01000000000")
        self.assertEqual(reloaded.shipping_address, "القاهرة")

    def test_rows_predating_the_migration_are_missing_from_the_customer_count(self):
        """Documents a real transitional gap rather than pretending it away.

        Every Order written before migration 0032 has customer_phone_hash="", and the
        KPI excludes those — an order with no phone is not a customer. So
        unique_customers undercounts until backfill_pii_encryption has run. Simulated
        here with a raw UPDATE, which is the only way to get an unhashed row now that
        save() always populates it."""
        order = self._order()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE products_order SET customer_phone_hash = '' WHERE id = %s",
                [order.id],
            )

        counted = (
            Order.objects.filter(store=self.store)
            .exclude(customer_phone_hash="")
            .values("customer_phone_hash")
            .distinct()
            .count()
        )
        self.assertEqual(counted, 0, "an unhashed legacy row should not be counted")

        # Re-saving is exactly what the backfill command does, and it repairs the row.
        Order.objects.get(pk=order.pk).save()

        repaired = (
            Order.objects.filter(store=self.store)
            .exclude(customer_phone_hash="")
            .values("customer_phone_hash")
            .distinct()
            .count()
        )
        self.assertEqual(repaired, 1)

    def test_an_order_with_no_phone_is_not_counted_as_a_customer(self):
        self._order(phone="")

        self.assertEqual(
            Order.objects.exclude(customer_phone_hash="").count(), 0
        )


class PhoneNormalizationTests(TestCase):
    """The blind index is only as good as the normalization feeding it."""

    def test_egyptian_formats_collapse_to_one_key(self):
        canonical = normalize_phone("01000000000")

        for spelling in ("0100 000 0000", "+201000000000", "0020 100 000 0000",
                         "0100-000-0000"):
            with self.subTest(spelling=spelling):
                self.assertEqual(normalize_phone(spelling), canonical)

    def test_short_values_are_kept_whole(self):
        self.assertEqual(normalize_phone("12345"), "12345")

    def test_empty_input_hashes_to_nothing(self):
        for empty in ("", None):
            with self.subTest(value=empty):
                self.assertEqual(normalize_phone(empty), "")
                self.assertEqual(phone_blind_index(empty), "")


class EncryptionWithoutAKeyTests(TestCase):
    """A missing key must degrade, not crash — and must be visibly a no-op.

    settings.py defaults FIELD_ENCRYPTION_KEY to "" and encrypt_value silently returns
    plaintext when it is unset, so an environment that forgets it gets no encryption at
    all. That is worth pinning: it is the difference between "encrypted" and "believed
    to be encrypted", and the Meta access tokens have relied on this code path since
    long before customer PII did.
    """

    @override_settings(FIELD_ENCRYPTION_KEY="")
    def test_saving_without_a_key_stores_plaintext_rather_than_failing(self):
        store = Store.objects.create(name="Perfamix Test")

        order = Order.objects.create(
            store=store, customer_name="محمد", customer_phone="01000000000",
            shipping_address="القاهرة", total_price=400,
        )

        self.assertEqual(Order.objects.get(pk=order.pk).customer_phone, "01000000000")

    @override_settings(FIELD_ENCRYPTION_KEY="")
    def test_grouping_still_works_unkeyed(self):
        """The hash falls back to an unkeyed digest, so analytics stay correct even in a
        misconfigured environment."""
        self.assertNotEqual(phone_blind_index("01000000000"), "")
        self.assertEqual(
            phone_blind_index("01000000000"), phone_blind_index("0100 000 0000")
        )

    @override_settings(FIELD_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
    def test_the_key_changes_the_hash(self):
        """Keyed, so a stolen database cannot brute-force the small space of Egyptian
        mobile numbers back out of the index."""
        keyed = phone_blind_index("01000000000")

        with override_settings(FIELD_ENCRYPTION_KEY=""):
            unkeyed = phone_blind_index("01000000000")

        self.assertNotEqual(keyed, unkeyed)


class NoteParsingTests(TestCase):
    """Note fields are free text typed per store, so they need normalising first.

    bulk_import writes columns K/L/M verbatim, so "Citrus, Mint", "عود وفانيليا" and
    "Bergamot/Pepper" all reach the database as-is. Comparing them literally finds far
    less overlap than actually exists.
    """

    def test_a_comma_separated_field_splits(self):
        self.assertEqual(
            sales_notes.parse_notes("Citrus, Mint, Bergamot"),
            ("citrus", "mint", "bergamot"),
        )

    def test_slashes_semicolons_and_newlines_all_separate(self):
        self.assertEqual(
            sales_notes.parse_notes("Cedar / Vetiver; Amber\nMusk"),
            ("cedar", "vetiver", "amber", "musk"),
        )

    def test_a_standalone_arabic_waw_separates_but_an_attached_one_does_not(self):
        """"عود و فانيليا" is two notes; "وفانيليا" is one. Splitting on every و would
        corrupt the second."""
        self.assertEqual(sales_notes.parse_notes("عود و فانيليا"), ("عود", "فانيليا"))
        self.assertEqual(sales_notes.parse_notes("وفانيليا"), ("وفانيليا",))

    def test_duplicates_collapse_and_order_is_kept(self):
        self.assertEqual(
            sales_notes.parse_notes("Rose, rose, ROSE, Oud"), ("rose", "oud")
        )

    def test_blank_input_is_empty_not_an_error(self):
        for blank in ("", None, "   ", ",,,"):
            with self.subTest(blank=blank):
                self.assertEqual(sales_notes.parse_notes(blank), ())

    def test_related_notes_share_an_accord_family(self):
        """The point of families: bergamot and lemon never match as strings."""
        self.assertIn("citrus", sales_notes.families(("bergamot",)))
        self.assertIn("citrus", sales_notes.families(("lemon",)))

    def test_an_unknown_note_contributes_no_family(self):
        """Bucketing an unrecognised string would invent similarity."""
        self.assertEqual(sales_notes.families(("unobtainium",)), frozenset())

    def test_a_note_can_belong_to_two_families(self):
        self.assertEqual(
            sales_notes.families(("cherry",)), frozenset({"fruity", "gourmand"})
        )

    def test_the_sweet_expansion_is_preserved_verbatim(self):
        """The existing "عايز حاجة مسكرة" behaviour depends on this exact list, so it
        moved out of search_service rather than changing."""
        self.assertEqual(
            sales_notes.SWEET_NOTE_EXPANSION,
            ("vanilla", "caramel", "tonka", "praline", "honey", "chocolate",
             "cacao", "marshmallow", "sugar", "cherry", "plum"),
        )


class ScentSimilarityTests(TestCase):
    """Similarity is scored from note data, and reported without a number.

    "بحب ريحة Sauvage بس عايز حاجة شبهه" used to be answered with Dior Homme Intense and
    Fahrenheit — same brand, same gender, nothing alike. Similarity was never computed;
    it was approximated by AND-filtering LLM-guessed notes, which matched nothing.
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Perfamix Test")
        cls.brand = Brand.objects.create(store=cls.store, name="Dior")

        cls.sauvage = cls._make(
            "Dior Sauvage", "Bergamot, Pepper", "Lavender, Patchouli",
            "Ambroxan, Cedar", occasion="Casual", longevity="8 hours",
            projection="Strong",
        )
        cls.lookalike = cls._make(
            "Ambero", "Bergamot, Pink Pepper", "Lavender", "Ambroxan, Vetiver"
        )
        cls.unrelated = cls._make(
            "Fahrenheit", "Mandarin", "Violet", "Leather, Tobacco",
            occasion="Evening", longevity="10 hours", projection="Strong",
        )

    @classmethod
    def _make(cls, name, top, middle, base, **extra):
        product = Product.objects.create(
            store=cls.store, brand=cls.brand, name=name, gender="male",
            top_notes=top, middle_notes=middle, base_notes=base,
            **extra
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=600, bottle_type="normal"
        )
        return product

    def _score(self, product):
        reference = sales_similarity.reference_from_product(self.sauvage)
        return sales_similarity.compare(reference, product)

    def test_a_genuine_lookalike_scores_far_above_an_unrelated_perfume(self):
        self.assertGreater(self._score(self.lookalike).score, self._score(self.unrelated).score)

    def test_a_genuine_lookalike_lands_in_the_close_band(self):
        self.assertEqual(self._score(self.lookalike).band, "close")

    def test_the_regression_an_unrelated_perfume_is_not_called_similar(self):
        """Fahrenheit shares gender, projection and an evening occasion with Sauvage and
        must still not read as similar to it."""
        self.assertNotEqual(self._score(self.unrelated).band, "close")

    def test_shared_notes_are_named_so_the_claim_is_evidenced(self):
        shared = self._score(self.lookalike).shared_notes

        self.assertIn("bergamot", shared)
        self.assertIn("ambroxan", shared)

    def test_occasion_and_gender_stay_separate_from_scent_dna(self):
        """Conflating them is what produced the bug: same occasion is not similarity."""
        result = self._score(self.unrelated)

        self.assertTrue(result.same_gender)
        self.assertLess(result.score, sales_similarity.CLOSE)

    def test_the_description_never_contains_a_percentage(self):
        """Handing the model a number is how "95% similar to the original" gets born."""
        reference = sales_similarity.reference_from_product(self.sauvage)
        description = sales_similarity.describe(reference, self._score(self.lookalike))

        self.assertNotIn("%", description)
        self.assertIn("ممنوع تذكر نسبة مئوية", description)

    def test_a_reference_we_do_not_stock_is_labelled_as_general_knowledge(self):
        """Its notes came from the model's world knowledge, not our catalogue, so the
        reply has to phrase it more cautiously."""
        reference = sales_similarity.reference_from_notes(
            "Creed Aventus", ["pineapple", "birch", "musk"]
        )

        self.assertEqual(reference.source, "general_knowledge")
        description = sales_similarity.describe(
            reference, sales_similarity.compare(reference, self.lookalike)
        )
        if description:
            self.assertIn("مش على عطر عندنا", description)

    def test_an_unusable_reference_scores_nothing_rather_than_guessing(self):
        empty = sales_similarity.reference_from_notes("Unknown", [])

        self.assertFalse(empty.is_usable)
        self.assertEqual(sales_similarity.compare(empty, self.lookalike).band, "none")

    def test_base_notes_outweigh_top_notes(self):
        """Base notes are the drydown — what someone means by how a perfume smells."""
        self.assertGreater(
            sales_similarity.LAYER_WEIGHTS["base"], sales_similarity.LAYER_WEIGHTS["top"]
        )


class RankingWeightTests(TestCase):
    """A strong explicit signal must not be outvoted by weak incidental ones.

    There was no ranking at all before: criteria either deleted a product or did nothing,
    so "similar to X" could not outrank "same occasion". These pin the ordering that fixes
    that, and the tie-break that keeps the previous behaviour intact.
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Perfamix Test")
        cls.brand = Brand.objects.create(store=cls.store, name="Dior")
        cls.own_brand = Brand.objects.create(store=cls.store, name="Perfamix Test")

    def _make(self, name, top="", middle="", base="", brand=None, **extra):
        product = Product.objects.create(
            store=self.store, brand=brand or self.brand, name=name, gender="male",
            top_notes=top, middle_notes=middle, base_notes=base, **extra
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=600, bottle_type="normal"
        )
        return product

    def test_similarity_outweighs_occasion_season_and_projection_combined(self):
        """The plan's core requirement, stated as arithmetic."""
        self.assertGreater(
            sales_ranking.WEIGHTS["similarity"],
            sales_ranking.WEIGHTS["occasion"]
            + sales_ranking.WEIGHTS["season"]
            + sales_ranking.WEIGHTS["projection"],
        )

    def test_an_explicit_exclusion_is_a_penalty_not_a_bonus(self):
        self.assertLess(sales_ranking.WEIGHTS["avoid"], 0)

    def test_a_lookalike_beats_an_occasion_match_end_to_end(self):
        reference_product = self._make(
            "Dior Sauvage", "Bergamot, Pepper", "Lavender", "Ambroxan, Cedar"
        )
        lookalike = self._make(
            "Ambero", "Bergamot, Pink Pepper", "Lavender", "Ambroxan, Vetiver"
        )
        occasion_only = self._make(
            "Fahrenheit", "Mandarin", "Violet", "Leather, Tobacco",
            occasion="Evening", season="Winter", projection="Strong",
        )
        reference = sales_similarity.reference_from_product(reference_product)

        ranked = sales_ranking.rank(
            [occasion_only, lookalike],
            {"gender": "male", "occasion": "evening", "season": "winter",
             "projection": "strong"},
            reference=reference,
        )

        self.assertEqual(ranked[0].product.name, "Ambero")

    def test_a_perfume_full_of_an_avoided_trait_is_pushed_down(self):
        """"مش عايز حاجة تقيلة أو تخنق اللي حواليا" has to actually cost something."""
        heavy = self._make("Heavy One", "Oud", "Incense", "Amber, Leather")
        light = self._make("Light One", "Bergamot", "Mint", "Cedar")

        ranked = sales_ranking.rank(
            [heavy, light], {"gender": "male", "avoid_traits": ["heavy", "suffocating"]}
        )

        self.assertEqual(ranked[0].product.name, "Light One")
        self.assertTrue(
            any("تقيل" in note for note in ranked[-1].mismatches),
            "the heavy perfume should be flagged as a mismatch, not silently ranked",
        )

    def test_an_avoided_note_outranks_a_wanted_one(self):
        """A wanted note plus an excluded note must not net out positive: an explicit
        exclusion is the stronger statement."""
        both = self._make("Both", "Vanilla", "", "Oud")

        ranked = sales_ranking.rank(
            [both], {"notes": ["vanilla"], "avoid_notes": ["oud"]}
        )

        self.assertTrue(ranked[0].mismatches)

    def test_partial_note_matches_score_proportionally(self):
        """The AND-filter this replaces scored two-of-three as zero."""
        two_of_three = self._make("Two", "Bergamot", "Pepper", "Cedar")
        one_of_three = self._make("One", "Bergamot", "Rose", "Musk")

        ranked = sales_ranking.rank(
            [one_of_three, two_of_three], {"notes": ["bergamot", "pepper", "cedar"]}
        )

        self.assertEqual(ranked[0].product.name, "Two")

    def test_wanting_something_uncommon_favours_a_store_exclusive(self):
        """"مش منتشرة" — the store's own blend is the only real signal for that in the
        data we hold."""
        mainstream = self._make("Mainstream", "Bergamot", "", "Cedar")
        exclusive = self._make(
            "Exclusive", "Bergamot", "", "Cedar", brand=self.own_brand
        )

        ranked = sales_ranking.rank(
            [mainstream, exclusive], {"wants_uncommon": True}
        )

        self.assertEqual(ranked[0].product.name, "Exclusive")

    def test_equal_scores_preserve_the_callers_ordering(self):
        """With nothing to discriminate on, ranking must not reorder at all.

        It used to re-sort equal scores by -oil_stock_grams. That column is gone, and the
        replacement is deliberately *no* secondary key: search_service._by_value has
        already ordered candidates cheapest-brand-bottle first, and re-sorting here would
        override the caller rather than defer to it. Python's sort is stable, so an
        all-equal set comes back exactly as it was handed in."""
        first = self._make("First", "Bergamot")
        second = self._make("Second", "Bergamot")

        ranked = sales_ranking.rank([second, first], {"notes": ["bergamot"]})

        self.assertEqual([entry.product.name for entry in ranked], ["Second", "First"])

    def test_reasons_are_rendered_without_the_score(self):
        product = self._make("Any", "Bergamot", "", "Cedar")

        ranked = sales_ranking.rank([product], {"notes": ["bergamot"]})
        note = sales_ranking.reasons_note(ranked[0])

        self.assertIn("bergamot", note)
        self.assertNotIn(str(ranked[0].score), note)

    def test_no_discriminating_signal_is_detected_as_no_signal(self):
        """Gender alone cannot order anything — it is already a hard filter."""
        self.assertFalse(sales_ranking.has_signal({"gender": "male"}))
        self.assertTrue(sales_ranking.has_signal({"notes": ["oud"]}))


class AccordProminenceTests(TestCase):
    """Conversation 736: "عايز حاجه للجيم تكون فريش", 1200 جنيه, male — eight perfumes tied
    at exactly 7.50 and the shortlist came out price-ascending.

    `notes` was a boolean. With `notes: ["fresh"]` the fraction `len(matched)/len(wanted)`
    could only be 1/1 or 0/1, so a Fall/Winter gourmand holding a trace of mint scored the
    same 2.0 as an aquatic — and since `ranked.sort` is stable, an all-equal list kept
    `search_service._by_value`'s cheapest-brand-bottle order. Stronger With You led the gym
    request at 400 جنيه; Invictus, the one product recorded `Sport`, came fourth and was
    offered last.

    The weights were there all along — `similarity.note_profile` computes base 1.0 / middle
    0.7 / top 0.45 — and the boolean threw every one of them away.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")

    def _make(self, name, top="", middle="", base="", **extra):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="male",
            top_notes=top, middle_notes=middle, base_notes=base, **extra
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=600, bottle_type="normal"
        )
        return product

    def test_a_trace_of_a_fresh_note_is_not_a_fresh_perfume(self):
        """The exact 736 shape: a gourmand reaches the fresh expansion through mint and
        lavender while being vanilla and amberwood at full weight."""
        gourmand = self._make(
            "Stronger With You", "Mint, Cardamom", "Lavender, Sage, Cinnamon",
            "Vanilla, Chestnut, Amberwood, Cedar",
        )
        aquatic = self._make(
            "Invictus", "Grapefruit, Mandarin, Marine Notes", "Bay Leaf",
            "Guaiac Wood, Oakmoss",
        )

        ranked = sales_ranking.rank([gourmand, aquatic], {"notes": ["fresh"]})

        self.assertEqual(ranked[0].product.name, "Invictus")

    def test_both_still_count_as_matched_so_neither_loses_its_reason(self):
        """Graded, not filtered. A trace of mint is weaker evidence, not absent evidence —
        scoring it as zero would take the ✅ line away and leave the model nothing to say."""
        gourmand = self._make("Gourmand", "Mint", "", "Vanilla, Amberwood, Cedar")

        entry = sales_ranking.rank([gourmand], {"notes": ["fresh"]})[0]

        self.assertGreater(entry.score, 0)
        self.assertTrue(any("fresh" in reason for reason in entry.reasons))

    def test_the_fit_value_never_reaches_the_reason_line(self):
        """reasons_note is read straight into a prompt, and a number there is how
        "شبهه بنسبة 95%" gets born."""
        product = self._make("Any", "Bergamot, Lemon", "", "Cedar")

        entry = sales_ranking.rank([product], {"notes": ["fresh"]})[0]
        note = sales_ranking.reasons_note(entry)

        self.assertNotIn(str(entry.score), note)
        self.assertFalse(
            any(character.isdigit() for character in note),
            f"no digits belong in the evidence line: {note!r}",
        )

    def test_a_dominant_accord_outscores_a_buried_one(self):
        """Same requested accord, same number of matching notes, different prominence."""
        buried = self._make("Buried", "Bergamot", "", "Oud, Amber, Leather, Vanilla")
        dominant = self._make("Dominant", "Bergamot", "Neroli", "Cedar")

        ranked = sales_ranking.rank([buried, dominant], {"notes": ["fresh"]})

        self.assertEqual(ranked[0].product.name, "Dominant")

    def test_an_unmapped_ingredient_is_still_scored_on_its_own_weight(self):
        """"elemi" and "hedione" are outside the family table. They can still be matched
        literally, and a product built on one must not read as a total miss."""
        strong = self._make("Strong", "", "", "Elemi")
        trace = self._make("Trace", "Elemi", "", "Vanilla, Cedar, Amber, Musk")

        ranked = sales_ranking.rank([strong, trace], {"notes": ["elemi"]})

        self.assertEqual(ranked[0].product.name, "Strong")
        self.assertGreater(ranked[-1].score, 0)


class SportOccasionAxisTests(TestCase):
    """The other half of conversation 736: `gym` sat in the same tuple as `office`.

    `_DAYTIME_TERMS` was one flat bucket containing daily/casual/office *and* sport/gym, so
    `_occasion_verdict("Casual/Evening", "gym")` matched on the word "casual" and collected
    the full occasion weight. A perfume recorded `Sport` earned exactly what one recorded
    `Office, Formal, Daily` earned, and the single field in the catalogue that says "this is
    a gym fragrance" bought nothing.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")

    def _entry(self, intent, **fields):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name="Test", gender="male", **fields
        )
        return sales_ranking.rank([product], intent)[0]

    def test_a_sport_perfume_outranks_a_casual_one_for_the_gym(self):
        sport = Product.objects.create(
            store=self.store, brand=self.brand, name="Invictus",
            gender="male", occasion="Sport",
        )
        casual = Product.objects.create(
            store=self.store, brand=self.brand, name="Acqua di Gio",
            gender="male", occasion="Casual",
        )

        ranked = sales_ranking.rank([casual, sport], {"occasion": "gym"})

        self.assertEqual(ranked[0].product.name, "Invictus")

    def test_a_casual_perfume_still_earns_a_reason_and_no_warning(self):
        """Partial credit, not CONFLICT. A casual fresh scent really is wearable to the gym,
        and calling half the catalogue a mismatch would manufacture a warning the reply then
        has to explain away."""
        entry = self._entry({"occasion": "gym"}, occasion="Casual")

        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)
        self.assertFalse(entry.mismatches)
        self.assertGreater(entry.score, 0)

    def test_a_casual_perfume_earns_less_than_a_sport_one(self):
        self.assertGreater(
            self._entry({"occasion": "gym"}, occasion="Sport").score,
            self._entry({"occasion": "gym"}, occasion="Casual").score,
        )

    def test_an_evening_perfume_is_flagged_for_a_gym_request(self):
        entry = self._entry({"occasion": "gym"}, occasion="Evening")

        self.assertTrue(entry.mismatches)
        self.assertIn("Evening", entry.mismatches[0])

    def test_a_mixed_casual_evening_perfume_is_read_on_its_casual_half(self):
        """"Casual/Evening" answers a gym request partly and contradicts it partly. The half
        that answers wins, because a warning here would be the stronger claim and the
        recorded value does not support it."""
        entry = self._entry({"occasion": "gym"}, occasion="Casual/Evening")

        self.assertFalse(entry.mismatches)
        self.assertLess(
            entry.score, self._entry({"occasion": "gym"}, occasion="Sport").score
        )

    def test_a_sport_perfume_still_satisfies_a_daytime_request(self):
        """The asymmetry, pinned. Sport leaves the *asked* side of the tuple but stays on the
        *recorded* side, because a sport fragrance genuinely is a daytime fragrance — taking
        it off both sides would silently downgrade "بالنهار" against `Sport` to UNKNOWN."""
        entry = self._entry({"occasion": "daily"}, occasion="Sport")

        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)
        self.assertFalse(entry.mismatches)

    def test_the_arabic_word_for_gym_resolves_on_the_same_axis(self):
        entry = self._entry({"occasion": "جيم"}, occasion="Sport")

        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)
        self.assertFalse(entry.mismatches)

    def test_season_credit_is_never_graded(self):
        """Unlike occasion there is no half-right season, and grading this axis would quietly
        rescale a signal deliberately sized at 0.8."""
        self.assertEqual(
            sales_ranking._season_verdict("Spring/Summer", "summer"),
            (sales_ranking.MATCH, 1.0),
        )
        self.assertEqual(
            sales_ranking._season_verdict("All Seasons", "winter"),
            (sales_ranking.MATCH, 1.0),
        )
        self.assertEqual(
            sales_ranking._season_verdict("Fall/Winter", "summer"),
            (sales_ranking.CONFLICT, 0.0),
        )


class OccasionImpliesSeasonTests(TestCase):
    """A gym request carries a season the customer never named.

    After the note-prominence and sport-axis fixes, Dior Homme Sport (Spring/Summer) still sat
    *below* four All-Seasons gourmands for "عايز حاجه للجيم تكون فريش", and a Fall/Winter one
    was 0.06 behind it. The season field was free: `rank()`'s season block only runs when
    `intent["season"]` is set, and a customer who names an activity never sets it.

    Inferring one into the intent was not an option. `intent["season"]` is a hard SQL filter in
    search_service, so a phantom season would *delete* every Fall/Winter perfume from the
    candidate set; `_drop_reason` would then report them as "مش لنفس الموسم اللي قاله",
    `constraints._SEASON` would echo "للصيف" back at someone who never said it, and
    `constraints.TASTE_KEYS` counts the slot toward the gate deciding whether we know enough to
    recommend at all. Hence a ranking-only signal that reads the field and never writes it.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")

    def _entry(self, intent, **fields):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name="Test", gender="male", **fields
        )
        return sales_ranking.rank([product], intent)[0]

    def test_warm_beats_year_round_beats_cold_for_the_gym(self):
        warm = self._entry({"occasion": "gym"}, season="Spring/Summer")
        year_round = self._entry({"occasion": "gym"}, season="All Seasons")
        cold = self._entry({"occasion": "gym"}, season="Fall/Winter")

        self.assertGreater(warm.score, year_round.score)
        self.assertGreater(year_round.score, cold.score)

    def test_a_cold_season_perfume_is_warned_about_by_name(self):
        """The reply has to be able to say why it is steering away, not just steer."""
        entry = self._entry({"occasion": "gym"}, season="Fall/Winter")

        self.assertTrue(entry.mismatches)
        self.assertIn("Fall/Winter", entry.mismatches[0])
        self.assertIn("Fall/Winter", sales_ranking.reasons_note(entry))

    def test_a_year_round_perfume_earns_neither_bonus_nor_warning(self):
        """The store said it works all year, so it is not disfavoured — and ranking has to
        agree with _season_verdict about what "All Seasons" means."""
        entry = self._entry({"occasion": "gym"}, season="All Seasons")

        self.assertFalse(entry.mismatches)
        self.assertFalse(entry.reasons)
        self.assertEqual(entry.score, 0)

    def test_a_blank_season_is_unknown_not_a_mismatch(self):
        entry = self._entry({"occasion": "gym"}, season="")

        self.assertFalse(entry.mismatches)
        self.assertFalse(entry.reasons)

    def test_an_explicitly_named_season_owns_the_question(self):
        """A stated season is the stronger statement, so the implied one must stand down
        rather than score the same field twice."""
        entry = self._entry(
            {"occasion": "gym", "season": "summer"}, season="Spring/Summer"
        )

        self.assertEqual(entry.score, sales_ranking.WEIGHTS["season"])
        self.assertEqual(entry.reasons, ["مناسب للموسم"])

    def test_a_non_sport_occasion_implies_nothing_about_season(self):
        """Office is a time of day, not a claim about exertion. Only sport gets an entry."""
        entry = self._entry({"occasion": "office"}, season="Fall/Winter")

        self.assertFalse(entry.mismatches)
        self.assertFalse(entry.reasons)
        self.assertEqual(entry.score, 0)

    def test_a_perfume_recorded_across_both_halves_is_credited_not_warned(self):
        warm_and_cold = self._entry({"occasion": "gym"}, season="Summer/Winter")

        self.assertFalse(warm_and_cold.mismatches)
        self.assertGreater(warm_and_cold.score, 0)

    def test_the_arabic_seasons_resolve_too(self):
        self.assertGreater(
            self._entry({"occasion": "gym"}, season="الصيف").score,
            self._entry({"occasion": "gym"}, season="الشتا").score,
        )

    def test_a_spring_summer_perfume_now_outranks_an_all_seasons_gourmand(self):
        """The reported bug, end to end on the real shapes. Dior Homme Sport's citrus is all
        top-layer over a woody-amber base, so it loses the note axis to Le Male's four
        expansion hits — the recorded season is what has to carry it."""
        dior_homme_sport = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Homme Sport", gender="male",
            season="Spring/Summer", occasion="Casual",
            top_notes="Lemon, Bergamot, Aldehydes", middle_notes="Elemi, Pink Pepper",
            base_notes="Woody Notes, Amber, Olibanum",
        )
        le_male = Product.objects.create(
            store=self.store, brand=self.brand, name="Le Male", gender="male",
            season="All Seasons", occasion="Casual",
            top_notes="Mint, Lavender, Bergamot",
            middle_notes="Cinnamon, Cumin, Orange Blossom",
            base_notes="Vanilla, Sandalwood, Tonka Bean",
        )

        ranked = sales_ranking.rank(
            [le_male, dior_homme_sport],
            {"notes": ["fresh"], "gender": "male", "occasion": "gym"},
        )

        self.assertEqual(ranked[0].product.name, "Dior Homme Sport")


class AccordDefinitionTests(TestCase):
    """An accord's families are stated, not derived from its note expansion.

    Deriving them read `neroli`'s secondary `floral` into "fresh", so Le Male — a vanilla-tonka
    fougère whose `orange blossom` is a white floral — collected freshness credit for being
    floral and outscored Dior Homme Sport on a gym request. The expansion lists notes that
    should make a perfume a *candidate*; it was never a definition of the accord.
    """

    def test_fresh_does_not_include_floral(self):
        self.assertNotIn("floral", sales_notes.request_families("fresh"))
        self.assertNotIn("floral", sales_notes.request_families("فريش"))

    def test_deriving_from_the_expansion_would_have_included_it(self):
        """Pins the mechanism, so nobody reintroduces the derivation as a simplification."""
        self.assertIn(
            "floral", sales_notes.families(sales_notes.FRESH_NOTE_EXPANSION, tolerant=True)
        )

    def test_fresh_is_exactly_the_light_families(self):
        self.assertEqual(sales_notes.request_families("fresh"), sales_notes.LIGHT_FAMILIES)

    def test_sweet_is_gourmand(self):
        self.assertEqual(sales_notes.request_families("sweet"), frozenset({"gourmand"}))
        self.assertEqual(sales_notes.request_families("مسكر"), frozenset({"gourmand"}))

    def test_a_plain_ingredient_still_derives_from_its_own_note(self):
        self.assertEqual(sales_notes.request_families("bergamot"), frozenset({"citrus"}))
        self.assertEqual(
            sales_notes.request_families("oud"), frozenset({"woody", "amber"})
        )

    def test_an_unmapped_ingredient_stays_empty(self):
        """_note_fit falls back to mass alone for these rather than inventing a composition."""
        self.assertEqual(sales_notes.request_families("elemi"), frozenset())
        self.assertEqual(sales_notes.request_families(""), frozenset())

    def test_the_expansion_table_itself_is_untouched(self):
        """search_service._notes_query builds its SQL OR from these, and neroli genuinely is a
        fresh note — it must keep making a perfume a candidate."""
        self.assertIn("neroli", sales_notes.FRESH_NOTE_EXPANSION)
        self.assertIn("citrus", sales_notes.request_families("neroli"))

    def test_a_white_floral_no_longer_outscores_a_citrus_aquatic(self):
        store = Store.objects.create(name="Perfamix Test")
        brand = Brand.objects.create(store=store, name="Dior")
        floral = Product.objects.create(
            store=store, brand=brand, name="Floral", gender="male",
            top_notes="Bergamot", middle_notes="Orange Blossom, Jasmine",
            base_notes="Vanilla, Tonka Bean",
        )
        aquatic = Product.objects.create(
            store=store, brand=brand, name="Aquatic", gender="male",
            top_notes="Bergamot, Grapefruit", middle_notes="Calone",
            base_notes="Cedar, Musk",
        )

        ranked = sales_ranking.rank([floral, aquatic], {"notes": ["fresh"]})

        self.assertEqual(ranked[0].product.name, "Aquatic")


class TolerantFamilyLookupTests(TestCase):
    """`families()` did an exact dict lookup while note *matching* used substrings, so a
    store-typed adjective silently erased a note's whole accord.

    Invictus is the case that mattered: its `marine notes` yielded no `aquatic`, so the one
    genuine aquatic in the catalogue read as having no aquatic character at the exact moment
    a customer asked for فريش.
    """

    def test_an_adjectived_note_resolves_only_when_tolerant(self):
        for note, family in (
            ("marine notes", "aquatic"),
            ("woody notes", "woody"),
            ("sicilian lemon", "citrus"),
            ("madagascar vanilla", "gourmand"),
        ):
            with self.subTest(note=note):
                self.assertIn(family, sales_notes.families([note], tolerant=True))
                self.assertNotIn(family, sales_notes.families([note]))

    def test_a_compound_word_resolves_through_the_substring_pass(self):
        """"amberwood" is one word, so the word pass cannot split it."""
        found = sales_notes.families(["amberwood"], tolerant=True)

        self.assertIn("amber", found)
        self.assertIn("woody", found)

    def test_a_known_note_never_reaches_the_substring_pass(self):
        """"pineapple" contains "pine". Resolving exactly and then by word, and only falling
        through to substrings when both find nothing, is what stops a fruit becoming a wood."""
        self.assertEqual(sales_notes.families(["pineapple"], tolerant=True), {"fruity"})
        self.assertEqual(
            sales_notes.families(["pineapple leaf"], tolerant=True), {"fruity"}
        )

    def test_short_keys_do_not_match_as_substrings(self):
        """"rum" is inside "geranium" and "tea" inside "steam"."""
        self.assertNotIn("gourmand", sales_notes.families(["chrysanthemum"], tolerant=True))
        self.assertNotIn("green", sales_notes.families(["steamed milk"], tolerant=True))

    def test_the_default_stays_exact_for_every_existing_caller(self):
        """similarity.compare, the avoid_heavy penalty and value.py compare two products
        against each other, where a missed family costs both sides equally. Flipping the
        default would move similarity bands tuned against this catalogue."""
        self.assertEqual(sales_notes.families(["marine notes"]), frozenset())
        self.assertEqual(sales_notes.families(["bergamot"]), {"citrus"})

    def test_an_unknown_note_still_contributes_nothing(self):
        self.assertEqual(sales_notes.families(["hedione"], tolerant=True), frozenset())


class SimilaritySearchTests(TestCase):
    """search_products end-to-end for "عايز حاجة شبه X".

    Covers the two halves of the fix: notes are OR-scored rather than AND-filtered, so a
    similarity request no longer falls through to the gender-and-brand-only branch; and
    when nothing is actually close, the caller is told so instead of being handed the
    nearest perfume as though it matched.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.dior = Brand.objects.create(store=self.store, name="Dior")
        self.own = Brand.objects.create(store=self.store, name="Perfamix Test")

    def _make(self, name, top="", middle="", base="", brand=None, **extra):
        product = Product.objects.create(
            store=self.store, brand=brand or self.dior, name=name, gender="male",
            top_notes=top, middle_notes=middle, base_notes=base,
            **extra
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=600, bottle_type="normal"
        )
        return product

    def _names(self, results):
        products = results["products"]
        chosen = products if products.exists() else results["alternatives"]
        return [product.name for product in (chosen or [])]

    def test_the_sauvage_regression_a_lookalike_is_ranked_first(self):
        self._make("Dior Sauvage", "Bergamot, Pepper", "Lavender", "Ambroxan, Cedar")
        self._make("Ambero", "Bergamot, Pink Pepper", "Lavender", "Ambroxan, Vetiver",
                   brand=self.own)
        self._make("Fahrenheit", "Mandarin", "Violet", "Leather, Tobacco",
                   occasion="Evening", projection="Strong")
        self._make("Dior Homme Intense", "Lavender", "Iris", "Leather, Amber",
                   occasion="Evening", projection="Strong")

        results = search_products(
            {
                "gender": "male",
                "occasion": "evening",
                "projection": "strong",
                "similar_to": "Dior Sauvage",
                "similar_to_notes": ["bergamot", "pepper", "ambroxan", "lavender"],
                "exclude_names": ["Dior Sauvage"],
                "wants_uncommon": True,
            },
            store=self.store,
        )

        self.assertEqual(self._names(results)[0], "Ambero")

    def test_several_notes_no_longer_have_to_all_match(self):
        """The AND-chain emptied the result set and hid the fall-through."""
        self._make("Partial", "Bergamot", "Rose", "Cedar")

        results = search_products(
            {"notes": ["bergamot", "pepper", "ambroxan"]}, store=self.store
        )

        self.assertIn("Partial", self._names(results))

    def test_a_reference_in_the_catalogue_is_preferred_over_guessed_notes(self):
        self._make("Dior Sauvage", "Bergamot", "Lavender", "Ambroxan")
        self._make("Other", "Rose", "Jasmine", "Musk")

        results = search_products(
            {"similar_to": "Dior Sauvage", "similar_to_notes": ["rose"]},
            store=self.store,
        )

        self.assertEqual(results["similarity"]["reference_source"], "catalogue")

    def test_an_unstocked_reference_falls_back_to_general_knowledge(self):
        self._make("Other", "Pineapple", "Birch", "Musk")

        results = search_products(
            {"similar_to": "Creed Aventus",
             "similar_to_notes": ["pineapple", "birch", "musk"]},
            store=self.store,
        )

        self.assertEqual(results["similarity"]["reference_source"], "general_knowledge")

    def test_no_close_match_is_reported_honestly(self):
        """The honesty requirement: do not present the nearest perfume as a match."""
        self._make("Nothing Alike", "Rose", "Jasmine", "Vanilla")

        results = search_products(
            {"similar_to": "Creed Aventus",
             "similar_to_notes": ["pineapple", "birch", "ambergris"]},
            store=self.store,
        )

        self.assertFalse(results["similarity"]["has_close_match"])
        self.assertEqual(results["similarity"]["best_band"], "none")

    def test_a_close_match_is_reported_as_one(self):
        self._make("Twin", "Pineapple, Birch", "Ambergris", "Musk")

        results = search_products(
            {"similar_to": "Creed Aventus",
             "similar_to_notes": ["pineapple", "birch", "ambergris", "musk"]},
            store=self.store,
        )

        self.assertTrue(results["similarity"]["has_close_match"])

    def test_a_sparsely_filled_product_survives_an_occasion_request(self):
        """The regression this fixes: occasion was an icontains AND-filter, and icontains
        against an empty column matches nothing — so naming an occasion silently deleted
        every product whose occasion the store never filled in."""
        self._make("No Occasion Recorded", "Bergamot", "", "Cedar")

        results = search_products(
            {"gender": "male", "occasion": "evening", "notes": ["bergamot"]},
            store=self.store,
        )

        self.assertIn("No Occasion Recorded", self._names(results))

    def test_the_return_value_is_still_a_queryset(self):
        """recommend() calls .exists() on this, and the existing tests call len()."""
        self._make("Any", "Bergamot", "", "Cedar")

        results = search_products({"notes": ["bergamot"]}, store=self.store)

        self.assertTrue(hasattr(results["products"], "exists"))
        self.assertEqual(len(results["products"]), 1)

    def test_similarity_is_none_when_none_was_requested(self):
        self._make("Any", "Bergamot", "", "Cedar")

        self.assertIsNone(
            search_products({"gender": "male"}, store=self.store)["similarity"]
        )


class ValueLanguageTests(TestCase):
    """The value pick must not call a dearer bottle cheaper.

    The transcript rendered "الـ90 ملي أوفر — كمية أكتر بـ80% بفرق 302 جنيه بس". Every
    number is right and the sentence is still wrong: "أوفر" beside a bare price difference
    reads as *cheaper by 302*, and the model duly told a customer the bigger bottle saved
    them money.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage", gender="male",
            )
        self.small = ProductVariant.objects.create(
            product=self.product, volume=50, price=642, bottle_type="normal"
        )
        self.large = ProductVariant.objects.create(
            product=self.product, volume=90, price=944, bottle_type="normal"
        )

    def _note(self, max_price=None):
        return value_pick_note(
            self.product, list(self.product.variants.all()), max_price=max_price
        )

    def test_the_bigger_bottle_is_described_as_more_expensive_overall(self):
        note = self._note()

        self.assertIn("أغلى", note)
        self.assertIn("302", note)

    def test_the_per_ml_saving_is_stated_separately_from_the_total(self):
        note = self._note()

        self.assertIn("سعر الملي أرخص", note)
        self.assertIn("10.5", note)   # 944/90
        self.assertIn("12.8", note)   # 642/50

    def test_the_regression_it_never_claims_the_dearer_bottle_is_cheaper(self):
        note = self._note()

        self.assertNotIn("أوفر بفرق", note)
        self.assertIn('ممنوع تقول إنه "أرخص"', note)

    def test_a_negative_price_difference_is_never_rendered(self):
        """The latent bug: the baseline was the smallest bottle while the winner was the
        cheapest per ml, with nothing guaranteeing the winner cost more. 30ml@500 beside
        50ml@400 produced "بفرق -100 جنيه بس"."""
        self.small.volume = 30
        self.small.price = 500
        self.small.save()
        self.large.volume = 50
        self.large.price = 400
        self.large.save()

        note = self._note()

        self.assertNotIn("-100", note)
        self.assertNotIn("-", note.replace("—", ""))

    def test_price_per_ml_is_correct(self):
        self.assertEqual(sales_value.price_per_ml(self.large), Decimal(944) / Decimal(90))

    def test_a_zero_volume_variant_has_no_price_per_ml(self):
        self.small.volume = 0
        self.small.save()

        self.assertIsNone(sales_value.price_per_ml(self.small))

    def test_cross_product_value_reports_only_recorded_dimensions(self):
        """"ليه أدفع 1200 بدل 500؟" must be answered from data, not invention."""
        cheap = Product.objects.create(
            store=self.store, brand=self.brand, name="Cheap", gender="male",
            longevity="4 hours", projection="Moderate",
            )
        ProductVariant.objects.create(product=cheap, volume=50, price=500)
        dear = Product.objects.create(
            store=self.store, brand=self.brand, name="Dear", gender="male",
            longevity="10 hours", projection="Strong", perfume_type="niche",
            )
        ProductVariant.objects.create(product=dear, volume=50, price=1200)

        dimensions, unknown = sales_value.cross_product_value(cheap, dear)
        labels = [label for label, _, _ in dimensions]

        self.assertIn("الثبات", labels)
        self.assertIn("الفوحان", labels)
        self.assertIn("سعر الملي", labels)
        self.assertIn("الموسم", unknown)

    def test_unknown_dimensions_are_banned_by_name_not_omitted(self):
        bare_one = Product.objects.create(
            store=self.store, brand=self.brand, name="Bare", gender="male",
            )
        ProductVariant.objects.create(product=bare_one, volume=50, price=500)

        note = sales_value.value_comparison_note(bare_one, self.product)

        self.assertIn("ممنوع تخترع فرق فيها", note)

    def test_the_comparison_permits_recommending_the_cheaper_option(self):
        """A trustworthy salesperson does not force the expensive product."""
        note = sales_value.value_comparison_note(self.product, self.product)

        self.assertIn("الأرخص أنسب ليه", note)


class BlankFieldHallucinationGuardTests(TestCase):
    """An unrecorded field must be named as unrecorded, not left blank.

    "Longevity: " with nothing after it reads to the model as a gap to fill, and it filled
    them — quoting hour counts and projection figures no row contained.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Sparse", gender="male",
            )
        ProductVariant.objects.create(product=self.product, volume=50, price=500)

    def test_a_blank_longevity_renders_as_unrecorded(self):
        self.assertIn("Longevity: غير مسجل", format_product(self.product))

    def test_a_blank_field_carries_an_explicit_ban(self):
        block = format_product(self.product)

        self.assertIn("بيانات ناقصة", block)
        self.assertIn("ممنوع تذكر رقم ساعات", block)

    def test_missing_notes_are_banned_too(self):
        self.assertIn("ممنوع تخترع نوتات", format_product(self.product))

    def test_a_fully_populated_product_gets_no_warning(self):
        self.product.longevity = "8 hours"
        self.product.projection = "Strong"
        self.product.season = "Winter"
        self.product.occasion = "Evening"
        self.product.top_notes = "Bergamot"
        self.product.save()

        self.assertNotIn("بيانات ناقصة", format_product(self.product))


class ComparisonSuppressesPricesTests(TestCase):
    """The comparison prompt forbids prices; the block it injected mandated them.

    format_products carried the 💡 Value Pick line telling the model to lead with the
    numbers, while comparison_service told it to mention none — two opposite orders in one
    request, and an "أوفر" verdict about one perfume's size ladder could be read back as a
    verdict about the other.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage", gender="male",
            top_notes="Bergamot", )
        ProductVariant.objects.create(product=self.product, volume=50, price=642)
        ProductVariant.objects.create(product=self.product, volume=90, price=944)

    def test_prices_and_the_value_pick_are_dropped_in_comparison_mode(self):
        block = format_product(self.product, show_prices=False)

        self.assertNotIn("642", block)
        self.assertNotIn("💡 Value Pick", block)
        self.assertNotIn("Available Sizes", block)

    def test_the_scent_data_comparison_needs_is_still_present(self):
        block = format_product(self.product, show_prices=False)

        self.assertIn("Bergamot", block)
        self.assertIn("Dior Sauvage", block)

    def test_prices_are_still_shown_by_default(self):
        """Every other branch depends on them."""
        block = format_product(self.product)

        self.assertIn("642", block)
        self.assertIn("💡 Value Pick", block)


class ObjectionDetectionTests(TestCase):
    """Which objection was raised, decided in code rather than by a twelfth intent."""

    def _kind(self, message):
        found = sales_objection.detect(message)
        return found.kind if found else None

    def test_each_objection_type_is_recognised(self):
        cases = (
            ("غالي شوية عليا", "price"),
            ("ليه أدفع 1200 بدل 500؟", "price_gap"),
            ("خايف الثبات يطلع وحش", "longevity_doubt"),
            ("ده تقليد ولا أصلي؟", "authenticity_doubt"),
            ("جربت قبل كده ومعجبنيش", "tried_before"),
            ("مش واثق بصراحة", "not_sure"),
            ("هفكر وأرجعلك", "thinking"),
            ("مش عارف أختار", "cant_choose"),
            ("مش عارف إذا هتعجبني", "wont_like"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(self._kind(message), expected)

    def test_an_ordinary_request_is_not_an_objection(self):
        for message in ("عايز عطر رجالي", "بكام سوفاج؟", "تمام خدلي الـ90"):
            with self.subTest(message=message):
                self.assertIsNone(sales_objection.detect(message))

    def test_the_transcript_complaint_is_a_complaint_not_an_objection(self):
        """A customer describing a perfume they already bought needs resolving before
        selling — the reply that failed here explained skin chemistry instead."""
        found = sales_objection.detect(
            "جبت من عندكم عطر قبل كده وكان مكتوب ثابت 8 ساعات، وبعد ساعتين مش بحسه. "
            "خايف أطلب تاني وأدفع فلوس على الفاضي."
        )

        self.assertEqual(found.kind, "longevity_doubt")
        self.assertTrue(found.is_complaint)

    def test_a_forward_looking_worry_is_not_a_complaint(self):
        found = sales_objection.detect("خايف الثبات يطلع وحش")

        self.assertFalse(found.is_complaint)

    def test_a_tatweel_does_not_defeat_matching(self):
        """Customers write "بـ1200" constantly, and normalize_arabic leaves the tatweel."""
        self.assertEqual(self._kind("ليه أدفع بـ1200 بدل 500؟"), "price_gap")

    def test_every_kind_has_playbook_guidance(self):
        for _, phrases in sales_objection._PATTERNS:
            self.assertTrue(phrases)
        kinds = {kind for kind, _ in sales_objection._PATTERNS}
        self.assertEqual(kinds - set(sales_objection.PLAYBOOK), set())

    def test_blank_input_is_safe(self):
        for blank in ("", None, "   "):
            with self.subTest(blank=blank):
                self.assertIsNone(sales_objection.detect(blank))


class SalesStageTests(TestCase):
    """Where the customer stands, and whether closing has been earned."""

    def test_an_objection_outranks_the_classification(self):
        """"غالي" arrives labelled faq or product_info, and answering it as an ordinary
        question is the defend-instead-of-address failure."""
        found = sales_objection.detect("غالي شوية")

        self.assertEqual(
            sales_stage.derive("product_info", "غالي شوية", objection=found),
            sales_stage.OBJECTION,
        )

    def test_a_past_purchase_objection_is_the_complaint_stage(self):
        found = sales_objection.detect("العطر اللي اشتريته مش ثابت")

        self.assertEqual(
            sales_stage.derive("faq", "العطر اللي اشتريته مش ثابت", objection=found),
            sales_stage.COMPLAINT,
        )

    def test_stages_map_from_the_classification(self):
        cases = (
            ("order", sales_stage.ORDER_COLLECTION),
            ("comparison", sales_stage.COMPARISON),
            ("identification", sales_stage.IDENTIFICATION),
            ("handoff", sales_stage.COMPLAINT),
            ("greeting", sales_stage.DISCOVERY),
        )
        for request_type, expected in cases:
            with self.subTest(request_type=request_type):
                self.assertEqual(sales_stage.derive(request_type, "أي كلام"), expected)

    def test_a_recommendation_with_no_constraints_is_discovery(self):
        self.assertEqual(
            sales_stage.derive("recommendation", "عايز عطر", intent={}),
            sales_stage.DISCOVERY,
        )

    def test_a_recommendation_with_constraints_is_the_recommendation_stage(self):
        self.assertEqual(
            sales_stage.derive("recommendation", "عايز عطر", intent={"notes": ["oud"]}),
            sales_stage.RECOMMENDATION,
        )

    def test_a_price_question_is_purchase_intent_but_a_scent_question_is_not(self):
        """The persona wants the next step offered on a price question and not on a
        factual one."""
        self.assertEqual(
            sales_stage.derive("product_info", "سوفاج بكام؟"),
            sales_stage.PURCHASE_INTENT,
        )
        self.assertEqual(
            sales_stage.derive("product_info", "ريحته ايه؟"),
            sales_stage.RECOMMENDATION,
        )

    def test_closing_is_allowed_only_at_the_buying_stages(self):
        for stage in (sales_stage.PURCHASE_INTENT, sales_stage.ORDER_COLLECTION):
            with self.subTest(stage=stage):
                self.assertTrue(sales_stage.closing_allowed(stage))

        for stage in (sales_stage.DISCOVERY, sales_stage.RECOMMENDATION,
                      sales_stage.COMPARISON, sales_stage.OBJECTION,
                      sales_stage.IDENTIFICATION, sales_stage.COMPLAINT):
            with self.subTest(stage=stage):
                self.assertFalse(sales_stage.closing_allowed(stage))


class ConstraintAcknowledgementTests(TestCase):
    """What the customer said must reach the prompt that answers them.

    The failure: five stated constraints, and the reply was "ميزانيتك في حدود كام؟" with
    no sign any of them had registered. Every one was extracted correctly and then dropped,
    because the budget gate built its prompt from a hardcoded sentence.
    """

    def test_constraints_are_rendered_as_short_arabic_phrases(self):
        phrases = sales_constraints.describe({
            "gender": "male",
            "occasion": "evening",
            "longevity": "long-lasting",
            "avoid_traits": ["heavy", "suffocating"],
        })

        self.assertIn("رجالي", phrases)
        self.assertIn("للسهرات بالليل", phrases)
        self.assertIn("ثابت", phrases)
        self.assertIn("مش تقيل", phrases)
        self.assertIn("مش خانق", phrases)

    def test_the_hint_forbids_reciting_everything_back(self):
        hint = sales_constraints.acknowledgement_hint({"gender": "male", "notes": ["oud"]})

        self.assertIn("نص جملة قصيرة", hint)
        self.assertIn("ممنوع تكرار كلام العميل", hint)
        self.assertIn("ممنوع تستخدم نفس الصيغة", hint)

    def test_the_hint_does_not_pin_one_opening_phrase(self):
        """It briefly named "تمام جداً، أرشحلك..." as the way in, and conv_990 opened three
        of six replies with exactly that. Describing the move is fine; quoting it is not."""
        hint = sales_constraints.acknowledgement_hint({"gender": "male"})

        self.assertNotIn("تمام جداً، أرشحلك", hint)

    def test_no_constraints_produces_no_hint(self):
        self.assertEqual(sales_constraints.acknowledgement_hint({}), "")

    def test_an_unmapped_value_is_echoed_rather_than_dropped(self):
        self.assertIn("للجيم", sales_constraints.describe({"occasion": "للجيم"}))

    def test_the_multiple_gender_signal_is_not_echoed_as_a_preference(self):
        """It means "he wants one of each, ask which first" — not a taste."""
        self.assertEqual(sales_constraints.describe({"gender": "multiple"}), [])

    def test_the_scenario_a_request_clears_the_recommend_threshold(self):
        """"فخمة وثابتة للليل بس مش خانقة" — enough to recommend from, so the turn must
        not be blocked on a budget question."""
        intent = {
            "gender": "male",
            "occasion": "evening",
            "longevity": "long-lasting",
            "avoid_traits": ["heavy", "suffocating"],
        }

        self.assertGreaterEqual(
            sales_constraints.taste_constraint_count(intent),
            sales_constraints.MIN_CONSTRAINTS_TO_RECOMMEND,
        )
        self.assertTrue(sales_constraints.can_recommend_without_budget(intent))

    def test_a_bare_request_does_not_clear_the_threshold(self):
        self.assertFalse(sales_constraints.can_recommend_without_budget({"gender": "male"}))

    def test_a_budget_alone_is_not_taste_information(self):
        """It is the thing being asked about, so counting it would let a bare budget stand
        in for knowing anything about the customer."""
        self.assertEqual(
            sales_constraints.taste_constraint_count({"max_price": 1000}), 0
        )

    def test_a_gift_with_unknown_taste_is_detected(self):
        is_gift, taste_known = sales_constraints.gift_context(
            "عايز برفان لمراتي هدية بس مش عارف هي بتحب إيه", intent={}
        )

        self.assertTrue(is_gift)
        self.assertFalse(taste_known)

    def test_a_gift_with_stated_taste_is_not_uncertain(self):
        is_gift, taste_known = sales_constraints.gift_context(
            "عايز هدية لمراتي، بتحب الفانيليا", intent={"notes": ["vanilla"]}
        )

        self.assertTrue(is_gift)
        self.assertTrue(taste_known)

    def test_a_non_gift_request_is_not_flagged(self):
        is_gift, _ = sales_constraints.gift_context("عايز عطر لنفسي", intent={})

        self.assertFalse(is_gift)

    def test_the_gift_hint_forbids_guarantees(self):
        """"الاتنين مضمونين" is not something we can know about a recipient we have never
        met."""
        hint = sales_constraints.GIFT_UNCERTAINTY_HINT

        self.assertIn("ممنوع", hint)
        self.assertIn("مضمون", hint)
        self.assertIn("برفان كانت بتحبه", hint)


class PrematureClosingTests(TestCase):
    """Closing questions are stripped where the stage has not earned them.

    Kept out of sanitize_reply on purpose: a legitimate close must survive untouched, and
    "الـ 90 ملي أوفر بكتير. أجيبلك الـ 90 ولا الـ 50؟" is pinned as passing through.
    """

    def test_the_transcripts_premature_close_is_removed(self):
        cleaned = strip_premature_closing(
            "Ambero ريحته دافية وثابتة. تحب أساعدك في الطلب؟"
        )

        self.assertNotIn("تحب أساعدك", cleaned)
        self.assertIn("Ambero", cleaned)

    def test_the_common_closing_variants_are_removed(self):
        for closer in ("تحب تطلب؟", "تحب تطلبه؟", "نسجل الطلب؟", "تحب نكمل الطلب؟"):
            with self.subTest(closer=closer):
                cleaned = strip_premature_closing(f"العطر ده ثابت جداً. {closer}")

                self.assertNotIn(closer.rstrip("؟"), cleaned)
                self.assertIn("ثابت", cleaned)

    def test_a_size_close_is_removed_too(self):
        cleaned = strip_premature_closing("ده أنسب ليك. أجيبلك الـ90 ولا الـ50؟")

        self.assertNotIn("أجيبلك", cleaned)

    def test_a_narrowing_question_is_not_a_close_and_survives(self):
        """Closing is not the same as asking a useful question."""
        reply = "فهمتك. ميزانيتك في حدود كام؟"

        self.assertEqual(strip_premature_closing(reply), reply)

    def test_a_reply_that_is_only_a_close_is_left_alone(self):
        only_close = "تحب تطلب؟"

        self.assertEqual(strip_premature_closing(only_close), only_close)

    def test_sanitize_reply_still_leaves_a_legitimate_close_untouched(self):
        """The pinned guarantee: the two passes stay separate."""
        reply = "الـ 90 ملي أوفر بكتير. أجيبلك الـ 90 ولا الـ 50؟"

        self.assertEqual(sanitize_reply(reply), reply)

    def test_empty_and_none_pass_through(self):
        self.assertEqual(strip_premature_closing(""), "")
        self.assertIsNone(strip_premature_closing(None))


class MarketingLanguageTests(TestCase):
    """Brochure phrasing and manufactured precision are removed, not asked about.

    The persona's own approved-words list recommended "جذابة جداً" — the exact register the
    evaluation flagged. That is fixed at the source; this catches what the model still
    produces.
    """

    def test_intensifiers_are_softened_without_breaking_the_sentence(self):
        cleaned = soften_marketing_language("ريحته جذابة جداً وفخمة جداً")

        self.assertNotIn("جداً", cleaned)
        self.assertIn("جذابة", cleaned)
        self.assertIn("فخمة", cleaned)

    def test_brochure_phrases_are_replaced(self):
        cleaned = soften_marketing_language("فيه لمسة عصرية جذابة وتركيبة رائعة")

        self.assertNotIn("لمسة عصرية جذابة", cleaned)
        self.assertNotIn("تركيبة رائعة", cleaned)

    def test_a_manufactured_similarity_percentage_is_removed(self):
        """"95% similar to the original" is not a fact we hold."""
        cleaned = soften_marketing_language("ده مطابق للأصل بنسبة 95% تقريباً")

        self.assertNotIn("95", cleaned)

    def test_an_absolute_guarantee_is_removed(self):
        for claim in ("ده مضمون 100%", "الاتنين مضمونين"):
            with self.subTest(claim=claim):
                cleaned = soften_marketing_language(claim)

                self.assertNotIn("مضمون 100%", cleaned)
                self.assertNotIn("مضمونين", cleaned)

    def test_the_personas_own_quality_reassurance_survives(self):
        """The trade-secrets rule says "أضمنلك إن جودتها هتعجبك" and is ratchet-pinned, so
        the guard must target quantified certainty rather than the word itself."""
        reply = "دي أسرار المهنة يا فندم، بس أضمنلك إن جودتها هتعجبك جداً!"

        self.assertIn("أضمنلك", soften_marketing_language(reply))

    def test_structural_emoji_survive_the_emoji_cap(self):
        """🔹 opens each recommendation line and 💰 marks the total order_service greps
        for — stripping either would damage the reply."""
        reply = "🔹 Ambero: ريحته دافية\n🔹 Citrolo: منعش\n🔹 Sauvage: نضيف\n💰 الإجمالي: 900"

        cleaned = soften_marketing_language(reply)

        self.assertEqual(cleaned.count("🔹"), 3)
        self.assertIn("💰", cleaned)

    def test_decorative_emoji_spam_is_capped(self):
        cleaned = soften_marketing_language("ريحته تجنن 😍😍🥰😻💕🌸")

        self.assertLess(len(cleaned), len("ريحته تجنن 😍😍🥰😻💕🌸"))

    def test_a_clean_reply_is_unchanged(self):
        reply = "Ambero ريحته دافية بالتوابل والفانيليا، ثابت وفواح."

        self.assertEqual(soften_marketing_language(reply), reply)

    def test_empty_and_none_pass_through(self):
        self.assertEqual(soften_marketing_language(""), "")
        self.assertIsNone(soften_marketing_language(None))


class SalesScenarioTests(TestCase):
    """The nine evaluation scenarios, routed end to end.

    Each asserts on the prompt the branch actually built, following the house pattern from
    RouterBranchPromptTests: what matters is whether the model was *told* the right thing,
    since the reply itself is the model's to write. Letters match the evaluation brief.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        StoreSettings.objects.create(store=self.store, business_facts="عندنا فرع في مدينة نصر.")
        self.conversation = Conversation.objects.create(store=self.store)
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.own = Brand.objects.create(store=self.store, name="Perfamix Test")

        self.sauvage = self._make(
            "Dior Sauvage", "Bergamot, Pepper", "Lavender", "Ambroxan, Cedar",
            longevity="8 hours", projection="Strong", occasion="Casual",
        )
        self.light = self._make(
            "Citrolo", "Bergamot, Lemon", "Mint", "Cedar", brand=self.own,
            longevity="8 hours", projection="Moderate", occasion="Evening",
        )
        self.heavy = self._make(
            "Oudy", "Oud", "Incense", "Amber, Leather",
            longevity="12 hours", projection="Enormous", occasion="Evening",
        )
        self.vanilla = self._make(
            "Black Opium", "Coffee", "Vanilla", "Vanilla, Patchouli",
            gender="female", longevity="10 hours",
        )

    def _make(self, name, top, middle, base, brand=None, gender="male", **extra):
        product = Product.objects.create(
            store=self.store, brand=brand or self.brand, name=name, gender=gender,
            top_notes=top, middle_notes=middle, base_notes=base,
            **extra
        )
        ProductVariant.objects.create(product=product, volume=50, price=600)
        ProductVariant.objects.create(product=product, volume=90, price=900)
        return product

    def _route_recommendation(self, message, intent):
        """Route a recommendation and return (prompt_sent_to_recommend, general_prompt)."""
        with mock.patch("products.services.router.classify", return_value="recommendation"), \
             mock.patch("products.services.router.extract_intent", return_value=intent), \
             mock.patch("products.services.ai.recommendation.chat", return_value="ok") as rec, \
             mock.patch("products.services.general_service.chat", return_value="ok") as gen:
            route(message, [], self.store, self.conversation)

        recommend_prompt = (
            "\n".join(m["content"] for m in rec.call_args[0][0]) if rec.called else ""
        )
        general_prompt = (
            "\n".join(m["content"] for m in gen.call_args[0][0]) if gen.called else ""
        )
        return recommend_prompt, general_prompt

    # ---- A: rich constraints, no budget ----

    def test_a_a_rich_request_is_recommended_not_blocked_on_a_budget_question(self):
        """The transcript failure: five constraints answered with "ميزانيتك كام؟" alone."""
        recommend_prompt, general_prompt = self._route_recommendation(
            "عايز برفان رجالي ريحته فخمة وثابتة، ويكون مناسب للخروجات بالليل، "
            "بس مش عايز حاجة تقيلة أو تخنق اللي حواليا.",
            {"gender": "male", "occasion": "evening", "longevity": "long-lasting",
             "avoid_traits": ["heavy", "suffocating"]},
        )

        self.assertTrue(recommend_prompt, "the turn was blocked instead of recommending")
        self.assertNotIn("ميزانيتك في حدود كام عشان أرشحلك", general_prompt)

    def test_a_the_stated_constraints_reach_the_recommendation_prompt(self):
        recommend_prompt, _ = self._route_recommendation(
            "عايز برفان رجالي ثابت للخروجات بالليل بس مش تقيل",
            {"gender": "male", "occasion": "evening", "longevity": "long-lasting",
             "avoid_traits": ["heavy"]},
        )

        self.assertIn("العميل قال بالفعل", recommend_prompt)
        self.assertIn("للسهرات بالليل", recommend_prompt)
        self.assertIn("مش تقيل", recommend_prompt)

    def test_a_the_acknowledgement_is_a_hint_not_a_script(self):
        """A single mandated sentence would be the same bug in a nicer costume — and it
        happened: the hint briefly quoted "تمام جداً، أرشحلك..." and conv_990 opened three of
        six replies with exactly that."""
        recommend_prompt, _ = self._route_recommendation(
            "عايز حاجة ثابتة للليل مش تقيلة",
            {"gender": "male", "occasion": "evening", "longevity": "long-lasting",
             "avoid_traits": ["heavy"]},
        )

        self.assertIn("ممنوع تستخدم نفس الصيغة", recommend_prompt)
        self.assertIn("ممنوع تكرار كلام العميل", recommend_prompt)
        self.assertNotIn("تمام جداً، أرشحلك", recommend_prompt)

    def test_a_a_sparse_request_still_asks_for_a_budget_but_acknowledges_first(self):
        _, general_prompt = self._route_recommendation(
            "عايز عطر رجالي فيه عود", {"gender": "male", "notes": ["oud"]}
        )

        self.assertIn("العميل قال بالفعل", general_prompt)
        self.assertIn("اعترف باللي قاله", general_prompt)

    def test_a_a_heavy_perfume_is_flagged_when_heaviness_was_excluded(self):
        results = search_products(
            {"gender": "male", "avoid_traits": ["heavy", "suffocating"]}, store=self.store
        )
        ranked = results["ranked"]

        self.assertTrue(ranked[self.heavy.id].mismatches)

    # ---- B: similar to X, not mainstream ----

    def test_b_similarity_drives_the_recommendation_and_is_evidenced(self):
        recommend_prompt, _ = self._route_recommendation(
            "بحب ريحة Sauvage بس عايز حاجة شبهه ومش منتشرة أوي",
            {"gender": "male", "similar_to": "Dior Sauvage",
             "similar_to_notes": ["bergamot", "pepper", "ambroxan"],
             "exclude_names": ["Dior Sauvage"], "wants_uncommon": True},
        )

        self.assertIn("طلب حاجة شبه", recommend_prompt)
        self.assertIn("Match:", recommend_prompt)

    def test_b_the_prompt_forbids_a_similarity_percentage(self):
        """"95% similar to the original" is the claim this has to make unsayable."""
        recommend_prompt, _ = self._route_recommendation(
            "عايز حاجة شبه Sauvage",
            {"gender": "male", "similar_to": "Dior Sauvage",
             "similar_to_notes": ["bergamot", "ambroxan"]},
        )

        self.assertIn("ممنوع تذكر نسبة مئوية للتشابه", recommend_prompt)

    def test_b_no_close_match_is_admitted_rather_than_faked(self):
        recommend_prompt, _ = self._route_recommendation(
            "عايز حاجة شبه Creed Aventus",
            {"gender": "male", "similar_to": "Creed Aventus",
             "similar_to_notes": ["pineapple", "birch", "ambergris"]},
        )

        self.assertIn("مفيش حاجة قريبة", recommend_prompt)
        self.assertIn("ممنوع تقول عن أي عطر إنه شبهه", recommend_prompt)

    # ---- C: identification from clues ----

    def _route_identification(self, message, clues):
        with mock.patch("products.services.router.classify", return_value="identification"), \
             mock.patch(
                 "products.services.identification_service.chat",
                 side_effect=[json.dumps(clues), "ok"],
             ) as chat_mock:
            route(message, [], self.store, self.conversation)
        return "\n".join(m["content"] for m in chat_mock.call_args[0][0])

    def test_c_a_clue_based_guess_is_hedged_not_asserted(self):
        """"العطر اللي بتوصفه هو Black Opium" turned four vague clues into a fact."""
        prompt = self._route_identification(
            "مش فاكر اسم البرفان، الزجاجة سودا والريحة فيها فانيليا وحاجة حلوة وثابتة",
            {"notes": ["vanilla"], "sweet": True, "gender": "female",
             "bottle_color": "black", "longevity_hint": "long"},
        )

        self.assertTrue(
            "غالبًا" in prompt or "قريب من" in prompt,
            "the identification must be hedged, not asserted",
        )

    def test_c_the_bottle_colour_is_not_treated_as_evidence(self):
        """There is no bottle-colour field, so it cannot corroborate anything."""
        prompt = self._route_identification(
            "الزجاجة سودا وفيها فانيليا",
            {"notes": ["vanilla"], "bottle_color": "black"},
        )

        self.assertIn("مش بنسجل ده في بياناتنا", prompt)
        self.assertIn("مينفعش تعتمد عليه كدليل", prompt)

    def test_c_exactly_one_clarifying_question_is_requested(self):
        prompt = self._route_identification(
            "نسيت اسمه بس فيه فانيليا", {"notes": ["vanilla"]}
        )

        self.assertIn("سؤال واحد", prompt)
        self.assertIn("ممنوع تسأل أكتر من سؤال", prompt)

    def test_c_identification_does_not_try_to_close_the_sale(self):
        prompt = self._route_identification(
            "نسيت اسمه بس فيه فانيليا", {"notes": ["vanilla"]}
        )

        self.assertIn("ممنوع تسأله يطلب", prompt)

    def test_c_an_unstocked_guess_is_named_only_with_not_available(self):
        prompt = self._route_identification(
            "نسيت اسمه، ريحته أناناس",
            {"notes": [], "likely_known_perfume": "Creed Aventus"},
        )

        self.assertIn("Creed Aventus", prompt)
        self.assertIn("مش موجود عندنا", prompt)
        self.assertIn("ممنوع توحي إننا بنبيعه", prompt)

    def test_c_no_clues_means_no_name_is_invented(self):
        prompt = self._route_identification("مش فاكر اسمه خلاص", {})

        self.assertIn("ممنوع تخترع اسم عطر", prompt)

    def test_c_confidence_rises_with_verifiable_clue_types(self):
        weak = identification_service.score_candidates(
            {"notes": ["vanilla"]}, [self.vanilla]
        )
        strong = identification_service.score_candidates(
            {"notes": ["vanilla", "coffee"], "sweet": True, "gender": "female",
             "name_fragment": "opium"},
            [self.vanilla],
        )

        self.assertEqual(identification_service.confidence_tier([]), "none")
        self.assertEqual(identification_service.confidence_tier(strong), "high")
        self.assertIn(identification_service.confidence_tier(weak), ("low", "medium"))

    # ---- D: complaint about a past purchase ----

    def _route_objection(self, message, classification="faq"):
        with mock.patch("products.services.router.classify", return_value=classification), \
             mock.patch("products.services.objection_service.resolve_products", return_value=[]), \
             mock.patch("products.services.objection_service.chat", return_value="ok") as chat_mock:
            route(message, [], self.store, self.conversation)
        self.assertTrue(chat_mock.called, "the objection branch was not reached")
        return "\n".join(m["content"] for m in chat_mock.call_args[0][0])

    def test_d_a_longevity_complaint_reaches_the_objection_branch(self):
        prompt = self._route_objection(
            "جبت من عندكم عطر قبل كده وكان مكتوب ثابت 8 ساعات، وبعد ساعتين مش بحسه. "
            "خايف أطلب تاني وأدفع فلوس على الفاضي."
        )

        self.assertIn("longevity_doubt", prompt)

    def test_d_acknowledgement_comes_before_any_explanation(self):
        """The reply that failed opened with skin chemistry and bottle economics."""
        prompt = self._route_objection("العطر اللي اشتريته من عندكم مش ثابت")

        self.assertIn("ابدأ بالاعتراف", prompt)
        self.assertIn("ممنوع تبدأ بشرح", prompt)

    def test_d_a_complaint_is_not_blamed_on_the_customer(self):
        prompt = self._route_objection("العطر اللي اشتريته مش ثابت")

        self.assertIn("ممنوع تقول إن المشكلة منه", prompt)

    def test_d_a_complaint_turn_may_not_close_the_sale(self):
        prompt = self._route_objection("العطر اللي اشتريته مش ثابت")

        self.assertIn("ممنوع تقفل البيعة", prompt)

    def test_d_the_complaint_branch_does_not_silence_the_bot(self):
        """needs_human makes views.py return an empty reply, so marking every complaint as
        needing a human would go quiet on the customers who most need answering."""
        self._route_objection("العطر اللي اشتريته مش ثابت")
        self.conversation.refresh_from_db()

        self.assertFalse(self.conversation.needs_human)

    def test_d_an_explicit_request_for_a_human_still_hands_off(self):
        """The objection branch must not swallow a genuine handoff."""
        with mock.patch("products.services.router.classify", return_value="handoff"), \
             mock.patch("products.services.general_service.chat", return_value="ok"), \
             mock.patch("products.services.router.notify_handoff") as notify:
            route("العطر مش ثابت وعايز اكلم حد حقيقي", [], self.store, self.conversation)

        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.needs_human)
        self.assertTrue(notify.called)

    def test_d_no_invented_remedy_is_offered(self):
        """There is no returns or exchange field, so none may be promised."""
        prompt = self._route_objection("العطر اللي اشتريته مش ثابت")

        self.assertIn("ممنوع تعرض استرجاع أو استبدال", prompt)

    # ---- E: price-gap objection ----

    def test_e_the_price_gap_objection_is_recognised(self):
        prompt = self._route_objection("ليه أجيب ده بـ1200 وأنا ممكن أجيب حاجة بـ500؟")

        self.assertIn("price_gap", prompt)

    def test_e_the_value_answer_is_built_from_real_dimensions(self):
        with mock.patch("products.services.router.classify", return_value="faq"), \
             mock.patch(
                 "products.services.objection_service.resolve_products",
                 return_value=[self.light, self.heavy],
             ), \
             mock.patch("products.services.objection_service.chat", return_value="ok") as chat_mock:
            route("ليه أدفع 1200 بدل 500؟", [], self.store, self.conversation)

        prompt = "\n".join(m["content"] for m in chat_mock.call_args[0][0])

        self.assertIn("الفرق الحقيقي بين", prompt)
        self.assertIn("سعر الملي", prompt)

    def test_e_recommending_the_cheaper_option_is_explicitly_permitted(self):
        prompt = self._route_objection("ليه أدفع 1200 بدل 500؟")

        self.assertIn("الأرخص أنسب ليه", prompt)

    def test_e_no_invented_differentiator_is_allowed(self):
        prompt = self._route_objection("ليه أدفع 1200 بدل 500؟")

        self.assertIn("الفروقات الحقيقية الموجودة في البيانات", prompt)

    # ---- F: gift with unknown taste ----

    def test_f_a_gift_with_unknown_taste_acknowledges_the_uncertainty(self):
        _, general_prompt = self._route_recommendation(
            "عايز برفان لمراتي هدية بس مش عارف هي بتحب إيه",
            {"gender": "female"},
        )

        self.assertIn("مش عارف ذوق المستلم", general_prompt)
        self.assertIn("بتساعده يقرب لذوقها مش بتضمنه", general_prompt)

    def test_f_the_one_high_value_question_is_asked(self):
        _, general_prompt = self._route_recommendation(
            "عايز هدية لمراتي مش عارف ذوقها", {"gender": "female"}
        )

        self.assertIn("برفان كانت بتحبه", general_prompt)

    def test_f_guarantees_are_forbidden(self):
        """"الاتنين مضمونين" about a recipient we have never met."""
        _, general_prompt = self._route_recommendation(
            "عايز هدية لمراتي مش عارف ذوقها", {"gender": "female"}
        )

        self.assertIn('ممنوع تقول "مضمون"', general_prompt)

    # ---- G: budget ----

    def test_g_a_stated_budget_filters_and_is_not_asked_again(self):
        recommend_prompt, _ = self._route_recommendation(
            "عايز حاجة كويسة تحت 1000", {"gender": "male", "max_price": 1000}
        )

        self.assertIn("ميزانية العميل: 1000", recommend_prompt)
        self.assertIn("متسألوش عن الميزانية تاني", recommend_prompt)

    def test_g_a_budget_excludes_products_priced_above_it_from_the_matched_set(self):
        """The matched set honours the budget. The alternatives fallback deliberately
        relaxes it — that predates this work and `budget_label` marks such sizes ❌ — so the
        guarantee to pin is that an affordable match wins and an unaffordable one is
        flagged, not that it never appears."""
        affordable = self._make("Affordable Rose", "Rose", "", "Musk")
        expensive = self._make("Pricey", "Rose", "", "Musk")
        expensive.variants.all().delete()
        ProductVariant.objects.create(product=expensive, volume=50, price=5000)

        results = search_products(
            {"gender": "male", "max_price": 1000, "notes": ["rose"]}, store=self.store
        )

        self.assertEqual(
            [product.name for product in results["products"]], ["Affordable Rose"]
        )
        self.assertNotIn(expensive.id, results["ranked"])

    def test_g_an_unaffordable_alternative_is_flagged_and_ranked_last(self):
        expensive = self._make("Pricey", "Tuberose", "", "Musk")
        expensive.variants.all().delete()
        ProductVariant.objects.create(product=expensive, volume=50, price=5000)

        results = search_products(
            {"gender": "male", "max_price": 1000, "notes": ["tuberose"]},
            store=self.store,
        )
        entry = results["ranked"][expensive.id]

        self.assertIn("مفيش حجم متاح داخل ميزانيته", entry.mismatches)

    def test_g_a_saved_budget_survives_a_later_turn(self):
        """merge_preferences must still restore it — the memory path is unchanged."""
        merge_preferences(self.conversation, {"gender": "male", "max_price": 1000})

        later = merge_preferences(self.conversation, {"gender": "male"})

        self.assertEqual(later["max_price"], 1000)

    def test_g_an_exclusion_also_survives_a_later_turn(self):
        """Losing an exclusion is worse than losing a preference: it means recommending
        the exact thing the customer rejected."""
        merge_preferences(
            self.conversation, {"gender": "male", "avoid_traits": ["heavy"]}
        )

        later = merge_preferences(self.conversation, {"gender": "male"})

        self.assertEqual(later["avoid_traits"], ["heavy"])

    # ---- H: ready to buy ----

    def test_h_a_size_choice_reaches_the_order_flow_unchanged(self):
        with mock.patch("products.services.router.classify", return_value="order"), \
             mock.patch("products.services.router.handle_order", return_value=("ok", "")) as order:
            route("تمام خدلي الـ90ml", [], self.store, self.conversation)

        self.assertTrue(order.called, "the order flow must still be reached")

    def test_h_closing_is_allowed_once_the_customer_is_buying(self):
        self.assertTrue(sales_stage.closing_allowed(sales_stage.ORDER_COLLECTION))
        self.assertTrue(sales_stage.closing_allowed(sales_stage.PURCHASE_INTENT))

    def test_h_a_purchase_intent_reply_keeps_its_closing_question(self):
        """The gate must not strip a close the customer has earned."""
        reply = "تمام، الـ90 بـ 900 جنيه. تحب أساعدك في الطلب؟"

        with mock.patch("products.services.router.classify", return_value="product_info"), \
             mock.patch(
                 "products.services.router.get_product_info", return_value=(reply, "")
             ):
            result, _ = route("الـ90 بكام؟", [], self.store, self.conversation)

        self.assertIn("تحب أساعدك في الطلب", result)

    def test_h_the_same_close_is_stripped_at_a_deciding_stage(self):
        """Same reply, earlier stage — this is the whole point of the gate."""
        reply = "Citrolo ريحته منعشة. تحب أساعدك في الطلب؟"

        with mock.patch("products.services.router.classify", return_value="product_info"), \
             mock.patch(
                 "products.services.router.get_product_info", return_value=(reply, "")
             ):
            result, _ = route("ريحته ايه؟", [], self.store, self.conversation)

        self.assertNotIn("تحب أساعدك في الطلب", result)

    # ---- I: factual product question ----

    def test_i_a_factual_question_is_answered_from_the_database(self):
        with mock.patch("products.services.router.classify", return_value="product_info"), \
             mock.patch(
                 "products.services.product_info.resolve_products",
                 return_value=[self.sauvage],
             ), \
             mock.patch("products.services.product_info.chat", return_value="ok") as chat_mock:
            route("ثبات سوفاج قد ايه؟", [], self.store, self.conversation)

        prompt = "\n".join(m["content"] for m in chat_mock.call_args[0][0])

        self.assertIn("8 hours", prompt)
        self.assertIn("ممنوع تخترع أي معلومة", prompt)

    def test_i_an_unrecorded_fact_is_refused_rather_than_invented(self):
        bare = self._make("Bare", "", "", "")
        bare.longevity = ""
        bare.save()

        with mock.patch("products.services.router.classify", return_value="product_info"), \
             mock.patch(
                 "products.services.product_info.resolve_products", return_value=[bare]
             ), \
             mock.patch("products.services.product_info.chat", return_value="ok") as chat_mock:
            route("ثباته قد ايه؟", [], self.store, self.conversation)

        prompt = "\n".join(m["content"] for m in chat_mock.call_args[0][0])

        self.assertIn("غير مسجل", prompt)
        self.assertIn("ممنوع تذكر رقم ساعات", prompt)

    def test_i_a_factual_question_does_not_close_the_sale(self):
        with mock.patch("products.services.router.classify", return_value="product_info"), \
             mock.patch(
                 "products.services.router.get_product_info",
                 return_value=("ثباته 8 ساعات. تحب تطلب؟", ""),
             ):
            result, _ = route("ريحته عاملة ايه؟", [], self.store, self.conversation)

        self.assertNotIn("تحب تطلب", result)


class StoreConfiguredClaimsSurviveTests(TestCase):
    """A guard against inventing claims must not gag the store's own configured ones.

    StoreSettings.business_facts is documented as the place a store records "نسبة تشابه
    التركيب", so a similarity percentage can be a legitimate, store-authored fact. This is
    the same trap test_promotion_can_still_quote_the_stores_configured_offers exists to
    catch — a blanket price ban once came within one reading of gagging the promotion
    branch. The ban is on *deriving* a percentage from our ranking, never on relaying one.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        StoreSettings.objects.create(
            store=self.store,
            business_facts="تركيباتنا بتوصل لنسبة تشابه 90% مع العطر الأصلي.",
        )

    def test_the_configured_similarity_fact_reaches_the_prompt(self):
        prompt = get_system_prompt(self.store)

        self.assertIn("نسبة تشابه 90%", prompt)

    def test_the_sanitizer_does_not_strip_a_relayed_store_fact(self):
        """The store wrote this number; the bot is only repeating it."""
        reply = "تركيباتنا بتوصل لنسبة تشابه 90% مع العطر الأصلي يا فندم."

        self.assertIn("90%", soften_marketing_language(reply))

    def test_but_an_invented_match_percentage_is_still_removed(self):
        self.assertNotIn(
            "95", soften_marketing_language("ده مطابق للأصل بنسبة 95%")
        )


class IdentificationCarveOutIsScopedTests(TestCase):
    """Naming an unstocked perfume is allowed in identification only.

    The identification branch may say "غالبًا Black Opium، بس مش موجود عندنا" — a deliberate,
    scoped exception to the persona red line that a perfume absent from the data does not
    exist. The exception must not leak into the branches that recommend or answer freely,
    where it would become permission to invent products.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.conversation = Conversation.objects.create(store=self.store)

    def test_the_persona_still_forbids_naming_absent_products(self):
        """The ratchet-pinned rule is untouched."""
        prompt = get_system_prompt(self.store)

        self.assertIn("مش في البيانات", prompt)

    def test_the_no_product_data_branch_still_bans_naming_a_perfume(self):
        with mock.patch(
            "products.services.general_service.chat", return_value="ok"
        ) as chat_mock:
            handle_general("في بلو دي شانيل؟", [], self.store)

        prompt = "\n".join(m["content"] for m in chat_mock.call_args[0][0])

        self.assertIn("ممنوع تذكر سعر أو اسم عطر من ذاكرتك", prompt)

    def test_the_carve_out_text_appears_only_in_the_identification_branch(self):
        """Asserted on the carve-out's own instruction rather than on "مش موجود عندنا",
        which the persona itself contains in the very red line being carved out of. If this
        shows up in handle_general's prompt, the exception has leaked."""
        with mock.patch(
            "products.services.general_service.chat", return_value="ok"
        ) as chat_mock:
            handle_general("عايز عطر", [], self.store)

        prompt = "\n".join(m["content"] for m in chat_mock.call_args[0][0])

        self.assertNotIn("ممنوع توحي إننا بنبيعه", prompt)


class ThrottleKeyTests(TestCase):
    """What the throttles actually key on.

    There were no throttle tests at all before this, and all three keying defects were
    invisible without one: a bypassable IP key, five views throttling on a header they never
    send, and rate settings that were never read.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(username="o@x.com", password="pw")
        self.store = Store.objects.create(name="Perfamix Test", owner=self.owner)
        self.other = Store.objects.create(name="Other Store")

    def _key(self, throttle, store=None, user=None, **meta):
        request = self.factory.get("/", **meta)
        if store is not None:
            request.store = store
        request.user = user
        return throttle.get_cache_key(request, None)

    def test_each_store_gets_its_own_bucket(self):
        """The five JWT dashboard views read HTTP_X_API_KEY, which they never send, so they
        fell through to a bare IP and lost per-store isolation entirely."""
        throttle = StoreKeyThrottle()

        self.assertNotEqual(
            self._key(throttle, store=self.store),
            self._key(throttle, store=self.other),
        )

    def test_two_stores_behind_one_ip_do_not_collide(self):
        throttle = StoreKeyThrottle()
        shared = {"REMOTE_ADDR": "197.1.1.1"}

        self.assertNotEqual(
            self._key(throttle, store=self.store, **shared),
            self._key(throttle, store=self.other, **shared),
        )

    def test_the_scope_prefix_is_never_dropped(self):
        """The old fallback returned a bare ident, skipping cache_format — so two throttles
        hitting it shared one bucket and mixed their limits."""
        for throttle in (StoreKeyThrottle(), ChatThrottle()):
            with self.subTest(scope=throttle.scope):
                key = self._key(throttle, store=self.store)
                self.assertTrue(key.startswith(f"throttle_{throttle.scope}_"), key)

    def test_the_store_and_chat_scopes_stay_separate(self):
        self.assertNotEqual(
            self._key(StoreKeyThrottle(), store=self.store),
            self._key(ChatThrottle(), store=self.store),
        )

    def test_no_raw_api_key_reaches_the_cache_key(self):
        """It used to key on the API key verbatim, putting a store secret in the Redis
        keyspace where KEYS/MONITOR and key-level metrics can read it."""
        key = self._key(
            StoreKeyThrottle(), store=self.store,
            HTTP_X_API_KEY=self.store.api_key,
        )

        self.assertNotIn(self.store.api_key, key)

    def test_no_throttle_hardcodes_its_own_rate(self):
        """This is the defect itself. SimpleRateThrottle.__init__ consults get_rate() only
        `if not getattr(self, "rate", None)`, so a class-level `rate` made
        DEFAULT_THROTTLE_RATES unreachable and editing settings changed nothing."""
        from products import throttles as throttle_module

        for name in dir(throttle_module):
            attribute = getattr(throttle_module, name)
            if isinstance(attribute, type) and getattr(attribute, "scope", None):
                with self.subTest(throttle=name):
                    self.assertNotIn(
                        "rate", attribute.__dict__,
                        f"{name} pins its own rate, so settings are ignored for it",
                    )

    def test_the_rate_is_read_from_the_configured_table(self):
        """Patches THROTTLE_RATES rather than settings: DRF binds
        `THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES` at class-definition time, so
        override_settings cannot reach it. That table is what get_rate() reads."""
        rates = dict(ChatThrottle.THROTTLE_RATES)
        rates["chat"] = "7/minute"

        with mock.patch.object(ChatThrottle, "THROTTLE_RATES", rates):
            throttle = ChatThrottle()

        self.assertEqual(throttle.num_requests, 7)
        self.assertEqual(throttle.duration, 60)

    def test_every_scope_in_use_is_configured(self):
        """A scope missing from settings raises ImproperlyConfigured at throttle init, which
        would surface as a 500 on the endpoint rather than at startup."""
        from products import throttles as throttle_module

        configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for name in dir(throttle_module):
            attribute = getattr(throttle_module, name)
            scope = getattr(attribute, "scope", None)
            if isinstance(attribute, type) and scope:
                with self.subTest(throttle=name):
                    self.assertIn(scope, configured)


class ProxyHeaderBypassTests(TestCase):
    """The security fix: an IP throttle must not be keyed on a client-supplied header.

    With NUM_PROXIES unset, DRF's get_ident returns the whole X-Forwarded-For — which the
    client sends. Rotating one header gave a fresh bucket, and the only protection on the
    login and password-reset endpoints was an IP throttle.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def _ident(self, xff):
        request = self.factory.get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR=xff)
        return LoginThrottle().get_ident(request)

    def test_a_spoofed_forwarded_for_no_longer_changes_the_bucket(self):
        """A reverse proxy *appends* the true client IP to whatever the client sent, so with
        NUM_PROXIES=1 DRF reads the last entry — the part the client cannot forge. Varying
        the attacker-controlled prefix must therefore leave the bucket alone.

        Before the fix DRF joined the whole header, so the prefix was part of the key and
        one extra hop of junk bought a fresh bucket.
        """
        real_client = "197.55.55.55"

        self.assertEqual(
            self._ident(f"1.2.3.4, {real_client}"),
            self._ident(f"9.9.9.9, 8.8.8.8, {real_client}"),
        )

    def test_the_whole_header_is_no_longer_the_key(self):
        """The precise old behaviour: ''.join(xff.split()) when NUM_PROXIES is None."""
        from rest_framework.settings import api_settings

        self.assertNotEqual(
            api_settings.NUM_PROXIES, None,
            "with NUM_PROXIES unset the entire client-supplied header becomes the key",
        )

    def test_num_proxies_zero_ignores_the_header_entirely(self):
        """The correct setting for a directly-exposed gunicorn, where nothing appends a
        trustworthy entry and NUM_PROXIES=1 would trust the client's own value."""
        request = self.factory.get(
            "/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4"
        )

        with mock.patch("rest_framework.throttling.api_settings") as api:
            api.NUM_PROXIES = 0
            self.assertEqual(LoginThrottle().get_ident(request), "10.0.0.1")

    def test_num_proxies_is_configured(self):
        from rest_framework.settings import api_settings

        self.assertIsNotNone(
            api_settings.NUM_PROXIES,
            "unset NUM_PROXIES means DRF trusts the whole client-supplied XFF",
        )

    def test_the_real_client_ip_is_taken_from_behind_one_proxy(self):
        """With one trusted proxy the last XFF entry is the one the proxy appended."""
        request = self.factory.get(
            "/", REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="1.2.3.4, 197.55.55.55",
        )

        self.assertEqual(LoginThrottle().get_ident(request), "197.55.55.55")

    def test_credential_endpoints_are_throttled_far_below_the_anon_default(self):
        """All four had authentication_classes = [] and set no throttle_classes, so their
        only limit was AnonRateThrottle at 30/minute on a spoofable key."""
        from dashboard.views_auth import (
            ForgotPasswordView,
            LoginView,
            RegisterView,
            ResetPasswordView,
        )

        for view in (LoginView, RegisterView, ForgotPasswordView, ResetPasswordView):
            with self.subTest(view=view.__name__):
                self.assertTrue(view.throttle_classes, "no explicit throttle")
                throttle = view.throttle_classes[0]()
                self.assertLess(throttle.num_requests / throttle.duration, 30 / 60)


class WebhookIsNotThrottledTests(TestCase):
    """Meta's webhook must never be refused.

    The old WebhookThrottle keyed on the client IP, so every store shared one bucket and one
    busy store throttled the rest — and exceeding it returned 429, which makes Meta retry
    and, on sustained failure, disable the webhook subscription app-wide.
    """

    def test_the_webhook_view_has_no_throttle_at_all(self):
        from products.views_meta import MetaWebhookView

        self.assertEqual(MetaWebhookView.throttle_classes, [])

    def test_an_empty_list_not_a_missing_attribute(self):
        """Deleting the attribute is the trap: DRF then falls back to
        DEFAULT_THROTTLE_CLASSES — AnonRateThrottle at 30/minute on Meta's own IPs, which is
        a *tighter* global bucket than the one being removed, still answering 429."""
        from products.views_meta import MetaWebhookView

        self.assertIn("throttle_classes", MetaWebhookView.__dict__)

    def test_the_old_throttle_class_is_gone(self):
        from products import throttles

        self.assertFalse(hasattr(throttles, "WebhookThrottle"))


class RateLimitPrimitiveTests(TestCase):
    """products.services.rate_limit — the counter behind the message-path limits."""

    def setUp(self):
        from products.services import rate_limit

        self.rate_limit = rate_limit
        for bucket in ("a", "b", "minute", "hour"):
            rate_limit.reset(bucket)

    def test_exactly_the_limit_is_allowed(self):
        outcomes = [self.rate_limit.hit("a", 3, 60)[0] for _ in range(5)]

        self.assertEqual(outcomes, [True, True, True, False, False])

    def test_separate_buckets_do_not_share_a_counter(self):
        for _ in range(3):
            self.rate_limit.hit("a", 3, 60)

        self.assertTrue(self.rate_limit.hit("b", 3, 60)[0])

    def test_retry_after_is_the_window_length(self):
        self.rate_limit.hit("a", 1, 60)
        allowed, retry_after = self.rate_limit.hit("a", 1, 60)

        self.assertFalse(allowed)
        self.assertEqual(retry_after, 60)

    def test_the_longest_exhausted_window_decides_the_wait(self):
        """An hourly breach must not be retried in sixty seconds, only to be refused again."""
        buckets = [("minute", 2, 60), ("hour", 3, 3600)]
        for _ in range(3):
            self.rate_limit.hit_all(buckets)

        allowed, retry_after, exhausted = self.rate_limit.hit_all(buckets)

        self.assertFalse(allowed)
        self.assertEqual(retry_after, 3600)
        self.assertEqual(exhausted, "hour")

    def test_every_window_is_counted_even_after_one_refuses(self):
        """Short-circuiting would leave the hour counter behind whenever the minute limit
        tripped first, so sustained abuse would never reach the hourly ceiling."""
        buckets = [("minute", 1, 60), ("hour", 10, 3600)]
        for _ in range(4):
            self.rate_limit.hit_all(buckets)

        from django.core.cache import cache

        self.assertEqual(cache.get("ratelimit:hour"), 4)

    def test_peek_reports_without_counting(self):
        """A deferred message re-enters the task and must not be charged twice."""
        self.rate_limit.hit("a", 2, 60)

        for _ in range(5):
            self.assertEqual(self.rate_limit.peek([("a", 2, 60)]), (True, 0))

        from django.core.cache import cache

        self.assertEqual(cache.get("ratelimit:a"), 1)

    def test_peek_refuses_once_the_window_is_full(self):
        for _ in range(2):
            self.rate_limit.hit("a", 2, 60)

        self.assertEqual(self.rate_limit.peek([("a", 2, 60)]), (False, 60))

    def test_a_cache_outage_fails_open(self):
        """Matching products/cache.py's documented trade: losing accuracy during an outage
        beats taking the message path down with it."""
        with mock.patch("products.services.rate_limit.cache") as broken:
            broken.incr.return_value = None

            self.assertEqual(self.rate_limit.hit("a", 1, 60), (True, 0))


class MessageRateLimitTests(TestCase):
    """The per-sender and per-store limits on the expensive path.

    route() spends two to three model calls per message, and nothing capped per store or per
    end customer: the old WebhookThrottle keyed on Meta's IP, and usage_service counts
    without ever blocking. One WhatsApp user could bill the store without limit.
    """

    def setUp(self):
        from products.services import rate_limit
        from products import tasks

        self.tasks = tasks
        self.rate_limit = rate_limit
        self.store = Store.objects.create(name="Perfamix Test")
        for sender in ("2010", "2099"):
            for bucket, _limit, _window in tasks._rate_limit_buckets(
                self.store.id, "whatsapp", sender
            ):
                rate_limit.reset(bucket)

    def _run(self, deferrals=0, sender="2010"):
        """Run the task body with everything past the rate limit stubbed out."""
        with mock.patch.object(self.tasks, "route", return_value=("ok", "")), \
             mock.patch.object(self.tasks, "send_platform_message", return_value=None), \
             mock.patch.object(self.tasks, "sanitize_reply", side_effect=lambda r, c: r), \
             mock.patch.object(
                 self.tasks.process_incoming_message, "apply_async"
             ) as apply_async:
            self.tasks.process_incoming_message(
                self.store.id, "whatsapp", sender, "عايز عطر", deferrals=deferrals
            )
        return apply_async

    def test_a_normal_message_is_processed(self):
        apply_async = self._run()

        self.assertFalse(apply_async.called, "a first message must not be deferred")
        self.assertEqual(Message.objects.filter(role="user").count(), 1)

    def test_a_sender_past_the_minute_limit_is_deferred_not_dropped(self):
        for _ in range(self.tasks.SENDER_PER_MINUTE):
            self._run()

        apply_async = self._run()

        self.assertTrue(apply_async.called, "the message was dropped instead of deferred")
        self.assertGreater(apply_async.call_args.kwargs["countdown"], 0)
        self.assertEqual(apply_async.call_args.kwargs["kwargs"], {"deferrals": 1})

    def test_a_deferred_message_saves_nothing_so_no_half_turn_is_left_behind(self):
        for _ in range(self.tasks.SENDER_PER_MINUTE):
            self._run()
        before = Message.objects.count()

        self._run()

        self.assertEqual(Message.objects.count(), before)

    def test_deferral_stops_at_the_cap(self):
        """Unbounded requeueing turns a flood into an unbounded queue, which is shared with
        every other store's live conversations."""
        for _ in range(self.tasks.SENDER_PER_MINUTE):
            self._run()

        apply_async = self._run(deferrals=self.tasks.MAX_DEFERRALS)

        self.assertFalse(apply_async.called, "deferred past the cap instead of dropping")

    def test_a_retry_is_not_charged_a_second_time(self):
        """Counting on every retry would push a sender further past the limit by the
        system's own back-pressure rather than by anything they sent."""
        from django.core.cache import cache

        for _ in range(self.tasks.SENDER_PER_MINUTE):
            self._run()
        key = f"ratelimit:sender:{self.store.id}:whatsapp:2010"
        before = cache.get(key)

        self._run(deferrals=1)

        self.assertEqual(cache.get(key), before)

    def test_deferrals_are_staggered_so_replies_keep_their_order(self):
        """Without a stagger, every message a sender queued in one window is re-dispatched to
        the same instant and they race — so the saved history can misrepresent the
        conversation and poison the next turn's context."""
        for _ in range(self.tasks.SENDER_PER_MINUTE):
            self._run()

        first = self._run(deferrals=1).call_args.kwargs["countdown"]
        second = self._run(deferrals=2).call_args.kwargs["countdown"]

        self.assertGreater(second, first)

    def test_one_sender_flooding_does_not_block_another(self):
        for _ in range(self.tasks.SENDER_PER_MINUTE):
            self._run(sender="2010")

        apply_async = self._run(sender="2099")

        self.assertFalse(apply_async.called, "a different sender was caught by the limit")

    def test_the_store_ceiling_is_higher_than_one_senders(self):
        """The per-store bucket exists for cross-tenant isolation, not to cap a customer."""
        self.assertGreater(self.tasks.STORE_PER_MINUTE, self.tasks.SENDER_PER_MINUTE)


class AlreadyDescribedTests(TestCase):
    """conv_990: the bot described Dior Homme Intense four times in six turns.

    Three of those replies opened with the identical "تمام جداً، أرشحلك Dior Homme Intense
    لأنه ثابت وفوحانه متوسط", and two of the turns were the customer merely *answering a
    question the bot had asked*. Nothing tracked what the customer had already been told.
    """

    def setUp(self):
        from products.services.sales import described

        self.described = described
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        for name in ("Dior Homme Intense", "Le Male"):
            Product.objects.create(
                store=self.store, brand=self.brand, name=name, gender="male",
                longevity="8 hours", projection="Moderate",
            )
        self.history = [
            {"role": "user", "content": "عايز برفان ثابت وفواح"},
            {"role": "assistant", "content": (
                "تمام جداً، أرشحلك Dior Homme Intense لأنه ثابت وفوحانه متوسط. "
                "العطر لراجل ولا لست؟"
            )},
        ]

    def test_a_perfume_the_bot_named_counts_as_described(self):
        self.assertIn(
            "Dior Homme Intense",
            self.described.already_described(self.history, self.store),
        )

    def test_a_perfume_only_the_customer_named_does_not(self):
        """They may be asking about it for the first time — we have told them nothing."""
        history = [{"role": "user", "content": "عندكم Le Male؟"}]

        self.assertEqual(
            self.described.already_described(history, self.store), frozenset()
        )

    def test_an_empty_conversation_describes_nothing(self):
        self.assertEqual(self.described.already_described([], self.store), frozenset())

    def test_answering_our_question_is_a_follow_up(self):
        """The two turns from the transcript that produced a full re-pitch."""
        seen = self.described.already_described(self.history, self.store)

        for message in ("راجل", "بالنهار اكتر"):
            with self.subTest(message=message):
                self.assertTrue(
                    self.described.is_follow_up(message, self.history, seen)
                )

    def test_asking_us_to_choose_is_a_follow_up(self):
        seen = self.described.already_described(self.history, self.store)

        self.assertTrue(
            self.described.is_follow_up("ترشحلي انهي احسن منهم", self.history, seen)
        )

    def test_a_fresh_request_is_not_a_follow_up(self):
        """A customer stating new criteria wants the detail, however much we have said."""
        seen = self.described.already_described(self.history, self.store)

        self.assertFalse(
            self.described.is_follow_up(
                "عايز عطر حريمي مسكر للصيف وميزانيتي 700 ومش تقيل",
                self.history, seen,
            )
        )

    def test_the_first_recommendation_can_never_be_shortened(self):
        """Requiring something already described is what protects turn one."""
        self.assertFalse(
            self.described.is_follow_up("راجل", self.history, frozenset())
        )

    def test_a_short_message_is_only_an_answer_if_we_asked_something(self):
        seen = self.described.already_described(self.history, self.store)
        no_question = [
            {"role": "assistant", "content": "أرشحلك Dior Homme Intense."},
        ]

        self.assertFalse(self.described.is_follow_up("راجل", no_question, seen))

    def test_the_hint_bans_re_describing_and_names_what_was_said(self):
        seen = self.described.already_described(self.history, self.store)

        hint = self.described.repeat_ban_hint(seen, follow_up=True)

        self.assertIn("Dior Homme Intense", hint)
        self.assertIn("ممنوع تعيد وصف", hint)
        self.assertIn("سطر واحد قصير", hint)

    def test_nothing_described_produces_no_hint(self):
        self.assertEqual(self.described.repeat_ban_hint(frozenset(), True), "")

    def test_a_non_follow_up_still_gets_the_no_repeat_ban(self):
        """Even a fresh request should not re-recite specs the customer already heard; it
        just is not held to the one-line shape."""
        seen = self.described.already_described(self.history, self.store)

        hint = self.described.repeat_ban_hint(seen, follow_up=False)

        self.assertIn("ممنوع تعيد وصف", hint)
        self.assertNotIn("سطر واحد قصير", hint)


class ContradictedFieldWarningTests(TestCase):
    """A populated-but-different occasion or season must be flagged, not passed over.

    conv_990 turn 8: the customer said "بالنهار اكتر" and the bot called Dior Homme Intense
    "مناسبة للنهار", two turns after correctly describing it as an evening scent. The row says
    occasion='Evening/Formal'. It could because a miss on occasion produced *nothing* — the
    model saw a ✅ line pulling one way and no counter-signal.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")

    def _make(self, **fields):
        return Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Homme Intense",
            gender="male", **fields,
        )

    def test_a_contradicted_occasion_is_flagged_with_the_recorded_value(self):
        product = self._make(occasion="Evening/Formal")

        entry = sales_ranking.rank([product], {"occasion": "daily"})[0]

        self.assertTrue(entry.mismatches)
        self.assertIn("Evening/Formal", entry.mismatches[0])
        self.assertNotIn("مناسب للمناسبة اللي قالها", entry.reasons)

    def test_a_contradicted_season_is_flagged_too(self):
        product = self._make(season="Fall/Winter")

        entry = sales_ranking.rank([product], {"season": "summer"})[0]

        self.assertTrue(entry.mismatches)
        self.assertIn("Fall/Winter", entry.mismatches[0])

    def test_a_matching_occasion_still_earns_its_reason(self):
        product = self._make(occasion="Daily, Office, Dates")

        entry = sales_ranking.rank([product], {"occasion": "daily"})[0]

        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)
        self.assertFalse(entry.mismatches)

    def test_a_blank_field_is_unknown_not_a_mismatch(self):
        """Treating empty as "different" is what made the old icontains AND-filter delete
        every sparsely-filled product the moment a customer named an occasion."""
        product = self._make(occasion="")

        entry = sales_ranking.rank([product], {"occasion": "daily"})[0]

        self.assertFalse(entry.mismatches)
        self.assertFalse(entry.reasons)

    def test_the_warning_reaches_the_prompt(self):
        product = self._make(occasion="Evening/Formal")
        entry = sales_ranking.rank([product], {"occasion": "daily"})[0]

        self.assertIn("Evening/Formal", sales_ranking.reasons_note(entry))

    def test_a_daytime_request_now_ranks_daytime_perfumes_first(self):
        """Side effect worth pinning: with a miss scoring nothing, an evening perfume and a
        daily one used to tie on this axis."""
        evening = self._make(occasion="Evening/Formal")
        daily = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage",
            gender="male", occasion="Daily, Office, Dates",
        )

        ranked = sales_ranking.rank([evening, daily], {"occasion": "daily"})

        self.assertEqual(ranked[0].product.name, "Dior Sauvage")


class UnsetPreferenceIsNotPersistedTests(TestCase):
    """`wants_uncommon: false` is the extractor reporting the absence of a preference."""

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.conversation = Conversation.objects.create(store=self.store)

    def test_a_false_flag_is_not_saved_as_a_preference(self):
        merge_preferences(self.conversation, {"gender": "male", "wants_uncommon": False})

        self.conversation.refresh_from_db()
        self.assertNotIn("wants_uncommon", self.conversation.preferences)
        self.assertEqual(self.conversation.preferences.get("gender"), "male")

    def test_a_true_flag_still_is(self):
        merge_preferences(self.conversation, {"wants_uncommon": True})

        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.preferences.get("wants_uncommon"))


class SuppressedReasonsKeepTheirWarningTests(TestCase):
    """Suppressing the recital must not suppress the safety half of the same line.

    The ✅ reasons and the ⚠️ mismatches share one rendered line but have opposite lifetimes:
    the reasons are the selling justification, so re-sending them every turn is what made the
    bot recite "ثابت وفوحانه متوسط" four times — while the mismatches are a warning that
    never goes stale. Dropping the whole line for an already-described perfume took the
    warning with it, and the reply then offered an Evening/Formal perfume as "أنسب للنهار في
    المكتب" on the very turn the warning had fired.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Homme Intense",
            gender="male", occasion="Evening/Formal", longevity="8 hours",
        )
        ProductVariant.objects.create(
            product=self.product, volume=50, price=550, bottle_type="normal"
        )
        self.entry = sales_ranking.rank(
            [self.product], {"occasion": "daily", "longevity": "long-lasting"}
        )[0]

    def test_the_full_note_carries_both_halves(self):
        note = sales_ranking.reasons_note(self.entry)

        self.assertIn("✅ ليه مناسب", note)
        self.assertIn("⚠️ مش مطابق في", note)

    def test_mismatches_only_drops_the_selling_half(self):
        note = sales_ranking.reasons_note(self.entry, mismatches_only=True)

        self.assertNotIn("✅ ليه مناسب", note)
        self.assertIn("Evening/Formal", note)

    def test_an_already_described_perfume_keeps_its_warning_in_the_prompt(self):
        """Brief mode omits the Occasion field, so this line is the only place the recorded
        value still appears."""
        context = _format_products(
            Product.objects.filter(pk=self.product.pk),
            ranked={self.product.id: self.entry},
            brief_for={self.product.name},
        )

        self.assertIn("Evening/Formal", context)
        self.assertNotIn("✅ ليه مناسب", context)

    def test_a_first_mention_still_gets_the_full_evidence_line(self):
        context = _format_products(
            Product.objects.filter(pk=self.product.pk),
            ranked={self.product.id: self.entry},
            brief_for=frozenset(),
        )

        self.assertIn("✅ ليه مناسب", context)
        self.assertIn("Longevity: 8 hours", context)


class FieldVocabularyTests(TestCase):
    """A vocabulary gap must not become a false accusation.

    Now that a miss produces a *warning* rather than silence, any mismatch between the
    extractor's vocabulary and the store's own typing mislabels a suitable perfume. "بالنهار"
    is extracted as `daily` while the catalogue types `Casual`, so Le Male — genuinely a
    daytime perfume — was flagged "مناسبته المسجلة Casual، مش اللي قاله" and dropped.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")

    def _entry(self, intent, **fields):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name="Test", gender="male", **fields
        )
        return sales_ranking.rank([product], intent)[0]

    def test_casual_satisfies_a_daytime_request(self):
        entry = self._entry({"occasion": "daily"}, occasion="Casual")

        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)
        self.assertFalse(entry.mismatches)

    def test_a_register_request_never_contradicts_a_time_of_day(self):
        """"formal" says nothing about when a perfume is worn, so an Office-tagged scent is
        neither confirmed nor contradicted by it. Silence is the honest verdict — the same
        principle as a blank field: unknown is not a mismatch."""
        entry = self._entry({"occasion": "formal"}, occasion="Office, Daily")

        self.assertFalse(entry.mismatches)
        self.assertFalse(entry.reasons)

    def test_an_evening_perfume_is_still_flagged_for_a_daytime_request(self):
        """The synonyms must not be so loose that nothing is ever a mismatch."""
        entry = self._entry({"occasion": "daily"}, occasion="Evening/Formal")

        self.assertTrue(entry.mismatches)
        self.assertIn("Evening/Formal", entry.mismatches[0])

    def test_all_seasons_satisfies_any_season(self):
        """search_service already ORs season__icontains="All Seasons" into its filter;
        ranking has to agree or a year-round perfume is warned about for every season."""
        for season in ("summer", "winter", "spring"):
            with self.subTest(season=season):
                entry = self._entry({"season": season}, season="All Seasons")
                self.assertFalse(entry.mismatches)
                self.assertIn("مناسب للموسم", entry.reasons)

    def test_a_wrong_season_is_still_flagged(self):
        entry = self._entry({"season": "summer"}, season="Fall/Winter")

        self.assertTrue(entry.mismatches)
        self.assertIn("Fall/Winter", entry.mismatches[0])

    def test_an_unmapped_occasion_falls_back_to_a_substring_test(self):
        entry = self._entry({"occasion": "للجيم"}, occasion="للجيم والرياضة")

        self.assertIn("مناسب للمناسبة اللي قالها", entry.reasons)

    def test_an_evening_perfume_fails_an_office_request(self):
        """The bug this table nearly caused. `office` was mapped to `formal`, and because
        "Evening/Formal" contains "formal" an evening perfume passed an office request and was
        sold as "مناسب للنهار" — exactly what the mismatch warning exists to stop. Formality is
        a register, not a time of day."""
        entry = self._entry({"occasion": "office"}, occasion="Evening/Formal")

        self.assertTrue(entry.mismatches)
        self.assertIn("Evening/Formal", entry.mismatches[0])

    def test_an_office_perfume_passes_an_office_request(self):
        entry = self._entry({"occasion": "office"}, occasion="Casual/Office/Everyday")

        self.assertFalse(entry.mismatches)

    def test_evening_and_daytime_never_satisfy_each_other(self):
        for wanted, recorded in (
            ("daily", "Evening"),
            ("office", "Evening"),
            ("evening", "Daily, Office, Dates"),
            ("night", "Casual"),
        ):
            with self.subTest(wanted=wanted, recorded=recorded):
                self.assertTrue(self._entry({"occasion": wanted}, occasion=recorded).mismatches)


class ConversationContinuityTests(TestCase):
    """Conversation 997: four different pairs of perfumes across seven turns, eight in total,
    with the customer never once rejecting anything.

    The cause was that `search_products` re-derived its shortlist from scratch every turn. On
    the turn the customer said "معايا 800", Le Male — whose 50ml is 623, and which they had
    been converging on for two turns — survived every hard filter and sat at rank 3. The
    prompt asks for the best 1-2, so it silently vanished, leaving two persona rules in
    conflict with the data deciding which won: "لو أبدى اهتمام بواحد — خليه الأساس" against
    "المنتج اللي مش في البيانات = مش موجود عندنا".
    """

    def setUp(self):
        from products.services.sales import described

        self.described = described
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.cheap = self._make("Le Male", price=623, longevity="7 hours")
        self.rival = self._make("Dior Sauvage", price=400, longevity="8 hours")
        self.pricey = self._make("Green Irish Tweed", price=3300, longevity="7 hours")
        self.conversation = Conversation.objects.create(store=self.store)

    def _make(self, name, price, **fields):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="male",
            projection="Moderate", **fields,
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=price, bottle_type="normal"
        )
        return product

    def _reply(self, text, shown=()):
        """One exchange. `shown` names the perfumes whose data was injected that turn."""
        Message.objects.create(
            conversation=self.conversation, role="user", content="تمام"
        )
        context = "".join(
            f"Name (الاسم الصحيح): {name}" + "\n" for name in shown
        )
        Message.objects.create(
            conversation=self.conversation, role="assistant",
            content=text, internal_context=context,
        )

    def _order_reply(self, text, cart=()):
        """One exchange as the ORDER flow saves it, whose internal_context is a cart rather
        than injected product data. `cart` is (name, volume, quantity, price) tuples, rendered
        in the exact shape order_service.py:732 writes."""
        Message.objects.create(
            conversation=self.conversation, role="user", content="تمام"
        )
        context = ", ".join(
            f"{name} ({volume} ملي) (زجاجة البراند) x {qty} ({price} EGP)"
            for name, volume, qty, price in cart
        ) or "No products found"
        Message.objects.create(
            conversation=self.conversation, role="assistant",
            content=text, internal_context=context,
        )

    # ── what the conversation is on ──────────────────────────────────────
    def test_perfumes_shown_and_named_are_under_discussion(self):
        self._reply(
            "أرشحلك Le Male وGreen Irish Tweed.",
            shown=("Le Male", "Green Irish Tweed"),
        )

        self.assertEqual(
            self.described.under_discussion(self.conversation, self.store),
            {"Le Male", "Green Irish Tweed"},
        )

    def test_a_perfume_in_the_data_but_never_mentioned_is_not_under_discussion(self):
        """The injected block holds up to twelve products; most are never put to the
        customer, so the data alone is not what the conversation is on."""
        self._reply("أرشحلك Le Male.", shown=("Le Male", "Dior Sauvage"))

        self.assertNotIn(
            "Dior Sauvage",
            self.described.under_discussion(self.conversation, self.store),
        )

    def test_a_perfume_named_only_while_being_withdrawn_is_not_under_discussion(self):
        """Conversation 1012: announcing "Ambero خرج من الاختيارات" put Ambero in the reply, so
        the next turn found it under discussion again, found it still failing the customer's
        constraints, and announced the same withdrawal a second time. A dropped perfume is
        absent from the injected data by definition, which is what breaks the loop."""
        self._reply(
            "أرشحلك Le Male. أما Green Irish Tweed خرج من الاختيارات.",
            shown=("Le Male",),
        )

        under = self.described.under_discussion(self.conversation, self.store)

        self.assertIn("Le Male", under)
        self.assertNotIn("Green Irish Tweed", under)

    def test_older_replies_fall_out_of_the_window(self):
        """Two replies back, so a conversation is not pinned to perfumes it has moved past."""
        self._reply("أرشحلك Dior Sauvage.", shown=("Dior Sauvage",))
        self._reply("أرشحلك Le Male.", shown=("Le Male",))
        self._reply("الـ50 بـ 623 جنيه.", shown=("Le Male",))

        self.assertNotIn(
            "Dior Sauvage",
            self.described.under_discussion(self.conversation, self.store),
        )

    def test_a_perfume_only_the_customer_named_is_not_under_discussion(self):
        Message.objects.create(
            conversation=self.conversation, role="user", content="عندكم Le Male؟"
        )

        self.assertEqual(
            self.described.under_discussion(self.conversation, self.store), frozenset()
        )

    # ── an order turn's context is a cart, not product data ──────────────
    def test_the_cart_shape_is_told_apart_from_product_data(self):
        """Matched positively, so neither product-data label shape nor an empty context takes
        the order branch. The label itself is not a usable discriminator: format_products
        emits "Name (الاسم الصحيح):" while older rows and fixtures carry a bare "Name:"."""
        self.assertTrue(
            self.described._is_cart_context(
                "Le Male (50 ملي) (زجاجة البراند) x 1 (623.00 EGP)"
            )
        )
        self.assertTrue(self.described._is_cart_context("No products found"))
        self.assertFalse(self.described._is_cart_context("Name (الاسم الصحيح): Le Male"))
        self.assertFalse(self.described._is_cart_context("Name: Le Male"))
        self.assertFalse(self.described._is_cart_context(""))
        self.assertFalse(self.described._is_cart_context(None))

    def test_a_perfume_the_order_flow_is_asking_about_is_under_discussion(self):
        """Conversation 726. The order flow omits a perfume from its cart context while it
        waits for a bottle type (order_service.py:621-624), so the perfume it was *asking
        about* scored zero and was subtracted as withdrawn, leaving a cart-resident Le Male as
        the only thing under discussion. The customer was then told four times that perfumes
        sitting in stock, in both bottle types, were not in the data."""
        self._order_reply(
            "الطلب لحد دلوقتي: 1 × Le Male. "
            "محتاج أعرف نوع الزجاجة (أوريجينال أم زجاجة البراند؟) من عطر Dior Sauvage.",
            cart=(("Le Male", 50, 1, "623.00"),),
        )

        under = self.described.under_discussion(self.conversation, self.store)

        self.assertIn("Dior Sauvage", under)
        self.assertIn("Le Male", under)

    def test_a_cart_perfume_absent_from_the_latest_reply_does_not_outrank_it(self):
        """`latest_only` is what keeps the stale line out: Le Male stays in the cart across the
        turn, but the reply the customer is answering is about Dior Sauvage."""
        self._order_reply(
            "الطلب لحد دلوقتي: 1 × Le Male.", cart=(("Le Male", 50, 1, "623.00"),)
        )
        self._order_reply(
            "محتاج أعرف نوع الزجاجة من عطر Dior Sauvage.",
            cart=(("Le Male", 50, 1, "623.00"),),
        )

        self.assertEqual(
            self.described.offered_in_order(
                self.conversation, self.store, latest_only=True
            ),
            ["Dior Sauvage"],
        )
        # The default still sorts the older turn after everything we just said.
        self.assertEqual(
            self.described.offered_in_order(self.conversation, self.store),
            ["Dior Sauvage", "Le Male"],
        )

    def test_latest_only_falls_back_when_the_latest_reply_named_nothing(self):
        """An FAQ or shipping reply names no perfume, so the older turn is the best anchor
        available — returning nothing would drop the referent entirely."""
        self._reply("أرشحلك Le Male.", shown=("Le Male",))
        self._reply("الشحن 60 جنيه لكل المحافظات.", shown=())

        self.assertEqual(
            self.described.offered_in_order(
                self.conversation, self.store, latest_only=True
            ),
            ["Le Male"],
        )

    def test_a_deferral_is_not_a_withdrawal(self):
        """"لحظة أتأكدلك منه" is what persona rule 3 asks for when a named perfume is missing
        from the injected data — correct output that looked identical to a withdrawal, so the
        perfume the customer was waiting to hear about dropped out of the next turn."""
        self._reply("أرشحلك Le Male.", shown=("Le Male",))
        self._reply("عطر Dior Sauvage لحظة أتأكدلك منه وأرد عليك.", shown=())

        under = self.described.under_discussion(self.conversation, self.store)

        self.assertIn("Dior Sauvage", under)
        self.assertIn("Le Male", under)

    def test_a_deferral_elsewhere_does_not_rescue_a_withdrawal(self):
        """Clause-scoped, so a reply that drops one perfume and defers on another is read
        correctly on both counts — otherwise conversation 1012's repeat-withdrawal loop
        comes back."""
        self._reply("أرشحلك Le Male.", shown=("Le Male",))
        self._reply(
            "Green Irish Tweed خرج من الاختيارات. عطر Dior Sauvage هسأل وأرد عليك.",
            shown=(),
        )

        under = self.described.under_discussion(self.conversation, self.store)

        self.assertIn("Dior Sauvage", under)
        self.assertNotIn("Green Irish Tweed", under)

    def test_an_out_of_stock_announcement_still_withdraws(self):
        """order_service's stock returns save an empty context, so the perfume they name has to
        stay withdrawn. The cart branch must not swallow that case."""
        self._reply("أرشحلك Le Male.", shown=("Le Male",))
        Message.objects.create(
            conversation=self.conversation, role="assistant",
            content="للأسف عطر Dior Sauvage نفد من المخزون بجميع أحجامه حالياً 😔",
            internal_context="",
        )

        under = self.described.under_discussion(self.conversation, self.store)

        self.assertIn("Le Male", under)
        self.assertNotIn("Dior Sauvage", under)

    # ── the ranking signal ───────────────────────────────────────────────
    def test_continuity_lifts_a_perfume_that_previously_lost(self):
        """The turn-7 failure, as arithmetic."""
        intent = {"gender": "male", "longevity": "long-lasting", "max_price": 800}

        without = sales_ranking.rank([self.rival, self.cheap], intent)
        with_keep = sales_ranking.rank([self.rival, self.cheap], intent, keep={"Le Male"})

        self.assertEqual(without[0].product.name, "Dior Sauvage")
        self.assertEqual(with_keep[0].product.name, "Le Male")

    def test_continuity_cannot_outrank_a_similarity_match(self):
        """An explicit "عايز حاجة شبه X" must still be able to move the conversation."""
        self.assertLess(
            sales_ranking.WEIGHTS["continuity"], sales_ranking.WEIGHTS["similarity"]
        )

    def test_continuity_cannot_rescue_an_excluded_scent(self):
        """A real exclusion stays authoritative: the avoid penalty outweighs the boost."""
        self.cheap.base_notes = "Oud"
        self.cheap.save()

        ranked = sales_ranking.rank(
            [self.rival, self.cheap],
            {"gender": "male", "avoid_notes": ["oud"]},
            keep={"Le Male"},
        )

        self.assertEqual(ranked[0].product.name, "Dior Sauvage")

    def test_continuity_outweighs_the_slots_a_conversation_narrows_on(self):
        """Budget, occasion, season, longevity and projection are refinements, so a customer
        adding one must not displace the perfume they were converging on."""
        for key in ("budget", "occasion", "longevity", "projection", "season"):
            with self.subTest(slot=key):
                self.assertGreater(
                    sales_ranking.WEIGHTS["continuity"], sales_ranking.WEIGHTS[key]
                )

    def test_the_reason_line_says_why_it_was_kept(self):
        ranked = sales_ranking.rank([self.cheap], {"gender": "male"}, keep={"Le Male"})

        self.assertIn("العميل بيتكلم عنه بالفعل", ranked[0].reasons)

    # ── search_products threading ────────────────────────────────────────
    def test_a_kept_perfume_leads_the_shortlist(self):
        results = search_products(
            {"gender": "male", "longevity": "long-lasting", "max_price": 800},
            store=self.store, keep={"Le Male"},
        )

        self.assertEqual([p.name for p in results["products"]][0], "Le Male")
        self.assertEqual(results["keeping"], ["Le Male"])

    def test_a_new_budget_reports_what_it_ruled_out(self):
        """Silently swapping a perfume out is what made the conversation read as random."""
        results = search_products(
            {"gender": "male", "max_price": 800}, store=self.store,
            keep={"Le Male", "Green Irish Tweed"},
        )

        self.assertEqual(results["keeping"], ["Le Male"])
        self.assertIn("Green Irish Tweed", results["dropped"])
        self.assertIn("3300", results["dropped"]["Green Irish Tweed"])

    def test_a_note_request_does_not_evict_a_discussed_perfume(self):
        """Notes narrow the search; they are not a reason to drop what is being discussed."""
        results = search_products(
            {"gender": "male", "notes": ["oud"]}, store=self.store, keep={"Le Male"},
        )

        self.assertEqual(results["keeping"], ["Le Male"])
        self.assertEqual(results["dropped"], {})

    def test_an_expensive_kept_perfume_still_reaches_the_scorer(self):
        """The candidate cap is ordered cheapest-first, so a costly perfume under discussion
        could fall outside it — and the boost cannot lift what the scorer never sees."""
        results = search_products(
            {"gender": "male"}, store=self.store, keep={"Green Irish Tweed"},
        )

        self.assertIn("Green Irish Tweed", [p.name for p in results["products"]])

    # ── the prompt half ──────────────────────────────────────────────────
    def test_the_note_tells_the_model_to_stay_put(self):
        note = self.described.continuity_note(["Le Male"], [], converge=False)

        self.assertIn("Le Male", note)
        self.assertIn("ممنوع تقلب على عطور جديدة", note)

    def test_converging_demands_exactly_one_perfume(self):
        """The ranking boost puts the right perfume in front of the model; nothing stops it
        presenting a fresh pair alongside. This is the clause that does."""
        note = self.described.continuity_note(["Le Male"], [], converge=True)

        self.assertIn("واحد", note)
        self.assertIn("ممنوع تعرض عليه اتنين", note)

    def test_not_converging_leaves_the_pair_allowed(self):
        note = self.described.continuity_note(["Le Male"], [], converge=False)

        self.assertNotIn("ممنوع تعرض عليه اتنين", note)

    def test_the_note_names_what_dropped_out(self):
        note = self.described.continuity_note(
            ["Le Male"],
            {"Green Irish Tweed": "أرخص حجم فيه 3300 جنيه، فوق ميزانيته"},
            converge=True,
        )

        self.assertIn("Green Irish Tweed", note)
        self.assertIn("خرجت من شروطه", note)
        self.assertIn("3300", note)
        self.assertIn("ممنوع تخترع سبب", note)

    def test_nothing_under_discussion_produces_no_note(self):
        self.assertEqual(self.described.continuity_note([], {}, converge=True), "")

    def test_the_drop_reason_is_computed_not_guessed(self):
        """Turn 2 of conversation 997 said Jasmino "خرج لأنه سعره أعلى" — it was dropped on
        gender, and no budget existed anywhere in the conversation. The note had offered
        "(السعر مثلاً)" as the reason to give, and the model took the example as the answer."""
        female = Product.objects.create(
            store=self.store, brand=self.brand, name="Jasmino", gender="female",
        )
        ProductVariant.objects.create(
            product=female, volume=50, price=578, bottle_type="normal"
        )

        results = search_products(
            {"gender": "male"}, store=self.store, keep={"Le Male", "Jasmino"},
        )

        self.assertIn("النوع", results["dropped"]["Jasmino"])
        self.assertNotIn("ميزاني", results["dropped"]["Jasmino"])

    def test_an_unnameable_cause_yields_no_invented_reason(self):
        note = self.described.continuity_note(["Le Male"], {"Green Irish Tweed": None})

        self.assertIn("Green Irish Tweed", note)
        self.assertIn("ممنوع تخترع سبب", note)

    def test_an_affordable_perfume_is_never_blamed_on_price(self):
        """The bug this fix reproduced one layer down: the budget branch had a fallback that
        fired when the perfume *was* affordable, so one excluded on season was reported as
        "مفيش منه حجم داخل ميزانيته" while its 50ml sat at 550 against an 800 budget."""
        winter = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Homme Intense",
            gender="male", season="Fall/Winter",
        )
        ProductVariant.objects.create(
            product=winter, volume=50, price=550, bottle_type="normal"
        )

        results = search_products(
            {"gender": "male", "season": "summer", "max_price": 800},
            store=self.store, keep={"Dior Homme Intense"},
        )

        reason = results["dropped"]["Dior Homme Intense"]
        self.assertIn("الموسم", reason)
        self.assertNotIn("ميزاني", reason)

    def test_price_is_blamed_only_when_it_is_the_blocker(self):
        results = search_products(
            {"gender": "male", "max_price": 800},
            store=self.store, keep={"Green Irish Tweed"},
        )

        self.assertIn("3300", results["dropped"]["Green Irish Tweed"])
    def test_a_withdrawal_is_announced_only_once(self):
        """Conversation 1012 told the customer Ambero was out on two consecutive turns.

        The `internal_context` intersection stops the *announcement* reply from counting, but
        the earlier reply that recommended the perfume is still inside the two-reply window —
        so it stayed under discussion, still failed the constraints, and was announced again.
        A withdrawal in the most recent reply has to settle the matter.
        """
        # Turn 1: both recommended, both backed by data.
        self._reply(
            "أرشحلك Le Male وGreen Irish Tweed.",
            shown=("Le Male", "Green Irish Tweed"),
        )
        first = self.described.under_discussion(self.conversation, self.store)

        # Turn 2: one is withdrawn — named in prose, absent from the data.
        self._reply(
            "أرشحلك Le Male. أما Green Irish Tweed خرج من الاختيارات.",
            shown=("Le Male",),
        )
        second = self.described.under_discussion(self.conversation, self.store)

        self.assertIn("Green Irish Tweed", first, "it was on the table on the first turn")
        self.assertNotIn(
            "Green Irish Tweed", second,
            "still under discussion, so the same withdrawal is announced twice",
        )
        self.assertIn("Le Male", second, "the kept perfume must survive the window")

    def test_a_withdrawal_two_replies_back_does_not_resurface(self):
        self._reply("أرشحلك Le Male وGreen Irish Tweed.", shown=("Le Male", "Green Irish Tweed"))
        self._reply("Green Irish Tweed خرج من الاختيارات.", shown=("Le Male",))
        self._reply("Le Male الـ50 بـ 623 جنيه.", shown=("Le Male",))

        self.assertNotIn(
            "Green Irish Tweed",
            self.described.under_discussion(self.conversation, self.store),
        )




class OrderEditNotCancelTests(TestCase):
    """Conversation 1005: "مش عايز 1 × Noirvel (90ml)" wiped a two-item cart.

    The customer was removing one line of two. They got "تم إلغاء الطلب اللي كنا بنجهزه", lost
    their name, phone and address with it, and retyped everything to order the one perfume they
    had wanted from the start.

    The capability was already there — handle_order's extractor rule 5 drops the named perfume
    and keeps the rest, and `cart_cleared` exists for the genuinely-empty case. The message
    never arrived: "مش عايز" was a listed example of order_cancel, and the branch cleared the
    cart whenever it held any items.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Jean Paul Gaultier")
        self.le_male = self._make("Le Male", 856)
        self.noirvel = self._make("Noirvel", 1085)
        self.conversation = Conversation.objects.create(store=self.store)

    def _make(self, name, price):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="male",
        )
        return ProductVariant.objects.create(
            product=product, volume=90, price=price, bottle_type="normal"
        )

    def _cart(self, *variants, **details):
        cart = Cart.objects.create(
            conversation=self.conversation,
            customer_name=details.get("name", "محمد فؤاد"),
            customer_phone=details.get("phone", "01153032052"),
            secondary_phone=details.get("secondary", "01051089101"),
            shipping_address=details.get("address", "المقطم شارع 9"),
        )
        for variant in variants:
            CartItem.objects.create(
                cart=cart, variant=variant, quantity=1, bottle_type="normal"
            )
        return cart

    def _route(self, message):
        with mock.patch(
            "products.services.router.classify", return_value="order_cancel"
        ), mock.patch(
            "products.services.router.handle_order", return_value=("edited", "")
        ) as handle_order:
            from products.services.router import route

            reply, _ = route(message, [], self.store, self.conversation)
        return reply, handle_order

    # ── naming.mentioned_in ──────────────────────────────────────────────
    def test_a_summary_line_resolves_to_its_perfume(self):
        from products.services.sales import naming

        found = naming.mentioned_in(
            "مش عايز 1 × Noirvel (90ml)",
            [self.le_male.product, self.noirvel.product],
        )

        self.assertEqual([p.name for p in found], ["Noirvel"])

    def test_a_blanket_cancellation_names_nothing(self):
        from products.services.sales import naming

        for message in ("الغي الاوردر", "مش عايز خلاص", "بلاش الطلب كله"):
            with self.subTest(message=message):
                self.assertEqual(
                    naming.mentioned_in(
                        message, [self.le_male.product, self.noirvel.product]
                    ),
                    [],
                )

    def test_a_partial_name_is_not_a_match(self):
        from products.services.sales import naming

        self.assertEqual(
            naming.mentioned_in("مش عايز Le", [self.le_male.product]), []
        )

    # ── the router guard ─────────────────────────────────────────────────
    def test_naming_one_of_two_items_edits_instead_of_cancelling(self):
        """The exact failure. Enforced in code, not left to the classifier, because a dropped
        prompt rule on this branch destroys a sale."""
        self._cart(self.le_male, self.noirvel)

        reply, handle_order = self._route("مش عايز 1 × Noirvel (90ml)")

        self.assertTrue(handle_order.called, "the removal was treated as a cancellation")
        self.assertEqual(reply, "edited")
        self.assertNotIn("إلغاء", reply)

    def test_the_cart_survives_a_line_removal(self):
        self._cart(self.le_male, self.noirvel)

        self._route("مش عايز 1 × Noirvel (90ml)")

        self.assertTrue(Cart.objects.filter(conversation=self.conversation).exists())

    def test_naming_the_only_item_still_cancels(self):
        """A one-item cart named in full genuinely is a cancellation, and the extractor's
        cart_cleared flag already covers that path."""
        self._cart(self.le_male)

        reply, handle_order = self._route("مش عايز Le Male")

        self.assertFalse(handle_order.called)
        self.assertIn("إلغاء", reply)

    def test_a_blanket_cancellation_still_cancels_a_multi_item_cart(self):
        self._cart(self.le_male, self.noirvel)

        reply, handle_order = self._route("الغي الاوردر")

        self.assertFalse(handle_order.called)
        self.assertIn("إلغاء", reply)
        self.assertFalse(
            CartItem.objects.filter(cart__conversation=self.conversation).exists()
        )

    # ── contact details survive a cancellation ───────────────────────────
    def test_cancelling_keeps_the_details_already_given(self):
        """They retyped name, phone and address in full because the row was deleted."""
        self._cart(self.le_male, self.noirvel)

        self._route("الغي الاوردر")

        cart = Cart.objects.get(conversation=self.conversation)
        self.assertEqual(cart.customer_name, "محمد فؤاد")
        self.assertEqual(cart.customer_phone, "01153032052")
        self.assertEqual(cart.shipping_address, "المقطم شارع 9")
        self.assertFalse(cart.items.exists())

    def test_cancelling_advances_the_summary_window(self):
        """The trap in keeping the details. `_summary_was_shown` scopes to
        created_at >= cart.created_at, so a surviving row would let a summary sent *before* the
        cancellation authorise a confirmation *after* it — an order with no total ever shown.
        Delete-and-recreate is what keeps that guard honest."""
        from products.services.order_service import clear_cart

        original = self._cart(self.le_male, self.noirvel)
        before = original.created_at

        clear_cart(self.conversation, keep_details=True)

        self.assertGreater(Cart.objects.get(conversation=self.conversation).created_at, before)

    def test_a_stale_summary_cannot_confirm_after_a_cancellation(self):
        from products.services.order_service import _summary_was_shown, clear_cart

        self._cart(self.le_male, self.noirvel)
        Message.objects.create(
            conversation=self.conversation, role="assistant",
            content="💰 الإجمالي: 1941.00 جنيه.",
        )
        clear_cart(self.conversation, keep_details=True)

        fresh = Cart.objects.get(conversation=self.conversation)

        self.assertFalse(_summary_was_shown(self.conversation, fresh))

    def test_a_completed_order_still_starts_the_next_one_empty(self):
        """create_order_in_db keeps the default: a cart that became an Order leaves nothing."""
        from products.services.order_service import clear_cart

        self._cart(self.le_male)

        clear_cart(self.conversation)

        self.assertFalse(Cart.objects.filter(conversation=self.conversation).exists())


class PerformanceLeadNoteTests(TestCase):
    """The lead recommendation must follow the ranking, and quote the recorded figure.

    Evaluation scenario M1, on the turn the customer said "بس اهم حاجه الثبات": the ranker put
    Ambero (10 hours) first and the reply recommended Dark Aura (8 hours), describing both as
    "وثباتهم كويس" without quoting either number. The ranking was right and the prose ignored it.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Perfamix Test")
        self.lead = self._make("Ambero", "10 hours", "Strong")
        self.other = self._make("Dark Aura", "8 hours", "Strong")

    def _make(self, name, longevity, projection):
        return Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="male",
            longevity=longevity, projection=projection,
        )

    def _note(self, intent):
        from products.services.ai.recommendation import _performance_note

        return _performance_note([self.lead, self.other], intent)

    def test_the_note_names_the_top_ranked_perfume(self):
        note = self._note({"longevity": "long-lasting"})

        self.assertIn("Ambero", note)
        self.assertIn("رشحه هو الأساس", note)

    def test_the_note_carries_the_recorded_figure(self):
        self.assertIn("10 hours", self._note({"longevity": "long-lasting"}))

    def test_vague_performance_claims_are_forbidden_by_name(self):
        self.assertIn("ثباته كويس", self._note({"longevity": "long-lasting"}))

    def test_projection_is_covered_too(self):
        note = self._note({"projection": "strong"})

        self.assertIn("الفوحان", note)
        self.assertIn("Strong", note)

    def test_both_axes_are_reported_together(self):
        note = self._note({"longevity": "long-lasting", "projection": "strong"})

        self.assertIn("10 hours", note)
        self.assertIn("Strong", note)

    def test_no_note_without_a_performance_request(self):
        """It must not appear on every turn — the persona already carries too many rules."""
        self.assertEqual(self._note({"gender": "male", "max_price": 700}), "")

    def test_an_unrecorded_figure_is_named_as_missing_not_invented(self):
        blank = self._make("Nofigure", "", "")

        from products.services.ai.recommendation import _performance_note

        note = _performance_note([blank], {"longevity": "long-lasting"})

        self.assertIn("غير مسجل", note)

    def test_an_empty_shortlist_is_safe(self):
        from products.services.ai.recommendation import _performance_note

        self.assertEqual(_performance_note([], {"longevity": "long-lasting"}), "")

    def test_the_note_reaches_the_recommendation_prompt(self):
        import inspect

        from products.services.ai import recommendation

        source = inspect.getsource(recommendation.recommend)

        self.assertIn("_performance_note(products, intent)", source)


class LongevityBandTieBreakTests(TestCase):
    """Longevity has to be able to discriminate, or declaring it a priority does nothing.

    similarity._ordinal maps every value from 7 to 12 hours onto 3, and 3 is exactly what
    "long-lasting" asks for. A real catalogue lives almost entirely inside that band, so nine
    of the eleven candidates in evaluation scenario M1 scored an identical 1.0 and the customer
    who had just said "بس اهم حاجه الثبات" was handed an 8-hour perfume over an 11-hour one.

    Raising WEIGHTS["longevity"] would not have helped: with the hit at 1.0 for everyone, any
    weight multiplies a constant and reorders nothing.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Perfamix Test")

    def _make(self, name, longevity):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name,
            gender="male", longevity=longevity,
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=500, bottle_type="normal"
        )
        return product

    INTENT = {"gender": "male", "longevity": "long-lasting"}

    def test_hours_are_parsed_off_the_free_text_field(self):
        from products.services.sales.similarity import peak_hours

        self.assertEqual(peak_hours("10 hours"), 10)
        self.assertEqual(peak_hours("8–10 hrs"), 10)
        self.assertEqual(peak_hours("6–8 hours"), 8)
        self.assertIsNone(peak_hours("Strong"))
        self.assertIsNone(peak_hours(""))
        self.assertIsNone(peak_hours(None))

    def test_the_ordinal_bands_are_unchanged(self):
        """_ordinal also drives _performance, whose stronger/lighter verdict appears in the
        similarity reason line for every comparison — re-banding it was not the fix."""
        from products.services.sales.similarity import _LONGEVITY_WORDS, _ordinal

        for text, expected in (
            ("2 hours", 1), ("5 hours", 2), ("8 hours", 3),
            ("12 hours", 3), ("48 hours", 4),
        ):
            with self.subTest(text=text):
                self.assertEqual(_ordinal(text, _LONGEVITY_WORDS), expected)

    def test_more_hours_ranks_higher_inside_the_same_band(self):
        eleven = self._make("Safrano", "11 hours")
        eight = self._make("Dark Aura", "8 hours")

        ranked = {e.product.name: e.score for e in sales_ranking.rank([eight, eleven], self.INTENT)}

        self.assertGreater(ranked["Safrano"], ranked["Dark Aura"])

    def test_the_tie_break_stays_below_a_single_weight_unit(self):
        """It orders within a tier. It must never overturn a real signal."""
        from products.services.sales.ranking import WEIGHTS, _within_band_bonus

        widest = _within_band_bonus("longevity", "12 hours", 1.0)

        self.assertLess(widest, WEIGHTS["longevity"])
        self.assertLess(widest, WEIGHTS["continuity"])

    def test_a_half_credit_match_earns_no_bonus(self):
        """Partial credit is reported as a shortfall; rewarding it would blur the distinction."""
        from products.services.sales.ranking import _within_band_bonus

        self.assertEqual(_within_band_bonus("longevity", "6 hours", 0.5), 0.0)

    def test_a_missing_longevity_earns_no_bonus(self):
        from products.services.sales.ranking import _within_band_bonus

        self.assertEqual(_within_band_bonus("longevity", "", 1.0), 0.0)
        self.assertEqual(_within_band_bonus("longevity", "Strong", 1.0), 0.0)

    def test_full_credit_is_still_full_credit(self):
        """The bonus is additive. A product whose only reason was a longevity match must keep
        it, or the ✅ evidence half of its note disappears."""
        product = self._make("Ambero", "10 hours")

        entry = sales_ranking.rank([product], self.INTENT)[0]

        self.assertTrue(any("بيطابق طلبه" in reason for reason in entry.reasons))
        self.assertGreaterEqual(entry.score, sales_ranking.WEIGHTS["longevity"])

    def test_the_recorded_hours_reach_the_evidence_line(self):
        """"وثباتهم كويس" with no number is what the evaluation caught. Give the model the
        figure so it can quote the row instead of asserting."""
        product = self._make("Ambero", "10 hours")

        entry = sales_ranking.rank([product], self.INTENT)[0]

        self.assertIn("10 hours", sales_ranking.reasons_note(entry))

    def test_continuity_still_outranks_the_tie_break(self):
        """A refinement must not displace the perfume the customer was converging on."""
        eleven = self._make("Safrano", "11 hours")
        eight = self._make("Dark Aura", "8 hours")

        ranked = {
            e.product.name: e.score
            for e in sales_ranking.rank([eight, eleven], self.INTENT, keep=("Dark Aura",))
        }

        self.assertGreater(ranked["Dark Aura"], ranked["Safrano"])


class DemonstrativeReferenceTests(TestCase):
    """"تمام هاخد ده" must resolve to the perfume we just led with.

    Evaluation scenario F1: after the bot recommended two perfumes, "تمام هاخد ده، وضيف كمان
    واحد للهدية" and then "خليه 90 ملي بدل الـ50" both produced the identical scripted reply
    "مش واضحلي عايز تطلب أنهي عطر". Two rules were in tension and both were obeyed: the
    extractor is told an ordinal means "the perfume you named in your previous reply", but the
    reply reached it only through a truncated history, and it is separately told to return []
    rather than pull products out of the history when the saved cart is empty.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Emporio Armani")
        self.lead = Product.objects.create(
            store=self.store, brand=self.brand,
            name="Stronger With You Intensely", gender="male",
        )
        self.second = Product.objects.create(
            store=self.store, brand=self.brand,
            name="Stronger With You", gender="male",
        )
        ProductVariant.objects.create(
            product=self.lead, volume=90, price=780, bottle_type="normal"
        )
        ProductVariant.objects.create(
            product=self.second, volume=90, price=700, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _recommended_both(self):
        """The reply and the data behind it, the way the router saves them."""
        save_message(
            self.conversation, "assistant",
            "أنصحك بـ Stronger With You Intensely بـ 780 جنيه. "
            "ولو تحب حاجة أخف، Stronger With You بـ 700 جنيه.",
            internal_context="Name: Stronger With You Intensely\nName: Stronger With You",
        )

    def test_the_offered_perfumes_are_returned_in_the_order_they_were_named(self):
        from products.services.sales import described

        self._recommended_both()

        offered = described.offered_in_order(self.conversation, self.store)

        self.assertEqual(
            offered, ["Stronger With You Intensely", "Stronger With You"]
        )

    def test_latest_only_keeps_both_when_the_latest_reply_named_both(self):
        """`latest_only` drops the older-reply tail, not perfumes we just named together."""
        from products.services.sales import described

        self._recommended_both()

        self.assertEqual(
            described.offered_in_order(
                self.conversation, self.store, latest_only=True
            ),
            ["Stronger With You Intensely", "Stronger With You"],
        )

    def test_a_perfume_named_without_data_behind_it_is_not_a_referent(self):
        """A withdrawal names a perfume with no data — it must not become "ده"."""
        from products.services.sales import described

        save_message(
            self.conversation, "assistant",
            "Ambero خرج من الاختيارات. أنصحك بـ Stronger With You بـ 700 جنيه.",
            internal_context="Name: Stronger With You",
        )
        Product.objects.create(
            store=self.store, brand=self.brand, name="Ambero", gender="male",
        )

        self.assertEqual(
            described.offered_in_order(self.conversation, self.store),
            ["Stronger With You"],
        )

    def test_nesting_names_do_not_collide(self):
        """"Stronger With You" is a prefix of "Stronger With You Intensely", so asking each
        name where it occurs returns the same index for both and the shorter, more generic
        name wins the tie — pointing "ده" at the wrong perfume."""
        from products.services.sales import described

        save_message(
            self.conversation, "assistant",
            "أنصحك بـ Stronger With You بـ 700 جنيه، "
            "ولو تحب حاجة أقوى Stronger With You Intensely بـ 780 جنيه.",
            internal_context="Name: Stronger With You\nName: Stronger With You Intensely",
        )

        self.assertEqual(
            described.offered_in_order(self.conversation, self.store),
            ["Stronger With You", "Stronger With You Intensely"],
        )

    def test_nothing_offered_yet_is_an_empty_list(self):
        from products.services.sales import described

        self.assertEqual(described.offered_in_order(self.conversation, self.store), [])
        self.assertEqual(described.offered_in_order(None, self.store), [])

    def test_the_extractor_is_handed_the_offered_list(self):
        from products.services.order_service import _offered_context

        self._recommended_both()

        block = _offered_context(self.conversation, self.store)

        self.assertIn("PERFUMES YOU JUST OFFERED", block)
        self.assertIn("1. Stronger With You Intensely", block)
        self.assertIn("2. Stronger With You", block)

    def test_the_block_is_empty_when_nothing_was_offered(self):
        from products.services.order_service import _offered_context

        self.assertEqual(_offered_context(self.conversation, self.store), "")

    def test_the_extractor_is_taught_the_demonstrative(self):
        import inspect

        from products.services import order_service

        source = inspect.getsource(order_service.handle_order)

        self.assertIn("DEMONSTRATIVE REFERENCE", source)
        self.assertIn("هاخد ده", source)

    def test_the_empty_cart_rule_allows_a_pointed_reference(self):
        """It used to say "return [] unless they NAME a perfume", which forbade resolving "ده"."""
        import inspect

        from products.services import order_service

        source = inspect.getsource(order_service.handle_order)

        self.assertIn("points at one with a demonstrative/ordinal", source)

    # ── the identical reply ──────────────────────────────────────────────
    def test_the_clarification_is_not_repeated_verbatim(self):
        from products.services.order_service import _WHICH_PERFUME, _ask_which_perfume

        self._recommended_both()
        first = _ask_which_perfume(self.conversation, self.store)
        save_message(self.conversation, "assistant", first)

        second = _ask_which_perfume(self.conversation, self.store)

        self.assertEqual(first, _WHICH_PERFUME)
        self.assertNotEqual(second, first)

    def test_the_second_ask_names_the_perfumes_we_offered(self):
        """An open question becomes a choice, which is also more useful."""
        from products.services.order_service import _WHICH_PERFUME, _ask_which_perfume

        self._recommended_both()
        save_message(self.conversation, "assistant", _WHICH_PERFUME)

        second = _ask_which_perfume(self.conversation, self.store)

        self.assertIn("Stronger With You Intensely", second)
        self.assertIn("Stronger With You", second)

    def test_the_second_ask_still_works_with_nothing_offered(self):
        from products.services.order_service import _WHICH_PERFUME, _ask_which_perfume

        save_message(self.conversation, "assistant", _WHICH_PERFUME)

        second = _ask_which_perfume(self.conversation, self.store)

        self.assertNotEqual(second, _WHICH_PERFUME)
        self.assertTrue(second.strip())


class HarnessFalseDenialCheckTests(TestCase):
    """The eval harness must be able to catch a denial of a perfume we stock.

    `_DENIAL` sat in `eval_harness/checks.py` with the comment "used to catch 'we don't have it'
    while stock exists" and zero references anywhere — and `rescore.py`'s docstring already
    committed in writing to the false-positive rule with nothing implementing it. Two halves of
    one unfinished feature, which is why the suite scored conversation 1099 clean.

    These are the first tests over harness code; `build_ground_truth` needs the ORM, so they
    live here with everything else.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Versace")
        self.eros = Product.objects.create(
            store=self.store, brand=self.brand, name="Eros", gender="male",
        )
        ProductVariant.objects.create(
            product=self.eros, volume=90, price=1019, bottle_type="normal"
        )
        self.truth = self._truth()

    def _truth(self):
        from eval_harness import checks

        return checks.build_ground_truth(self.store)

    def _denied(self, reply, truth=None):
        from eval_harness import checks

        return checks._false_denial(reply, truth or self.truth)

    # ── it fires on the real defect ───────────────────────────────────────
    def test_the_transcripts_false_denial_is_caught(self):
        """The literal reply from conversation 1099."""
        self.assertEqual(
            self._denied("عطر Versace Eros مش متوفر عندنا حالياً، يا فندم."), "Eros"
        )

    def test_every_denial_phrasing_is_caught(self):
        for reply in (
            "للأسف Eros مش متوفر عندنا.",
            "Eros غير متوفر حالياً.",
            "مفيش عندنا Eros.",
            # Conversation 726's phrasing. None of the four original patterns matched it, so a
            # perfume in stock in both bottle types was denied and the suite scored it clean.
            "عطر Eros مش موجود في البيانات اللي معايا دلوقتي، هسأل وأرد عليك يا فندم.",
            "Eros غير موجود حالياً.",
            "مش عندنا Eros خلاص.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self._denied(reply), "Eros")

    # ── telling the customer about the injected data ──────────────────────
    def _leak(self, reply):
        from eval_harness import checks

        return [
            code for code, _, _ in checks.check_reply(
                reply, truth=self.truth, context="", customer_text="", turn_state={},
            )
        ]

    def test_talking_about_the_data_is_its_own_finding(self):
        """Independent of ground truth: the customer should never learn "البيانات" exists."""
        self.assertIn(
            "internal_data_leak",
            self._leak("عطر Eros مش موجود في البيانات اللي معايا، هسأل وأرد عليك."),
        )

    def test_the_leak_fires_even_for_a_perfume_we_do_not_stock(self):
        """A perfume we genuinely lack is a correct denial but still not an excuse to describe
        the plumbing — ✅ "لحظة أتأكدلك" says the same thing without it."""
        codes = self._leak("Black Orchid مش موجود في البيانات اللي معايا.")

        self.assertIn("internal_data_leak", codes)
        self.assertNotIn("false_denial", codes)

    def test_a_clean_deferral_is_not_a_leak(self):
        """The sanctioned escape hatch from prompts.py rule 2 must stay clean."""
        self.assertNotIn(
            "internal_data_leak", self._leak("لحظة أتأكدلك منه يا فندم وأرد عليك.")
        )

    # ── the false positives it must not produce ───────────────────────────
    def test_an_original_bottle_denial_is_not_a_false_denial(self):
        """product_formatting dictates this sentence verbatim for a global brand with no
        original variant, and the persona reproduces it بالحرف — so the *fixed* 1099 reply
        still contains it. Eros genuinely has no original bottle."""
        reply = "Versace Eros متوفر. للاسف مش متوفر منه زجاجة أوريجينال حالياً."

        self.assertIsNone(self._denied(reply))

    def test_a_size_scoped_denial_is_not_a_false_denial(self):
        for reply in (
            "Eros موجود. حجم 50 ملي غير متوفر حالياً (المتاح: 90 ملي).",
            "Eros متوفر بس الـ50 ملي مش متوفر.",
        ):
            with self.subTest(reply=reply):
                self.assertIsNone(self._denied(reply))

    def test_a_brand_bottle_denial_is_not_a_false_denial(self):
        reply = "زجاجات البراند لعطر Eros غير متوفرة حالياً."

        self.assertIsNone(self._denied(reply))

    def test_denying_a_perfume_we_do_not_stock_is_correct(self):
        """Scenario X1's case — the inverse, where denial is the right answer."""
        self.assertIsNone(self._denied("عطر Black Orchid مش متوفر عندنا."))

    def test_a_denial_naming_no_product_is_not_flagged(self):
        self.assertIsNone(self._denied("للأسف العطر ده مش متوفر عندنا يا فندم."))

    def test_a_reply_with_no_denial_at_all_is_not_flagged(self):
        self.assertIsNone(self._denied("Eros متوفر عندنا، الـ90 ملي بـ1019 جنيه."))

    def test_a_deactivated_product_may_be_denied(self):
        """`names` stays unfiltered so hallucinations are still caught; only the availability
        set is active-only, so a denial about a deactivated perfume is correct."""
        self.eros.is_active = False
        self.eros.save()

        self.assertIsNone(self._denied("Eros مش متوفر عندنا.", self._truth()))

    def test_an_out_of_stock_original_only_product_may_be_denied(self):
        """Only original bottles can run out; with no sellable variant the denial is true."""
        sold_out = Product.objects.create(
            store=self.store, brand=self.brand, name="Oud Wood", gender="unisex",
        )
        ProductVariant.objects.create(
            product=sold_out, volume=100, price=700, bottle_type="original", stock=0
        )

        truth = self._truth()

        self.assertIn("Eros", truth["available_names"])
        self.assertNotIn("Oud Wood", truth["available_names"])
        self.assertIsNone(self._denied("Oud Wood مش متوفر عندنا.", truth))

    def test_a_similarity_denial_is_not_an_availability_denial(self):
        """Scenario S1 produces this verbatim: "مفيش عندنا حاجة شبه X" denies a RESEMBLANCE and
        is the honest answer when no close match exists. It says nothing about stock."""
        reply = (
            "مفيش عندنا حاجة شبه Dior Sauvage بالظبط، لكن ممكن يعجبك Luna Rossa Carbon "
            "لو بتحب الريحة النضيفة، وفيه كمان Ambero."
        )

        self.assertIsNone(self._denied(reply))

    def test_a_denial_is_not_pinned_on_a_perfume_being_recommended(self):
        """A reply that denies one thing and offers another is ordinary, so the denied name has
        to sit in the same clause as the denial — not just somewhere in the reply."""
        reply = "مفيش عندنا حاجة زي Baccarat Rouge 540، بس Ambero قريب في الجو."

        self.assertIsNone(self._denied(reply))

    def test_a_denial_in_a_later_clause_is_still_caught(self):
        """Clause scoping must not become a way to hide a real denial."""
        reply = "رشحتلك حاجات تانية، بس Eros مش متوفر عندنا خلاص."

        self.assertEqual(self._denied(reply), "Eros")

    def test_a_reordered_name_is_still_matched(self):
        """Matching goes through naming.mentioned_in, not a substring test. "9pm by Afnan" for
        "Afnan 9PM" is that function's own documented case, and substring matching missed it."""
        Product.objects.create(
            store=self.store, brand=self.brand, name="Afnan 9PM", gender="unisex",
        )
        ProductVariant.objects.create(
            product=Product.objects.get(store=self.store, name="Afnan 9PM"),
            volume=90, price=1077, bottle_type="normal",
        )

        self.assertEqual(
            self._denied("9pm by Afnan مش متوفر عندنا.", self._truth()), "Afnan 9PM"
        )

    def test_a_nesting_name_reports_the_longer_one(self):
        """Catalogue names nest: text containing the longer name satisfies every token of the
        shorter one, so the more specific name has to win."""
        for name, price in (("Stronger With You", 700), ("Stronger With You Intensely", 780)):
            product = Product.objects.create(
                store=self.store, brand=self.brand, name=name, gender="male",
            )
            ProductVariant.objects.create(
                product=product, volume=90, price=price, bottle_type="normal"
            )

        self.assertEqual(
            self._denied("Stronger With You Intensely مش متوفر عندنا.", self._truth()),
            "Stronger With You Intensely",
        )

    # ── it is actually wired in, on both passes ───────────────────────────
    def test_check_reply_emits_the_finding(self):
        from eval_harness import checks

        findings = checks.check_reply(
            "عطر Versace Eros مش متوفر عندنا حالياً.",
            truth=self.truth, context="", customer_text="", turn_state={},
        )

        self.assertIn("false_denial", [code for code, _, _ in findings])

    def test_rescore_emits_the_finding_too(self):
        """checks.py findings only reach runs.json — rescore is where findings.json comes from."""
        from eval_harness import checks, rescore

        record = {
            "id": "T1",
            "turns": [{
                "n": 1, "user": "ليه مرشحتش eros",
                "reply": "عطر Eros مش متوفر عندنا حالياً.",
                "context": "", "search": {"matched": ["Eros"]},
                "merged_intent": {}, "stage": None,
            }],
        }

        findings = rescore.rescore(record, self.truth)

        self.assertIn("false_denial", [f["code"] for f in findings])

    def test_rescore_excuses_a_denial_the_injected_row_supports(self):
        """format_product writes this Stock Status when nothing is sellable, so a reply
        relaying it is telling the truth about the data it was given."""
        from eval_harness import rescore

        record = {
            "id": "T2",
            "turns": [{
                "n": 1, "user": "عندكم eros؟",
                "reply": "عطر Eros مش متوفر عندنا حالياً.",
                "context": "Name: Eros\nStock Status: ❌ هذا المنتج غير متوفر حالياً بجميع أحجامه",
                "search": {"matched": ["Eros"]}, "merged_intent": {}, "stage": None,
            }],
        }

        findings = rescore.rescore(record, self.truth)

        self.assertNotIn("false_denial", [f["code"] for f in findings])

    def test_the_severity_is_one_rescore_can_sort(self):
        """rescore.main() indexes a fixed severity table and raises KeyError otherwise."""
        from eval_harness import checks

        findings = checks.check_reply(
            "عطر Eros مش متوفر عندنا.",
            truth=self.truth, context="", customer_text="", turn_state={},
        )
        severities = {severity for _, severity, _ in findings}

        self.assertTrue(severities <= {"critical", "high", "medium", "low"})

    def test_a_scenario_covers_this_case(self):
        """X1 probes the inverse. Nothing covered a perfume that IS stocked."""
        from eval_harness.scenarios import SCENARIOS

        probes = " ".join(s["probe"] for s in SCENARIOS)

        self.assertIn("ليه مرشحتش versace eros", [t for s in SCENARIOS for t in s["turns"]])
        self.assertIn("not in the data I was handed", probes)

    # ── false positives the denial work surfaced in neighbouring checks ───
    def test_a_products_own_total_price_is_not_an_order_total(self):
        """"سعره الإجمالي 944" is one bottle's price. Read as a cart total it flagged a correct
        product_info answer about Dior Sauvage's 90ml against a budget stated two turns later."""
        from eval_harness import checks

        reply = "أنصحك بحجم الـ 90 ملي، بس سعره الإجمالي 944 جنيه."

        self.assertEqual(checks.check_stated_total(reply, 700, self.truth), [])

    def test_a_real_order_total_is_still_caught(self):
        from eval_harness import checks

        self.assertEqual(
            checks.check_stated_total("💰 الإجمالي: 1560 جنيه.", 900, self.truth), [1560.0]
        )

    def test_rescore_ignores_a_budget_not_yet_stated(self):
        """runner.py gates this; rescore did not, so a price quoted before the customer
        mentioned a budget was scored against it."""
        from eval_harness import rescore

        record = {
            "id": "T3",
            "turns": [
                {"n": 1, "user": "بحب سوفاج",
                 "reply": "الـ90 ملي سعره الإجمالي 944 جنيه.",
                 "context": "", "search": {}, "merged_intent": {}, "stage": None},
                {"n": 2, "user": "ميزانيتي 700",
                 "reply": "تمام.", "context": "", "search": {},
                 "merged_intent": {"max_price": 700}, "stage": None},
            ],
        }

        codes = [f["code"] for f in rescore.rescore(record, self.truth, scenario_budget=700)]

        self.assertNotIn("over_budget_total", codes)

    def test_an_honest_admission_is_not_a_similarity_overclaim(self):
        """"مش موجود عندنا حاجة شبهه" admits the gap outright, but "مش موجود" was missing from
        the admission markers, so the word "شبه" alone made it an overclaim."""
        from eval_harness import rescore

        record = {
            "id": "T4",
            "turns": [{
                "n": 1, "user": "عايز حاجه شبه سوفاج",
                "reply": "Dior Sauvage مش موجود عندنا حاجة شبهه بالظبط.",
                "context": "",
                "search": {"similarity": {
                    "reference_name": "Dior Sauvage",
                    "best_band": "none", "has_close_match": False,
                }},
                "merged_intent": {}, "stage": None,
            }],
        }

        codes = [f["code"] for f in rescore.rescore(record, self.truth)]

        self.assertNotIn("similarity_overclaim", codes)

    def test_naming_a_perfume_does_not_set_the_brand(self):
        """Inferring brand='Dior' from "سوفاج" collapsed a twelve-perfume shortlist to two
        mainstream Dior products for a customer who had just asked for something uncommon."""
        import inspect

        from products.services.ai import intent as intent_module

        source = inspect.getsource(intent_module.extract_intent)

        self.assertIn("NAMING A PERFUME IS NOT NAMING A BRAND", source)


class NameTokenPunctuationTests(TestCase):
    """Punctuation glued to a name must not defeat deterministic matching.

    `tokens()` split on whitespace only, so "بكام Dior Sauvage؟" produced the token
    "sauvage؟" and matched no product — while the identical message with a space before the
    "؟" matched fine. That silently broke every deterministic call site: `mentioned_in` on the
    order-cancel branch, `match_product` as the resolver's post-filter, and the named-perfume
    guard in `product_info`. Found while wiring the 1099 referent fix.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.product = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage", gender="male",
        )

    def test_a_trailing_arabic_question_mark_does_not_hide_a_name(self):
        found = sales_naming.mentioned_in("بكام Dior Sauvage؟", [self.product])

        self.assertEqual([p.name for p in found], ["Dior Sauvage"])

    def test_an_arabic_comma_does_not_either(self):
        found = sales_naming.mentioned_in("عايز Dior Sauvage، بكام؟", [self.product])

        self.assertEqual([p.name for p in found], ["Dior Sauvage"])

    def test_names_carrying_digits_tokenise_unchanged(self):
        """The fix must not disturb names where a number is identifying."""
        for name, expected in (
            ("Afnan 9PM", {"9pm", "afnan"}),
            ("XJ 1861 Naxos", {"1861", "naxos", "xj"}),
            ("Baccarat Rouge 540", {"540", "baccarat", "rouge"}),
        ):
            with self.subTest(name=name):
                self.assertEqual(sales_naming.tokens(name), expected)

    def test_a_message_naming_nothing_still_matches_nothing(self):
        self.assertEqual(sales_naming.mentioned_in("بكام؟", [self.product]), [])

    def test_the_summary_line_shape_still_resolves(self):
        """The order-cancel case the function was written for."""
        noirvel = Product.objects.create(
            store=self.store, brand=self.brand, name="Noirvel", gender="male",
        )

        found = sales_naming.mentioned_in("مش عايز 1 × Noirvel (90ml)", [noirvel])

        self.assertEqual([p.name for p in found], ["Noirvel"])


class ProductInfoReferentTests(TestCase):
    """A follow-up with no perfume name is about the perfume we just offered.

    Conversation 1099, turn 6: the bot had just discussed Versace Eros, the customer said
    "مش متوفر متأكد ؟", and `resolve_products` returned Dior Sauvage + Lattafa Asad from two
    turns earlier. The reply was only correct because the model read Eros's prices out of the
    conversation history — the injected context was wrong, which stops being survivable as soon
    as the 8-message window truncates.

    Resolved in Python from `Message.internal_context` rather than left to the resolver, whose
    only reference guidance was one sentence plus a rule scoped to short confirmations
    ("ماشي", "تمام") that a doubt utterance does not match.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Versace")
        self.eros = Product.objects.create(
            store=self.store, brand=self.brand, name="Eros", gender="male",
        )
        ProductVariant.objects.create(
            product=self.eros, volume=90, price=1019, bottle_type="normal"
        )
        self.older = Product.objects.create(
            store=self.store, brand=self.brand, name="Dior Sauvage", gender="male",
        )
        ProductVariant.objects.create(
            product=self.older, volume=90, price=944, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(store=self.store)

    def _offered(self, name):
        """A reply that named `name`, with the data behind it, as the router saves it."""
        save_message(
            self.conversation, "assistant",
            f"{name} متوفر عندنا، والـ 90 ملي بـ 1019 جنيه.",
            internal_context=f"Name (الاسم الصحيح): {name}",
        )

    def _referent(self, message):
        from products.services.product_info import _referent_from_conversation

        return [
            p.name for p in
            _referent_from_conversation(message, self.store, self.conversation)
        ]

    def _offered_both(self):
        """One reply naming two perfumes, with both rows behind it."""
        save_message(
            self.conversation, "assistant",
            "Eros بـ 1019 جنيه، و Dior Sauvage بـ 944 جنيه.",
            internal_context=(
                "Name (الاسم الصحيح): Eros\nName (الاسم الصحيح): Dior Sauvage"
            ),
        )

    def test_the_referent_is_every_perfume_the_latest_reply_named(self):
        """Conversation 726 asked "كل واحده كام سعرها" about two perfumes and `offered[0]`
        handed the model one row. It also forced a guess when the customer went on to name one
        of the two in Arabic, which `_named_in_message` cannot match at all."""
        self._offered_both()

        self.assertEqual(
            self._referent("كل واحده كام سعرها"), ["Eros", "Dior Sauvage"]
        )

    def test_the_full_726_sequence_stays_on_both_perfumes(self):
        """The whole seam, end to end: an ORDER turn that asks a bottle type about two perfumes
        (cart context, both omitted from it while pending), then the price answer, then a
        follow-up naming one of the two in Arabic. Every turn has to stay on the two perfumes;
        the cart's own perfume must never become the subject."""
        save_message(
            self.conversation, "assistant",
            "الطلب لحد دلوقتي: 1 × Le Male. محتاج أعرف نوع الزجاجة "
            "(أوريجينال أم زجاجة البراند؟) من عطر Eros و من عطر Dior Sauvage؟",
            internal_context="Le Male (50 ملي) (زجاجة البراند) x 1 (623.00 EGP)",
        )

        self.assertEqual(
            self._referent("كل واحده كام سعرها"), ["Eros", "Dior Sauvage"]
        )

        # The reply that referent then produces, saved the way the router saves it.
        self._offered_both()

        self.assertEqual(
            self._referent("سعر سوفاج لوحده عامل كام"), ["Eros", "Dior Sauvage"]
        )

    def test_a_doubt_utterance_resolves_to_the_perfume_just_offered(self):
        self._offered("Eros")

        self.assertEqual(self._referent("مش متوفر متأكد ؟"), ["Eros"])

    def test_a_bare_price_question_resolves_to_it_too(self):
        """"بكام؟" is the purest case — no name, no pronoun, pure ellipsis."""
        self._offered("Eros")

        self.assertEqual(self._referent("بكام؟"), ["Eros"])

    def test_the_newest_turn_wins_over_an_earlier_one(self):
        """The exact 1099 shape: an older perfume must not out-rank the newest."""
        self._offered("Dior Sauvage")
        self._offered("Eros")

        self.assertEqual(self._referent("متأكد؟"), ["Eros"])

    def test_nothing_offered_yet_is_a_no_op(self):
        self.assertEqual(self._referent("بكام؟"), [])

    def test_no_conversation_is_a_no_op(self):
        """Six existing tests call get_product_info with three positional args, so the
        conversation-gated path must stay inert for them."""
        from products.services.product_info import _referent_from_conversation

        self._offered("Eros")

        self.assertEqual(_referent_from_conversation("بكام؟", self.store, None), [])
        self.assertEqual(_referent_from_conversation("بكام؟", None, self.conversation), [])

    def test_a_withdrawn_perfume_is_never_the_referent(self):
        """Named with no data behind it means we said it was gone."""
        self._offered("Eros")
        save_message(
            self.conversation, "assistant",
            "Dior Sauvage خرج من الاختيارات.",
            internal_context="",
        )

        self.assertNotIn("Dior Sauvage", self._referent("بكام؟"))

    # ── precedence ────────────────────────────────────────────────────────
    def test_an_explicit_name_in_the_message_wins(self):
        """An explicit name beats anything inferred from an earlier turn."""
        self._offered("Eros")

        with mock.patch(
            "products.services.product_info.resolve_products"
        ) as resolver, mock.patch(
            "products.services.product_info.chat", return_value="ok"
        ):
            _, context = get_product_info(
                "بكام Dior Sauvage؟", [], self.store, self.conversation
            )

        self.assertIn("Dior Sauvage", context)
        self.assertNotIn("Eros", context)
        self.assertFalse(resolver.called, "the resolver should not be needed at all")

    def test_the_referent_is_preferred_over_the_resolvers_guess(self):
        """This is the fix: the resolver returning the wrong perfume no longer decides."""
        self._offered("Eros")

        with mock.patch(
            "products.services.product_info.resolve_products",
            return_value=[self.older],
        ), mock.patch(
            "products.services.product_info.chat", return_value="ok"
        ):
            _, context = get_product_info(
                "مش متوفر متأكد ؟", [], self.store, self.conversation
            )

        self.assertIn("Eros", context)
        self.assertNotIn("Dior Sauvage", context)

    def test_the_resolver_still_runs_when_nothing_is_under_discussion(self):
        """Arabic transliterations stay on the LLM path, so it must not be bypassed."""
        with mock.patch(
            "products.services.product_info.resolve_products",
            return_value=[self.older],
        ) as resolver, mock.patch(
            "products.services.product_info.chat", return_value="ok"
        ):
            _, context = get_product_info(
                "بكام سوفاج؟", [], self.store, self.conversation
            )

        self.assertTrue(resolver.called)
        self.assertIn("Dior Sauvage", context)

    # ── the anchor handed to the resolver ─────────────────────────────────
    def test_the_resolver_is_given_the_offered_list(self):
        from products.services.sales import described

        self._offered("Eros")

        block = described.offered_context_block(self.conversation, self.store)

        self.assertIn("PERFUMES YOU JUST OFFERED", block)
        self.assertIn("1. Eros", block)

    def test_the_offered_block_is_empty_when_nothing_was_offered(self):
        from products.services.sales import described

        self.assertEqual(
            described.offered_context_block(self.conversation, self.store), ""
        )
        self.assertEqual(described.offered_context_block(None, self.store), "")

    def test_the_order_branch_still_reads_the_same_block(self):
        """order_service._offered_context is imported by name and its strings are pinned."""
        from products.services.order_service import _offered_context
        from products.services.sales import described

        self._offered("Eros")

        self.assertEqual(
            _offered_context(self.conversation, self.store),
            described.offered_context_block(self.conversation, self.store),
        )

    def test_the_resolver_prompt_states_that_the_newest_turn_wins(self):
        import inspect

        from products.services import product_resolver

        source = inspect.getsource(product_resolver.resolve_products)

        self.assertIn("THE NEWEST TURN WINS", source)
        self.assertIn("PERFUMES YOU JUST OFFERED", source)


class WantedHeavyIsNotAvoidedTests(TestCase):
    """Wanting a heavy perfume and rejecting one are opposites, and were the same output.

    "عايزه حاجه تقيله للشتا" — I want something HEAVY for winter — came back as
    avoid_traits ["heavy"] and persisted for the whole conversation. `avoid_traits` is the only
    extracted field scored as a penalty (-3.0), and `notes.HEAVY_NOTES` includes musk and amber,
    the two commonest bases and the fixatives that produce high longevity. So the inversion put
    -3.0 on precisely the perfumes the customer asked for, and the reply then described a winter
    oriental as "خفيف ومش خانق" to someone who wanted heavy. Scenario M2 scored 5.3 on it.

    The deterministic half guesses no polarity: it only resolves two extracted fields
    contradicting each other on the same axis.
    """

    def _clean(self, intent):
        from products.services.ai.intent import _sanitize

        return _sanitize(dict(intent))["avoid_traits"]

    def test_wanting_strength_discards_an_avoid_on_the_same_axis(self):
        self.assertEqual(
            self._clean({"projection": "strong", "avoid_traits": ["heavy"]}), []
        )

    def test_every_projection_axis_trait_is_discarded(self):
        for trait in ("heavy", "loud", "strong"):
            with self.subTest(trait=trait):
                self.assertEqual(
                    self._clean({"projection": "strong", "avoid_traits": [trait]}), []
                )

    def test_a_different_axis_survives_the_contradiction_check(self):
        """Wanting strength says nothing about sweetness."""
        self.assertEqual(
            self._clean({"projection": "strong", "avoid_traits": ["heavy", "sweet"]}),
            ["sweet"],
        )

    def test_a_genuine_avoid_is_untouched(self):
        """No positive request means nothing to contradict — this is a real exclusion."""
        self.assertEqual(self._clean({"avoid_traits": ["heavy"]}), ["heavy"])

    def test_a_moderate_request_is_not_a_contradiction(self):
        self.assertEqual(
            self._clean({"projection": "moderate", "avoid_traits": ["heavy"]}), ["heavy"]
        )

    def test_the_prompt_states_the_polarity_rule(self):
        import inspect

        from products.services.ai import intent as intent_module

        source = inspect.getsource(intent_module.extract_intent)

        self.assertIn("POLARITY FIRST", source)
        self.assertIn("عايزه حاجه تقيله للشتا", source)
        self.assertIn("مش عايز حاجة تقيلة", source)

    def test_the_ranker_cannot_penalise_a_wanted_strength(self):
        """End to end: the intent a wanted-heavy customer produces must not carry the penalty."""
        from products.services.ai.intent import _sanitize
        from products.services.sales import ranking

        store = Store.objects.create(name="Perfamix Test")
        brand = Brand.objects.create(store=store, name="Perfamix Test")
        heavy = Product.objects.create(
            store=store, brand=brand, name="Ambero", gender="male",
            projection="Strong", base_notes="Amber, Musk, Sandalwood",
        )
        ProductVariant.objects.create(
            product=heavy, volume=50, price=601, bottle_type="normal"
        )

        intent = _sanitize({"projection": "strong", "avoid_traits": ["heavy"]})
        entry = sales_ranking.rank([heavy], intent)[0]

        self.assertEqual(
            [m for m in entry.mismatches if "تقيل" in m], [],
            "a wanted strength must not be scored as an avoided one",
        )


class OnlineFirstCallToActionTests(TestCase):
    """The default next step is completing the purchase in chat, not a shop visit.

    The brands this agent ships to sell mainly online. The persona listed "تحب تطلب؟" and
    "تنورنا في الستور تشم وتجرب؟" as peers with no priority, and the closing gate then deleted
    every online closer while matching nothing about a visit — so the walk-in invite was the
    only CTA that could survive mid-conversation. The evaluation scored that as
    sales_effectiveness 6.9, with "no concrete next step" as its commonest complaint.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.settings = StoreSettings.objects.create(
            store=self.store,
            payment_instructions="عربون لا يقل عن ٢٥٠ج والباقي عند الاستلام.",
        )

    def test_the_online_close_is_the_default_path(self):
        prompt = get_system_prompt(self.store)

        self.assertIn("الأصل إن العميل يطلب من هنا", prompt)
        self.assertIn("يختار الحجم", prompt)

    def test_the_store_visit_is_demoted_to_a_secondary_option(self):
        prompt = get_system_prompt(self.store)

        self.assertIn("الزيارة للستور اختيار **تاني**", prompt)

    def test_the_visit_is_no_longer_a_peer_in_the_cta_menu(self):
        """It used to sit beside "تحب تطلب؟" with no priority between them."""
        self.assertNotIn("تنورنا في الستور تشم وتجرب", get_system_prompt(self.store))

    def test_payment_terms_reach_the_agent(self):
        """They only ever reached the post-order confirmation, so the persona could neither
        state the deposit nor invent it — it just stopped short of closing."""
        prompt = get_system_prompt(self.store)

        self.assertIn("٢٥٠ج", prompt)
        self.assertIn("للحظة إتمام الطلب بس", prompt)

    def test_a_store_with_no_payment_terms_gets_no_block(self):
        """The close must degrade to collecting details rather than inventing a method."""
        self.settings.payment_instructions = "   "
        self.settings.save()

        prompt = get_system_prompt(self.store)

        self.assertNotIn("شروط الدفع والشحن بتاعة الستور", prompt)
        self.assertIn("الأصل إن العميل يطلب من هنا", prompt)

    def test_payment_facts_are_not_a_blanket_ban_any_more(self):
        """Rule 8 forbade saying anything about دفع/تحويل/شحن/توصيل, which made an online CTA
        a rule violation. It now defers to whether the terms were actually injected."""
        prompt = get_system_prompt(self.store)

        self.assertNotIn(
            "أو استرجاع أو دفع أو تحويل أو شحن أو توصيل أو عروض", prompt
        )

    def test_the_address_contradiction_is_gone(self):
        """One line ordered the bot to send the address; another forbade inventing it, and the
        address is not in the prompt at all."""
        self.assertNotIn("ابعتله العنوان — مش ترشيحات", get_system_prompt(self.store))

    def test_low_intent_still_suppresses_the_close(self):
        """Scenario D3 scores closing_timing 10 for declining to close. Online-first must not
        turn into pressure."""
        prompt = get_system_prompt(self.store)

        self.assertIn("بيتفرج بس", prompt)
        self.assertIn("من غير أي سؤال قفل", prompt)

    def test_no_store_still_renders(self):
        self.assertTrue(get_system_prompt(None))


class NarrowingVersusClosingTests(TestCase):
    """A size choice is a next step, not an order ask, and is earned a stage earlier.

    The closing gate was mechanically one-sided: every pattern in PREMATURE_CLOSERS is an
    *online* closer and none of them matches "تنورنا في الستور تشم وتجرب؟". So at six of the
    eight stages the only CTA that could physically survive to the customer was a walk-in
    invite — backwards for a business that sells online, and most of why the evaluation's
    sales_effectiveness sat at 6.9 with "no concrete next step" as its commonest complaint.
    """

    SIZE = "ده أنسب ليك. أجيبلك الـ90 ولا الـ50؟"
    HARD = "ده أنسب ليك. تحب تطلب؟"

    def test_a_size_choice_survives_from_the_recommendation_stage_on(self):
        for stage in (
            sales_stage.RECOMMENDATION,
            sales_stage.PURCHASE_INTENT,
            sales_stage.ORDER_COLLECTION,
        ):
            with self.subTest(stage=stage):
                self.assertIn("أجيبلك", _finalize(self.SIZE, stage))

    def test_a_size_choice_is_still_stripped_before_a_recommendation(self):
        for stage in (
            sales_stage.DISCOVERY,
            sales_stage.COMPARISON,
            sales_stage.OBJECTION,
            sales_stage.IDENTIFICATION,
            sales_stage.COMPLAINT,
        ):
            with self.subTest(stage=stage):
                self.assertNotIn("أجيبلك", _finalize(self.SIZE, stage))

    def test_a_hard_ask_is_still_gated_to_the_purchase_stages(self):
        """Widening the gate wholesale would have let a close onto a factual question."""
        for stage in (
            sales_stage.DISCOVERY,
            sales_stage.RECOMMENDATION,
            sales_stage.COMPARISON,
            sales_stage.OBJECTION,
            sales_stage.IDENTIFICATION,
            sales_stage.COMPLAINT,
        ):
            with self.subTest(stage=stage):
                self.assertNotIn("تحب تطلب", _finalize(self.HARD, stage))

        for stage in (sales_stage.PURCHASE_INTENT, sales_stage.ORDER_COLLECTION):
            with self.subTest(stage=stage):
                self.assertIn("تحب تطلب", _finalize(self.HARD, stage))

    def test_soft_closing_is_strictly_wider_than_closing(self):
        for stage in sales_stage.STAGES:
            with self.subTest(stage=stage):
                if sales_stage.closing_allowed(stage):
                    self.assertTrue(sales_stage.soft_closing_allowed(stage))

    def test_the_default_call_still_strips_both_tiers(self):
        """Every existing caller and test passes no allow_soft, so behaviour is unchanged."""
        self.assertNotIn("أجيبلك", strip_premature_closing(self.SIZE))
        self.assertNotIn("تحب تطلب", strip_premature_closing(self.HARD))

    # ── the feminine form escaped all three layers at once ────────────────
    def test_the_feminine_address_is_caught(self):
        """R3 said "تحبي أجهزلك واحدة؟". Every pattern required `تحب` followed by whitespace,
        so the feminine form slipped past production, checks.py and rescore.py together — for a
        store whose customers are largely women, half the conversations."""
        feminine = "أنصحك بـ Ambero. تحبي أجهزلك واحدة؟"

        self.assertNotIn("أجهزلك", _finalize(feminine, sales_stage.RECOMMENDATION))

    def test_both_grammatical_forms_of_every_hard_ask_are_caught(self):
        for reply in (
            "ده حلو. تحب أساعدك في الطلب؟",
            "ده حلو. تحبي أساعدك في الطلب؟",
            "ده حلو. تحب نكمل الطلب؟",
            "ده حلو. تحبي نكمل الطلب؟",
            "ده حلو. تحبى تطلب؟",
        ):
            with self.subTest(reply=reply):
                cleaned = _finalize(reply, sales_stage.RECOMMENDATION)

                self.assertTrue(cleaned.rstrip().endswith("حلو."), cleaned)

    # ── the three layers must agree ───────────────────────────────────────
    def _harness_codes(self, reply, stage):
        from eval_harness import checks, rescore

        truth = checks.build_ground_truth(self.store)
        first = [
            code for code, _, _ in checks.check_reply(
                reply, truth=truth, context="x", customer_text="",
                turn_state={"stage": stage},
            )
        ]
        record = {"id": "T", "turns": [{
            "n": 1, "user": "x", "reply": reply, "context": "x",
            "search": {}, "merged_intent": {}, "stage": stage,
        }]}
        second = [f["code"] for f in rescore.rescore(record, truth)]
        return first, second

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")

    def test_all_three_layers_agree_a_size_choice_is_fine(self):
        """They had drifted: rescore flagged this high, checks.py never saw it, the judge
        scored it 9."""
        first, second = self._harness_codes(self.SIZE, sales_stage.RECOMMENDATION)

        self.assertNotIn("premature_close", first)
        self.assertNotIn("premature_close", second)
        self.assertIn("أجيبلك", _finalize(self.SIZE, sales_stage.RECOMMENDATION))

    def test_all_three_layers_agree_a_hard_ask_is_premature(self):
        first, second = self._harness_codes(
            "أنصحك بـ Ambero. تحبي أجهزلك واحدة؟", sales_stage.RECOMMENDATION
        )

        self.assertIn("premature_close", first)
        self.assertIn("premature_close", second)

    def test_the_harness_reads_the_production_stage_sets(self):
        """Three hardcoded copies had already drifted apart."""
        from eval_harness import rescore

        self.assertIs(rescore.CLOSING_STAGES, sales_stage.CLOSING_STAGES)
        self.assertIs(rescore.SOFT_CLOSING_STAGES, sales_stage.SOFT_CLOSING_STAGES)


class NamedPerfumeSurvivesTheShortlistTests(TestCase):
    """A perfume the customer named must have its row present, shortlist or not.

    Conversation 1099, turn 5: `extract_intent` on "ليه مرشحتش versace eros" returned gender,
    budget, longevity and projection — and nothing referencing Eros. So the name never became
    a search key, and whether Eros reached the customer depended on it surviving a generic
    filter capped at twelve products. It had survived one turn earlier and did not survive this
    one, and the model then reported it as unavailable.

    The extractor is asked to keep the name; this is the guarantee that does not depend on it.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Versace")
        self.eros = Product.objects.create(
            store=self.store, brand=self.brand, name="Eros", gender="male",
        )
        ProductVariant.objects.create(
            product=self.eros, volume=90, price=1019, bottle_type="normal"
        )
        self.shortlisted = Product.objects.create(
            store=self.store, brand=self.brand, name="Ambero", gender="male",
        )
        ProductVariant.objects.create(
            product=self.shortlisted, volume=90, price=1180, bottle_type="normal"
        )

    def _block(self, message):
        from products.services.ai.recommendation import _named_but_missing_block

        shortlist = Product.objects.filter(store=self.store, name="Ambero")
        return _named_but_missing_block(message, self.store, shortlist)

    def test_a_named_perfume_absent_from_the_shortlist_gets_its_row(self):
        block = self._block("ليه مرشحتش versace eros")

        self.assertIn("Eros", block)
        self.assertIn("1019", block)

    def test_the_block_says_outright_that_it_is_available(self):
        """Without this the persona's own red line turns the gap into a denial."""
        self.assertIn("ممنوع تقول إنه مش موجود", self._block("عايز حاجة زي Eros"))

    def test_a_perfume_already_in_the_shortlist_is_not_duplicated(self):
        self.assertEqual(self._block("عايز حاجة زي Ambero"), "")

    def test_no_named_perfume_means_no_block(self):
        self.assertEqual(self._block("عايز عطر رجالي فواح"), "")

    def test_the_block_is_none_safe(self):
        from products.services.ai.recommendation import _named_but_missing_block

        shortlist = Product.objects.filter(store=self.store, name="Ambero")

        self.assertEqual(_named_but_missing_block("", self.store, shortlist), "")
        self.assertEqual(_named_but_missing_block("eros", None, shortlist), "")

    def test_polarity_is_not_inferred(self):
        """The row states existence only. Guessing direction from a bare name is what produced
        avoid_traits ["heavy"] for a customer asking FOR a heavy perfume."""
        block = self._block("مش عايز versace eros")

        self.assertIn("Eros", block)
        for leaked in ("similar_to", "exclude", "avoid", "مش عايزه", "بيحبه"):
            self.assertNotIn(leaked, block)

    def test_the_guarantee_is_on_the_normal_path_not_just_the_empty_one(self):
        """It used to guard only the nothing-found branch, which is not where 1099 failed."""
        import inspect

        from products.services.ai import recommendation

        source = inspect.getsource(recommendation.recommend)
        case_one = source[: source.index("# Case 2")]

        self.assertIn("_named_but_missing_block(message, store, products", case_one)

    def test_the_extractor_is_told_never_to_drop_a_named_perfume(self):
        import inspect

        from products.services.ai import intent as intent_module

        source = inspect.getsource(intent_module.extract_intent)

        self.assertIn("A PERFUME NAMED IN THE LATEST MESSAGE IS NEVER DROPPED", source)
        self.assertIn("ليه مرشحتش", source)


class NamedPerfumeIsNeverDeniedTests(TestCase):
    """A perfume absent from the shortlist must not be reported as out of stock.

    Conversation 1099: the customer asked "ليه مرشحتش versace eros" and was told
    "عطر Versace Eros مش متوفر عندنا حالياً" — while Eros sat in the catalogue, active, at
    1019 جنيه for the 90ml. It had even been in the injected shortlist one turn earlier.

    Two causes, both fixed here. The classifier sent "ليه مرشحتش X" to the recommendation
    flow, which never looks a named perfume up. And the shortlist is a filtered, capped
    selection of a 47-product catalogue, but the persona told the model that a product absent
    from its data does not exist — so with 12 slots and 47 products, false denials were
    guaranteed rather than accidental.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Versace")
        self.eros = Product.objects.create(
            store=self.store, brand=self.brand, name="Eros", gender="male",
        )
        ProductVariant.objects.create(
            product=self.eros, volume=50, price=666, bottle_type="normal"
        )
        ProductVariant.objects.create(
            product=self.eros, volume=90, price=1019, bottle_type="normal"
        )

    # ── the deterministic name fallback ──────────────────────────────────
    def test_a_named_perfume_is_matched_without_a_model_call(self):
        from products.services.product_info import _named_in_message

        found = _named_in_message("ليه مرشحتش versace eros", self.store)

        self.assertEqual([p.name for p in found], ["Eros"])

    def test_a_perfume_that_is_genuinely_absent_stays_absent(self):
        """The guard must not manufacture a match — "بلاك اوركيد" really is not stocked."""
        from products.services.product_info import _named_in_message

        self.assertEqual(_named_in_message("عندكم بلاك اوركيد؟", self.store), [])

    def test_a_message_naming_nothing_matches_nothing(self):
        from products.services.product_info import _named_in_message

        self.assertEqual(_named_in_message("عايز عطر رجالي فواح", self.store), [])

    def test_the_fallback_is_none_safe(self):
        from products.services.ai.recommendation import _named_in_message as rec_named
        from products.services.product_info import _named_in_message as info_named

        self.assertEqual(info_named(None, self.store), [])
        self.assertEqual(info_named("eros", None), [])
        self.assertEqual(rec_named("", self.store), [])

    def test_both_branches_share_the_same_matcher(self):
        """The recommendation branch could deny a perfume too, so it needs the guard as well."""
        from products.services.ai.recommendation import _named_in_message as rec_named
        from products.services.product_info import _named_in_message as info_named

        message = "ليه مرشحتش versace eros"

        self.assertEqual(
            [p.name for p in rec_named(message, self.store)],
            [p.name for p in info_named(message, self.store)],
        )

    # ── product_info falls back when the resolver comes back empty ────────
    def test_product_info_recovers_when_the_resolver_returns_nothing(self):
        with mock.patch(
            "products.services.product_info.resolve_products", return_value=[]
        ), mock.patch(
            "products.services.product_info.chat", return_value="ok"
        ):
            _, context = get_product_info("ليه مرشحتش versace eros", [], self.store)

        self.assertIn("Eros", context)
        self.assertIn("1019", context)

    def test_product_info_still_reports_a_genuinely_unknown_perfume(self):
        """The guard must not turn an unknown name into a resolved product. Eros may still
        appear below as a suggested alternative — that is the existing fallback, and the
        distinction is the "لم يتم التعرف" notice at the top."""
        with mock.patch(
            "products.services.product_info.resolve_products", return_value=[]
        ), mock.patch(
            "products.services.product_info.chat", return_value="ok"
        ):
            _, context = get_product_info("عندكم بلاك اوركيد؟", [], self.store)

        self.assertIn("لم يتم التعرف على اسم منتج محدد", context)
        self.assertNotIn("بيانات المنتجات الحقيقية", context)

    # ── the prompt rules that licensed the denial ────────────────────────
    def test_the_persona_separates_absent_from_unavailable(self):
        """The red line used to read "المنتج اللي مش في البيانات = مش موجود عندنا"."""
        prompt = get_system_prompt(self.store)

        self.assertIn("مش في البيانات دي\" ≠ \"مش موجود في الستور", prompt)
        self.assertNotIn("المنتج اللي مش في البيانات = مش موجود عندنا", prompt)

    def test_the_nothing_found_branch_no_longer_asserts_unavailability(self):
        import inspect

        from products.services.ai import recommendation

        source = inspect.getsource(recommendation.recommend)

        self.assertIn("_named_in_message(message, store)", source)
        self.assertNotIn("وأخبره أن العطر غير متوفر حالياً", source)

    def test_the_classifier_routes_why_didnt_you_recommend_to_product_info(self):
        import inspect

        from products.services.ai import classifier

        source = inspect.getsource(classifier.classify)

        self.assertIn("ASKING WHY A NAMED PERFUME WAS NOT OFFERED", source)
        self.assertIn("ليه مرشحتش", source)


class BudgetWarningReachesTheCustomerTests(TestCase):
    """The end-to-end gap that let the whole thing ship.

    Every existing budget test called _over_budget_warning directly, so none of them noticed
    that handle_order never called it. These drive a real order turn and read the reply the
    customer would actually receive.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Emporio Armani")
        product = Product.objects.create(
            store=self.store, brand=self.brand,
            name="Stronger With You Intensely", gender="male",
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=450, bottle_type="normal"
        )
        ProductVariant.objects.create(
            product=product, volume=90, price=780, bottle_type="normal"
        )
        self.conversation = Conversation.objects.create(
            store=self.store, preferences={"max_price": 900},
        )

    DETAILS = {
        "customer_name": "بلال حسن",
        "customer_phone": "01012345678",
        "customer_secondary_phone": "01198765432",
        "shipping_address": "مدينة نصر، شارع عباس العقاد",
    }

    def _summary(self, quantity):
        payload = {
            "products": [{
                "name": "Stronger With You Intensely",
                "quantity": quantity, "volume": 90, "bottle_type": "normal",
            }],
            "is_confirmed": False,
            **self.DETAILS,
        }
        with mock.patch(
            "products.services.order_service.chat", return_value=json.dumps(payload)
        ):
            reply, _ = handle_order("...", [], self.store, self.conversation)
        return reply

    def test_the_customer_is_warned_when_the_cart_breaks_their_budget(self):
        """Scenario F1: 2 × 780 = 1560 was quoted against a stated 900 in silence."""
        reply = self._summary(quantity=2)

        self.assertIn("💰 الإجمالي:", reply)
        self.assertIn("1560", reply)
        self.assertIn("900", reply)
        self.assertIn("⚠️", reply)

    def test_an_in_budget_cart_gets_no_warning(self):
        reply = self._summary(quantity=1)

        self.assertIn("💰 الإجمالي:", reply)
        self.assertNotIn("⚠️", reply)

    def test_the_warning_does_not_break_the_confirmation_handshake(self):
        """_summary_was_shown greps the saved reply for the 💰 marker to authorise an order."""
        from products.services.order_service import _summary_was_shown, get_cart

        reply = self._summary(quantity=2)
        save_message(self.conversation, "assistant", reply)

        self.assertTrue(_summary_was_shown(self.conversation, get_cart(self.conversation)))

    def test_no_order_is_created_while_the_summary_is_being_shown(self):
        self._summary(quantity=2)

        self.assertEqual(Order.objects.count(), 0)

    # ── the details request used to repeat byte-for-byte ─────────────────
    def _details_request(self, quantity, volume=90):
        payload = {
            "products": [{
                "name": "Stronger With You Intensely",
                "quantity": quantity, "volume": volume, "bottle_type": "normal",
            }],
            "is_confirmed": False,
            "customer_name": None, "customer_phone": None,
            "customer_secondary_phone": None, "shipping_address": None,
        }
        with mock.patch(
            "products.services.order_service.chat", return_value=json.dumps(payload)
        ):
            reply, _ = handle_order("...", [], self.store, self.conversation)
        return reply

    def test_the_details_request_says_what_is_in_the_cart(self):
        """It went out identically on three turns while the cart changed under it, so a size
        change was invisible and "الاجمالي بقى كام؟" went unanswered."""
        reply = self._details_request(quantity=2)

        self.assertIn("Stronger With You Intensely", reply)
        self.assertIn("1560", reply)
        self.assertIn("ناقصني", reply)

    def test_the_details_request_changes_when_the_cart_changes(self):
        two = self._details_request(quantity=2)
        one = self._details_request(quantity=1)

        self.assertNotEqual(two, one)
        self.assertIn("780", one)

    def test_the_budget_warning_arrives_before_personal_details_are_handed_over(self):
        """Waiting for the full summary means learning you are over budget only after giving
        your name, phone and address."""
        reply = self._details_request(quantity=2)

        self.assertIn("⚠️", reply)
        self.assertIn("900", reply)

    def test_the_interim_recap_cannot_authorise_a_confirmation(self):
        """_summary_was_shown greps for the 💰 marker, so the recap must not carry it."""
        from products.services.order_service import (
            CONFIRMATION_SUMMARY_MARKER,
            _summary_was_shown,
            get_cart,
        )

        reply = self._details_request(quantity=2)
        save_message(self.conversation, "assistant", reply)

        self.assertNotIn(CONFIRMATION_SUMMARY_MARKER, reply)
        self.assertFalse(
            _summary_was_shown(self.conversation, get_cart(self.conversation))
        )

    def test_an_empty_cart_gets_no_recap(self):
        from products.services.order_service import _cart_recap

        self.assertEqual(_cart_recap(self.conversation, [], 0), "")


class OverBudgetLineTests(TestCase):
    """Noirvel's 90ml at 1085 was assembled against a stated 900 and nobody said anything.

    The order flow never read conversation.preferences, so the only thing standing between the
    customer and an over-budget line was their own reading of the summary.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Perfamix Test")
        self.conversation = Conversation.objects.create(
            store=self.store, preferences={"max_price": 900},
        )
        product = Product.objects.create(
            store=self.store, brand=self.brand, name="Noirvel", gender="male",
        )
        self.variant = ProductVariant.objects.create(
            product=product, volume=90, price=1085, bottle_type="normal"
        )

    def _items(self, price):
        return [{
            "variant": self.variant, "quantity": 1,
            "price": Decimal(str(price)), "bottle_type": "normal",
        }]

    def test_a_line_above_the_stated_budget_is_flagged(self):
        from products.services.order_service import _over_budget_warning

        warning = _over_budget_warning(self.conversation, self._items(1085))

        self.assertIn("Noirvel", warning)
        self.assertIn("1085", warning)
        self.assertIn("900", warning)

    def test_a_line_within_the_budget_is_not_flagged(self):
        from products.services.order_service import _over_budget_warning

        self.assertEqual(_over_budget_warning(self.conversation, self._items(856)), "")

    def test_no_stated_budget_means_no_flag(self):
        from products.services.order_service import _over_budget_warning

        self.conversation.preferences = {}
        self.conversation.save()

        self.assertEqual(_over_budget_warning(self.conversation, self._items(1085)), "")

    def test_the_flag_offers_to_remove_it_rather_than_refusing(self):
        """The customer may well want it, and the summary is already the moment they check."""
        from products.services.order_service import _over_budget_warning

        self.assertIn("أشيله", _over_budget_warning(self.conversation, self._items(1085)))

    # ── the quantity used to be ignored ──────────────────────────────────
    def test_a_line_is_measured_by_its_total_not_its_unit_price(self):
        """2 × 780 = 1560 against a stated 900 passed silently: the check read
        item["price"], the unit price, while the summary line prints price × quantity."""
        from products.services.order_service import _over_budget_warning

        items = [{
            "variant": self.variant, "quantity": 2,
            "price": Decimal("780"), "bottle_type": "normal",
        }]

        warning = _over_budget_warning(self.conversation, items)

        self.assertIn("1560", warning)
        self.assertIn("900", warning)

    # ── the cart total was never checked at all ──────────────────────────
    def test_two_affordable_lines_that_break_the_budget_together_are_flagged(self):
        """Each line cleared 900 on its own, so nothing was ever said about 1753."""
        from products.services.order_service import _over_budget_warning

        second = ProductVariant.objects.create(
            product=Product.objects.create(
                store=self.store, brand=self.brand, name="Vanilo", gender="male",
            ),
            volume=90, price=897, bottle_type="normal",
        )
        items = [
            {"variant": self.variant, "quantity": 1,
             "price": Decimal("856"), "bottle_type": "normal"},
            {"variant": second, "quantity": 1,
             "price": Decimal("897"), "bottle_type": "normal"},
        ]

        warning = _over_budget_warning(self.conversation, items)

        self.assertIn("1753", warning)
        self.assertIn("900", warning)

    def test_a_total_within_the_budget_is_still_silent(self):
        from products.services.order_service import _over_budget_warning

        items = [{
            "variant": self.variant, "quantity": 1,
            "price": Decimal("400"), "bottle_type": "normal",
        }]

        self.assertEqual(_over_budget_warning(self.conversation, items), "")

    def test_an_over_budget_line_is_not_also_reported_as_an_over_budget_total(self):
        """One problem, one warning — the customer should not read the same thing twice."""
        from products.services.order_service import _over_budget_warning

        warning = _over_budget_warning(self.conversation, self._items(1085))

        self.assertEqual(warning.count("⚠️"), 1)
        self.assertNotIn("إجمالي الطلب", warning)

    # ── the whole function was unreachable ───────────────────────────────
    def test_the_warning_actually_reaches_the_summary(self):
        """It was defined, unit-tested, and never called. handle_order's summary block was
        never edited to interpolate it, so no customer ever saw a budget warning."""
        import inspect

        from products.services import order_service

        source = inspect.getsource(order_service.handle_order)

        self.assertIn("_over_budget_warning", source)

    def test_the_summary_marker_survives_the_warning(self):
        """_summary_was_shown greps for 💰 الإجمالي:, so the warning must not displace it."""
        import inspect

        from products.services import order_service

        source = inspect.getsource(order_service.handle_order)
        total_at = source.index("💰 الإجمالي:")
        warning_at = source.index("_over_budget_warning")

        self.assertLess(total_at, warning_at)

    def test_product_info_is_forbidden_from_computing_a_total(self):
        """A product_info turn holds one perfume row and no cart, so the model did the
        arithmetic itself: "الإجمالي هيبقى 1560 جنيه ... للطلبين" for an empty cart."""
        import inspect

        from products.services import product_info

        source = inspect.getsource(product_info.get_product_info)

        self.assertIn("ممنوع تحسب إجمالي طلب", source)

    def test_the_classifier_routes_an_order_total_question_to_the_order_flow(self):
        """"الاجمالي بقى كام" was caught by the "كام" price rule and sent to product_info."""
        import inspect

        from products.services.ai import classifier

        source = inspect.getsource(classifier.classify)

        self.assertIn("THE ORDER TOTAL IS NOT A PRODUCT PRICE", source)
        self.assertIn("الاجمالي بقى كام", source)

    def test_the_extractor_is_taught_positional_reference(self):
        """"هات 90 ملي من اول واحد ده" put both perfumes from the previous reply in the cart."""
        import inspect

        from products.services import order_service

        source = inspect.getsource(order_service.handle_order)

        self.assertIn("POSITIONAL REFERENCE", source)
        self.assertIn("اول واحد", source)

    def test_the_note_seals_the_two_lists_against_each_other(self):
        """Handed "Ambero dropped" alongside a keeping list, the model announced *Vanilo* as
        dropped — a perfume on the keeping list, in budget and available. Telling a customer an
        available perfume is gone is worse than saying nothing."""
        from products.services.sales import described

        note = described.continuity_note(
            ["Vanilo", "Black Opium"],
            {"Ambero": "مش من نفس النوع اللي طلبه"},
            converge=False,
        )

        self.assertIn("ممنوع تقول عن أي عطر تاني إنه خرج", note)
        self.assertIn("Vanilo", note)
        self.assertIn("Black Opium", note)

    def test_the_dropped_list_is_stated_as_exhaustive(self):
        from products.services.sales import described

        note = described.continuity_note(
            ["Vanilo"], {"Ambero": None}, converge=False
        )

        self.assertIn("بس هي اللي خرجت", note)


class ReferenceIsNotItsOwnLookalikeTests(TestCase):
    """Evaluation scenario M1: "بحب سوفاج" → the reply names Dior Sauvage → the next turn asks
    for something less mainstream, and Dior Sauvage comes back *first in its own lookalike
    shortlist*, pushing a real candidate off the twelve-slot context.

    The exclusion existed already but carved out a reference under discussion (`keep`), because
    removing it from `base` also removed it from the keep/dropped logic and the model then had
    no data about the perfume being discussed — conversation 630, "Intensely مش مناسب لميزانيتك"
    about a 780 EGP perfume on an 800 EGP budget. M1 is where both hold at once, so `keep`
    membership was the wrong test. Candidacy and data are separated instead.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Dior")
        self.sauvage = self._make("Dior Sauvage", "Bergamot", "Lavender", "Ambroxan", 642)
        self.lookalike = self._make("Ambero", "Bergamot", "Lavender", "Ambroxan", 700)

    def _make(self, name, top, middle, base, price):
        product = Product.objects.create(
            store=self.store, brand=self.brand, name=name, gender="male",
            top_notes=top, middle_notes=middle, base_notes=base,
        )
        ProductVariant.objects.create(
            product=product, volume=50, price=price, bottle_type="normal"
        )
        return product

    def _names(self, results):
        found = []
        for key in ("products", "alternatives"):
            queryset = results.get(key)
            if queryset is not None:
                found += [product.name for product in queryset]
        return found

    def test_a_reference_under_discussion_is_not_a_candidate(self):
        results = search_products(
            {"similar_to": "Dior Sauvage", "wants_uncommon": True},
            store=self.store, keep={"Dior Sauvage"},
        )

        self.assertNotIn("Dior Sauvage", self._names(results))
        self.assertIn("Ambero", self._names(results))

    def test_a_reference_not_under_discussion_is_not_a_candidate_either(self):
        """Regression guard: the pre-existing half of the exclusion still holds."""
        results = search_products({"similar_to": "Dior Sauvage"}, store=self.store)

        self.assertNotIn("Dior Sauvage", self._names(results))

    def test_the_reference_is_still_reported_so_its_row_can_be_injected(self):
        """Conversation 630's requirement, expressed as data rather than prose. Losing the
        candidacy must not lose the perfume."""
        for keep in ({"Dior Sauvage"}, set()):
            with self.subTest(keep=keep):
                results = search_products(
                    {"similar_to": "Dior Sauvage"}, store=self.store, keep=keep,
                )
                self.assertEqual(results["reference_product"], self.sauvage)

    def test_no_reference_reports_none(self):
        results = search_products({"gender": "male"}, store=self.store)

        self.assertIsNone(results["reference_product"])

    def test_the_self_match_never_set_the_band(self):
        """The harness blamed has_close_match, and that was never the mechanism:
        _similarity_summary already skips the reference when computing the best band. Pinned so
        a future reader does not go hunting for a bug that is not there."""
        results = search_products(
            {"similar_to": "Dior Sauvage"}, store=self.store, keep={"Dior Sauvage"},
        )

        self.assertEqual(results["similarity"]["reference_name"], "Dior Sauvage")
        self.assertTrue(
            results["similarity"]["has_close_match"], "Ambero is a real close match"
        )

    # ── the prompt half ──────────────────────────────────────────────────
    def _block(self, keep=frozenset()):
        from products.services.ai.recommendation import _reference_block

        results = search_products(
            {"similar_to": "Dior Sauvage"}, store=self.store, keep=keep,
        )
        return _reference_block(results)

    def test_the_block_carries_the_real_row(self):
        block = self._block()

        self.assertIn("Dior Sauvage", block)
        self.assertIn("642", block)

    def test_the_block_says_it_is_available(self):
        self.assertIn("ممنوع تقول إنه مش موجود", self._block())

    def test_the_block_says_it_is_the_comparison_target_not_a_recommendation(self):
        """Without this the row reads as one more candidate and we are back to M1, just with an
        extra header above it."""
        block = self._block()

        self.assertIn("مرجع المقارنة", block)
        self.assertIn("ممنوع ترشحه", block)

    def test_no_reference_renders_nothing(self):
        from products.services.ai.recommendation import _reference_block

        self.assertEqual(_reference_block({}), "")
        self.assertEqual(_reference_block(None), "")
        self.assertEqual(
            _reference_block(search_products({"gender": "male"}, store=self.store)), ""
        )

    def test_both_branches_inject_the_block(self):
        import inspect
        from products.services.ai import recommendation

        source = inspect.getsource(recommendation.recommend)

        self.assertEqual(
            source.count("_reference_block(search, max_price)"), 2,
            "the exact-match and alternatives branches both need it",
        )


class NothingAffordableStillQuotesARealPriceTests(TestCase):
    """Evaluation scenario X3, a critical `invented_number`: budget 300, every size in the
    shortlist labelled "❌ (أعلى من الميزانية بكتير — ممنوع تعرضه)", and the reply said
    "الـ50 ملي بتاعهم فوق 590 جنيه". No such price exists in the catalogue.

    The model had the real numbers. It was told never to present a ❌ size and, a line later, to
    present the nearest size with its price difference — both impossible at once when every size
    is ❌, and the way out it found was a figure it invented. So the one true figure is named and
    every other number forbidden, instead of asking for a number it is simultaneously banned
    from giving.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.brand = Brand.objects.create(store=self.store, name="Creed")

    def _note(self, max_price, variants):
        from products.services.ai.recommendation import _in_budget_note

        product = Product.objects.create(
            store=self.store, brand=self.brand, name="Aventus", gender="male",
        )
        for volume, price, bottle_type, stock in variants:
            ProductVariant.objects.create(
                product=product, volume=volume, price=price,
                bottle_type=bottle_type, stock=stock,
            )
        return _in_budget_note(
            Product.objects.filter(pk=product.pk), Decimal(str(max_price))
        )

    def test_the_cheapest_size_is_named_with_its_real_price(self):
        note = self._note(300, [(50, 1240, "normal", None), (100, 2100, "normal", None)])

        self.assertIn("1240", note)
        self.assertNotIn("2100", note)

    def test_only_that_figure_is_permitted(self):
        note = self._note(300, [(50, 1240, "normal", None)])

        self.assertIn("الرقم الوحيد", note)
        self.assertIn("ممنوع تقرّب", note)

    def test_the_quoted_size_must_be_sellable(self):
        """A cheaper original with stock=0 cannot be the sanctioned figure — the affordable
        branch already learned this when it named a stock-0 60ml at 456."""
        note = self._note(300, [(60, 900, "original", 0), (50, 1240, "normal", None)])

        self.assertIn("1240", note)
        self.assertNotIn("900", note)

    def test_it_still_refuses_to_generalise_about_the_store(self):
        note = self._note(300, [(50, 1240, "normal", None)])

        self.assertIn("مفيش أي حجم في القائمة دي", note)
        self.assertIn("ممنوع تعمم على الستور كله", note)

    def test_an_affordable_size_takes_the_other_branch_unchanged(self):
        note = self._note(1500, [(50, 1240, "normal", None)])

        self.assertIn("فيه أحجام داخل ميزانية العميل", note)
        self.assertNotIn("الرقم الوحيد", note)

    def test_no_budget_still_renders_nothing(self):
        from products.services.ai.recommendation import _in_budget_note

        self.assertEqual(_in_budget_note(Product.objects.none(), None), "")
