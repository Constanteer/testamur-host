from __future__ import annotations

import unittest
from pathlib import Path


class EmailVerificationSessionImplementationTests(unittest.TestCase):
    def test_durable_verification_guard_lives_in_account_store(self) -> None:
        identity_source = Path("apps/web/identity.py").read_text(encoding="utf-8")
        package_source = Path("apps/web/__init__.py").read_text(encoding="utf-8")

        method_start = identity_source.index("    def user_for_session(")
        method_end = identity_source.index("\n    def sign_out(", method_start)
        method_source = identity_source[method_start:method_end]

        self.assertIn('row["email_verified_at"] is None', method_source)
        self.assertIn('DELETE FROM sessions WHERE session_hash = ?', method_source)

        # The invariant belongs to AccountStore itself. Importing apps.web must not
        # be required to monkey-patch authentication correctness into the store.
        self.assertNotIn("AccountStore.user_for_session =", package_source)
        self.assertNotIn("_verified_user_for_session", package_source)


if __name__ == "__main__":
    unittest.main()
