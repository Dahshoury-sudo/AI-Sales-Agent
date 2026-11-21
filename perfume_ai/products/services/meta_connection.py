"""Check a store's Facebook Page + Instagram connection against Meta, and fix what can be fixed.

The agent answers Messenger DMs, Instagram DMs and comments only when every piece lines up: a
never-expiring Page token for the right Page, the Instagram business account ID that Meta's webhook
sends, the Page subscribed to the app's webhook, and the app's permissions granted. Each of these
fails silently when wrong — Meta just never calls, or rejects the reply — so this walks through
them one by one and says, in Arabic, which one is off.

It fixes only what has exactly one right answer: an empty Page ID (taken from the Page token), the
Instagram ID (the account linked to the Page), and the Page's webhook subscription. Tokens and the
app secret never appear in the result or the log.
"""
import logging

import requests

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v19.0"
# Page webhook fields the agent needs: DMs and comments.
REQUIRED_PAGE_FIELDS = ("feed", "messages")
REQUIRED_SCOPES = (
    "pages_messaging", "pages_manage_metadata", "pages_read_engagement", "pages_manage_engagement",
    "instagram_basic", "instagram_manage_messages", "instagram_manage_comments",
)
REPLY_SWITCHES = {
    "messenger_replies_enabled": "رسائل ماسنجر",
    "instagram_replies_enabled": "رسائل انستجرام",
    "facebook_comment_replies_enabled": "تعليقات فيسبوك",
    "instagram_comment_replies_enabled": "تعليقات انستجرام",
}
TOKEN_HELP = "اعمل توكن الصفحة من التطبيق: Messenger API Settings ← Generate access token للصفحة."


class GraphError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def page_token(store_settings):
    return store_settings.messenger_access_token or store_settings.meta_access_token or ""


