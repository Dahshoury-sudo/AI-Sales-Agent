import logging
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate

from products.models import Store, StoreSettings
from products.throttles import (
    LoginThrottle,
    PasswordResetThrottle,
    RegisterThrottle,
)
from .auth_backend import StoreOwnerAuthentication

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterThrottle]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")
        store_name = request.data.get("store_name", "").strip()
        full_name = request.data.get("full_name", "").strip()

        if not email or not password or not store_name:
            return Response({"error": "email, password, and store_name are required."}, status=400)

        if len(password) < 8:
            return Response({"error": "Password must be at least 8 characters."}, status=400)

        if User.objects.filter(username=email).exists():
            return Response({"error": "An account with this email already exists."}, status=400)

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=password,
                    first_name=full_name.split()[0] if full_name else "",
                    last_name=" ".join(full_name.split()[1:]) if full_name else "",
                )

                store = Store.objects.create(
                    owner=user,
                    name=store_name,
                )

                StoreSettings.objects.create(store=store)

            refresh = RefreshToken.for_user(user)
            return Response({
                "message": "Account created successfully.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "store": {
                    "id": store.id,
                    "name": store.name,
                    "api_key": store.api_key,
                }
            }, status=201)

        except Exception as e:
            logger.exception(f"Registration error: {e}")
            return Response({"error": "An error occurred during registration."}, status=500)


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")

        if not email or not password:
            return Response({"error": "Email and password are required."}, status=400)

        user = authenticate(username=email, password=password)

        if user is None:
            return Response({"error": "Invalid email or password."}, status=401)

        store = Store.objects.filter(owner=user, is_active=True).first()
        if not store:
            return Response({"error": "No active store found for this account."}, status=404)

        refresh = RefreshToken.for_user(user)
        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "store": {
                "id": store.id,
                "name": store.name,
            }
        })


class ProfileView(APIView):
    authentication_classes = [StoreOwnerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        store = request.store
        return Response({
            "email": user.email,
            "full_name": f"{user.first_name} {user.last_name}".strip(),
            "store": {
                "id": store.id,
                "name": store.name,
                "api_key": store.api_key,
                "created_at": store.created_at.isoformat(),
            }
        })

    def put(self, request):
        user = request.user
        store = request.store

        full_name = request.data.get("full_name", "").strip()
        store_name = request.data.get("store_name", "").strip()

        if full_name:
            parts = full_name.split()
            user.first_name = parts[0]
            user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
            user.save()

        if store_name:
            store.name = store_name
            store.save()

        return Response({"message": "Profile updated."})


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return Response({"error": "Email is required."}, status=400)

        # Always return success to prevent email enumeration attacks
        try:
            user = User.objects.get(email=email)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)

            reset_url = f"{request.scheme}://{request.get_host()}/dashboard/reset-password/{uid}/{token}/"

            send_mail(
                subject="إعادة تعيين كلمة المرور - Perfume AI",
                message=f"مرحباً {user.first_name}،\n\nاضغط على الرابط التالي لإعادة تعيين كلمة المرور:\n{reset_url}\n\nالرابط صالح لمدة ساعة واحدة.\n\nلو مبعتش الطلب ده، تجاهل الرسالة.\n\n— فريق Perfume AI",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            logger.info(f"Password reset email sent to {email}")
        except User.DoesNotExist:
            logger.info(f"Password reset requested for non-existent email: {email}")
        except Exception as e:
            logger.exception(f"Error sending reset email: {e}")

        return Response({"message": "لو الإيميل ده مسجل عندنا، هتوصلك رسالة فيها رابط إعادة التعيين."})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request, uidb64, token):
        password = request.data.get("password", "")

        if not password or len(password) < 8:
            return Response({"error": "Password must be at least 8 characters."}, status=400)

        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response({"error": "Invalid reset link."}, status=400)

        if not default_token_generator.check_token(user, token):
            return Response({"error": "الرابط منتهي الصلاحية أو مستخدم قبل كده. اطلب رابط جديد."}, status=400)

        user.set_password(password)
        user.save()

        return Response({"message": "تم تغيير كلمة المرور بنجاح! سجل دخولك بالباسورد الجديد."})

