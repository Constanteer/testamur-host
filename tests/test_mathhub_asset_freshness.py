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

    def test_index_versions_local_assets_with_mathhub_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(
                '<link rel="stylesheet" href="./styles.css">\n'
                '<script defer src="./app.js"></script>\n',
                encoding="utf-8",
            )
            snapshot = Path(hosted_app.__file__).resolve().parents[2] / "MATHHUB_SNAPSHOT"
            with patch.object(hosted_app, "_MATHHUB_ROOT", root), patch.object(
                Path, "read_text", autospec=True
            ) as read_text:
                original_read_text = Path.read_text

                def read(path: Path, *args, **kwargs):
                    if path == snapshot:
                        return "Constanteer/Mathub main @ ddf6fe15b3f7868de55718a5f15fa6260bcc70d4\n"
                    return original_read_text(path, *args, **kwargs)

                read_text.side_effect = read
                response = hosted_app._mathhub_static("")

        html = response["body"].decode("utf-8")
        self.assertIn('href="./styles.css?v=ddf6fe15b3f7"', html)
        self.assertIn('src="./app.js?v=ddf6fe15b3f7"', html)
        self.assertNotIn("?v=current", html)


if __name__ == "__main__":
    unittest.main()
