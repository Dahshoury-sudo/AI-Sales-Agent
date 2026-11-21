"""Connecting a store's Facebook Page + Instagram: the dashboard check, webhook verification, and
which ID the agent sends from."""
import json
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from products import tasks
from products.models import Store, StoreSettings
from products.services.meta_connection import GRAPH_URL, REQUIRED_SCOPES

PAGE = "1110001"
IG = "17841400000000001"
APP = "9990001"
TOKEN = "EAAPageTokenSecretValue"
SECRET = "app-secret-value"
REQUEST = "products.services.meta_connection.requests.request"


def graph_response(payload, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = payload
    return response


class FakeMeta:
    """Answers the Graph calls the connection check makes. `errors` maps a call key to Meta's message."""

    def __init__(self, page_id=PAGE, **state):
        self.page_id = page_id
        self.me = state.get("me", {"id": page_id, "name": "Perfamix", "metadata": {"type": "page"}})
        self.app = {"id": APP, "name": "Perfamix Agent"}
        self.secret = state.get("secret", SECRET)
        self.debug = state.get("debug", {"expires_at": 0, "scopes": list(REQUIRED_SCOPES)})
        self.page = state.get("page", {"id": page_id,
                                       "instagram_business_account": {"id": IG, "username": "perfamix"}})
        self.subscribed = state.get("subscribed", [{"id": APP, "subscribed_fields": ["feed", "messages"]}])
        self.errors = state.get("errors", {})
        self.calls = []

    def key(self, method, path, params):
        path = path.replace(self.page_id, "page", 1) if path.startswith(self.page_id) else path
        if path == "app" and "|" in params["access_token"]:
            path = "app secret"
        if params.get("platform"):
            path += f" {params['platform']}"
        return f"POST {path}" if method == "POST" else path

    def __call__(self, method, url, params=None, timeout=None):
        params = dict(params or {})
        key = self.key(method, url.removeprefix(f"{GRAPH_URL}/"), params)
        self.calls.append((key, params))
        if key in self.errors:
            return graph_response({"error": {"message": self.errors[key]}}, 400)
        if key == "app secret" and params["access_token"] != f"{APP}|{self.secret}":
            return graph_response({"error": {"message": "Error validating client secret."}}, 400)
        answers = {
            "me": self.me, "app": self.app, "app secret": {"id": APP}, "debug_token": {"data": self.debug},
            "page": self.page, "page/subscribed_apps": {"data": self.subscribed},
            "POST page/subscribed_apps": {"success": True},
            "page/conversations": {"data": []}, "page/conversations instagram": {"data": []},
        }
        if key not in answers:
            raise AssertionError(f"unexpected Graph call {key}")
        return graph_response(answers[key])

    def keys(self):
        return [key for key, _ in self.calls]


class MetaConnectionCheckTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="meta-owner")
        self.store = Store.objects.create(name="Perfamix", owner=self.owner)
        self.settings = StoreSettings.objects.create(
            store=self.store, facebook_page_id=PAGE, instagram_account_id=IG, messenger_access_token=TOKEN,
            meta_app_secret=SECRET, meta_verify_token="wv_verify", comment_reply_messages="ردينا عليك في الخاص",
        )
        self.dash = self.client_for(self.owner)

    @staticmethod
    def client_for(user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client

    def check(self, fake, client=None):
        with patch(REQUEST, side_effect=fake):
            response = (client or self.dash).post(reverse("dashboard-api-meta-check"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    @staticmethod
    def rows(result):
        return {check["key"]: check for check in result["checks"]}

    def assertStates(self, result, **expected):
        rows = self.rows(result)
        self.assertEqual({key: rows[key]["ok"] for key in expected}, expected)

    def test_a_fully_connected_store_is_all_green_and_nothing_changes(self):
        fake = FakeMeta()
        result = self.check(fake)
        self.assertEqual([c["key"] for c in result["checks"]], [
            "page_token", "app", "app_secret", "token_expiry", "permissions", "instagram", "subscription",
            "messenger_access", "instagram_access", "verify_token", "reply_switches", "comment_messages"])
        self.assertTrue(all(c["ok"] for c in result["checks"]), result["checks"])
        self.assertEqual(result["changed"], [])
        self.assertNotIn("POST page/subscribed_apps", fake.keys())

    def test_empty_page_and_instagram_ids_are_filled_and_saved(self):
        StoreSettings.objects.filter(pk=self.settings.pk).update(facebook_page_id="", instagram_account_id="")
        result = self.check(FakeMeta())
        self.assertEqual(result["changed"], ["facebook_page_id", "instagram_account_id"])
        self.assertEqual((result["facebook_page_id"], result["instagram_account_id"]), (PAGE, IG))
        self.settings.refresh_from_db()
        self.assertEqual((self.settings.facebook_page_id, self.settings.instagram_account_id), (PAGE, IG))
        self.assertStates(result, page_token=True, instagram=True)

    def test_a_wrong_instagram_id_is_replaced_by_the_account_linked_to_the_page(self):
        StoreSettings.objects.filter(pk=self.settings.pk).update(instagram_account_id="perfamix")
        result = self.check(FakeMeta())
        self.assertEqual(result["changed"], ["instagram_account_id"])
        self.assertIn("perfamix", self.rows(result)["instagram"]["detail"])
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.instagram_account_id, IG)

    def test_page_without_a_linked_instagram_account(self):
        result = self.check(FakeMeta(page={"id": PAGE}))
        self.assertStates(result, instagram=False, instagram_access=None, messenger_access=True)
        self.assertIn("Linked accounts", self.rows(result)["instagram"]["detail"])

    def test_a_user_token_stops_the_check_and_says_so(self):
        fake = FakeMeta(me={"id": "42", "name": "Mohamed", "metadata": {"type": "user"}})
        result = self.check(fake)
        self.assertStates(result, page_token=False)
        self.assertIn("مش توكن صفحة", self.rows(result)["page_token"]["detail"])
        self.assertEqual(fake.keys(), ["me"])
        self.assertNotIn("app", self.rows(result))

    def test_a_token_for_another_page_is_not_silently_switched_to(self):
        fake = FakeMeta(me={"id": "5550001", "name": "Other page", "metadata": {"type": "page"}})
        result = self.check(fake)
        detail = self.rows(result)["page_token"]["detail"]
        self.assertFalse(self.rows(result)["page_token"]["ok"])
        self.assertIn("5550001", detail)
        self.assertIn(PAGE, detail)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.facebook_page_id, PAGE)

    def test_an_invalid_token_shows_metas_reason(self):
        result = self.check(FakeMeta(errors={"me": "Error validating access token: Session has expired."}))
        self.assertStates(result, page_token=False, verify_token=True)
        self.assertIn("Session has expired", self.rows(result)["page_token"]["detail"])

    def test_meta_unreachable(self):
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            result = self.dash.post(reverse("dashboard-api-meta-check"), {}, format="json").json()
        self.assertIn("مقدرناش نوصل", self.rows(result)["page_token"]["detail"])

    def test_missing_page_subscription_is_added_without_dropping_other_fields(self):
        for subscribed, expected in (
            ([{"id": APP, "subscribed_fields": ["messaging_postbacks"]},
              {"id": "other-app", "subscribed_fields": ["feed"]}], "feed,messages,messaging_postbacks"),
            ([{"id": "other-app", "subscribed_fields": ["feed", "messages"]}], "feed,messages"),
            ([], "feed,messages"),
        ):
            with self.subTest(subscribed=subscribed):
                fake = FakeMeta(subscribed=subscribed)
                result = self.check(fake)
                posted = [params for key, params in fake.calls if key == "POST page/subscribed_apps"]
                self.assertEqual([p["subscribed_fields"] for p in posted], [expected])
                self.assertStates(result, subscription=True)

    def test_subscription_failure_is_reported(self):
        fake = FakeMeta(subscribed=[], errors={"POST page/subscribed_apps": "(#200) Requires pages_manage_metadata"})
        result = self.check(fake)
        self.assertStates(result, subscription=False)
        self.assertIn("pages_manage_metadata", self.rows(result)["subscription"]["detail"])

    def test_wrong_or_missing_app_secret_skips_the_token_details(self):
        for secret in ("wrong-secret", ""):
            with self.subTest(secret=secret):
                StoreSettings.objects.filter(pk=self.settings.pk).update(meta_app_secret=secret)
                fake = FakeMeta()
                result = self.check(fake)
                self.assertStates(result, app_secret=False, token_expiry=None, permissions=None, subscription=True)
                self.assertNotIn("debug_token", fake.keys())

    def test_expiring_token_and_missing_permissions_are_named(self):
        scopes = [s for s in REQUIRED_SCOPES if s not in ("instagram_manage_comments", "pages_manage_engagement")]
        result = self.check(FakeMeta(debug={"expires_at": 1790000000, "scopes": scopes}))
        self.assertStates(result, token_expiry=False, permissions=False)
        detail = self.rows(result)["permissions"]["detail"]
        self.assertIn("instagram_manage_comments", detail)
        self.assertIn("pages_manage_engagement", detail)

    def test_conversation_access_errors_are_shown_per_platform(self):
        result = self.check(FakeMeta(errors={
            "page/conversations instagram": "(#3) Application does not have the capability to make this API call."}))
        self.assertStates(result, messenger_access=True, instagram_access=False)
        self.assertIn("capability", self.rows(result)["instagram_access"]["detail"])

    def test_local_settings_are_listed(self):
        StoreSettings.objects.filter(pk=self.settings.pk).update(
            meta_verify_token="", instagram_comment_replies_enabled=False, comment_reply_messages="")
        result = self.check(FakeMeta())
        self.assertStates(result, verify_token=False, reply_switches=False, comment_messages=False)
        self.assertIn("تعليقات انستجرام", self.rows(result)["reply_switches"]["detail"])

    def test_no_token_is_refused_before_calling_meta(self):
        StoreSettings.objects.filter(pk=self.settings.pk).update(messenger_access_token="", meta_access_token="")
        with patch(REQUEST) as request:
            response = self.dash.post(reverse("dashboard-api-meta-check"), {}, format="json")
        self.assertEqual(response.status_code, 400)
        request.assert_not_called()

    def test_tokens_and_secret_never_reach_the_response_or_the_log(self):
        fake = FakeMeta(subscribed=[], errors={"POST page/subscribed_apps": "denied",
                                               "page/conversations": "denied"})
        with self.assertLogs("products.services.meta_connection", "WARNING") as logs:
            result = self.check(fake)
        text = json.dumps(result, ensure_ascii=False) + "\n".join(logs.output)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(SECRET, text)

    def test_each_owner_checks_only_their_own_store(self):
        other_owner = User.objects.create_user(username="other-meta-owner")
        other = Store.objects.create(name="Other", owner=other_owner)
        StoreSettings.objects.create(store=other, facebook_page_id="7770001", messenger_access_token="other-token",
                                     meta_app_secret=SECRET)
        fake = FakeMeta(page_id="7770001")
        self.check(fake, client=self.client_for(other_owner))
        self.assertEqual(fake.calls[0][1]["access_token"], "other-token")
        self.settings.refresh_from_db()
        self.assertEqual((self.settings.facebook_page_id, self.settings.instagram_account_id), (PAGE, IG))

    def test_anonymous_request_is_refused(self):
        self.assertEqual(APIClient().post(reverse("dashboard-api-meta-check")).status_code, 401)

    def test_settings_show_the_webhook_url_to_paste_into_meta(self):
        url = self.dash.get(reverse("dashboard-api-settings")).json()["webhook_url"]
        self.assertEqual(url, "https://testserver/api/webhooks/meta/")  # Meta only calls https
        with override_settings(ALLOWED_HOSTS=["testserver", "localhost"]):
            local = self.dash.get(reverse("dashboard-api-settings"), HTTP_HOST="localhost:8000").json()
        self.assertEqual(local["webhook_url"], "http://localhost:8000/api/webhooks/meta/")


class WebhookVerificationTests(TestCase):
    def setUp(self):
        store = Store.objects.create(name="Verify store", owner=User.objects.create_user(username="verify-owner"))
        StoreSettings.objects.create(store=store, meta_verify_token="wv_verify")

    def verify(self, **params):
        return self.client.get(reverse("meta-webhook"), params)

    def test_meta_gets_its_challenge_back_only_with_the_right_token(self):
        ok = self.verify(**{"hub.mode": "subscribe", "hub.verify_token": "wv_verify", "hub.challenge": "1158201444"})
        self.assertEqual((ok.status_code, ok.content), (200, b"1158201444"))
        wrong = self.verify(**{"hub.mode": "subscribe", "hub.verify_token": "guess", "hub.challenge": "1"})
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(self.verify(**{"hub.challenge": "1"}).status_code, 400)


class PrivateReplySenderTests(TestCase):
    def test_private_replies_to_comments_are_sent_from_the_page_on_both_platforms(self):
        cache.clear()
        store = Store.objects.create(name="Reply store", owner=User.objects.create_user(username="reply-owner"))
        StoreSettings.objects.create(store=store, facebook_page_id=PAGE, instagram_account_id=IG,
                                     messenger_access_token=TOKEN)
        for platform in ("facebook", "instagram"):
            with self.subTest(platform=platform), \
                 patch("products.tasks.route", return_value=("الإجابة", "")), \
                 patch("products.tasks.sanitize_reply", side_effect=lambda reply, conversation: reply), \
                 patch("products.tasks.fetch_post_content", return_value=""), \
                 patch("products.tasks.reply_to_comment"), patch("products.tasks.reply_to_ig_comment"), \
                 patch("products.tasks.send_private_reply") as private:
                tasks.process_comment_task(store.pk, platform, f"{platform}-comment", f"{platform}-customer",
                                           "بكام؟", "post")
            private.assert_called_once()
            self.assertEqual(private.call_args.args[:2], (PAGE, f"{platform}-comment"))
