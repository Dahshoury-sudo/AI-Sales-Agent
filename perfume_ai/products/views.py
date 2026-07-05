from rest_framework.generics import ListAPIView
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Product
from .serializers import ProductSerializer

from .services.router import route
from .services.conversation_service import (
    create_conversation,
    save_message,
)


class ProductListView(ListAPIView):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductSerializer


class ChatAPIView(APIView):

    def post(self, request):

        message = request.data.get("message")

        if not message:
            return Response(
                {"error": "message is required"},
                status=400
            )

        try:

            conversation = create_conversation()

            save_message(
                conversation,
                "user",
                message
            )

            reply = route(message)

            save_message(
                conversation,
                "assistant",
                reply
            )

            return Response({
                "conversation_id": conversation.id,
                "reply": reply
            })

        except Exception as e:

            return Response(
                {"error": str(e)},
                status=500
            )