# Introduce SharedBill and move every existing shared bill into it, all in one
# migration - deliberately, not Django's own makemigrations grouping.
#
# ExpenseSplit.expense targets SharedBill by the end of this migration, but
# SQLite checks foreign-key integrity when that field's underlying column is
# altered (a table rebuild, since SQLite has no native ALTER for a FK's
# target). If the AlterField ran before the data existed to satisfy it - or in
# a separate migration/transaction from the backfill that creates it - that
# check fails immediately. So the backfill (RunPython) runs first and the
# AlterField last, both inside this one migration/transaction: by the time
# SQLite checks, every ExpenseSplit.expense_id already points at a real
# SharedBill row.
#
# What the backfill moves, precisely:
#   - split_only=True Expenses: a pure receivable, never real spending. Its
#     SharedBill is created and its splits repointed, then the Expense itself
#     is deleted - it never represented anything but the bill now sitting in
#     SharedBill.
#   - split_only=False Expenses: the payer's bill AND their spending record,
#     in one row. The Expense survives, now linked from its new SharedBill via
#     `linked_expense` - still the payer's real personal expense, just no
#     longer double-booked as the bill itself.
#   - ExpenseSplit.include_in_expenses=True: used to count a counterparty's
#     share as their spending with no Expense row of their own -
#     net_spending's old third leg read the flag directly. That leg is gone;
#     counting a share now means having a real Expense, so one is created
#     here for every such split, exactly as SplitViewSet.perform_update does
#     going forward when someone flips that switch.
from decimal import Decimal
from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    Expense = apps.get_model('expenses', 'Expense')
    ExpenseSplit = apps.get_model('expenses', 'ExpenseSplit')
    SharedBill = apps.get_model('expenses', 'SharedBill')

    old_expense_ids = set(
        ExpenseSplit.objects.values_list('expense_id', flat=True).distinct())

    bills_created = 0
    expenses_retired = 0
    for expense in Expense.objects.filter(id__in=old_expense_ids):
        bill = SharedBill.objects.create(
            user_id=expense.user_id,
            amount=expense.amount,
            description=expense.description,
            date=expense.date,
            category_id=expense.category_id,
            group_id=expense.group_id,
            paid_by_person_id=expense.paid_by_person_id,
        )
        bill.tags.set(expense.tags.all())
        bills_created += 1

        ExpenseSplit.objects.filter(expense_id=expense.id).update(expense_id=bill.id)

        if expense.split_only:
            expense.delete()
            expenses_retired += 1
        else:
            bill.linked_expense_id = expense.id
            bill.save(update_fields=['linked_expense'])

    counted_created = 0
    for split in ExpenseSplit.objects.filter(include_in_expenses=True, linked_expense__isnull=True):
        person = split.person
        if not person or not person.linked_user_id:
            continue  # shouldn't happen - the flag could only be set by a linked account
        bill = split.expense
        counted = Expense.objects.create(
            user_id=person.linked_user_id,
            amount=split.amount,
            transaction_type='expense',
            category_id=bill.category_id,
            description=bill.description,
            date=bill.date,
            group_id=bill.group_id,
        )
        split.linked_expense_id = counted.id
        split.save(update_fields=['linked_expense'])
        counted_created += 1

    print(f'  SharedBill backfill: {bills_created} bills created, '
          f'{expenses_retired} split-only expenses retired, '
          f'{counted_created} counterparty expenses created.')


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('expenses', '0013_push_subscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='expensesplit',
            name='linked_expense',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='counted_split', to='expenses.expense'),
        ),
        migrations.CreateModel(
            name='SharedBill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('description', models.TextField()),
                ('date', models.DateField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shared_bills', to='expenses.expensecategory')),
                ('group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bills', to='expenses.splitgroup')),
                ('linked_expense', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shared_bill', to='expenses.expense')),
                ('paid_by_person', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='paid_bills', to='expenses.person')),
                ('tags', models.ManyToManyField(blank=True, related_name='shared_bills', to='expenses.expensetag')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shared_bills', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-date', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='sharedbill',
            index=models.Index(fields=['user', 'date'], name='expenses_sh_user_id_68208b_idx'),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='expensesplit',
            name='expense',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='splits', to='expenses.sharedbill'),
        ),
    ]
