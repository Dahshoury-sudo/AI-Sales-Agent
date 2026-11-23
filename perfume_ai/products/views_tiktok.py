import json
import logging

from django.http import HttpResponse
from rest_framework.views import APIView

from products.models import StoreSettings
from products.services import tiktok_service

logger = logging.getLogger(__name__)

# Shown in the inbox in place of a message the customer did not type.
SHARED_VIDEO_CAPTION = "🎬 شارك فيديو تيك توك"


class TikTokWebhookView(APIView):
    """TikTok's inbound webhook for DMs to every connected store.

    One URL per TikTok app, so this serves all stores: the app secret signs every
    request, and the store is found by the receiving business id. Like
    MetaWebhookView, it is unthrottled here, because a refusal makes TikTok retry
    and rate limiting belongs in products/tasks.py, where the store and sender are
    known.

    Unlike MetaWebhookView, a forged request gets 401 before the body is even parsed,
    and an unexpected error is left to become a 500. TikTok then resends, and
    InboundEvent turns the resend of an already-handled message into a no-op.
    """

    authentication_classes = []
    permission_classes = []
    # Explicitly empty: unset means DRF's AnonRateThrottle (30/minute per IP), and all
    # of TikTok's traffic comes from TikTok's addresses. See MetaWebhookView.
    throttle_classes = []

    def post(self, request):
        body = request.body
        if not tiktok_service.verify_signature(request.headers.get("Tiktok-Signature"), body):
            logger.warning("Rejecting TikTok webhook: bad or missing signature")
            return HttpResponse("Invalid signature", status=401)
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return HttpResponse("Invalid JSON", status=400)

        for message in tiktok_service.parse_webhook(payload):
            store_settings = (StoreSettings.objects.select_related("store")
                              .filter(tiktok_business_id=message.business_id).first())
            if store_settings is None:
                logger.warning("No store connected to TikTok business %s", message.business_id)
                continue
            try:
                self.dispatch_message(store_settings.store, message)
            except ValueError:
                # receive_event refuses a message id it has seen with different content.
                # Retrying cannot fix that, so it must not make TikTok resend forever.
                logger.exception("TikTok message %s conflicts with an earlier one", message.message_id)
        return HttpResponse("EVENT_RECEIVED", status=200)

    @staticmethod
    def dispatch_message(store, message):
        from products.tasks import process_attachment_async, process_message_async
        if message.kind == "text":
            if message.text.strip():
                process_message_async(store.id, "tiktok", message.conversation_id, message.text,
                                      source_id=message.message_id)
        elif message.kind == "image":
            if message.media_id:
                # Stored as a marker, the way WhatsApp stores a media id: the inbox shows
                # "attachment from the customer" until TikTok media download is added.
                process_attachment_async(store.id, "tiktok", message.conversation_id,
                                         f"tiktok-media:{message.media_id}", "",
                                         source_id=message.message_id)
        elif message.kind == "share_post":
            if message.url:
                process_attachment_async(store.id, "tiktok", message.conversation_id,
                                         message.url, SHARED_VIDEO_CAPTION,
                                         source_id=message.message_id)
        else:
            # Stickers and anything newer: nothing the agent can answer.
            logger.info("Ignoring TikTok %s message %s", message.kind, message.message_id)
