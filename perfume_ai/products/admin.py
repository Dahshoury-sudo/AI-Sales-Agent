from django.contrib import admin

from .encryption import normalize_phone, phone_blind_index

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
    StoreMonthlyUsage,
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
    # Phone fields are gone from here on purpose. They are encrypted with a
    # non-deterministic cipher, so an icontains lookup encrypts the search term into
    # ciphertext that matches nothing — the search box would silently return no
    # results. Phone lookup is served by get_search_results below, via the blind
    # index. list_display is unaffected: that reads instances, which decrypt normally.
    search_fields = ("customer_name",)
    inlines = [OrderItemInline]

    def get_search_results(self, request, queryset, search_term):
        """Let a store owner still find an order by phone number.

        Exact match only — that is all a blind index can do, and it is what looking up
        "the order for 01000000000" needs. Normalization means any spelling of the same
        number finds it.
        """
        queryset, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )

        # Enough digits to be a phone number rather than a name. normalize_phone
        # already strips formatting, so this reuses it instead of pulling a private
        # helper out of the order service and dragging its imports into the admin.
        if len(normalize_phone(search_term)) >= 7:
            queryset |= self.get_queryset(request).filter(
                customer_phone_hash=phone_blind_index(search_term)
            )

        return queryset, may_have_duplicates

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


@admin.register(StoreMonthlyUsage)
class StoreMonthlyUsageAdmin(admin.ModelAdmin):
    list_display = ("store", "period", "llm_messages", "warned_at_80", "warned_at_cap")
    list_filter = ("store", "period")
    # Counters are written by the message path; editing them by hand would desync
    # billing from what actually ran.
    readonly_fields = ("store", "period", "llm_messages", "updated_at")