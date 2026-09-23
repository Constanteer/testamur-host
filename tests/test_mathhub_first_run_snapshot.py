from __future__ import annotations

import unittest
from pathlib import Path


class HostedMathHubFirstRunSnapshotTests(unittest.TestCase):
    def test_hosted_snapshot_has_real_starter_claim_bootstrap(self) -> None:
        source = Path("mathhub_app/mathhub.py").read_text(encoding="utf-8")
        self.assertIn("AUTO_STARTER_CLAIMS", source)
        self.assertIn("ensure_starter_claim_task", source)
        self.assertIn("start_starter_claim_bootstrap()", source)
        self.assertIn('"imports":["Mathlib"]', source)

    def test_hosted_math_library_is_page_not_modal(self) -> None:
        source = Path("mathhub_app/library-browser-ui.js").read_text(encoding="utf-8")
        styles = Path("mathhub_app/library-browser-ui.css").read_text(encoding="utf-8")
        self.assertIn("state.view==='library'", source)
        self.assertIn('class="shell page library-page"', source)
        self.assertNotIn('class="library-backdrop"', source)
        self.assertIn(".library-workspace", styles)


if __name__ == "__main__":
    unittest.main()
