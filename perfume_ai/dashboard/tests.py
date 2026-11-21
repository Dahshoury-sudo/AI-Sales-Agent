from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from django.core.cache import cache
from unittest.mock import patch

from products.models import Store, StoreSettings
from dashboard.views_store import REPLY_SWITCHES


class PromotionSettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="promotions-owner")
        self.store = Store.objects.create(name="Offers store", owner=self.owner)
        self.settings = StoreSettings.objects.create(
            store=self.store, business_facts="عنوان الستور: القاهرة.",
        )
        self.other = StoreSettings.objects.create(store=Store.objects.create(name="Other store"))
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.owner)}")
        self.url = reverse("dashboard-api-settings")

    def test_existing_and_new_settings_start_with_empty_promotions(self):
        self.assertEqual(self.client.get(self.url).data.get("promotions"), "")
        self.settings.delete()
        self.assertEqual(self.client.get(self.url).data.get("promotions"), "")

    def test_round_trip_multiline_promotions_is_store_scoped(self):
        for offers in ("بوكس الصيف: 3 عطور بـ3000 جنيه.\n\nهدية 50 مل مع العرض.",
                       "عرض الشتاء: عطرين بـ900 جنيه.", ""):
            with self.subTest(offers=offers):
                response = self.client.put(self.url, {
                    "promotions": offers, "store_id": self.other.store_id,
                }, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.client.get(self.url).data.get("promotions"), offers)
                self.settings.refresh_from_db()
                self.other.refresh_from_db()
                self.assertEqual(self.settings.business_facts, "عنوان الستور: القاهرة.")
                self.assertEqual(getattr(self.other, "promotions", None), "")

    def test_omitting_promotions_preserves_saved_offers(self):
        self.client.put(self.url, {"promotions": "عرض الصيف بـ900 جنيه."}, format="json")
        self.client.put(self.url, {"business_facts": "العنوان الجديد"}, format="json")
        self.assertEqual(self.client.get(self.url).data.get("promotions"), "عرض الصيف بـ900 جنيه.")

    def test_old_offer_text_is_not_migrated_on_read(self):
        self.settings.business_facts = "عرض قديم بـ100 جنيه."
        self.settings.system_prompt = "باندل قديم بـ200 جنيه."
        self.settings.save()
        data = self.client.get(self.url).data
        self.assertEqual(data.get("promotions"), "")
        self.assertEqual(data["business_facts"], "عرض قديم بـ100 جنيه.")
        self.assertEqual(data["system_prompt"], "باندل قديم بـ200 جنيه.")

    def test_promotion_settings_require_owner_authentication(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.put(self.url, {"promotions": "offer"}, format="json").status_code, 401)


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

    def switches(self, settings):
        settings.refresh_from_db()
        return {field: getattr(settings, field) for field in REPLY_SWITCHES}

    def test_defaults_and_each_switch_alone_are_store_scoped(self):
        data = self.client.get(self.url).data
        self.assertEqual({f: data[f] for f in REPLY_SWITCHES}, dict.fromkeys(REPLY_SWITCHES, True))
        for off in REPLY_SWITCHES:
            with self.subTest(off=off):
                expected = {f: f != off for f in REPLY_SWITCHES}
                payload = {**expected, "store_id": self.other.store_id}
                self.assertEqual(self.client.put(self.url, payload, format="json").status_code, 200)
                data = self.client.get(self.url).data
                for field in REPLY_SWITCHES:
                    self.assertIs(data[field], expected[field])
                self.assertEqual(self.switches(self.other), dict.fromkeys(REPLY_SWITCHES, True))

    def test_invalid_values_reject_entire_update(self):
        for field in REPLY_SWITCHES:
            for value in ("true", "false", 0, 1, None, [], {}):
                response = self.client.put(self.url, {field: value, "business_facts": "must not save"}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.switches(self.settings), dict.fromkeys(REPLY_SWITCHES, True))
                self.assertEqual(self.settings.business_facts, "")

    def test_old_grouped_keys_are_ignored(self):
        payload = {"message_replies_enabled": False, "comment_replies_enabled": False}
        self.assertEqual(self.client.put(self.url, payload, format="json").status_code, 200)
        self.assertEqual(self.switches(self.settings), dict.fromkeys(REPLY_SWITCHES, True))
        self.assertNotIn("message_replies_enabled", self.client.get(self.url).data)

    def test_partial_updates_preserve_switches_credentials_and_other_fields(self):
        self.client.put(self.url, {"whatsapp_replies_enabled": False}, format="json")
        self.client.put(self.url, {"business_facts": "facts", "messenger_access_token": "••••••••"}, format="json")
        self.assertEqual(self.switches(self.settings),
                         {f: f != "whatsapp_replies_enabled" for f in REPLY_SWITCHES})
        self.assertEqual(self.settings.messenger_access_token, "secret")
        self.assertEqual(self.settings.business_facts, "facts")

    def test_concurrent_save_does_not_restore_an_omitted_switch(self):
        original_save = StoreSettings.save

        def save_after_other_request(instance, *args, **kwargs):
            StoreSettings.objects.filter(pk=instance.pk).update(instagram_comment_replies_enabled=False)
            return original_save(instance, *args, **kwargs)

        with patch.object(StoreSettings, "save", autospec=True, side_effect=save_after_other_request):
            response = self.client.put(self.url, {"messenger_replies_enabled": False}, format="json")
        self.assertEqual(response.status_code, 200)
        saved = self.switches(self.settings)
        self.assertFalse(saved["messenger_replies_enabled"])
        self.assertFalse(saved["instagram_comment_replies_enabled"])

    def test_read_and_write_require_owner_authentication(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.put(self.url, {"whatsapp_replies_enabled": False}, format="json").status_code, 401)

    def test_settings_row_can_be_created_with_one_switch_disabled(self):
        self.settings.delete()
        response = self.client.put(self.url, {"facebook_comment_replies_enabled": False}, format="json")
        self.assertEqual(response.status_code, 200)
        saved = StoreSettings.objects.get(store=self.store)
        self.assertEqual(self.switches(saved), {f: f != "facebook_comment_replies_enabled" for f in REPLY_SWITCHES})