def _graph(method, path, token, params=None):
    """Call the Graph API; return the JSON body or raise GraphError with Meta's own message."""
    params = {**(params or {}), "access_token": token}
    try:
        response = requests.request(method, f"{GRAPH_URL}/{path}", params=params, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Meta connection check %s %s: %s", method, path, type(exc).__name__)
        raise GraphError("مقدرناش نوصل لفيسبوك دلوقتي. جرب تاني بعد شوية.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or "error" in data:
        message = (data.get("error") or {}).get("message") or f"HTTP {response.status_code}"
        logger.warning("Meta connection check %s %s failed: HTTP %s: %s", method, path,
                       response.status_code, message)
        raise GraphError(message)
    return data


class _Report:
    def __init__(self):
        self.checks = []

    def add(self, key, label, ok, detail):
        """ok: True, False, or None when the check could not run because an earlier one failed."""
        self.checks.append({"key": key, "label": label, "ok": ok, "detail": detail})


def check_connection(store_settings, fix=True):
    """Run every check. Returns (checks, changed_fields); the caller saves changed_fields."""
    report, changed = _Report(), []
    token = page_token(store_settings)
    _check_remote(store_settings, token, fix, report, changed)
    _check_local(store_settings, report)
    return report.checks, changed


def _check_remote(store_settings, token, fix, report, changed):
    # 1. The token belongs to a Page, and to this store's Page.
    try:
        me = _graph("GET", "me", token, {"fields": "id,name", "metadata": "1"})
    except GraphError as exc:
        report.add("page_token", "توكن الصفحة", False, f"التوكن مش شغال: {exc.message} — {TOKEN_HELP}")
        return
    token_type = (me.get("metadata") or {}).get("type")
    page_id = (store_settings.facebook_page_id or "").strip()
    if token_type and token_type != "page":
        report.add("page_token", "توكن الصفحة", False,
                   f"ده توكن حساب «{me.get('name', '')}» مش توكن صفحة. {TOKEN_HELP}")
        return
    if not page_id:
        if not fix:
            report.add("page_token", "توكن الصفحة", False, "الـ Facebook Page ID فاضي.")
            return
        page_id = store_settings.facebook_page_id = me["id"]
        changed.append("facebook_page_id")
        report.add("page_token", "توكن الصفحة", True, f"توكن صفحة «{me.get('name', '')}» — اتحط الـ Page ID تلقائياً.")
    elif me["id"] != page_id:
        report.add("page_token", "توكن الصفحة", False,
                   f"التوكن ده لصفحة «{me.get('name', '')}» ({me['id']}) مش الصفحة المسجلة ({page_id}). "
                   f"اعمل توكن للصفحة الصح أو صحح الـ Page ID.")
        return
    else:
        report.add("page_token", "توكن الصفحة", True, f"توكن صفحة «{me.get('name', '')}».")

    # 2. The app this token was issued by — its webhook is the one Meta will call.
    try:
        app = _graph("GET", "app", token, {"fields": "id,name"})
    except GraphError as exc:
        report.add("app", "التطبيق", False, exc.message)
        app = None
    else:
        report.add("app", "التطبيق", True, f"التوكن تابع لتطبيق «{app.get('name', '')}».")

    # 3-4. App secret (signs every webhook) and, with it, the token's expiry and permissions.
    _check_secret_and_token(store_settings, token, app, report)

    # 5. The Instagram business account linked to the Page: its ID is what the webhook sends.
    ig_id = _check_instagram(store_settings, token, page_id, fix, report, changed)

    # 6. The Page is subscribed to the app's webhook for DMs and comments.
    _check_subscription(token, page_id, app, fix, report)

    # 7. The token can actually reach the conversations it will answer.
    for key, label, params, needed in (
        ("messenger_access", "رسائل ماسنجر", {"limit": "1"}, True),
        ("instagram_access", "رسائل انستجرام", {"platform": "instagram", "limit": "1"}, bool(ig_id)),
    ):
        if not needed:
            report.add(key, label, None, "محتاج حساب انستجرام مربوط بالصفحة الأول.")
            continue
        try:
            _graph("GET", f"{page_id}/conversations", token, params)
        except GraphError as exc:
            report.add(key, label, False, exc.message)
        else:
            report.add(key, label, True, "البوت يقدر يقرا الرسايل ويرد.")


def _check_secret_and_token(store_settings, token, app, report):
    secret = store_settings.meta_app_secret or ""
    if not secret:
        report.add("app_secret", "App Secret", False, "حط الـ App Secret من إعدادات التطبيق (App settings ← Basic).")
    elif not app:
        report.add("app_secret", "App Secret", None, "محتاج التطبيق يتعرف الأول.")
    else:
        try:
            _graph("GET", "app", f"{app['id']}|{secret}", {"fields": "id"})
        except GraphError:
            report.add("app_secret", "App Secret", False,
                       f"الـ App Secret ده مش بتاع تطبيق «{app.get('name', '')}». انسخه تاني من App settings ← Basic.")
            secret = ""
        else:
            report.add("app_secret", "App Secret", True, "صح.")
    if not (secret and app):
        report.add("token_expiry", "مدة التوكن", None, "محتاج App Secret صح عشان نفحصها.")
        report.add("permissions", "الصلاحيات", None, "محتاج App Secret صح عشان نفحصها.")
        return

    try:
        info = _graph("GET", "debug_token", f"{app['id']}|{secret}", {"input_token": token}).get("data") or {}
    except GraphError as exc:
        report.add("token_expiry", "مدة التوكن", False, exc.message)
        report.add("permissions", "الصلاحيات", None, "مقدرناش نقرا صلاحيات التوكن.")
        return
    expires_at = info.get("expires_at") or 0
    if expires_at:
        report.add("token_expiry", "مدة التوكن", False, f"التوكن ده بيخلص. {TOKEN_HELP}")
    else:
        report.add("token_expiry", "مدة التوكن", True, "مش بيخلص.")
    scopes = set(info.get("scopes") or [])
    missing = [scope for scope in REQUIRED_SCOPES if scope not in scopes]
    if missing:
        report.add("permissions", "الصلاحيات", False,
                   "ناقص: " + "، ".join(missing) + ". ضيفها للتطبيق واعمل التوكن تاني.")
    else:
        report.add("permissions", "الصلاحيات", True, "كل الصلاحيات المطلوبة موجودة.")


def _check_instagram(store_settings, token, page_id, fix, report, changed):
    try:
        page = _graph("GET", page_id, token, {"fields": "instagram_business_account{id,username}"})
    except GraphError as exc:
        report.add("instagram", "حساب انستجرام", False, exc.message)
        return None
    account = page.get("instagram_business_account") or {}
    ig_id = account.get("id")
    if not ig_id:
        report.add("instagram", "حساب انستجرام", False,
                   "مفيش حساب انستجرام بزنس مربوط بالصفحة. اربطه من إعدادات الصفحة ← Linked accounts.")
        return None
    # Left-to-right isolate, or the Arabic line around it renders the handle as "perfamix@".
    handle = f"⁦@{account['username']}⁩" if account.get("username") else ig_id
    stored = (store_settings.instagram_account_id or "").strip()
    if stored == ig_id:
        report.add("instagram", "حساب انستجرام", True, f"{handle} مربوط.")
    elif fix:
        store_settings.instagram_account_id = ig_id
        changed.append("instagram_account_id")
        note = f"اتغير الـ Instagram ID من {stored} لـ {ig_id}." if stored else "اتحط الـ Instagram ID تلقائياً."
        report.add("instagram", "حساب انستجرام", True, f"{handle} مربوط. {note}")
    else:
        report.add("instagram", "حساب انستجرام", False, f"الـ Instagram ID المسجل غلط، الصح {ig_id} ({handle}).")
    return ig_id


def _check_subscription(token, page_id, app, fix, report):
    label = "اشتراك الصفحة في الـ Webhook"
    if not app:
        report.add("subscription", label, None, "محتاج التطبيق يتعرف الأول.")
        return
    try:
        apps = _graph("GET", f"{page_id}/subscribed_apps", token).get("data") or []
    except GraphError as exc:
        report.add("subscription", label, False, exc.message)
        return
    ours = next((a for a in apps if str(a.get("id")) == str(app["id"])), None)
    existing = set((ours or {}).get("subscribed_fields") or [])
    missing = [field for field in REQUIRED_PAGE_FIELDS if field not in existing]
    if not missing:
        report.add("subscription", label, True, "الصفحة مشتركة في الرسائل والتعليقات.")
        return
    if not fix:
        report.add("subscription", label, False, "الصفحة مش مشتركة في: " + "، ".join(missing) + ".")
        return
    try:
        # Replaces the app's field list for this Page, so keep whatever was already there.
        _graph("POST", f"{page_id}/subscribed_apps", token,
               {"subscribed_fields": ",".join(sorted(existing | set(REQUIRED_PAGE_FIELDS)))})
    except GraphError as exc:
        report.add("subscription", label, False, f"مقدرناش نشترك: {exc.message}")
    else:
        report.add("subscription", label, True, "اشتركنا الصفحة في الرسائل والتعليقات.")


def _check_local(store_settings, report):
    if (store_settings.meta_verify_token or "").strip():
        report.add("verify_token", "Verify Token", True, "موجود — لازم يكون نفس اللي في إعدادات الـ Webhook في التطبيق.")
    else:
        report.add("verify_token", "Verify Token", False, "اعمل Verify Token واحفظه، وحط نفسه في إعدادات الـ Webhook في التطبيق.")

    off = [label for field, label in REPLY_SWITCHES.items() if not getattr(store_settings, field)]
    if off:
        report.add("reply_switches", "الردود التلقائية", False, "متوقفة على: " + "، ".join(off) + ".")
    else:
        report.add("reply_switches", "الردود التلقائية", True, "شغالة على الرسائل والتعليقات.")

    if (store_settings.comment_reply_messages or "").strip():
        report.add("comment_messages", "رسائل الرد على التعليقات", True, "موجودة.")
    else:
        report.add("comment_messages", "رسائل الرد على التعليقات", False,
                   "مفيش رسائل — البوت هيرد على التعليقات بـ «✅ Check your DM!».")
