"""Map model-suggested category and tag names onto real rows.

Extracted so the write paths - the ExpenseViewSet and the assistant agent -
share one implementation instead of duplicating it.

The model is deliberately allowed to coin a category or tag that doesn't exist
yet: a fixed list can't describe a life, and forcing every note into the
nearest existing bucket is how "Food" quietly swallows a vet bill. But a name
here arrives from a language model, not from a person typing into a form, so it
is treated as a *proposal* and put through three gates before it becomes a row:

  1. reuse   - an existing name is matched case-insensitively and whitespace-
               insensitively, so "food", "Food" and "Food " are one category,
               never three.
  2. shape   - a category name is a label (a few words), not a sentence, a
               number, or "N/A". Anything that isn't label-shaped is refused.
  3. ceiling - a list nobody can read is as useless as no list. Past a generous
               cap, auto-creation stops and rows land in a visible fallback
               bucket the user can re-file, rather than growing the list
               without bound.

Nothing here ever renames or retypes a category that already exists; the only
write it can make is an insert.
"""

import logging
import re

from .models import ExpenseCategory, ExpenseTag
from .services import MAX_TAGS_PER_ITEM

logger = logging.getLogger(__name__)

# A category is structural: few, shared by every chart on the dashboard, and
# (today) global rather than per-user, so one account's bad parse would show up
# in everyone's list. It gets the tighter gate.
MAX_CATEGORY_NAME_LENGTH = 40
MAX_CATEGORY_WORDS = 4

# A tag is cheap and disposable - many per user, no structural meaning - so the
# gate only has to keep out sentences and junk.
MAX_TAG_NAME_LENGTH = 32
MAX_TAG_WORDS = 3

# Past this many active categories of one kind, stop minting more from model
# output. Chosen well above any plausible hand-made list (the app ships with a
# dozen) so it only ever fires on runaway generation.
MAX_AUTO_CATEGORIES_PER_TYPE = 60

# Names that carry no meaning. Letting these through mints "N/A", "Unknown" and
# "Misc" as sibling categories; routing them to one bucket keeps the list honest.
_PLACEHOLDER_NAMES = {
    'na', 'n/a', 'none', 'null', 'nil', 'nan', 'unknown', 'unspecified',
    'undefined', 'other', 'others', 'misc', 'miscellaneous', 'general',
    'uncategorised', 'uncategorized', 'category', 'expense', 'income', 'tag',
    'tbd', 'todo', 'test',
}

# Where an unusable name lands. A real, visible row - the user can see the
# expense was filed as "Other" and move it - rather than a silent mis-file into
# whatever category happened to sort first.
_FALLBACK_CATEGORY_NAMES = {
    'expense': 'Other',
    'income': 'Other income',
    'debt': 'Other debt',
    'credit': 'Other credit',
}

# Decoration models like to wrap a name in. Stripped from the ends only, so
# "E-commerce" and "Rent (flat)" survive intact.
_EDGE_JUNK = ' \t\r\n#"\'`*_-.,;:!?/\\|[](){}'

_HAS_LETTER = re.compile(r'[^\W\d_]', re.UNICODE)


def normalise_label(name, max_length, max_words):
    """A model-proposed label, cleaned up - or None when it isn't a label.

    Returns the name with runs of whitespace collapsed and edge decoration
    removed. None means "this is not something to create a row for": empty,
    a sentence, digits or punctuation only, or a placeholder word.
    """
    if not isinstance(name, str):
        return None
    cleaned = re.sub(r'\s+', ' ', name).strip(_EDGE_JUNK).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length or len(cleaned.split(' ')) > max_words:
        return None
    if not _HAS_LETTER.search(cleaned):
        return None
    if cleaned.lower() in _PLACEHOLDER_NAMES:
        return None
    return cleaned


def known_tag_names(user):
    """The user's existing tag names, so the model reuses them instead of
    coining near-duplicates."""
    return list(ExpenseTag.objects.filter(user=user).values_list('name', flat=True))


