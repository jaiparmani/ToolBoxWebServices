from django.urls import path

from . import views

app_name = "whatsappbot"

urlpatterns = [
    # Meta GETs this once to verify the webhook, then POSTs updates here.
    # The secret in the path must match settings.WHATSAPP_WEBHOOK_SECRET.
    path("webhook/<str:secret>/", views.webhook, name="webhook"),
    # Authenticated: link/unlink the signed-in account to a WhatsApp number.
    path("link/", views.whatsapp_link, name="link"),
]
