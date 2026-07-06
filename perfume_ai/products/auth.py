from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import Store

class StoreAPIKeyAuthentication(BaseAuthentication):
    """
    Custom authentication class that requires an X-API-Key header.
    Validates the key against the Store model and attaches the store to the request.
    """
    def authenticate(self, request):
        api_key = request.META.get('HTTP_X_API_KEY')
        
        if not api_key:
            raise AuthenticationFailed('X-API-Key header is required.')

        try:
            store = Store.objects.get(api_key=api_key, is_active=True)
        except Store.DoesNotExist:
            raise AuthenticationFailed('Invalid or inactive API Key.')

        # Attach store to request so views can access request.store
        request.store = store
        
        # Return (user, auth) tuple required by DRF
        # We don't have a specific Django User here, so we return None for the user.
        return (None, store)
