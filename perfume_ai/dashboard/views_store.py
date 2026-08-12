import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from products.models import StoreSettings
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)


class StoreSettingsView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = request.store
        try:
            settings = store.settings
        except StoreSettings.DoesNotExist:
            settings = StoreSettings.objects.create(store=store)

        return Response({
            "system_prompt": settings.system_prompt,
            "whatsapp_number": settings.whatsapp_number,
            "meta_verify_token": settings.meta_verify_token,
            "meta_access_token": "••••••••" if settings.meta_access_token else "",
            "messenger_access_token": "••••••••" if settings.messenger_access_token else "",
            "meta_app_secret": "••••••••" if settings.meta_app_secret else "",
            "facebook_page_id": settings.facebook_page_id,
            "instagram_account_id": settings.instagram_account_id,
            "whatsapp_phone_number_id": settings.whatsapp_phone_number_id,
            "comment_reply_messages": settings.comment_reply_messages,
        })

    def put(self, request):
        store = request.store
        try:
            settings = store.settings
        except StoreSettings.DoesNotExist:
            settings = StoreSettings.objects.create(store=store)

        updatable_fields = [
            "system_prompt", "whatsapp_number", "meta_verify_token",
            "meta_access_token", "messenger_access_token", "meta_app_secret",
            "facebook_page_id", "instagram_account_id", "whatsapp_phone_number_id",
            "comment_reply_messages",
        ]

        for field in updatable_fields:
            value = request.data.get(field)
            if value is not None:
                # Don't overwrite with masked value
                if value == "••••••••":
                    continue
                setattr(settings, field, value)

        settings.save()
        return Response({"message": "Settings updated successfully."})


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
