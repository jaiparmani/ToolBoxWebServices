"""Map model-suggested category and tag names onto real rows.

Extracted so the write paths - the ExpenseViewSet and the assistant agent -
share one implementation instead of duplicating it.
"""

from .models import ExpenseCategory, ExpenseTag


def known_tag_names(user):
    """The user's existing tag names, so the model reuses them instead of
    coining near-duplicates."""
    return list(ExpenseTag.objects.filter(user=user).values_list('name', flat=True))


def resolve_tags(user, names):
    """Map suggested tag names onto the user's tags, creating what's missing.

    ExpenseTag.name is globally unique, so a name another account holds can't be
    created here - skip those rather than failing the save.
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


def resolve_category(parsed):
    """Find or create the category for a parsed item, honouring its type.

    The model's transaction_type wins; reuse an existing category only when its
    type agrees, so "lent 200 to raj" can't be filed as an expense merely
    because some unrelated category shares the name.
    """
    transaction_type = parsed['transaction_type']
    name = parsed['category_name'][:100]
    category = ExpenseCategory.objects.filter(
        name__iexact=name, is_active=True, transaction_type=transaction_type
    ).first()
    if not category:
        if ExpenseCategory.objects.filter(name__iexact=name).exists():
            name = f"{name} ({transaction_type})"[:100]
        category, _ = ExpenseCategory.objects.get_or_create(
            name=name, defaults={'transaction_type': transaction_type},
        )
    return category
