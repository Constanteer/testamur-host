from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.web import hosted_app


class MathHubAssetFreshnessTests(unittest.TestCase):
    def test_js_and_css_revalidate_after_host_redeploy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.js").write_text("console.log('new')", encoding="utf-8")
            (root / "app.css").write_text("body{}", encoding="utf-8")
            with patch.object(hosted_app, "_MATHHUB_ROOT", root):
                js = hosted_app._mathhub_static("app.js")
                css = hosted_app._mathhub_static("app.css")
        self.assertEqual(js["headers"]["Cache-Control"], "no-cache, must-revalidate")
        self.assertEqual(css["headers"]["Cache-Control"], "no-cache, must-revalidate")

    def test_index_versions_local_assets_with_mathhub_snapshot(self) -> None:
        snapshot_path = Path(hosted_app.__file__).resolve().parents[2] / "MATHHUB_SNAPSHOT"
        snapshot = snapshot_path.read_text(encoding="utf-8")
        match = re.search(r"\b[0-9a-f]{40}\b", snapshot, re.IGNORECASE)
        self.assertIsNotNone(match, "MATHHUB_SNAPSHOT must contain the canonical commit SHA")
        token = match.group(0)[:12]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(
                '<link rel="stylesheet" href="./styles.css">\n'
                '<script defer src="./app.js"></script>\n',
                encoding="utf-8",
            )
            with patch.object(hosted_app, "_MATHHUB_ROOT", root):
                response = hosted_app._mathhub_static("")

        html = response["body"].decode("utf-8")
        self.assertIn(f'href="./styles.css?v={token}"', html)
        self.assertIn(f'src="./app.js?v={token}"', html)
        self.assertNotIn("?v=current", html)


if __name__ == "__main__":
    unittest.main()
