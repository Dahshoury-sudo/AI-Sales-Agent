"""Dashboard replies outside Meta's 24-hour messaging window.

Meta accepts a Messenger/Instagram message only within 24 hours of the customer's
last message. A person replying from the handoff dashboard can easily miss that, and
the reply used to go out untagged, be rejected, and show the owner a generic
"check your connection" error. The HUMAN_AGENT tag stretches the window to 7 days for
messages a person writes; Meta grants it through App Review + Business Verification.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from products.models import Conversation, Message, Store, StoreSettings
from products.services import meta_service
from products.services.meta_service import human_reply_window, send_platform_message


class SendHelperPayloadTests(TestCase):
    """The tag goes into the Send API body only when asked for."""

    def _body(self, helper, *args, **kwargs):
        response = mock.Mock()
        response.json.return_value = {"message_id": "m1"}
        with mock.patch("products.services.meta_service.requests.post", return_value=response) as post:
            helper(*args, **kwargs)
        return post.call_args.kwargs["json"]

    def test_untagged_bodies_are_unchanged(self):
        for helper in (meta_service.send_messenger_message, meta_service.send_instagram_message):
            with self.subTest(helper=helper.__name__):
                self.assertEqual(
                    self._body(helper, "PAGE", "USER", "hi", "tok"),
                    {"recipient": {"id": "USER"}, "message": {"text": "hi"}},
                )

    def test_tagged_text_bodies_carry_the_human_agent_tag(self):
        for helper in (meta_service.send_messenger_message, meta_service.send_instagram_message):
            with self.subTest(helper=helper.__name__):
                body = self._body(helper, "PAGE", "USER", "hi", "tok", tag="HUMAN_AGENT")
                self.assertEqual(body["messaging_type"], "MESSAGE_TAG")
                self.assertEqual(body["tag"], "HUMAN_AGENT")
                self.assertEqual(body["message"], {"text": "hi"})

    def test_tagged_image_body_carries_the_tag(self):
        body = self._body(meta_service.send_instagram_image, "PAGE", "USER", "https://x/y.jpg", "tok", tag="HUMAN_AGENT")
        self.assertEqual(body["messaging_type"], "MESSAGE_TAG")
        self.assertEqual(body["tag"], "HUMAN_AGENT")

    def test_untagged_image_body_has_no_tag(self):
        body = self._body(meta_service.send_messenger_image, "PAGE", "USER", "https://x/y.jpg", "tok")
        self.assertNotIn("tag", body)
        self.assertNotIn("messaging_type", body)


class HumanReplyWindowTests(TestCase):
    """Which window a dashboard reply falls in depends on the customer's last message."""

    def setUp(self):
        self.store = Store.objects.create(name="Perfamix Test")
        self.conversation = Conversation.objects.create(
            store=self.store, platform="messenger", platform_sender_id="USER9",
        )

    def _customer_said(self, ago, role="user"):
        message = Message.objects.create(conversation=self.conversation, role=role, content="hi")
        Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - ago)

    def test_no_customer_message_counts_as_standard(self):
        self.assertEqual(human_reply_window(self.conversation), "standard")

    def test_boundaries(self):
        for ago, expected in [
            (timedelta(hours=1), "standard"),
            (timedelta(hours=23, minutes=59), "standard"),
            (timedelta(hours=25), "human_agent"),
            (timedelta(days=6, hours=23), "human_agent"),
            (timedelta(days=7, minutes=1), "closed"),
        ]:
            with self.subTest(ago=ago):
                Message.objects.all().delete()
                self._customer_said(ago)
                self.assertEqual(human_reply_window(self.conversation), expected)

    def test_measured_from_the_newest_customer_message(self):
        self._customer_said(timedelta(days=10))
        self._customer_said(timedelta(hours=2))
        self.assertEqual(human_reply_window(self.conversation), "standard")

    def test_bot_and_staff_messages_do_not_reopen_the_window(self):
        """Only the customer's own messages count; our replies never extend it."""
        self._customer_said(timedelta(days=2))
        self._customer_said(timedelta(minutes=5), role="assistant")
        self._customer_said(timedelta(minutes=1), role="agent")
        self.assertEqual(human_reply_window(self.conversation), "human_agent")


