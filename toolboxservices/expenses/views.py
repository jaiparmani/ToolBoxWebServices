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
from decimal import Decimal, InvalidOperation
import django_filters

from .models import Expense, ExpenseCategory, ExpenseSplit, ExpenseTag, Person, SplitGroup, RecurringRule, Notification, notify
from .serializers import (
    ExpenseSerializer, ExpenseCreateSerializer, ExpenseListSerializer,
    ExpenseCategorySerializer, ExpenseTagSerializer, ExpenseSummarySerializer,
    ExpenseSplitSerializer, PersonSerializer, SplitGroupSerializer, RecurringRuleSerializer,
    CopilotCardSerializer
)
from .services import (
    compute_shares,
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
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        """Only the authenticated user's tags."""
        return ExpenseTag.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ExpenseViewSet(viewsets.ModelViewSet):
    """ViewSet for Expense CRUD operations with filtering and pagination"""
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter, filters.SearchFilter]
    filterset_class = ExpenseFilter
    ordering_fields = ['date', 'amount', 'created_at', 'updated_at']
    ordering = ['-date', '-created_at']
    search_fields = ['description', 'location', 'payment_method']

    def get_queryset(self):
        """Only the authenticated user's expenses."""
        return Expense.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return ExpenseCreateSerializer
        elif self.action == 'list':
            return ExpenseListSerializer
        else:
            return ExpenseSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get expense summary statistics for the authenticated user."""
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
    def notifications(self, request):
        """The caller's notification feed: recent items + unread count."""
        qs = Notification.objects.filter(user=request.user)
        unread = qs.filter(is_read=False).count()
        items = list(qs[:40])
        return Response({
            'unread_count': unread,
            'results': [{
                'id': n.id, 'kind': n.kind, 'title': n.title, 'body': n.body,
                'link': n.link, 'is_read': n.is_read, 'created_at': n.created_at.isoformat(),
            } for n in items],
        })

    @action(detail=False, methods=['post'], url_path='notifications/read')
    def notifications_read(self, request):
        """Mark notifications read — a list of `ids`, or all of them."""
        qs = Notification.objects.filter(user=request.user, is_read=False)
        ids = request.data.get('ids')
        if isinstance(ids, list) and ids:
            qs = qs.filter(id__in=ids)
        updated = qs.update(is_read=True)
        return Response({'marked': updated})

    @action(detail=False, methods=['get'])
    def monthly_report(self, request):
        """Get monthly expense report for the authenticated user."""
        user = request.user
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

        # Calculate monthly totals by category. category__id is included so the
        # client can drill from a category into its actual transactions.
        category_totals = monthly_expenses.values(
            'category__id', 'category__name', 'category__color'
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
        """The authenticated user. Kept as (user, error) so callers are unchanged;
        IsAuthenticated guarantees a real user, so the error is always None."""
        return request.user, None

    # Category/tag resolution lives in expenses.resolvers now, shared with the
    # assistant agent. These stay as thin delegates so existing callers work.
    @staticmethod
    def _known_tag_names(user):
        from .resolvers import known_tag_names
        return known_tag_names(user)

    @staticmethod
    def _resolve_tags(user, names):
        from .resolvers import resolve_tags
        return resolve_tags(user, names)

    @staticmethod
    def _resolve_category(parsed):
        from .resolvers import resolve_category
        return resolve_category(parsed)

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
    def create_split(self, request):
        """Create a shared bill from explicit fields - no model call.

        Body: {
          "amount": 1200, "description": "dinner", "category_id": 1,
          "date": "2026-08-23", "split_with_me": true,
          "participants": [{"person_id": 3}, {"name": "priya", "amount": 500}]
        }

        The LLM route is convenient but costs a request against a daily quota
        and can misread a sentence into a wrong balance. This is the path for
        when the numbers need to be exactly what you typed.
        """
        user, error = self._resolve_user(request)
        if error:
            return error

        data = request.data
        try:
            amount = Decimal(str(data.get('amount'))).quantize(Decimal('0.01'))
        except (TypeError, InvalidOperation):
            return Response({'error': 'A valid amount is required'},
                            status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({'error': 'Amount must be greater than zero'},
                            status=status.HTTP_400_BAD_REQUEST)

        description = str(data.get('description') or '').strip()
        if not description:
            return Response({'error': 'A description is required'},
                            status=status.HTTP_400_BAD_REQUEST)

        group = None
        if data.get('group_id'):
            group = SplitGroup.objects.filter(id=data['group_id'], owner=user).first()
            if not group:
                return Response({'error': 'Unknown group'}, status=status.HTTP_400_BAD_REQUEST)

        participants = data.get('participants') or []
        # Splitting within a group usually means splitting with everyone in it,
        # so the members stand in when no participants are named.
        if group and not participants:
            participants = [{'person_id': p.id} for p in group.members.all()]
        if not isinstance(participants, list) or not participants:
            return Response({'error': 'At least one person to split with is required'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Resolve each participant to a Person, creating by name when needed.
        resolved, explicit = [], {}
        for entry in participants:
            if not isinstance(entry, dict):
                return Response({'error': 'Each participant must be an object'},
                                status=status.HTTP_400_BAD_REQUEST)
            person = None
            if entry.get('person_id'):
                person = Person.objects.filter(user=user, id=entry['person_id']).first()
                if not person:
                    return Response({'error': f"Unknown person {entry['person_id']}"},
                                    status=status.HTTP_400_BAD_REQUEST)
            elif entry.get('user_id'):
                # Picked from the app's users: link by id rather than by name, so
                # the split reaches their panel without depending on spelling.
                from django.contrib.auth.models import User as AuthUser
                account = AuthUser.objects.filter(id=entry['user_id'], is_active=True).first()
                if not account:
                    return Response({'error': f"Unknown user {entry['user_id']}"},
                                    status=status.HTTP_400_BAD_REQUEST)
                if account == user:
                    return Response({'error': "You're already part of the split - "
                                              "use 'split with me' instead of adding yourself"},
                                    status=status.HTTP_400_BAD_REQUEST)
                person = Person.objects.filter(user=user, linked_user=account).first()
                if not person:
                    person = (Person.objects.filter(user=user, name__iexact=account.username)
                              .first())
                    if person:
                        person.linked_user = account
                        person.save(update_fields=['linked_user'])
                    else:
                        person = Person.objects.create(user=user, name=account.username,
                                                       linked_user=account)
            else:
                name = str(entry.get('name') or '').strip()[:100]
                if not name:
                    return Response({'error': 'Each participant needs a person_id or a name'},
                                    status=status.HTTP_400_BAD_REQUEST)
                person = Person.objects.filter(user=user, name__iexact=name).first()
                if not person:
                    person = Person.objects.create(user=user, name=name,
                                                   linked_user=match_account(name))
            if person in resolved:
                continue  # the same person listed twice is one share
            resolved.append(person)
            if entry.get('amount') not in (None, ''):
                try:
                    share = Decimal(str(entry['amount'])).quantize(Decimal('0.01'))
                except InvalidOperation:
                    return Response({'error': f'Invalid amount for {person.name}'},
                                    status=status.HTTP_400_BAD_REQUEST)
                if share <= 0:
                    return Response({'error': f'Amount for {person.name} must be above zero'},
                                    status=status.HTTP_400_BAD_REQUEST)
                explicit[person.name] = share

        if explicit and sum(explicit.values()) > amount:
            return Response(
                {'error': "The shares add up to more than the bill."},
                status=status.HTTP_400_BAD_REQUEST)

        split_with_me = bool(data.get('split_with_me', True))
        if not split_with_me and not explicit and not resolved:
            return Response({'error': 'Nobody to split with'},
                            status=status.HTTP_400_BAD_REQUEST)

        owed = compute_shares(amount, [p.name for p in resolved], split_with_me,
                              {k: v for k, v in explicit.items()} or None)

        category = None
        if data.get('category_id'):
            category = ExpenseCategory.objects.filter(id=data['category_id'], is_active=True).first()
        if not category:
            category = self._resolve_category(
                {'transaction_type': 'expense', 'category_name': data.get('category_name') or 'Shared'})

        expense_date = _coerce_date_value(data.get('date')) or timezone.now().date()
        expense = Expense.objects.create(
            user=user, amount=amount, transaction_type='expense',
            category=category, description=description, date=expense_date,
            group=group,
        )
        # Anyone split with inside a group belongs to it from then on, so the
        # membership list can't drift from who actually shares the bills.
        if group:
            group.members.add(*resolved)

        splits = []
        by_name = {p.name: p for p in resolved}
        for name, share in owed.items():
            person = by_name.get(name)
            if person:
                splits.append(ExpenseSplit.objects.create(
                    expense=expense, person=person, amount=share))

        owed_total = sum(s.amount for s in splits)

        # Notify: a record for you, and a heads-up in the feed of anyone you
        # split with who has an account here.
        who = ', '.join(s.person.name for s in splits) or 'someone'
        notify(expense.user, 'Split added',
               f"{expense.description} — ₹{expense.amount}. You're owed ₹{owed_total} from {who}.",
               kind='split', link='/splits')
        creator_name = (expense.user.get_full_name() or expense.user.username).strip()
        for s in splits:
            if s.person.linked_user and s.amount:
                notify(s.person.linked_user,
                       f"{creator_name} split a bill with you",
                       f"{expense.description} — you owe ₹{s.amount}.",
                       kind='split', link='/splits')

        return Response({
            'expense': ExpenseSerializer(expense).data,
            'splits': ExpenseSplitSerializer(splits, many=True).data,
            'your_share': expense.amount - owed_total,
            'owed_to_you': owed_total,
        }, status=status.HTTP_201_CREATED)

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

        group = None
        if request.data.get('group_id'):
            group = SplitGroup.objects.filter(id=request.data['group_id'], owner=user).first()
            if not group:
                return Response({'error': 'Unknown group'}, status=status.HTTP_400_BAD_REQUEST)

        # Inside a group, the members are the likely names - handing them to the
        # model keeps "split 900 with the flat" resolving to the right people.
        known_people = list(
            (group.members if group else Person.objects.filter(user=user))
            .values_list('name', flat=True))
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
            group=group,
        )
        expense.tags.set(self._resolve_tags(user, parsed.get('tags')))

        splits = []
        for name, share in parsed['owed'].items():
            # Match an existing person case-insensitively so "raj" and "Raj"
            # stay one balance rather than two.
            person = Person.objects.filter(user=user, name__iexact=name).first()
            if not person:
                # Link to an account with this username when there is one, so the
                # split appears in their panel too.
                person = Person.objects.create(user=user, name=name,
                                               linked_user=match_account(name))
            elif person.linked_user_id is None:
                # A person added before they had an account picks the link up now.
                account = match_account(person.name)
                if account:
                    person.linked_user = account
                    person.save(update_fields=['linked_user'])
            splits.append(ExpenseSplit.objects.create(
                expense=expense, person=person, amount=share))

        if group:
            group.members.add(*[s.person for s in splits])

        owed_total = sum(s.amount for s in splits)

        # Notify: a record for you, and a heads-up in the feed of anyone you
        # split with who has an account here.
        who = ', '.join(s.person.name for s in splits) or 'someone'
        notify(expense.user, 'Split added',
               f"{expense.description} — ₹{expense.amount}. You're owed ₹{owed_total} from {who}.",
               kind='split', link='/splits')
        creator_name = (expense.user.get_full_name() or expense.user.username).strip()
        for s in splits:
            if s.person.linked_user and s.amount:
                notify(s.person.linked_user,
                       f"{creator_name} split a bill with you",
                       f"{expense.description} — you owe ₹{s.amount}.",
                       kind='split', link='/splits')

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

        tags = ExpenseTag.objects.filter(id__in=tag_ids, user=request.user)
        expense.tags.add(*tags)

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

        tags = ExpenseTag.objects.filter(id__in=tag_ids, user=request.user)
        expense.tags.remove(*tags)

        serializer = self.get_serializer(expense)
        return Response(serializer.data)


def match_account(name):
    """Find the account a split-partner name refers to, or None.

    An exact username wins outright. A case-insensitive match is only used when
    it is unambiguous - this database holds both "Jai" and "jai", and guessing
    between them would attach someone's debts to the wrong account.
    """
    from django.contrib.auth.models import User
    exact = User.objects.filter(username=name, is_active=True).first()
    if exact:
        return exact
    candidates = list(User.objects.filter(username__iexact=name, is_active=True)[:2])
    return candidates[0] if len(candidates) == 1 else None


def _resolve_split_user(request):
    """The authenticated user, shared by the split viewsets. IsAuthenticated
    guarantees a real user, so the error is always None."""
    return request.user, None


class PersonViewSet(viewsets.ModelViewSet):
    """People the user splits expenses with."""
    serializer_class = PersonSerializer
    pagination_class = None

    def get_queryset(self):
        return Person.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        user, error = _resolve_split_user(self.request)
        if error:
            raise serializers.ValidationError({'userid': 'A valid userid is required.'})
        serializer.save(user=user)

    @action(detail=False, methods=['get'])
    def available_users(self, request):
        """Accounts you can split with, for a picker.

        Only id and username are returned - enough to choose somebody, and
        nothing about their money. A search term is required beyond the people
        already known to you, so this isn't a way to page through every account
        on the server.
        """
        user, error = _resolve_split_user(request)
        if error:
            return error

        from django.contrib.auth.models import User as AuthUser
        already = dict(Person.objects.filter(user=user, linked_user__isnull=False)
                       .values_list('linked_user_id', 'name'))

        search = (request.GET.get('search') or '').strip()
        accounts = AuthUser.objects.filter(is_active=True).exclude(id=user.id)
        if search:
            accounts = accounts.filter(username__icontains=search)
        else:
            # No search term: show only the people already split with, so the
            # full account list isn't handed out for the asking.
            accounts = accounts.filter(id__in=already.keys())

        return Response([
            {'user_id': a.id, 'username': a.username, 'already_added': a.id in already}
            for a in accounts.order_by('username')[:20]
        ])


class SplitViewSet(viewsets.ReadOnlyModelViewSet):
    """Shared expenses: what each person owes, and settling up."""
    serializer_class = ExpenseSplitSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        uid = self.request.user.id
        # Splits you are party to, whichever end you are on.
        queryset = ExpenseSplit.objects.filter(
            Q(expense__user_id=uid) | Q(person__linked_user_id=uid)
        ).select_related('person', 'expense')
        if self.request.GET.get('settled') == 'false':
            queryset = queryset.filter(is_settled=False)
        person = self.request.GET.get('person')
        if person:
            queryset = queryset.filter(person_id=person)
        return queryset

    @action(detail=False, methods=['get'])
    def balances(self, request):
        """Both directions: what people owe you, and what you owe them.

        The two lists come from the same ExpenseSplit rows read from opposite
        ends - a split is "owed to me" for whoever paid and "I owe" for the
        account the person is linked to - so a shared bill is never stored
        twice and the two sides cannot drift apart.
        """
        user, error = _resolve_split_user(request)
        if error:
            return error

        # Money coming back to you: splits on expenses you paid.
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
                'linked_username': person.linked_user.username if person.linked_user else None,
                'owed': row['owed'] if row else 0,
                'unsettled_count': row['items'] if row else 0,
            })
        balances.sort(key=lambda b: (-(b['owed'] or 0), b['name']))

        # Money you owe: splits pointing at you, on expenses somebody else paid.
        debts = (ExpenseSplit.objects
                 .filter(person__linked_user=user, is_settled=False)
                 .exclude(expense__user=user)
                 .values('expense__user_id', 'expense__user__username')
                 .annotate(owed=Sum('amount'), items=Count('id')))
        you_owe = [{
            'user_id': d['expense__user_id'],
            'name': d['expense__user__username'],
            'owed': d['owed'],
            'unsettled_count': d['items'],
        } for d in debts]
        you_owe.sort(key=lambda b: (-(b['owed'] or 0), b['name']))

        total_owed_to_you = sum((b['owed'] or 0) for b in balances)
        total_you_owe = sum((b['owed'] or 0) for b in you_owe)
        return Response({
            'total_owed_to_you': total_owed_to_you,
            'total_you_owe': total_you_owe,
            'net': total_owed_to_you - total_you_owe,
            'balances': balances,
            'you_owe': you_owe,
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

        # Either side can record that the money moved: the payer confirming it
        # arrived, or the debtor marking that they sent it.
        queryset = ExpenseSplit.objects.filter(
            Q(expense__user=user) | Q(person__linked_user=user), is_settled=False)
        split_ids = request.data.get('split_ids')
        person_id = request.data.get('person_id')
        owed_to_user_id = request.data.get('owed_to_user_id')
        if split_ids:
            queryset = queryset.filter(id__in=split_ids)
        elif person_id:
            queryset = queryset.filter(person_id=person_id)
        elif owed_to_user_id:
            # Settling a debt of yours: every split you owe that account.
            queryset = queryset.filter(person__linked_user=user,
                                       expense__user_id=owed_to_user_id)
        else:
            return Response({'error': 'person_id, owed_to_user_id or split_ids is required'},
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


def _coerce_date_value(value):
    """A client-supplied YYYY-MM-DD, or None when absent or unusable."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


class SplitGroupViewSet(viewsets.ModelViewSet):
    """Groups you split within - a flat, a trip, a regular table.

    A group is a filter over existing splits rather than a second ledger, so
    its balances are the same ExpenseSplit rows the person view uses, counted
    with the group applied. The two can't disagree.
    """
    serializer_class = SplitGroupSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = SplitGroup.objects.filter(owner=self.request.user)
        if self.request.GET.get('archived') != 'true':
            queryset = queryset.filter(is_archived=False)
        return queryset.prefetch_related('members')

    def perform_create(self, serializer):
        user, error = _resolve_split_user(self.request)
        if error:
            raise serializers.ValidationError({'userid': 'A valid userid is required.'})
        serializer.save(owner=user)

    @action(detail=True, methods=['post'])
    def add_members(self, request, pk=None):
        """Add people to a group, by person_id, user_id or plain name.

        Accepting a name means a group can be built before everyone involved
        has an account; accepting user_id links them properly when they do.
        """
        group = self.get_object()
        user = group.owner
        added = []
        for entry in request.data.get('members') or []:
            person = None
            if isinstance(entry, dict) and entry.get('person_id'):
                person = Person.objects.filter(user=user, id=entry['person_id']).first()
            elif isinstance(entry, dict) and entry.get('user_id'):
                from django.contrib.auth.models import User as AuthUser
                account = AuthUser.objects.filter(id=entry['user_id'], is_active=True).first()
                if account and account != user:
                    person = (Person.objects.filter(user=user, linked_user=account).first()
                              or Person.objects.create(user=user, name=account.username,
                                                       linked_user=account))
            else:
                name = str(entry.get('name') if isinstance(entry, dict) else entry).strip()[:100]
                if name:
                    person = (Person.objects.filter(user=user, name__iexact=name).first()
                              or Person.objects.create(user=user, name=name,
                                                       linked_user=match_account(name)))
            if person:
                group.members.add(person)
                added.append(person)

        if not added:
            return Response({'error': 'No valid members to add'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(group).data)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        """Take somebody out of the group.

        Their past splits stay: they really did owe that money, and deleting
        the history to tidy a membership list would quietly change balances.
        """
        group = self.get_object()
        person_id = request.data.get('person_id')
        person = group.members.filter(id=person_id).first()
        if not person:
            return Response({'error': 'That person is not in this group'},
                            status=status.HTTP_400_BAD_REQUEST)
        group.members.remove(person)
        return Response(self.get_serializer(group).data)

    @action(detail=True, methods=['get'])
    def balances(self, request, pk=None):
        """What each member owes for this group's expenses only."""
        group = self.get_object()

        rows = (ExpenseSplit.objects
                .filter(expense__group=group, is_settled=False)
                .values('person_id', 'person__name')
                .annotate(owed=Sum('amount'), items=Count('id')))
        owed = {r['person_id']: r for r in rows}

        members = []
        for person in group.members.all():
            row = owed.get(person.id)
            members.append({
                'person_id': person.id,
                'name': person.name,
                'linked_username': person.linked_user.username if person.linked_user else None,
                'owed': row['owed'] if row else 0,
                'unsettled_count': row['items'] if row else 0,
            })
        members.sort(key=lambda m: (-(m['owed'] or 0), m['name']))

        spend = group.expenses.aggregate(total=Sum('amount'), count=Count('id'))
        return Response({
            'group': self.get_serializer(group).data,
            'total_spent': spend['total'] or 0,
            'expense_count': spend['count'] or 0,
            'total_outstanding': sum((m['owed'] or 0) for m in members),
            'members': members,
        })

    @action(detail=True, methods=['get'])
    def expenses(self, request, pk=None):
        """This group's expenses, most recent first."""
        group = self.get_object()
        queryset = group.expenses.select_related('category').order_by('-date', '-created_at')[:50]
        return Response(ExpenseListSerializer(queryset, many=True).data)


class RecurringRuleViewSet(viewsets.ModelViewSet):
    """Recurring income and bills - the rules the projection layer runs on."""
    serializer_class = RecurringRuleSerializer
    pagination_class = None

    def get_queryset(self):
        return RecurringRule.objects.filter(user=self.request.user).select_related('category')

    def perform_create(self, serializer):
        user, error = _resolve_split_user(self.request)
        if error:
            raise serializers.ValidationError({'userid': 'A valid userid is required.'})
        serializer.save(user=user)


class MoneyViewSet(viewsets.ViewSet):
    """Read-only money intelligence: the forward projection and the pulse.

    Separate from expenses CRUD because it computes rather than stores - the
    seam the Cash Flow River and Money Pulse render, and where budgets/goals
    will hang later.
    """

    @action(detail=False, methods=['get'])
    def projection(self, request):
        user, error = _resolve_split_user(request)
        if error:
            return error
        try:
            days = max(7, min(int(request.GET.get('days', 30)), 120))
        except (TypeError, ValueError):
            days = 30
        from .projections import build_projection
        return Response(build_projection(user, days=days))

    @action(detail=False, methods=['get'])
    def pulse(self, request):
        user, error = _resolve_split_user(request)
        if error:
            return error
        from .projections import money_pulse
        return Response(money_pulse(user))

    @action(detail=False, methods=['post'])
    def afford(self, request):
        """"Can I afford a ₹200 dinner Friday?" — parsed, then computed against
        the projection. The model only reads the sentence; the maths (and the
        yes/no) come from the user's real forward balance."""
        user, error = _resolve_split_user(request)
        if error:
            return error
        question = request.data.get('question', '')
        if not question or not question.strip():
            return Response({'error': 'question is required'}, status=status.HTTP_400_BAD_REQUEST)

        from .services import parse_afford_query
        from .projections import affordability
        try:
            parsed = parse_afford_query(question)
        except ExpenseParseNotPossible as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ExpenseParseRateLimited as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except ExpenseParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        result = affordability(user, parsed['amount'], parsed['date'])
        result['question'] = question.strip()
        result['interpretation'] = parsed.get('interpretation') or ''
        return Response(result)


class CopilotViewSet(viewsets.ViewSet):
    """The autonomous copilot: proactive, actionable cards from the user's data.

    Listing refreshes lazily (regenerates from live data on read), so it works
    with no external scheduler; a management command can also run it on a
    schedule. Cards are dismissed or marked actioned - never edited by hand.
    """

    def list(self, request):
        from . import copilot
        cards = copilot.refresh(request.user)
        return Response(CopilotCardSerializer(cards, many=True).data)

    @action(detail=False, methods=['post'])
    def refresh(self, request):
        from . import copilot
        cards = copilot.refresh(request.user)
        return Response(CopilotCardSerializer(cards, many=True).data)

    def _get_card(self, request, pk):
        from .models import CopilotCard
        try:
            return CopilotCard.objects.get(pk=pk, user=request.user), None
        except CopilotCard.DoesNotExist:
            return None, Response({'error': 'Card not found.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        card, error = self._get_card(request, pk)
        if error:
            return error
        card.status = 'dismissed'
        card.save(update_fields=['status', 'updated_at'])
        return Response(CopilotCardSerializer(card).data)

    @action(detail=True, methods=['post'])
    def act(self, request, pk=None):
        """Mark a card actioned - the user followed its suggestion."""
        card, error = self._get_card(request, pk)
        if error:
            return error
        card.status = 'actioned'
        card.save(update_fields=['status', 'updated_at'])
        return Response(CopilotCardSerializer(card).data)
