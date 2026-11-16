"""How Usher signs in to Jellyfin and TMDb, and TMDb's credit in About."""

import io
import json
import os
import re
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from usher import __version__
from usher.clients import Jellyfin, jellyfin_device_id
from usher.discover import Discover
from tests.webjs import client_source

ROOT = Path(__file__).resolve().parent.parent
DEVICE = "d" * 32


class JellyfinHeaderTests(unittest.TestCase):
    def serve(self):
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"Id": "s" * 32}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}", seen

    def test_sends_the_full_header_and_no_key_in_the_address(self):
        base, seen = self.serve()
        Jellyfin(base, "k" * 32, "8" * 32, DEVICE).get("/System/Info/Public")
        path, auth = seen[0]
        self.assertEqual(auth, f'MediaBrowser Token="{"k" * 32}", Client="Usher", Device="Usher%20server", DeviceId="{DEVICE}", Version="{__version__}"')
        self.assertNotIn("api_key", path.lower())
        self.assertNotIn("k" * 32, path)

    def test_header_values_are_url_encoded(self):
        jf = Jellyfin("http://x", 'a"b, c', "8" * 32, DEVICE)
        self.assertIn('Token="a%22b%2C%20c"', jf._authorization())

    def test_device_id_is_made_once_and_kept_privately(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = jellyfin_device_id(tmp)
            self.assertRegex(first, r"^[0-9a-f]{32}$")
            self.assertEqual(jellyfin_device_id(tmp), first)
            self.assertEqual(stat.S_IMODE(os.stat(Path(tmp) / "jellyfin-device-id").st_mode), 0o600)


class TmdbCredentialTests(unittest.TestCase):
    def discover(self, token):
        with mock.patch.dict("os.environ", {"TMDB_TOKEN": token}):
            return Discover(mock.Mock(), mock.Mock())

    def fetch(self, d):
        sent = []

        def fake_open(req, timeout=None):
            sent.append(req)
            return io.BytesIO(b"{}")

        with mock.patch("usher.discover.urllib.request.urlopen", fake_open):
            d._get("/trending/all/week")
        return sent[0]

    def test_read_access_token_goes_in_a_bearer_header(self):
        token = "eyJ" + "x" * 200
        req = self.fetch(self.discover(token))
        self.assertEqual(req.get_header("Authorization"), f"Bearer {token}")
        self.assertNotIn("api_key", req.full_url)
        self.assertNotIn(token, req.full_url)

    def test_old_api_key_still_works_and_asks_once_for_the_token(self):
        d = self.discover("0123456789abcdef" * 2)
        with self.assertLogs("usher.discover", "WARNING") as logs:
            req = self.fetch(d)
            d._cache.clear()
            self.fetch(d)
        self.assertIn("api_key=", req.full_url)
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Read Access Token", logs.output[0])
        self.assertNotIn("0123456789abcdef", logs.output[0])


class AboutCreditTests(unittest.TestCase):
    def test_about_shows_tmdb_logo_notice_and_justwatch(self):
        js = client_source()
        about = js[js.index("aboutSection() {") :]
        about = about[: about.index("\n  }\n")]
        self.assertIn('"/icons/tmdb.svg"', about)
        self.assertIn("This product uses the TMDB API but is not endorsed or certified by TMDB.", about)
        self.assertIn("JustWatch", about)
        self.assertIn("this.aboutSection()", js)

    def test_logo_is_tmdbs_own_file_cached_offline_and_smaller_than_usher(self):
        svg = (ROOT / "web/icons/tmdb.svg").read_text(encoding="utf-8")
        self.assertTrue(svg.startswith("<svg") and "<script" not in svg)
        self.assertIn('"/icons/tmdb.svg"', (ROOT / "web/sw.js").read_text(encoding="utf-8"))
        css = (ROOT / "web/status.css").read_text(encoding="utf-8")
        logo = int(re.search(r"\.about-tmdb \{[^}]*height: (\d+)px", css).group(1))
        icon = int(re.search(r"\.about-icon \{[^}]*height: (\d+)px", css).group(1))
        self.assertLess(logo, icon)


if __name__ == "__main__":
    unittest.main()
