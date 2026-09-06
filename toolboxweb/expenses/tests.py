"""Splits, read from both ends.

The whole point of Person.linked_user is that one ExpenseSplit row is "owed to
me" for whoever paid and "I owe" for the account the person signs in with -
never two mirrored rows. These tests pin that down from three accounts at once:
the payer (ashok), the counterparty (jai) and a stranger (priya) who must see
nothing at all.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import Expense, ExpenseCategory, ExpenseSplit, Person, SplitGroup


class TwoSidedSplitTests(APITestCase):
    """A split named at one account has to be real for the other one too."""

    def setUp(self):
        self.ashok = User.objects.create_user('ashok', password='x')
        self.jai = User.objects.create_user('jai', password='x')
        self.priya = User.objects.create_user('priya', password='x')
        for user in (self.ashok, self.jai, self.priya):
            Token.objects.get_or_create(user=user)
        self.category = ExpenseCategory.objects.create(
            name='Shared', transaction_type='expense')

    def auth(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.get(user=user).key}')

    def make_split(self, amount='1200', share=None, split_with_me=True):
        """Ashok pays `amount` and names jai's account as a participant."""
        self.auth(self.ashok)
        participant = {'user_id': self.jai.id}
        if share is not None:
            participant['amount'] = share
        response = self.client.post('/api/expenses/expenses/create_split/', {
            'amount': amount,
            'description': 'dinner',
            'category_id': self.category.id,
            'split_with_me': split_with_me,
            'participants': [participant],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    # ── Bug 1: the counterparty can see it ─────────────────────────────────

    def test_split_is_visible_to_the_linked_account(self):
        self.make_split()

        self.auth(self.jai)
        listed = self.client.get('/api/expenses/splits/').data['results']
        self.assertEqual(len(listed), 1)
        row = listed[0]
        self.assertEqual(row['direction'], 'you_owe')
        self.assertEqual(row['counterparty'], 'ashok')
        self.assertTrue(row['can_edit'])
        self.assertEqual(Decimal(row['amount']), Decimal('600.00'))

    def test_payer_reads_the_same_row_the_other_way(self):
        self.make_split()

        self.auth(self.ashok)
        row = self.client.get('/api/expenses/splits/').data['results'][0]
        self.assertEqual(row['direction'], 'owed_to_you')
        self.assertEqual(row['counterparty'], 'jai')

        # One row, not a mirrored pair.
        self.assertEqual(ExpenseSplit.objects.count(), 1)

    def test_balances_read_from_both_ends(self):
        self.make_split()

        self.auth(self.ashok)
        mine = self.client.get('/api/expenses/splits/balances/').data
        self.assertEqual(Decimal(mine['total_owed_to_you']), Decimal('600.00'))
        self.assertEqual(Decimal(mine['total_you_owe']), Decimal('0'))

        self.auth(self.jai)
        theirs = self.client.get('/api/expenses/splits/balances/').data
        self.assertEqual(Decimal(theirs['total_you_owe']), Decimal('600.00'))
        self.assertEqual(Decimal(theirs['total_owed_to_you']), Decimal('0'))
        self.assertEqual(theirs['you_owe'][0]['name'], 'ashok')

    def test_debtor_can_filter_by_the_account_they_owe(self):
        self.make_split()
        self.auth(self.jai)
        listed = self.client.get(
            f'/api/expenses/splits/?owed_to={self.ashok.id}&settled=false').data['results']
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['direction'], 'you_owe')

    def test_a_third_account_sees_nothing(self):
        self.make_split()
        self.auth(self.priya)
        self.assertEqual(self.client.get('/api/expenses/splits/').data['results'], [])
        balances = self.client.get('/api/expenses/splits/balances/').data
        self.assertEqual(Decimal(balances['total_owed_to_you']), Decimal('0'))
        self.assertEqual(Decimal(balances['total_you_owe']), Decimal('0'))

        split = ExpenseSplit.objects.get()
        self.assertEqual(self.client.get(f'/api/expenses/splits/{split.id}/').status_code, 404)
        self.assertEqual(
            self.client.patch(f'/api/expenses/splits/{split.id}/',
                              {'amount': '1'}, format='json').status_code, 404)
        self.assertEqual(
            self.client.delete(f'/api/expenses/splits/{split.id}/').status_code, 404)

    # ── Bug 4: the other person can edit it ────────────────────────────────

    def test_counterparty_can_edit_the_split(self):
        self.make_split()
        split = ExpenseSplit.objects.get()

        self.auth(self.jai)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'amount': '500.00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        self.assertEqual(split.amount, Decimal('500.00'))
        # The bill still fits, so it is untouched - only the payer's residual moved.
        self.assertEqual(split.expense.amount, Decimal('1200.00'))

    def test_counterparty_can_delete_the_split(self):
        self.make_split()
        split = ExpenseSplit.objects.get()
        self.auth(self.jai)
        self.assertEqual(
            self.client.delete(f'/api/expenses/splits/{split.id}/').status_code, 204)
        self.assertFalse(ExpenseSplit.objects.exists())

    def test_counterparty_delete_does_not_destroy_the_payers_expense(self):
        """Jai removing the share he owes must not delete ashok's expense.

        Deleting the last split takes the parent expense with it only when the
        payer does it - it is the payer's record. For a counterparty the bill
        just shrinks back to the payer's own residual.
        """
        self.make_split(amount='1200', share='500')
        split = ExpenseSplit.objects.get()
        expense_id = split.expense_id
        self.auth(self.jai)
        self.assertEqual(
            self.client.delete(f'/api/expenses/splits/{split.id}/').status_code, 204)
        expense = Expense.objects.filter(id=expense_id).first()
        self.assertIsNotNone(expense, "the payer's expense was deleted by the borrower")
        self.assertEqual(expense.amount, Decimal('700.00'))

    def test_payer_deleting_last_split_still_removes_the_expense(self):
        """The owner's own delete keeps its original cascade."""
        self.make_split(amount='1200', share='500')
        split = ExpenseSplit.objects.get()
        expense_id = split.expense_id
        self.auth(self.ashok)
        self.assertEqual(
            self.client.delete(f'/api/expenses/splits/{split.id}/').status_code, 204)
        self.assertFalse(Expense.objects.filter(id=expense_id).exists())

    def test_counterparty_can_settle(self):
        self.make_split()
        self.auth(self.jai)
        response = self.client.post('/api/expenses/splits/settle/',
                                    {'owed_to_user_id': self.ashok.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(ExpenseSplit.objects.get().is_settled)

    # ── Bug 2: the payer's own share may be zero ───────────────────────────

    def test_payer_can_cover_the_whole_bill(self):
        """Ashok pays 1200 and jai's share is all of it: ashok owes nothing."""
        data = self.make_split(amount='1200', share='1200')
        self.assertEqual(Decimal(str(data['your_share'])), Decimal('0.00'))
        self.assertEqual(Decimal(str(data['owed_to_you'])), Decimal('1200.00'))
        expense = Expense.objects.get()
        self.assertEqual(expense.amount, Decimal('1200.00'))
        self.assertEqual(expense.splits.get().amount, Decimal('1200.00'))

    def test_split_without_me_leaves_the_payer_at_zero(self):
        data = self.make_split(amount='1200', split_with_me=False)
        self.assertEqual(Decimal(str(data['your_share'])), Decimal('0.00'))

    def test_editing_a_share_up_to_the_whole_bill_zeroes_the_payer(self):
        """The bug: raising a share used to inflate the bill instead."""
        self.make_split(amount='1200')  # jai owes 600, ashok's residual 600
        split = ExpenseSplit.objects.get()

        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'amount': '1200.00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        self.assertEqual(split.expense.amount, Decimal('1200.00'))  # not 1800
        self.assertEqual(split.expense.amount - split.amount, Decimal('0.00'))

    def test_a_share_past_the_bill_grows_the_bill_to_exactly_the_shares(self):
        self.make_split(amount='1200')
        split = ExpenseSplit.objects.get()
        self.auth(self.ashok)
        self.client.patch(f'/api/expenses/splits/{split.id}/',
                          {'amount': '1500.00'}, format='json')
        split.refresh_from_db()
        self.assertEqual(split.expense.amount, Decimal('1500.00'))

    def test_a_zero_share_for_someone_else_is_still_rejected(self):
        """A row of zero says nothing; only the payer's residual may be zero."""
        self.make_split()
        split = ExpenseSplit.objects.get()
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'amount': '0'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ExpenseSplit.objects.get().amount, Decimal('600.00'))

    def test_shares_over_the_bill_are_rejected_at_creation(self):
        self.auth(self.ashok)
        response = self.client.post('/api/expenses/expenses/create_split/', {
            'amount': '1000', 'description': 'dinner', 'category_id': self.category.id,
            'participants': [{'user_id': self.jai.id, 'amount': '1500'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)


class GroupVisibilityTests(APITestCase):
    """A group is one account's list, but its members are parties to its bills."""

    def setUp(self):
        self.ashok = User.objects.create_user('ashok', password='x')
        self.jai = User.objects.create_user('jai', password='x')
        self.priya = User.objects.create_user('priya', password='x')
        for user in (self.ashok, self.jai, self.priya):
            Token.objects.get_or_create(user=user)
        self.category = ExpenseCategory.objects.create(
            name='Shared', transaction_type='expense')

        self.group = SplitGroup.objects.create(owner=self.ashok, name='Flat')
        self.jai_person = Person.objects.create(
            user=self.ashok, name='jai', linked_user=self.jai)
        self.priya_person = Person.objects.create(
            user=self.ashok, name='priya', linked_user=self.priya)
        self.group.members.add(self.jai_person, self.priya_person)

        expense = Expense.objects.create(
            user=self.ashok, amount=Decimal('900.00'), transaction_type='expense',
            category=self.category, description='rent', date='2026-01-01',
            group=self.group)
        ExpenseSplit.objects.create(
            expense=expense, person=self.jai_person, amount=Decimal('450.00'))
        ExpenseSplit.objects.create(
            expense=expense, person=self.priya_person, amount=Decimal('300.00'))

    def auth(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.get(user=user).key}')

    def test_member_sees_the_group(self):
        self.auth(self.jai)
        groups = self.client.get('/api/expenses/groups/').data
        self.assertEqual([g['name'] for g in groups], ['Flat'])

    def test_member_sees_the_whole_group_ledger(self):
        """A group is a shared context: members read the same ledger as the owner."""
        self.auth(self.jai)
        data = self.client.get(f'/api/expenses/groups/{self.group.id}/balances/').data
        self.assertFalse(data['viewer_is_owner'])
        self.assertEqual(data['owner_username'], 'ashok')
        # Their own side is still called out separately...
        self.assertEqual(Decimal(data['your_share_outstanding']), Decimal('450.00'))
        # ...but every member is visible, not just themselves.
        self.assertEqual(sorted(m['name'] for m in data['members']), ['jai', 'priya'])
        self.assertEqual(Decimal(data['total_outstanding']), Decimal('750.00'))
        # The group total, not only the bills they are named on.
        self.assertEqual(Decimal(data['total_spent']), Decimal('900.00'))
        you = [m for m in data['members'] if m['is_you']]
        self.assertEqual([m['name'] for m in you], ['jai'])

    def test_member_sees_every_group_expense(self):
        """Including a bill they are not personally split on."""
        Expense.objects.create(
            user=self.ashok, amount=Decimal('200.00'), transaction_type='expense',
            category=self.category, description='wifi', date='2026-01-02',
            group=self.group)
        self.auth(self.jai)
        rows = self.client.get(f'/api/expenses/groups/{self.group.id}/expenses/').data
        self.assertEqual(sorted(r['description'] for r in rows), ['rent', 'wifi'])


    def test_owner_balances_show_everyone(self):
        self.auth(self.ashok)
        data = self.client.get(f'/api/expenses/groups/{self.group.id}/balances/').data
        self.assertTrue(data['viewer_is_owner'])
        self.assertEqual(Decimal(data['total_outstanding']), Decimal('750.00'))
        self.assertEqual(Decimal(data['your_share_outstanding']), Decimal('0'))

    def test_member_cannot_restructure_someone_elses_group(self):
        self.auth(self.jai)
        self.assertEqual(self.client.post(
            f'/api/expenses/groups/{self.group.id}/add_members/',
            {'members': [{'name': 'gatecrasher'}]}, format='json').status_code, 403)
        self.assertEqual(self.client.post(
            f'/api/expenses/groups/{self.group.id}/remove_member/',
            {'person_id': self.jai_person.id}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(
            f'/api/expenses/groups/{self.group.id}/').status_code, 403)

    def test_stranger_sees_no_group(self):
        """Widening the ledger to members must not widen it past them."""
        outsider = User.objects.create_user('outsider', password='x')
        Token.objects.get_or_create(user=outsider)
        self.auth(outsider)
        self.assertEqual(self.client.get('/api/expenses/groups/').data, [])
        self.assertEqual(self.client.get(
            f'/api/expenses/groups/{self.group.id}/balances/').status_code, 404)
        self.assertEqual(self.client.get(
            f'/api/expenses/groups/{self.group.id}/expenses/').status_code, 404)

    def test_owner_sees_every_group_bill(self):
        """The owner's own view is unchanged by the widening."""
        other = Person.objects.create(user=self.ashok, name='sam')
        self.group.members.add(other)
        theirs = Expense.objects.create(
            user=self.ashok, amount=Decimal('300.00'), transaction_type='expense',
            category=self.category, description='sam only', date='2026-01-02',
            group=self.group)
        ExpenseSplit.objects.create(expense=theirs, person=other, amount=Decimal('150.00'))

        self.auth(self.ashok)
        listed = self.client.get(f'/api/expenses/groups/{self.group.id}/expenses/').data
        self.assertEqual(sorted(e['description'] for e in listed), ['rent', 'sam only'])

        # And the member now reads that same bill, which is the change.
        self.auth(self.jai)
        listed = self.client.get(f'/api/expenses/groups/{self.group.id}/expenses/').data
        self.assertEqual(sorted(e['description'] for e in listed), ['rent', 'sam only'])
