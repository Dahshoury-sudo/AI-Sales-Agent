import logging
import requests

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


def reply_to_ig_comment(comment_id, message, token):
    """
    Post a public reply to an Instagram comment.
    Uses: POST /{ig-comment-id}/replies
    """
    url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
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
        logger.error(f"Error posting IG comment reply to {comment_id}: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        return None


def send_private_reply(sender_endpoint_id, comment_id, message, token):
    """
    Send a private Messenger/IG reply to the person who made a comment.
    """
    url = f"https://graph.facebook.com/v19.0/{sender_endpoint_id}/messages"
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


def fetch_post_content(post_id, token, platform="facebook"):
    """
    Fetch the text content of a Facebook Page or Instagram post.
    Used to give the AI context about which product/offer the commenter is referring to.
    """
    url = f"https://graph.facebook.com/v19.0/{post_id}"
    fields = "caption" if platform == "instagram" else "message,story"
    try:
        response = requests.get(
            url,
            params={"fields": fields, "access_token": token},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message") or data.get("story") or data.get("caption") or None
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


# Platforms delivered through the Facebook Page Send API. "facebook" marks a
# conversation that started as a comment on a Page post rather than a DM — the
# distinction is kept because process_comment_task uses it to choose the right
# comment-reply endpoint, but a *private* reply to a commenter goes out through
# the same Page endpoint Messenger uses. Omitting it here meant handoff replies to
# Facebook commenters hit the "Unknown platform" branch below and were never sent,
# while the dashboard still reported "Message sent".
MESSENGER_PLATFORMS = ("messenger", "facebook")


# Which platform label a *conversation* gets, given the source it arrived from.
# A Facebook commenter and a Facebook DM sender share the same page-scoped ID, so
# filing both under "messenger" keeps them in one conversation and the bot
# remembers the comment when the DM continues. Comment-reply endpoints still key
# off the original source, so this only affects conversation identity.
#
# Instagram is deliberately not mapped: its comment IDs and DM IDs are different
# ID spaces, so relabelling would not merge anything. Joining those needs the
# recipient_id that the Send API returns, which is a separate piece of work.
_CONVERSATION_PLATFORM = {"facebook": "messenger"}


def conversation_platform_for(source_platform):
    """The platform label to store a conversation under for a given source."""
    return _CONVERSATION_PLATFORM.get(source_platform, source_platform)


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
    bottle_image_url = ""
    if "[SEND_BOTTLE_IMAGE]" in text:
        text = text.replace("[SEND_BOTTLE_IMAGE]", "").strip()
        # Per-store: this was a single global setting, so every store's customers
        # were shown the first store's bottles and packaging.
        bottle_image_url = store_settings.bottle_image_url or ""
        if not bottle_image_url:
            logger.warning(
                f"Store '{conversation.store.name}' emitted [SEND_BOTTLE_IMAGE] but has "
                f"no bottle_image_url configured; sending text only."
            )

    # Send the image first if requested
    if bottle_image_url:
        if conversation.platform == "whatsapp":
            send_whatsapp_image(store_settings.whatsapp_phone_number_id, sender_id, bottle_image_url, token)
        elif conversation.platform in MESSENGER_PLATFORMS:
            send_messenger_image(store_settings.facebook_page_id, sender_id, bottle_image_url, token)
        elif conversation.platform == "instagram":
            # Instagram Messaging sends through the Facebook Page ID, not the IG
            # account ID — same as send_instagram_message below. Passing the IG
            # account ID here meant image sends failed.
            send_instagram_image(store_settings.facebook_page_id, sender_id, bottle_image_url, token)

    # Only send text if there is text left after removing the token
    if text:
        if conversation.platform == "whatsapp":
            send_whatsapp_message(store_settings.whatsapp_phone_number_id, sender_id, text, token)
        elif conversation.platform in MESSENGER_PLATFORMS:
            send_messenger_message(store_settings.facebook_page_id, sender_id, text, token)
        elif conversation.platform == "instagram":
            send_instagram_message(store_settings.facebook_page_id, sender_id, text, token)
        else:
            logger.warning(f"Unknown platform '{conversation.platform}' for conversation {conversation.id}")

