"""Manage the OpenRouter keys used for AI features.

There is no Django admin on this project, so this is how keys get added:

    manage.py openrouter_keys add sk-or-v1-... --label "personal"
    manage.py openrouter_keys list
    manage.py openrouter_keys disable 3
    manage.py openrouter_keys enable 3
    manage.py openrouter_keys clear-limits
    manage.py openrouter_keys remove 3

Keys are only ever printed masked.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from llm.models import OpenRouterKey


class Command(BaseCommand):
    help = 'Add, list and manage the OpenRouter API keys used for AI features.'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)

        add = sub.add_parser('add', help='Add a key to the rotation')
        add.add_argument('key')
        add.add_argument('--label', default='', help='Something to recognise it by')

        sub.add_parser('list', help='Show every key and its rotation state')
        sub.add_parser('clear-limits', help='Forget recorded rate limits and retry every key')

        for name, helptext in (('disable', 'Take a key out of rotation'),
                               ('enable', 'Put a key back in rotation'),
                               ('remove', 'Delete a key')):
            p = sub.add_parser(name, help=helptext)
            p.add_argument('id', type=int)

    def handle(self, *args, **options):
        getattr(self, f"_{options['action'].replace('-', '_')}")(options)

    def _get(self, options):
        try:
            return OpenRouterKey.objects.get(pk=options['id'])
        except OpenRouterKey.DoesNotExist:
            raise CommandError(f"No key with id {options['id']}. Run 'list' to see them.")

    def _add(self, options):
        key = options['key'].strip()
        if not key.startswith('sk-'):
            raise CommandError("That doesn't look like an OpenRouter key (expected sk-...).")
        if OpenRouterKey.objects.filter(key=key).exists():
            raise CommandError('That key is already stored.')

        record = OpenRouterKey.objects.create(key=key, label=options['label'])
        self.stdout.write(self.style.SUCCESS(
            f"Added key {record.id}: {record.masked}"
            + (f" ({record.label})" if record.label else '')))
        self.stdout.write(f"{OpenRouterKey.objects.usable().count()} key(s) now in rotation.")

    def _list(self, options):
        keys = OpenRouterKey.objects.all()
        if not keys:
            self.stdout.write('No keys stored. Add one with: '
                              'manage.py openrouter_keys add sk-or-v1-...')
            return

        now = timezone.now()
        self.stdout.write(f"{'id':<4} {'key':<22} {'label':<14} {'state':<24} {'uses':>5}  last used")
        self.stdout.write('-' * 88)
        for k in keys:
            if not k.is_active:
                state = 'disabled'
            elif k.is_cooling_down:
                mins = int((k.rate_limited_until - now).total_seconds() // 60)
                state = f'rate limited ({mins}m left)'
            else:
                state = 'ready'
            last = k.last_used_at.strftime('%Y-%m-%d %H:%M') if k.last_used_at else 'never'
            self.stdout.write(
                f"{k.id:<4} {k.masked:<22} {(k.label or '-'):<14} {state:<24} {k.use_count:>5}  {last}")
        self.stdout.write(f"\n{OpenRouterKey.objects.usable().count()} of {len(keys)} ready to use.")

    def _disable(self, options):
        record = self._get(options)
        record.is_active = False
        record.save(update_fields=['is_active'])
        self.stdout.write(self.style.SUCCESS(f'Disabled {record.masked}'))

    def _enable(self, options):
        record = self._get(options)
        record.is_active = True
        record.save(update_fields=['is_active'])
        self.stdout.write(self.style.SUCCESS(f'Enabled {record.masked}'))

    def _remove(self, options):
        record = self._get(options)
        masked = record.masked
        record.delete()
        self.stdout.write(self.style.SUCCESS(f'Removed {masked}'))

    def _clear_limits(self, options):
        count = OpenRouterKey.objects.exclude(rate_limited_until=None).update(
            rate_limited_until=None, last_error='')
        self.stdout.write(self.style.SUCCESS(f'Cleared rate limits on {count} key(s).'))
