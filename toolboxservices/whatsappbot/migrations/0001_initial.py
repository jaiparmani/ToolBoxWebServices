import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WhatsAppLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('wa_id', models.CharField(db_index=True, max_length=32, unique=True)),
                ('name', models.CharField(blank=True, default='', max_length=255)),
                ('awaiting_import', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='whatsapp_link', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'WhatsApp link',
                'verbose_name_plural': 'WhatsApp links',
            },
        ),
    ]
