from django.contrib import admin

from .models import (
    Brand,
    Category,
    Product,
    ProductVariant,
    Conversation,
    Message,
    Store,
    StoreSettings,
    Order,
    OrderItem,
    ConversationEvaluation,
    Notification,
    StaticFAQ,
)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "store", "country")
    list_filter = ("store",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "store")
    list_filter = ("store",)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    fields = ('bottle_type', 'volume', 'price', 'stock')
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "store",
        "brand",
        "oil_stock_grams",
        "concentration_percentage",
        "is_active",
    )

    list_filter = (
        "store",
        "brand",
        "gender",
        "is_active",
    )

    inlines = [ProductVariantInline]

    search_fields = (
        "name",
        "description",
    )


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "needs_human", "created_at")
    list_filter = ("store", "needs_human", "created_at")
    actions = ["trigger_evaluation"]

    @admin.action(description="Run LLM Judge Evaluation on selected conversations")
    def trigger_evaluation(self, request, queryset):
        from products.services.ai.evaluator_service import evaluate_conversation
        count = 0
        for conversation in queryset:
            if evaluate_conversation(conversation):
                count += 1
        self.message_user(request, f"Successfully evaluated {count} conversations.")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conversation",
        "role",
        "created_at",
    )

    list_filter = (
        "role",
    )

@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "api_key", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "api_key")

@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "whatsapp_number")

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "customer_name", "customer_phone", "secondary_phone", "total_price", "status", "created_at")
    list_filter = ("store", "status")
    search_fields = ("customer_name", "customer_phone", "secondary_phone")
    inlines = [OrderItemInline]

@admin.register(ConversationEvaluation)
class ConversationEvaluationAdmin(admin.ModelAdmin):
    list_display = ("conversation", "overall_score", "has_hallucination", "created_at")
    list_filter = ("has_hallucination", "overall_score", "created_at")

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "type", "title", "is_read", "created_at")
    list_filter = ("store", "type", "is_read")
    search_fields = ("title", "message")

@admin.register(StaticFAQ)
class StaticFAQAdmin(admin.ModelAdmin):
    list_display = ("question", "store", "priority", "is_active", "created_at")
    list_filter = ("store", "is_active")
    search_fields = ("question", "keywords", "answer")
    list_editable = ("priority", "is_active")