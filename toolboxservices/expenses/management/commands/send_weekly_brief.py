"""Send a weekly AI spending brief to all eligible users as a push notification.

    python manage.py send_weekly_brief

A user is eligible when they have more than 10 expenses and at least one active
PushSubscription. Errors for individual users are logged and do not stop the run.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from expenses.models import PushSubscription, notify
from expenses.services import (
    ExpenseParseError, ExpenseParseNotPossible, ExpenseParseRateLimited,
    generate_weekly_brief,
)
from expenses.models import Expense

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send weekly AI brief to all users'

    def handle(self, *args, **options):
        User = get_user_model()

        # Users who have enough history and at least one push subscription
        eligible_ids = (
            PushSubscription.objects.values_list('user_id', flat=True).distinct()
        )
        users = User.objects.filter(is_active=True, id__in=eligible_ids)

        sent, skipped, errored = 0, 0, 0
        for user in users:
            expense_count = Expense.objects.filter(user=user).count()
            if expense_count <= 10:
                skipped += 1
                continue
            try:
                brief = generate_weekly_brief(user)
                notify(user, 'Your weekly money brief', brief,
                       kind='insight', link='/expense-tracker')
                sent += 1
            except (ExpenseParseNotPossible, ExpenseParseRateLimited, ExpenseParseError) as exc:
                logger.warning('send_weekly_brief: user %s LLM error: %s', user.id, exc)
                errored += 1
            except Exception as exc:
                logger.error('send_weekly_brief: user %s unexpected error: %s', user.id, exc,
                             exc_info=True)
                errored += 1

        self.stdout.write(self.style.SUCCESS(
            f'Weekly brief: {sent} sent, {skipped} skipped (too few expenses), '
            f'{errored} error(s).'
        ))
