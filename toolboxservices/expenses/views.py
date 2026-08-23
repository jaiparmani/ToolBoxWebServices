from rest_framework import viewsets, status, filters, serializers
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Sum, Count, Q
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from datetime import datetime, timedelta
import django_filters

from .models import Expense, ExpenseCategory, ExpenseSplit, ExpenseTag, Person
from .serializers import (
    ExpenseSerializer, ExpenseCreateSerializer, ExpenseListSerializer,
    ExpenseCategorySerializer, ExpenseTagSerializer, ExpenseSummarySerializer,
    ExpenseSplitSerializer, PersonSerializer
)
from .services import (
    ExpenseParseError, ExpenseParseNotPossible, ExpenseParseRateLimited,
    parse_expense_batch, parse_expense_text, parse_search_query, parse_split_text,
    validate_supplied_items,
)


class StandardResultsSetPagination(PageNumberPagination):
    """Custom pagination for API responses"""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class ExpenseFilter(django_filters.FilterSet):
    """Filter for expenses"""
    date_from = django_filters.DateFilter(field_name='date', lookup_expr='gte')
    date_to = django_filters.DateFilter(field_name='date', lookup_expr='lte')
    amount_min = django_filters.NumberFilter(field_name='amount', lookup_expr='gte')
    amount_max = django_filters.NumberFilter(field_name='amount', lookup_expr='lte')
    category = django_filters.NumberFilter(field_name='category__id')
    tags = django_filters.CharFilter(method='filter_by_tags')
    transaction_type = django_filters.ChoiceFilter(choices=Expense.TRANSACTION_TYPE_CHOICES)
    search = django_filters.CharFilter(method='filter_by_search')

    class Meta:
        model = Expense
        fields = ['date', 'category', 'transaction_type', 'is_recurring']

    def filter_by_tags(self, queryset, name, value):
        """Filter by tag names or IDs"""
        tag_list = value.split(',')
        return queryset.filter(tags__id__in=tag_list).distinct()

    def filter_by_search(self, queryset, name, value):
        """Search in description and location"""
        return queryset.filter(
            Q(description__icontains=value) |
            Q(location__icontains=value) |
            Q(payment_method__icontains=value)
        )


class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    """ViewSet for ExpenseCategory CRUD operations"""
    queryset = ExpenseCategory.objects.filter(is_active=True)
    serializer_class = ExpenseCategorySerializer
    permission_classes = [AllowAny]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ['name', 'transaction_type', 'created_at']
    ordering = ['transaction_type', 'name']

    def get_queryset(self):
        """Filter categories by transaction type if specified"""
        queryset = super().get_queryset()
        transaction_type = self.request.query_params.get('type', None)
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type)
        return queryset

    def perform_create(self, serializer):
        """Set the user for the category (if needed for future user-specific categories)"""
        serializer.save()


class ExpenseTagViewSet(viewsets.ModelViewSet):
    """ViewSet for ExpenseTag CRUD operations"""
    serializer_class = ExpenseTagSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        """Only return tags for the specified user"""
        userid = self.request.GET.get('userid')
        if not userid:
            return ExpenseTag.objects.none()

        try:
            user_id = int(userid)
            return ExpenseTag.objects.filter(user_id=user_id, user__is_active=True)
        except ValueError:
            return ExpenseTag.objects.none()

    def perform_create(self, serializer):
        """Associate tag with specified user"""
        userid = self.request.GET.get('userid')
        if not userid:
            raise PermissionDenied("userid parameter is required.")

        try:
            user_id = int(userid)
            from django.contrib.auth.models import User
            user = User.objects.get(id=user_id, is_active=True)
            serializer.save(user=user)
        except (ValueError, ObjectDoesNotExist):
            raise PermissionDenied("Invalid userid parameter.")


