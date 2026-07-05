from django.urls import path
from .views import ProductListView, ChatAPIView

urlpatterns = [
    path("products/", ProductListView.as_view()),
    path("chat/", ChatAPIView.as_view()),
]