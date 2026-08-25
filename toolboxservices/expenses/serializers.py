from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Expense, ExpenseCategory, ExpenseSplit, ExpenseTag, Person, SplitGroup, RecurringRule, CopilotCard


class ExpenseCategorySerializer(serializers.ModelSerializer):
    """Serializer for ExpenseCategory CRUD operations"""

    class Meta:
        model = ExpenseCategory
        fields = ['id', 'name', 'description', 'color', 'icon',
                 'transaction_type', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        """Ensure category name is unique"""
        if ExpenseCategory.objects.filter(name=value).exists():
            if self.instance and self.instance.name == value:
                return value
            raise serializers.ValidationError("A category with this name already exists.")
        return value


class ExpenseTagSerializer(serializers.ModelSerializer):
    """Serializer for ExpenseTag CRUD operations"""
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove user from required fields since it's set programmatically
        if 'user' in self.fields:
            self.fields['user'].required = False

    def create(self, validated_data):
        """Create tag with proper user assignment"""
        request = self.context.get('request')
        if request:
            user = request.user
            if not user or user.is_anonymous:
                raise serializers.ValidationError("User authentication required.")
            validated_data['user'] = user
        return super().create(validated_data)

    class Meta:
        model = ExpenseTag
        fields = ['id', 'name', 'color', 'user', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_name(self, value):
        """Ensure tag name is unique for the user"""
        request = self.context.get('request')
        if request:
            user = request.user
            if not user or user.is_anonymous:
                raise serializers.ValidationError("User authentication required.")
        else:
            user = None

        if user and ExpenseTag.objects.filter(name=value, user=user).exists():
            if self.instance and self.instance.name == value:
                return value
            raise serializers.ValidationError("You already have a tag with this name.")
        return value


class ExpenseListSerializer(serializers.ModelSerializer):
    """Serializer for listing expenses with summary data"""
    category = ExpenseCategorySerializer(read_only=True)
    tags = ExpenseTagSerializer(many=True, read_only=True)
    amount_display = serializers.CharField(read_only=True)
    is_recent = serializers.BooleanField(read_only=True)
    balance_effect = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Expense
        fields = ['id', 'amount', 'amount_display', 'transaction_type', 'category',
                 'description', 'date', 'tags', 'is_recent', 'balance_effect',
                 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ExpenseCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating expenses with category/tag selection"""
    category_id = serializers.IntegerField(write_only=True)
    tag_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        default=list
    )

    class Meta:
        model = Expense
        fields = ['id', 'amount', 'transaction_type', 'category_id', 'description',
                 'date', 'tag_ids', 'related_expense', 'lender_borrower',
                 'receipt_image', 'location', 'payment_method', 'is_recurring',
                 'recurring_interval', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_amount(self, value):
        """Ensure amount is positive"""
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate(self, data):
        """Custom validation for expense creation"""
        category_id = data.get('category_id')
        transaction_type = data.get('transaction_type')

        if not category_id:
            raise serializers.ValidationError({"category_id": "Category is required."})

        try:
            category = ExpenseCategory.objects.get(id=category_id)
        except ExpenseCategory.DoesNotExist:
            raise serializers.ValidationError({"category_id": "Invalid category selected."})

        # Validate transaction type consistency
        if transaction_type and category.transaction_type != transaction_type:
            raise serializers.ValidationError({
                "transaction_type": f"Transaction type must match category type ({category.get_transaction_type_display()})."
            })

        return data

    def create(self, validated_data):
        """Create expense with tags"""
        category_id = validated_data.pop('category_id')
        tag_ids = validated_data.pop('tag_ids', [])

        # Get the category
        category = ExpenseCategory.objects.get(id=category_id)

        # Create the expense
        expense = Expense.objects.create(
            category=category,
            **validated_data
        )

        # Add tags if provided
        if tag_ids:
            tags = ExpenseTag.objects.filter(id__in=tag_ids, user=expense.user)
            expense.tags.set(tags)

        return expense


class ExpenseSerializer(serializers.ModelSerializer):
    """Full serializer for Expense CRUD operations with nested data"""
    category = ExpenseCategorySerializer(read_only=True)
    tags = ExpenseTagSerializer(many=True, read_only=True)
    amount_display = serializers.CharField(read_only=True)
    is_recent = serializers.BooleanField(read_only=True)
    is_debt_related = serializers.BooleanField(read_only=True)
    balance_effect = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    # Write fields
    category_id = serializers.IntegerField(write_only=True, required=False)
    tag_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        default=list
    )

    class Meta:
        model = Expense
        fields = ['id', 'user', 'amount', 'amount_display', 'transaction_type',
                 'category', 'category_id', 'description', 'date', 'tags', 'tag_ids',
                 'related_expense', 'lender_borrower', 'receipt_image', 'location',
                 'payment_method', 'is_recurring', 'recurring_interval',
                 'is_recent', 'is_debt_related', 'balance_effect',
                 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']

    def validate(self, data):
        """Custom validation for expense updates"""
        category_id = data.get('category_id')
        transaction_type = data.get('transaction_type')

        if category_id:
            try:
                category = ExpenseCategory.objects.get(id=category_id)
                # Validate transaction type consistency
                if transaction_type and category.transaction_type != transaction_type:
                    raise serializers.ValidationError({
                        "transaction_type": f"Transaction type must match category type ({category.get_transaction_type_display()})."
                    })
            except ExpenseCategory.DoesNotExist:
                raise serializers.ValidationError({"category_id": "Invalid category selected."})

        return data

    def update(self, instance, validated_data):
        """Update expense with tags"""
        category_id = validated_data.pop('category_id', None)
        tag_ids = validated_data.pop('tag_ids', None)

        # Update category if provided
        if category_id:
            category = ExpenseCategory.objects.get(id=category_id)
            instance.category = category

        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        # Update tags if provided
        if tag_ids is not None:
            tags = ExpenseTag.objects.filter(id__in=tag_ids, user=instance.user)
            instance.tags.set(tags)

        return instance


class ExpenseSummarySerializer(serializers.Serializer):
    """Serializer for expense summary statistics"""
    total_expenses = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_income = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_debt = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_credit = serializers.DecimalField(max_digits=12, decimal_places=2)
    net_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    transaction_count = serializers.IntegerField()
    category_breakdown = serializers.DictField(child=serializers.DecimalField(max_digits=10, decimal_places=2))


class PersonSerializer(serializers.ModelSerializer):
    """Someone the user splits expenses with.

    linked_user is writable so a person added before they had an account can be
    attached to one later, which makes their side of existing splits appear in
    their own panel.
    """
    # default=None matters: without it, a person with no account hits a None
    # partway along 'linked_user.username', DRF raises SkipField, and the key
    # vanishes from that row - so the payload shape differed per person.
    linked_username = serializers.CharField(
        source='linked_user.username', read_only=True, default=None)

    class Meta:
        model = Person
        fields = ['id', 'name', 'linked_user', 'linked_username', 'created_at']
        read_only_fields = ['id', 'linked_username', 'created_at']


class ExpenseSplitSerializer(serializers.ModelSerializer):
    """One person's share of one expense, with enough of the expense to read it."""
    person_name = serializers.CharField(source='person.name', read_only=True)
    paid_by = serializers.CharField(source='expense.user.username', read_only=True)
    description = serializers.CharField(source='expense.description', read_only=True)
    date = serializers.DateField(source='expense.date', read_only=True)
    expense_total = serializers.DecimalField(
        source='expense.amount', max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = ExpenseSplit
        fields = ['id', 'expense', 'expense_total', 'person', 'person_name', 'paid_by',
                  'description', 'date', 'amount', 'is_settled', 'settled_at']
        read_only_fields = fields


class SplitGroupSerializer(serializers.ModelSerializer):
    """A group, with enough about its members to render a list without a second call."""
    members = PersonSerializer(many=True, read_only=True)
    member_ids = serializers.PrimaryKeyRelatedField(
        many=True, write_only=True, required=False,
        queryset=Person.objects.all(), source='members')
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = SplitGroup
        fields = ['id', 'name', 'emoji', 'members', 'member_ids', 'member_count',
                  'is_archived', 'created_at']
        read_only_fields = ['id', 'members', 'member_count', 'created_at']

    def get_member_count(self, obj):
        return obj.members.count()

    def validate_name(self, value):
        """Names are unique per owner. Without this the DB constraint fires as
        an uncaught IntegrityError - a 500 - the moment you reuse a name."""
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('A name is required.')
        request = self.context.get('request')
        owner_id = request.user.id if request and request.user.is_authenticated else None
        if owner_id:
            clash = SplitGroup.objects.filter(owner_id=owner_id, name__iexact=value)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError('You already have a group with that name.')
        return value

    def validate_member_ids(self, value):
        """Members have to be people the caller owns, or a group could be built
        out of somebody else's contacts."""
        request = self.context.get('request')
        owner_id = request.user.id if request and request.user.is_authenticated else None
        if owner_id:
            foreign = [p.name for p in value if str(p.user_id) != str(owner_id)]
            if foreign:
                raise serializers.ValidationError(
                    f"Not your contacts: {', '.join(foreign)}")
        return value


class RecurringRuleSerializer(serializers.ModelSerializer):
    """A recurring income or bill."""
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)
    next_date = serializers.SerializerMethodField()

    class Meta:
        model = RecurringRule
        fields = ['id', 'description', 'amount', 'transaction_type', 'category', 'category_name',
                  'cadence', 'interval', 'anchor_date', 'end_date', 'is_active',
                  'next_date', 'created_at']
        read_only_fields = ['id', 'category_name', 'next_date', 'created_at']

    def get_next_date(self, obj):
        from django.utils import timezone
        from datetime import timedelta
        today = timezone.now().date()
        upcoming = obj.occurrences(today, today + timedelta(days=400))
        return upcoming[0].isoformat() if upcoming else None

    def validate_transaction_type(self, value):
        if value not in ('expense', 'income'):
            raise serializers.ValidationError("Recurring rules are 'expense' or 'income' only.")
        return value


class CopilotCardSerializer(serializers.ModelSerializer):
    """A proactive copilot card. Read-only over the API - cards are generated by
    the detector, and only their status changes via the dismiss/act actions."""

    class Meta:
        model = CopilotCard
        fields = ['id', 'kind', 'severity', 'status', 'title', 'body',
                  'metric_value', 'metric_label', 'data',
                  'action_label', 'action_route', 'created_at', 'updated_at']
        read_only_fields = fields
