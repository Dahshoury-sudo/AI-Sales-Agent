from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Count, Sum

from products.models import Conversation, Order
from .auth_backend import StoreOwnerAuthentication


class DashboardOverviewView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store

        total_conversations = Conversation.objects.filter(store=store).count()
        total_orders = Order.objects.filter(store=store).count()
        pending_orders = Order.objects.filter(store=store, status="pending").count()
        pending_handoffs = Conversation.objects.filter(store=store, needs_human=True).count()

        # Platform breakdown
        platform_stats = (
            Conversation.objects.filter(store=store)
            .values("platform")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Revenue
        revenue = Order.objects.filter(
            store=store, status__in=["confirmed", "delivered"]
        ).aggregate(total=Sum("total_price"))

        return Response({
            "total_conversations": total_conversations,
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "pending_handoffs": pending_handoffs,
            "revenue": str(revenue["total"] or 0),
            "platform_breakdown": {s["platform"]: s["count"] for s in platform_stats},
        })
