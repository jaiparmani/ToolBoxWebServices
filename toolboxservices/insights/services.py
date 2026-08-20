"""Turn a user's logged health metrics into a Claude-written review.

Two halves that stay independent on purpose:

  build_health_context() - pure Django ORM, no network. Testable on its own.
  generate_health_insight() - takes that context to Claude and validates what
  comes back.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from llm.client import LLMError, LLMNotConfigured, LLMRateLimited, call_json

from health.models import HealthMetric

logger = logging.getLogger(__name__)


class InsightNotPossible(Exception):
    """Caller's fault or nothing to analyse - not enough data, no API key."""


class InsightGenerationError(Exception):
    """The model call itself failed or came back unusable."""


class InsightRateLimited(InsightGenerationError):
    """The provider is out of quota - a temporary condition, not a failure."""


# How to collapse several same-day entries into one daily figure. Water and
# steps accumulate across the day; weight is a reading, so the last one wins.
DAILY_AGGREGATION = {
    'weight': 'last',
    'water': 'sum',
    'sleep': 'sum',
    'steps': 'sum',
}

# Structured output contract. Every property is required and additionalProperties
# is false - both are required by the structured-outputs API.
INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "One short sentence (max ~90 chars) capturing the period.",
        },
        "summary": {
            "type": "string",
            "description": "2-4 sentences of plain-language overview across all metrics.",
        },
        "observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific patterns visible in the data, each citing the numbers it rests on.",
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Trends worth attention. Empty array if nothing stands out.",
        },
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete, small actions for the coming week.",
        },
        "data_gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Metrics that were missing or logged too sparsely to say anything about.",
        },
    },
    "required": ["headline", "summary", "observations", "concerns", "suggestions", "data_gaps"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You review a person's self-logged health metrics and report what the numbers show.\n"
    "\n"
    "Ground every statement in the data you are given: quote the actual figures and date "
    "ranges rather than describing trends in the abstract. Where a metric was logged on only "
    "a few days, say the sample is too thin instead of reading a trend into it, and list it "
    "under data_gaps.\n"
    "\n"
    "You are not a clinician. Do not diagnose, do not name conditions, and do not give medical "
    "advice. Suggestions should be ordinary habit-level adjustments (logging consistency, "
    "sleep timing, hydration targets). If something in the data looks genuinely worth a "
    "doctor's attention, say plainly that it is worth raising with one.\n"
    "\n"
    "Write for the person themselves - direct, concrete, no hedging filler. Keep lists short; "
    "three strong observations beat eight weak ones. Return empty arrays rather than padding "
    "with generic advice."
)


@dataclass
class HealthContext:
    """Everything the model gets to see, plus the window it covers."""
    period_start: date
    period_end: date
    metrics: dict = field(default_factory=dict)
    entry_count: int = 0

    def as_prompt_json(self):
        return json.dumps(
            {
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "metrics": self.metrics,
            },
            indent=2,
        )


def _f(value):
    """Decimal -> float, rounded, so the JSON stays readable."""
    if value is None:
        return None
    return round(float(value), 2)


def build_health_context(user, days=30):
    """Collapse a user's metrics for the last `days` days into a compact structure.

    Raises InsightNotPossible when there is nothing logged in the window.
    """
    period_end = timezone.now().date()
    period_start = period_end - timedelta(days=days - 1)

    entries = list(
        HealthMetric.objects
        .filter(user=user, date__gte=period_start, date__lte=period_end)
        .order_by('date', 'created_at')
    )
    if not entries:
        raise InsightNotPossible(
            f"No health metrics logged between {period_start} and {period_end}."
        )

    metrics = {}
    for metric_type, label in HealthMetric.METRIC_TYPE_CHOICES:
        of_type = [e for e in entries if e.metric_type == metric_type]
        if not of_type:
            continue

        # One figure per day
        per_day = {}
        for entry in of_type:
            key = entry.date.isoformat()
            if DAILY_AGGREGATION.get(metric_type) == 'sum':
                per_day[key] = per_day.get(key, Decimal('0')) + entry.value
            else:
                per_day[key] = entry.value  # entries are date-ordered, so this is the last

        values = list(per_day.values())
        daily = [{"date": d, "value": _f(v)} for d, v in sorted(per_day.items())]

        # Compare the most recent week against everything before it
        recent_cutoff = (period_end - timedelta(days=6)).isoformat()
        recent = [_f(v) for d, v in per_day.items() if d >= recent_cutoff]
        earlier = [_f(v) for d, v in per_day.items() if d < recent_cutoff]

        metrics[metric_type] = {
            "label": label,
            "unit": of_type[-1].unit or HealthMetric.DEFAULT_UNITS.get(metric_type, ''),
            "days_logged": len(per_day),
            "days_in_period": days,
            "latest": {"date": daily[-1]["date"], "value": daily[-1]["value"]},
            "period_average": _f(sum(values) / len(values)),
            "period_min": _f(min(values)),
            "period_max": _f(max(values)),
            "last_7_days_average": round(sum(recent) / len(recent), 2) if recent else None,
            "prior_days_average": round(sum(earlier) / len(earlier), 2) if earlier else None,
            "daily": daily,
            "notes": [
                {"date": e.date.isoformat(), "note": e.notes}
                for e in of_type if e.notes
            ][:10],
        }

    # Metrics the user never logged at all - the model should call these out
    logged = set(metrics)
    missing = [label for key, label in HealthMetric.METRIC_TYPE_CHOICES if key not in logged]

    context = HealthContext(period_start=period_start, period_end=period_end, entry_count=len(entries))
    context.metrics = {"tracked": metrics, "never_logged_this_period": missing}
    return context


