"""Send a weekly AI spending brief to all eligible users as a push notification.

    python manage.py send_weekly_brief

A user is eligible when they have more than 10 expenses and at least one active
PushSubscription. Errors for individual users are logged and do not stop the run.

The actual loop is expenses.services.run_weekly_brief_batch — shared with
POST /api/expenses/weekly-brief/run/, which exists because a plain console on
PythonAnywhere doesn't inherit the WSGI file's environment, so this command
can silently fail to find LLM_GATEWAY_URL/TOKEN when run from one.
"""

from django.core.management.base import BaseCommand

from expenses.services import run_weekly_brief_batch


class Command(BaseCommand):
    help = 'Send weekly AI brief to all users'

    def handle(self, *args, **options):
        result = run_weekly_brief_batch()
        self.stdout.write(self.style.SUCCESS(
            f"Weekly brief: {result['sent']} sent, {result['skipped']} skipped (too few expenses), "
            f"{result['errored']} error(s). Brain: {result['brain_ingested']} ingested, "
            f"{result['brain_errored']} failed"
            + (f" ({result['last_brain_error']})" if result['last_brain_error'] else '') + '.'
        ))
