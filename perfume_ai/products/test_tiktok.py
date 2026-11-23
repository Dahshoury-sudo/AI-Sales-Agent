"""TikTok DMs: webhook, parser, signature, sending, tokens, OAuth connect.

TikTok is never reached: FakeTikTok stands in for requests.request inside
tiktok_service and fails on any call a test did not expect.

The payload fixtures below are modelled on integrator descriptions, not on a real
TikTok webhook. When the first real one arrives, replace receive() with it and fix
tiktok_service.parse_webhook until these tests pass again (docs/tiktok-setup.md).
"""
import hashlib
import hmac
import io
import json
import time
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from django.contrib.auth.models import User
from django.core import signing
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from products import tasks
from products.models import Conversation, InboundEvent, Message, Notification, Store, StoreSettings
from products.services import tiktok_service
from products.services.meta_service import UncertainDelivery, send_platform_message
from products.services.response_policy import message_replies_enabled

SECRET = "tiktok-secret"
TIKTOK = dict(
    TIKTOK_APP_ID="app-1",
    TIKTOK_APP_SECRET=SECRET,
    TIKTOK_REDIRECT_URI="https://example.test/dashboard/tiktok/callback/",
    TIKTOK_API_BASE="https://tiktok.invalid/open_api/v1.3",
    TIKTOK_AUTH_URL="https://tiktok.invalid/v2/auth/authorize",
    TIKTOK_WEBHOOK_MAX_AGE=0,
)
API = TIKTOK["TIKTOK_API_BASE"]
BUSINESS = "biz-open-id-1"
CONVERSATION = "conv-0001-abcdefgh"
CUSTOMER = "user-9"
BOTTLE = "https://cdn.example.test/bottle.jpg"


def receive(kind="text", message_id="msg-1", content_as_string=False, event="im_receive_msg",
            sender=CUSTOMER, top_level_business=True, **content_extra):
    """An inbound DM webhook body, as integrators describe TikTok's."""
    content = {"message_id": message_id, "conversation_id": CONVERSATION, "type": kind,
               "timestamp": 1764000000000, "from": sender, "to": BUSINESS,
               "from_user": {"id": sender}, "to_user": {"id": BUSINESS}}
    if kind == "text":
        content["text"] = {"body": "عندكم عطر عود؟"}
    elif kind == "image":
        content["image"] = {"media_id": "media-1"}
    elif kind == "share_post":
        content["share_post"] = {"embed_url": "https://www.tiktok.com/embed/v2/123"}
    elif kind == "sticker":
        content["sticker"] = {"id": "st-1"}
    content.update(content_extra)
    payload = {"event": event, "create_time": 1764000000,
               "content": json.dumps(content) if content_as_string else content}
    if top_level_business:
        payload["user_openid"] = BUSINESS
    return payload


def sign(body, secret=SECRET, timestamp=None):
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},s={digest}"


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, content=b""):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self._content = content

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    def iter_content(self, size):
        for start in range(0, len(self._content), size):
            yield self._content[start:start + size]

    def close(self):
        pass


def ok(data=None):
    return FakeResponse(200, {"code": 0, "message": "OK", "data": data or {}})


def refused(code=40001, message="refused"):
    return FakeResponse(200, {"code": code, "message": message, "data": {}})


def image(size=1024, content_type="image/jpeg"):
    return FakeResponse(200, headers={"Content-Type": content_type}, content=b"x" * size)


