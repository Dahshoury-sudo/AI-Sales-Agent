"""Authentication and polling regressions; all requests stay inside Django."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from products.models import Conversation, Store
from products.throttles import ChatPollingThrottle, DashboardPollingThrottle
from products.views import signer


class RequestLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.clock = patch('rest_framework.throttling.SimpleRateThrottle.timer', return_value=1000.0).start()
        self.addCleanup(patch.stopall)
        self.owner = get_user_model().objects.create_user(username='polling-owner')
        self.store = Store.objects.create(name='Polling store', owner=self.owner)
        self.conversation = Conversation.objects.create(store=self.store, platform='web')
        self.web = APIClient()
        self.web.credentials(HTTP_X_API_KEY=self.store.api_key)
        self.staff = APIClient()
        self.staff.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')

    def poll(self, client=None, conversation=None, **headers):
        return (client or self.web).get('/api/chat/messages/', {
            'conversation_id': signer.sign_object((conversation or self.conversation).pk),
        }, **headers)

    def exhaust(self, throttle_type):
        request = RequestFactory().get('/')
        request.store = self.store
        throttle = throttle_type()
        for _ in range(throttle.num_requests):
            self.assertTrue(throttle.allow_request(request, None))

    def test_fifty_chats_poll_alongside_all_dashboard_readers(self):
        conversations = [self.conversation] + [
            Conversation.objects.create(store=self.store, platform='web') for _ in range(49)
        ]
        dashboard_paths = [
            '/api/history/conversations/', '/api/handoff/conversations/',
            f'/api/handoff/conversations/{self.conversation.pk}/messages/',
            '/api/orders/dashboard/', '/dashboard/api/notifications/',
        ]
        for tick in range(20):
            self.clock.return_value = 1000.0 + tick * 3
            for conversation in conversations:
                self.assertEqual(self.poll(conversation=conversation).status_code, 200)
            if tick % 3 == 0:
                for path in dashboard_paths:
                    self.assertEqual(self.staff.get(path).status_code, 200, path)

    def test_exhausted_web_budget_does_not_block_staff_writes_or_another_store(self):
        self.exhaust(ChatPollingThrottle)
        response = self.poll()
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response['Retry-After']), 0)
        self.assertEqual(self.staff.get('/api/history/conversations/').status_code, 200)
        self.assertEqual(self.staff.post(
            f'/api/handoff/conversations/{self.conversation.pk}/resolve/').status_code, 200)
        other = Store.objects.create(name='Other polling store')
        conversation = Conversation.objects.create(store=other, platform='web')
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=other.api_key)
        self.assertEqual(self.poll(client, conversation).status_code, 200)
        self.clock.return_value += 61
        self.assertEqual(self.poll().status_code, 200)

    def test_dashboard_readers_share_a_budget_separate_from_web_and_writes(self):
        self.exhaust(DashboardPollingThrottle)
        for path in ['/api/history/conversations/', '/api/handoff/conversations/',
                     f'/api/handoff/conversations/{self.conversation.pk}/messages/',
                     '/api/orders/dashboard/', '/dashboard/api/notifications/']:
            response = self.staff.get(path)
            self.assertEqual(response.status_code, 429, path)
            self.assertIn('Retry-After', response)
        self.assertEqual(self.poll().status_code, 200)
        self.assertEqual(self.staff.post(
            f'/api/handoff/conversations/{self.conversation.pk}/resolve/').status_code, 200)

    def test_existing_submission_and_write_limits_remain_enforced(self):
        for _ in range(30):
            # Invalid bodies exercise admission without making any model calls.
            self.assertEqual(self.web.post('/api/chat/', {}, format='json').status_code, 400)
        self.assertEqual(self.web.post('/api/chat/', {}, format='json').status_code, 429)
        self.assertEqual(self.poll().status_code, 200)
        url = f'/api/handoff/conversations/{self.conversation.pk}/resolve/'
        for _ in range(60):
            self.assertEqual(self.staff.post(url).status_code, 200)
        self.assertEqual(self.staff.post(url).status_code, 429)
        self.assertEqual(self.staff.get('/api/history/conversations/').status_code, 200)

    def test_expired_dashboard_token_recovers_with_rotated_refresh_pair(self):
        refresh = RefreshToken.for_user(self.owner)
        expired = refresh.access_token
        expired.set_exp(lifetime=timedelta(seconds=-1))
        self.staff.credentials(HTTP_AUTHORIZATION=f'Bearer {expired}')
        paths = ['/api/history/conversations/',
                 f'/api/handoff/conversations/{self.conversation.pk}/messages/']
        for path in paths:
            self.assertEqual(self.staff.get(path).status_code, 401)
        response = APIClient().post('/dashboard/api/token/refresh/', {'refresh': str(refresh)}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data['refresh'], str(refresh))
        self.staff.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        for path in paths:
            self.assertEqual(self.staff.get(path).status_code, 200)

    @override_settings(CORS_ALLOWED_ORIGINS=['https://shop.example'], CORS_ALLOW_ALL_ORIGINS=False)
    def test_widget_can_read_retry_header_cross_origin(self):
        self.exhaust(ChatPollingThrottle)
        response = self.poll(HTTP_ORIGIN='https://shop.example')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Access-Control-Allow-Origin'], 'https://shop.example')
        self.assertIn('retry-after', response['Access-Control-Expose-Headers'].lower())