class ReplySwitchMigrationTests(TransactionTestCase):
    def test_grouped_switches_split_per_platform_and_merge_back_without_turning_replies_on(self):
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        restore_target = executor.loader.graph.leaf_nodes()
        old_target = [("products", "0046_storesettings_promotions")]
        new_target = [("products", "0047_storesettings_per_platform_reply_switches")]
        combos = [(m, c) for m in (True, False) for c in (True, False)]
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            OldStore = apps.get_model("products", "Store")
            OldSettings = apps.get_model("products", "StoreSettings")
            rows = {combo: OldSettings.objects.create(
                        store=OldStore.objects.create(name=f"Legacy {combo}"),
                        message_replies_enabled=combo[0], comment_replies_enabled=combo[1]).pk
                    for combo in combos}

            MigrationExecutor(connection).migrate(new_target)
            for (messages, comments), pk in rows.items():
                saved = StoreSettings.objects.get(pk=pk)
                self.assertEqual({f: getattr(saved, f) for f in REPLY_SWITCHES}, {
                    "whatsapp_replies_enabled": messages, "messenger_replies_enabled": messages,
                    "instagram_replies_enabled": messages,
                    "facebook_comment_replies_enabled": comments, "instagram_comment_replies_enabled": comments,
                })
            # One platform off is enough to keep its whole old group off after a rollback.
            StoreSettings.objects.filter(pk=rows[(True, True)]).update(
                instagram_replies_enabled=False, facebook_comment_replies_enabled=False)

            executor = MigrationExecutor(connection)
            executor.migrate(old_target)
            OldSettings = executor.loader.project_state(old_target).apps.get_model("products", "StoreSettings")
            back = {combo: tuple(OldSettings.objects.filter(pk=pk).values_list(
                        "message_replies_enabled", "comment_replies_enabled").get())
                    for combo, pk in rows.items()}
            self.assertEqual(back, {(True, True): (False, False), (True, False): (True, False),
                                    (False, True): (False, True), (False, False): (False, False)})
        finally:
            # Later TransactionTestCases need the current schema, not just migration 0046.
            MigrationExecutor(connection).migrate(restore_target)
