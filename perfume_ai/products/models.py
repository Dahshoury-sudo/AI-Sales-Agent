import secrets
from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField, EncryptedTextField, phone_blind_index

def generate_api_key():
    return secrets.token_urlsafe(32)

class Store(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="stores", null=True, blank=True)
    name = models.CharField(max_length=200)
    api_key = models.CharField(max_length=100, unique=True, default=generate_api_key)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class StoreSettings(models.Model):
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="settings")
    system_prompt = models.TextField(blank=True, help_text="Custom system prompt for this store's AI")
    whatsapp_number = models.CharField(max_length=50, blank=True)
    
    # Meta Integration Credentials (encrypted at rest)
    # Indexed: every inbound webhook resolves the store by one of these before it
    # can do anything else, so they are the hottest lookups in the system.
    meta_verify_token = models.CharField(max_length=100, blank=True, db_index=True, help_text="Token for webhook verification")
    meta_access_token = EncryptedTextField(blank=True, help_text="WhatsApp Graph API Access Token")
    messenger_access_token = EncryptedTextField(blank=True, help_text="Messenger & Instagram Page Access Token")
    meta_app_secret = EncryptedCharField(max_length=500, blank=True, help_text="App Secret for signature validation")
    facebook_page_id = models.CharField(max_length=100, blank=True, db_index=True)
    instagram_account_id = models.CharField(max_length=100, blank=True, db_index=True)
    whatsapp_phone_number_id = models.CharField(max_length=100, blank=True, db_index=True)

    # Payment / deposit block appended to the order confirmation message.
    # Per-store because it carries the store's own payment account: the text used
    # to be hardcoded in order_service.create_order_in_db, so every store's
    # customers were told to pay the first store's InstaPay account.
    payment_instructions = models.TextField(
        blank=True,
        help_text=(
            "تعليمات الدفع اللي تظهر للعميل بعد تأكيد الطلب (العربون، طرق التحويل، "
            "سياسة الإلغاء). لو فاضية، البوت هيقول للعميل إن فريق المبيعات هيتواصل معاه "
            "بتفاصيل الدفع."
        ),
    )

    # Photo of this store's own bottles/packaging, sent when the bot emits
    # [SEND_BOTTLE_IMAGE]. Per-store because it was a single global setting, so
    # every store's customers were shown the first store's packaging.
    bottle_image_url = models.URLField(
        blank=True,
        max_length=500,
        help_text=(
            "لينك صورة الزجاجات والبوكس بتاع الستور. لو فاضية، البوت مش هيبعت صور."
        ),
    )

    # Store-specific factual claims injected into the system prompt: oil ratios,
    # available bottle sizes, whether there is a physical branch, how close the
    # blends are to the originals. These were hardcoded as universal truths, so
    # every store's bot asserted the first store's business model.
    business_facts = models.TextField(
        blank=True,
        help_text=(
            "حقائق خاصة بالستور يقولها البوت للعميل (نسب الزيت، الأحجام المتاحة، "
            "هل فيه فرع على أرض الواقع، نسبة تشابه التركيب). لو فاضية، البوت مش "
            "هيدّعي أي حاجة من دي."
        ),
    )

    # Comment Auto-Reply
    comment_reply_messages = models.TextField(
        blank=True,
        default="✅ تم الرد في الخاص، راجع رسائلك!\n📩 جاوبناك في الخاص، اتفضل شوف!\nتم الرد في الإنبوكس ✅\nCheck your DM, we replied! 💬\nراجع رسائلك الخاصة، بعتنالك الرد 📬",
        help_text="رسائل الرد على التعليقات — كل سطر رسالة منفصلة، البوت يختار عشوائياً (حد أقصى 5 رسائل)"
    )

    # The subscription's monthly allowance of model-billed messages. Null means
    # unlimited. Exceeding it never stops the bot replying — a store whose bot goes
    # quiet mid-sale loses more than the overage is worth — it only notifies the owner
    # and shows up on the invoice.
    monthly_message_cap = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="الحد الشهري للرسائل اللي بتستهلك الـ AI. فاضي = بدون حد."
    )

    def __str__(self):
        return f"Settings for {self.store.name}"

