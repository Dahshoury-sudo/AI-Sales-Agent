"""Point the WebVitas TikTok app's DM webhook at this server.

TikTok keeps one callback URL per app, for every connected store, so this runs once
per environment (see docs/tiktok-setup.md), not once per store:

    python manage.py tiktok_register_webhook https://your-domain/api/webhooks/tiktok/
"""

import requests
from django.core.management.base import BaseCommand, CommandError

from products.services import tiktok_service


class Command(BaseCommand):
    help = "Register the TikTok DIRECT_MESSAGE webhook callback URL for the WebVitas TikTok app."

    def add_arguments(self, parser):
        parser.add_argument("callback_url", help="Public https URL of /api/webhooks/tiktok/")

    def handle(self, *args, callback_url, **options):
        if not callback_url.startswith("https://"):
            raise CommandError("TikTok only calls https URLs.")
        if not (tiktok_service.settings.TIKTOK_APP_ID and tiktok_service.settings.TIKTOK_APP_SECRET):
            raise CommandError("Set TIKTOK_APP_ID and TIKTOK_APP_SECRET first.")
        try:
            tiktok_service.register_webhook(callback_url)
        except tiktok_service.TikTokError as e:
            raise CommandError(f"TikTok refused the webhook: {e}") from e
        except requests.RequestException as e:
            raise CommandError(f"Could not reach TikTok: {e}") from e
        self.stdout.write(self.style.SUCCESS(f"TikTok DM webhook now points at {callback_url}"))