class FakeTikTok:
    """Stands in for requests.request in tiktok_service. Routes match on method and the
    URL's ending; a route value is a response, an exception to raise, or a list of
    them used in turn."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, method, ending, *responses):
        self.routes.append([method, ending, list(responses)])
        return self

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for route_method, ending, responses in self.routes:
            if route_method == method and url.endswith(ending) and responses:
                response = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"Unexpected TikTok call: {method} {url}")

    def urls(self):
        return [call["url"].replace(API + "/", "") for call in self.calls]

    def sent(self):
        return [call for call in self.calls if call["url"].endswith("business/message/send/")]


class FakeTikTokMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.tiktok = FakeTikTok()
        self.enterContext(patch("products.services.tiktok_service.requests.request", self.tiktok))


def connect(store, **overrides):
    now = timezone.now()
    values = dict(tiktok_business_id=BUSINESS, tiktok_username="perfamix",
                  tiktok_access_token="access-1", tiktok_refresh_token="refresh-1",
                  tiktok_access_expires_at=now + timedelta(hours=20),
                  tiktok_refresh_expires_at=now + timedelta(days=25),
                  bottle_image_url=BOTTLE)
    values.update(overrides)
    settings_row, _ = StoreSettings.objects.get_or_create(store=store)
    StoreSettings.objects.filter(pk=settings_row.pk).update(**values)
    # get_or_create cached the pre-update row on store.settings; code under test must
    # read the row as saved.
    store._state.fields_cache.pop("settings", None)
    return StoreSettings.objects.get(pk=settings_row.pk)


# ── Parser ──────────────────────────────────────────────────────────────────


class ParserTests(SimpleTestCase):
    def parse_one(self, payload):
        messages = tiktok_service.parse_webhook(payload)
        self.assertEqual(len(messages), 1)
        return messages[0]

    def test_text_message(self):
        message = self.parse_one(receive())
        self.assertEqual((message.business_id, message.conversation_id, message.message_id,
                          message.sender_id, message.kind, message.text),
                         (BUSINESS, CONVERSATION, "msg-1", CUSTOMER, "text", "عندكم عطر عود؟"))
        self.assertEqual(message.timestamp_ms, 1764000000000)

    def test_content_may_be_a_json_string(self):
        self.assertEqual(self.parse_one(receive(content_as_string=True)).text, "عندكم عطر عود؟")

    def test_image_share_post_and_sticker_kinds(self):
        self.assertEqual(self.parse_one(receive("image")).media_id, "media-1")
        self.assertEqual(self.parse_one(receive("share_post")).url, "https://www.tiktok.com/embed/v2/123")
        self.assertEqual(self.parse_one(receive("sticker")).kind, "sticker")

    def test_eu_receive_event_is_a_customer_message(self):
        self.assertEqual(self.parse_one(receive(event="im_receive_msg_eu")).event, "im_receive_msg_eu")

    def test_echoes_and_other_events_are_dropped(self):
        for event in ("im_send_msg", "something_else", ""):
            with self.subTest(event=event):
                self.assertEqual(tiktok_service.parse_webhook(receive(event=event)), [])

    def test_message_sent_by_the_business_itself_is_dropped(self):
        self.assertEqual(tiktok_service.parse_webhook(receive(sender=BUSINESS)), [])

    def test_business_id_found_without_top_level_openid(self):
        self.assertEqual(self.parse_one(receive(top_level_business=False)).business_id, BUSINESS)
        payload = receive(top_level_business=False, to_user={}, to="")
        payload["business_id"] = "explicit-biz"
        self.assertEqual(self.parse_one(payload).business_id, "explicit-biz")

    def test_unreadable_message_is_logged_by_keys_never_by_text(self):
        with self.assertLogs("products.services.tiktok_service", "WARNING") as logs:
            self.assertEqual(tiktok_service.parse_webhook(receive(conversation_id="")), [])
        self.assertIn("conversation_id", logs.output[0])
        self.assertNotIn("عود", "".join(logs.output))

    def test_garbage_is_ignored(self):
        for payload in (None, [], "text"):
            with self.subTest(payload=payload):
                self.assertEqual(tiktok_service.parse_webhook(payload), [])
        with self.assertLogs("products.services.tiktok_service", "WARNING"):
            self.assertEqual(tiktok_service.parse_webhook({"event": "im_receive_msg", "content": "{not json"}), [])


# ── Signature ───────────────────────────────────────────────────────────────


@override_settings(**TIKTOK)
class SignatureTests(SimpleTestCase):
    body = b'{"event":"im_receive_msg"}'

    def test_valid_signature(self):
        self.assertTrue(tiktok_service.verify_signature(sign(self.body), self.body))

    def test_tampered_body_wrong_secret_missing_or_malformed_header(self):
        good = sign(self.body)
        for header, body in ((good, self.body + b" "), (sign(self.body, "other"), self.body),
                             (None, self.body), ("", self.body), ("garbage", self.body),
                             ("t=abc,s=00", self.body), ("t=1,s=", self.body),
                             (good.replace("s=", "s=ü"), self.body)):
            with self.subTest(header=header):
                self.assertFalse(tiktok_service.verify_signature(header, body))

    @override_settings(TIKTOK_APP_SECRET="")
    def test_no_secret_rejects_everything(self):
        self.assertFalse(tiktok_service.verify_signature(sign(self.body, ""), self.body))

    def test_age_checked_only_when_configured(self):
        old = sign(self.body, timestamp=int(time.time()) - 3600)
        self.assertTrue(tiktok_service.verify_signature(old, self.body))
        with override_settings(TIKTOK_WEBHOOK_MAX_AGE=300):
            self.assertFalse(tiktok_service.verify_signature(old, self.body))
            self.assertTrue(tiktok_service.verify_signature(sign(self.body), self.body))


# ── Webhook view ────────────────────────────────────────────────────────────


@override_settings(**TIKTOK)
class WebhookViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.store = Store.objects.create(name="TikTok store")
        connect(self.store)
        self.text = self.enterContext(patch("products.tasks.process_message_async"))
        self.attachment = self.enterContext(patch("products.tasks.process_attachment_async"))

    def post(self, payload, valid=True, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        headers = {"HTTP_TIKTOK_SIGNATURE": sign(body)} if valid else {}
        return self.client.post(reverse("tiktok-webhook"), body, content_type="application/json", **headers)

    def test_unsigned_request_is_refused_and_nothing_dispatched(self):
        self.assertEqual(self.post(receive(), valid=False).status_code, 401)
        self.text.assert_not_called()

    def test_text_message_dispatched_to_the_dm_pipeline(self):
        self.assertEqual(self.post(receive()).status_code, 200)
        self.text.assert_called_once_with(self.store.id, "tiktok", CONVERSATION, "عندكم عطر عود؟",
                                          source_id="msg-1")

    def test_image_and_shared_video_go_through_the_attachment_flow(self):
        self.post(receive("image", message_id="img-1"))
        self.post(receive("share_post", message_id="post-1"))
        self.assertEqual(self.attachment.call_args_list[0].args,
                         (self.store.id, "tiktok", CONVERSATION, "tiktok-media:media-1", ""))
        self.assertEqual(self.attachment.call_args_list[0].kwargs, {"source_id": "img-1"})
        self.assertEqual(self.attachment.call_args_list[1].args,
                         (self.store.id, "tiktok", CONVERSATION, "https://www.tiktok.com/embed/v2/123",
                          "🎬 شارك فيديو تيك توك"))

    def test_sticker_echo_self_and_unknown_business_are_ignored(self):
        other = receive()
        other["user_openid"] = "someone-else"
        other["content"]["to_user"] = {"id": "someone-else"}
        for payload in (receive("sticker"), receive(event="im_send_msg"), receive(sender=BUSINESS), other):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 200)
        self.text.assert_not_called()
        self.attachment.assert_not_called()

    def test_bad_json_is_400(self):
        self.assertEqual(self.post(None, raw=b"{not json").status_code, 400)

    def test_never_throttled(self):
        for i in range(40):
            self.assertEqual(self.post(receive(message_id=f"m{i}")).status_code, 200)
        self.assertEqual(self.text.call_count, 40)


@override_settings(**TIKTOK)
class WebhookDedupTests(TestCase):
    """Real dispatch, queued task stubbed: TikTok's at-least-once retries are harmless."""

    def setUp(self):
        cache.clear()
        self.store = Store.objects.create(name="TikTok store")
        connect(self.store)
        self.queued = self.enterContext(patch.object(tasks.process_incoming_message, "delay"))

    def post(self, payload):
        body = json.dumps(payload).encode()
        return self.client.post(reverse("tiktok-webhook"), body, content_type="application/json",
                                HTTP_TIKTOK_SIGNATURE=sign(body))

    def test_redelivered_message_is_one_event(self):
        self.post(receive())
        self.post(receive())
        self.assertEqual(InboundEvent.objects.filter(platform="tiktok").count(), 1)
        conversation = InboundEvent.objects.get(platform="tiktok").conversation
        self.assertEqual((conversation.platform, conversation.platform_sender_id), ("tiktok", CONVERSATION))

    def test_reused_message_id_with_other_text_does_not_make_tiktok_retry_forever(self):
        self.post(receive())
        changed = receive()
        changed["content"]["text"] = {"body": "different"}
        with self.assertLogs("products.views_tiktok", "ERROR"):
            self.assertEqual(self.post(changed).status_code, 200)
        self.assertEqual(InboundEvent.objects.filter(platform="tiktok").count(), 1)


