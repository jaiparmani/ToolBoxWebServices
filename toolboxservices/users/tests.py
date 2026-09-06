"""Signing in with a mobile number.

Registration stores the number normalised (separators stripped, optional
leading '+'), so login has to normalise the typed string the same way before
comparing. These cover the formats a person actually types.
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import UserProfile


class PhoneMpinLoginTests(APITestCase):
    LOGIN = reverse('mpin-login')

    def setUp(self):
        self.user = User.objects.create_user(
            'ashok', email='ashok@example.com', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.phone = '+919876543210'      # as normalize_phone would store it
        profile.save(update_fields=['phone'])
        profile.set_mpin('041100')
        self.profile = profile

    def login(self, identifier, mpin='041100'):
        return self.client.post(
            self.LOGIN, {'identifier': identifier, 'mpin': mpin}, format='json')

    def test_exactly_as_stored(self):
        self.assertEqual(self.login('+919876543210').status_code, 200)

    def test_typed_with_separators(self):
        """The case the old lookup could not match: it only stripped spaces,
        so a dash made the string non-numeric and it fell through to a
        username lookup that finds nobody."""
        for typed in ('+91 98765 43210', '+91-98765-43210', '+91 (98765) 43210'):
            with self.subTest(typed=typed):
                self.assertEqual(self.login(typed).status_code, 200, typed)

    def test_without_the_plus(self):
        """Whether people type the '+' is a coin flip; the digits still name
        one account."""
        self.assertEqual(self.login('919876543210').status_code, 200)

    def test_email_and_username_still_work(self):
        self.assertEqual(self.login('ashok@example.com').status_code, 200)
        self.assertEqual(self.login('ashok').status_code, 200)

    def test_wrong_mpin_is_rejected(self):
        self.assertEqual(self.login('+919876543210', mpin='000000').status_code, 401)

    def test_unknown_number_is_rejected(self):
        self.assertEqual(self.login('+919999999999').status_code, 401)

    def test_an_ambiguous_number_resolves_to_nobody(self):
        """Two accounts on one number must not sign either of them in."""
        other = User.objects.create_user('jai', email='jai@example.com', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=other)
        profile.phone = '+919876543210'
        profile.save(update_fields=['phone'])
        profile.set_mpin('041100')
        self.assertEqual(self.login('+919876543210').status_code, 401)

    def test_a_number_with_no_mpin_set_is_rejected(self):
        User.objects.create_user('priya', email='p@example.com', password='x')
        priya = User.objects.get(username='priya')
        profile, _ = UserProfile.objects.get_or_create(user=priya)
        profile.phone = '+919111111111'
        profile.save(update_fields=['phone'])
        self.assertEqual(self.login('+919111111111').status_code, 401)
