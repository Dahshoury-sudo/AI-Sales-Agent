from rest_framework.generics import ListAPIView
from rest_framework.views import APIView
from rest_framework.response import Response
from django.views.generic import TemplateView

from django.db.models import Avg, Count
from .models import Product, ConversationEvaluation, Order, Conversation
from .serializers import ProductSerializer
from .auth import StoreAPIKeyAuthentication
from .services.router import route

from .services.conversation_service import (
    create_conversation,
    get_conversation,
    save_message,
    get_conversation_messages,
)


class ProductListView(ListAPIView):
    authentication_classes = [StoreAPIKeyAuthentication]
    serializer_class = ProductSerializer

    def get_queryset(self):
        return Product.objects.filter(store=self.request.store, is_active=True)

class ChatDemoView(TemplateView):
    template_name = 'products/chat.html'

class ChatAPIView(APIView):
    authentication_classes = [StoreAPIKeyAuthentication]

    def post(self, request):

        message = request.data.get("message")
        conversation_id = request.data.get("conversation_id")
        store = request.store

        if not message:
            return Response(
                {"error": "message is required"},
                status=400
            )

        try:

            if conversation_id:
                conversation = get_conversation(conversation_id, store)
                if not conversation:
                    return Response(
                        {"error": "Conversation not found or does not belong to this store"},
                        status=404
                    )
                past_messages = get_conversation_messages(conversation)
                history = [{"role": msg.role, "content": msg.content} for msg in past_messages]
            else:
                conversation = create_conversation(store)
                history = []

            save_message(
                conversation,
                "user",
                message
            )

            if conversation.needs_human:
                return Response({
                    "conversation_id": conversation.id,
                    "reply": "",
                    "needs_human": True,
                    "info": "This conversation is currently handed over to a human agent."
                })

            reply, context = route(message, history, store, conversation)

            save_message(
                conversation,
                "assistant",
                reply,
                internal_context=context
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

class AnalyticsAPIView(APIView):
    authentication_classes = [StoreAPIKeyAuthentication]

    def get(self, request):
        store = request.store
        
        # Total counts
        total_convs = Conversation.objects.filter(store=store).count()
        total_orders = Order.objects.filter(store=store).count()
        handoff_convs = Conversation.objects.filter(store=store, needs_human=True).count()
        
        # Evaluations aggregation
        evals = ConversationEvaluation.objects.filter(conversation__store=store)
        total_evals = evals.count()
        
        if total_evals == 0:
            return Response({
                "overall_score": 0,
                "metrics": {
                    "intent_accuracy": 0,
                    "search_accuracy": 0,
                    "product_info": 0,
                    "comparison": 0,
                    "orders": 0,
                    "hallucination_rate": 0
                },
                "total_conversations": total_convs,
                "total_orders": total_orders,
                "conversion_rate": round((total_orders / total_convs * 100) if total_convs > 0 else 0, 1),
                "handoff_rate": round((handoff_convs / total_convs * 100) if total_convs > 0 else 0, 1)
            })

        avgs = evals.aggregate(
            avg_intent=Avg('intent_score'),
            avg_search=Avg('search_score'),
            avg_product=Avg('product_info_score'),
            avg_comparison=Avg('comparison_score'),
            avg_order=Avg('order_score'),
            avg_overall=Avg('overall_score')
        )
        
        hallucinations = evals.filter(has_hallucination=True).count()
        hallucination_rate = (hallucinations / total_evals) * 100
        
        return Response({
            "overall_score": round(avgs['avg_overall'] or 0, 1),
            "metrics": {
                "intent_accuracy": round(avgs['avg_intent'] or 0, 1),
                "search_accuracy": round(avgs['avg_search'] or 0, 1),
                "product_info": round(avgs['avg_product'] or 0, 1),
                "comparison": round(avgs['avg_comparison'] or 0, 1),
                "orders": round(avgs['avg_order'] or 0, 1),
                "hallucination_rate": round(hallucination_rate, 1)
            },
            "total_conversations": total_convs,
            "total_orders": total_orders,
            "conversion_rate": round((total_orders / total_convs * 100) if total_convs > 0 else 0, 1),
            "handoff_rate": round((handoff_convs / total_convs * 100) if total_convs > 0 else 0, 1)
        })


class OrdersDashboardView(TemplateView):
    template_name = 'products/orders_dashboard.html'


class OrdersDashboardAPIView(APIView):
    authentication_classes = [StoreAPIKeyAuthentication]

    def get(self, request):
        store = request.store
        orders = Order.objects.filter(store=store).prefetch_related(
            'items__variant__product'
        ).order_by('-created_at')

        orders_data = []
        for order in orders:
            items = []
            for item in order.items.all():
                items.append({
                    "product_name": item.variant.product.name,
                    "volume": item.variant.volume,
                    "quantity": item.quantity,
                    "unit_price": str(item.price_at_time_of_order),
                    "line_total": str(item.price_at_time_of_order * item.quantity),
                })
            orders_data.append({
                "id": order.id,
                "customer_name": order.customer_name,
                "customer_phone": order.customer_phone,
                "shipping_address": order.shipping_address,
                "total_price": str(order.total_price),
                "status": order.status,
                "bot_notes": order.bot_notes,
                "created_at": order.created_at.isoformat(),
                "items": items,
            })

        return Response({
            "store_name": store.name,
            "orders": orders_data,
        })


class OrderStatusUpdateView(APIView):
    authentication_classes = [StoreAPIKeyAuthentication]

    def patch(self, request, order_id):
        store = request.store
        new_status = request.data.get("status")

        valid_statuses = ["pending", "confirmed", "cancelled", "delivered"]
        if new_status not in valid_statuses:
            return Response({"error": "Invalid status"}, status=400)

        try:
            order = Order.objects.get(id=order_id, store=store)
        except Order.DoesNotExist:
            return Response({"error": "Order not found"}, status=404)

        order.status = new_status
        order.save()

        return Response({"id": order.id, "status": order.status})