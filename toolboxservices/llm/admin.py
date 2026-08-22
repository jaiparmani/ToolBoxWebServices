from django.contrib import admin

from .models import OpenRouterKey


@admin.register(OpenRouterKey)
class OpenRouterKeyAdmin(admin.ModelAdmin):
    """The key queue: the row at the top is the one the next call will use."""

    list_display = ('queue_position', 'masked', 'label', 'created_at')
    list_display_links = ('masked',)
    search_fields = ('label',)
    ordering = ('position', 'id')
    actions = ['send_to_back']

    @admin.display(description='Queue')
    def queue_position(self, obj):
        first = OpenRouterKey.objects.order_by('position', 'id').first()
        return 'next' if first and first.pk == obj.pk else obj.position

    def get_readonly_fields(self, request, obj=None):
        # The key is entered once. Afterwards only the masked form is shown, so
        # a stored secret isn't put back on screen every time a label is edited.
        return ('masked', 'position', 'created_at') if obj else ()

    def get_fields(self, request, obj=None):
        if obj:
            return ('masked', 'label', 'position', 'created_at')
        return ('key', 'label')

    @admin.action(description='Send selected keys to the back of the queue')
    def send_to_back(self, request, queryset):
        for record in queryset.order_by('position', 'id'):
            record.push_to_back()
        self.message_user(request, f'Moved {queryset.count()} key(s) to the back.')
