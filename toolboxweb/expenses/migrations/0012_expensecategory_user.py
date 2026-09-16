"""
Make ExpenseCategory user-scoped.

Before: one global category pool, name is globally unique.
After:  user=null  → system/default category (visible to everyone, read-only via API)
        user=<pk>  → private category belonging only to that user

Existing categories keep user=null so all existing expenses keep their FK and
every existing user still sees every category they have already used.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('expenses', '0011_push_subscription'),
    ]

    operations = [
        # 1. Drop the old global unique constraint on name.
        migrations.AlterField(
            model_name='expensecategory',
            name='name',
            field=models.CharField(max_length=100),
        ),

        # 2. Add the user FK (nullable — existing rows stay as system defaults).
        migrations.AddField(
            model_name='expensecategory',
            name='user',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='expense_categories',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # 3. Per-user unique name constraint.
        migrations.AddConstraint(
            model_name='expensecategory',
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=False),
                fields=['user', 'name'],
                name='unique_category_name_per_user',
            ),
        ),

        # 4. System-category unique name constraint (replaces the old unique=True).
        migrations.AddConstraint(
            model_name='expensecategory',
            constraint=models.UniqueConstraint(
                condition=models.Q(user__isnull=True),
                fields=['name'],
                name='unique_system_category_name',
            ),
        ),
    ]
