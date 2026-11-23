"""Connect a store's TikTok Business account to the WebVitas TikTok app.

The dashboard authenticates with a JWT kept in localStorage, and TikTok's redirect
back carries no Authorization header. So the redirect lands on a static page
(tiktok_callback.html), and that page sends code and state here with the owner's
JWT. The connection is saved only when the state was issued to this same owner and
store, so nobody can send an owner a link that attaches their own TikTok account to
the owner's store, or the owner's account to theirs.
"""

import logging

import requests
from django.core import signing
from django.db import IntegrityError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from products.models import StoreSettings
from products.services import tiktok_service
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)

STATE_SALT = "tiktok-oauth"
STATE_MAX_AGE = 600


def _status(store_settings):
    status = tiktok_service.connection_status(store_settings)
    for field in ("access_expires_at", "refresh_expires_at"):
        status[field] = status[field].isoformat() if status[field] else None
    return status


class _TikTokView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def settings_row(self, request):
        return StoreSettings.objects.get_or_create(store=request.store)[0]


class TikTokStatusView(_TikTokView):
    def get(self, request):
        return Response(_status(self.settings_row(request)))


class TikTokConnectView(_TikTokView):
    def post(self, request):
        if not tiktok_service.configured():
            return Response({"error": "ربط تيك توك مش متاح لسه على السيرفر ده "
                                      "(TIKTOK_APP_ID / TIKTOK_APP_SECRET / TIKTOK_REDIRECT_URI)."},
                            status=400)
        state = signing.dumps({"s": request.store.pk, "u": request.user.pk}, salt=STATE_SALT)
        return Response({"authorize_url": tiktok_service.authorize_url(state)})


class TikTokCompleteView(_TikTokView):
    def post(self, request):
        code, state = request.data.get("code"), request.data.get("state")
        if not isinstance(code, str) or not code or not isinstance(state, str) or not state:
            return Response({"error": "رد تيك توك ناقص. جرّب الربط تاني."}, status=400)
        try:
            issued = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
        except signing.SignatureExpired:
            return Response({"error": "انتهت مهلة الربط. اضغط \"ربط تيك توك\" تاني."}, status=400)
        except signing.BadSignature:
            return Response({"error": "طلب الربط ده مش صالح."}, status=400)
        if issued != {"s": request.store.pk, "u": request.user.pk}:
            logger.warning("TikTok connect state issued to another owner or store (store %s)",
                           request.store.pk)
            return Response({"error": "طلب الربط ده مش بتاع حسابك. اضغط \"ربط تيك توك\" من حسابك."},
                            status=403)

        try:
            grant = tiktok_service.exchange_code(code)
        except (tiktok_service.TikTokError, requests.RequestException):
            logger.exception("TikTok code exchange failed for store %s", request.store.pk)
            return Response({"error": "تيك توك رفض الربط. جرّب تاني بعد شوية."}, status=502)

        missing = [scope for scope in tiktok_service.REQUIRED_SCOPES if scope not in grant.scopes]
        if grant.scopes and missing:
            return Response({"error": "لازم توافق على صلاحيات الرسائل كلها في صفحة تيك توك: "
                                      + ", ".join(missing)}, status=400)
        if not grant.business_id:
            return Response({"error": "تيك توك مارجعش رقم الحساب. جرّب الربط تاني."}, status=502)

        account = tiktok_service.fetch_account(grant.access_token, grant.business_id)
        if account["is_business"] is False:
            return Response({"error": "الحساب ده مش Business Account. حوّل حساب تيك توك لحساب "
                                      "بيزنس من إعدادات التطبيق وبعدين اربطه."}, status=400)

        taken = (StoreSettings.objects.filter(tiktok_business_id=grant.business_id)
                 .exclude(store=request.store).exists())
        if taken:
            return Response({"error": "حساب تيك توك ده مربوط بمتجر تاني."}, status=409)

        store_settings = self.settings_row(request)
        store_settings.tiktok_business_id = grant.business_id
        store_settings.tiktok_username = account["username"]
        tiktok_service.apply_grant(store_settings, grant)
        try:
            store_settings.save(update_fields=["tiktok_business_id", "tiktok_username",
                                               *tiktok_service.TOKEN_FIELDS])
        except IntegrityError:
            # Another store connected the same account between the check and the save.
            return Response({"error": "حساب تيك توك ده مربوط بمتجر تاني."}, status=409)
        return Response(_status(store_settings))


class TikTokDisconnectView(_TikTokView):
    def post(self, request):
        store_settings = self.settings_row(request)
        tiktok_service.disconnect(store_settings)
        return Response(_status(store_settings))
