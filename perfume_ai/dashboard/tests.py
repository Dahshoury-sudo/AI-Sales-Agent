from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

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
