import hashlib
import hmac
import inspect
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.conf import settings
from django.test import TestCase, override_settings
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
    StoreSettings,
)
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
from products.services.product_resolver import resolve_products
from products.services.reply_sanitizer import sanitize_reply
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
                oil_stock_grams=500, concentration_percentage=30,
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
            oil_stock_grams=1000,
            concentration_percentage=30,
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

    def test_confirming_decrements_stock(self):
        self._confirm()

        self.product.refresh_from_db()
        # 50ml at 30% concentration = 15g of oil
        self.assertEqual(self.product.oil_stock_grams, 985)

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
            gender="male", oil_stock_grams=1000, concentration_percentage=30,
        )
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
        self.product.refresh_from_db()
        self.assertEqual(self.product.oil_stock_grams, 1000, "no stock was taken, none to restore")

    def test_cancelling_a_confirmed_order_still_restores_stock(self):
        """The existing path must keep working."""
        self.product.oil_stock_grams = 985
        self.product.save()
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
        self.product.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.product.oil_stock_grams, 1000)

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
            gender="male", oil_stock_grams=1000, concentration_percentage=30,
        )
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
        self.product.refresh_from_db()
        self.assertEqual(self.product.oil_stock_grams, 1000, "stock was taken")

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
            gender="male", oil_stock_grams=1000, concentration_percentage=30,
        )
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
                gender="male", oil_stock_grams=1000, concentration_percentage=30,
            )
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
            gender="male", oil_stock_grams=1000, concentration_percentage=30,
        )
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

        self.product.refresh_from_db()
        self.assertEqual(self.product.oil_stock_grams, 1000)
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
            description="Fresh spicy", oil_stock_grams=20, concentration_percentage=30,
        )
        # 50ml at 30% needs 15g of oil, so it is available from the 20g in stock.
        ProductVariant.objects.create(
            product=self.product, volume=50, price=400, bottle_type="normal"
        )
        # 200ml needs 60g, which the 20g in stock cannot cover — out of stock.
        ProductVariant.objects.create(
            product=self.product, volume=200, price=900, bottle_type="normal"
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
                gender="male", oil_stock_grams=1000, concentration_percentage=30,
            )
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
            ("products.services.comparison_service.chat",
             lambda: compare_products("hi", [], self.store), '{"perfume_1": "", "perfume_2": ""}'),
        ]
        for target, call, payload in cases:
            with self.subTest(target=target):
                profile = self._profile_used(target, call, return_value=payload)
                self.assertEqual(profile, "extract")

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
            oil_stock_grams=1000,
            concentration_percentage=30,
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

    def test_brand_bottles_show_scarcity_when_the_oil_is_nearly_out(self):
        """Only original bottles had a low-stock signal; brand bottles had none."""
        self.product.oil_stock_grams = 45  # exactly 3 × 50ml
        self.product.save()
        self.large.delete()

        self.assertIn("3 زجاجة فقط", format_product(self.product))

    def test_plentiful_stock_shows_no_scarcity_claim(self):
        self.assertNotIn("زجاجة فقط", format_product(self.product))

    def test_store_exclusive_carries_a_selling_instruction(self):
        """The ⭐ marker existed but told the model nothing, so a request for نيش got
        "not available" while three store-exclusive blends sat in context."""
        own_brand = Brand.objects.create(store=self.store, name=self.store.name)
        exclusive = Product.objects.create(
            store=self.store, brand=own_brand, name="Citrolo", gender="female",
            oil_stock_grams=500, concentration_percentage=30,
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

    def test_a_zero_concentration_product_offers_no_brand_bottles(self):
        """Behaviour change, made deliberately: the old check treated "needs no oil"
        as "always in stock", so a misconfigured product offered sizes that cannot be
        filled. Bad configuration should hide a size, not sell it."""
        self.product.concentration_percentage = 0
        self.product.save()

        block = format_product(self.product)

        self.assertIn("غير متوفر حالياً بجميع أحجامه", block)
        self.assertEqual(value_pick_note(self.product, self._variants()), "")

    def test_a_zero_volume_variant_is_not_offered(self):
        self.small.volume = 0
        self.small.save()

        # It still appears under "Out of Stock Sizes", which the block itself labels
        # DO NOT OFFER — what matters is that it is not in the available list.
        available = format_product(self.product).split("Out of Stock Sizes")[0]

        self.assertNotIn("الـ 0 ملي", available)
        self.assertIn("الـ 90 ملي", available)


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
            oil_stock_grams=1000, concentration_percentage=30,
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
