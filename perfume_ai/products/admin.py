from django.contrib import admin

from .models import (
    Brand,
    Category,
    Product,
    Conversation,
    Message,
)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "country")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "brand",
        "price",
        "stock",
        "is_active",
    )

    list_filter = (
        "brand",
        "gender",
        "is_active",
    )

    search_fields = (
        "name",
        "description",
    )


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
    )


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