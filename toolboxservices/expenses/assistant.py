"""One agent, one brain.

The assistant is a thin intent router over the app's existing AI capabilities.
A single classification call decides what the user wants; the work itself is
done by the SAME functions the rest of the app already uses as tools - the
parse_* parsers, the generate_* insight writers, the category/tag resolvers.
Nothing here re-implements that logic.

Contract:
  run(user, message)  -> a typed response dict the frontend renders inline.
  commit(user, payload) -> writes a confirmed draft (expense / batch / split /
                           tags). Reads never write; writes always come back as
                           a preview first, so nothing is saved on a model's
                           say-so.

Every response that references money/health carries the real underlying figures
(the parsers/insight-context compute them from the user's data), and the whole
thing survives garbage model output exactly like call_json does, because it is
call_json underneath.
"""

from decimal import Decimal

from django.utils import timezone

from .services import (
    ExpenseParseError, ExpenseParseNotPossible, ExpenseParseRateLimited,
    _translate_errors, parse_expense_batch, parse_expense_text, parse_search_query,
    parse_split_text, compute_shares,
)
from .resolvers import known_tag_names, resolve_category, resolve_tags
from .models import Expense, ExpenseCategory, ExpenseSplit, Person
from llm.client import call_json, LLMError

# ── Router ───────────────────────────────────────────────────────────────────

INTENTS = ('add_expense', 'add_batch', 'add_split', 'search',
           'explain_spending', 'explain_health', 'auto_tag', 'chat')

ROUTER_PROMPT = (
    "You are the router for a personal-finance assistant. Read the user's message "
    "and choose exactly one intent, then write one short friendly sentence.\n"
    "\n"
    "Intents:\n"
    "  add_expense      - a single transaction to record: \"20 aamras\", \"spent 200 on "
    "chai\", \"got 5000 salary\", \"lent 300 to raj\". A leading number is a strong signal.\n"
    "  add_batch        - MANY transactions in one message, e.g. a pasted list or "
    "\"20 vada pav, 100 chai, 250 lunch\".\n"
    "  add_split        - a shared bill to split: \"split 1200 dinner with raj and mira\".\n"
    "  search           - a question about their recorded transactions: \"find everything "
    "over 500 last week\", \"how much on food this month\", \"show my income\".\n"
    "  explain_spending - asking for an opinion/review of their spending: \"did I overspend "
    "on food?\", \"how am I doing this month?\", \"review my spending\".\n"
    "  explain_health   - the same, about their health logs.\n"
    "  auto_tag         - asking to tag/label a transaction, or \"suggest tags\".\n"
    "  chat             - greetings, help, capabilities, anything else.\n"
    "\n"
    "The reply is your natural-language acknowledgement. Never state a specific "
    "number or total yourself - the app computes those.\n"
    "\n"
    "Respond with ONLY a JSON object: {\"intent\": \"<one of the above>\", \"reply\": \"...\"}."
)


def _validate_route(parsed):
    if not isinstance(parsed, dict):
        raise LLMError("Router reply was not an object.")
    intent = parsed.get('intent')
    if intent not in INTENTS:
        raise LLMError("Unknown intent.")
    reply = parsed.get('reply')
    if not isinstance(reply, str) or not reply.strip():
        reply = "On it."
    return {'intent': intent, 'reply': reply.strip()}


def route(message):
    messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": message},
    ]
    return _translate_errors(
        call_json, messages, validate=_validate_route, expect_key='intent',
        retry_instruction='Reply with ONLY {"intent": "...", "reply": "..."} - no prose.',
    )


# ── Tool dispatch (reads) ────────────────────────────────────────────────────

def _known_people(user):
    return list(Person.objects.filter(user=user).values_list('name', flat=True))


def _draft_from_parsed(p):
    return {
        'amount': float(p['amount']),
        'transaction_type': p['transaction_type'],
        'description': p['description'],
        'category_name': p['category_name'],
        'tags': p.get('tags') or [],
    }