def _client():
    """Build the Anthropic client, or explain why we can't."""
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise InsightNotPossible(
            "ANTHROPIC_API_KEY is not set on the server, so insights cannot be generated."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise InsightNotPossible(
            "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return anthropic, anthropic.Anthropic(api_key=api_key)


def generate_health_insight(user, days=30):
    """Build the context, ask Claude, and return (parsed_dict, metadata).

    metadata carries model / effort / token counts for the Insight row.
    """
    context = build_health_context(user, days=days)
    anthropic, client = _client()

    model = getattr(settings, 'ANTHROPIC_MODEL', 'claude-opus-5')
    effort = getattr(settings, 'ANTHROPIC_EFFORT', 'medium')
    max_tokens = getattr(settings, 'ANTHROPIC_MAX_TOKENS', 8000)

    user_content = (
        f"Here is {user.username}'s logged health data for the period. Values are already "
        f"aggregated to one figure per day.\n\n"
        f"```json\n{context.as_prompt_json()}\n```\n\n"
        f"Review the period and fill in the required structure."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": INSIGHT_SCHEMA},
            },
        )
    except anthropic.APIStatusError as exc:
        logger.exception("Claude returned %s for user %s", exc.status_code, user.id)
        raise InsightGenerationError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        logger.exception("Could not reach Claude for user %s", user.id)
        raise InsightGenerationError("Could not reach the Claude API. Check network access.") from exc

    # Check why generation stopped before trusting the content.
    if response.stop_reason == "refusal":
        details = getattr(response, 'stop_details', None)
        detail = getattr(details, 'explanation', None) if details else None
        raise InsightGenerationError(f"Claude declined to answer. {detail or ''}".strip())
    if response.stop_reason == "max_tokens":
        raise InsightGenerationError(
            f"Response hit the {max_tokens}-token limit before finishing. "
            f"Raise ANTHROPIC_MAX_TOKENS or lower ANTHROPIC_EFFORT."
        )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise InsightGenerationError("Claude returned no text content.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InsightGenerationError("Claude returned malformed JSON.") from exc

    missing = [k for k in INSIGHT_SCHEMA["required"] if k not in parsed]
    if missing:
        raise InsightGenerationError(f"Claude's response is missing fields: {', '.join(missing)}")

    metadata = {
        "model": response.model,
        "effort": effort,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "period_start": context.period_start,
        "period_end": context.period_end,
        "entry_count": context.entry_count,
    }
    return parsed, metadata


# --------------------------------------------------------------------------
# Expense insights
#
# Same output shape as the health review, so Insight.payload, the serializer
# and the UI stay identical across scopes. Runs on OpenRouter (llm.client)
# rather than Anthropic: OPENROUTER_API_KEY is the key configured on the
# server, and the JSON hardening there already survives the free pool.
# --------------------------------------------------------------------------

EXPENSE_REQUIRED_FIELDS = ["headline", "summary", "observations", "concerns",
                           "suggestions", "data_gaps"]

EXPENSE_SYSTEM_PROMPT = (
    "You review a person's spending record for a period and report what the numbers show.\n"
    "\n"
    "Ground every statement in the data you are given: quote actual amounts, counts and "
    "category names rather than describing trends in the abstract. Percentages should be "
    "ones you can compute from the figures provided. Where a category has only a couple of "
    "transactions, say the sample is thin instead of reading a trend into it, and list it "
    "under data_gaps.\n"
    "\n"
    "Be useful and specific: which categories dominate, what changed against the earlier "
    "part of the period, which single transactions are unusually large, and where a small "
    "habit change would plausibly matter. Do not moralise about spending and do not give "
    "investment advice. Amounts are in Indian rupees.\n"
    "\n"
    "Write for the person themselves - direct, concrete, no filler. Three strong "
    "observations beat eight weak ones. Return empty arrays rather than padding.\n"
    "\n"
    "Respond with ONLY a JSON object - no prose, no code fences - with exactly these keys: "
    "\"headline\" (one sentence, max ~90 chars), \"summary\" (2-4 sentences), "
    "\"observations\" (array of strings), \"concerns\" (array of strings), "
    "\"suggestions\" (array of strings), \"data_gaps\" (array of strings)."
)


def build_expense_context(user, days=30):
    """Collapse a user's transactions for the last `days` days into a compact structure.

    Pure ORM, no network - testable on its own, same split as the health side.
    Raises InsightNotPossible when there is nothing in the window.
    """
    from decimal import Decimal

    from expenses.models import Expense

    period_end = timezone.now().date()
    period_start = period_end - timedelta(days=days - 1)

    entries = list(
        Expense.objects
        .filter(user=user, date__gte=period_start, date__lte=period_end)
        .select_related('category')
        .order_by('date', 'created_at')
    )
    if not entries:
        raise InsightNotPossible(
            f"No transactions recorded between {period_start} and {period_end}."
        )

    def total(rows):
        return _f(sum((e.amount for e in rows), Decimal('0')))

    by_type = {}
    for ttype, label in Expense.TRANSACTION_TYPE_CHOICES:
        rows = [e for e in entries if e.transaction_type == ttype]
        if rows:
            by_type[ttype] = {"label": label, "total": total(rows), "count": len(rows)}

    spend = [e for e in entries if e.transaction_type == 'expense']

    categories = {}
    for entry in spend:
        name = entry.category.name if entry.category else 'Uncategorised'
        bucket = categories.setdefault(name, {"total": Decimal('0'), "count": 0})
        bucket["total"] += entry.amount
        bucket["count"] += 1
    by_category = sorted(
        ({"category": k, "total": _f(v["total"]), "count": v["count"]}
         for k, v in categories.items()),
        key=lambda row: row["total"], reverse=True,
    )

    # Recent week against everything before it, mirroring the health context.
    cutoff = period_end - timedelta(days=6)
    recent = [e for e in spend if e.date >= cutoff]
    earlier = [e for e in spend if e.date < cutoff]

    daily = {}
    for entry in spend:
        key = entry.date.isoformat()
        daily[key] = daily.get(key, Decimal('0')) + entry.amount

    largest = sorted(spend, key=lambda e: e.amount, reverse=True)[:5]

    return {
        "period_start": period_start,
        "period_end": period_end,
        "entry_count": len(entries),
        "data": {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "days_in_period": days,
            "currency": "INR",
            "totals_by_type": by_type,
            "spend_by_category": by_category,
            "spend_total": total(spend),
            "transaction_count": len(entries),
            "days_with_spending": len(daily),
            "last_7_days_spend": total(recent),
            "prior_days_spend": total(earlier),
            "daily_spend": [{"date": d, "total": _f(v)} for d, v in sorted(daily.items())],
            "largest_transactions": [
                {"date": e.date.isoformat(), "amount": _f(e.amount),
                 "description": e.description[:80],
                 "category": e.category.name if e.category else None}
                for e in largest
            ],
        },
    }


def _validate_expense_insight(parsed):
    """The model's review must have every field, with the list fields as lists."""
    if not isinstance(parsed, dict):
        raise LLMError("The model did not return a JSON object.")

    missing = [k for k in EXPENSE_REQUIRED_FIELDS if k not in parsed]
    if missing:
        raise LLMError(f"The response is missing fields: {', '.join(missing)}")

    for key in ("headline", "summary"):
        if not isinstance(parsed[key], str) or not parsed[key].strip():
            raise LLMError(f"The response has an empty {key}.")
        parsed[key] = parsed[key].strip()

    for key in ("observations", "concerns", "suggestions", "data_gaps"):
        value = parsed[key]
        if isinstance(value, str):  # a lone string instead of a list
            value = [value] if value.strip() else []
        if not isinstance(value, list):
            raise LLMError(f"{key} should be a list of strings.")
        parsed[key] = [str(v).strip() for v in value if str(v).strip()]

    return parsed


def generate_expense_insight(user, days=30):
    """Build the context, ask the model, and return (parsed_dict, metadata)."""
    context = build_expense_context(user, days=days)

    user_content = (
        f"Here is {user.username}'s transaction data for the period, already aggregated.\n\n"
        f"```json\n{json.dumps(context['data'], indent=2)}\n```\n\n"
        f"Review the period and fill in the required structure."
    )
    messages = [
        {"role": "system", "content": EXPENSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        parsed, meta = call_json(
            messages,
            validate=_validate_expense_insight,
            expect_key='headline',
            retry_instruction=(
                "That was not usable. Reply with ONLY a JSON object, no prose and no code "
                "fences, with the keys headline, summary, observations, concerns, "
                "suggestions and data_gaps."
            ),
            max_tokens=4000,
            timeout=60,
            return_meta=True,
        )
    except LLMNotConfigured as exc:
        raise InsightNotPossible(str(exc)) from exc
    except LLMRateLimited as exc:
        raise InsightRateLimited(str(exc)) from exc
    except LLMError as exc:
        raise InsightGenerationError(str(exc)) from exc

    return parsed, {
        "model": meta["model"],
        "effort": "",
        "input_tokens": meta["input_tokens"],
        "output_tokens": meta["output_tokens"],
        "period_start": context["period_start"],
        "period_end": context["period_end"],
        "entry_count": context["entry_count"],
    }
