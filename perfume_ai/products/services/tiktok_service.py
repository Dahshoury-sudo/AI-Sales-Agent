"""TikTok DMs through TikTok's Business Messaging API.

Everything that knows TikTok's endpoints, field names and payload shape lives in
this module, so it is the only file to correct once real traffic is seen.

Where the facts come from: TikTok's official docs (business-api.tiktok.com) render
only in a browser and could not be read while this was written. Endpoints and
fields follow Chatwoot's open-source TikTok channel and integrator docs (Infobip,
uchat). Check every call here against the portal once the TikTok app is approved,
and check parse_webhook against the first real webhook (docs/tiktok-setup.md).

Shape of the integration:
- One TikTok app owned by WebVitas. Each store connects its own TikTok Business
  account through OAuth (dashboard/views_tiktok.py). Its app secret signs every
  webhook, for all stores.
- Access tokens last about a day and refresh tokens about a month, so tokens are
  refreshed on demand before a send and hourly by the beat task.
- A store's TikTok customers are keyed by TikTok's conversation_id, because the
  send API addresses a conversation, not a user.
- TikTok accepts replies only within 48 hours of the customer's last message.
"""

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import requests
from django.conf import settings
from django.utils import timezone

from products.services.meta_service import UncertainDelivery

logger = logging.getLogger(__name__)

SCOPES = (
    "user.info.basic",
    "user.info.username",
    "user.info.profile",
    "user.account.type",
    "message.list.read",
    "message.list.send",
    "message.list.manage",
)
# Without these the agent can neither see nor answer DMs, so a connection that
# lacks them is refused rather than saved half-working.
REQUIRED_SCOPES = ("message.list.read", "message.list.send")

REPLY_WINDOW = timedelta(hours=48)
# get_valid_token refreshes when less than this is left before a send.
SEND_REFRESH_MARGIN = timedelta(minutes=10)
# The hourly beat task refreshes anything expiring sooner than this.
SCHEDULED_REFRESH_MARGIN = timedelta(hours=2)
# The owner is warned this long before the refresh token itself runs out.
RECONNECT_WARNING = timedelta(days=3)
# Used when TikTok's response omits expires_in: refresh early rather than never.
DEFAULT_ACCESS_LIFETIME = timedelta(hours=24)

IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
MAX_IMAGE_BYTES = 3 * 1024 * 1024
TIMEOUT = 10

RECEIVE_EVENTS = ("im_receive_msg", "im_receive_msg_eu")