def run(user, message):
    """Classify the message and hand it to the right tool. Returns a typed dict.

    Write intents (expense/batch/split) are saved immediately and the response
    includes the saved ids so the frontend can offer a delete/undo button. Read
    intents resolve inline. If a tool can't make sense of the message it
    degrades to a plain answer rather than erroring.
    """
    from .serializers import ExpenseSerializer

    message = (message or '').strip()
    if not message:
        raise ExpenseParseNotPossible("Say something and I'll help.")

    routed = route(message)
    intent, reply = routed['intent'], routed['reply']

    try:
        if intent == 'add_expense':
            p = parse_expense_text(message, known_tags=known_tag_names(user))
            draft = _draft_from_parsed(p)
            expense = _create_expense(user, draft)
            return {'type': 'expense_added', 'reply': reply,
                    'expense': ExpenseSerializer(expense).data}

        if intent == 'add_batch':
            items = parse_expense_batch(message, known_tags=known_tag_names(user))
            if not items:
                return {'type': 'answer', 'reply': "I couldn't find any transactions in that."}
            from .views import _coerce_date_value
            created = []
            for i in items:
                on = i.get('date')
                created.append(_create_expense(user, _draft_from_parsed(i),
                               on_date=_coerce_date_value(on) if on else None))
            return {'type': 'batch_added', 'reply': reply,
                    'expenses': [ExpenseSerializer(e).data for e in created],
                    'count': len(created)}

        if intent == 'add_split':
            p = parse_split_text(message, known_people=_known_people(user), known_tags=known_tag_names(user))
            owed = [{'name': n, 'amount': float(a)} for n, a in p['owed'].items()]
            your_share = float(Decimal(str(p['amount'])) - sum((Decimal(str(o['amount'])) for o in owed), Decimal('0')))
            split_data = {
                'amount': float(p['amount']), 'description': p['description'],
                'category_name': p['category_name'], 'split_with_me': p['split_with_me'],
                'owed': owed, 'your_share': your_share,
            }
            result = _create_split(user, split_data)
            return {'type': 'split_added', 'reply': reply, **result}

        if intent == 'search':
            return {'type': 'search', 'reply': reply, **_run_search(user, message)}

        if intent == 'explain_spending':
            return _run_insight(user, 'expense', reply)

        if intent == 'explain_health':
            return _run_insight(user, 'health', reply)

        if intent == 'auto_tag':
            return _run_auto_tag(user, message, reply)

    except (ExpenseParseNotPossible, ExpenseParseError):
        # A tool couldn't parse the message - fall back to a plain reply.
        return {'type': 'answer', 'reply': reply}

    return {'type': 'answer', 'reply': reply}


def _run_search(user, question):
    from .views import ExpenseFilter  # one-directional; views does not import us
    from .models import ExpenseCategory
    from .serializers import ExpenseListSerializer
    from django.db.models import Sum, Count

    parsed = parse_search_query(question)
    filters = parsed['filters']
    applied = dict(filters)
    category_name = applied.pop('category', None)
    qs = Expense.objects.filter(user=user)
    if category_name:
        category = ExpenseCategory.objects.filter(name__iexact=category_name).first()
        if category:
            qs = qs.filter(category=category)
        else:
            applied['search'] = applied.get('search') or category_name
    qs = ExpenseFilter(applied, queryset=qs).qs
    totals = qs.aggregate(total=Sum('amount'), count=Count('id'))
    gross_total = float(totals['total'] or 0)

    # Spending is your own share only: money others owe you on these bills is
    # lending, not spending. Net it out so the assistant agrees with the summary
    # / monthly_report views (see expenses.net_spending). Splits only exist on
    # expenses, so income/debt/credit questions are unaffected.
    from .net_spending import owed_to_you_total
    owed_to_you = float(owed_to_you_total(qs))
    net_total = gross_total - owed_to_you
    if net_total < 0:
        net_total = 0.0

    return {
        'interpretation': parsed['interpretation'],
        'total': net_total,
        'gross_total': gross_total,
        'owed_to_you': owed_to_you,
        'count': totals['count'] or 0,
        'results': ExpenseListSerializer(qs.order_by('-date')[:20], many=True).data,
    }


def spending_review(user):
    """The spending review the in-app AI produces for 'review my spending'.

    A thin public wrapper over the same insight path (explain_spending), so other
    surfaces — the Telegram bot's /review — get the identical analysis without
    reaching into a private helper or re-running the router. Returns the typed
    insight dict (or a plain 'answer' dict when there is nothing to analyse), and
    raises the same ExpenseParse* errors the router paths raise.
    """
    return _run_insight(user, 'expense', "Here's how your spending looks.")


