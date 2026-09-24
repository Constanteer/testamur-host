from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from apps.web.mail import ResendMailer


class MailOriginValidationTests(unittest.TestCase):
    def configured(self, origin: str) -> dict[str, str]:
        return {
            "TESTAMUR_MAIL_BACKEND": "resend",
            "RESEND_API_KEY": "re_test",
            "TESTAMUR_MAIL_FROM": "Testamur <accounts@testamur.org>",
            "TESTAMUR_PUBLIC_ORIGIN": origin,
        }

    def test_production_verification_origin_requires_https(self) -> None:
        with patch.dict(os.environ, self.configured("http://testamur.org"), clear=True):
            with self.assertRaisesRegex(RuntimeError, "must use https"):
                ResendMailer.from_env()

    def test_public_origin_rejects_path_query_fragment_and_credentials(self) -> None:
        invalid = (
            "https://testamur.org/account",
            "https://testamur.org?next=evil",
            "https://testamur.org#fragment",
            "https://user:pass@testamur.org",
        )
        for origin in invalid:
            with self.subTest(origin=origin), patch.dict(os.environ, self.configured(origin), clear=True):
                with self.assertRaisesRegex(RuntimeError, "only scheme and authority"):
                    ResendMailer.from_env()

    def test_https_origin_and_local_http_are_allowed(self) -> None:
        for origin in ("https://testamur.org", "http://localhost:8788", "http://127.0.0.1:8788"):
            with self.subTest(origin=origin), patch.dict(os.environ, self.configured(origin), clear=True):
                mailer = ResendMailer.from_env()
                self.assertIsNotNone(mailer)
                assert mailer is not None
                self.assertEqual(mailer.public_origin, origin)


if __name__ == "__main__":
    unittest.main()
