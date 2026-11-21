from django.contrib import admin
from django import forms
from django.db.models import DecimalField, OuterRef, Subquery, Sum
from django.db.models.functions import ExtractMonth, ExtractYear
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from .scent_tags import SCENT_TAG_CHOICES, normalize_scent_tags

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
    AIUsageDaily,
    PostCommentRule,
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

class ProductAdminForm(forms.ModelForm):
    scent_tags = forms.MultipleChoiceField(choices=SCENT_TAG_CHOICES, required=False,
                                           widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Product
        fields = "__all__"

    def clean_scent_tags(self):
        return normalize_scent_tags(self.cleaned_data["scent_tags"])


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
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
        from products.services.ai import usage as ai_usage
        from products.services.ai.evaluator_service import evaluate_conversation
        count = 0
        # Your own QA spend: recorded, but not against the store whose chat is judged.
        with ai_usage.attribute(None, "admin_evaluation"):
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

    def get_readonly_fields(self, request, obj=None):
        return ("status",) if obj and obj.status == "cancelled" else ()

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            from .services.order_service import change_order_status
            from django.db import transaction
            with transaction.atomic():
                updated = change_order_status(obj.pk, obj.store, obj.status)
                obj.status = updated.status
                super().save_model(request, obj, form, change)
        else:
            super().save_model(request, obj, form, change)

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


def monthly_ai_cost(store_ref, period_ref):
    """Subquery: a store's AI cost (USD) for the calendar month starting at `period_ref`."""
    total = (AIUsageDaily.objects
             .filter(store=store_ref, date__year=ExtractYear(period_ref), date__month=ExtractMonth(period_ref))
             .values("store").annotate(total=Sum("cost_usd")).values("total"))
    return Subquery(total, output_field=DecimalField(max_digits=14, decimal_places=8))


@admin.register(StoreMonthlyUsage)
class StoreMonthlyUsageAdmin(admin.ModelAdmin):
    list_display = ("store", "period", "llm_messages", "ai_cost", "cost_per_message",
                    "warned_at_80", "warned_at_cap")
    list_filter = ("store", "period")
    # Counters are written by the message path; editing them by hand would desync
    # billing from what actually ran.
    readonly_fields = ("store", "period", "llm_messages", "ai_cost", "cost_per_message",
                       "usage_breakdown", "updated_at")
    fields = ("store", "period", "llm_messages", "ai_cost", "cost_per_message", "usage_breakdown",
              "warned_at_80", "warned_at_cap", "updated_at")

    @admin.display(description="AI usage by channel")
    def usage_breakdown(self, obj):
        rows = (AIUsageDaily.objects
                .filter(store=obj.store, date__year=obj.period.year, date__month=obj.period.month)
                .values("channel")
                .annotate(**{name: Sum(name) for name in (
                    "calls", "failed_calls", "unpriced_calls", "prompt_tokens", "cached_tokens",
                    "completion_tokens", "cost_usd")})
                .order_by("-cost_usd"))
        if not rows:
            return "No AI usage recorded for this month (cost tracking starts with the release that added it)."
        cells = []
        for row in rows:
            cached = f"{row['cached_tokens'] / row['prompt_tokens']:.0%}" if row["prompt_tokens"] else "-"
            problems = ", ".join(f"{row[k]} {label}" for k, label in (
                ("failed_calls", "failed"), ("unpriced_calls", "unpriced")) if row[k])
            cells.append((row["channel"], row["calls"], f"{row['prompt_tokens']:,}", cached,
                          f"{row['completion_tokens']:,}", f"${row['cost_usd']:.4f}", problems or "-"))
        head = "".join(f"<th>{label}</th>" for label in (
            "Channel", "Model calls", "Input tokens", "Cached", "Output tokens", "Cost USD", "Problems"))
        body = format_html_join("", "<tr>" + "<td>{}</td>" * 7 + "</tr>", cells)
        return format_html('<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>', mark_safe(head), body)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            ai_cost_usd=monthly_ai_cost(OuterRef("store"), OuterRef("period")))

    @admin.display(description="AI cost (USD)", ordering="ai_cost_usd")
    def ai_cost(self, obj):
        return f"${obj.ai_cost_usd:.4f}" if obj.ai_cost_usd is not None else "-"

    @admin.display(description="Cost per AI message (USD)")
    def cost_per_message(self, obj):
        if obj.ai_cost_usd is None or not obj.llm_messages:
            return "-"
        return f"${obj.ai_cost_usd / obj.llm_messages:.5f}"


@admin.register(AIUsageDaily)
class AIUsageDailyAdmin(admin.ModelAdmin):
    """Read-only: rows are written by every model call, so hand edits would desync them."""
    list_display = ("date", "store_label", "channel", "calls", "failed_calls", "unpriced_calls",
                    "prompt_tokens", "cached_tokens", "completion_tokens", "cost_usd")
    list_filter = ("channel", "store")
    date_hierarchy = "date"

    @admin.display(description="Store", ordering="store")
    def store_label(self, obj):
        return obj.store or "No store (admin evaluations)"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PostCommentRule)
class PostCommentRuleAdmin(admin.ModelAdmin):
    list_display = ("post_id", "label", "store", "platform", "is_active", "created_at")
    list_filter = ("store", "platform", "is_active")
    search_fields = ("post_id", "label")
    list_editable = ("is_active",)
