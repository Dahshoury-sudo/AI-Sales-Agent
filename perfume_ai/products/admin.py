from django.contrib import admin
from .models import Brand, Category, Product

@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "country")
    search_fields = ("name",)


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
        "gender",
        "stock",
        "is_active",
    )

    list_filter = (
        "gender",
        "brand",
        "category",
        "is_active",
    )

    search_fields = (
        "name",
        "description",
        "top_notes",
        "middle_notes",
        "base_notes",
    )