class ExpenseViewSet(viewsets.ModelViewSet):
    """ViewSet for Expense CRUD operations with filtering and pagination"""
    permission_classes = [AllowAny]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter, filters.SearchFilter]
    filterset_class = ExpenseFilter
    ordering_fields = ['date', 'amount', 'created_at', 'updated_at']
    ordering = ['-date', '-created_at']
    search_fields = ['description', 'location', 'payment_method']

    def get_queryset(self):
        """Only return expenses for the specified user"""
        userid = self.request.GET.get('userid')
        if not userid:
            return Expense.objects.none()

        try:
            user_id = int(userid)
            return Expense.objects.filter(user_id=user_id, user__is_active=True)
        except ValueError:
            return Expense.objects.none()

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return ExpenseCreateSerializer
        elif self.action == 'list':
            return ExpenseListSerializer
        else:
            return ExpenseSerializer

    def perform_create(self, serializer):
        """Associate expense with specified user"""
        userid = self.request.GET.get('userid')
        if not userid:
            raise PermissionDenied("userid parameter is required.")

        try:
            user_id = int(userid)
            from django.contrib.auth.models import User
            user = User.objects.get(id=user_id, is_active=True)
            serializer.save(user=user)
        except (ValueError, ObjectDoesNotExist):
            raise PermissionDenied("Invalid userid parameter.")

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get expense summary statistics"""
        userid = request.GET.get('userid')
        if not userid:
            return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(userid)
            from django.contrib.auth.models import User
            user = User.objects.get(id=user_id, is_active=True)
        except (ValueError, ObjectDoesNotExist):
            return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)

        queryset = self.get_queryset()

        # Date range filter
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)

        # Calculate totals by transaction type
        totals = queryset.aggregate(
            total_expenses=Sum('amount', filter=Q(transaction_type='expense')),
            total_income=Sum('amount', filter=Q(transaction_type='income')),
            total_debt=Sum('amount', filter=Q(transaction_type='debt')),
            total_credit=Sum('amount', filter=Q(transaction_type='credit'))
        )

        # Calculate net balance
        income_total = totals.get('total_income') or 0
        credit_total = totals.get('total_credit') or 0
        expense_total = totals.get('total_expenses') or 0
        debt_total = totals.get('total_debt') or 0

        net_balance = (income_total + credit_total) - (expense_total + debt_total)

        # Category breakdown for expenses
        category_breakdown = {}
        expense_categories = queryset.filter(transaction_type='expense').values(
            'category__name'
        ).annotate(total=Sum('amount')).order_by('-total')

        for cat in expense_categories:
            category_breakdown[cat['category__name']] = cat['total']

        summary_data = {
            'total_expenses': totals.get('total_expenses') or 0,
            'total_income': totals.get('total_income') or 0,
            'total_debt': totals.get('total_debt') or 0,
            'total_credit': totals.get('total_credit') or 0,
            'net_balance': net_balance,
            'transaction_count': queryset.count(),
            'category_breakdown': category_breakdown
        }

        serializer = ExpenseSummarySerializer(summary_data)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent expenses (last 7 days)"""
        week_ago = timezone.now().date() - timedelta(days=7)
        recent_expenses = self.get_queryset().filter(date__gte=week_ago)
        serializer = self.get_serializer(recent_expenses, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def monthly_report(self, request):
        """Get monthly expense report"""
        userid = request.GET.get('userid')
        if not userid:
            return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(userid)
            from django.contrib.auth.models import User
            user = User.objects.get(id=user_id, is_active=True)
        except (ValueError, ObjectDoesNotExist):
            return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)
        year = request.query_params.get('year', timezone.now().year)
        month = request.query_params.get('month', timezone.now().month)

        monthly_expenses = self.get_queryset().filter(
            date__year=year,
            date__month=month
        )

        # Group by day
        daily_totals = monthly_expenses.values('date').annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('date')

        # Calculate monthly totals by category
        category_totals = monthly_expenses.values(
            'category__name', 'category__color'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        report_data = {
            'year': int(year),
            'month': int(month),
            'daily_totals': daily_totals,
            'category_totals': category_totals,
            'total_amount': monthly_expenses.aggregate(total=Sum('amount'))['total'] or 0,
            'total_count': monthly_expenses.count()
        }

        return Response(report_data)

    def _resolve_user(self, request):
        """Same ?userid= contract the rest of the API uses. Returns (user, error)."""
        userid = request.GET.get('userid')
        if not userid:
            return None, Response({'error': 'userid parameter is required'},
                                  status=status.HTTP_400_BAD_REQUEST)
        try:
            from django.contrib.auth.models import User
            return User.objects.get(id=int(userid), is_active=True), None
        except (ValueError, ObjectDoesNotExist):
            return None, Response({'error': 'Invalid userid parameter'},
                                  status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _known_tag_names(user):
        """The user's existing tags, so the model reuses them instead of coining near-duplicates."""
        return list(ExpenseTag.objects.filter(user=user).values_list('name', flat=True))

    @staticmethod
    def _resolve_tags(user, names):
        """Map model-suggested tag names onto the user's tags, creating what's missing.

        ExpenseTag.name is globally unique rather than unique per user, so a name
        another account already holds cannot be created here. Skip those rather
        than failing the save - a missing tag is a far smaller loss than a
        rejected expense.
        """
        resolved = []
        for name in names or []:
            tag = ExpenseTag.objects.filter(name__iexact=name, user=user).first()
            if not tag:
                if ExpenseTag.objects.filter(name__iexact=name).exists():
                    continue  # owned by another user
                tag = ExpenseTag.objects.create(name=name[:50], user=user)
            resolved.append(tag)
        return resolved

    @staticmethod
    def _resolve_category(parsed):
        """Find or create the category for a parsed item, honouring its type.

        The model's transaction_type wins: it read the note, whereas a category
        match is just a name collision. Reuse an existing category only when its
        type agrees, so "lent 200 to raj" can't be filed as an expense merely
        because some unrelated category shares the name the model picked.
        """
        transaction_type = parsed['transaction_type']
        name = parsed['category_name'][:100]
        category = ExpenseCategory.objects.filter(
            name__iexact=name, is_active=True, transaction_type=transaction_type
        ).first()
        if not category:
            # ExpenseCategory.name is unique, so a same-name category of a
            # different type forces a qualified name rather than an IntegrityError.
            if ExpenseCategory.objects.filter(name__iexact=name).exists():
                name = f"{name} ({transaction_type})"[:100]
            category, _ = ExpenseCategory.objects.get_or_create(
                name=name,
                defaults={'transaction_type': transaction_type},
            )
        return category

    @action(detail=False, methods=['post'])
    def quick_add(self, request):
        """Parse a free-text note (e.g. "20 aamras") with an LLM and save it as an expense."""
        user, error = self._resolve_user(request)
        if error:
            return error

        text = request.data.get('text', '')
        if not text or not text.strip():
            return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = parse_expense_text(text, known_tags=self._known_tag_names(user))
        except ExpenseParseNotPossible as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ExpenseParseRateLimited as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except ExpenseParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        expense = Expense.objects.create(
            user=user,
            amount=parsed['amount'],
            transaction_type=parsed['transaction_type'],
            category=self._resolve_category(parsed),
            description=parsed['description'],
            date=timezone.now().date(),
        )
        expense.tags.set(self._resolve_tags(user, parsed.get('tags')))

        serializer = ExpenseSerializer(expense)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def bulk_add(self, request):
        """Extract every transaction in a pasted log (e.g. an exported chat).

        Body: {"text": "...", "commit": false}
           or {"items": [...], "commit": true}

        Defaults to a dry run: the parsed rows come back for review and nothing
        is written. Bulk writes driven by model output shouldn't happen without
        a look first.

        To save, send back the reviewed rows as "items". That skips a second
        model call - which was both slow and wrong, since re-parsing could
        produce different rows than the ones the user actually approved.
        """
        user, error = self._resolve_user(request)
        if error:
            return error

        commit = str(request.data.get('commit', False)).lower() in ('true', '1', 'yes')
        supplied = request.data.get('items')
        text = request.data.get('text', '')

        if commit and supplied is not None:
            # Saving rows the user already reviewed - no model call needed.
            try:
                items = validate_supplied_items(supplied)
            except ExpenseParseNotPossible as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        else:
            if not text or not text.strip():
                return Response({'error': 'text is required'},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                items = parse_expense_batch(text, known_tags=self._known_tag_names(user))
            except ExpenseParseNotPossible as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            except ExpenseParseRateLimited as exc:
                return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            except ExpenseParseError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        if not items:
            return Response(
                {'committed': False, 'count': 0, 'items': [],
                 'detail': 'No transactions were found in that text.'},
                status=status.HTTP_200_OK,
            )

        if not commit:
            preview = [
                {
                    'amount': item['amount'],
                    'transaction_type': item['transaction_type'],
                    'description': item['description'],
                    'category_name': item['category_name'],
                    'tags': item.get('tags', []),
                    'date': item['date'].isoformat() if item['date'] else None,
                }
                for item in items
            ]
            return Response({'committed': False, 'count': len(preview), 'items': preview})

        today = timezone.now().date()
        created = []
        for item in items:
            expense = Expense.objects.create(
                user=user,
                amount=item['amount'],
                transaction_type=item['transaction_type'],
                category=self._resolve_category(item),
                description=item['description'],
                date=item['date'] or today,
            )
            expense.tags.set(self._resolve_tags(user, item.get('tags')))
            created.append(expense)

        serializer = ExpenseSerializer(created, many=True)
        return Response(
            {'committed': True, 'count': len(created), 'items': serializer.data},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['post'])
    def split_add(self, request):
        """Log a shared bill: "split 1200 dinner with raj and priya".

        The expense is recorded in full against the payer, and each other
        person's share is stored as a split they owe. The payer's own share is
        simply what's left, so their spending total stays truthful without a
        second record.
        """
        user, error = self._resolve_user(request)
        if error:
            return error

        text = request.data.get('text', '')
        if not text or not text.strip():
            return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)

        known_people = list(Person.objects.filter(user=user).values_list('name', flat=True))
        try:
            parsed = parse_split_text(text, known_people=known_people,
                                      known_tags=self._known_tag_names(user))
        except ExpenseParseNotPossible as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ExpenseParseRateLimited as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except ExpenseParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        expense = Expense.objects.create(
            user=user,
            amount=parsed['amount'],
            transaction_type='expense',
            category=self._resolve_category(parsed),
            description=parsed['description'],
            date=timezone.now().date(),
        )
        expense.tags.set(self._resolve_tags(user, parsed.get('tags')))

        splits = []
        for name, share in parsed['owed'].items():
            # Match an existing person case-insensitively so "raj" and "Raj"
            # stay one balance rather than two.
            person = Person.objects.filter(user=user, name__iexact=name).first()
            if not person:
                person = Person.objects.create(user=user, name=name)
            splits.append(ExpenseSplit.objects.create(
                expense=expense, person=person, amount=share))

        owed_total = sum(s.amount for s in splits)
        return Response({
            'expense': ExpenseSerializer(expense).data,
            'splits': ExpenseSplitSerializer(splits, many=True).data,
            'your_share': expense.amount - owed_total,
            'owed_to_you': owed_total,
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    def ask(self, request):
        """Answer a question about spending by filtering, not by generating prose.

        Body: {"question": "how much on food last month"}

        The model only chooses filter values; ExpenseFilter applies them. That
        keeps the arithmetic in the database - the totals are computed here, not
        written by a model that might get them wrong - and means a bad answer can
        only ever be a filter combination the user could have picked by hand.
        """
        user, error = self._resolve_user(request)
        if error:
            return error

        question = request.data.get('question', '')
        if not question or not question.strip():
            return Response({'error': 'question is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = parse_search_query(question)
        except ExpenseParseNotPossible as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ExpenseParseRateLimited as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except ExpenseParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        filters = parsed['filters']

        # A category name is friendlier for the model than an id; the filter wants the id.
        applied = dict(filters)
        category_name = applied.pop('category', None)
        queryset = self.get_queryset()
        if category_name:
            category = ExpenseCategory.objects.filter(name__iexact=category_name).first()
            if category:
                queryset = queryset.filter(category=category)
            else:
                # Unknown category: fall back to matching the text rather than
                # silently returning everything, which would look like an answer.
                applied['search'] = applied.get('search') or category_name

        queryset = ExpenseFilter(applied, queryset=queryset).qs

        totals = queryset.aggregate(total=Sum('amount'), count=Count('id'))
        serializer = ExpenseListSerializer(queryset.order_by('-date')[:50], many=True)
        return Response({
            'question': question.strip(),
            'interpretation': parsed['interpretation'],
            'filters': filters,
            'total': totals['total'] or 0,
            'count': totals['count'] or 0,
            'results': serializer.data,
        })

    @action(detail=True, methods=['post'])
    def add_tags(self, request, pk=None):
        """Add tags to an expense"""
        expense = self.get_object()
        tag_ids = request.data.get('tag_ids', [])

        if not tag_ids:
            return Response(
                {'error': 'tag_ids is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        userid = request.GET.get('userid')
        if not userid:
            return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(userid)
            tags = ExpenseTag.objects.filter(id__in=tag_ids, user_id=user_id, user__is_active=True)
            expense.tags.add(*tags)
        except ValueError:
            return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(expense)
        return Response(serializer.data)

    @action(detail=True, methods=['delete'])
    def remove_tags(self, request, pk=None):
        """Remove tags from an expense"""
        expense = self.get_object()
        tag_ids = request.data.get('tag_ids', [])

        if not tag_ids:
            return Response(
                {'error': 'tag_ids is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        userid = request.GET.get('userid')
        if not userid:
            return Response({'error': 'userid parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_id = int(userid)
            tags = ExpenseTag.objects.filter(id__in=tag_ids, user_id=user_id, user__is_active=True)
            expense.tags.remove(*tags)
        except ValueError:
            return Response({'error': 'Invalid userid parameter'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(expense)
        return Response(serializer.data)


def _resolve_split_user(request):
    """The ?userid= contract, shared by the split viewsets."""
    userid = request.GET.get('userid')
    if not userid:
        return None, Response({'error': 'userid parameter is required'},
                              status=status.HTTP_400_BAD_REQUEST)
    try:
        from django.contrib.auth.models import User
        return User.objects.get(id=int(userid), is_active=True), None
    except (ValueError, ObjectDoesNotExist):
        return None, Response({'error': 'Invalid userid parameter'},
                              status=status.HTTP_400_BAD_REQUEST)


class PersonViewSet(viewsets.ModelViewSet):
    """People the user splits expenses with."""
    serializer_class = PersonSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        userid = self.request.GET.get('userid')
        if not userid:
            return Person.objects.none()
        try:
            return Person.objects.filter(user_id=int(userid))
        except ValueError:
            return Person.objects.none()

    def perform_create(self, serializer):
        user, error = _resolve_split_user(self.request)
        if error:
            raise serializers.ValidationError({'userid': 'A valid userid is required.'})
        serializer.save(user=user)


class SplitViewSet(viewsets.ReadOnlyModelViewSet):
    """Shared expenses: what each person owes, and settling up."""
    serializer_class = ExpenseSplitSerializer
    permission_classes = [AllowAny]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        userid = self.request.GET.get('userid')
        if not userid:
            return ExpenseSplit.objects.none()
        try:
            queryset = ExpenseSplit.objects.filter(
                expense__user_id=int(userid)).select_related('person', 'expense')
        except ValueError:
            return ExpenseSplit.objects.none()
        if self.request.GET.get('settled') == 'false':
            queryset = queryset.filter(is_settled=False)
        person = self.request.GET.get('person')
        if person:
            queryset = queryset.filter(person_id=person)
        return queryset

    @action(detail=False, methods=['get'])
    def balances(self, request):
        """What each person currently owes, largest first.

        Only unsettled splits count. People who owe nothing are still listed so
        the UI can show them without a second call.
        """
        user, error = _resolve_split_user(request)
        if error:
            return error

        rows = (ExpenseSplit.objects
                .filter(expense__user=user, is_settled=False)
                .values('person_id', 'person__name')
                .annotate(owed=Sum('amount'), items=Count('id')))
        owed_by_person = {r['person_id']: r for r in rows}

        balances = []
        for person in Person.objects.filter(user=user):
            row = owed_by_person.get(person.id)
            balances.append({
                'person_id': person.id,
                'name': person.name,
                'owed': row['owed'] if row else 0,
                'unsettled_count': row['items'] if row else 0,
            })
        balances.sort(key=lambda b: (-(b['owed'] or 0), b['name']))

        return Response({
            'total_owed_to_you': sum((b['owed'] or 0) for b in balances),
            'balances': balances,
        })

    @action(detail=False, methods=['post'])
    def settle(self, request):
        """Mark someone's outstanding shares as paid.

        Body: {"person_id": 3}  (optionally {"split_ids": [1, 2]} for specific ones)

        Splits are marked rather than deleted, so who paid for what stays on
        record after the money changes hands.
        """
        user, error = _resolve_split_user(request)
        if error:
            return error

        queryset = ExpenseSplit.objects.filter(expense__user=user, is_settled=False)
        split_ids = request.data.get('split_ids')
        person_id = request.data.get('person_id')
        if split_ids:
            queryset = queryset.filter(id__in=split_ids)
        elif person_id:
            queryset = queryset.filter(person_id=person_id)
        else:
            return Response({'error': 'person_id or split_ids is required'},
                            status=status.HTTP_400_BAD_REQUEST)

        settled = list(queryset)
        if not settled:
            return Response({'error': 'Nothing outstanding to settle.'},
                            status=status.HTTP_400_BAD_REQUEST)

        total = sum(s.amount for s in settled)
        queryset.update(is_settled=True, settled_at=timezone.now())
        return Response({
            'settled_count': len(settled),
            'settled_total': total,
            'splits': ExpenseSplitSerializer(settled, many=True).data,
        })
