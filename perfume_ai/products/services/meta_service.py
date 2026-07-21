import logging
import requests

logger = logging.getLogger(__name__)

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
    url = f"https://graph.facebook.com/v19.0/me/messages"
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

def send_instagram_message(ig_account_id, recipient_id, text, token):
    url = f"https://graph.facebook.com/v19.0/me/messages"
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
        if e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None


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

    token = store_settings.meta_access_token
    sender_id = conversation.platform_sender_id

    if not token or not sender_id:
        logger.warning(f"Missing token or sender_id for conversation {conversation.id}")
        return

    if conversation.platform == "whatsapp":
        send_whatsapp_message(store_settings.whatsapp_phone_number_id, sender_id, text, token)
    elif conversation.platform == "messenger":
        send_messenger_message(store_settings.facebook_page_id, sender_id, text, token)
    elif conversation.platform == "instagram":
        send_instagram_message(store_settings.instagram_account_id, sender_id, text, token)
    else:
        logger.warning(f"Unknown platform '{conversation.platform}' for conversation {conversation.id}")
