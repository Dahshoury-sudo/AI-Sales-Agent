from django.urls import path
from .views import ProductListView, ChatAPIView, AnalyticsAPIView, ChatDemoView

urlpatterns = [
    path("products/", ProductListView.as_view()),
    path("chat/", ChatAPIView.as_view()),
    path("analytics/", AnalyticsAPIView.as_view()),
    path("demo/", ChatDemoView.as_view()),
]