class TikTokError(Exception):
    """TikTok answered and refused the request."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def configured():
    """Whether the WebVitas TikTok app credentials are set on this server."""
    return bool(settings.TIKTOK_APP_ID and settings.TIKTOK_APP_SECRET and settings.TIKTOK_REDIRECT_URI)


def _call(method, path, *, token=None, json_body=None, params=None, data=None, files=None):
    """One Business API request; returns the response's `data` object.

    TikTok reports most errors as HTTP 200 with a non-zero `code`, so the HTTP status
    alone says nothing. Raises TikTokError when TikTok refuses. Timeouts and
    connection errors propagate as requests exceptions, because only the caller knows
    whether the request might still have taken effect.
    """
    headers = {"Access-Token": token} if token else {}
    response = requests.request(
        method, f"{settings.TIKTOK_API_BASE}/{path.lstrip('/')}",
        headers=headers, json=json_body, params=params, data=data, files=files, timeout=TIMEOUT,
    )
    try:
        body = response.json()
    except ValueError:
        raise TikTokError(f"{path}: HTTP {response.status_code} with a non-JSON body",
                          code=response.status_code) from None
    if not isinstance(body, dict):
        raise TikTokError(f"{path}: unexpected response type", code=response.status_code)
    code = body.get("code", 0)
    if response.status_code >= 400 or code not in (0, "0") or ("error" in body and "data" not in body):
        # The message names the problem; tokens are only ever in headers or request
        # bodies, never echoed here.
        logger.warning("TikTok %s %s refused: http=%s code=%s message=%s",
                       method, path, response.status_code, code, body.get("message"))
        raise TikTokError(f"{path}: {body.get('message') or 'refused'}", code=code)
    payload = body.get("data")
    return payload if isinstance(payload, dict) else body


# ── Webhooks ────────────────────────────────────────────────────────────────


def verify_signature(header, raw_body, now=None):
    """Check `Tiktok-Signature: t=<unix>,s=<hex>`: HMAC-SHA256 of "{t}.{body}".

    Signed with the app secret, which is shared by every store. With no secret
    configured nothing can be trusted, so everything is rejected; there are no
    legacy TikTok integrations to keep working.
    """
    secret = settings.TIKTOK_APP_SECRET
    if not secret or not header:
        return False
    parts = {}
    for item in str(header).split(","):
        key, sep, value = item.strip().partition("=")
        if sep:
            parts[key.strip()] = value.strip()
    timestamp, signature = parts.get("t", ""), parts.get("s", "")
    if not timestamp.isdigit() or not signature:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.lower().encode()):
        return False
    age = int(now if now is not None else time.time()) - int(timestamp)
    # Logged so the first live retries show whether TikTok re-signs them; that decides
    # whether TIKTOK_WEBHOOK_MAX_AGE can be turned on safely.
    logger.info("TikTok webhook signature valid, age %ss", age)
    max_age = settings.TIKTOK_WEBHOOK_MAX_AGE
    if max_age and abs(age) > max_age:
        logger.warning("Rejecting TikTok webhook signed %ss ago (max %ss)", age, max_age)
        return False
    return True


@dataclass(frozen=True)
class InboundTikTokMessage:
    event: str
    business_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    kind: str            # text, image, share_post, sticker, or another TikTok type
    text: str = ""
    media_id: str = ""
    url: str = ""
    timestamp_ms: int = 0


def _first(*values):
    for value in values:
        if value not in (None, "", {}, []):
            return str(value)
    return ""


def _obj(value):
    """A nested object that may arrive as a dict or as a JSON-encoded string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def parse_webhook(payload):
    """The customer messages in a webhook body, ready for the DM pipeline.

    THE ONLY PLACE THAT KNOWS TIKTOK'S PAYLOAD SHAPE. It is written from integrator
    descriptions, not from a real payload: `content` may be an object or a JSON
    string, and the receiving business's id is looked for in several places. When
    the first real webhook arrives, correct this function and the fixtures in
    products/test_tiktok.py together.

    Echoes of our own sends (`im_send_msg`) and messages the business itself sent are
    dropped here. Other event types return nothing.
    """
    if not isinstance(payload, dict):
        return []
    event = str(payload.get("event") or "")
    if event not in RECEIVE_EVENTS:
        return []
    content = _obj(payload.get("content")) or payload
    from_user = _obj(content.get("from_user"))
    to_user = _obj(content.get("to_user"))
    kind = _first(content.get("type"), content.get("message_type")).lower()

    business_id = _first(payload.get("business_id"), content.get("business_id"),
                         payload.get("user_openid"), to_user.get("id"), content.get("to"))
    sender_id = _first(from_user.get("id"), content.get("from"), content.get("sender_id"))
    conversation_id = _first(content.get("conversation_id"), payload.get("conversation_id"))
    message_id = _first(content.get("message_id"), payload.get("message_id"))

    text_obj = content.get("text")
    text = text_obj if isinstance(text_obj, str) else _obj(text_obj).get("body", "")
    media_id = _first(_obj(content.get("image")).get("media_id"))
    url = _first(_obj(content.get("share_post")).get("embed_url"))
    try:
        timestamp_ms = int(content.get("timestamp") or payload.get("create_time") or 0)
    except (TypeError, ValueError):
        timestamp_ms = 0

    if not (business_id and conversation_id and message_id and kind):
        # Keys only: the customer's text must not end up in logs.
        logger.warning("TikTok %s webhook not understood; payload keys %s, content keys %s",
                       event, sorted(payload), sorted(content))
        return []
    if sender_id and sender_id == business_id:
        return []
    return [InboundTikTokMessage(
        event=event, business_id=business_id, conversation_id=conversation_id,
        message_id=message_id, sender_id=sender_id, kind=kind, text=str(text or ""),
        media_id=media_id, url=url, timestamp_ms=timestamp_ms,
    )]


