import logging
import re
from urllib.parse import urlparse, parse_qs

import requests as http_requests

from products.models import PostCommentRule

logger = logging.getLogger(__name__)


def should_reply_to_post(store, platform, post_id):
    """Decide whether the bot should reply to comments on this post.

    Blocklist mode: the bot replies to every post by default.  If the post
    appears in PostCommentRule (active) for this store+platform, the bot
    stays silent.

    Called from views_meta.py before dispatching process_comment, so a
    blocked post never reaches the Celery queue and costs nothing.

    Returns True if the bot should reply, False if it should stay silent.
    """
    if not post_id:
        # No post ID available — can't filter, so reply.
        return True

    is_blocked = PostCommentRule.objects.filter(
        store=store,
        platform=platform,
        post_id=post_id,
        is_active=True,
    ).exists()

    if is_blocked:
        logger.info(
            "Comment on blocked post %s (%s) for store '%s' — skipping.",
            post_id, platform, store.name,
        )

    return not is_blocked


# ── Post IDs: what a rule must hold to match the webhook ────────────────────
#
# A rule only works if its post_id equals what Meta's webhook sends:
#   Facebook feed webhook → value.post_id  = "PAGEID_POSTID"
#   Instagram comments    → value.media.id = the Graph API media ID (digits)
# Every rule saved from the dashboard goes through validate_post_id, so a value
# that can never match is rejected instead of being saved and silently ignored.

GRAPH_URL = "https://graph.facebook.com/v19.0"
PLATFORMS = ("facebook", "instagram")
# How many pages of recent posts a pasted link is searched against (100 each).
MAX_LOOKUP_PAGES = 5

_FB_FIELDS = "id,message,story,created_time,full_picture,permalink_url,attachments{target{id}}"
_IG_FIELDS = "id,caption,shortcode,media_type,media_url,thumbnail_url,permalink,timestamp"

_FB_POST_ID = re.compile(r"(\d+)_(\d+)")
_DIGITS = re.compile(r"\d+")
_META_HOST = re.compile(
    r"(?:[\w-]+\.)*(facebook\.com|fb\.com|fb\.watch|instagram\.com|instagr\.am)", re.IGNORECASE
)

REQUIRED = "الرابط أو الـ Post ID مطلوب."
UNKNOWN_PLATFORM = "المنصة غير معروفة."
SETUP_NEEDED = {
    "facebook": "لازم تربط الـ Facebook Page ID والـ Access Token في إعدادات الستور الأول.",
    "instagram": "لازم تربط حساب الانستجرام والـ Access Token في إعدادات الستور الأول.",
}
META_UNREACHABLE = {
    "facebook": "مقدرناش نوصل لفيسبوك دلوقتي. جرب تاني بعد شوية.",
    "instagram": "مقدرناش نوصل لانستجرام دلوقتي. جرب تاني بعد شوية.",
}
NOT_FOUND = {
    "facebook": "مقدرش ألاقي البوست ده في آخر بوستات صفحتك. اختار البوست من القائمة.",
    "instagram": "مقدرش ألاقي البوست ده في آخر بوستات حسابك. اختار البوست من القائمة.",
}
NOT_A_POST = {
    "facebook": "ده مش رابط بوست فيسبوك. اختار البوست من القائمة أو الصق رابط البوست نفسه.",
    "instagram": "ده مش رابط بوست انستجرام. اختار البوست من القائمة أو الصق رابط البوست نفسه.",
}
WRONG_PLATFORM = {
    "facebook": "ده رابط انستجرام. غيّر المنصة لـ Instagram.",
    "instagram": "ده رابط فيسبوك. غيّر المنصة لـ Facebook.",
}
OTHER_PAGE = "البوست ده مش من صفحتك المربوطة، فالبوت مش بيستقبل تعليقاته أصلاً."


