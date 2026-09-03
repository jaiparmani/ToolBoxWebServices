"""Your true spending — only your own share of every bill.

A split expense is stored in full against whoever paid, with ExpenseSplit rows
recording what each other person owes. That keeps the ledger simple but makes a
raw ``Sum('amount')`` overstate what you actually spent: money you laid out and
will get back (lending) counts as spending.

``net_spending`` corrects that. For a user and date window it returns spending
totals where every bill contributes only the user's share:

    net = own expenses (full)
        - what others owe you on them   (money lent out, not spent)
        + your share of bills others paid (your part of someone else's expense)

Splits count whether or not they are settled — your share is spent the moment
the bill happens, regardless of when the debt is squared up.

The shape mirrors what ``monthly_report`` already returns (``total``,
``count``, ``category_totals``, ``daily_totals``) so callers can drop it in.
"""
from decimal import Decimal

from django.db.models import Count, Sum

from .models import Expense, ExpenseSplit

ZERO = Decimal("0")


def _apply_dates(qs, date_from, date_to, field="date"):
    if date_from:
        qs = qs.filter(**{f"{field}__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field}__lte": date_to})
    return qs


def net_spending(user, date_from=None, date_to=None):
    """Return the user's share-only expense spending for the window.

    ``{'total': float, 'count': int,
       'category_totals': [{category__id, category__name, category__color, total, count}],
       'daily_totals': [{date, total, count}]}``

    ``category_totals`` omits categories that net to nothing (e.g. a pure loan),
    and both totals are clamped at zero so a fully-lent bill never reads negative.
    """
    own = Expense.objects.filter(user=user, transaction_type="expense")
    own = _apply_dates(own, date_from, date_to)

    # money others owe you, on expenses you paid — subtract (lent, not spent)
    owed = ExpenseSplit.objects.filter(
        expense__user=user, expense__transaction_type="expense"
    )
    owed = _apply_dates(owed, date_from, date_to, field="expense__date")

    # your share of bills someone else paid — add (spent, not on your ledger)
    mine = (
        ExpenseSplit.objects.filter(
            person__linked_user=user, expense__transaction_type="expense"
        ).exclude(expense__user=user)
    )
    mine = _apply_dates(mine, date_from, date_to, field="expense__date")

    cat_total, cat_meta, cat_count = {}, {}, {}
    day_total, day_count = {}, {}

    def add_cat(cid, name, color, amount, count):
        cat_total[cid] = cat_total.get(cid, ZERO) + amount
        cat_count[cid] = cat_count.get(cid, 0) + count
        cat_meta.setdefault(cid, (name, color))

    def add_day(d, amount, count):
        day_total[d] = day_total.get(d, ZERO) + amount
        day_count[d] = day_count.get(d, 0) + count

    # 1) own expenses at full amount
    for r in own.values("category__id", "category__name", "category__color").annotate(
        t=Sum("amount"), c=Count("id")
    ):
        add_cat(r["category__id"], r["category__name"], r["category__color"], r["t"] or ZERO, r["c"] or 0)
    for r in own.values("date").annotate(t=Sum("amount"), c=Count("id")):
        add_day(r["date"].isoformat(), r["t"] or ZERO, r["c"] or 0)

    # 2) subtract what others owe you (no count change — still your transaction)
    for r in owed.values("expense__category__id").annotate(t=Sum("amount")):
        add_cat(r["expense__category__id"], None, None, -(r["t"] or ZERO), 0)
    for r in owed.values("expense__date").annotate(t=Sum("amount")):
        add_day(r["expense__date"].isoformat(), -(r["t"] or ZERO), 0)

    # 3) add your share of bills others paid
    for r in mine.values(
        "expense__category__id", "expense__category__name", "expense__category__color"
    ).annotate(t=Sum("amount"), c=Count("id")):
        add_cat(r["expense__category__id"], r["expense__category__name"], r["expense__category__color"], r["t"] or ZERO, r["c"] or 0)
    for r in mine.values("expense__date").annotate(t=Sum("amount"), c=Count("id")):
        add_day(r["expense__date"].isoformat(), r["t"] or ZERO, r["c"] or 0)

    category_totals = []
    for cid, tot in cat_total.items():
        if tot <= ZERO:
            continue
        name, color = cat_meta.get(cid, (None, None))
        category_totals.append({
            "category__id": cid,
            "category__name": name,
            "category__color": color,
            "total": float(tot),
            "count": cat_count.get(cid, 0),
        })
    category_totals.sort(key=lambda x: -x["total"])

    daily_totals = [
        {"date": d, "total": float(day_total[d] if day_total[d] > ZERO else ZERO), "count": day_count.get(d, 0)}
        for d in sorted(day_total)
    ]

    own_total = own.aggregate(t=Sum("amount"))["t"] or ZERO
    owed_total = owed.aggregate(t=Sum("amount"))["t"] or ZERO
    mine_total = mine.aggregate(t=Sum("amount"))["t"] or ZERO
    total = own_total - owed_total + mine_total
    if total < ZERO:
        total = ZERO

    count = own.count() + mine.values("expense_id").distinct().count()

    return {
        "total": float(total),
        "count": count,
        "category_totals": category_totals,
        "daily_totals": daily_totals,
    }


def owed_to_you_total(expense_qs):
    """What your split participants owe on the expenses in ``expense_qs``.

    This is money you laid out and will get back — lending, not spending. Netting
    it out of a raw ``Sum('amount')`` gives your true spend on a filtered set of
    expenses (the same rule ``net_spending`` applies over a date window, here
    applied to an arbitrary queryset so the ``ask``/search paths agree with it).

    Splits only exist on expense rows, so this returns zero for income/debt/credit
    filters and never over-subtracts.
    """
    owed = ExpenseSplit.objects.filter(
        expense__in=expense_qs, expense__transaction_type="expense"
    ).aggregate(t=Sum("amount"))["t"]
    return owed or ZERO


def expense_share_fields(expense):
    """(your_share, owed_to_you) for a single expense you own, as floats.

    ``owed_to_you`` is what your split participants owe on this bill;
    ``your_share`` is the remainder you actually spent.
    """
    owed = sum((s.amount for s in expense.splits.all()), ZERO)
    share = expense.amount - owed
    if share < ZERO:
        share = ZERO
    return float(share), float(owed)
