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
    url = f"https://graph.facebook.com/v19.0/me/messages"
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

    token = store_settings.meta_access_token
    sender_id = conversation.platform_sender_id

    if not token or not sender_id:
        logger.warning(f"Missing token or sender_id for conversation {conversation.id}")
        return

    # Check for the special image token
    should_send_image = False
    if "[SEND_BOTTLE_IMAGE]" in text:
        should_send_image = True
        text = text.replace("[SEND_BOTTLE_IMAGE]", "").strip()
        
        # TODO: Replace this URL with your actual image URL
        BOTTLE_IMAGE_URL = "https://res.cloudinary.com/dtssxxfra/image/upload/v1785375186/WhatsApp_Image_2026-07-28_at_10.39.44_PM_enqvw9.jpg"

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
            send_instagram_message(store_settings.instagram_account_id, sender_id, text, token)
        else:
            logger.warning(f"Unknown platform '{conversation.platform}' for conversation {conversation.id}")
