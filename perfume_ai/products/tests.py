import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from products.models import (
    Brand,
    Cart,
    CartItem,
    Conversation,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Store,
    StoreSettings,
)
from products.services.ai.prompts import get_system_prompt
from products.services.ai.recommendation import _coerce_budget, _format_products
from products.services.meta_service import send_platform_message
from products.services.product_info import get_product_info
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
)
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
        """Run one order turn with the extractor's JSON stubbed."""
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
        self._turn({"products": self.ONE_PERFUME})

        reply, _ = self._turn({
            "products": self.ONE_PERFUME,
            "customer_name": "محمد",
            "customer_phone": "01000000000",
            "customer_secondary_phone": "01100000000",
            "shipping_address": "القاهرة، المعادي، ٥ شارع النصر",
            "is_confirmed": True,
        })

        self.assertIn("تم تأكيد طلبك بنجاح", reply)
        order = Order.objects.get(store=self.store)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.customer_name, "محمد")
        self.assertFalse(
            Cart.objects.filter(conversation=self.conversation).exists(),
            "cart should be gone so a second order starts clean",
        )

    def test_confirming_decrements_stock(self):
        self._turn({"products": self.ONE_PERFUME})
        self._turn({
            "products": self.ONE_PERFUME,
            "customer_name": "محمد", "customer_phone": "01000000000",
            "customer_secondary_phone": "01100000000", "shipping_address": "القاهرة",
            "is_confirmed": True,
        })

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
        self.assertIn("لو السؤال واضح ومفهوم وأنت مش عارف الإجابة", prompt)
