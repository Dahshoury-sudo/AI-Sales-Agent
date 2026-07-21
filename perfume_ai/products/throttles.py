from rest_framework.throttling import SimpleRateThrottle


class StoreKeyThrottle(SimpleRateThrottle):
    """
    Rate-limits requests per store API key.
    This prevents a single store from overwhelming the system.
    """
    scope = 'store'
    rate = '60/minute'

    def get_cache_key(self, request, view):
        api_key = request.META.get('HTTP_X_API_KEY')
        if api_key:
            return self.cache_format % {
                'scope': self.scope,
                'ident': api_key
            }
        return self.get_ident(request)


class ChatThrottle(SimpleRateThrottle):
    """
    Stricter rate limit for the AI chat endpoint to control AI API costs.
    """
    scope = 'chat'
    rate = '30/minute'

    def get_cache_key(self, request, view):
        api_key = request.META.get('HTTP_X_API_KEY')
        if api_key:
            return self.cache_format % {
                'scope': self.scope,
                'ident': api_key
            }
        return self.get_ident(request)


class WebhookThrottle(SimpleRateThrottle):
    """
    Higher rate limit for Meta webhooks since they can send bursts.
    """
    scope = 'webhook'
    rate = '200/minute'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }
