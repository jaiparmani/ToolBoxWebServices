# The last step: drop the two columns SharedBill replaces, now that 0014 has
# moved everything they held into real rows (a SharedBill, and - for
# include_in_expenses=True - a counterparty's own Expense).
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0014_sharedbill'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='expense',
            name='split_only',
        ),
        migrations.RemoveField(
            model_name='expensesplit',
            name='include_in_expenses',
        ),
    ]
