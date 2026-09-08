from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class ApiKeyAuthentication(BaseAuthentication):
    """Accept ``Authorization: Api-Key tbk_…`` or ``Authorization: Bearer tbk_…``.

    The second form lets ChatGPT Custom GPT Actions (which only offer Bearer
    auth) work without a proxy, while keeping the original ``Api-Key`` scheme
    for Apple Shortcuts and other headless clients.
    """
    _KEYWORDS = {'Api-Key', 'Bearer'}

    def authenticate(self, request):
        auth = request.META.get('HTTP_AUTHORIZATION', '').split()
        if len(auth) != 2 or auth[0] not in self._KEYWORDS:
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
