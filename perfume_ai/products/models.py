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
    meta_access_token = EncryptedTextField(blank=True, help_text="Graph API Access Token")
    meta_app_secret = EncryptedCharField(max_length=500, blank=True, help_text="App Secret for signature validation")
    facebook_page_id = models.CharField(max_length=100, blank=True)
    instagram_account_id = models.CharField(max_length=100, blank=True)
    whatsapp_phone_number_id = models.CharField(max_length=100, blank=True)

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

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="products", null=True, blank=True)
    name = models.CharField(max_length=200)
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)

    gender = models.CharField(max_length=20, choices=GENDER_CHOICES)

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
        ("normal", "زجاجة المحل"),
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