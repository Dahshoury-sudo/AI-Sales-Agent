from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from products.models import Notification
from .auth_backend import StoreOwnerAuthentication


class NotificationListView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        notifications = Notification.objects.filter(store=store)[:50]

        unread_count = Notification.objects.filter(store=store, is_read=False).count()

        data = [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ]

        return Response({"notifications": data, "unread_count": unread_count})


class NotificationMarkReadView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store = request.store
        Notification.objects.filter(store=store, is_read=False).update(is_read=True)
        return Response({"status": "All notifications marked as read."})
