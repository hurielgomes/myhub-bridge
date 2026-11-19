from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser import MyHubBrowser


SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" fill="#ff0000"/></svg>'''
JSON = '{"partes": {}, "animacoes": {}}'


class FakeDownload:
    def __init__(self, filename: str, content: str):
        self.suggested_filename = filename
        self.content = content

    def save_as(self, path: str):
        Path(path).write_text(self.content, encoding="utf-8")


class FakeDownloadContext:
    def __init__(self, download):
        self.value = download

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakePage:
    def __init__(self, downloads):
        self.downloads = list(downloads)

    def expect_download(self, timeout=10000):
        if not self.downloads:
            raise AssertionError("não havia download simulado")
        return FakeDownloadContext(self.downloads.pop(0))

    def wait_for_timeout(self, _ms):
        pass


class FakeLocator:
    def click(self, timeout=5000):
        return None


class CaptureDownloadTests(unittest.TestCase):
    def make_browser(self, downloads):
        browser = MyHubBrowser.__new__(MyHubBrowser)
        browser.cfg = {"timeouts": {"attachment_seconds": 1, "download_event_ms": 100}}
        browser.page = FakePage(downloads)
        browser._attachment_candidates = lambda: (
            [(FakeLocator(), {"text": "Download", "aria": "download file"}) for _ in downloads],
            [{"text": "Download", "aria": "download file"}],
        )
        return browser

    def test_generic_attachment_downloads_and_sniffs_svg_and_json(self):
        browser = self.make_browser([
            FakeDownload("file", SVG),
            FakeDownload("file", JSON),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            saved, diagnostics = browser._collect_downloads(
                ["svg", "json_sidecar"], Path(tmp), {"text": "", "code_blocks": []}
            )
            self.assertEqual({Path(x["filename"]).suffix for x in saved}, {".svg", ".json"})
            self.assertEqual(len(saved), 2)
            self.assertTrue(any("downloaded" in str(x) for x in diagnostics))

    def test_invalid_svg_download_is_rejected(self):
        browser = self.make_browser([FakeDownload("file.svg", "<svg><path></svg")])
        with tempfile.TemporaryDirectory() as tmp:
            saved, diagnostics = browser._collect_downloads(
                ["svg"], Path(tmp), {"text": "", "code_blocks": []}
            )
            self.assertEqual(saved, [])
            self.assertTrue(any("invalid_file" in str(x) or "unsupported_or_invalid_file" in str(x) for x in diagnostics))

    def test_valid_inline_svg_skips_download(self):
        browser = self.make_browser([])
        with tempfile.TemporaryDirectory() as tmp:
            saved, diagnostics = browser._collect_downloads(
                ["svg"], Path(tmp), {"text": SVG, "code_blocks": []}
            )
            self.assertEqual(saved, [])
            self.assertEqual(diagnostics, [])


if __name__ == "__main__":
    unittest.main()
