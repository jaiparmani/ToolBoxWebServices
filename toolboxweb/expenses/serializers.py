from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Expense, ExpenseCategory, ExpenseTag


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

    class Meta:
        model = ExpenseTag
        fields = ['id', 'name', 'color', 'user', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_name(self, value):
        """Ensure tag name is unique for the user"""
        user = self.context['request'].user
        if ExpenseTag.objects.filter(name=value, user=user).exists():
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