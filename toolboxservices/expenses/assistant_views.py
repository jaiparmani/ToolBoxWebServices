"""The single assistant endpoint: POST /api/assistant/.

One authenticated surface for every AI capability. A message is routed and
handled by expenses.assistant; a {commit: ...} payload writes a confirmed draft.
It lives apart from views.py so the agent can import view helpers without a
circular import.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import assistant
from .services import ExpenseParseError, ExpenseParseNotPossible, ExpenseParseRateLimited


class AssistantView(APIView):
    """POST a free-text message, or a {commit: ...} to save a confirmed draft."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        # Confirm path: write exactly the draft that was previewed.
        if request.data.get('commit'):
            try:
                return Response(assistant.commit(user, request.data), status=status.HTTP_201_CREATED)
            except ExpenseParseNotPossible as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        message = request.data.get('message', '')
        if not message or not message.strip():
            return Response({'error': 'message is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = assistant.run(user, message)
        except ExpenseParseNotPossible as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ExpenseParseRateLimited as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except ExpenseParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        # Echo the conversation id so the client can thread context if it wants.
        conversation_id = request.data.get('conversation_id')
        if conversation_id:
            result['conversation_id'] = conversation_id
        return Response(result)