class PostLookupError(Exception):
    """Recent posts could not be listed. `status` is the HTTP status the dashboard returns."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def _token(store_settings):
    if not store_settings:
        return None
    return store_settings.messenger_access_token or store_settings.meta_access_token or None


def _account_id(store_settings, platform):
    if not store_settings:
        return ""
    field = "facebook_page_id" if platform == "facebook" else "instagram_account_id"
    return (getattr(store_settings, field, "") or "").strip()


def validate_post_id(platform, value, store_settings):
    """Return (post_id, error): `value` in the exact form the webhook sends, or why it can't match.

    A bare Facebook post number gets the store's page ID prefixed, since that is
    how the webhook spells it.
    """
    value = (value or "").strip()
    if platform == "facebook":
        page_id = _account_id(store_settings, "facebook")
        if _DIGITS.fullmatch(value):
            if not page_id:
                return None, SETUP_NEEDED["facebook"]
            return f"{page_id}_{value}", None
        match = _FB_POST_ID.fullmatch(value)
        if not match:
            return None, NOT_A_POST["facebook"]
        if page_id and match.group(1) != page_id:
            return None, OTHER_PAGE
        return value, None
    if platform == "instagram":
        if _DIGITS.fullmatch(value):
            return value, None
        return None, NOT_A_POST["instagram"]
    return None, UNKNOWN_PLATFORM


def is_matchable(platform, post_id, store_settings):
    """True if a saved rule's post_id is in the form the webhook sends, so it can block anything."""
    normalized, error = validate_post_id(platform, post_id, store_settings)
    return error is None and normalized == post_id


# ── Listing the account's recent posts ──────────────────────────────────────

def _normalize_post(platform, item):
    if platform == "facebook":
        attachments = (item.get("attachments") or {}).get("data") or []
        return {
            "id": item["id"],
            "text": item.get("message") or item.get("story") or "",
            "image": item.get("full_picture") or "",
            "permalink": item.get("permalink_url") or "",
            "created_at": item.get("created_time") or "",
            "target_ids": [str(a["target"]["id"]) for a in attachments if (a.get("target") or {}).get("id")],
        }
    image = item.get("thumbnail_url") or (item.get("media_url") if item.get("media_type") != "VIDEO" else "")
    return {
        "id": item["id"],
        "text": item.get("caption") or "",
        "image": image or "",
        "permalink": item.get("permalink") or "",
        "created_at": item.get("timestamp") or "",
        "shortcode": item.get("shortcode") or "",
    }


