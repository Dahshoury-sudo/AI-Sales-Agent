from django.urls import path
from .views import (
    ProductListView, ChatAPIView, AnalyticsAPIView, ChatDemoView,
    OrdersDashboardView, OrdersDashboardAPIView, OrderStatusUpdateView,
)

urlpatterns = [
    path("products/", ProductListView.as_view()),
    path("chat/", ChatAPIView.as_view()),
    path("analytics/", AnalyticsAPIView.as_view()),
    path("demo/", ChatDemoView.as_view()),
    path("orders/", OrdersDashboardView.as_view(), name="orders-dashboard"),
    path("orders/dashboard/", OrdersDashboardAPIView.as_view(), name="orders-dashboard-api"),
    path("orders/<int:order_id>/status/", OrderStatusUpdateView.as_view(), name="order-status-update"),
]