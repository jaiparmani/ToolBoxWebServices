from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0015_remove_split_only_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='sentiment',
            field=models.CharField(
                blank=True,
                choices=[('good', 'Felt good'), ('neutral', 'Neutral'), ('regret', 'Regret')],
                max_length=10,
                null=True,
            ),
        ),
    ]
