# Insights API Documentation

## Overview
Exposes Claude over the data already in ToolBox. The first scope is `health`:
the API pulls a user's `HealthMetric` rows for a date window, sends a compact
aggregate to Claude, and stores the structured review it returns.

Reviews are stored, not just returned, so the frontend can show history and so
a scheduled job can generate them overnight without anyone opening the app.

## Base Information

- **Base URL**: `http://localhost:8000/api/insights/`
- **User scoping**: `?userid=<id>` query parameter (same convention as the health and expense APIs)
- **Content-Type**: `application/json`
- **Model**: `claude-opus-5` via the official `anthropic` Python SDK

## Server setup

The API key is read from the environment - never commit it.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

On PythonAnywhere, add that line to `~/.bashrc` **and** set it in the WSGI
configuration file (the web app does not inherit your shell environment).

Optional overrides, all with sensible defaults in `settings.py`:

| Env var | Default | Notes |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-opus-5` | |
| `ANTHROPIC_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` - raise if reviews feel shallow |
| `ANTHROPIC_MAX_TOKENS` | `8000` | Caps thinking + response together |

Install the dependency:

```bash
pip install -r requirements.txt
```

---

## Endpoints

### List past insights

```
GET /api/insights/health/?userid=1
```

Paginated, newest first. Includes failed runs so a stretch of errors is visible.

```json
{
  "count": 12,
  "next": null,
  "previous": null,
  "results": [ { "...": "insight object" } ]
}
```

### Retrieve one

```
GET /api/insights/health/{id}/?userid=1
```

### Latest successful review

```
GET /api/insights/health/latest/?userid=1
```

Returns `404` with `{"error": "No insight has been generated yet."}` if there
has never been a successful run. This is the endpoint the dashboard should call.

### Generate a review now

```
POST /api/insights/health/generate/?userid=1
Content-Type: application/json

{ "days": 30, "force": false }
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `days` | int | `30` | Window to analyse, clamped to 1-180 |
| `force` | bool | `false` | Skip the 12-hour reuse window and call the model again |

If a successful insight was generated in the last 12 hours and `force` is not
set, the stored one comes back with `"regenerated": false` and **no model call
is made**. A fresh run returns `201` with `"regenerated": true`.

---

## The insight object

```json
{
  "id": 7,
  "scope": "health",
  "scope_display": "Health",
  "status": "success",
  "period_start": "2026-07-21",
  "period_end": "2026-08-19",
  "headline": "Sleep is trending up; water logging fell off this week.",
  "summary": "Across the last 30 days sleep averaged 7.1 hours, up from 6.4 in the first half...",
  "payload": {
    "observations": ["Sleep rose from a 6.4h average in weeks 1-2 to 7.1h in weeks 3-4."],
    "concerns": ["Water was logged on only 3 of the last 7 days."],
    "suggestions": ["Log water at each meal so the weekly average means something."],
    "data_gaps": ["Steps were recorded on 1 of 30 days - not enough to read a trend."],
    "entries_analysed": 110
  },
  "model": "claude-opus-5",
  "effort": "medium",
  "input_tokens": 2483,
  "output_tokens": 412,
  "error_message": null,
  "created_at": "2026-08-19T06:15:04Z"
}
```

`payload` keys are guaranteed present - the API constrains Claude's response to
a JSON schema, and a response missing any field is rejected rather than stored.

---

## Status codes

| Code | Meaning |
|---|---|
| `200` | Existing insight returned (list, retrieve, latest, or cached generate) |
| `201` | A new insight was generated |
| `400` | Missing/invalid `userid`, bad `days`, no metrics in the window, or `ANTHROPIC_API_KEY` not configured |
| `404` | No insight exists yet (`latest`) |
| `502` | Claude call failed, was refused, or returned unusable output - a `failed` insight row is written with the reason |

---

## Daily generation

A management command runs the review for every user who logged something in
the window:

```bash
python manage.py generate_health_insights
```

| Flag | Notes |
|---|---|
| `--days N` | Window size (default 30) |
| `--user-id N` | Only this user |
| `--force` | Run even if today's insight exists |
| `--dry-run` | Report who would be processed, without calling Claude |

Users with no metrics in the window are skipped before any model call, so an
idle account costs nothing.

**PythonAnywhere**: Tasks tab → daily task:

```
cd ~/toolboxweb && /home/jaiparmani/.virtualenvs/venv/bin/python manage.py generate_health_insights
```

---

## What gets sent to Claude

Only aggregated health metrics for the requested window - one figure per day
per metric, plus period statistics and any notes attached to entries. Water and
steps are summed across the day; weight takes the last reading. No account
details beyond the username are included.

The system prompt constrains the model to describe the data rather than
diagnose, and instructs it to flag thin samples as data gaps instead of reading
trends into three data points.

---

## Tests

```bash
python manage.py test insights
```

The Claude call is stubbed, so the suite runs offline and costs nothing. It
covers the daily aggregation rules, the request shape, the reuse window, and
each failure path (refusal, truncation, malformed output, missing key).
