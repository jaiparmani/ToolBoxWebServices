"""Your true spending — only your own share of every bill.

A shared bill lives in SharedBill, separate from the personal Expense ledger.
Counting your side of one as spending means writing a real Expense linked
back to it - the payer's own copy (SharedBill.linked_expense) or a
counterparty's own copy of their share (ExpenseSplit.linked_expense) - rather
than a flag on someone else's row. That means every Expense row this module
sees IS real personal spending already; the only correction left is money
lent out on your own bills, which isn't yours to keep counting as spent.

``net_spending`` returns spending totals where every bill contributes only
the user's share:

    net = own expenses (full) - what others owe you on them (lent, not spent)

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
       'tag_totals': [{tag__id, tag__name, tag__color, total, count}],
       'daily_totals': [{date, total, count}]}``

    ``category_totals``/``tag_totals`` omit rows that net to nothing (e.g. a pure
    loan), and both totals are clamped at zero so a fully-lent bill never reads
    negative. An expense with several tags contributes its full share to each of
    them — tag totals are not a partition of spend the way categories are, so
    they will not sum back to the overall total, by design.
    """
    # Every Expense row here is real personal spending already - a shared bill
    # only produces one once its side opts in (SharedBill.linked_expense for
    # the payer, ExpenseSplit.linked_expense for whoever it's owed by), so
    # there's nothing left to exclude.
    own = Expense.objects.filter(user=user, transaction_type="expense")
    own = _apply_dates(own, date_from, date_to)

    # Money others owe you, on bills you're counting as your own spending —
    # subtract it (lent, not spent). `expense` here is the split's SharedBill;
    # `expense__linked_expense` reaches the payer's own Expense copy of it, if
    # they made one - a bill they never counted isn't in `own` either, so its
    # owed side must not be subtracted (or the total would go negative).
    owed = ExpenseSplit.objects.filter(
        expense__linked_expense__user=user,
        expense__linked_expense__transaction_type="expense",
    )
    owed = _apply_dates(owed, date_from, date_to, field="expense__date")

    cat_total, cat_meta, cat_count = {}, {}, {}
    tag_total, tag_meta, tag_count = {}, {}, {}
    day_total, day_count = {}, {}

    def add_cat(cid, name, color, amount, count):
        cat_total[cid] = cat_total.get(cid, ZERO) + amount
        cat_count[cid] = cat_count.get(cid, 0) + count
        cat_meta.setdefault(cid, (name, color))

    def add_tag(tid, name, color, amount, count):
        # An expense's tags are a many-to-many set — every tag on a bill gets
        # credited its full share, so a bill tagged both "Travel" and "Food"
        # contributes to both. Untagged rows (tid is None, from the LEFT OUTER
        # JOIN the M2M traversal produces) are dropped in the final assembly.
        if tid is None:
            return
        tag_total[tid] = tag_total.get(tid, ZERO) + amount
        tag_count[tid] = tag_count.get(tid, 0) + count
        tag_meta.setdefault(tid, (name, color))

    def add_day(d, amount, count):
        day_total[d] = day_total.get(d, ZERO) + amount
        day_count[d] = day_count.get(d, 0) + count

    # 1) own expenses at full amount
    for r in own.values("category__id", "category__name", "category__color").annotate(
        t=Sum("amount"), c=Count("id")
    ):
        add_cat(r["category__id"], r["category__name"], r["category__color"], r["t"] or ZERO, r["c"] or 0)
    for r in own.values("tags__id", "tags__name", "tags__color").annotate(
        t=Sum("amount"), c=Count("id")
    ):
        add_tag(r["tags__id"], r["tags__name"], r["tags__color"], r["t"] or ZERO, r["c"] or 0)
    for r in own.values("date").annotate(t=Sum("amount"), c=Count("id")):
        add_day(r["date"].isoformat(), r["t"] or ZERO, r["c"] or 0)

    # 2) subtract what others owe you (no count change — still your transaction).
    # Grouped by the linked Expense's own category/tags/date, not the bill's -
    # they're edited independently now, and `own` above is grouped the same way.
    for r in owed.values("expense__linked_expense__category__id").annotate(t=Sum("amount")):
        add_cat(r["expense__linked_expense__category__id"], None, None, -(r["t"] or ZERO), 0)
    for r in owed.values("expense__linked_expense__tags__id").annotate(t=Sum("amount")):
        add_tag(r["expense__linked_expense__tags__id"], None, None, -(r["t"] or ZERO), 0)
    for r in owed.values("expense__linked_expense__date").annotate(t=Sum("amount")):
        add_day(r["expense__linked_expense__date"].isoformat(), -(r["t"] or ZERO), 0)

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

    tag_totals = []
    for tid, tot in tag_total.items():
        if tot <= ZERO:
            continue
        name, color = tag_meta.get(tid, (None, None))
        tag_totals.append({
            "tag__id": tid,
            "tag__name": name,
            "tag__color": color,
            "total": float(tot),
            "count": tag_count.get(tid, 0),
        })
    tag_totals.sort(key=lambda x: -x["total"])

    daily_totals = [
        {"date": d, "total": float(day_total[d] if day_total[d] > ZERO else ZERO), "count": day_count.get(d, 0)}
        for d in sorted(day_total)
    ]

    own_total = own.aggregate(t=Sum("amount"))["t"] or ZERO
    owed_total = owed.aggregate(t=Sum("amount"))["t"] or ZERO
    total = own_total - owed_total
    if total < ZERO:
        total = ZERO

    count = own.count()

    return {
        "total": float(total),
        "count": count,
        "category_totals": category_totals,
        "tag_totals": tag_totals,
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
        expense__linked_expense__in=expense_qs, expense__linked_expense__transaction_type="expense"
    ).aggregate(t=Sum("amount"))["t"]
    return owed or ZERO


def expense_share_fields(expense):
    """(your_share, owed_to_you) for a single expense you own, as floats.

    ``owed_to_you`` is what your split participants owe on this bill;
    ``your_share`` is the remainder you actually spent. Zero/zero for a plain
    expense that was never a shared bill, or for a counterparty's own copy of
    their share (ExpenseSplit.linked_expense) - there's nothing owed on that,
    it's just yours.
    """
    bill = getattr(expense, 'shared_bill', None)
    owed = sum((s.amount for s in bill.splits.all()), ZERO) if bill else ZERO
    share = expense.amount - owed
    if share < ZERO:
        share = ZERO
    return float(share), float(owed)