# ── Through the DM pipeline ─────────────────────────────────────────────────


@override_settings(**TIKTOK)
class EndToEndTests(FakeTikTokMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = Store.objects.create(name="TikTok store")
        self.settings_row = connect(self.store)
        self.route = self.enterContext(patch("products.tasks.route", return_value=("عندنا عود ✨", "")))
        self.enterContext(patch("products.tasks.sanitize_reply", side_effect=lambda reply, conv: reply))

    def deliver(self, payload):
        body = json.dumps(payload).encode()
        with patch.object(tasks.process_incoming_message, "delay") as queued:
            response = self.client.post(reverse("tiktok-webhook"), body, content_type="application/json",
                                        HTTP_TIKTOK_SIGNATURE=sign(body))
        self.assertEqual(response.status_code, 200)
        if queued.called:
            tasks.process_incoming_message(*queued.call_args.args, **queued.call_args.kwargs)
        return InboundEvent.objects.get(platform="tiktok")

    def test_customer_message_answered_on_tiktok(self):
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "tt-out-1"}}))
        event = self.deliver(receive())
        reply = Message.objects.get(pk=event.reply_message_id)
        self.assertEqual(reply.delivery_status, "sent")
        self.assertEqual(reply.delivery_parts["text"], {"status": "sent", "provider_id": "tt-out-1"})
        call = self.tiktok.sent()[0]
        self.assertEqual(call["headers"], {"Access-Token": "access-1"})
        self.assertEqual(call["json"], {"business_id": BUSINESS, "recipient_type": "CONVERSATION",
                                        "recipient": CONVERSATION, "message_type": "TEXT",
                                        "text": {"body": "عندنا عود ✨"}})

    def test_switch_off_keeps_the_message_and_sends_nothing(self):
        StoreSettings.objects.filter(pk=self.settings_row.pk).update(tiktok_replies_enabled=False)
        self.assertFalse(message_replies_enabled(self.store.pk, "tiktok"))
        event = self.deliver(receive())
        self.assertEqual(event.result["reason"], "message_replies_disabled")
        self.assertEqual(event.user_message.content, "عندكم عطر عود؟")
        self.route.assert_not_called()
        self.assertEqual(self.tiktok.calls, [])

    def test_switch_follows_the_field(self):
        self.assertTrue(message_replies_enabled(self.store.pk, "tiktok"))


