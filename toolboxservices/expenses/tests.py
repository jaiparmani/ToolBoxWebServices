"""Splits, read from both ends.

The whole point of Person.linked_user is that one ExpenseSplit row is "owed to
me" for whoever paid and "I owe" for the account the person signs in with -
never two mirrored rows. These tests pin that down from three accounts at once:
the payer (ashok), the counterparty (jai) and a stranger (priya) who must see
nothing at all.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import (Expense, ExpenseCategory, ExpenseSplit, ExpenseTag, Notification,
                     Person, SplitGroup)


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


class SplitConsentTests(APITestCase):
    """A split someone else made is *shared*, not yours - until you say so.

    Ashok splitting a bill with jai used to reach into jai's spending totals the
    moment it was created: net_spending added his share, so his summary moved
    while his expense list showed nothing. Felt in the balance, invisible as an
    item. Consent now sits on the one shared row (include_in_expenses), written
    by the account that owes it and by nobody else.
    """

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

    def make_split(self, amount='1200', share=None):
        self.auth(self.ashok)
        participant = {'user_id': self.jai.id}
        if share is not None:
            participant['amount'] = share
        response = self.client.post('/api/expenses/expenses/create_split/', {
            'amount': amount, 'description': 'dinner',
            'category_id': self.category.id, 'participants': [participant],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return ExpenseSplit.objects.order_by('created_at').last()

    def summary_total(self, user):
        self.auth(user)
        response = self.client.get('/api/expenses/expenses/summary/')
        self.assertEqual(response.status_code, 200, response.data)
        return Decimal(str(response.data['total_expenses']))

    # ── the claim: it must not land on their books ─────────────────────────

    def test_a_split_does_not_become_an_expense_on_the_other_persons_books(self):
        self.make_split()

        # No row of his own - the bill is stored once, against whoever paid.
        self.assertFalse(Expense.objects.filter(user=self.jai).exists())
        # And nothing in his list.
        self.auth(self.jai)
        listed = self.client.get('/api/expenses/expenses/').data['results']
        self.assertEqual(listed, [])
        # The part that was actually wrong: his spending total stayed put too.
        self.assertEqual(self.summary_total(self.jai), Decimal('0'))

    def test_the_shared_item_is_visible_before_it_is_accepted(self):
        """Invisible-but-charged was the bug; visible-and-uncharged is the fix."""
        self.make_split()
        self.auth(self.jai)
        rows = self.client.get('/api/expenses/splits/?direction=you_owe').data['results']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['direction'], 'you_owe')
        self.assertEqual(Decimal(row['amount']), Decimal('600.00'))
        self.assertFalse(row['include_in_expenses'])
        self.assertTrue(row['can_include'])

    def test_the_other_person_can_opt_in_and_only_then_does_it_count(self):
        split = self.make_split()
        self.assertEqual(self.summary_total(self.jai), Decimal('0'))

        self.auth(self.jai)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'include_in_expenses': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['include_in_expenses'])

        self.assertEqual(self.summary_total(self.jai), Decimal('600.00'))
        # Still one row, read from both ends - opting in mirrored nothing.
        self.assertEqual(ExpenseSplit.objects.count(), 1)
        self.assertFalse(Expense.objects.filter(user=self.jai).exists())
        # The payer's own figures are untouched by the other side's choice.
        self.assertEqual(self.summary_total(self.ashok), Decimal('600.00'))

    def test_opting_back_out_takes_it_out_of_spending_again(self):
        split = self.make_split()
        self.auth(self.jai)
        self.client.patch(f'/api/expenses/splits/{split.id}/',
                          {'include_in_expenses': True}, format='json')
        self.assertEqual(self.summary_total(self.jai), Decimal('600.00'))
        self.auth(self.jai)
        self.client.patch(f'/api/expenses/splits/{split.id}/',
                          {'include_in_expenses': False}, format='json')
        self.assertEqual(self.summary_total(self.jai), Decimal('0'))

    def test_the_payer_cannot_answer_for_the_other_person(self):
        """Consent belongs to whoever owes the share, not to whoever billed it."""
        split = self.make_split()
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'include_in_expenses': True}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        split.refresh_from_db()
        self.assertFalse(split.include_in_expenses)

    def test_a_third_account_cannot_opt_a_split_in(self):
        split = self.make_split()
        self.auth(self.priya)
        self.assertEqual(
            self.client.patch(f'/api/expenses/splits/{split.id}/',
                              {'include_in_expenses': True}, format='json').status_code, 404)

    # ── partial settlement ─────────────────────────────────────────────────

    def test_a_partial_payment_leaves_the_right_remainder(self):
        split = self.make_split()  # jai owes 600
        self.auth(self.jai)
        response = self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '250',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Decimal(str(response.data['settled_total'])), Decimal('250.00'))
        self.assertEqual(Decimal(str(response.data['remaining'])), Decimal('350.00'))

        split.refresh_from_db()
        self.assertFalse(split.is_settled)
        self.assertEqual(split.settled_amount, Decimal('250.00'))
        self.assertEqual(split.outstanding, Decimal('350.00'))

    def test_both_sides_agree_on_what_is_left_after_a_part_payment(self):
        self.make_split()
        self.auth(self.jai)
        self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '250',
        }, format='json')

        self.auth(self.jai)
        mine = self.client.get('/api/expenses/splits/balances/').data
        self.assertEqual(Decimal(str(mine['total_you_owe'])), Decimal('350.00'))

        self.auth(self.ashok)
        theirs = self.client.get('/api/expenses/splits/balances/').data
        self.assertEqual(Decimal(str(theirs['total_owed_to_you'])), Decimal('350.00'))
        self.assertEqual(Decimal(str(theirs['balances'][0]['owed'])), Decimal('350.00'))

    def test_paying_the_remainder_closes_the_split(self):
        split = self.make_split()
        self.auth(self.jai)
        self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '250'}, format='json')
        self.auth(self.jai)
        response = self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '350'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        self.assertTrue(split.is_settled)
        self.assertEqual(split.settled_amount, Decimal('600.00'))
        self.assertEqual(split.outstanding, Decimal('0.00'))

    def test_a_payment_larger_than_the_debt_is_refused(self):
        split = self.make_split()
        self.auth(self.jai)
        response = self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '900'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        split.refresh_from_db()
        self.assertEqual(split.settled_amount, Decimal('0.00'))

    def test_a_payment_spills_across_bills_oldest_first(self):
        self.make_split(amount='400', share='400')
        self.make_split(amount='500', share='500')
        first, second = ExpenseSplit.objects.order_by('created_at')
        # Same day, so the tie is broken by creation order.
        self.auth(self.jai)
        response = self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '600'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_settled)
        self.assertEqual(first.settled_amount, Decimal('400.00'))
        self.assertFalse(second.is_settled)
        self.assertEqual(second.outstanding, Decimal('300.00'))
        self.assertEqual(Decimal(str(response.data['remaining'])), Decimal('300.00'))

    def test_a_settle_with_no_amount_still_clears_everything(self):
        """The old all-or-nothing call is unchanged."""
        split = self.make_split()
        self.auth(self.jai)
        response = self.client.post('/api/expenses/splits/settle/',
                                    {'owed_to_user_id': self.ashok.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        self.assertTrue(split.is_settled)
        self.assertEqual(split.settled_amount, Decimal('600.00'))

    def test_a_share_cannot_be_cut_below_what_was_already_paid(self):
        split = self.make_split()
        self.auth(self.jai)
        self.client.post('/api/expenses/splits/settle/', {
            'owed_to_user_id': self.ashok.id, 'amount': '250'}, format='json')
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'amount': '100'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        split.refresh_from_db()
        self.assertEqual(split.amount, Decimal('600.00'))

    def test_a_third_account_cannot_settle_someone_elses_split(self):
        split = self.make_split()
        self.auth(self.priya)
        response = self.client.post('/api/expenses/splits/settle/', {
            'split_ids': [split.id], 'amount': '100'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        split.refresh_from_db()
        self.assertEqual(split.settled_amount, Decimal('0.00'))


class SplitFullEditTests(APITestCase):
    """Editing a split reopens the whole bill, not just one number.

    Only the amount was writable, so a shared bill entered with the wrong
    description, date or category had no way back short of deleting it. The same
    fields the create dialog collects are now editable from either side; who is
    *on* the bill stays with the payer, because the Person rows are in their
    contact list and the other side has nobody to add.
    """

    def setUp(self):
        self.ashok = User.objects.create_user('ashok', password='x')
        self.jai = User.objects.create_user('jai', password='x')
        self.priya = User.objects.create_user('priya', password='x')
        for user in (self.ashok, self.jai, self.priya):
            Token.objects.get_or_create(user=user)
        self.category = ExpenseCategory.objects.create(
            name='Shared', transaction_type='expense')
        self.travel = ExpenseCategory.objects.create(
            name='Travel', transaction_type='expense')

    def auth(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.get(user=user).key}')

    def make_split(self, amount='1200'):
        self.auth(self.ashok)
        response = self.client.post('/api/expenses/expenses/create_split/', {
            'amount': amount, 'description': 'dinner',
            'category_id': self.category.id,
            'participants': [{'user_id': self.jai.id}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return ExpenseSplit.objects.get()

    def test_the_read_payload_carries_enough_to_reopen_the_dialog(self):
        split = self.make_split()
        self.auth(self.ashok)
        row = self.client.get(f'/api/expenses/splits/{split.id}/').data
        self.assertEqual(row['description'], 'dinner')
        self.assertEqual(row['category'], self.category.id)
        self.assertEqual(row['category_name'], 'Shared')
        self.assertEqual(row['date'], str(split.expense.date))
        self.assertEqual([p['name'] for p in row['participants']], ['jai'])

    def test_the_payer_can_change_the_whole_bill(self):
        split = self.make_split()
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/', {
            'description': 'flights', 'date': '2026-01-09',
            'category_id': self.travel.id, 'expense_amount': '2000',
            'amount': '900',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        expense = split.expense
        self.assertEqual(expense.description, 'flights')
        self.assertEqual(expense.date.isoformat(), '2026-01-09')
        self.assertEqual(expense.category_id, self.travel.id)
        self.assertEqual(expense.amount, Decimal('2000.00'))
        self.assertEqual(split.amount, Decimal('900.00'))

    def test_the_other_side_can_correct_the_bill_too(self):
        """The person being billed is usually the one who spots what's wrong."""
        split = self.make_split()
        self.auth(self.jai)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'description': 'dinner at moti'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        self.assertEqual(split.expense.description, 'dinner at moti')
        # ...and the payer is told, rather than finding out by accident.
        self.assertTrue(Notification.objects.filter(
            user=self.ashok, kind='split').exists())

    def test_the_payer_can_rewrite_who_is_on_the_bill(self):
        split = self.make_split()
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/', {
            'participants': [
                {'user_id': self.jai.id, 'amount': '400'},
                {'name': 'meera', 'amount': '300'},
            ],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        split.refresh_from_db()
        self.assertEqual(split.amount, Decimal('400.00'))
        shares = {s.person.name: s.amount for s in split.expense.splits.all()}
        self.assertEqual(shares, {'jai': Decimal('400.00'), 'meera': Decimal('300.00')})
        # The bill still covers the shares, so it is left alone.
        split.expense.refresh_from_db()
        self.assertEqual(split.expense.amount, Decimal('1200.00'))

    def test_dropping_someone_from_the_roster_removes_their_share(self):
        split = self.make_split()
        self.auth(self.ashok)
        self.client.patch(f'/api/expenses/splits/{split.id}/', {
            'participants': [
                {'user_id': self.jai.id, 'amount': '400'},
                {'name': 'meera', 'amount': '300'},
            ]}, format='json')
        response = self.client.patch(f'/api/expenses/splits/{split.id}/', {
            'participants': [{'user_id': self.jai.id, 'amount': '400'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [s.person.name for s in split.expense.splits.all()], ['jai'])

    def test_you_cannot_drop_the_share_you_are_editing(self):
        split = self.make_split()
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/', {
            'participants': [{'name': 'meera', 'amount': '300'}]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(ExpenseSplit.objects.filter(id=split.id).exists())

    def test_the_other_side_cannot_change_who_is_on_the_bill(self):
        split = self.make_split()
        self.auth(self.jai)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/', {
            'participants': [
                {'user_id': self.jai.id, 'amount': '400'},
                {'name': 'meera', 'amount': '300'},
            ]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(ExpenseSplit.objects.count(), 1)

    def test_a_third_account_gets_404_on_read_edit_and_delete(self):
        split = self.make_split()
        self.auth(self.priya)
        self.assertEqual(
            self.client.get(f'/api/expenses/splits/{split.id}/').status_code, 404)
        self.assertEqual(
            self.client.patch(f'/api/expenses/splits/{split.id}/',
                              {'description': 'mine now'}, format='json').status_code, 404)
        self.assertEqual(
            self.client.delete(f'/api/expenses/splits/{split.id}/').status_code, 404)
        split.refresh_from_db()
        self.assertEqual(split.expense.description, 'dinner')

    def test_an_unknown_category_is_rejected(self):
        split = self.make_split()
        self.auth(self.ashok)
        response = self.client.patch(f'/api/expenses/splits/{split.id}/',
                                     {'category_id': 99999}, format='json')
        self.assertEqual(response.status_code, 400, response.data)


class AssistantDateTests(APITestCase):
    """"spent 10 yesterday" has to land on yesterday.

    The parser already resolved relative dates; the value was dropped between
    parsing and writing, so a dated note silently became today's.
    """

    def setUp(self):
        self.user = User.objects.create_user('ashok', password='x')
        Token.objects.get_or_create(user=self.user)
        ExpenseCategory.objects.create(name='Food', transaction_type='expense')

    def _parsed(self, when):
        return {
            'amount': 10, 'transaction_type': 'expense', 'description': 'chai',
            'category_name': 'Food', 'tags': [], 'date': when,
        }

    def test_a_relative_date_survives_to_the_written_row(self):
        from .assistant import _create_expense, _draft_from_parsed
        yesterday = date.today() - timedelta(days=1)
        expense = _create_expense(self.user, _draft_from_parsed(self._parsed(yesterday)))
        self.assertEqual(expense.date, yesterday)

    def test_a_confirmed_draft_keeps_its_date_through_the_round_trip(self):
        """The draft goes to the client as JSON and comes back as a string."""
        from .assistant import _create_expense, _draft_from_parsed
        yesterday = date.today() - timedelta(days=1)
        draft = _draft_from_parsed(self._parsed(yesterday))
        draft['date'] = yesterday.isoformat()          # what the client sends back
        expense = _create_expense(self.user, draft)
        self.assertEqual(expense.date, yesterday)

    def test_a_note_with_no_date_still_lands_today(self):
        from .assistant import _create_expense, _draft_from_parsed
        expense = _create_expense(self.user, _draft_from_parsed(self._parsed(None)))
        self.assertEqual(expense.date, date.today())

    def test_an_unusable_date_falls_back_to_today_rather_than_failing(self):
        from .assistant import _create_expense, _draft_from_parsed
        draft = _draft_from_parsed(self._parsed(None))
        draft['date'] = 'not-a-date'
        expense = _create_expense(self.user, draft)
        self.assertEqual(expense.date, date.today())


class ModelCoinedLabelTests(APITestCase):
    """A parse may name a category or tag that doesn't exist yet.

    Letting it through is the point - a fixed list can't describe a life. The
    tests here pin the three things that keep that from turning into litter:
    an existing name is reused whatever its case, a name that isn't label-shaped
    never becomes a row, and the list stops growing at a ceiling.
    """

    def setUp(self):
        self.user = User.objects.create_user('ashok', password='x')
        Token.objects.get_or_create(user=self.user)

    def parsed(self, category_name, transaction_type='expense'):
        return {'amount': 10, 'transaction_type': transaction_type,
                'description': 'chai', 'category_name': category_name, 'tags': []}

    # ── Creating what's genuinely new ──────────────────────────────────────

    def test_a_brand_new_category_is_created_and_attached(self):
        from .resolvers import resolve_category
        category = resolve_category(self.parsed('Pet care'))
        self.assertEqual(category.name, 'Pet care')
        self.assertEqual(category.transaction_type, 'expense')

    def test_a_brand_new_tag_is_created_for_the_user(self):
        from .resolvers import resolve_tags
        tags = resolve_tags(self.user, ['vet'])
        self.assertEqual([t.name for t in tags], ['vet'])
        self.assertEqual(tags[0].user, self.user)

    def test_a_coined_category_reaches_the_saved_expense(self):
        """End to end through the write path the batch import uses."""
        from .views import ExpenseViewSet
        item = self.parsed('Pet care')
        item['tags'] = ['vet']
        expense = Expense.objects.create(
            user=self.user, amount=10, transaction_type='expense',
            category=ExpenseViewSet._resolve_category(item),
            description='vet visit', date=date.today())
        expense.tags.set(ExpenseViewSet._resolve_tags(self.user, item['tags']))
        self.assertEqual(expense.category.name, 'Pet care')
        self.assertEqual([t.name for t in expense.tags.all()], ['vet'])

    def test_a_lowercase_coinage_is_stored_with_a_capital(self):
        from .resolvers import resolve_category
        self.assertEqual(resolve_category(self.parsed('groceries')).name, 'Groceries')

    # ── Reusing what's already there ───────────────────────────────────────

    def test_a_different_case_reuses_the_existing_category(self):
        from .resolvers import resolve_category
        existing = ExpenseCategory.objects.create(name='Food', transaction_type='expense')
        for name in ('food', 'FOOD', '  Food  '):
            self.assertEqual(resolve_category(self.parsed(name)).id, existing.id)
        self.assertEqual(ExpenseCategory.objects.filter(name__iexact='food').count(), 1)

    def test_a_different_case_reuses_the_existing_tag(self):
        from .resolvers import resolve_tags
        existing = ExpenseTag.objects.create(name='chai', user=self.user)
        tags = resolve_tags(self.user, ['Chai'])
        self.assertEqual([t.id for t in tags], [existing.id])
        self.assertEqual(ExpenseTag.objects.filter(user=self.user).count(), 1)

    def test_the_same_name_repeated_in_one_list_makes_one_tag(self):
        from .resolvers import resolve_tags
        tags = resolve_tags(self.user, ['chai', 'Chai', ' chai '])
        self.assertEqual(len(tags), 1)
        self.assertEqual(ExpenseTag.objects.filter(user=self.user).count(), 1)

    def test_a_name_held_by_another_type_is_qualified_not_stolen(self):
        from .resolvers import resolve_category
        income = ExpenseCategory.objects.create(name='Salary', transaction_type='income')
        coined = resolve_category(self.parsed('Salary', transaction_type='expense'))
        self.assertNotEqual(coined.id, income.id)
        self.assertEqual(coined.transaction_type, 'expense')
        income.refresh_from_db()
        self.assertEqual(income.name, 'Salary')          # untouched

    def test_an_existing_category_is_never_renamed_or_retyped(self):
        from .resolvers import resolve_category
        existing = ExpenseCategory.objects.create(name='food', transaction_type='expense')
        resolve_category(self.parsed('Food'))
        existing.refresh_from_db()
        self.assertEqual(existing.name, 'food')
        self.assertEqual(existing.transaction_type, 'expense')

    # ── Refusing junk ──────────────────────────────────────────────────────

    def test_a_sentence_does_not_become_a_category(self):
        from .resolvers import resolve_category
        long_name = 'Dinner at that place with Raj and the others last friday'
        category = resolve_category(self.parsed(long_name))
        self.assertEqual(category.name, 'Other')
        self.assertFalse(ExpenseCategory.objects.filter(name=long_name).exists())

    def test_placeholders_and_punctuation_do_not_become_categories(self):
        from .resolvers import resolve_category
        for junk in ('N/A', 'unknown', 'Misc', '???', '   ', '123', '-'):
            self.assertEqual(resolve_category(self.parsed(junk)).name, 'Other',
                             f'{junk!r} should not become a category')
        # One shared bucket, not one per piece of junk.
        self.assertEqual(ExpenseCategory.objects.filter(transaction_type='expense').count(), 1)

    def test_the_fallback_bucket_is_per_transaction_type(self):
        from .resolvers import resolve_category
        self.assertEqual(resolve_category(self.parsed('N/A', 'income')).name, 'Other income')

    def test_junk_tags_are_skipped_rather_than_substituted(self):
        from .resolvers import resolve_tags
        tags = resolve_tags(self.user, ['n/a', '???', '', None, 42,
                                        'a whole sentence about the purchase', 'chai'])
        self.assertEqual([t.name for t in tags], ['chai'])
        self.assertEqual(ExpenseTag.objects.filter(user=self.user).count(), 1)

    def test_no_more_than_three_tags_are_attached(self):
        from .resolvers import resolve_tags
        tags = resolve_tags(self.user, ['a', 'b', 'c', 'd', 'e'])
        self.assertEqual(len(tags), 3)

    def test_a_tag_another_account_owns_is_skipped_not_stolen(self):
        from .resolvers import resolve_tags
        other = User.objects.create_user('priya', password='x')
        theirs = ExpenseTag.objects.create(name='chai', user=other)
        self.assertEqual(resolve_tags(self.user, ['chai']), [])
        theirs.refresh_from_db()
        self.assertEqual(theirs.user, other)

    def test_auto_creation_stops_at_the_ceiling(self):
        from . import resolvers
        for i in range(resolvers.MAX_AUTO_CATEGORIES_PER_TYPE):
            ExpenseCategory.objects.create(name=f'Cat {i}', transaction_type='expense')
        category = resolvers.resolve_category(self.parsed('Pet care'))
        self.assertEqual(category.name, 'Other')
        self.assertFalse(ExpenseCategory.objects.filter(name='Pet care').exists())

    def test_the_ceiling_does_not_block_reusing_an_existing_category(self):
        from . import resolvers
        for i in range(resolvers.MAX_AUTO_CATEGORIES_PER_TYPE):
            ExpenseCategory.objects.create(name=f'Cat {i}', transaction_type='expense')
        self.assertEqual(resolvers.resolve_category(self.parsed('Cat 3')).name, 'Cat 3')


class InlineLabelCreationTests(APITestCase):
    """The composer creates a category/tag over the same endpoints the Labels
    tab uses - a human typing a name is trusted, so none of the model-output
    guards apply here."""

    def setUp(self):
        self.user = User.objects.create_user('ashok', password='x')
        Token.objects.get_or_create(user=self.user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Token {Token.objects.get(user=self.user).key}')

    def test_a_category_created_inline_can_be_used_on_an_expense(self):
        created = self.client.post('/api/expenses/categories/', {
            'name': 'Pet care', 'color': '#7C5CFF', 'transaction_type': 'expense',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        saved = self.client.post('/api/expenses/expenses/', {
            'amount': '250.00', 'transaction_type': 'expense',
            'category_id': created.data['id'], 'description': 'vet visit',
            'date': date.today().isoformat(),
        }, format='json')
        self.assertEqual(saved.status_code, 201, saved.data)

    def test_an_income_expense_cannot_take_an_expense_category(self):
        """Why the composer creates the category with the sheet's own type."""
        created = self.client.post('/api/expenses/categories/', {
            'name': 'Pet care', 'transaction_type': 'expense'}, format='json')
        saved = self.client.post('/api/expenses/expenses/', {
            'amount': '250.00', 'transaction_type': 'income',
            'category_id': created.data['id'], 'description': 'refund',
            'date': date.today().isoformat(),
        }, format='json')
        self.assertEqual(saved.status_code, 400, saved.data)

    def test_a_duplicate_name_is_refused_so_the_client_must_reuse(self):
        ExpenseCategory.objects.create(name='Food', transaction_type='expense')
        response = self.client.post('/api/expenses/categories/', {
            'name': 'Food', 'transaction_type': 'expense'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_a_tag_created_inline_belongs_to_the_caller(self):
        response = self.client.post('/api/expenses/tags/', {
            'name': 'vet', 'color': '#7C5CFF'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ExpenseTag.objects.get(name='vet').user, self.user)