class HandoffReplyTagTests(TestCase):
    """End to end through the dashboard reply endpoint."""

    def setUp(self):
        self.owner = User.objects.create_user("owner-ha", "owner-ha@example.com", "pw")
        self.store = Store.objects.create(name="Perfamix Test", owner=self.owner)
        StoreSettings.objects.create(
            store=self.store, facebook_page_id="PAGE123", instagram_account_id="IG456",
            whatsapp_phone_number_id="WA789", messenger_access_token="tok", meta_access_token="tok",
        )
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION="Bearer " + str(RefreshToken.for_user(self.owner).access_token)
        )

    def _conversation(self, platform, customer_ago):
        conversation = Conversation.objects.create(
            store=self.store, platform=platform, platform_sender_id="USER9", needs_human=True,
        )
        if customer_ago is not None:
            message = Message.objects.create(conversation=conversation, role="user", content="فين الطلب؟")
            Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - customer_ago)
        return conversation

    def _reply(self, conversation, result={"message_id": "m1"}):
        helpers = {
            "messenger": mock.patch("products.services.meta_service.send_messenger_message", return_value=result),
            "instagram": mock.patch("products.services.meta_service.send_instagram_message", return_value=result),
            "whatsapp": mock.patch("products.services.meta_service.send_whatsapp_message", return_value=result),
        }
        mocks = {name: patcher.start() for name, patcher in helpers.items()}
        try:
            response = self.client.post(
                f"/api/handoff/conversations/{conversation.id}/reply/",
                {"message": "اهلا يا فندم، طلبك اتشحن"}, format="json",
            )
        finally:
            for patcher in helpers.values():
                patcher.stop()
        return response, mocks

    def _tag_sent(self, helper):
        self.assertTrue(helper.called, "the reply was not sent")
        return helper.call_args.kwargs.get("tag")

    def test_inside_24_hours_the_reply_is_untagged(self):
        response, mocks = self._reply(self._conversation("messenger", timedelta(hours=1)))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._tag_sent(mocks["messenger"]))

    def test_after_24_hours_the_reply_carries_human_agent(self):
        for platform, helper in [("messenger", "messenger"), ("facebook", "messenger"), ("instagram", "instagram")]:
            with self.subTest(platform=platform):
                response, mocks = self._reply(self._conversation(platform, timedelta(hours=25)))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._tag_sent(mocks[helper]), "HUMAN_AGENT")

    def test_after_7_days_meta_is_not_called(self):
        conversation = self._conversation("instagram", timedelta(days=8))
        response, mocks = self._reply(conversation)

        self.assertEqual(response.status_code, 502)
        self.assertFalse(mocks["instagram"].called)
        self.assertEqual(response.json()["status"], "not_delivered")
        self.assertIn("7 أيام", response.json()["error"])
        saved = conversation.messages.get(role="agent")
        self.assertEqual(saved.delivery_status, "failed")

    def test_a_rejected_tagged_reply_explains_human_agent(self):
        response, _ = self._reply(self._conversation("messenger", timedelta(days=2)), result=None)

        self.assertEqual(response.status_code, 502)
        self.assertIn("Human Agent", response.json()["error"])

    def test_a_rejected_reply_inside_24_hours_keeps_the_generic_error(self):
        response, _ = self._reply(self._conversation("messenger", timedelta(hours=1)), result=None)

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("Human Agent", response.json()["error"])
        self.assertNotIn("7 أيام", response.json()["error"])

    def test_whatsapp_is_untouched_even_after_7_days(self):
        response, mocks = self._reply(self._conversation("whatsapp", timedelta(days=8)))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(mocks["whatsapp"].called)
        self.assertNotIn("tag", mocks["whatsapp"].call_args.kwargs)

    def test_no_customer_message_sends_untagged(self):
        _, mocks = self._reply(self._conversation("messenger", None))
        self.assertIsNone(self._tag_sent(mocks["messenger"]))

    def test_bot_replies_never_use_the_tag(self):
        """Meta allows HUMAN_AGENT only for messages a person writes."""
        conversation = self._conversation("messenger", timedelta(hours=25))
        with mock.patch(
            "products.services.meta_service.send_messenger_message", return_value={"message_id": "m1"}
        ) as send:
            self.assertIs(send_platform_message(conversation, "رد البوت"), True)
        self.assertIsNone(send.call_args.kwargs.get("tag"))

    def test_bot_replies_are_not_blocked_after_7_days(self):
        """The 7-day cut-off is for dashboard replies; the bot path is unchanged."""
        conversation = self._conversation("messenger", timedelta(days=8))
        with mock.patch(
            "products.services.meta_service.send_messenger_message", return_value={"message_id": "m1"}
        ) as send:
            send_platform_message(conversation, "رد البوت")
        self.assertTrue(send.called)
