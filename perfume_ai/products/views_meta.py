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
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge")

        if mode == "subscribe" and token:
            store_settings = StoreSettings.objects.filter(meta_verify_token=token).first()
            if store_settings:
                return HttpResponse(challenge, status=200)
            else:
                return HttpResponse("Forbidden", status=403)
        return HttpResponse("Bad Request", status=400)

    def post(self, request):
        body = request.body
        print("====== WEBHOOK RECEIVED ======")
        print(f"BODY: {body.decode('utf-8')[:500]}")  # Print first 500 chars

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print("ERROR: Invalid JSON")
            return Response("Invalid JSON", status=400)
            
        if data.get("object") in ["whatsapp_business_account", "page", "instagram"]:
            for entry in data.get("entry", []):
                platform = "unknown"
                
                if "changes" in entry:
                    platform = "whatsapp"
                    for change in entry.get("changes", []):
                        value = change.get("value", {})
                        if "messages" in value:
                            receiving_id = value.get("metadata", {}).get("phone_number_id")
                            store_settings = StoreSettings.objects.filter(whatsapp_phone_number_id=receiving_id).first()
                            if not store_settings:
                                print(f"WARNING: No store found for WA phone number ID '{receiving_id}'")
                                logger.warning(f"No store found for WA phone number ID {receiving_id}")
                                continue
                                
                            if not self.verify_signature(request, body, store_settings.meta_app_secret):
                                print("ERROR: Invalid signature from Meta!")
                                return HttpResponse("Invalid signature", status=403)
                            
                            print("SUCCESS: Message verified and passed to process_message_async")
                            
                            for message in value.get("messages", []):
                                if message.get("type") == "text":
                                    sender_id = message.get("from")
                                    text = message.get("text", {}).get("body")
                                    self.process_message(store_settings.store, platform, sender_id, text, store_settings)

                elif "messaging" in entry:
                    for messaging_event in entry.get("messaging", []):
                        sender_id = messaging_event.get("sender", {}).get("id")
                        recipient_id = messaging_event.get("recipient", {}).get("id")
                        
                        store_settings = StoreSettings.objects.filter(facebook_page_id=recipient_id).first()
                        if store_settings:
                            platform = "messenger"
                        else:
                            store_settings = StoreSettings.objects.filter(instagram_account_id=recipient_id).first()
                            if store_settings:
                                platform = "instagram"
                                
                        if not store_settings:
                            logger.warning(f"No store found for recipient ID {recipient_id}")
                            continue

                        if not self.verify_signature(request, body, store_settings.meta_app_secret):
                            return HttpResponse("Invalid signature", status=403)
                        
                        if "message" in messaging_event and "text" in messaging_event["message"]:
                            text = messaging_event["message"]["text"]
                            self.process_message(store_settings.store, platform, sender_id, text, store_settings)

            return HttpResponse("EVENT_RECEIVED", status=200)
        else:
            return HttpResponse("NOT_FOUND", status=404)

    def verify_signature(self, request, body, app_secret):
        """Verify the X-Hub-Signature-256 header. Returns True if valid or if no secret is configured."""
        signature = request.headers.get("X-Hub-Signature-256")
        if not app_secret:
            # No secret configured, skip validation
            return True
        if not signature:
            logger.warning("Missing X-Hub-Signature-256 header")
            return False
            
        expected_signature = "sha256=" + hmac.new(
            app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            logger.warning("Invalid webhook signature")
            return False
        return True

    def process_message(self, store, platform, sender_id, text, store_settings):
        """Dispatch message processing to a background thread for fast webhook response."""
        from products.tasks import process_message_async
        process_message_async(store.id, platform, sender_id, text)