class Brand(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="brands", null=True, blank=True)
    name = models.CharField(max_length=100)
    country = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Category(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="categories", null=True, blank=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Product(models.Model):
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("unisex", "Unisex"),
    ]

    PERFUME_TYPE_CHOICES = [
        ("oriental", "عطور شرقية"),
        ("western", "عطور غربية"),
        ("niche", "نيش"),
        ("ultra_niche", "الترا نيش (بريميوم)"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="products", null=True, blank=True)
    name = models.CharField(max_length=200)
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)

    gender = models.CharField(max_length=20, choices=GENDER_CHOICES)
    perfume_type = models.CharField(max_length=20, choices=PERFUME_TYPE_CHOICES, blank=True, null=True)

    season = models.CharField(max_length=100, blank=True)
    occasion = models.CharField(max_length=100, blank=True)

    longevity = models.CharField(max_length=100, blank=True)
    projection = models.CharField(max_length=100, blank=True)

    concentration = models.CharField(max_length=50, blank=True)

    top_notes = models.TextField(blank=True)
    middle_notes = models.TextField(blank=True)
    base_notes = models.TextField(blank=True)

    description = models.TextField(blank=True)

    # oil_stock_grams and concentration_percentage lived here. They gated availability:
    # a brand bottle was sellable only while the remaining bulk oil could fill it. The
    # problem was that the counter only ever went down — every confirmed order decremented
    # it and nothing replenished it automatically — so products slid to zero and became
    # invisible to the bot with nobody told, and the derived "(N زجاجة فقط)" line turned a
    # stale number into a false scarcity claim to the customer.
    #
    # Brand bottles are compounded to order, so they are now always available for an
    # active product. ProductVariant.stock still gates original bottles, which are
    # discrete physical units that cannot be re-blended.

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    BOTTLE_TYPE_CHOICES = (
        ("normal", "زجاجة البراند"),
        ("original", "أوريجينال"),
    )

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    volume = models.PositiveIntegerField(help_text="Volume in ml (e.g., 50, 80, 100)")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    
    bottle_type = models.CharField(max_length=20, choices=BOTTLE_TYPE_CHOICES, default="normal")
    stock = models.PositiveIntegerField(null=True, blank=True, help_text="مخزون الزجاجات (للأوريجينال فقط)")

    class Meta:
        ordering = ['bottle_type', 'volume']
        unique_together = ('product', 'volume', 'bottle_type')

    def __str__(self):
        return f"{self.product.name} - {self.volume}ml"


class Conversation(models.Model):
    PLATFORM_CHOICES = (
        ("web", "Web"),
        ("whatsapp", "WhatsApp"),
        ("messenger", "Messenger"),
        ("instagram", "Instagram"),
        # Started as a comment on a Facebook Page post rather than a DM.
        # views_meta.py has always stored this value; it was missing here, and
        # send_platform_message did not dispatch it either, so handoff replies to
        # Facebook commenters were silently dropped.
        ("facebook", "Facebook Comment"),
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="conversations", null=True, blank=True)
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES, default="web")
    platform_sender_id = models.CharField(max_length=255, blank=True, help_text="External user ID from the platform")
    needs_human = models.BooleanField(default=False)
    # What this customer has told us they want, carried across the history window.
    # extract_intent re-derives every search criterion from the last 8 messages only,
    # so a budget or gender stated five turns ago vanished — and losing max_price
    # flips recommendation.py into refusing to quote prices at all, while the router
    # re-asks for a budget the customer already gave. Lives here rather than on Cart
    # because clear_cart drops that row on every completed order, and a customer's
    # taste should outlive one purchase.
    preferences = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # get_or_create_platform_conversation filters on exactly these three
            # and takes the newest — once per inbound message on every platform.
            models.Index(
                fields=["store", "platform", "platform_sender_id", "-created_at"],
                name="conv_lookup_idx",
            ),
            # The handoff dashboard lists open conversations per store.
            models.Index(fields=["store", "needs_human"], name="conv_handoff_idx"),
        ]

    def __str__(self):
        return f"Conversation #{self.id}"


class Message(models.Model):
    # "agent" is a human colleague replying through the dashboard handoff. It is a
    # distinct role because these used to be saved as "assistant": once the handoff
    # was resolved and the bot resumed, it read a human's words as its own prior
    # output and imitated them — introducing itself as "محمد" and repeating claims
    # the bot cannot make. build_llm_history keeps these out of the model's context.
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
        ("agent", "Human Agent"),
    )

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages"
    )

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    content = models.TextField()
    internal_context = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # get_conversation_messages orders by -created_at within one
            # conversation and slices — the history fetch on every single turn.
            models.Index(fields=["conversation", "-created_at"], name="msg_history_idx"),
        ]

    def __str__(self):
        return f"{self.role} - {self.created_at}"

