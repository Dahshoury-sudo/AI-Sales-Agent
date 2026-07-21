import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction

from products.models import Product, ProductVariant, Brand, Category
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)


class ProductListView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        search = request.GET.get("search", "").strip()
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("per_page", 20))

        products = Product.objects.filter(store=store).select_related("brand", "category")

        if search:
            products = products.filter(name__icontains=search)

        total = products.count()
        start = (page - 1) * per_page
        end = start + per_page
        products = products.order_by("-created_at")[start:end]

        data = []
        for p in products:
            variants = []
            for v in p.variants.all():
                variants.append({
                    "id": v.id,
                    "volume": v.volume,
                    "price": str(v.price),
                    "bottle_type": v.bottle_type,
                    "stock": v.stock,
                })
            data.append({
                "id": p.id,
                "name": p.name,
                "brand": p.brand.name if p.brand else "",
                "category": p.category.name if p.category else "",
                "gender": p.gender,
                "is_active": p.is_active,
                "oil_stock_grams": p.oil_stock_grams,
                "variants": variants,
                "created_at": p.created_at.isoformat(),
            })

        return Response({
            "products": data,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        })


class ProductDetailView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, product_id):
        store = request.store
        try:
            p = Product.objects.get(id=product_id, store=store)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=404)

        variants = []
        for v in p.variants.all():
            variants.append({
                "id": v.id,
                "volume": v.volume,
                "price": str(v.price),
                "bottle_type": v.bottle_type,
                "stock": v.stock,
            })

        return Response({
            "id": p.id,
            "name": p.name,
            "brand": p.brand.name if p.brand else "",
            "category": p.category.name if p.category else "",
            "gender": p.gender,
            "season": p.season,
            "occasion": p.occasion,
            "longevity": p.longevity,
            "projection": p.projection,
            "concentration": p.concentration,
            "top_notes": p.top_notes,
            "middle_notes": p.middle_notes,
            "base_notes": p.base_notes,
            "description": p.description,
            "oil_stock_grams": p.oil_stock_grams,
            "concentration_percentage": p.concentration_percentage,
            "is_active": p.is_active,
            "variants": variants,
        })

    def put(self, request, product_id):
        store = request.store
        try:
            product = Product.objects.get(id=product_id, store=store)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=404)

        simple_fields = [
            "name", "gender", "season", "occasion", "longevity", "projection",
            "concentration", "top_notes", "middle_notes", "base_notes",
            "description", "oil_stock_grams", "concentration_percentage", "is_active",
        ]

        for field in simple_fields:
            value = request.data.get(field)
            if value is not None:
                setattr(product, field, value)

        product.save()

        # Update variants if provided
        variants_data = request.data.get("variants")
        if variants_data:
            for v_data in variants_data:
                if "id" in v_data:
                    try:
                        variant = ProductVariant.objects.get(id=v_data["id"], product=product)
                        for k in ["volume", "price", "bottle_type", "stock"]:
                            if k in v_data:
                                setattr(variant, k, v_data[k])
                        variant.save()
                    except ProductVariant.DoesNotExist:
                        pass
                else:
                    ProductVariant.objects.create(
                        product=product,
                        volume=v_data.get("volume", 0),
                        price=v_data.get("price", 0),
                        bottle_type=v_data.get("bottle_type", "normal"),
                        stock=v_data.get("stock"),
                    )

        return Response({"message": "Product updated."})

    def delete(self, request, product_id):
        store = request.store
        try:
            product = Product.objects.get(id=product_id, store=store)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=404)

        product.is_active = False
        product.save()
        return Response({"message": "Product deactivated."})


class ProductCreateView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store = request.store
        data = request.data

        name = data.get("name", "").strip()
        if not name:
            return Response({"error": "Product name is required."}, status=400)

        brand_name = data.get("brand", "").strip()
        category_name = data.get("category", "").strip()

        try:
            with transaction.atomic():
                brand, _ = Brand.objects.get_or_create(store=store, name=brand_name) if brand_name else (None, False)
                category, _ = Category.objects.get_or_create(store=store, name=category_name) if category_name else (None, False)

                product = Product.objects.create(
                    store=store,
                    name=name,
                    brand=brand,
                    category=category,
                    gender=data.get("gender", "unisex"),
                    season=data.get("season", ""),
                    occasion=data.get("occasion", ""),
                    longevity=data.get("longevity", ""),
                    projection=data.get("projection", ""),
                    concentration=data.get("concentration", ""),
                    top_notes=data.get("top_notes", ""),
                    middle_notes=data.get("middle_notes", ""),
                    base_notes=data.get("base_notes", ""),
                    description=data.get("description", ""),
                    oil_stock_grams=data.get("oil_stock_grams", 0),
                    concentration_percentage=data.get("concentration_percentage", 30),
                )

                for v_data in data.get("variants", []):
                    ProductVariant.objects.create(
                        product=product,
                        volume=v_data.get("volume", 0),
                        price=v_data.get("price", 0),
                        bottle_type=v_data.get("bottle_type", "normal"),
                        stock=v_data.get("stock"),
                    )

            return Response({"message": "Product created.", "id": product.id}, status=201)

        except Exception as e:
            logger.exception(f"Product creation error: {e}")
            return Response({"error": "Failed to create product."}, status=500)