def known_category_names(transaction_type=None):
    """Active category names, optionally of one kind. Used by the parse prompts."""
    qs = ExpenseCategory.objects.filter(is_active=True)
    if transaction_type:
        qs = qs.filter(transaction_type=transaction_type)
    return list(qs.values_list('name', flat=True))


def resolve_tags(user, names):
    """Map suggested tag names onto the user's tags, creating what's missing.

    ExpenseTag.name is globally unique, so a name another account holds can't be
    created here - skip those rather than failing the save.

    A name that isn't tag-shaped is skipped rather than substituted: a tag is
    optional decoration, and a missing one costs nothing, where a wrong one is
    worse than nothing.
    """
    resolved = []
    seen = set()
    for raw in names or []:
        name = normalise_label(raw, MAX_TAG_NAME_LENGTH, MAX_TAG_WORDS)
        if not name or name.lower() in seen:
            if name is None and raw:
                logger.info("Skipping unusable tag name %r", raw)
            continue
        seen.add(name.lower())
        tag = ExpenseTag.objects.filter(name__iexact=name, user=user).first()
        if not tag:
            if ExpenseTag.objects.filter(name__iexact=name).exists():
                continue  # owned by another user
            tag = ExpenseTag.objects.create(name=name[:50], user=user)
        resolved.append(tag)
        if len(resolved) >= MAX_TAGS_PER_ITEM:
            break
    return resolved


def resolve_category(parsed):
    """Find or create the category for a parsed item, honouring its type.

    The model's transaction_type wins; reuse an existing category only when its
    type agrees, so "lent 200 to raj" can't be filed as an expense merely
    because some unrelated category shares the name.
    """
    transaction_type = parsed['transaction_type']
    name = normalise_label(
        parsed.get('category_name'), MAX_CATEGORY_NAME_LENGTH, MAX_CATEGORY_WORDS)

    if name is None:
        logger.info("Unusable category name %r - filing under the fallback bucket",
                    parsed.get('category_name'))
        return _fallback_category(transaction_type)

    category = ExpenseCategory.objects.filter(
        name__iexact=name, is_active=True, transaction_type=transaction_type
    ).first()
    if category:
        return category

    if not _room_for_another_category(transaction_type):
        return _fallback_category(transaction_type)

    return _create_category(name, transaction_type)


def _room_for_another_category(transaction_type):
    """False once the list of this kind is long enough to be unreadable."""
    count = ExpenseCategory.objects.filter(
        is_active=True, transaction_type=transaction_type).count()
    if count < MAX_AUTO_CATEGORIES_PER_TYPE:
        return True
    logger.warning(
        "Not auto-creating another %s category: %s already exist (cap %s)",
        transaction_type, count, MAX_AUTO_CATEGORIES_PER_TYPE)
    return False


def _create_category(name, transaction_type):
    """Insert a category, sidestepping the global unique-name constraint.

    Only ever called for a name no active category of this type already has.
    A lowercase first letter is raised so a freshly coined "groceries" sits
    beside "Groceries" rather than under it - cosmetic, and only ever applied
    to a row being created, never to one that exists.
    """
    if name[:1].islower():
        name = name[0].upper() + name[1:]
    name = name[:100]
    if ExpenseCategory.objects.filter(name__iexact=name).exists():
        # The name belongs to a category of another type (or an archived one).
        # Qualify it rather than stealing or renaming that row.
        name = f"{name} ({transaction_type})"[:100]
        existing = ExpenseCategory.objects.filter(
            name__iexact=name, is_active=True, transaction_type=transaction_type
        ).first()
        if existing:
            return existing
    category, _ = ExpenseCategory.objects.get_or_create(
        name=name, defaults={'transaction_type': transaction_type},
    )
    return category


def _fallback_category(transaction_type):
    """The bucket unusable names land in - created on first use, then reused."""
    name = _FALLBACK_CATEGORY_NAMES.get(transaction_type, 'Other')
    category = ExpenseCategory.objects.filter(
        name__iexact=name, is_active=True, transaction_type=transaction_type
    ).first()
    if category:
        return category
    return _create_category(name, transaction_type)
