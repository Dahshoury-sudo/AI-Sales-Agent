import logging
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView
from rest_framework.response import Response
from django.views.generic import TemplateView
from django.http import HttpResponse
from django.db import transaction
from django.conf import settings

logger = logging.getLogger(__name__)

from django.db.models import Avg, Count
from .models import Product, ConversationEvaluation, Order, Conversation
from .serializers import ProductSerializer
from .auth import StoreAPIKeyAuthentication
from dashboard.auth_backend import StoreOwnerAuthentication
from rest_framework.permissions import IsAuthenticated
from .services.router import route
from .throttles import ChatThrottle, StoreKeyThrottle

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


class HomeView(TemplateView):
    template_name = 'products/home.html'


class InternalDashboardView(TemplateView):
    template_name = 'products/internal_dashboard.html'


class TermsView(TemplateView):
    template_name = 'products/terms.html'


class PrivacyView(TemplateView):
    template_name = 'products/privacy.html'

class ChatAPIView(APIView):
    authentication_classes = [StoreAPIKeyAuthentication]
    throttle_classes = [ChatThrottle]

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

            image_url = None
            if "[SEND_BOTTLE_IMAGE]" in reply:
                reply = reply.replace("[SEND_BOTTLE_IMAGE]", "").strip()
                image_url = settings.BOTTLE_IMAGE_URL

            save_message(
                conversation,
                "assistant",
                reply,
                internal_context=context
            )

            return Response({
                "conversation_id": conversation.id,
                "reply": reply,
                "image_url": image_url
            })

        except Exception as e:
            logger.exception(f"Chat error for store '{store.name}': {e}")
            return Response(
                {"error": "حصل مشكلة غير متوقعة. لو المشكلة استمرت تواصل مع الدعم الفني."},
                status=500
            )

class AnalyticsAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

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
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

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
                "secondary_phone": order.secondary_phone,
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
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

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

        old_status = order.status

        with transaction.atomic():
            order.status = new_status
            order.save()

            # Restore stock if the order is being cancelled (and wasn't already cancelled)
            if new_status == "cancelled" and old_status != "cancelled":
                from products.services.order_service import restore_stock
                restore_stock(order)

        logger.info(f"Order #{order.id} status changed: {old_status} -> {new_status} (store: {store.name})")
        return Response({"id": order.id, "status": order.status})


class BulkImportView(TemplateView):
    template_name = 'products/bulk_import.html'


class AnalyticsDashboardView(TemplateView):
    template_name = 'products/analytics.html'


class BulkImportAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store = request.store
        file = request.FILES.get("file")

        if not file:
            return Response({"error": "لازم ترفع ملف Excel (.xlsx)"}, status=400)

        if not file.name.endswith(".xlsx"):
            return Response({"error": "الملف لازم يكون بصيغة .xlsx"}, status=400)

        from .services.bulk_import import parse_excel

        file_bytes = file.read()
        results = parse_excel(file_bytes, store)

        return Response(results)


class BulkImportTemplateView(APIView):
    """Download an empty Excel template with the correct headers."""

    def get(self, request):
        import openpyxl
        from io import BytesIO

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Products"

        headers = [
            "name", "brand", "category", "gender", "perfume_type",
            "season", "occasion", "longevity", "projection",
            "concentration", "top_notes", "middle_notes", "base_notes",
            "description",
            "oil_stock_grams", "concentration_percentage",
            "norm_vol_1", "norm_price_1",
            "norm_vol_2", "norm_price_2",
            "norm_vol_3", "norm_price_3",
            "orig_vol_1", "orig_price_1", "orig_stock_1",
            "orig_vol_2", "orig_price_2", "orig_stock_2",
        ]
        ws.append(headers)

        # Example row
        ws.append([
            "Bleu de Chanel", "Chanel", "Perfume", "male", "western",
            "All Seasons", "Casual", "8 hours", "Moderate",
            "EDP", "Citrus, Mint", "Jasmine, Ginger", "Cedar, Sandalwood",
            "A fresh and woody fragrance",
            1000, 30,
            50, 500,
            100, 900,
            150, 1200,
            100, 5000, 5,
            200, 9000, 2
        ])

        # Auto-size columns
        for col in ws.columns:
            max_length = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = max_length + 2

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="products_template.xlsx"'
        return response

class HandoffDashboardView(TemplateView):
    template_name = 'products/handoff_dashboard.html'

class HandoffConversationsAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [StoreKeyThrottle]

    def get(self, request):
        store = request.store
        convs = Conversation.objects.filter(store=store, needs_human=True).prefetch_related('messages').order_by('-created_at')
        
        data = []
        for c in convs:
            msgs = list(c.messages.all())
            last_msg = sorted(msgs, key=lambda m: m.created_at, reverse=True)[0] if msgs else None
            data.append({
                "id": c.id,
                "platform": c.platform,
                "platform_sender_id": c.platform_sender_id,
                "created_at": c.created_at.isoformat(),
                "last_message": last_msg.content if last_msg else "No messages"
            })
            
        return Response({"conversations": data})

class HandoffMessagesAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [StoreKeyThrottle]

    def get(self, request, conversation_id):
        store = request.store
        try:
            conv = Conversation.objects.get(id=conversation_id, store=store)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=404)
            
        msgs = conv.messages.order_by('created_at')
        data = []
        for m in msgs:
            data.append({
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat()
            })
            
        return Response({
            "conversation_id": conv.id,
            "needs_human": conv.needs_human,
            "messages": data
        })

class HandoffReplyAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [StoreKeyThrottle]

    def post(self, request, conversation_id):
        store = request.store
        message = request.data.get("message")
        
        if not message:
            return Response({"error": "Message is required"}, status=400)
            
        try:
            conv = Conversation.objects.get(id=conversation_id, store=store)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=404)
            
        if not conv.needs_human:
            conv.needs_human = True
            conv.save()
            
        save_message(conv, "assistant", message)
        
        # Send reply back to the customer on their platform
        from products.services.meta_service import send_platform_message
        try:
            send_platform_message(conv, message)
        except Exception as e:
            logger.exception(f"Failed to send handoff reply to {conv.platform}: {e}")
        
        return Response({"status": "Message sent"})

class HandoffResolveAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [StoreKeyThrottle]

    def post(self, request, conversation_id):
        store = request.store
        try:
            conv = Conversation.objects.get(id=conversation_id, store=store)
        except Conversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=404)
            
        conv.needs_human = False
        conv.save()
        
        return Response({"status": "Conversation resolved"})

class ChatHistoryDashboardView(TemplateView):
    template_name = 'products/chat_history.html'

class ChatHistoryConversationsAPIView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [StoreKeyThrottle]

    def get(self, request):
        store = request.store
        # Get all conversations for this store, ordered by newest first
        convs = Conversation.objects.filter(store=store).prefetch_related('messages').order_by('-created_at')
        
        data = []
        for c in convs:
            msgs = list(c.messages.all())
            last_msg = sorted(msgs, key=lambda m: m.created_at, reverse=True)[0] if msgs else None
            data.append({
                "id": c.id,
                "platform": c.platform,
                "platform_sender_id": c.platform_sender_id,
                "needs_human": c.needs_human,
                "created_at": c.created_at.isoformat(),
                "last_message": last_msg.content if last_msg else "No messages"
            })
            
        return Response({"conversations": data})