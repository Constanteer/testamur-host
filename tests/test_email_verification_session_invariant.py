from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.web.identity import AccountStore


class EmailVerificationSessionInvariantTests(unittest.TestCase):
    def test_session_is_revoked_if_durable_verification_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp) / "accounts.sqlite3")
            _pending, verification_token = store.sign_up(
                username="verified-session-user",
                email="verified-session@example.com",
                password="a-long-test-password",
            )
            user, session_token = store.verify_email(token=verification_token)
            self.assertTrue(user["email_verified"])
            self.assertIsNotNone(store.user_for_session(session_token))

            with store._connect() as connection:
                connection.execute(
                    "UPDATE users SET email_verified_at = NULL, email_verification_source = NULL WHERE user_id = ?",
                    (user["user_id"],),
                )

            self.assertIsNone(store.user_for_session(session_token))
            with store._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM sessions WHERE user_id = ?",
                    (user["user_id"],),
                ).fetchone()
            self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
