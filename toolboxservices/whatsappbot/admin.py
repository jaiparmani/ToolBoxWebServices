from django.contrib import admin

from .models import WhatsAppLink


@admin.register(WhatsAppLink)
class WhatsAppLinkAdmin(admin.ModelAdmin):
    list_display = ("wa_id", "user", "name", "awaiting_import", "created_at")
    search_fields = ("wa_id", "name", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