def register_webhook(callback_url):
    """Point the app's DIRECT_MESSAGE webhook at callback_url (one URL per app)."""
    return _call("POST", "business/webhook/update/", json_body={
        "app_id": settings.TIKTOK_APP_ID,
        "secret": settings.TIKTOK_APP_SECRET,
        "event_type": "DIRECT_MESSAGE",
        "callback_url": callback_url,
    })


# ── OAuth and tokens ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenGrant:
    business_id: str
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    scopes: frozenset


def _seconds(value):
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _grant(data):
    if not data.get("access_token"):
        raise TikTokError("token response had no access_token")
    scope = data.get("scope") or ""
    scopes = scope if isinstance(scope, (list, tuple)) else str(scope).replace(" ", ",").split(",")
    return TokenGrant(
        business_id=_first(data.get("open_id"), data.get("business_id")),
        access_token=str(data["access_token"]),
        refresh_token=str(data.get("refresh_token") or ""),
        expires_in=_seconds(data.get("expires_in")),
        refresh_expires_in=_seconds(data.get("refresh_token_expires_in")),
        scopes=frozenset(s.strip() for s in scopes if s and s.strip()),
    )


def authorize_url(state):
    query = urlencode({
        "client_key": settings.TIKTOK_APP_ID,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "redirect_uri": settings.TIKTOK_REDIRECT_URI,
        "state": state,
    })
    return f"{settings.TIKTOK_AUTH_URL}?{query}"


def exchange_code(code):
    return _grant(_call("POST", "tt_user/oauth2/token/", json_body={
        "client_id": settings.TIKTOK_APP_ID,
        "client_secret": settings.TIKTOK_APP_SECRET,
        "grant_type": "authorization_code",
        "auth_code": code,
        "redirect_uri": settings.TIKTOK_REDIRECT_URI,
    }))


def _refresh_grant(refresh_token):
    return _grant(_call("POST", "tt_user/oauth2/refresh_token/", json_body={
        "client_id": settings.TIKTOK_APP_ID,
        "client_secret": settings.TIKTOK_APP_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }))


def fetch_account(token, business_id):
    """Best-effort profile of the connected account: username and, if TikTok says so,
    whether it is a Business account. Never raises: the connection works without it."""
    try:
        data = _call("GET", "business/get/", token=token, params={
            "business_id": business_id,
            "fields": json.dumps(["username", "display_name"]),
        })
    except (TikTokError, requests.RequestException):
        logger.warning("Could not fetch the TikTok profile for business %s", business_id)
        return {"username": "", "is_business": None}
    account_type = str(data.get("account_type") or "").lower()
    is_business = data.get("is_business_account")
    if is_business is None and account_type:
        is_business = account_type == "business"
    return {
        "username": _first(data.get("username"), data.get("display_name")),
        "is_business": is_business,
    }


TOKEN_FIELDS = ["tiktok_access_token", "tiktok_refresh_token",
                "tiktok_access_expires_at", "tiktok_refresh_expires_at"]


def apply_grant(store_settings, grant, now=None):
    """Copy a grant onto the row (unsaved). A refresh that returns no new refresh
    token keeps the old one."""
    now = now or timezone.now()
    store_settings.tiktok_access_token = grant.access_token
    store_settings.tiktok_access_expires_at = now + (
        timedelta(seconds=grant.expires_in) if grant.expires_in else DEFAULT_ACCESS_LIFETIME)
    if grant.refresh_token:
        store_settings.tiktok_refresh_token = grant.refresh_token
        store_settings.tiktok_refresh_expires_at = (
            now + timedelta(seconds=grant.refresh_expires_in) if grant.refresh_expires_in else None)


def disconnect(store_settings):
    store_settings.tiktok_business_id = ""
    store_settings.tiktok_username = ""
    store_settings.tiktok_access_token = ""
    store_settings.tiktok_refresh_token = ""
    store_settings.tiktok_access_expires_at = None
    store_settings.tiktok_refresh_expires_at = None
    store_settings.save(update_fields=["tiktok_business_id", "tiktok_username"] + TOKEN_FIELDS)


