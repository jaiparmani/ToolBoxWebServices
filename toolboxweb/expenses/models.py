from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
import decimal


class ExpenseCategory(models.Model):
    """Model for expense categories like Food, Transport, Entertainment, etc."""

    TRANSACTION_TYPE_CHOICES = [
        ('expense', 'Expense'),
        ('income', 'Income'),
        ('credit', 'Credit'),
        ('debt', 'Debt'),
    ]

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#007bff', help_text='Hex color code for UI display')
    icon = models.CharField(max_length=50, blank=True, null=True, help_text='Icon class or name')
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES, default='expense')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['transaction_type', 'name']
        verbose_name_plural = 'Expense Categories'

    def __str__(self):
        return f"{self.get_transaction_type_display()}: {self.name}"


class ExpenseTag(models.Model):
    """Model for custom tags that can be applied to expenses"""

    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(max_length=7, default='#6c757d', help_text='Hex color code for UI display')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='expense_tags')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Expense Tags'

    def __str__(self):
        return self.name


class Expense(models.Model):
    """Model for individual expense records"""

    TRANSACTION_TYPE_CHOICES = [
        ('expense', 'Expense'),
        ('income', 'Income'),
        ('credit', 'Credit'),
        ('debt', 'Debt'),
        ('repayment', 'Repayment'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='expenses')
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(decimal.Decimal('0.01'))]
    )
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES, default='expense')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.CASCADE, related_name='expenses')
    description = models.TextField()
    date = models.DateField()
    tags = models.ManyToManyField(ExpenseTag, blank=True, related_name='expenses')

    # Debt/Repayment specific fields
    related_expense = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                      help_text='Link to original debt for repayments')
    lender_borrower = models.CharField(max_length=100, blank=True, null=True,
                                     help_text='Name of lender (for debts) or borrower (for credits)')

    # Optional fields
    receipt_image = models.ImageField(upload_to='expense_receipts/', blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    payment_method = models.CharField(max_length=50, blank=True, null=True)

    # Recurring transactions
    is_recurring = models.BooleanField(default=False)
    recurring_interval = models.CharField(max_length=20, blank=True, null=True)  # daily, weekly, monthly, yearly

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['user', 'date']),
            models.Index(fields=['user', 'category']),
            models.Index(fields=['user', 'transaction_type']),
            models.Index(fields=['date']),
            models.Index(fields=['transaction_type']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_transaction_type_display()} - {self.amount} - {self.description[:50]}"

    def save(self, *args, **kwargs):
        # Set category transaction type consistency
        if self.category and not self.transaction_type:
            self.transaction_type = self.category.transaction_type
        super().save(*args, **kwargs)

    @property
    def amount_display(self):
        """Return formatted amount with currency symbol"""
        return f"₹{self.amount}"

    @property
    def is_recent(self):
        """Check if expense is from last 7 days"""
        from django.utils import timezone
        week_ago = timezone.now().date() - timezone.timedelta(days=7)
        return self.date >= week_ago

    @property
    def is_debt_related(self):
        """Check if this is a debt or repayment transaction"""
        return self.transaction_type in ['debt', 'repayment']

    @property
    def balance_effect(self):
        """Return the effect on balance: positive for income/credit, negative for expense/debt"""
        if self.transaction_type in ['income', 'credit']:
            return self.amount
        elif self.transaction_type in ['expense', 'debt']:
            return -self.amount
        elif self.transaction_type == 'repayment':
            return self.amount  # Repayment reduces debt, so positive effect
        return decimal.Decimal('0')


class Person(models.Model):
    """Someone you split expenses with.

    Not a Django user - most people you share a bill with will never log in.
    The existing Expense.lender_borrower is free text, which cannot support
    splitting one bill several ways and turns every typo into a new person,
    so shared spending gets its own record instead.
    """

    name = models.CharField(max_length=100)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='split_people')

    # When this person also has an account, their side of the split shows up in
    # their own panel: the same ExpenseSplit rows are "owed to me" for the payer
    # and "I owe" for whoever is linked here, so nothing is recorded twice.
    # SET_NULL rather than CASCADE - deleting an account shouldn't erase the
    # history of what was split with them.
    linked_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='split_debts',
                                    help_text='The account this person signs in with, if any')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'People'
        constraints = [
            # Scoped to the owner, unlike ExpenseTag.name which is globally
            # unique and so cannot be shared between accounts.
            models.UniqueConstraint(fields=['user', 'name'], name='unique_person_per_user'),
        ]

    def __str__(self):
        return self.name


class ExpenseSplit(models.Model):
    """One person's share of one expense.

    The expense's owner paid the bill; each split is what somebody else owes
    them for it. Settling marks the split rather than deleting it, so the
    history of who paid for what survives.
    """

    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name='splits')
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='splits')
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(decimal.Decimal('0.01'))],
        help_text="What this person owes the payer for this expense")
    is_settled = models.BooleanField(default=False)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['expense', 'person'], name='unique_split_per_person'),
        ]
        indexes = [
            models.Index(fields=['person', 'is_settled']),
        ]

    def __str__(self):
        return f"{self.person.name} owes {self.amount} for {self.expense.description[:30]}"

    def settle(self):
        from django.utils import timezone as _tz
        self.is_settled = True
        self.settled_at = _tz.now()
        self.save(update_fields=['is_settled', 'settled_at'])
