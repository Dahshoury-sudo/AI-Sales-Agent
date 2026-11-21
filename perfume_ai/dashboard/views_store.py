import logging
from django.urls import reverse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from products.models import StoreSettings
from products.services.meta_connection import check_connection, page_token
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)

REPLY_SWITCHES = (
    "whatsapp_replies_enabled", "messenger_replies_enabled", "instagram_replies_enabled",
    "facebook_comment_replies_enabled", "instagram_comment_replies_enabled",
)


def meta_webhook_url(request):
    """The Callback URL to paste into the Meta app's webhook settings.

    Meta only calls HTTPS URLs, so a public host is always shown as https even when the
    request reached Django as http behind a proxy that doesn't set X-Forwarded-Proto.
    """
    host = request.get_host()
    local = host.split(":")[0] in ("localhost", "127.0.0.1")
    scheme = request.scheme if local else "https"
    return f"{scheme}://{host}{reverse('meta-webhook')}"


class StoreSettingsView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        try:
            settings = store.settings
        except StoreSettings.DoesNotExist:
            settings, _ = StoreSettings.objects.get_or_create(store=store)

        return Response({
            "system_prompt": settings.system_prompt,
            "whatsapp_number": settings.whatsapp_number,
            "webhook_url": meta_webhook_url(request),
            "meta_verify_token": settings.meta_verify_token,
            "meta_access_token": "••••••••" if settings.meta_access_token else "",
            "messenger_access_token": "••••••••" if settings.messenger_access_token else "",
            "meta_app_secret": "••••••••" if settings.meta_app_secret else "",
            "facebook_page_id": settings.facebook_page_id,
            "instagram_account_id": settings.instagram_account_id,
            "whatsapp_phone_number_id": settings.whatsapp_phone_number_id,
            "comment_reply_messages": settings.comment_reply_messages,
            "payment_instructions": settings.payment_instructions,
            "bottle_image_url": settings.bottle_image_url,
            "business_facts": settings.business_facts,
            "promotions": settings.promotions,
            "website_ordering_enabled": settings.website_ordering_enabled,
            **{field: getattr(settings, field) for field in REPLY_SWITCHES},
        })

    def put(self, request):
        store = request.store
        for field in ("website_ordering_enabled", *REPLY_SWITCHES):
            if field in request.data and type(request.data[field]) is not bool:
                return Response({field: "Must be true or false."}, status=400)
        try:
            settings = store.settings
        except StoreSettings.DoesNotExist:
            settings, _ = StoreSettings.objects.get_or_create(store=store)

        updatable_fields = [
            "system_prompt", "whatsapp_number", "meta_verify_token",
            "meta_access_token", "messenger_access_token", "meta_app_secret",
            "facebook_page_id", "instagram_account_id", "whatsapp_phone_number_id",
            "comment_reply_messages", "payment_instructions",
            "bottle_image_url", "business_facts", "promotions",
            "website_ordering_enabled",
            *REPLY_SWITCHES,
        ]

        updated_fields = []
        for field in updatable_fields:
            value = request.data.get(field)
            if value is not None:
                # Don't overwrite with masked value
                if value == "••••••••":
                    continue
                setattr(settings, field, value)
                updated_fields.append(field)

        settings.save(update_fields=updated_fields)
        return Response({"message": "Settings updated successfully."})


class MetaConnectionCheckView(APIView):
    """Check the store's Facebook Page + Instagram connection against Meta and fix what it can."""
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        settings, _ = StoreSettings.objects.get_or_create(store=request.store)
        if not page_token(settings):
            return Response({"error": "حط توكن الصفحة (Page Token) واحفظ الإعدادات الأول."}, status=400)

        checks, changed = check_connection(settings, fix=True)
        if changed:
            settings.save(update_fields=changed)
        return Response({
            "checks": checks,
            "changed": changed,
            "facebook_page_id": settings.facebook_page_id,
            "instagram_account_id": settings.instagram_account_id,
        })


class StoreAPIKeyView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        store = request.store
        from products.models import generate_api_key
        store.api_key = generate_api_key()
        store.save()
        return Response({
            "message": "API key regenerated.",
            "api_key": store.api_key,
        })