# ── send_platform_message on TikTok ─────────────────────────────────────────


@override_settings(**TIKTOK)
class SendTests(FakeTikTokMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = Store.objects.create(name="TikTok store")
        self.settings_row = connect(self.store)
        self.conversation = Conversation.objects.create(store=self.store, platform="tiktok",
                                                        platform_sender_id=CONVERSATION)
        self.customer_wrote(timedelta(minutes=5))

    def customer_wrote(self, ago):
        message = Message.objects.create(conversation=self.conversation, role="user", content="hi")
        Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - ago)

    def saved_reply(self, text):
        return Message.objects.create(conversation=self.conversation, role="assistant", content=text,
                                      delivery_status="sending")

    def test_text(self):
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "m1"}}))
        self.assertIs(send_platform_message(self.conversation, "أهلاً"), True)
        self.assertEqual(self.tiktok.sent()[0]["json"]["text"], {"body": "أهلاً"})

    def test_bottle_image_downloaded_uploaded_sent_then_text(self):
        self.tiktok.on("GET", BOTTLE, image())
        self.tiktok.on("POST", "business/message/media/upload/", ok({"media_id": "up-1"}))
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "m-img"}}),
                       ok({"message": {"message_id": "m-txt"}}))
        saved = self.saved_reply("دي الزجاجات [SEND_BOTTLE_IMAGE]")
        self.assertIs(send_platform_message(self.conversation, saved.content, saved_message=saved), True)
        self.assertEqual(self.tiktok.urls(), [BOTTLE, "business/message/media/upload/",
                                              "business/message/send/", "business/message/send/"])
        download, upload, image_send, text_send = self.tiktok.calls
        self.assertIs(download["allow_redirects"], False)
        self.assertEqual(upload["data"], {"business_id": BUSINESS, "media_type": "IMAGE"})
        self.assertEqual(upload["files"]["file"][2], "image/jpeg")
        self.assertEqual(image_send["json"]["image"], {"media_id": "up-1"})
        self.assertEqual(text_send["json"]["text"], {"body": "دي الزجاجات"})
        saved.refresh_from_db()
        self.assertEqual(saved.delivery_parts["image"]["status"], "sent")

    def test_upload_failure_still_sends_the_text_but_reports_failure(self):
        self.tiktok.on("GET", BOTTLE, image())
        self.tiktok.on("POST", "business/message/media/upload/", refused())
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "m-txt"}}))
        saved = self.saved_reply("[SEND_BOTTLE_IMAGE] اتفضل")
        self.assertIs(send_platform_message(self.conversation, saved.content, saved_message=saved), False)
        saved.refresh_from_db()
        self.assertEqual(saved.delivery_parts["image"], {"status": "failed"})
        self.assertEqual(saved.delivery_parts["text"]["status"], "sent")

    def test_unsafe_or_oversized_bottle_image_is_never_uploaded(self):
        for url, response in (("http://cdn.example.test/bottle.jpg", None),
                              (BOTTLE, image(size=tiktok_service.MAX_IMAGE_BYTES + 1)),
                              (BOTTLE, image(content_type="text/html")),
                              (BOTTLE, FakeResponse(302, headers={"Location": "http://10.0.0.1/"}))):
            with self.subTest(url=url, response=response and response.status_code):
                self.tiktok.calls.clear()
                self.tiktok.routes.clear()
                if response:
                    self.tiktok.on("GET", url, response)
                self.assertEqual(tiktok_service.upload_image(BUSINESS, url, "access-1"), "")
                self.assertNotIn("business/message/media/upload/", self.tiktok.urls())

    def test_timeout_is_uncertain(self):
        self.tiktok.on("POST", "business/message/send/", requests.exceptions.Timeout())
        saved = self.saved_reply("أهلاً")
        with self.assertRaises(UncertainDelivery):
            send_platform_message(self.conversation, saved.content, saved_message=saved)
        saved.refresh_from_db()
        self.assertEqual(saved.delivery_parts["text"], {"status": "uncertain"})

    def test_refusal_inside_http_200_is_a_failure(self):
        self.tiktok.on("POST", "business/message/send/", refused(10001, "outside window"))
        self.assertIs(send_platform_message(self.conversation, "أهلاً"), False)

    def test_outside_48_hours_nothing_is_sent(self):
        Message.objects.filter(conversation=self.conversation).update(
            created_at=timezone.now() - timedelta(hours=49))
        self.assertIs(send_platform_message(self.conversation, "أهلاً"), False)
        self.assertEqual(self.tiktok.calls, [])

    def test_inside_48_hours_is_sent(self):
        Message.objects.filter(conversation=self.conversation).update(
            created_at=timezone.now() - timedelta(hours=47))
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "m1"}}))
        self.assertIs(send_platform_message(self.conversation, "أهلاً"), True)

    def test_not_connected_sends_nothing(self):
        connect(self.store, tiktok_access_token="", tiktok_refresh_token="", tiktok_access_expires_at=None)
        self.conversation.refresh_from_db()
        self.assertIs(send_platform_message(Conversation.objects.get(pk=self.conversation.pk), "أهلاً"), False)
        self.assertEqual(self.tiktok.calls, [])

    def test_token_near_expiry_is_refreshed_first(self):
        connect(self.store, tiktok_access_expires_at=timezone.now() + timedelta(minutes=3))
        self.tiktok.on("POST", "tt_user/oauth2/refresh_token/", ok({
            "access_token": "access-2", "refresh_token": "refresh-2",
            "expires_in": 86400, "refresh_token_expires_in": 2592000}))
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "m1"}}))
        conversation = Conversation.objects.get(pk=self.conversation.pk)
        self.assertIs(send_platform_message(conversation, "أهلاً"), True)
        self.assertEqual(self.tiktok.urls(), ["tt_user/oauth2/refresh_token/", "business/message/send/"])
        self.assertEqual(self.tiktok.sent()[0]["headers"], {"Access-Token": "access-2"})


