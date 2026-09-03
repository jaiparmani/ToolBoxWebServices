from django.contrib import admin

from .models import TelegramLink


@admin.register(TelegramLink)
class TelegramLinkAdmin(admin.ModelAdmin):
    list_display = ("chat_id", "user", "username", "awaiting_import", "created_at")
    search_fields = ("chat_id", "username", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
