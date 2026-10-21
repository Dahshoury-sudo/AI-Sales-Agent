"""Customer follow-ups remain visible until staff actually read them."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from products.models import Conversation, InboundEvent, Message, Notification, Store
from products.services.conversation_service import (
    generate_event, get_or_create_platform_conversation, receive_event, save_message,
)


class HandoffAttentionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user('attention-owner', password='test')
        self.store = Store.objects.create(name='Attention', owner=self.owner)
        self.conv = Conversation.objects.create(store=self.store, needs_human=True,
                                                platform='messenger', platform_sender_id='customer')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(RefreshToken.for_user(self.owner).access_token))

    def incoming(self, text='new message', source='one', attachment='', conversation=None):
        conv = conversation or self.conv
        event = receive_event(self.store, conv.platform, conv.platform_sender_id, text,
                              source, conversation=conv, attachment_url=attachment)
        route = Mock(side_effect=AssertionError('Bot must stay silent'))
        generate_event(event, route, Mock(side_effect=AssertionError('No bot reply')))
        event.refresh_from_db()
        return event

    def read(self, message_id, conversation=None):
        return self.client.post(f'/api/handoff/conversations/{(conversation or self.conv).pk}/read/',
                                {'through_message_id': message_id}, format='json')

    def test_followups_and_attachments_notify_once_without_bot_reply(self):
        for platform in ('web', 'whatsapp', 'messenger', 'instagram', 'facebook'):
            with self.subTest(platform=platform):
                self.conv.platform = platform
                self.conv.save(update_fields=['platform'])
                for attachment in ('', 'https://example.test/receipt.png'):
                    source = f'{platform}:{attachment}'
                    event = self.incoming(text='' if attachment else '<b>hello</b>', source=source, attachment=attachment)
                    retry = self.incoming(text=event.text, source=source, attachment=attachment)
                    self.assertEqual(event.pk, retry.pk)
                    self.assertEqual(event.result['reply'], '')
                    self.assertTrue(event.user_message.handoff_unread)
                    self.assertIsNone(event.reply_message_id)
                    notification = Notification.objects.get(source_message=event.user_message)
                    self.assertEqual(notification.type, 'handoff_message')
                    self.assertFalse(notification.is_read)
        self.assertEqual(Message.objects.count(), 10)
        self.assertEqual(Notification.objects.count(), 10)

    def test_notification_failure_rolls_back_message_and_event(self):
        with patch('products.services.notification_service.notify_handoff_message', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.incoming()
        self.assertFalse(Message.objects.exists())
        self.assertEqual(InboundEvent.objects.get().status, 'pending')
        self.incoming()
        self.assertEqual(Notification.objects.count(), 1)

    def test_web_and_attachment_worker_retries_preserve_followup_alerts(self):
        from products.views import signer
        from products.tasks import process_incoming_attachment
        web = Conversation.objects.create(store=self.store, platform='web', needs_human=True)
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=self.store.api_key)
        payload = {'conversation_id': signer.sign_object(web.pk), 'client_message_id': 'web-followup', 'message': 'hello'}
        with patch('products.views.route', side_effect=AssertionError('Bot must stay silent')):
            first = client.post('/api/chat/', payload)
            retry = client.post('/api/chat/', payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data, retry.data)
        with patch('products.tasks.send_platform_message', side_effect=AssertionError('No outbound reply')):
            for _ in range(2):
                process_incoming_attachment.run(self.store.pk, 'messenger', 'customer',
                    'https://example.test/receipt.png', '', source_id='attachment-followup')
        self.assertEqual(Message.objects.filter(handoff_unread=True).count(), 2)
        self.assertEqual(Notification.objects.filter(type='handoff_message').count(), 2)

    def test_initial_handoff_sets_unread_without_second_notification(self):
        self.conv.needs_human = False
        self.conv.save()
        def transfer(text, history, store, conversation):
            from products.services.notification_service import notify_handoff
            conversation.needs_human = True
            conversation.save(update_fields=['needs_human'])
            notify_handoff(conversation)
            return 'transferred', ''
        event = receive_event(self.store, 'messenger', 'customer', 'human please', 'initial', self.conv)
        generate_event(event, transfer, lambda text, _: text)
        self.assertTrue(Message.objects.get(role='user').handoff_unread)
        self.assertEqual(list(Notification.objects.values_list('type', flat=True)), ['handoff'])

    def test_normal_bot_and_staff_messages_are_not_unread(self):
        self.conv.needs_human = False
        self.conv.save()
        event = receive_event(self.store, 'web', '', 'hello', 'bot', self.conv)
        generate_event(event, lambda *args: ('hello', ''), lambda text, _: text)
        save_message(self.conv, 'agent', 'staff response')
        self.assertFalse(Message.objects.filter(handoff_unread=True).exists())
        self.assertFalse(Notification.objects.exists())

    def test_old_chat_rises_and_list_uses_one_query_for_summaries(self):
        old_time = timezone.now() - timedelta(days=5)
        Conversation.objects.filter(pk=self.conv.pk).update(created_at=old_time)
        old = save_message(self.conv, 'agent', 'previous reply')
        Message.objects.filter(pk=old.pk).update(created_at=old_time)
        newer = Conversation.objects.create(store=self.store, needs_human=True)
        save_message(newer, 'user', 'newer conversation')
        empty = Conversation.objects.create(store=self.store, needs_human=True)
        event = self.incoming()
        from products.views import HandoffConversationsAPIView
        from rest_framework.test import APIRequestFactory, force_authenticate
        request = APIRequestFactory().get('/api/handoff/conversations/')
        request.store = self.store
        force_authenticate(request, user=self.owner)
        with self.assertNumQueries(1):
            response = HandoffConversationsAPIView.as_view()(request)
        rows = response.data['conversations']
        self.assertEqual(rows[0]['id'], self.conv.pk)
        self.assertEqual(rows[0]['last_message'], event.text)
        self.assertEqual(rows[0]['unread_count'], 1)
        self.assertEqual(rows[0]['last_message_at'], event.user_message.created_at.isoformat())
        self.assertEqual(next(r for r in rows if r['id'] == empty.pk)['last_message_at'], empty.created_at.isoformat())

    def test_equal_timestamps_have_deterministic_message_order(self):
        other = Conversation.objects.create(store=self.store, needs_human=True)
        a = save_message(other, 'user', 'first')
        b = save_message(self.conv, 'user', 'second')
        Message.objects.filter(pk__in=[a.pk, b.pk]).update(created_at=timezone.now())
        rows = self.client.get('/api/handoff/conversations/').data['conversations']
        self.assertEqual([r['id'] for r in rows], [self.conv.pk, other.pk])

    def test_read_is_bounded_idempotent_and_get_does_not_acknowledge(self):
        first = self.incoming()
        second = self.incoming(source='two')
        history = self.client.get(f'/api/handoff/conversations/{self.conv.pk}/messages/')
        self.assertEqual([m['id'] for m in history.data['messages']], [first.user_message_id, second.user_message_id])
        self.assertEqual(Message.objects.filter(handoff_unread=True).count(), 2)
        for _ in range(2):
            response = self.read(first.user_message_id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['unread_count'], 1)
        self.assertTrue(Notification.objects.get(source_message_id=first.user_message_id).is_read)
        self.assertFalse(Notification.objects.get(source_message_id=second.user_message_id).is_read)
        self.assertEqual(self.read(second.user_message_id).data['unread_count'], 0)
        self.assertEqual(self.read(first.user_message_id).data['unread_count'], 0)

    def test_invalid_or_cross_store_reads_cannot_clear_messages(self):
        event = self.incoming()
        other = Conversation.objects.create(store=Store.objects.create(name='Other'), needs_human=True)
        foreign = save_message(other, 'user', 'private')
        for value in (None, 0, -1, True, '1', 1.5, 2**100, foreign.pk):
            with self.subTest(value=value):
                self.assertEqual(self.read(value).status_code, 400)
        self.assertEqual(self.read(foreign.pk, other).status_code, 404)
        self.assertEqual(self.client.get(f'/api/handoff/conversations/{other.pk}/messages/').status_code, 404)
        self.assertTrue(Message.objects.get(pk=event.user_message_id).handoff_unread)

        self.client.credentials()
        self.assertEqual(self.read(event.user_message_id).status_code, 401)

    def test_notifications_include_source_and_reading_them_does_not_clear_chat(self):
        event = self.incoming()
        data = self.client.get('/dashboard/api/notifications/').data
        self.assertEqual(data['notifications'][0]['conversation_id'], self.conv.pk)
        self.assertEqual(data['notifications'][0]['source_message_id'], event.user_message_id)
        self.client.post('/dashboard/api/notifications/mark-read/')
        self.assertTrue(Message.objects.get(pk=event.user_message_id).handoff_unread)

    def test_unresolved_handoff_survives_idle_time_and_newer_bot_chat(self):
        old = save_message(self.conv, 'agent', 'old reply')
        Message.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=7))
        Conversation.objects.create(store=self.store, platform='messenger', platform_sender_id='customer')
        selected, created = get_or_create_platform_conversation(self.store, 'messenger', 'customer')
        self.assertFalse(created)
        self.assertEqual(selected.pk, self.conv.pk)

    def test_resolve_clears_unread_and_restores_rollover(self):
        event = self.incoming()
        result = self.client.post(f'/api/handoff/conversations/{self.conv.pk}/resolve/')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(Message.objects.get(pk=event.user_message_id).handoff_unread)
        self.assertTrue(Notification.objects.get().is_read)
        Message.objects.update(created_at=timezone.now() - timedelta(days=2))
        selected, created = get_or_create_platform_conversation(self.store, 'messenger', 'customer')
        self.assertTrue(created)
        self.assertNotEqual(selected.pk, self.conv.pk)

    def test_resolution_failure_rolls_back_flag_and_acknowledgements(self):
        event = self.incoming()
        with patch('products.services.conversation_service.acknowledge_handoff', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.client.post(f'/api/handoff/conversations/{self.conv.pk}/resolve/')
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_human)
        self.assertTrue(Message.objects.get(pk=event.user_message_id).handoff_unread)


@skipUnless(connection.vendor == 'postgresql', 'Cross-worker serialization requires PostgreSQL')
class ConcurrentHandoffAttentionTests(TransactionTestCase):
    setUp = HandoffAttentionTests.setUp
    incoming = HandoffAttentionTests.incoming

    def test_resolve_waits_for_inflight_message_then_clears_its_alert(self):
        from products.services.conversation_service import conversation_lock
        from products.services.notification_service import notify_handoff_message
        started, release, resolving = Event(), Event(), Event()
        token = str(RefreshToken.for_user(self.owner).access_token)

        def delayed_notification(message):
            started.set()
            if not release.wait(timeout=10):
                raise AssertionError('Message transaction was not released')
            notify_handoff_message(message)

        def receive():
            close_old_connections()
            try:
                with conversation_lock(f'conversation:{self.conv.pk}'):
                    self.incoming()
            finally:
                close_old_connections()

        def resolve():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                resolving.set()
                return client.post(f'/api/handoff/conversations/{self.conv.pk}/resolve/').status_code
            finally:
                close_old_connections()

        with patch('products.services.notification_service.notify_handoff_message', side_effect=delayed_notification):
            with ThreadPoolExecutor(max_workers=2) as pool:
                inbound = pool.submit(receive)
                try:
                    self.assertTrue(started.wait(timeout=10))
                    resolution = pool.submit(resolve)
                    self.assertTrue(resolving.wait(timeout=10))
                    with self.assertRaises(TimeoutError):
                        resolution.result(timeout=0.2)
                finally:
                    release.set()
                inbound.result(timeout=10)
                self.assertEqual(resolution.result(timeout=10), 200)
        self.conv.refresh_from_db()
        self.assertFalse(self.conv.needs_human)
        self.assertFalse(Message.objects.filter(handoff_unread=True).exists())
        self.assertTrue(Notification.objects.get().is_read)
