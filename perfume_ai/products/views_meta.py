import hmac
import hashlib
import json
import logging
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response

from products.models import StoreSettings
from products.services.conversation_service import get_or_create_platform_conversation, get_conversation_messages, save_message
from products.services.router import route
from products.services.meta_service import send_platform_message
from products.throttles import WebhookThrottle

logger = logging.getLogger(__name__)

class MetaWebhookView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [WebhookThrottle]

    def get(self, request):
        logger.info(f"Received GET webhook request: {request.GET}")
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if mode == "subscribe" and token:
            store_settings = StoreSettings.objects.filter(meta_verify_token=token).first()
            if store_settings:
                logger.info(f"Webhook verified successfully for store ID: {store_settings.store.id}")
                return HttpResponse(challenge, status=200)
            else:
                logger.warning(f"Webhook verification failed: No store found with token {token}")
                return HttpResponse("Forbidden", status=403)
        
        logger.warning(f"Webhook verification failed: Invalid mode or missing token. Mode: {mode}")
        return HttpResponse("Bad Request", status=400)

    def post(self, request):
        body = request.body
        logger.info(f"Received POST webhook request. Body: {body}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON body")
            return Response("Invalid JSON", status=400)
            
        object_type = data.get("object")
        logger.info(f"Webhook object type: {object_type}")
        
        if object_type in ["whatsapp_business_account", "page", "instagram"]:
            for entry in data.get("entry", []):
                platform = "unknown"
                
                if "changes" in entry:
                    platform = "whatsapp"
                    logger.info("Processing WhatsApp changes")
                    for change in entry.get("changes", []):
                        value = change.get("value", {})
                        if "messages" in value:
                            receiving_id = value.get("metadata", {}).get("phone_number_id")
                            logger.info(f"Received WA message for phone_number_id: {receiving_id}")
                            store_settings = StoreSettings.objects.filter(whatsapp_phone_number_id=receiving_id).first()
                            if not store_settings:
                                logger.warning(f"No store found for WA phone number ID {receiving_id}")
                                continue
                                
                            if not self.verify_signature(request, body, store_settings.meta_app_secret):
                                return HttpResponse("Invalid signature", status=403)
                            
                            for message in value.get("messages", []):
                                if message.get("type") == "text":
                                    sender_id = message.get("from")
                                    text = message.get("text", {}).get("body")
                                    logger.info(f"Dispatching WA message from {sender_id}: {text}")
                                    self.process_message(store_settings.store, platform, sender_id, text, store_settings)
                                else:
                                    logger.info(f"Ignored non-text WA message type: {message.get('type')}")

                elif "messaging" in entry:
                    platform = "messenger_or_instagram"
                    logger.info("Processing Messenger/Instagram messaging events")
                    for messaging_event in entry.get("messaging", []):
                        sender_id = messaging_event.get("sender", {}).get("id")
                        recipient_id = messaging_event.get("recipient", {}).get("id")
                        logger.info(f"Received message event. Sender: {sender_id}, Recipient: {recipient_id}")
                        
                        store_settings = StoreSettings.objects.filter(facebook_page_id=recipient_id).first()
                        if store_settings:
                            platform = "messenger"
                            logger.info(f"Matched Facebook Page ID: {recipient_id}")
                        else:
                            store_settings = StoreSettings.objects.filter(instagram_account_id=recipient_id).first()
                            if store_settings:
                                platform = "instagram"
                                logger.info(f"Matched Instagram Account ID: {recipient_id}")
                                
                        if not store_settings:
                            logger.warning(f"No store found for recipient ID {recipient_id}")
                            continue

                        if not self.verify_signature(request, body, store_settings.meta_app_secret):
                            return HttpResponse("Invalid signature", status=403)
                        
                        if "message" in messaging_event and "text" in messaging_event["message"]:
                            text = messaging_event["message"]["text"]
                            logger.info(f"Dispatching {platform} message from {sender_id}: {text}")
                            self.process_message(store_settings.store, platform, sender_id, text, store_settings)
                        else:
                            logger.info(f"Ignored non-text {platform} message")

            logger.info("Successfully processed webhook EVENT_RECEIVED")
            return HttpResponse("EVENT_RECEIVED", status=200)
        else:
            logger.warning(f"Webhook received unknown object type: {object_type}")
            return HttpResponse("NOT_FOUND", status=404)

    def verify_signature(self, request, body, app_secret):
        """Verify the X-Hub-Signature-256 header. Returns True if valid or if no secret is configured."""
        signature = request.headers.get("X-Hub-Signature-256")
        logger.info(f"Verifying signature: {signature}")
        if not app_secret:
            logger.info("No app_secret configured, skipping validation")
            return True
        if not signature:
            logger.warning("Missing X-Hub-Signature-256 header")
            return False
            
        expected_signature = "sha256=" + hmac.new(
            app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            logger.warning(f"Invalid webhook signature. Expected: {expected_signature}, Got: {signature}")
            return False
        logger.info("Signature verified successfully")
        return True

    def process_message(self, store, platform, sender_id, text, store_settings):
        """Dispatch message processing to a background thread for fast webhook response."""
        from products.tasks import process_message_async
        process_message_async(store.id, platform, sender_id, text)
