from django.http import JsonResponse
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist


class UserIdValidationMiddleware:
    """
    Middleware to validate userid query parameter and attach user to request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only process API requests (those starting with /api/)
        if request.path.startswith('/api/'):
            userid = request.GET.get('userid')

            if userid:
                try:
                    # Validate userid exists
                    user = User.objects.get(id=userid, is_active=True)
                    # Attach user to request for use in views
                    request.validated_user = user
                except (ObjectDoesNotExist, ValueError):
                    return JsonResponse(
                        {'error': 'Invalid or inactive userid provided'},
                        status=400
                    )
            else:
                # For endpoints that require a userid, they will handle the error
                request.validated_user = None

        response = self.get_response(request)
        return response