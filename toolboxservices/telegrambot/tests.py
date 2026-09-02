from django.test import TestCase

from .views import _split_command


class SplitCommandTests(TestCase):
    def test_plain_text(self):
        self.assertEqual(_split_command("20 chai"), ("", "20 chai"))

    def test_command_with_args(self):
        self.assertEqual(
            _split_command("/ask how much on food"), ("/ask", "how much on food")
        )

    def test_command_strips_botname(self):
        self.assertEqual(_split_command("/start@MyBot abc"), ("/start", "abc"))

    def test_bare_command(self):
        self.assertEqual(_split_command("/help"), ("/help", ""))
