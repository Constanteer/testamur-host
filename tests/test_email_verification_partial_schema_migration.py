from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apps.web.identity import AccountStore


class EmailVerificationPartialSchemaMigrationTests(unittest.TestCase):
    def test_missing_source_column_does_not_grandfather_pending_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "accounts.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE users (
                        user_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        email TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        password_digest TEXT NOT NULL,
                        email_verified_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );
                    INSERT INTO users(
                        user_id, username, email, display_name, password_digest,
                        email_verified_at, created_at, updated_at
                    ) VALUES (
                        'usr_pending', 'pending', 'pending@example.com', 'Pending',
                        'not-used-by-this-test', NULL, 100, 100
                    );
                    INSERT INTO users(
                        user_id, username, email, display_name, password_digest,
                        email_verified_at, created_at, updated_at
                    ) VALUES (
                        'usr_verified', 'verified', 'verified@example.com', 'Verified',
                        'not-used-by-this-test', 90, 80, 90
                    );
                    """
                )

            AccountStore(database)

            with sqlite3.connect(database) as connection:
                pending = connection.execute(
                    "SELECT email_verified_at, email_verification_source FROM users WHERE user_id = 'usr_pending'"
                ).fetchone()
                verified = connection.execute(
                    "SELECT email_verified_at, email_verification_source FROM users WHERE user_id = 'usr_verified'"
                ).fetchone()

            self.assertEqual(pending, (None, None))
            self.assertEqual(verified, (90, None))


if __name__ == "__main__":
    unittest.main()
