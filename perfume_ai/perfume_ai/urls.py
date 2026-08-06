"""
URL configuration for perfume_ai project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView
from products.views import InternalDashboardView, WidgetTestView

urlpatterns = [
    # Redirect frontend pages to Vercel domain
    path("", RedirectView.as_view(url="https://webvitas.com", permanent=True), name="home"),
    path("terms/", RedirectView.as_view(url="https://webvitas.com/terms.html", permanent=True), name="terms"),
    path("privacy/", RedirectView.as_view(url="https://webvitas.com/privacy.html", permanent=True), name="privacy"),
    
    path("internal/", InternalDashboardView.as_view(), name="internal-dashboard"),
    path("widget-test/", WidgetTestView.as_view(), name="widget-test"),
    path("admin/", admin.site.urls),
    path("api/", include("products.urls")),
    path("dashboard/", include("dashboard.urls")),
]