def _run_insight(user, scope, reply):
    from insights.services import (
        generate_expense_insight, generate_health_insight,
        InsightNotPossible, InsightRateLimited, InsightGenerationError,
    )
    generator = generate_expense_insight if scope == 'expense' else generate_health_insight
    try:
        parsed, meta = generator(user, days=30)
    except InsightNotPossible as exc:
        return {'type': 'answer', 'reply': str(exc)}
    except InsightRateLimited as exc:
        raise ExpenseParseRateLimited(str(exc))
    except InsightGenerationError as exc:
        raise ExpenseParseError(str(exc))
    return {
        'type': 'insight', 'reply': reply, 'scope': scope,
        'headline': parsed['headline'], 'summary': parsed['summary'],
        'observations': parsed.get('observations', []),
        'concerns': parsed.get('concerns', []),
        'suggestions': parsed.get('suggestions', []),
        'entries_analysed': meta.get('entry_count'),
    }


def _run_auto_tag(user, message, reply):
    """Suggest tags for the user's most recent untagged expense (or, failing
    that, for whatever the message describes)."""
    latest = (Expense.objects.filter(user=user, transaction_type='expense')
              .exclude(tags__isnull=False).order_by('-date', '-created_at').first())
    if latest:
        p = parse_expense_text(latest.description, known_tags=known_tag_names(user))
        tags = p.get('tags') or []
        if not tags:
            return {'type': 'answer', 'reply': "Nothing obvious to tag your latest expense with."}
        return {'type': 'tag_suggestion', 'reply': reply, 'expense_id': latest.id,
                'description': latest.description, 'amount': float(latest.amount), 'tags': tags}
    return {'type': 'answer', 'reply': "Everything's already tagged — add an expense and I'll suggest tags for it."}


# ── Commit (writes) ──────────────────────────────────────────────────────────

def _create_expense(user, draft, on_date=None):
    expense = Expense.objects.create(
        user=user,
        amount=Decimal(str(draft['amount'])),
        transaction_type=draft.get('transaction_type', 'expense'),
        category=resolve_category(draft),
        description=draft.get('description', ''),
        date=on_date or timezone.now().date(),
    )
    expense.tags.set(resolve_tags(user, draft.get('tags')))
    return expense


def _create_split(user, split):
    """Write a shared bill from a confirmed split preview (names + owed amounts)."""
    from .views import match_account
    from .serializers import ExpenseSerializer, ExpenseSplitSerializer

    amount = Decimal(str(split['amount']))
    category = resolve_category({'transaction_type': 'expense',
                                 'category_name': split.get('category_name') or 'Shared'})
    expense = Expense.objects.create(
        user=user, amount=amount, transaction_type='expense',
        category=category, description=split.get('description', 'Shared expense'),
        date=timezone.now().date(),
    )
    splits = []
    for row in split.get('owed', []):
        name = str(row.get('name') or '').strip()[:100]
        share = Decimal(str(row.get('amount') or 0))
        if not name or share <= 0:
            continue
        person = Person.objects.filter(user=user, name__iexact=name).first()
        if not person:
            person = Person.objects.create(user=user, name=name, linked_user=match_account(name))
        splits.append(ExpenseSplit.objects.create(expense=expense, person=person, amount=share))
    return {'expense': ExpenseSerializer(expense).data,
            'splits': ExpenseSplitSerializer(splits, many=True).data}


def commit(user, payload):
    """Write a confirmed draft. Returns a small result dict per kind."""
    from .serializers import ExpenseSerializer

    kind = payload.get('commit')
    if kind == 'expense':
        expense = _create_expense(user, payload['draft'])
        return {'type': 'added', 'kind': 'expense', 'expense': ExpenseSerializer(expense).data}

    if kind == 'batch':
        created = []
        for draft in payload.get('drafts', []):
            on = draft.get('date')
            from .views import _coerce_date_value
            created.append(_create_expense(user, draft, on_date=_coerce_date_value(on) if on else None))
        return {'type': 'added', 'kind': 'batch', 'count': len(created)}

    if kind == 'split':
        return {'type': 'added', 'kind': 'split', **_create_split(user, payload['split'])}

    if kind == 'tags':
        expense = Expense.objects.filter(id=payload.get('expense_id'), user=user).first()
        if not expense:
            raise ExpenseParseNotPossible("That expense is gone.")
        expense.tags.add(*resolve_tags(user, payload.get('tags')))
        return {'type': 'added', 'kind': 'tags', 'expense': ExpenseSerializer(expense).data}

    raise ExpenseParseNotPossible("Nothing to confirm.")
