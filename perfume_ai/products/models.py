import secrets
from django.db import models
from django.contrib.auth.models import User
from .encryption import EncryptedCharField, EncryptedTextField

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
    meta_verify_token = models.CharField(max_length=100, blank=True, help_text="Token for webhook verification")
    meta_access_token = EncryptedTextField(blank=True, help_text="WhatsApp Graph API Access Token")
    messenger_access_token = EncryptedTextField(blank=True, help_text="Messenger & Instagram Page Access Token")
    meta_app_secret = EncryptedCharField(max_length=500, blank=True, help_text="App Secret for signature validation")
    facebook_page_id = models.CharField(max_length=100, blank=True)
    instagram_account_id = models.CharField(max_length=100, blank=True)
    whatsapp_phone_number_id = models.CharField(max_length=100, blank=True)

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

    oil_stock_grams = models.PositiveIntegerField(default=0, help_text="رصيد الزيت العطري بالجرام")
    concentration_percentage = models.PositiveIntegerField(default=30, help_text="نسبة الزيت للزجاجة (مثال: 30%)")

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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Conversation #{self.id}"


class Message(models.Model):
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
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
    customer_phone = models.CharField(max_length=50, blank=True)
    secondary_phone = models.CharField(max_length=50, blank=True)
    shipping_address = models.TextField(blank=True)
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
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=50)
    secondary_phone = models.CharField(max_length=50, blank=True)
    shipping_address = models.TextField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    bot_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

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