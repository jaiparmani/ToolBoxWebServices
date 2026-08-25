"""The copilot: turn the user's own data into proactive, actionable cards.

Every card is a real condition detected from recorded activity and the
projection - never an invented figure. Detectors return plain dicts; refresh()
upserts them into CopilotCard on a stable dedupe_key so a re-run updates a card
rather than duplicating it, respects a user's dismissal, and clears cards whose
condition no longer holds.

Detectors (each cites the number behind it):
  bill_overdraw        - an upcoming bill drives the projected balance negative
  category_spike       - a category's last-7-days spend is well above its usual
  subscription_renewed - a recurring charge just went out
  split_stale          - money owed to you has sat unsettled too long
  low_runway           - the projected balance runs out inside the window
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import CopilotCard, Expense, ExpenseSplit, RecurringRule
from .projections import build_projection

# Tunables - deliberately conservative so a card means something.
SPIKE_RATIO = Decimal('1.5')        # last 7d must exceed 1.5x the usual week
SPIKE_MIN_ABS = Decimal('500')      # ...and be at least this much, to skip noise
STALE_SPLIT_DAYS = 14
RENEWED_WINDOW_DAYS = 3
LOW_RUNWAY_DAYS = 7


def _f(v):
    return Decimal(str(v or 0))


def _card(kind, severity, title, body, dedupe_key,
          metric_value=None, metric_label='', data=None,
          action_label='', action_route=''):
    return {
        'kind': kind, 'severity': severity, 'title': title, 'body': body,
        'dedupe_key': dedupe_key,
        'metric_value': metric_value, 'metric_label': metric_label,
        'data': data or {}, 'action_label': action_label, 'action_route': action_route,
    }


def _rupees(v):
    return f"₹{_f(v):,.0f}"


# ── Detectors ────────────────────────────────────────────────────────────────

def _bill_overdraw(user, proj):
    """The first upcoming day the projected balance goes negative, and the bill
    on that day that tips it over."""
    series = proj.get('series') or []
    for day in series:
        if day.get('is_today'):
            continue
        if _f(day['balance']) < 0:
            bills = [e for e in day.get('events', []) if e.get('type') != 'income']
            date = day['date']
            if bills:
                biggest = max(bills, key=lambda e: e.get('amount', 0))
                title = f"{biggest['description']} may overdraw you"
                body = (f"{_rupees(biggest['amount'])} for {biggest['description']} on "
                        f"{date} pushes your projected balance to {_rupees(day['balance'])}.")
            else:
                title = "Your balance dips below zero"
                body = (f"At your recent pace, the projected balance falls to "
                        f"{_rupees(day['balance'])} by {date}.")
            return [_card(
                'bill_overdraw', 'urgent', title, body,
                dedupe_key=f"bill_overdraw:{date}",
                metric_value=_f(day['balance']), metric_label='Projected balance',
                data={'date': date, 'events': day.get('events', [])},
                action_label='See cash flow', action_route='/dashboard',
            )]
    return []


def _category_spike(user):
    today = timezone.now().date()
    last7 = (Expense.objects
             .filter(user=user, transaction_type='expense', date__gte=today - timedelta(days=6))
             .values('category__id', 'category__name')
             .annotate(total=Sum('amount')))
    prior = (Expense.objects
             .filter(user=user, transaction_type='expense',
                     date__gte=today - timedelta(days=34), date__lte=today - timedelta(days=7))
             .values('category__id')
             .annotate(total=Sum('amount')))
    prior_weekly = {r['category__id']: _f(r['total']) / 4 for r in prior}

    year, week, _ = today.isocalendar()
    cards = []
    for row in last7:
        cid = row['category__id']
        recent = _f(row['total'])
        usual = prior_weekly.get(cid, Decimal('0'))
        if usual > 0 and recent >= SPIKE_MIN_ABS and recent > usual * SPIKE_RATIO:
            pct = int((recent / usual - 1) * 100)
            name = row['category__name'] or 'Uncategorised'
            cards.append(_card(
                'category_spike', 'watch',
                title=f"{name} is up {pct}% this week",
                body=(f"You've spent {_rupees(recent)} on {name} in the last 7 days, "
                      f"versus about {_rupees(usual)} in a typical week."),
                dedupe_key=f"category_spike:{cid}:{year}-W{week}",
                metric_value=recent, metric_label=f"{name}, last 7 days",
                data={'recent': float(recent), 'usual': float(usual), 'category_id': cid},
                action_label='Review spending', action_route=f"/expense-tracker?category={cid}",
            ))
    return cards


def _subscription_renewed(user):
    today = timezone.now().date()
    cards = []
    rules = RecurringRule.objects.filter(user=user, is_active=True, transaction_type='expense')
    for rule in rules:
        occ = rule.occurrences(today - timedelta(days=RENEWED_WINDOW_DAYS), today)
        if occ:
            when = occ[-1]
            cards.append(_card(
                'subscription_renewed', 'info',
                title=f"{rule.description} renewed",
                body=f"{_rupees(rule.amount)} for {rule.description} went out on {when.isoformat()}.",
                dedupe_key=f"subscription_renewed:{rule.id}:{when.isoformat()}",
                metric_value=_f(rule.amount), metric_label=rule.description,
                data={'rule_id': rule.id, 'date': when.isoformat()},
                action_label='Manage recurring', action_route='/recurring',
            ))
    return cards


def _split_stale(user):
    today = timezone.now().date()
    cutoff = today - timedelta(days=STALE_SPLIT_DAYS)
    stale = (ExpenseSplit.objects
             .filter(expense__user=user, is_settled=False, expense__date__lte=cutoff)
             .select_related('person', 'expense'))
    by_person = {}
    for s in stale:
        p = by_person.setdefault(s.person_id, {'name': s.person.name, 'total': Decimal('0'), 'oldest': s.expense.date, 'count': 0})
        p['total'] += _f(s.amount)
        p['count'] += 1
        if s.expense.date < p['oldest']:
            p['oldest'] = s.expense.date
    cards = []
    for pid, p in by_person.items():
        age = (today - p['oldest']).days
        cards.append(_card(
            'split_stale', 'watch',
            title=f"{p['name']} has owed you for {age} days",
            body=(f"{_rupees(p['total'])} across {p['count']} unsettled "
                  f"{'bill' if p['count'] == 1 else 'bills'}, the oldest from {p['oldest'].isoformat()}."),
            dedupe_key=f"split_stale:{pid}",
            metric_value=p['total'], metric_label=f"{p['name']} owes you",
            data={'person_id': pid, 'age_days': age, 'count': p['count']},
            action_label='Settle up', action_route='/splits',
        ))
    return cards


def _low_runway(user, proj):
    r = proj.get('runway_days')
    if r is None or r > LOW_RUNWAY_DAYS:
        return []
    return [_card(
        'low_runway', 'urgent' if r <= 3 else 'watch',
        title=f"Runway is about {r} day{'' if r == 1 else 's'}",
        body=(f"At {_rupees(proj.get('daily_discretionary'))}/day and the bills ahead, the "
              f"projected balance dips to {_rupees(proj['projected_low']['balance'])} "
              f"by {proj['projected_low']['date']}."),
        dedupe_key="low_runway",
        metric_value=Decimal(str(r)), metric_label='Days of runway',
        data={'runway_days': r, 'projected_low': proj.get('projected_low')},
        action_label='See cash flow', action_route='/dashboard',
    )]


def detect(user):
    """Run every detector and return the candidate card dicts."""
    proj = build_projection(user, days=30)
    cards = []
    cards += _bill_overdraw(user, proj)
    cards += _low_runway(user, proj)
    cards += _category_spike(user)
    cards += _subscription_renewed(user)
    cards += _split_stale(user)
    return cards


def refresh(user):
    """Upsert the current candidates and clear cards whose condition has cleared.

    Dismissed cards are respected: a still-holding condition keeps its dismissed
    state (won't nag), and a cleared condition is removed regardless of state.
    Returns the live (non-dismissed) cards, most severe first.
    """
    candidates = detect(user)
    by_key = {c['dedupe_key']: c for c in candidates}
    existing = {c.dedupe_key: c for c in CopilotCard.objects.filter(user=user)}

    fields = ('kind', 'severity', 'title', 'body', 'metric_value', 'metric_label',
              'data', 'action_label', 'action_route')
    for key, c in by_key.items():
        card = existing.get(key)
        if card:
            if card.status == 'dismissed':
                continue  # still holds, but the user dismissed it - stay quiet
            for f in fields:
                setattr(card, f, c[f])
            card.save(update_fields=list(fields) + ['updated_at'])
        else:
            CopilotCard.objects.create(user=user, **c)

    # A condition that no longer holds shouldn't linger.
    for key, card in existing.items():
        if key not in by_key:
            card.delete()

    return live_cards(user)


SEVERITY_ORDER = {'urgent': 0, 'watch': 1, 'info': 2}


def live_cards(user):
    cards = list(CopilotCard.objects.filter(user=user).exclude(status='dismissed'))
    cards.sort(key=lambda c: (SEVERITY_ORDER.get(c.severity, 3), -c.id))
    return cards
