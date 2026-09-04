from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class ApiKeyAuthentication(BaseAuthentication):
    keyword = 'Api-Key'

    def authenticate(self, request):
        auth = request.META.get('HTTP_AUTHORIZATION', '').split()
        if len(auth) != 2 or auth[0] != self.keyword:
            return None

        key = auth[1]
        if not key.startswith('tbk_'):
            raise AuthenticationFailed('Invalid API key format.')

        from .models import ShortcutAPIKey
        try:
            api_key = ShortcutAPIKey.objects.select_related('user').get(key=key)
        except ShortcutAPIKey.DoesNotExist:
            raise AuthenticationFailed('Invalid API key.')

        if not api_key.user.is_active:
            raise AuthenticationFailed('User account is disabled.')

        api_key.touch()
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return self.keyword
