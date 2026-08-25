"""Regenerate copilot cards for every active user.

The cards also refresh lazily when a user opens the dashboard/inbox, so this
command is optional - but wiring it to a scheduled task (e.g. a daily
PythonAnywhere task) means concerns like a bill that will overdraw are ready
the moment the user looks, and "new since yesterday" is meaningful.

    python manage.py generate_copilot_cards
    python manage.py generate_copilot_cards --user 1
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from expenses import copilot


class Command(BaseCommand):
    help = "Regenerate copilot cards from each user's live data."

    def add_arguments(self, parser):
        parser.add_argument('--user', type=int, default=None,
                            help='Only this user id (default: all active users).')

    def handle(self, *args, **options):
        User = get_user_model()
        qs = User.objects.filter(is_active=True)
        if options['user']:
            qs = qs.filter(id=options['user'])

        total_users, total_cards = 0, 0
        for user in qs:
            try:
                cards = copilot.refresh(user)
            except Exception as exc:  # one user's bad data must not stop the run
                self.stderr.write(f"user {user.id}: {exc}")
                continue
            total_users += 1
            total_cards += len(cards)
        self.stdout.write(self.style.SUCCESS(
            f"Refreshed copilot cards for {total_users} user(s); {total_cards} live card(s)."))
