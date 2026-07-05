from django.shortcuts import render
from rest_framework.generics import ListAPIView
from .models import Product
from .serializers import ProductSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from .services.search_service import search_products
from .services.ai_service import ask_ai

class ProductListView(ListAPIView):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer



class ChatAPIView(APIView):

    def post(self, request):
        message = request.data.get("message")

        if not message:
            return Response({"error": "message is required"}, status=400)

        products = search_products(message)
        reply = ask_ai(message, products)

        return Response({
            "reply": reply
        })