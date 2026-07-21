from django.urls import path
from .views import (
    ProductListView, ChatAPIView, AnalyticsAPIView, ChatDemoView,
    OrdersDashboardView, OrdersDashboardAPIView, OrderStatusUpdateView,
    BulkImportView, BulkImportAPIView, BulkImportTemplateView,
    AnalyticsDashboardView, HandoffDashboardView, HandoffConversationsAPIView,
    HandoffMessagesAPIView, HandoffReplyAPIView, HandoffResolveAPIView,
)
from .views_meta import MetaWebhookView

urlpatterns = [
    path("products/", ProductListView.as_view()),
    path("chat/", ChatAPIView.as_view()),
    path("analytics/data/", AnalyticsAPIView.as_view(), name="analytics-api"),
    path("analytics/", AnalyticsDashboardView.as_view(), name="analytics-dashboard"),
    path("demo/", ChatDemoView.as_view()),
    path("orders/", OrdersDashboardView.as_view(), name="orders-dashboard"),
    path("orders/dashboard/", OrdersDashboardAPIView.as_view(), name="orders-dashboard-api"),
    path("orders/<int:order_id>/status/", OrderStatusUpdateView.as_view(), name="order-status-update"),
    path("import/", BulkImportView.as_view(), name="bulk-import"),
    path("import/upload/", BulkImportAPIView.as_view(), name="bulk-import-api"),
    path("import/template/", BulkImportTemplateView.as_view(), name="bulk-import-template"),
    path("handoff/", HandoffDashboardView.as_view(), name="handoff-dashboard"),
    path("handoff/conversations/", HandoffConversationsAPIView.as_view(), name="handoff-conversations"),
    path("handoff/conversations/<int:conversation_id>/messages/", HandoffMessagesAPIView.as_view(), name="handoff-messages"),
    path("handoff/conversations/<int:conversation_id>/reply/", HandoffReplyAPIView.as_view(), name="handoff-reply"),
    path("handoff/conversations/<int:conversation_id>/resolve/", HandoffResolveAPIView.as_view(), name="handoff-resolve"),
    path("webhooks/meta/", MetaWebhookView.as_view(), name="meta-webhook"),
]