from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

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

    def test_verification_email_contains_code_and_link(self) -> None:
        mailer = ResendMailer(
            api_key="re_test",
            sender="Testamur <verify@testamur.org>",
            public_origin="https://testamur.org",
        )
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'{"id":"email_test"}'
        response.status = 200
        response.getcode.return_value = 200
        with patch("apps.web.mail.urllib.request.urlopen", return_value=response) as opened:
            email_id = mailer.send_verification(
                user={
                    "user_id": "usr_test",
                    "email": "person@example.com",
                    "display_name": "Person",
                },
                token="verification-token-value",
                code="123456",
            )
        self.assertEqual(email_id, "email_test")
        request = opened.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("123456", payload["html"])
        self.assertIn("/verify-email?token=verification-token-value", payload["html"])
        self.assertEqual(payload["to"], ["person@example.com"])

    def test_unknown_backend_fails_closed(self) -> None:
        with self.env(TESTAMUR_MAIL_BACKEND="smtp", RESEND_API_KEY="re_test"):
            with self.assertRaisesRegex(RuntimeError, "Unsupported TESTAMUR_MAIL_BACKEND"):
                ResendMailer.from_env()


if __name__ == "__main__":
    unittest.main()
