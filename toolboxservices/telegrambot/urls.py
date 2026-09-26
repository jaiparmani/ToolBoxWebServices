from django.urls import path

from . import views

app_name = "telegrambot"

urlpatterns = [
    # telegram-router POSTs here once it has classified a message as ToolBox's.
    # Gated by settings.TELEGRAM_ROUTER_TOKEN, not a Telegram-issued secret —
    # this app no longer receives Telegram's webhook directly.
    path("relay/", views.relay, name="relay"),
    # Authenticated: link/unlink the signed-in account to a Telegram id (Settings).
    path("link/", views.telegram_link, name="link"),
]
