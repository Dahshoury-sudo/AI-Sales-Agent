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


# ── URL → Post ID extraction ────────────────────────────────────────────────
#
# Store owners paste a post URL from their browser; we extract the ID that
# the webhook will send so the blocklist filter can match it.
#
# Facebook webhook sends post_id as "PAGE_ID_POST_ID" (e.g. "123_456").
# Instagram webhook sends the numeric media ID.

# Instagram shortcodes are a base-64 encoding of the numeric media ID using
# this custom alphabet. Decoding is pure math — no API call needed.
_IG_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _ig_shortcode_to_media_id(shortcode):
    """Convert an Instagram shortcode to its numeric media ID."""
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + _IG_CHARSET.index(char)
    return str(media_id)


def _extract_instagram_id(url):
    """Extract numeric media ID from an Instagram URL.

    Handles:
      https://www.instagram.com/p/SHORTCODE/
      https://www.instagram.com/reel/SHORTCODE/
      https://www.instagram.com/tv/SHORTCODE/
    """
    parsed = urlparse(url)
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", parsed.path)
    if not match:
        return None
    shortcode = match.group(1)
    try:
        return _ig_shortcode_to_media_id(shortcode)
    except (ValueError, IndexError):
        return None


def _extract_facebook_id(url, page_id, token=None):
    """Extract post ID from a Facebook URL in PAGE_ID_POST_ID format.

    Strategy:
      1. Try regex for common URL patterns with numeric IDs.
      2. Fall back to the Graph API URL-lookup endpoint (handles pfbid,
         share links, and any other format Facebook invents).

    Handles:
      https://www.facebook.com/PAGE/posts/POST_ID
      https://www.facebook.com/PAGE_ID/posts/POST_ID
      https://www.facebook.com/permalink.php?story_fbid=POST_ID&id=PAGE_ID
      https://www.facebook.com/story.php?story_fbid=POST_ID&id=PAGE_ID
      https://www.facebook.com/photo/?fbid=POST_ID
      https://www.facebook.com/watch/?v=VIDEO_ID
      https://www.facebook.com/reel/REEL_ID
      https://www.facebook.com/share/p/HASH/      (via Graph API)
      https://www.facebook.com/.../posts/pfbid...  (via Graph API)
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)

    # ── Query-string patterns ───────────────────────────────────────────
    # permalink.php?story_fbid=XXX&id=YYY  or  story.php?story_fbid=XXX&id=YYY
    if "story_fbid" in query:
        story_fbid = query["story_fbid"][0]
        url_page_id = query.get("id", [None])[0] or page_id
        if story_fbid.isdigit() and url_page_id:
            return f"{url_page_id}_{story_fbid}"

    # /photo/?fbid=XXX
    if "fbid" in query and query["fbid"][0].isdigit() and page_id:
        return f"{page_id}_{query['fbid'][0]}"

    # /watch/?v=XXX
    if "v" in query and query["v"][0].isdigit() and page_id:
        return f"{page_id}_{query['v'][0]}"

    # ── Path patterns ───────────────────────────────────────────────────
    # /PAGE_OR_NAME/posts/POST_ID  (numeric post ID only)
    match = re.search(r"/([^/]+)/posts/(\d+)$", path)
    if match:
        post_num = match.group(2)
        url_page = match.group(1)
        effective_page_id = url_page if url_page.isdigit() else page_id
        if effective_page_id:
            return f"{effective_page_id}_{post_num}"

    # /reel/REEL_ID
    match = re.search(r"/reel/(\d+)$", path)
    if match and page_id:
        return f"{page_id}_{match.group(1)}"

    # /videos/VIDEO_ID
    match = re.search(r"/videos/(\d+)$", path)
    if match and page_id:
        return f"{page_id}_{match.group(1)}"

    # ── Graph API fallback ──────────────────────────────────────────────
    # Handles pfbid, share links, and anything else regex can't parse.
    if token:
        resolved = _resolve_facebook_url_via_api(url, token)
        if resolved:
            return resolved

    return None


def _resolve_facebook_url_via_api(url, token):
    """Ask the Graph API to resolve a URL to its object ID.

    Works for pfbid, share links, and any valid Facebook URL.
    Returns the object ID (usually PAGE_ID_POST_ID) or None.
    """
    try:
        response = http_requests.get(
            "https://graph.facebook.com/v19.0/",
            params={"id": url, "fields": "id", "access_token": token},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        logger.warning("Graph API URL lookup failed for %s: %s", url, e)
        return None


def extract_post_id_from_url(input_value, platform, store_settings=None):
    """Extract a webhook-compatible post ID from a URL or raw ID.

    Accepts either:
      - A full URL (https://...) — extracts the post ID automatically
      - A raw post ID string — returned as-is

    Returns (post_id, error_message). On success error_message is None.
    On failure post_id is None and error_message explains what went wrong.
    """
    input_value = input_value.strip()

    if not input_value:
        return None, "الرابط أو الـ Post ID مطلوب."

    # Not a URL — treat as raw post ID (backward compatible)
    if not input_value.startswith("http"):
        return input_value, None

    if platform == "instagram":
        post_id = _extract_instagram_id(input_value)
        if post_id:
            return post_id, None
        return None, "مقدرش أستخرج الـ Media ID من رابط انستجرام ده. تأكد إن الرابط صحيح."

    elif platform == "facebook":
        page_id = getattr(store_settings, "facebook_page_id", None) if store_settings else None
        token = None
        if store_settings:
            token = getattr(store_settings, "messenger_access_token", None) or getattr(
                store_settings, "meta_access_token", None
            )

        post_id = _extract_facebook_id(input_value, page_id, token)
        if post_id:
            return post_id, None

        if not page_id:
            return None, "مقدرش أستخرج الـ Post ID. لازم تربط الـ Facebook Page ID في إعدادات الستور الأول."
        return None, "مقدرش أستخرج الـ Post ID من الرابط ده. جرب تنسخ الرابط من البوست نفسه."

    return None, "المنصة غير معروفة."
