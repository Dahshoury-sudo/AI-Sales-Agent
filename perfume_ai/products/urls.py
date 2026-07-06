from django.urls import path
from .views import ProductListView, ChatAPIView, AnalyticsAPIView

urlpatterns = [
    path("products/", ProductListView.as_view()),
    path("chat/", ChatAPIView.as_view()),
    path("analytics/", AnalyticsAPIView.as_view()),
]