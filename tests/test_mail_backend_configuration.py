from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from apps.web.mail import ResendMailer


class MailBackendConfigurationTests(unittest.TestCase):
    def env(self, **values: str):
        return patch.dict(os.environ, values, clear=True)

    def test_no_mail_configuration_disables_mailer(self) -> None:
        with self.env():
            self.assertIsNone(ResendMailer.from_env())

    def test_api_key_alone_does_not_implicitly_select_provider(self) -> None:
        with self.env(RESEND_API_KEY="re_test"):
            with self.assertRaisesRegex(RuntimeError, "TESTAMUR_MAIL_BACKEND=resend"):
                ResendMailer.from_env()

    def test_resend_backend_requires_api_key(self) -> None:
        with self.env(TESTAMUR_MAIL_BACKEND="resend"):
            with self.assertRaisesRegex(RuntimeError, "requires RESEND_API_KEY"):
                ResendMailer.from_env()

    def test_resend_backend_requires_explicit_sender_and_public_origin(self) -> None:
        with self.env(TESTAMUR_MAIL_BACKEND="resend", RESEND_API_KEY="re_test"):
            with self.assertRaisesRegex(RuntimeError, "TESTAMUR_MAIL_FROM"):
                ResendMailer.from_env()
        with self.env(
            TESTAMUR_MAIL_BACKEND="resend",
            RESEND_API_KEY="re_test",
            TESTAMUR_MAIL_FROM="Testamur <verify@example.test>",
        ):
            with self.assertRaisesRegex(RuntimeError, "TESTAMUR_PUBLIC_ORIGIN"):
                ResendMailer.from_env()

    def test_explicit_resend_configuration_constructs_mailer(self) -> None:
        with self.env(
            TESTAMUR_MAIL_BACKEND="resend",
            RESEND_API_KEY="re_test",
            TESTAMUR_MAIL_FROM="Testamur <verify@example.test>",
            TESTAMUR_PUBLIC_ORIGIN="https://testamur.example/",
        ):
            mailer = ResendMailer.from_env()
        assert mailer is not None
        self.assertEqual(mailer.api_key, "re_test")
        self.assertEqual(mailer.sender, "Testamur <verify@example.test>")
        self.assertEqual(mailer.public_origin, "https://testamur.example")

    def test_unknown_backend_fails_closed(self) -> None:
        with self.env(TESTAMUR_MAIL_BACKEND="smtp", RESEND_API_KEY="re_test"):
            with self.assertRaisesRegex(RuntimeError, "Unsupported TESTAMUR_MAIL_BACKEND"):
                ResendMailer.from_env()


if __name__ == "__main__":
    unittest.main()
