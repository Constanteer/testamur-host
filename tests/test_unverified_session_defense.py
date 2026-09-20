from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apps.web.identity import AccountStore


class UnverifiedSessionDefenseTests(unittest.TestCase):
    def test_existing_session_cannot_authenticate_after_verification_is_cleared(self) -> None:
        """A stale/legacy session must never bypass the durable verification gate."""
        with tempfile.TemporaryDirectory() as tempdir:
            store = AccountStore(Path(tempdir) / "accounts.sqlite3")
            _pending, verification_token = store.sign_up(
                username="pending-user",
                email="pending@example.com",
                password="a-long-test-password",
            )
            verified, session = store.verify_email(token=verification_token)
            self.assertTrue(verified["email_verified"])
            self.assertIsNotNone(store.user_for_session(session))

            # Model a stale/pre-verification session surviving a migration or an
            # operator correction that makes verification state explicit again.
            with store._connect() as connection:
                connection.execute(
                    "UPDATE users SET email_verified_at = NULL, email_verification_source = NULL WHERE user_id = ?",
                    (verified["user_id"],),
                )

            self.assertIsNone(store.user_for_session(session))
            with store._connect() as connection:
                remaining = connection.execute(
                    "SELECT 1 FROM sessions WHERE session_hash = ?",
                    (__import__("hashlib").sha256(session.encode("utf-8")).hexdigest(),),
                ).fetchone()
            self.assertIsNone(remaining)


if __name__ == "__main__":
    unittest.main()