class Cart(models.Model):
    """An order in progress, before the customer confirms it.

    Order details used to be re-derived by the LLM from conversation history on
    every turn, but history is capped at 8 messages — so a name given five turns
    ago, or a perfume chosen before that, silently vanished from the cart.
    Persisting it means the flow survives long conversations: the extractor reads
    this instead of trying to remember.

    Deleted once converted into an Order, so a second order in the same
    conversation starts from empty.
    """

    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="cart"
    )
    customer_name = models.CharField(max_length=200, blank=True)
    # Same treatment as Order: the cart holds the same contact details before the
    # order exists, so leaving it plaintext would defeat encrypting the Order.
    customer_phone = EncryptedCharField(max_length=500, blank=True)
    secondary_phone = EncryptedCharField(max_length=500, blank=True)
    shipping_address = EncryptedTextField(blank=True)
    # A perfume the customer has chosen but not yet picked a size for. CartItem requires
    # a variant, so an unsized selection had nowhere to live and was simply dropped —
    # and because the extractor prompt (rightly) refuses to refill an empty cart from
    # history, the very next message lost the perfume outright: "هاخد اودورا" followed by
    # "خليها 2 بدل واحدة" was answered with "مش واضحلي عايز تطلب أنهي عطر".
    pending_product = models.ForeignKey(
        "Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_in_carts",
        help_text="عطر اختاره العميل لكن لسه محددش الحجم",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart for conversation #{self.conversation_id}"


class CartItem(models.Model):
    BOTTLE_CHOICES = (
        ("normal", "عادية"),
        ("original", "أوريجينال"),
    )

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    bottle_type = models.CharField(max_length=20, choices=BOTTLE_CHOICES, default="normal")

    class Meta:
        unique_together = ('cart', 'variant', 'bottle_type')

    def __str__(self):
        return f"{self.quantity} x {self.variant.product.name} ({self.variant.volume}ml)"


class Order(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
        ("delivered", "Delivered"),
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="orders")
    # customer_name stays unencrypted on purpose: admin search over it is an
    # icontains lookup, and substring search across ciphertext is not solvable —
    # not even with a blind index, which only supports exact match. The sensitive
    # pairing is name+address, and the address is encrypted.
    customer_name = models.CharField(max_length=200)
    # max_length grows well past the 11 digits it holds: Fernet ciphertext for a short
    # string is ~120 characters, so the old max_length=50 would fail on save.
    customer_phone = EncryptedCharField(max_length=500)
    secondary_phone = EncryptedCharField(max_length=500, blank=True)
    shipping_address = EncryptedTextField()
    # Deterministic keyed hash of the normalized phone. Exists because the encrypted
    # column cannot be queried: this is what the analytics DISTINCT and the admin
    # phone search run against. Written in save().
    customer_phone_hash = models.CharField(max_length=64, blank=True, db_index=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    bot_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Kept in step with customer_phone on every write, including the backfill
        # command, so the hash can never drift from the value it indexes.
        self.customer_phone_hash = phone_blind_index(self.customer_phone)
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            fields = set(kwargs["update_fields"])
            if "customer_phone" in fields:
                fields.add("customer_phone_hash")
            kwargs["update_fields"] = fields
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.id} - {self.customer_name}"

class OrderItem(models.Model):
    BOTTLE_CHOICES = (
        ("normal", "عادية"),
        ("original", "أوريجينال"),
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    bottle_type = models.CharField(max_length=20, choices=BOTTLE_CHOICES, default="normal")
    price_at_time_of_order = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.variant.product.name} ({self.variant.volume}ml) (Order #{self.order.id})"

class ConversationEvaluation(models.Model):
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE, related_name="evaluation")
    
    # Scores 0-100
    intent_score = models.IntegerField(default=100)
    search_score = models.IntegerField(default=100)
    product_info_score = models.IntegerField(default=100)
    comparison_score = models.IntegerField(default=100)
    order_score = models.IntegerField(default=100)
    
    # Overall score can be calculated or stored
    overall_score = models.FloatField(default=100.0)
    
    # Flags
    has_hallucination = models.BooleanField(default=False)
    
    # Notes
    evaluation_notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Eval for Conversation #{self.conversation.id} - Score: {self.overall_score}%"


class Notification(models.Model):
    TYPE_CHOICES = (
        ("handoff", "Human Handoff Required"),
        ("new_order", "New Order"),
        ("low_stock", "Low Stock"),
        ("delivery_failed", "Reply Not Delivered"),
        ("usage_warning", "Approaching Monthly Limit"),
    )

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.type}] {self.title} - {self.store.name}"


class StaticFAQ(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="static_faqs")
    question = models.CharField(max_length=500, help_text="السؤال كما يظهر للإدارة")
    keywords = models.TextField(help_text="كلمات مفتاحية مفصولة بفاصلة (مثال: شحن, توصيل, توصل)")
    answer = models.TextField(help_text="الرد الثابت")
    priority = models.IntegerField(default=0, help_text="أولوية أعلى = يتشيك الأول")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-priority', 'id']
        verbose_name = "سؤال ثابت"
        verbose_name_plural = "الأسئلة الثابتة"

    def __str__(self):
        return f"{self.question[:50]} ({self.store.name})"


class StoreMonthlyUsage(models.Model):
    """How many model-billed messages a store used in one calendar month.

    Nothing counted anything before this. throttles.py caps requests per *minute* and
    only on the DRF views — the Messenger and Instagram path goes through views_meta
    into Celery and never touches them, so the traffic that actually generates the
    OpenAI bill had no per-store limit of any kind, and the subscription tiers could
    not be enforced or invoiced.

    Counted in router.route at the point where model spending begins, so a StaticFAQ
    answer or a goodbye reply — both of which return earlier and cost nothing — is
    never billed to the store.
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="usage")
    # First day of the month this row covers.
    period = models.DateField()
    llm_messages = models.PositiveIntegerField(default=0)
    # Set once each, so an owner is warned rather than spammed every message.
    warned_at_80 = models.BooleanField(default=False)
    warned_at_cap = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "period"], name="one_usage_row_per_store_month"
            )
        ]
        ordering = ["-period"]

    def __str__(self):
        return f"{self.store.name} {self.period:%Y-%m}: {self.llm_messages}"