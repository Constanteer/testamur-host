from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from apps.web.hosted_app import _core_web_cache_token, _versioned_web_response, build_hosted_server, dispatch_account_write
from apps.web.identity import AccountError, AccountStore
from apps.web.mail import MailDeliveryError, ResendMailer


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, object], str, str]] = []
        self.fail = False

    def send_verification(self, *, user: dict[str, object], token: str, code: str) -> str:
        from apps.web.mail import MailDeliveryError

        self.sent.append((user, token, code))
        if self.fail:
            raise MailDeliveryError("simulated delivery failure")
        return "email_test"


def body(response: dict[str, object]) -> dict[str, object]:
    return dict(response["body"])


class EmailVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = AccountStore(Path(self.tempdir.name) / "accounts.sqlite3")
        self.mailer = FakeMailer()

    def signup(self, *, username: str = "hank", email: str = "hank@example.com") -> tuple[dict[str, object], str]:
        response = dispatch_account_write(
            self.store,
            "/v1/account/signup",
            {
                "username": username,
                "email": email,
                "password": "a-long-test-password",
                "display_name": "Hank",
            },
            cookie_header=None,
            mailer=self.mailer,
        )
        payload = body(response)
        self.assertEqual(response["status"], 202)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["authenticated"])
        self.assertTrue(payload["verification_required"])
        self.assertTrue(payload["email_sent"])
        self.assertEqual(len(self.mailer.sent), 1)
        return payload, self.mailer.sent[-1][1]

    def test_signup_requires_email_verification_before_signin(self) -> None:
        payload, token = self.signup()
        self.assertFalse(payload["user"]["email_verified"])

        with self.assertRaises(AccountError) as raised:
            self.store.sign_in(identifier="hank@example.com", password="a-long-test-password")
        self.assertEqual(raised.exception.code, "email_verification_required")

        verify_response = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/verify",
            {"token": token},
            cookie_header=None,
            mailer=self.mailer,
        )
        verified = body(verify_response)
        self.assertTrue(verified["authenticated"])
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["user"]["email_verified"])
        self.assertIn("Set-Cookie", verify_response["headers"])

        user, _session = self.store.sign_in(
            identifier="hank@example.com",
            password="a-long-test-password",
        )
        self.assertTrue(user["email_verified"])

        replay = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/verify",
            {"token": token},
            cookie_header=None,
            mailer=self.mailer,
        )
        replay_payload = body(replay)
        self.assertEqual(replay["status"], 400)
        self.assertEqual(replay_payload["error"]["code"], "verification_token_used")

    def test_verification_code_activates_account_without_opening_link(self) -> None:
        payload, token = self.signup()
        code = self.mailer.sent[-1][2]
        self.assertRegex(code, r"^\d{6}$")

        verify_response = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/verify-code",
            {"identifier": "hank@example.com", "code": code},
            cookie_header=None,
            mailer=self.mailer,
        )
        verified = body(verify_response)
        self.assertEqual(verify_response["status"], 200)
        self.assertTrue(verified["authenticated"])
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["method"], "code")
        self.assertIn("Set-Cookie", verify_response["headers"])

        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT email_verification_source FROM users WHERE email = ?",
                ("hank@example.com",),
            ).fetchone()
            self.assertEqual(row["email_verification_source"], "email_code")

        replay = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/verify",
            {"token": token},
            cookie_header=None,
            mailer=self.mailer,
        )
        self.assertEqual(replay["status"], 400)
        self.assertEqual(body(replay)["error"]["code"], "verification_token_used")

    def test_verification_code_locks_after_repeated_failures(self) -> None:
        self.signup()
        for attempt in range(8):
            response = dispatch_account_write(
                self.store,
                "/v1/account/email-verification/verify-code",
                {"identifier": "hank@example.com", "code": "000000"},
                cookie_header=None,
                mailer=self.mailer,
            )
            if attempt < 7:
                self.assertEqual(response["status"], 400)
                self.assertEqual(body(response)["error"]["code"], "invalid_verification_code")
            else:
                self.assertEqual(response["status"], 429)
                self.assertEqual(body(response)["error"]["code"], "verification_code_locked")

        real_code = self.mailer.sent[-1][2]
        locked = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/verify-code",
            {"identifier": "hank@example.com", "code": real_code},
            cookie_header=None,
            mailer=self.mailer,
        )
        self.assertEqual(locked["status"], 400)
        self.assertEqual(body(locked)["error"]["code"], "invalid_verification_code")

    def test_resend_invalidates_previous_token(self) -> None:
        _payload, original = self.signup()
        resend = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/resend",
            {"identifier": "hank@example.com"},
            cookie_header=None,
            mailer=self.mailer,
        )
        resend_payload = body(resend)
        self.assertEqual(resend["status"], 202)
        self.assertTrue(resend_payload["accepted"])
        self.assertEqual(len(self.mailer.sent), 1)

        with self.store._connect() as connection:
            connection.execute(
                "UPDATE email_verification_tokens SET created_at = created_at - 120"
            )
        resend = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/resend",
            {"identifier": "hank@example.com"},
            cookie_header=None,
            mailer=self.mailer,
        )
        self.assertEqual(resend["status"], 202)
        replacement = self.mailer.sent[-1][1]
        self.assertNotEqual(original, replacement)

        with self.assertRaises(AccountError) as raised:
            self.store.verify_email(token=original)
        self.assertEqual(raised.exception.code, "verification_token_used")

        user, _session = self.store.verify_email(token=replacement)
        self.assertTrue(user["email_verified"])

    def test_failed_resend_preserves_previous_delivered_link(self) -> None:
        _payload, original = self.signup()
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE email_verification_tokens SET created_at = created_at - 120"
            )

        self.mailer.fail = True
        resend = dispatch_account_write(
            self.store,
            "/v1/account/email-verification/resend",
            {"identifier": "hank@example.com"},
            cookie_header=None,
            mailer=self.mailer,
        )
        self.assertEqual(resend["status"], 202)

        user, _session = self.store.verify_email(token=original)
        self.assertTrue(user["email_verified"])

    def test_resend_backend_requires_explicit_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAMUR_MAIL_BACKEND": "resend",
                "RESEND_API_KEY": "re_test",
                "TESTAMUR_MAIL_FROM": "Testamur <onboarding@resend.dev>",
                "TESTAMUR_PUBLIC_ORIGIN": "https://testamur.org",
            },
            clear=True,
        ):
            mailer = ResendMailer.from_env()
        self.assertIsNotNone(mailer)
        assert mailer is not None
        self.assertEqual(mailer.api_key, "re_test")

    def test_hosted_web_assets_are_snapshot_versioned_and_revalidated(self) -> None:
        token = _core_web_cache_token()
        response = _versioned_web_response(
            {
                "status": 200,
                "headers": {
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-cache",
                },
                "body": b'<link rel="stylesheet" href="/styles.css" /><script defer src="/app.js"></script>',
            }
        )
        html = response["body"].decode("utf-8")
        self.assertIn(f'href="/styles.css?v={token}"', html)
        self.assertIn(f'src="/app.js?v={token}"', html)
        self.assertEqual(response["headers"]["Cache-Control"], "no-cache, must-revalidate")

        script = _versioned_web_response(
            {
                "status": 200,
                "headers": {
                    "Content-Type": "text/javascript; charset=utf-8",
                    "Cache-Control": "public, max-age=300",
                },
                "body": b"console.log('new deploy')",
            }
        )
        self.assertEqual(script["headers"]["Cache-Control"], "no-cache, must-revalidate")

    def test_mail_delivery_failure_records_safe_health_diagnostic(self) -> None:
        server = build_hosted_server(
            data_dir=Path(self.tempdir.name) / 'mail-diagnostic',
            host='127.0.0.1',
            port=0,
            mailer=ResendMailer(
                api_key='re_test',
                sender='Testamur <onboarding@resend.dev>',
                public_origin='https://testamur.org',
            ),
            monitor_target_providers={},
            monitor_target_provider_specs={},
            monitor_scheduler_enabled=False,
        )
        try:
            server.record_mail_delivery(
                False,
                MailDeliveryError('Resend rejected verification email (403): domain is not verified'),
            )
            self.assertFalse(server.mail_last_attempt_ok)
            self.assertEqual(server.mail_last_error_code, 'provider_rejected_403')
            self.assertIn('sending domain verification', server.mail_last_error_hint or '')
            self.assertIn('resend.dev testing domain', server.mail_last_error_hint or '')
        finally:
            server.server_close()
    def test_unexpected_account_failure_is_recorded_safely(self) -> None:
        server = build_hosted_server(
            data_dir=Path(self.tempdir.name) / "account-diagnostic",
            host="127.0.0.1",
            port=0,
            monitor_target_providers={},
            monitor_target_provider_specs={},
            monitor_scheduler_enabled=False,
        )
        try:
            server.record_account_failure(sqlite3.OperationalError("database is locked"))
            self.assertIsNotNone(server.account_last_error_at)
            self.assertEqual(server.account_last_error_type, "OperationalError")
            self.assertIn("Zeabur logs", server.account_last_error_hint or "")
            server.clear_account_failure()
            self.assertIsNone(server.account_last_error_at)
            self.assertIsNone(server.account_last_error_type)
        finally:
            server.server_close()

    def test_bad_mail_configuration_does_not_crash_host_startup(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAMUR_MAIL_BACKEND": "resend",
                "RESEND_API_KEY": "re_test",
            },
            clear=True,
        ):
            server = build_hosted_server(
                data_dir=Path(self.tempdir.name) / "host",
                host="127.0.0.1",
                port=0,
                monitor_target_providers={},
                monitor_target_provider_specs={},
                monitor_scheduler_enabled=False,
            )
        try:
            self.assertIsNone(server.mailer)
            self.assertIn("TESTAMUR_MAIL_FROM", server.mail_config_error or "")
        finally:
            server.server_close()

    def test_existing_accounts_are_grandfathered_during_schema_migration(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(
                """
                CREATE TABLE users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_digest TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
        current_user, token, code = self.store.sign_up(
            username="digest-source",
            email="digest@example.com",
            password="a-long-test-password",
        )
        del current_user, token, code
        with self.store._connect() as connection:
            digest = connection.execute(
                "SELECT password_digest FROM users WHERE username = 'digest-source'"
            ).fetchone()["password_digest"]
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                """
                INSERT INTO users(
                    user_id, username, email, display_name, password_digest, created_at, updated_at
                ) VALUES ('usr_0123456789abcdef01234567', 'legacy', 'legacy@example.com', 'Legacy', ?, 100, 100)
                """,
                (digest,),
            )

        migrated = AccountStore(legacy_path)
        user, _session = migrated.sign_in(
            identifier="legacy@example.com",
            password="a-long-test-password",
        )
        self.assertTrue(user["email_verified"])
        self.assertEqual(user["email_verified_at"], 100)


if __name__ == "__main__":
    unittest.main()
