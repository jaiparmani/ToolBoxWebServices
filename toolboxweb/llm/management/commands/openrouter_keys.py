"""Manage the OpenRouter keys used for AI features.

There is no Django admin on this project, so this is how keys get added:

    manage.py openrouter_keys add sk-or-v1-... --label "personal"
    manage.py openrouter_keys list
    manage.py openrouter_keys remove 3

Keys are only ever printed masked.
"""

from django.core.management.base import BaseCommand, CommandError

from llm.models import OpenRouterKey


class Command(BaseCommand):
    help = 'Add, list and manage the OpenRouter API keys used for AI features.'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)

        add = sub.add_parser('add', help='Add a key to the rotation')
        add.add_argument('key')
        add.add_argument('--label', default='', help='Something to recognise it by')

        sub.add_parser('list', help='Show the key queue, front first')

        remove = sub.add_parser('remove', help='Delete a key')
        remove.add_argument('id', type=int)

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
        self.stdout.write(f"{OpenRouterKey.objects.count()} key(s) in the queue.")

    def _list(self, options):
        keys = OpenRouterKey.objects.all()
        if not keys:
            self.stdout.write('No keys stored. Add one with: '
                              'manage.py openrouter_keys add sk-or-v1-...')
            return

        self.stdout.write(f"{'#':<3} {'id':<4} {'key':<22} label")
        self.stdout.write('-' * 52)
        for i, k in enumerate(keys, 1):
            arrow = '->' if i == 1 else '  '
            self.stdout.write(f"{arrow}{i:<2} {k.id:<4} {k.masked:<22} {k.label or '-'}")
        self.stdout.write(f"\n{len(keys)} key(s) queued; '->' is next up.")

    def _remove(self, options):
        record = self._get(options)
        masked = record.masked
        record.delete()
        self.stdout.write(self.style.SUCCESS(f'Removed {masked}'))