def list_recent_posts(store_settings, platform, after=None, limit=25):
    """Return (posts, next_cursor) for the store's Facebook page or Instagram account, newest first.

    Each post's `id` comes from Meta in the same form the comment webhook uses,
    so a rule made from it always matches. Raises PostLookupError (400 when the
    store isn't linked, 502 when Meta fails); the token never reaches the log.
    """
    account_id = _account_id(store_settings, platform)
    token = _token(store_settings)
    if not account_id or not token:
        raise PostLookupError(SETUP_NEEDED[platform], 400)

    edge, fields = ("posts", _FB_FIELDS) if platform == "facebook" else ("media", _IG_FIELDS)
    params = {"fields": fields, "limit": limit, "access_token": token}
    if after:
        params["after"] = after
    try:
        response = http_requests.get(f"{GRAPH_URL}/{account_id}/{edge}", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except (http_requests.RequestException, ValueError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning("Listing recent %s posts for %s failed: %s (HTTP %s)",
                       platform, account_id, type(exc).__name__, status)
        raise PostLookupError(META_UNREACHABLE[platform], 502)

    posts = [_normalize_post(platform, item) for item in data.get("data") or [] if item.get("id")]
    paging = data.get("paging") or {}
    next_cursor = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
    return posts, next_cursor


def _find_post(store_settings, platform, match):
    """Search the account's recent posts (up to MAX_LOOKUP_PAGES pages) for one that `match`es."""
    after = None
    for _ in range(MAX_LOOKUP_PAGES):
        posts, after = list_recent_posts(store_settings, platform, after=after, limit=100)
        for post in posts:
            if match(post):
                return post
        if not after:
            break
    return None


# ── Pasted link → webhook ID ────────────────────────────────────────────────

def _as_url(value):
    """The value as an https URL if it is a link (with or without a scheme), else None."""
    if re.match(r"https?://", value, re.IGNORECASE):
        return value
    host = _META_HOST.match(value)
    if host and value[host.end():host.end() + 1] in ("", "/", "?"):
        return "https://" + value
    return None


def _link_platform(url):
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(("instagram.com", "instagr.am")):
        return "instagram"
    if host.endswith(("facebook.com", "fb.com", "fb.watch")):
        return "facebook"
    return None


def _first(query, key):
    values = query.get(key)
    return values[0] if values else None


def _pfbid(text):
    match = re.search(r"pfbid\w+", text or "")
    return match.group(0) if match else None


def _same_post_link(permalink, url):
    if not permalink:
        return False
    a, b = _pfbid(permalink), _pfbid(url)
    if a and b:
        return a == b
    path_a, path_b = urlparse(permalink).path.rstrip("/"), urlparse(url).path.rstrip("/")
    return bool(path_b) and path_a == path_b


def _facebook_id_from_url(url, store_settings):
    """The PAGEID_POSTID a Facebook link points at, or None.

    Links that carry the numeric post ID are read offline. Photo, video and reel
    links carry the photo/video ID instead, which is not the post ID, so those —
    and pfbid/share links — are looked up among the page's recent posts.
    """
    page_id = _account_id(store_settings, "facebook")
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)

    # permalink.php / story.php?story_fbid=POST&id=PAGE
    story = _first(query, "story_fbid")
    if story and story.isdigit():
        url_page = _first(query, "id") or page_id
        if url_page:
            return f"{url_page}_{story}"

    # /PAGE_OR_NAME/posts/POST
    match = re.search(r"/([^/]+)/posts/(\d+)$", path)
    if match:
        url_page = match.group(1) if match.group(1).isdigit() else page_id
        if url_page:
            return f"{url_page}_{match.group(2)}"

    # /photo/?fbid=X, photo.php?fbid=X, /watch/?v=X, /reel/X, /videos/X
    media_id = _first(query, "fbid") or _first(query, "v")
    if not media_id:
        match = re.search(r"/(?:reel|videos)/(?:[^/]+/)?(\d+)$", path)
        media_id = match.group(1) if match else None
    if media_id and media_id.isdigit():
        post = _find_post(store_settings, "facebook",
                          lambda p: media_id in p["target_ids"] or p["id"].endswith(f"_{media_id}"))
        return post["id"] if post else None

    # pfbid, /share/p/…, anything else: match the permalink, then ask Meta.
    post = _find_post(store_settings, "facebook", lambda p: _same_post_link(p["permalink"], url))
    if post:
        return post["id"]
    resolved = _resolve_facebook_url_via_api(url, _token(store_settings))
    # A bare object ID here is a photo/video/URL node, not a post: only PAGEID_POSTID is trusted.
    return resolved if resolved and _FB_POST_ID.fullmatch(resolved) else None


def _resolve_facebook_url_via_api(url, token):
    """Ask the Graph API to resolve a URL to its object ID; None on failure."""
    if not token:
        return None
    try:
        response = http_requests.get(
            f"{GRAPH_URL}/",
            params={"id": url, "fields": "id", "access_token": token},
            timeout=10,
        )
        response.raise_for_status()
        return str(response.json().get("id") or "") or None
    except (http_requests.RequestException, ValueError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning("Graph API URL lookup failed: %s (HTTP %s)", type(exc).__name__, status)
        return None


def _instagram_id_from_url(url, store_settings):
    """The media ID an Instagram /p/, /reel/, /reels/ or /tv/ link points at, or None."""
    match = re.search(r"/(?:p|reels?|tv)/([A-Za-z0-9_-]+)", urlparse(url).path)
    if not match:
        return None
    shortcode = match.group(1)
    in_permalink = re.compile(rf"/{re.escape(shortcode)}(?:/|$)")
    post = _find_post(store_settings, "instagram",
                      lambda p: p["shortcode"] == shortcode or bool(in_permalink.search(p["permalink"])))
    return post["id"] if post else None


def extract_post_id_from_url(input_value, platform, store_settings=None):
    """Turn a pasted post link or ID into the ID the comment webhook sends.

    Returns (post_id, error_message). On success error_message is None; on
    failure post_id is None and the message tells the owner what to do. The
    result always passes validate_post_id, so a saved rule can always match.
    """
    value = (input_value or "").strip()
    if not value:
        return None, REQUIRED
    if platform not in PLATFORMS:
        return None, UNKNOWN_PLATFORM

    url = _as_url(value)
    if url is None:
        return validate_post_id(platform, value, store_settings)

    link_platform = _link_platform(url)
    if link_platform and link_platform != platform:
        return None, WRONG_PLATFORM[platform]

    try:
        if platform == "facebook":
            candidate = _facebook_id_from_url(url, store_settings)
        else:
            candidate = _instagram_id_from_url(url, store_settings)
    except PostLookupError as exc:
        return None, exc.message
    if not candidate:
        return None, NOT_FOUND[platform]
    return validate_post_id(platform, candidate, store_settings)
