"""Daily health review for every user who logged something recently.

Wire this to a scheduled task (PythonAnywhere Tasks tab, or cron):

    cd ~/toolboxweb && python manage.py generate_health_insights
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from health.models import HealthMetric
from insights.models import Insight
from insights.services import (
    InsightGenerationError,
    InsightNotPossible,
    generate_health_insight,
)


class Command(BaseCommand):
    help = "Generate a Claude health review for each user with recent metrics."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='Size of the window to analyse (default: 30).')
        parser.add_argument('--user-id', type=int, default=None,
                            help='Only run for this user.')
        parser.add_argument('--force', action='store_true',
                            help="Run even if today's insight already exists.")
        parser.add_argument('--dry-run', action='store_true',
                            help='Report who would be processed without calling Claude.')

    def handle(self, *args, **options):
        days = max(1, min(options['days'], 180))
        window_start = timezone.now().date() - timedelta(days=days - 1)
        today = timezone.now().date()

        users = User.objects.filter(is_active=True)
        if options['user_id']:
            users = users.filter(id=options['user_id'])

        # Only users with something logged in the window are worth a model call.
        active_ids = set(
            HealthMetric.objects
            .filter(date__gte=window_start, user__is_active=True)
            .values_list('user_id', flat=True)
            .distinct()
        )
        users = [u for u in users if u.id in active_ids]

        if not users:
            self.stdout.write(self.style.WARNING('No users with health metrics in the window.'))
            return

        generated = skipped = failed = 0

        for user in users:
            if not options['force'] and Insight.objects.filter(
                user=user, scope='health', status='success', created_at__date=today
            ).exists():
                self.stdout.write(f'- {user.username}: already has an insight for {today}, skipping')
                skipped += 1
                continue

            if options['dry_run']:
                self.stdout.write(f'- {user.username}: would generate ({days}-day window)')
                continue

            try:
                parsed, meta = generate_health_insight(user, days=days)
            except InsightNotPossible as exc:
                self.stdout.write(self.style.WARNING(f'- {user.username}: {exc}'))
                skipped += 1
                continue
            except InsightGenerationError as exc:
                Insight.objects.create(
                    user=user, scope='health', status='failed',
                    period_start=window_start, period_end=today, error_message=str(exc),
                )
                self.stdout.write(self.style.ERROR(f'- {user.username}: {exc}'))
                failed += 1
                continue

            Insight.objects.create(
                user=user,
                scope='health',
                status='success',
                period_start=meta['period_start'],
                period_end=meta['period_end'],
                headline=parsed['headline'][:255],
                summary=parsed['summary'],
                payload={
                    'observations': parsed['observations'],
                    'concerns': parsed['concerns'],
                    'suggestions': parsed['suggestions'],
                    'data_gaps': parsed['data_gaps'],
                    'entries_analysed': meta['entry_count'],
                },
                model=meta['model'],
                effort=meta['effort'],
                input_tokens=meta['input_tokens'],
                output_tokens=meta['output_tokens'],
            )
            generated += 1
            self.stdout.write(self.style.SUCCESS(
                f'- {user.username}: {parsed["headline"]} '
                f'({meta["input_tokens"]} in / {meta["output_tokens"]} out)'
            ))

        self.stdout.write(
            self.style.SUCCESS(f'Done. generated={generated} skipped={skipped} failed={failed}')
        )
