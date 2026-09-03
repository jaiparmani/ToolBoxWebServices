"""Tests for the insights app.

The Claude call is stubbed - these run offline and cost nothing. What they
cover is our side of the contract: how metrics get aggregated, what we send,
and what we do with each kind of response.
"""

import inspect
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from health.models import HealthMetric
from insights.models import Insight
from insights.services import InsightNotPossible, build_health_context

VALID_OUTPUT = {
    "headline": "Sleep improved, water intake slipped.",
    "summary": "Over the last 30 days sleep averaged 7.1 hours.",
    "observations": ["Sleep rose from 6.4h to 7.1h over the period."],
    "concerns": ["Water logging dropped off in the final week."],
    "suggestions": ["Log water at each meal to keep the record complete."],
    "data_gaps": ["Steps were logged on only 1 of 30 days."],
}


def fake_response(payload=None, stop_reason="end_turn"):
    """Minimal stand-in for an anthropic Message."""
    text = json.dumps(payload if payload is not None else VALID_OUTPUT)
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=2500, output_tokens=400),
    )


class FakeClient:
    """Records the kwargs it was called with, returns a canned response."""

    def __init__(self, response):
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return response

        self.messages = Messages()


def stub_client(response):
    """Patch services._client to hand back a FakeClient."""
    import anthropic
    client = FakeClient(response)
    return patch('insights.services._client', return_value=(anthropic, client)), client


class BuildHealthContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ctx', password='x')
        self.today = timezone.now().date()

    def test_sums_water_and_keeps_last_weight_per_day(self):
        for _ in range(4):
            HealthMetric.objects.create(user=self.user, metric_type='water', value=250, date=self.today)
        HealthMetric.objects.create(user=self.user, metric_type='weight', value=71, date=self.today)
        HealthMetric.objects.create(user=self.user, metric_type='weight', value=70, date=self.today)

        tracked = build_health_context(self.user, days=7).metrics['tracked']

        self.assertEqual(tracked['water']['daily'][0]['value'], 1000.0)  # 4 x 250 summed
        self.assertEqual(tracked['weight']['daily'][0]['value'], 70.0)   # last reading wins
        self.assertEqual(tracked['water']['unit'], 'ml')

    def test_lists_metrics_never_logged(self):
        HealthMetric.objects.create(user=self.user, metric_type='sleep', value=7, date=self.today)
        metrics = build_health_context(self.user, days=7).metrics
        self.assertIn('Steps', metrics['never_logged_this_period'])
        self.assertNotIn('Sleep', metrics['never_logged_this_period'])

    def test_ignores_entries_outside_the_window(self):
        HealthMetric.objects.create(user=self.user, metric_type='sleep', value=7,
                                    date=self.today - timedelta(days=40))
        with self.assertRaises(InsightNotPossible):
            build_health_context(self.user, days=30)

    def test_no_data_raises(self):
        with self.assertRaises(InsightNotPossible):
            build_health_context(self.user, days=30)


