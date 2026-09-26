from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from apps.web.identity import AccountStore


class AdminDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = AccountStore(Path(self.tempdir.name) / "accounts.sqlite3")

    def test_admin_snapshot_reports_accounts(self) -> None:
        _user, verification_token, _code = self.store.sign_up(
            username="verified",
            email="verified@example.com",
            password="test-password-long-enough",
            display_name="Verified User",
        )
        self.store.verify_email(token=verification_token)
        self.store.sign_up(
            username="pending",
            email="pending@example.com",
            password="test-password-long-enough",
            display_name="Pending User",
        )

        snapshot = self.store.admin_snapshot(now=int(time.time()))

        self.assertEqual(snapshot["summary"]["total_users"], 2)
        self.assertEqual(snapshot["summary"]["verified_users"], 1)
        self.assertEqual(snapshot["summary"]["unverified_users"], 1)
        self.assertEqual(snapshot["summary"]["signups_24h"], 2)
        self.assertGreaterEqual(snapshot["summary"]["active_users_24h"], 1)
        self.assertGreaterEqual(snapshot["summary"]["active_sessions"], 1)
        self.assertEqual(snapshot["summary"]["pending_verifications"], 1)


if __name__ == "__main__":
    unittest.main()
