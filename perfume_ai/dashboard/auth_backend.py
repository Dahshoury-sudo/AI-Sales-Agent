from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from products.models import Store


class StoreOwnerAuthentication(JWTAuthentication):
    """
    JWT authentication that also attaches the user's store to the request.
    """
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result

        try:
            store = Store.objects.get(owner=user, is_active=True)
        except Store.DoesNotExist:
            raise AuthenticationFailed("No active store found for this user.")
        except Store.MultipleObjectsReturned:
            store = Store.objects.filter(owner=user, is_active=True).first()

        request.store = store
        return (user, token)