@override_settings(ANTHROPIC_API_KEY='test-key', ANTHROPIC_EFFORT='medium')
class GenerateEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='gen', password='x')
        today = timezone.now().date()
        for i in range(10):
            HealthMetric.objects.create(user=self.user, metric_type='sleep',
                                        value=7, date=today - timedelta(days=i))
        self.url = f'/api/insights/health/generate/?userid={self.user.id}'

    def post(self, body=None):
        return self.client.post(self.url, data=json.dumps(body or {}),
                                content_type='application/json')

    def test_creates_and_stores_an_insight(self):
        patcher, client = stub_client(fake_response())
        with patcher:
            response = self.post({'days': 30})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body['regenerated'])
        self.assertEqual(body['headline'], VALID_OUTPUT['headline'])
        self.assertEqual(body['payload']['concerns'], VALID_OUTPUT['concerns'])

        insight = Insight.objects.get(user=self.user)
        self.assertEqual(insight.status, 'success')
        self.assertEqual(insight.model, 'claude-opus-5')
        self.assertEqual(insight.input_tokens, 2500)

    def test_sends_the_expected_request_shape(self):
        patcher, client = stub_client(fake_response())
        with patcher:
            self.post({'days': 14})

        kwargs = client.calls[0]
        self.assertEqual(kwargs['model'], 'claude-opus-5')
        self.assertEqual(kwargs['output_config']['effort'], 'medium')
        self.assertEqual(kwargs['output_config']['format']['type'], 'json_schema')
        schema = kwargs['output_config']['format']['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), set(schema['properties']))
        # The metrics actually reach the model
        self.assertIn('"sleep"', kwargs['messages'][0]['content'])

    def test_kwargs_are_accepted_by_the_installed_sdk(self):
        """Guards against an SDK upgrade renaming the params we rely on."""
        from anthropic.resources.messages import Messages
        accepted = set(inspect.signature(Messages.create).parameters)
        patcher, client = stub_client(fake_response())
        with patcher:
            self.post()
        self.assertTrue(set(client.calls[0]).issubset(accepted),
                        f"unknown params: {set(client.calls[0]) - accepted}")

    def test_reuses_a_recent_insight_instead_of_calling_the_model(self):
        patcher, client = stub_client(fake_response())
        with patcher:
            self.post()
            second = self.post()

        self.assertEqual(len(client.calls), 1)
        self.assertFalse(second.json()['regenerated'])
        self.assertEqual(Insight.objects.count(), 1)

    def test_force_bypasses_the_cache(self):
        patcher, client = stub_client(fake_response())
        with patcher:
            self.post()
            self.post({'force': True})

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(Insight.objects.count(), 2)

    def test_refusal_is_recorded_as_a_failed_insight(self):
        patcher, _ = stub_client(fake_response(stop_reason='refusal'))
        with patcher:
            response = self.post()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(Insight.objects.get(user=self.user).status, 'failed')

    def test_truncated_response_is_reported_not_stored_as_success(self):
        patcher, _ = stub_client(fake_response(stop_reason='max_tokens'))
        with patcher:
            response = self.post()

        self.assertEqual(response.status_code, 502)
        self.assertIn('token limit', response.json()['error'])

    def test_missing_field_in_model_output_is_rejected(self):
        broken = {k: v for k, v in VALID_OUTPUT.items() if k != 'suggestions'}
        patcher, _ = stub_client(fake_response(payload=broken))
        with patcher:
            response = self.post()

        self.assertEqual(response.status_code, 502)
        self.assertIn('suggestions', response.json()['error'])

    def test_user_with_no_metrics_gets_a_clear_400(self):
        other = User.objects.create_user(username='empty', password='x')
        patcher, client = stub_client(fake_response())
        with patcher:
            response = self.client.post(
                f'/api/insights/health/generate/?userid={other.id}',
                data='{}', content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(client.calls), 0)  # no model call wasted
        self.assertFalse(Insight.objects.exists())


@override_settings(ANTHROPIC_API_KEY='')
class MissingApiKeyTests(TestCase):
    def test_generate_explains_the_missing_key(self):
        user = User.objects.create_user(username='nokey', password='x')
        HealthMetric.objects.create(user=user, metric_type='sleep', value=7,
                                    date=timezone.now().date())
        response = self.client.post(f'/api/insights/health/generate/?userid={user.id}',
                                    data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('ANTHROPIC_API_KEY', response.json()['error'])


class LatestEndpointTests(TestCase):
    def test_only_returns_this_users_successful_insights(self):
        mine = User.objects.create_user(username='mine', password='x')
        theirs = User.objects.create_user(username='theirs', password='x')
        today = timezone.now().date()
        Insight.objects.create(user=theirs, period_start=today, period_end=today,
                               headline='not mine')
        Insight.objects.create(user=mine, period_start=today, period_end=today,
                               status='failed', headline='failed run')

        response = self.client.get(f'/api/insights/health/latest/?userid={mine.id}')
        self.assertEqual(response.status_code, 404)

        Insight.objects.create(user=mine, period_start=today, period_end=today,
                               headline='mine')
        response = self.client.get(f'/api/insights/health/latest/?userid={mine.id}')
        self.assertEqual(response.json()['headline'], 'mine')