def _access_valid(store_settings, now, margin):
    expires = store_settings.tiktok_access_expires_at
    return bool(store_settings.tiktok_access_token) and (expires is None or expires > now + margin)


def _refresh_usable(store_settings, now):
    expires = store_settings.tiktok_refresh_expires_at
    return bool(store_settings.tiktok_refresh_token) and (expires is None or expires > now)


def connection_status(store_settings, now=None):
    """What the dashboard shows. Never includes a token."""
    now = now or timezone.now()
    linked = bool(store_settings.tiktok_business_id)
    connected = linked and _access_valid(store_settings, now, timedelta(0))
    return {
        "configured": configured(),
        "linked": linked,
        "connected": connected,
        "needs_reconnect": linked and not connected and not _refresh_usable(store_settings, now),
        "username": store_settings.tiktok_username,
        "business_id": store_settings.tiktok_business_id,
        "access_expires_at": store_settings.tiktok_access_expires_at,
        "refresh_expires_at": store_settings.tiktok_refresh_expires_at,
    }


def _copy_tokens(source, target):
    for field in TOKEN_FIELDS:
        setattr(target, field, getattr(source, field))


def get_valid_token(store_settings, margin=SEND_REFRESH_MARGIN, now=None):
    """An access token good for at least `margin`, refreshing it if needed; "" if none.

    Refreshes under a per-store lock and re-reads the row inside it, because TikTok may
    rotate the refresh token: two workers refreshing with the same old token could
    leave the one that lost the race holding a token TikTok has already retired.
    """
    from products.models import StoreSettings
    from products.services.conversation_service import conversation_lock

    now = now or timezone.now()
    if _access_valid(store_settings, now, margin):
        return store_settings.tiktok_access_token
    with conversation_lock(f"tiktok-token:{store_settings.pk}"):
        current = StoreSettings.objects.get(pk=store_settings.pk)
        if not _access_valid(current, now, margin) and _refresh_usable(current, now):
            try:
                grant = _refresh_grant(current.tiktok_refresh_token)
            except TikTokError:
                # Kept, not cleared: the code TikTok uses for "this refresh token is dead"
                # is unknown, and wiping a token over a transient refusal would force a
                # reconnect. The hourly job retries; the owner is told once a day.
                logger.warning("TikTok refused to refresh the token for store %s", current.store_id)
                _notify_refresh_failed(current, now)
            except requests.RequestException:
                logger.warning("TikTok token refresh for store %s did not complete", current.store_id)
            else:
                apply_grant(current, grant, now)
                current.save(update_fields=TOKEN_FIELDS)
        _copy_tokens(current, store_settings)
    # An unexpired token is still usable inside the margin if the refresh failed.
    return store_settings.tiktok_access_token if _access_valid(store_settings, now, timedelta(0)) else ""


def _notify_refresh_failed(store_settings, now):
    from products.services.notification_service import notify_integration_problem
    notify_integration_problem(
        store_settings.store,
        "ربط تيك توك محتاج يتجدد ⚠️",
        "تيك توك رفض تجديد صلاحية الربط. لو الرسائل على تيك توك وقفت، ادخل على ربط المنصات "
        "واضغط \"ربط تيك توك\" تاني.",
        f"tiktok-refresh-failed:{store_settings.store_id}:{now:%Y%m%d}",
    )


