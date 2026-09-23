from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
