from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from django.core.cache import cache
from unittest.mock import patch

from products.models import Store, StoreSettings


class WebsiteOrderingSettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="website-owner")
        self.store = Store.objects.create(name="Online store", owner=self.owner)
        self.settings = StoreSettings.objects.create(store=self.store)
        other = Store.objects.create(name="Offline store")
        self.other_settings = StoreSettings.objects.create(store=other)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")
        self.url = reverse("dashboard-api-settings")

    def test_defaults_off_and_can_be_enabled_and_disabled_for_current_store(self):
        self.assertIs(self.client.get(self.url).data["website_ordering_enabled"], False)
        for enabled in (True, False):
            response = self.client.put(self.url, {"website_ordering_enabled": enabled}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertIs(self.client.get(self.url).data["website_ordering_enabled"], enabled)
            self.other_settings.refresh_from_db()
            self.assertFalse(self.other_settings.website_ordering_enabled)

    def test_omitting_field_preserves_existing_setting(self):
        self.settings.website_ordering_enabled = True
        self.settings.save(update_fields=["website_ordering_enabled"])
        response = self.client.put(self.url, {"business_facts": "Updated facts"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.website_ordering_enabled)

    def test_invalid_values_cannot_enable_website_ordering(self):
        for value in ("false", "true", 1, None):
            response = self.client.put(self.url, {"website_ordering_enabled": value,
                                                 "business_facts": "Must not save"}, format="json")
            self.assertEqual(response.status_code, 400)
            self.settings.refresh_from_db()
            self.assertFalse(self.settings.website_ordering_enabled)
            self.assertEqual(self.settings.business_facts, "")


class ReplyControlSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="reply-settings-owner")
        self.store = Store.objects.create(name="Reply settings", owner=self.owner)
        self.settings = StoreSettings.objects.create(store=self.store, messenger_access_token="secret")
        self.other = StoreSettings.objects.create(store=Store.objects.create(name="Other"))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")
        self.url = reverse("dashboard-api-settings")

    def test_defaults_and_all_combinations_are_store_scoped(self):
        data = self.client.get(self.url).data
        self.assertIs(data["message_replies_enabled"], True)
        self.assertIs(data["comment_replies_enabled"], True)
        for messages, comments in ((False, True), (True, False), (False, False), (True, True)):
            payload = {"message_replies_enabled": messages, "comment_replies_enabled": comments,
                       "store_id": self.other.store_id}
            self.assertEqual(self.client.put(self.url, payload, format="json").status_code, 200)
            data = self.client.get(self.url).data
            self.assertIs(data["message_replies_enabled"], messages)
            self.assertIs(data["comment_replies_enabled"], comments)
            self.other.refresh_from_db()
            self.assertTrue(self.other.message_replies_enabled)
            self.assertTrue(self.other.comment_replies_enabled)

    def test_invalid_values_reject_entire_update(self):
        for field in ("message_replies_enabled", "comment_replies_enabled"):
            for value in ("true", "false", 0, 1, None, [], {}):
                response = self.client.put(self.url, {field: value, "business_facts": "must not save"}, format="json")
                self.assertEqual(response.status_code, 400)
                self.settings.refresh_from_db()
                self.assertTrue(self.settings.message_replies_enabled)
                self.assertTrue(self.settings.comment_replies_enabled)
                self.assertEqual(self.settings.business_facts, "")

    def test_partial_updates_preserve_switches_credentials_and_other_fields(self):
        self.client.put(self.url, {"message_replies_enabled": False}, format="json")
        self.client.put(self.url, {"business_facts": "facts", "messenger_access_token": "••••••••"}, format="json")
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.message_replies_enabled)
        self.assertTrue(self.settings.comment_replies_enabled)
        self.assertEqual(self.settings.messenger_access_token, "secret")
        self.assertEqual(self.settings.business_facts, "facts")

    def test_concurrent_save_does_not_restore_an_omitted_switch(self):
        original_save = StoreSettings.save

        def save_after_other_request(instance, *args, **kwargs):
            StoreSettings.objects.filter(pk=instance.pk).update(comment_replies_enabled=False)
            return original_save(instance, *args, **kwargs)

        with patch.object(StoreSettings, "save", autospec=True, side_effect=save_after_other_request):
            response = self.client.put(self.url, {"message_replies_enabled": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.message_replies_enabled)
        self.assertFalse(self.settings.comment_replies_enabled)

    def test_read_and_write_require_owner_authentication(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.put(self.url, {"message_replies_enabled": False}, format="json").status_code, 401)

    def test_settings_row_can_be_created_with_one_switch_disabled(self):
        self.settings.delete()
        response = self.client.put(self.url, {"comment_replies_enabled": False}, format="json")
        self.assertEqual(response.status_code, 200)
        saved = StoreSettings.objects.get(store=self.store)
        self.assertTrue(saved.message_replies_enabled)
        self.assertFalse(saved.comment_replies_enabled)
