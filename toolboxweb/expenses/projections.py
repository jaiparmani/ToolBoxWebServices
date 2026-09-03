"""Turn what's recorded into what's likely to happen next.

No bank feed, no real balances - so every number here is derived from data the
app already holds, and each function returns the inputs it used alongside the
result. The brief is explicit: never hide the calculation, never present it as
advice. These are estimates, labelled as estimates.

  current_balance()  - net of every income and expense recorded, a proxy for
                       "money on hand" until real accounts exist.
  build_projection() - a day-by-day forward balance: recurring income and bills
                       placed on their dates, plus an average discretionary
                       drain from recent history.
  money_pulse()      - a plain-language status (calm / watchful / attention /
                       opportunity) with the figures behind it.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Expense, RecurringRule

TWO = Decimal('0.01')


def _f(value):
    return float(Decimal(str(value or 0)).quantize(TWO))


def current_balance(user):
    """Money-on-hand proxy: everything that came in, minus everything that went out.

    Credit/debt/repayment are deliberately left out - they're claims between
    people, not cash position; the splits ledger tracks those separately.
    """
    agg = Expense.objects.filter(user=user).aggregate(
        income=Sum('amount', filter=_type('income')),
        expense=Sum('amount', filter=_type('expense')),
    )
    return Decimal(str(agg['income'] or 0)) - Decimal(str(agg['expense'] or 0))


def _type(t):
    from django.db.models import Q
    return Q(transaction_type=t)


def daily_discretionary(user, lookback=30):
    """Average day-to-day spend, so projection drains at a realistic rate.

    Uses the last `lookback` days of ordinary expenses. Recurring bills are
    projected separately from their rules, so to avoid counting them twice we
    exclude days-of-spend that a rule already covers is overkill here; instead
    we simply average recorded expense and let recurring rules add the known
    fixed costs on top - a slight over-estimate, which errs toward caution.
    """
    since = timezone.now().date() - timedelta(days=lookback)
    total = (Expense.objects
             .filter(user=user, transaction_type='expense', date__gte=since)
             .aggregate(t=Sum('amount'))['t'] or 0)
    return Decimal(str(total)) / Decimal(lookback)


def discretionary_mix(user, lookback=30, top=6):
    """How the ordinary daily spend splits across categories, from recent history.

    The projection drains at an average daily rate; this says *where* that drain
    tends to go, so the Cash Flow River can show the composition of the everyday
    outflow, not just the fixed bills. Derived from the last `lookback` days of
    recorded expenses, top categories kept and the rest grouped as "Other".
    """
    since = timezone.now().date() - timedelta(days=lookback)
    rows = (Expense.objects
            .filter(user=user, transaction_type='expense', date__gte=since)
            .values('category__id', 'category__name', 'category__color')
            .annotate(total=Sum('amount'))
            .order_by('-total'))
    rows = list(rows)
    grand = sum((Decimal(str(r['total'] or 0)) for r in rows), Decimal('0'))
    if grand <= 0:
        return []
    mix = []
    kept = rows[:top]
    for r in kept:
        amt = Decimal(str(r['total'] or 0))
        mix.append({
            'category_id': r['category__id'],
            'category': r['category__name'] or 'Uncategorised',
            'color': r['category__color'],
            'amount': _f(amt),
            'share': _f(amt / grand),
        })
    if len(rows) > top:
        rest = sum((Decimal(str(r['total'] or 0)) for r in rows[top:]), Decimal('0'))
        if rest > 0:
            mix.append({'category_id': None, 'category': 'Other', 'color': None,
                        'amount': _f(rest), 'share': _f(rest / grand)})
    return mix


def build_projection(user, days=30):
    """A forward balance series with the events that move it.

    Returns a dict the Cash Flow River renders directly: a per-day list of
    {date, balance, events:[...]}, plus totals and the derived safe-to-spend.
    """
    today = timezone.now().date()
    end = today + timedelta(days=days)
    balance = current_balance(user)
    discretionary = daily_discretionary(user)

    rules = list(RecurringRule.objects.filter(user=user, is_active=True).select_related('category'))
    # Pre-compute every rule occurrence in the window, bucketed by date.
    events_by_day = {}
    for rule in rules:
        for d in rule.occurrences(today + timedelta(days=1), end):
            events_by_day.setdefault(d, []).append({
                'type': rule.transaction_type,
                'amount': _f(rule.amount),
                'signed': _f(rule.signed_amount),
                'description': rule.description,
                'category': rule.category.name if rule.category else None,
                'source': 'recurring',
                'rule_id': rule.id,
            })

    series = [{'date': today.isoformat(), 'balance': _f(balance), 'events': [],
               'inflow': 0.0, 'outflow': 0.0, 'is_today': True}]
    upcoming_income = Decimal('0')
    upcoming_bills = Decimal('0')
    next_income_date = None
    low_point = balance
    low_point_date = today.isoformat()

    for i in range(1, days + 1):
        day = today + timedelta(days=i)
        day_events = events_by_day.get(day, [])
        # Per-day flow decomposition, so the river can draw money coming in
        # (income events) against money going out (bills + the ordinary drain).
        day_inflow = Decimal('0')
        day_outflow = Decimal('0')
        for ev in day_events:
            balance += Decimal(str(ev['signed']))
            if ev['type'] == 'income':
                day_inflow += Decimal(str(ev['amount']))
                upcoming_income += Decimal(str(ev['amount']))
                if next_income_date is None:
                    next_income_date = day.isoformat()
            else:
                day_outflow += Decimal(str(ev['amount']))
                upcoming_bills += Decimal(str(ev['amount']))
        # ordinary daily drain counts as outflow too
        balance -= discretionary
        day_outflow += discretionary
        if balance < low_point:
            low_point = balance
            low_point_date = day.isoformat()
        series.append({
            'date': day.isoformat(),
            'balance': _f(balance),
            'events': day_events,
            'inflow': _f(day_inflow),
            'outflow': _f(day_outflow),
            'is_today': False,
        })

    # Safe-to-spend today: keep the projected low point above a small buffer.
    # (projected discretionary already counted, so this is headroom on top.)
    buffer = Decimal('0')
    safe_today = max(_f(low_point - buffer), 0.0)
    runway_days = _runway(current_balance(user), discretionary, rules, today, days)

    return {
        'as_of': today.isoformat(),
        'current_balance': _f(current_balance(user)),
        'daily_discretionary': _f(discretionary),
        'horizon_days': days,
        'projected_end_balance': series[-1]['balance'],
        'projected_low': {'balance': _f(low_point), 'date': low_point_date},
        'upcoming_income': _f(upcoming_income),
        'upcoming_bills': _f(upcoming_bills),
        'next_income_date': next_income_date,
        'safe_to_spend_today': safe_today,
        'runway_days': runway_days,
        'discretionary_mix': discretionary_mix(user),
        'series': series,
    }


def _runway(balance, discretionary, rules, today, cap_days):
    """Days until the projected balance would hit zero, capped at cap_days.

    None means it never dips to zero inside the window - healthy.
    """
    b = Decimal(str(balance))
    events = {}
    for rule in rules:
        for d in rule.occurrences(today + timedelta(days=1), today + timedelta(days=cap_days)):
            events[d] = events.get(d, Decimal('0')) + rule.signed_amount
    for i in range(1, cap_days + 1):
        day = today + timedelta(days=i)
        b += events.get(day, Decimal('0'))
        b -= discretionary
        if b <= 0:
            return i
    return None


def money_pulse(user):
    """A single read on where the user stands, with the numbers behind it.

    Four states, chosen in priority order so the most important wins:
      attention   - balance projected to run out inside the horizon
      watchful    - spending is accelerating week on week
      opportunity - spending unusually low, or sizeable income incoming
      calm        - none of the above
    """
    today = timezone.now().date()
    proj = build_projection(user, days=30)

    last7 = _spend(user, today - timedelta(days=6), today)
    prior7 = _spend(user, today - timedelta(days=13), today - timedelta(days=7))
    accelerating = prior7 > 0 and last7 > prior7 * Decimal('1.3')
    quiet = prior7 > 0 and last7 < prior7 * Decimal('0.6')

    runway = proj['runway_days']
    status, headline, detail = 'calm', 'On track', 'Nothing needs your attention right now.'

    if runway is not None and runway <= 14:
        status = 'attention'
        headline = f'Runway is short - about {runway} day{"" if runway == 1 else "s"}'
        detail = (f"At your recent pace (₹{proj['daily_discretionary']:.0f}/day) and with the "
                  f"bills coming up, the projected balance dips to ₹{proj['projected_low']['balance']:.0f} "
                  f"by {proj['projected_low']['date']}.")
    elif accelerating:
        status = 'watchful'
        headline = 'Spending is picking up'
        detail = (f"You've spent ₹{_f(last7):.0f} in the last 7 days versus ₹{_f(prior7):.0f} the week "
                  f"before - about {int((last7/prior7 - 1) * 100)}% more.")
    elif quiet or proj['upcoming_income'] > proj['current_balance']:
        status = 'opportunity'
        if proj['upcoming_income'] > 0 and proj['next_income_date']:
            headline = 'Income on the way'
            detail = (f"₹{proj['upcoming_income']:.0f} is expected by {proj['next_income_date']}, "
                      f"and spending has been light lately.")
        else:
            headline = 'Quiet spending week'
            detail = (f"You've spent ₹{_f(last7):.0f} in the last 7 days, well below your usual - "
                      f"a good week to get ahead.")

    return {
        'status': status,
        'headline': headline,
        'detail': detail,
        'inputs': {
            'current_balance': proj['current_balance'],
            'daily_discretionary': proj['daily_discretionary'],
            'runway_days': runway,
            'last_7_days_spend': _f(last7),
            'prior_7_days_spend': _f(prior7),
            'upcoming_income': proj['upcoming_income'],
            'upcoming_bills': proj['upcoming_bills'],
            'next_income_date': proj['next_income_date'],
        },
    }


def _spend(user, start, end):
    return Decimal(str(Expense.objects.filter(
        user=user, transaction_type='expense', date__gte=start, date__lte=end
    ).aggregate(t=Sum('amount'))['t'] or 0))


def affordability(user, amount, on_date):
    """Can the user spend `amount` on `on_date` without going under?

    Simulates the spend against the same forward projection the river shows -
    subtracts the amount from every projected day on/after the date and checks
    whether the projected low stays non-negative. Every figure returned is real,
    derived from recorded activity; nothing is invented.
    """
    from datetime import date as _date
    today = timezone.now().date()
    amount = Decimal(str(amount))
    if isinstance(on_date, str):
        on_date = _date.fromisoformat(on_date)
    if on_date < today:
        on_date = today

    horizon = min(max(30, (on_date - today).days + 14), 120)
    proj = build_projection(user, days=horizon)
    series = proj['series']

    # The projected low once the spend lands on/after on_date.
    low_after, low_after_date = None, None
    for d in series:
        day = _date.fromisoformat(d['date'])
        bal = Decimal(str(d['balance'])) - (amount if day >= on_date else Decimal('0'))
        if low_after is None or bal < low_after:
            low_after, low_after_date = bal, d['date']

    affordable = low_after >= Decimal('0')
    return {
        'amount': _f(amount),
        'date': on_date.isoformat(),
        'is_today': on_date == today,
        'affordable': affordable,
        'safe_to_spend_today': proj['safe_to_spend_today'],
        'projected_low_before': proj['projected_low'],
        'projected_low_after': {'balance': _f(low_after), 'date': low_after_date},
        'headroom': _f(low_after),           # room left at the low point after the spend
        'daily_discretionary': proj['daily_discretionary'],
        'next_income_date': proj['next_income_date'],
        'runway_days': proj['runway_days'],
    }
