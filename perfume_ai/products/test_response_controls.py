"""Reply switches govern inbound sources without losing the customer's inbox."""
import hashlib
import hmac
import json
from itertools import product
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from products import tasks
from products.models import Conversation, InboundEvent, Message, Notification, PostCommentRule, Store, StoreSettings
from products.services.conversation_service import build_llm_history, generate_event, receive_event
from products.services.response_policy import message_replies_enabled


class ResponseControlTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="reply-owner")
        self.store = Store.objects.create(name="Reply store", owner=self.owner)
        self.settings = StoreSettings.objects.create(
            store=self.store, facebook_page_id="page", instagram_account_id="ig",
            whatsapp_phone_number_id="wa", meta_app_secret="secret",
            messenger_access_token="token", meta_access_token="token",
        )
        self.client = APIClient()
        self.staff = APIClient()
        self.staff.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")
        self.route = self.enterContext(patch("products.tasks.route", return_value=("answer", "")))
        self.send = self.enterContext(patch("products.tasks.send_platform_message", return_value=True))
        self.enterContext(patch("products.tasks.sanitize_reply", side_effect=lambda reply, conv: reply))

    def configure(self, messages, comments=True):
        StoreSettings.objects.filter(pk=self.settings.pk).update(
            message_replies_enabled=messages, comment_replies_enabled=comments)

    def post(self, payload, valid=True):
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        return self.client.post(reverse("meta-webhook"), body, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=signature if valid else "sha256=invalid")

    def message_payload(self, platform, source, image=False):
        if platform == "whatsapp":
            message = {"id": source, "from": source, "type": "image" if image else "text"}
            message.update({"image": {"id": "media-id", "caption": "photo"}} if image
                           else {"text": {"body": "hello"}})
            return {"object": "whatsapp_business_account", "entry": [{"changes": [{
                "field": "messages", "value": {"metadata": {"phone_number_id": "wa"},
                                                "messages": [message]}}]}]}
        message = {"mid": source}
        message.update({"attachments": [{"type": "image", "payload": {"url": "https://example.test/photo.png"}}]}
                       if image else {"text": "hello"})
        return {"object": "instagram" if platform == "instagram" else "page", "entry": [{
            "messaging": [{"sender": {"id": source},
                           "recipient": {"id": "ig" if platform == "instagram" else "page"},
                           "message": message}]}]}

    def comment_payload(self, platform, source="comment"):
        if platform == "facebook":
            return {"object": "page", "entry": [{"id": "page", "changes": [{"field": "feed", "value": {
                "item": "comment", "verb": "add", "comment_id": source, "from": {"id": source},
                "message": "hello", "post_id": "post"}}]}]}
        return {"object": "instagram", "entry": [{"id": "ig", "changes": [{"field": "comments", "value": {
            "id": source, "from": {"id": source}, "text": "hello", "media": {"id": "post"}}}]}]}

    def test_message_and_image_matrix_retains_history_and_controls_sends(self):
        for enabled, comments, platform, image in product((True, False), (True, False),
                                                         ("messenger", "instagram", "whatsapp"), (False, True)):
            with self.subTest(enabled=enabled, comments=comments, platform=platform, image=image):
                self.configure(enabled, comments)
                source = f"{enabled}-{comments}-{platform}-{image}"
                task = tasks.process_incoming_attachment if image else tasks.process_incoming_message
                self.send.reset_mock()
                self.route.reset_mock()
                with patch.object(task, "delay") as queued:
                    response = self.post(self.message_payload(platform, source, image))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(queued.call_count, int(enabled))
                if queued.called:
                    task(*queued.call_args.args, **queued.call_args.kwargs)
                event = InboundEvent.objects.get(sender_id=source)
                self.assertEqual(event.status, "complete")
                self.assertEqual(Message.objects.filter(conversation=event.conversation, role="user").count(), 1)
                self.assertEqual(bool(event.user_message.attachment_url), image)
                self.assertEqual(self.send.call_count, int(enabled))
                self.assertEqual(self.route.call_count, int(enabled and not image))
                self.assertEqual(event.reply_message_id is not None, enabled)
                if not enabled:
                    self.assertEqual(event.result["reason"], "message_replies_disabled")

    def test_comment_matrix_keeps_private_answer_when_messages_are_off(self):
        for messages, enabled, platform in product((True, False), (True, False), ("facebook", "instagram")):
            with self.subTest(messages=messages, enabled=enabled, platform=platform):
                self.configure(messages, enabled)
                self.route.reset_mock()
                with patch.object(tasks.process_comment_task, "apply_async") as queued, \
                     patch("products.tasks.fetch_post_content", return_value="caption") as fetch, \
                     patch("products.tasks.reply_to_comment") as fb, \
                     patch("products.tasks.reply_to_ig_comment") as ig, \
                     patch("products.tasks.send_private_reply") as private:
                    response = self.post(self.comment_payload(platform, f"{messages}-{enabled}-{platform}"))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(queued.call_count, int(enabled))
                    if queued.called:
                        tasks.process_comment_task(*queued.call_args.kwargs["args"])
                    self.assertEqual(fetch.call_count, int(enabled))
                    self.assertEqual(self.route.call_count, int(enabled))
                    self.assertEqual(private.call_count, int(enabled))
                    self.assertEqual(fb.call_count, int(enabled and platform == "facebook"))
                    self.assertEqual(ig.call_count, int(enabled and platform == "instagram"))

    def test_disabled_events_still_require_valid_signatures(self):
        self.configure(False, False)
        payloads = [self.message_payload(p, p, image) for p in ("messenger", "instagram", "whatsapp")
                    for image in (False, True)]
        payloads += [self.comment_payload(p) for p in ("facebook", "instagram")]
        for payload in payloads:
            self.assertEqual(self.post(payload, valid=False).status_code, 403)
        self.assertFalse(InboundEvent.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_duplicates_while_off_never_replay_after_reenabling(self):
        self.configure(False)
        payload = self.message_payload("messenger", "duplicate")
        for _ in range(2):
            self.assertEqual(self.post(payload).status_code, 200)
        self.configure(True)
        with patch.object(tasks.process_incoming_message, "delay") as queued:
            self.post(payload)
        tasks.process_incoming_message(*queued.call_args.args, **queued.call_args.kwargs)
        self.assertEqual(InboundEvent.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        self.route.assert_not_called()
        self.send.assert_not_called()
        tasks.process_incoming_message(self.store.pk, "messenger", "duplicate", "new", source_id="new")
        self.route.assert_called_once()
        self.send.assert_called_once()

    def test_queued_text_and_image_recheck_without_rate_limit_charges(self):
        for image in (False, True):
            self.configure(True)
            task = tasks.process_incoming_attachment if image else tasks.process_incoming_message
            with patch.object(task, "delay") as queued:
                self.post(self.message_payload("messenger", f"queued-{image}", image))
            self.configure(False)
            with patch("products.tasks.rate_limit.hit_all") as hit, patch("products.tasks.rate_limit.peek") as peek:
                task(*queued.call_args.args, **queued.call_args.kwargs)
            hit.assert_not_called()
            peek.assert_not_called()
        self.assertEqual(Message.objects.filter(role="user").count(), 2)
        self.assertFalse(InboundEvent.objects.exclude(status="complete").exists())
        self.route.assert_not_called()
        self.send.assert_not_called()

    def test_deferred_message_and_legacy_dispatch_without_source_are_saved_silently(self):
        self.configure(False)
        with patch.object(tasks.process_incoming_message, "apply_async") as queued, \
             patch("products.tasks.rate_limit.peek") as peek:
            tasks.process_incoming_message(self.store.pk, "whatsapp", "customer", "deferred", deferrals=1,
                                           source_id="deferred")
            tasks.process_message_async(self.store.pk, "messenger", "customer", "no source")
            tasks.process_attachment_async(self.store.pk, "messenger", "customer", "https://example.test/image.png")
        queued.assert_not_called()
        peek.assert_not_called()
        self.assertEqual(Message.objects.filter(role="user").count(), 3)
        self.send.assert_not_called()

    def test_queued_comment_checks_switch_and_post_block_before_fetching(self):
        for platform in ("facebook", "instagram"):
            for muted_post in (False, True):
                self.configure(True, muted_post)
                if muted_post:
                    PostCommentRule.objects.create(store=self.store, platform=platform, post_id="post")
                with patch("products.tasks.fetch_post_content") as fetch, \
                     patch("products.tasks.send_private_reply") as private:
                    tasks.process_comment_task(self.store.pk, platform, "comment", "customer", "hello", "post")
                fetch.assert_not_called()
                private.assert_not_called()
        self.route.assert_not_called()

    def test_switch_is_checked_for_each_event_drained_from_queue(self):
        first = receive_event(self.store, "messenger", "customer", "first", "first")
        last = receive_event(self.store, "messenger", "customer", "last", "last")

        def turn_off(*args):
            self.configure(False)
            return "already started", ""

        self.route.side_effect = turn_off
        tasks.process_incoming_message(self.store.pk, "messenger", "customer", "last", event_id=last.pk)
        self.route.assert_called_once()
        self.send.assert_called_once()
        first.refresh_from_db()
        last.refresh_from_db()
        self.assertEqual(first.status, "complete")
        self.assertEqual(last.result["reason"], "message_replies_disabled")

    def test_undelivered_draft_is_suppressed_and_hidden_without_delivery_failure(self):
        event = receive_event(self.store, "messenger", "customer", "hello", "ready")
        generate_event(event, self.route, lambda reply, conv: reply)
        reply_id = event.reply_message_id
        self.route.reset_mock()
        self.configure(False)
        tasks.process_incoming_message(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(event.status, "complete")
        self.assertEqual(Message.objects.get(pk=reply_id).delivery_status, "suppressed")
        self.assertEqual(build_llm_history(event.conversation), [{"role": "user", "content": "hello"}])
        self.assertFalse(Notification.objects.exists())
        event.conversation.needs_human = True
        event.conversation.save(update_fields=["needs_human"])
        history = self.staff.get(reverse("chat-history-conversations")).data["conversations"]
        self.assertEqual(history[0]["last_message"], "hello")
        handoff = self.staff.get(reverse("handoff-conversations")).data["conversations"]
        self.assertEqual(handoff[0]["last_message"], "hello")
        messages = self.staff.get(reverse("handoff-messages", args=[event.conversation_id])).data["messages"]
        self.assertEqual([m["content"] for m in messages], ["hello"])
        self.configure(True)
        tasks.process_incoming_message(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
        self.route.assert_not_called()
        self.send.assert_not_called()

    def test_uncertain_delivery_evidence_is_retained_without_sending_more(self):
        event = receive_event(self.store, "messenger", "customer", "hello", "partial")
        generate_event(event, self.route, lambda reply, conv: reply)
        Message.objects.filter(pk=event.reply_message_id).update(delivery_parts={"image": {"status": "sent"}})
        self.configure(False)
        tasks.process_incoming_message(self.store.pk, "messenger", "customer", "hello", event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(event.reply_message.delivery_status, "uncertain")
        self.assertEqual(event.reply_message.delivery_parts["image"]["status"], "sent")
        self.assertEqual(event.status, "complete")
        self.send.assert_not_called()

    def test_existing_handoff_notifies_once_without_assigning_normal_conversations(self):
        conv = Conversation.objects.create(store=self.store, platform="messenger", platform_sender_id="staff",
                                           needs_human=True)
        self.configure(False)
        for _ in range(2):
            tasks.process_attachment_async(self.store.pk, "messenger", "staff", "https://example.test/receipt.png",
                                           source_id="receipt")
        tasks.process_message_async(self.store.pk, "messenger", "normal", "hello", source_id="normal")
        self.assertEqual(Notification.objects.filter(type="handoff_message").count(), 1)
        self.assertTrue(conv.messages.get().handoff_unread)
        self.assertFalse(Conversation.objects.get(platform_sender_id="normal").needs_human)
        self.send.assert_not_called()

    def test_web_and_manual_replies_still_work_when_both_switches_are_off(self):
        self.configure(False, False)
        event = receive_event(self.store, "web", "visitor", "hello", "web")
        generate_event(event, self.route, lambda reply, conv: reply)
        self.assertEqual(event.result["reply"], "answer")
        conv = Conversation.objects.create(store=self.store, platform="messenger", platform_sender_id="customer")
        with patch("products.services.meta_service.send_platform_message", return_value=True) as manual_send:
            response = self.staff.post(reverse("handoff-reply", args=[conv.pk]), {"message": "staff reply"}, format="json")
        self.assertEqual(response.status_code, 200)
        manual_send.assert_called_once()

    def test_missing_settings_retain_legacy_message_behavior(self):
        self.settings.delete()
        self.assertTrue(message_replies_enabled(self.store.pk, "messenger"))
        tasks.process_incoming_message(self.store.pk, "messenger", "customer", "hello", source_id="legacy")
        self.route.assert_called_once()
        self.send.assert_called_once()
