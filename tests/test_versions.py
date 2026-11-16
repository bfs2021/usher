"""The app's version must match everywhere, or the Home Screen app could keep an old copy (or reload in a loop)."""

import re
import unittest
from pathlib import Path

from usher import __version__

WEB = Path(__file__).resolve().parent.parent / "web"


class VersionTests(unittest.TestCase):
    def test_app_js_build(self):
        m = re.search(r'const BUILD = "([^"]+)"', (WEB / "updater.js").read_text())
        self.assertEqual(m and m.group(1), __version__)

    def test_service_worker_cache(self):
        m = re.search(r'const VERSION = "usher-([^"]+)"', (WEB / "sw.js").read_text())
        self.assertEqual(m and m.group(1), __version__)
