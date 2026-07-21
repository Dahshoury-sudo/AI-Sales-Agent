from django.urls import path
from django.views.generic import TemplateView
from rest_framework_simplejwt.views import TokenRefreshView

from .views_auth import RegisterView, LoginView, ProfileView, ForgotPasswordView, ResetPasswordView
from .views_store import StoreSettingsView, StoreAPIKeyView
from .views_products import ProductListView, ProductDetailView, ProductCreateView
from .views_analytics import DashboardOverviewView
from .views_notifications import NotificationListView, NotificationMarkReadView

urlpatterns = [
    # --- Page views ---
    path("login/", TemplateView.as_view(template_name="dashboard/login.html"), name="dashboard-login"),
    path("register/", TemplateView.as_view(template_name="dashboard/register.html"), name="dashboard-register"),
    path("forgot-password/", TemplateView.as_view(template_name="dashboard/forgot_password.html"), name="dashboard-forgot-password"),
    path("reset-password/<uidb64>/<token>/", TemplateView.as_view(template_name="dashboard/reset_password.html"), name="dashboard-reset-password"),
    path("", TemplateView.as_view(template_name="dashboard/store_dashboard.html"), name="dashboard-home"),

    # --- Auth API ---
    path("api/register/", RegisterView.as_view(), name="dashboard-api-register"),
    path("api/login/", LoginView.as_view(), name="dashboard-api-login"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="dashboard-api-token-refresh"),
    path("api/profile/", ProfileView.as_view(), name="dashboard-api-profile"),
    path("api/forgot-password/", ForgotPasswordView.as_view(), name="dashboard-api-forgot-password"),
    path("api/reset-password/<uidb64>/<token>/", ResetPasswordView.as_view(), name="dashboard-api-reset-password"),

    # --- Store Settings API ---
    path("api/settings/", StoreSettingsView.as_view(), name="dashboard-api-settings"),
    path("api/settings/regenerate-key/", StoreAPIKeyView.as_view(), name="dashboard-api-regenerate-key"),

    # --- Products API ---
    path("api/products/", ProductListView.as_view(), name="dashboard-api-products"),
    path("api/products/create/", ProductCreateView.as_view(), name="dashboard-api-product-create"),
    path("api/products/<int:product_id>/", ProductDetailView.as_view(), name="dashboard-api-product-detail"),

    # --- Dashboard Overview API ---
    path("api/overview/", DashboardOverviewView.as_view(), name="dashboard-api-overview"),

    # --- Notifications API ---
    path("api/notifications/", NotificationListView.as_view(), name="dashboard-api-notifications"),
    path("api/notifications/mark-read/", NotificationMarkReadView.as_view(), name="dashboard-api-notifications-mark-read"),
]
