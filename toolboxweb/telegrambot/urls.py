from django.urls import path

from . import views

app_name = "telegrambot"

urlpatterns = [
    # Telegram POSTs updates here. The secret in the path must match
    # settings.TELEGRAM_WEBHOOK_SECRET (see set_telegram_webhook command).
    path("webhook/<str:secret>/", views.webhook, name="webhook"),
    # Authenticated: link/unlink the signed-in account to a Telegram id (Settings).
    path("link/", views.telegram_link, name="link"),
]