# ── Tokens ──────────────────────────────────────────────────────────────────


@override_settings(**TIKTOK)
class TokenTests(FakeTikTokMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = Store.objects.create(name="TikTok store")
        self.now = timezone.now()

    def refresh_ok(self):
        self.tiktok.on("POST", "tt_user/oauth2/refresh_token/", ok({
            "access_token": "access-2", "refresh_token": "refresh-2",
            "expires_in": 86400, "refresh_token_expires_in": 2592000}))

    def test_fresh_token_used_without_refreshing(self):
        row = connect(self.store)
        self.assertEqual(tiktok_service.get_valid_token(row), "access-1")
        self.assertEqual(self.tiktok.calls, [])

    def test_expiring_token_refreshed_and_rotated_refresh_token_saved(self):
        row = connect(self.store, tiktok_access_expires_at=self.now + timedelta(minutes=5))
        self.refresh_ok()
        self.assertEqual(tiktok_service.get_valid_token(row, now=self.now), "access-2")
        self.assertEqual(self.tiktok.calls[0]["json"], {
            "client_id": "app-1", "client_secret": SECRET,
            "grant_type": "refresh_token", "refresh_token": "refresh-1"})
        saved = StoreSettings.objects.get(pk=row.pk)
        self.assertEqual((saved.tiktok_access_token, saved.tiktok_refresh_token), ("access-2", "refresh-2"))
        self.assertEqual(saved.tiktok_access_expires_at, self.now + timedelta(seconds=86400))
        self.assertEqual(saved.tiktok_refresh_expires_at, self.now + timedelta(seconds=2592000))

    def test_row_already_refreshed_by_another_worker_is_not_refreshed_again(self):
        stale = connect(self.store, tiktok_access_expires_at=self.now + timedelta(minutes=5))
        connect(self.store, tiktok_access_token="access-other",
                tiktok_access_expires_at=self.now + timedelta(hours=23))
        self.assertEqual(tiktok_service.get_valid_token(stale, now=self.now), "access-other")
        self.assertEqual(self.tiktok.calls, [])

    def test_refused_refresh_keeps_tokens_and_tells_the_owner_once_a_day(self):
        row = connect(self.store, tiktok_access_expires_at=self.now - timedelta(minutes=1))
        self.tiktok.on("POST", "tt_user/oauth2/refresh_token/", refused(10002, "invalid refresh token"))
        self.assertEqual(tiktok_service.get_valid_token(row, now=self.now), "")
        self.assertEqual(tiktok_service.get_valid_token(row, now=self.now), "")
        saved = StoreSettings.objects.get(pk=row.pk)
        self.assertEqual((saved.tiktok_business_id, saved.tiktok_refresh_token), (BUSINESS, "refresh-1"))
        self.assertEqual(Notification.objects.filter(store=self.store, type="integration").count(), 1)
        self.assertTrue(tiktok_service.connection_status(saved, now=self.now)["linked"])
        self.assertFalse(tiktok_service.connection_status(saved, now=self.now)["connected"])

    def test_network_error_keeps_a_still_valid_token(self):
        row = connect(self.store, tiktok_access_expires_at=self.now + timedelta(minutes=5))
        self.tiktok.on("POST", "tt_user/oauth2/refresh_token/", requests.exceptions.ConnectionError())
        self.assertEqual(tiktok_service.get_valid_token(row, now=self.now), "access-1")
        self.assertEqual(StoreSettings.objects.get(pk=row.pk).tiktok_access_token, "access-1")
        self.assertFalse(Notification.objects.exists())

    def test_expired_refresh_token_needs_a_reconnect(self):
        row = connect(self.store, tiktok_access_expires_at=self.now - timedelta(hours=1),
                      tiktok_refresh_expires_at=self.now - timedelta(minutes=1))
        self.assertEqual(tiktok_service.get_valid_token(row, now=self.now), "")
        self.assertEqual(self.tiktok.calls, [])
        self.assertTrue(tiktok_service.connection_status(row, now=self.now)["needs_reconnect"])

    def test_beat_task_refreshes_only_tokens_about_to_expire(self):
        connect(self.store, tiktok_access_expires_at=self.now + timedelta(minutes=90))
        fresh_store = Store.objects.create(name="Fresh store")
        connect(fresh_store, tiktok_business_id="biz-fresh",
                tiktok_access_expires_at=self.now + timedelta(hours=20))
        Store.objects.create(name="Never connected")
        self.refresh_ok()
        self.assertEqual(tiktok_service.refresh_expiring_tokens(now=self.now), 1)
        self.assertEqual(len(self.tiktok.calls), 1)
        self.assertEqual(StoreSettings.objects.get(store=self.store).tiktok_access_token, "access-2")
        self.assertEqual(StoreSettings.objects.get(store=fresh_store).tiktok_access_token, "access-1")

    def test_beat_task_warns_once_before_the_refresh_token_runs_out(self):
        connect(self.store, tiktok_refresh_expires_at=self.now + timedelta(days=2))
        tasks.refresh_tiktok_tokens()
        tasks.refresh_tiktok_tokens()
        warnings = Notification.objects.filter(store=self.store, type="integration")
        self.assertEqual(warnings.count(), 1)
        self.assertIn("ربط تيك توك", warnings.get().message)


# ── Dashboard: connect, status, disconnect ──────────────────────────────────


@override_settings(**TIKTOK)
class ConnectApiTests(FakeTikTokMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("tiktok-owner")
        self.store = Store.objects.create(name="TikTok store", owner=self.owner)
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")

    def state(self, store=None, user=None):
        return signing.dumps({"s": (store or self.store).pk, "u": (user or self.owner).pk}, salt="tiktok-oauth")

    def complete(self, state=None, code="auth-code"):
        return self.api.post(reverse("dashboard-api-tiktok-complete"),
                             {"code": code, "state": state or self.state()}, format="json")

    def grant(self, scope="user.info.basic,message.list.read,message.list.send", business=BUSINESS):
        self.tiktok.on("POST", "tt_user/oauth2/token/", ok({
            "open_id": business, "access_token": "access-new", "refresh_token": "refresh-new",
            "expires_in": 86400, "refresh_token_expires_in": 2592000, "scope": scope}))

    def account(self, **data):
        self.tiktok.on("GET", "business/get/", ok({"username": "perfamix", **data}))

    def test_every_endpoint_needs_the_owner_login(self):
        anonymous = APIClient()
        for name, method in (("dashboard-api-tiktok", "get"), ("dashboard-api-tiktok-connect", "post"),
                             ("dashboard-api-tiktok-complete", "post"), ("dashboard-api-tiktok-disconnect", "post")):
            with self.subTest(name=name):
                self.assertEqual(getattr(anonymous, method)(reverse(name)).status_code, 401)

    def test_connect_returns_tiktok_authorize_url_with_signed_state(self):
        response = self.api.post(reverse("dashboard-api-tiktok-connect"))
        self.assertEqual(response.status_code, 200)
        url = urlparse(response.data["authorize_url"])
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", TIKTOK["TIKTOK_AUTH_URL"])
        self.assertEqual(query["client_key"], "app-1")
        self.assertEqual(query["redirect_uri"], TIKTOK["TIKTOK_REDIRECT_URI"])
        self.assertIn("message.list.send", query["scope"].split(","))
        self.assertEqual(signing.loads(query["state"], salt="tiktok-oauth"),
                         {"s": self.store.pk, "u": self.owner.pk})

    @override_settings(TIKTOK_APP_ID="")
    def test_connect_explains_when_the_server_has_no_tiktok_app(self):
        response = self.api.post(reverse("dashboard-api-tiktok-connect"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("TIKTOK_APP_ID", response.data["error"])

    def test_successful_connection_is_saved_without_returning_tokens(self):
        self.grant()
        self.account()
        response = self.complete()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["connected"])
        self.assertEqual(response.data["username"], "perfamix")
        self.assertNotIn("access-new", json.dumps(response.data))
        self.assertNotIn("refresh-new", json.dumps(response.data))
        saved = StoreSettings.objects.get(store=self.store)
        self.assertEqual((saved.tiktok_business_id, saved.tiktok_access_token, saved.tiktok_refresh_token),
                         (BUSINESS, "access-new", "refresh-new"))
        self.assertEqual(self.tiktok.calls[0]["json"]["auth_code"], "auth-code")

    def test_expired_or_forged_state_rejected_before_calling_tiktok(self):
        with patch("dashboard.views_tiktok.STATE_MAX_AGE", -1):
            self.assertEqual(self.complete().status_code, 400)
        self.assertEqual(self.complete(state="forged").status_code, 400)
        self.assertEqual(self.tiktok.calls, [])

    def test_state_issued_to_another_store_is_refused(self):
        other_owner = User.objects.create_user("other-owner")
        other_store = Store.objects.create(name="Other", owner=other_owner)
        self.assertEqual(self.complete(state=self.state(other_store, other_owner)).status_code, 403)
        self.assertEqual(self.tiktok.calls, [])
        self.assertFalse(StoreSettings.objects.filter(tiktok_business_id=BUSINESS).exists())

    def test_missing_message_scopes_refused(self):
        self.grant(scope="user.info.basic")
        response = self.complete()
        self.assertEqual(response.status_code, 400)
        self.assertIn("message.list.read", response.data["error"])
        self.assertFalse(StoreSettings.objects.filter(tiktok_business_id=BUSINESS).exists())

    def test_non_business_account_refused(self):
        self.grant()
        self.account(is_business_account=False)
        self.assertEqual(self.complete().status_code, 400)
        self.assertFalse(StoreSettings.objects.filter(tiktok_business_id=BUSINESS).exists())

    def test_profile_lookup_failure_does_not_block_the_connection(self):
        self.grant()
        self.tiktok.on("GET", "business/get/", refused())
        response = self.complete()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "")

    def test_account_already_on_another_store_is_409(self):
        connect(Store.objects.create(name="First"), tiktok_business_id=BUSINESS)
        self.grant()
        self.account()
        self.assertEqual(self.complete().status_code, 409)

    def test_status_never_contains_tokens(self):
        connect(self.store)
        response = self.api.get(reverse("dashboard-api-tiktok"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["connected"])
        self.assertNotIn("access-1", json.dumps(response.data))
        self.assertNotIn("refresh-1", json.dumps(response.data))

    def test_disconnect_clears_everything(self):
        connect(self.store)
        response = self.api.post(reverse("dashboard-api-tiktok-disconnect"))
        self.assertFalse(response.data["linked"])
        saved = StoreSettings.objects.get(store=self.store)
        self.assertEqual((saved.tiktok_business_id, saved.tiktok_access_token, saved.tiktok_refresh_token),
                         ("", "", ""))

    def test_callback_page_is_served(self):
        self.assertContains(self.client.get(reverse("dashboard-tiktok-callback")), "/dashboard/api/tiktok/complete/")


# ── Staff replies from the handoff inbox ────────────────────────────────────


@override_settings(**TIKTOK)
class StaffReplyTests(FakeTikTokMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("tiktok-staff")
        self.store = Store.objects.create(name="TikTok store", owner=self.owner)
        connect(self.store)
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")
        self.conversation = Conversation.objects.create(store=self.store, platform="tiktok",
                                                        platform_sender_id=CONVERSATION, needs_human=True)

    def customer_wrote(self, ago):
        message = Message.objects.create(conversation=self.conversation, role="user", content="فين الطلب؟")
        Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - ago)

    def reply(self):
        return self.api.post(reverse("handoff-reply", args=[self.conversation.pk]),
                             {"message": "الطلب في الطريق"}, format="json")

    def test_reply_inside_48_hours_is_sent(self):
        self.customer_wrote(timedelta(hours=30))
        self.tiktok.on("POST", "business/message/send/", ok({"message": {"message_id": "m1"}}))
        self.assertEqual(self.reply().status_code, 200)

    def test_reply_after_48_hours_explains_tiktoks_window(self):
        self.customer_wrote(timedelta(hours=50))
        response = self.reply()
        self.assertEqual(response.status_code, 502)
        self.assertIn("48 ساعة", response.data["error"])
        self.assertEqual(self.tiktok.calls, [])

    def test_reply_while_disconnected_says_to_reconnect(self):
        self.customer_wrote(timedelta(hours=1))
        connect(self.store, tiktok_access_token="", tiktok_refresh_token="", tiktok_access_expires_at=None)
        response = self.reply()
        self.assertEqual(response.status_code, 502)
        self.assertIn("ربط تيك توك", response.data["error"])


# ── Webhook registration command ────────────────────────────────────────────


@override_settings(**TIKTOK)
class RegisterWebhookCommandTests(FakeTikTokMixin, SimpleTestCase):
    def test_registers_the_dm_webhook(self):
        self.tiktok.on("POST", "business/webhook/update/", ok())
        call_command("tiktok_register_webhook", "https://example.test/api/webhooks/tiktok/", stdout=io.StringIO())
        self.assertEqual(self.tiktok.calls[0]["json"], {
            "app_id": "app-1", "secret": SECRET, "event_type": "DIRECT_MESSAGE",
            "callback_url": "https://example.test/api/webhooks/tiktok/"})

    def test_refuses_plain_http(self):
        with self.assertRaises(CommandError):
            call_command("tiktok_register_webhook", "http://example.test/api/webhooks/tiktok/")
        self.assertEqual(self.tiktok.calls, [])