def refresh_expiring_tokens(now=None):
    """Hourly: refresh tokens close to expiry, and warn before a reconnect is needed."""
    from django.db.models import Q
    from products.models import StoreSettings
    from products.services.notification_service import notify_integration_problem

    now = now or timezone.now()
    linked = StoreSettings.objects.exclude(tiktok_business_id="").exclude(tiktok_refresh_token="")
    due = linked.filter(Q(tiktok_access_expires_at__isnull=True)
                        | Q(tiktok_access_expires_at__lte=now + SCHEDULED_REFRESH_MARGIN))
    refreshed = 0
    for store_settings in due.select_related("store"):
        if _refresh_usable(store_settings, now):
            get_valid_token(store_settings, margin=SCHEDULED_REFRESH_MARGIN, now=now)
            refreshed += 1
    ending = linked.filter(tiktok_refresh_expires_at__isnull=False,
                           tiktok_refresh_expires_at__lte=now + RECONNECT_WARNING)
    for store_settings in ending.select_related("store"):
        expires = store_settings.tiktok_refresh_expires_at
        notify_integration_problem(
            store_settings.store,
            "ربط تيك توك هينتهي قريب ⏳",
            f"صلاحية ربط تيك توك بتنتهي يوم {timezone.localtime(expires):%Y-%m-%d}. ادخل على ربط "
            "المنصات واضغط \"ربط تيك توك\" تاني عشان البوت يفضل يرد على العملاء هناك.",
            f"tiktok-reconnect:{store_settings.store_id}:{expires:%Y%m%d%H%M}",
        )
    return refreshed


# ── Sending ─────────────────────────────────────────────────────────────────


def reply_window_open(conversation, now=None):
    """TikTok accepts replies only within 48 hours of the customer's last message.

    A conversation with no customer message is let through and TikTok decides.
    """
    from products.services.meta_service import last_customer_message_at
    last = last_customer_message_at(conversation)
    return last is None or (now or timezone.now()) - last <= REPLY_WINDOW


def _send(body, token, what):
    try:
        data = _call("POST", "business/message/send/", token=token, json_body=body)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        raise UncertainDelivery("TikTok acknowledgement unavailable") from e
    except (TikTokError, requests.RequestException) as e:
        logger.error("TikTok refused a %s message: %s", what, e)
        return None
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    return {"message_id": _first(message.get("message_id"), data.get("message_id"))}


def send_text(business_id, conversation_id, text, token):
    """Returns {"message_id": ...} when sent, None when refused; raises
    UncertainDelivery when TikTok may have sent it without saying so."""
    return _send({
        "business_id": business_id,
        "recipient_type": "CONVERSATION",
        "recipient": conversation_id,
        "message_type": "TEXT",
        "text": {"body": text},
    }, token, "text")


def _download_image(url):
    """The image bytes and content type, or None.

    The URL is owner-entered, so the fetch is narrow: https only, no redirects,
    JPEG/PNG only, and at most 3 MB (TikTok's limit) read before giving up.
    """
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        logger.warning("Not sending the bottle image: its URL is not https")
        return None
    try:
        response = requests.request("GET", url, stream=True, timeout=TIMEOUT, allow_redirects=False)
        try:
            if response.status_code != 200:
                logger.warning("Bottle image download returned HTTP %s", response.status_code)
                return None
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type not in IMAGE_TYPES:
                logger.warning("Bottle image is %r; TikTok takes JPEG or PNG", content_type)
                return None
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    logger.warning("Bottle image is over TikTok's 3 MB limit")
                    return None
                chunks.append(chunk)
            return b"".join(chunks), content_type
        finally:
            response.close()
    except requests.RequestException as e:
        logger.warning("Bottle image download failed: %s", e)
        return None


def upload_image(business_id, image_url, token):
    """Upload the image to TikTok and return its media_id, or "" on any failure.

    Never raises: nothing has reached the customer yet, so every failure here is a
    clean "not sent".
    """
    image = _download_image(image_url)
    if image is None:
        return ""
    content, content_type = image
    try:
        data = _call("POST", "business/message/media/upload/", token=token,
                     data={"business_id": business_id, "media_type": "IMAGE"},
                     files={"file": (f"bottle.{IMAGE_TYPES[content_type]}", content, content_type)})
    except (TikTokError, requests.RequestException) as e:
        logger.warning("TikTok image upload failed: %s", e)
        return ""
    return _first(data.get("media_id"))


def send_image(business_id, conversation_id, image_url, token):
    media_id = upload_image(business_id, image_url, token)
    if not media_id:
        return None
    return _send({
        "business_id": business_id,
        "recipient_type": "CONVERSATION",
        "recipient": conversation_id,
        "message_type": "IMAGE",
        "image": {"media_id": media_id},
    }, token, "image")
