import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def reply_to_comment(comment_id, message, token):
    """
    Post a public reply to a Facebook comment.
    Uses: POST /{comment-id}/comments
    """
    url = f"https://graph.facebook.com/v19.0/{comment_id}/comments"
    try:
        response = requests.post(
            url,
            params={"access_token": token},
            json={"message": message},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error posting public comment reply to {comment_id}: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None


def send_private_reply(page_id, comment_id, message, token):
    """
    Send a private Messenger reply to the person who made a Facebook comment.
    Uses the modern Messenger Send API: POST /{page-id}/messages
    with recipient set to comment_id.
    """
    url = f"https://graph.facebook.com/v19.0/{page_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"comment_id": comment_id},
        "message": {"text": message}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending private reply for comment {comment_id}: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None


def fetch_post_content(post_id, token):
    """
    Fetch the text content of a Facebook Page post.
    Used to give the AI context about which product/offer the commenter is referring to.
    Returns the post message string, or None if unavailable.
    """
    url = f"https://graph.facebook.com/v19.0/{post_id}"
    try:
        response = requests.get(
            url,
            params={"fields": "message,story", "access_token": token},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        # 'message' is the post body; 'story' is used for share/link posts
        return data.get("message") or data.get("story") or None
    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not fetch post {post_id}: {e}")
        return None


def send_whatsapp_message(phone_number_id, recipient_id, text, token):
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "text",
        "text": {"body": text}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        if e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None

def send_messenger_message(page_id, recipient_id, text, token):
    url = f"https://graph.facebook.com/v19.0/{page_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending Messenger message: {e}")
        if e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None

def send_instagram_message(page_id, recipient_id, text, token):
    """
    Send an Instagram DM via the Messenger Send API using the Facebook Page ID.
    Instagram Messaging API uses the Page ID (not Instagram Account ID) as sender.
    """
    url = f"https://graph.facebook.com/v19.0/{page_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending Instagram message: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None


def send_whatsapp_image(phone_number_id, recipient_id, image_url, token):
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "image",
        "image": {"link": image_url}
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp image: {e}")
        if e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None

def send_messenger_image(page_id, recipient_id, image_url, token):
    url = f"https://graph.facebook.com/v19.0/{page_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": image_url, "is_reusable": True}
            }
        }
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending Messenger image: {e}")
        if e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None

# Instagram uses the same format as Messenger for images
send_instagram_image = send_messenger_image


def send_platform_message(conversation, text):
    """
    Unified function to send a message back to the user on whatever
    platform they're chatting from. Does nothing for web conversations.
    """
    if conversation.platform == "web":
        return

    try:
        store_settings = conversation.store.settings
    except Exception:
        logger.error(f"No StoreSettings found for store {conversation.store_id}")
        return

    sender_id = conversation.platform_sender_id

    # ── Pick the right token per platform ───────────────────────────────────
    if conversation.platform == "whatsapp":
        token = store_settings.meta_access_token
    else:
        # Messenger & Instagram use the Page Access Token.
        # Fall back to meta_access_token for backward compatibility.
        token = store_settings.messenger_access_token or store_settings.meta_access_token

    if not token or not sender_id:
        logger.warning(f"Missing token or sender_id for conversation {conversation.id}")
        return

    # Check for the special image token
    should_send_image = False
    if "[SEND_BOTTLE_IMAGE]" in text:
        should_send_image = True
        text = text.replace("[SEND_BOTTLE_IMAGE]", "").strip()
        BOTTLE_IMAGE_URL = settings.BOTTLE_IMAGE_URL

    # Send the image first if requested
    if should_send_image:
        if conversation.platform == "whatsapp":
            send_whatsapp_image(store_settings.whatsapp_phone_number_id, sender_id, BOTTLE_IMAGE_URL, token)
        elif conversation.platform == "messenger":
            send_messenger_image(store_settings.facebook_page_id, sender_id, BOTTLE_IMAGE_URL, token)
        elif conversation.platform == "instagram":
            send_instagram_image(store_settings.instagram_account_id, sender_id, BOTTLE_IMAGE_URL, token)

    # Only send text if there is text left after removing the token
    if text:
        if conversation.platform == "whatsapp":
            send_whatsapp_message(store_settings.whatsapp_phone_number_id, sender_id, text, token)
        elif conversation.platform == "messenger":
            send_messenger_message(store_settings.facebook_page_id, sender_id, text, token)
        elif conversation.platform == "instagram":
            send_instagram_message(store_settings.facebook_page_id, sender_id, text, token)
        else:
            logger.warning(f"Unknown platform '{conversation.platform}' for conversation {conversation.id}")

