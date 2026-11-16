"""Posters are shown whole. A cut-off picture is never passed on as good, every poster URL comes from one place
(with the art epoch, so copies already on devices are skipped), and the app shows a poster only once it is whole."""

import re
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from usher import clients
from usher.clients import Arr, Jellyfin, cut_off_note, image_whole, read_image
from usher.server import Handler
from usher.titles import ART_EPOCH, arr_art, jf_art, remote_art
from tests.webjs import client_source

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(256)) * 8 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 300 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
WEBP = b"RIFF" + (300 - 8).to_bytes(4, "little") + b"WEBPVP8 " + b"\x01" * (300 - 16)

JF_WHOLE, JF_CUT_JPEG, JF_CUT_PNG, JF_SHORT, JF_FLAKY = "a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32


class Pictures(BaseHTTPRequestHandler):
    """Stands in for the media server and Sonarr: whole, cut-off and short pictures."""

    flaky_calls = 0

    def log_message(self, *a):
        pass

    def picture(self, body, ctype="image/jpeg", length=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length if length is not None else len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        if length is not None:
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)

    def do_GET(self):
        p = self.path
        if JF_WHOLE in p:
            return self.picture(JPEG)
        if JF_CUT_JPEG in p or "/mediacover/7/" in p.lower():
            return self.picture(JPEG[: len(JPEG) // 2])  # the server's own copy is cut off
        if JF_CUT_PNG in p:
            return self.picture(PNG[:200], "image/png")
        if JF_SHORT in p:
            return self.picture(JPEG[:500], length=len(JPEG))  # promises more than it sends, then hangs up
        if JF_FLAKY in p:
            Pictures.flaky_calls += 1
            return self.picture(JPEG if Pictures.flaky_calls > 1 else JPEG[:100])
        if "/mediacover/8/" in p.lower():
            return self.picture(JPEG)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


class Rig(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Pictures)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_port}"
        self.jf = Jellyfin(base, "k" * 32)
        self.sonarr = Arr("sonarr", base, "k" * 32)
        clients.CUT_OFF.clear()
        Pictures.flaky_calls = 0

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        clients.CUT_OFF.clear()


class WholePictures(unittest.TestCase):
    def test_formats(self):
        for good in (JPEG, JPEG + b"\x00\x00", PNG, WEBP, b"GIF89a....;", b"\x00\x00\x00 ftypavif..."):
            self.assertTrue(image_whole(good), good[:12])
        for cut in (b"", JPEG[:-2], JPEG[:300], PNG[:-4], PNG[:100], WEBP[:200], b"GIF89a...."):
            self.assertFalse(image_whole(cut), cut[:12])


class CutOffNeverPassedOn(Rig):
    def test_whole_picture_passes(self):
        self.assertEqual(self.jf.image(JF_WHOLE), (JPEG, "image/jpeg"))
        self.assertEqual(self.sonarr.image("8/poster-250.jpg"), (JPEG, "image/jpeg"))
        self.assertEqual(clients.CUT_OFF, {})

    def test_cut_off_jpeg_png_and_short_reply_give_nothing_and_one_warning_each(self):
        with self.assertLogs("usher", "WARNING") as logs:
            for jf_id in (JF_CUT_JPEG, JF_CUT_PNG, JF_SHORT):
                self.assertEqual(self.jf.image(jf_id), (None, None), jf_id)
                self.assertEqual(self.jf.image(jf_id), (None, None), jf_id)  # again: no second warning
        self.assertEqual(len(logs.records), 3)
        for jf_id in (JF_CUT_JPEG, JF_CUT_PNG, JF_SHORT):
            self.assertEqual(sum(jf_id in r.getMessage() for r in logs.records), 1)

    def test_retried_once(self):
        self.assertEqual(self.jf.image(JF_FLAKY), (JPEG, "image/jpeg"))
        self.assertEqual(Pictures.flaky_calls, 2)
        self.assertEqual(clients.CUT_OFF, {})

    def test_short_read_is_caught_not_raised(self):
        import http.client

        class Resp:
            headers = {"Content-Type": "image/jpeg", "Content-Length": "999"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                raise http.client.IncompleteRead(b"\xff\xd8", 997)

        with self.assertLogs("usher", "WARNING"):
            self.assertEqual(read_image(Resp, "remote picture"), (None, None))

    def test_healthz_note_once_it_repeats(self):
        self.assertIsNone(cut_off_note())
        with self.assertLogs("usher", "WARNING"):
            self.jf.image(JF_CUT_JPEG)
            self.assertIsNone(cut_off_note())
            self.jf.image(JF_CUT_JPEG)
        self.assertIn("2 cut-off pictures", cut_off_note())

    def handler(self):
        h = object.__new__(Handler)
        h.app = mock.Mock()
        h.app.jf, h.app.sonarr = self.jf, self.sonarr
        sent = {}
        h.send = lambda status, data, ctype=None, headers=None: sent.update(status=status, data=data, cache=(headers or {}).get("Cache-Control"))
        return h, sent

    def test_server_answers_fallback_or_short_404_never_the_cut_off_bytes(self):
        h, sent = self.handler()
        with self.assertLogs("usher", "WARNING"):
            h.path = f"/api/art/jellyfin/{JF_CUT_JPEG}?v=t&a={ART_EPOCH}"
            h.art(None, None, JF_CUT_JPEG)
            self.assertEqual((sent["status"], sent["cache"]), (404, "private, max-age=600"))

            # Sonarr's copy is cut off: TMDb's poster stands in, kept for a shorter time
            h.app.discover.arr_fallback.return_value = "https://image.tmdb.org/t/p/w342/x.jpg"
            with mock.patch("usher.artwork.remote_image", return_value=(JPEG, "image/jpeg")):
                h.path = f"/api/art/sonarr/7?v=t&a={ART_EPOCH}"
                h.art("sonarr", "7", None)
            self.assertEqual((sent["status"], sent["data"], sent["cache"]), (200, JPEG, "private, max-age=21600"))

            # ...and with no TMDb poster either: the short-lived 404, so the app shows the title and tries later
            h.app.discover.arr_fallback.return_value = None
            h.art("sonarr", "7", None)
            self.assertEqual((sent["status"], sent["cache"]), (404, "private, max-age=600"))

    def test_server_keeps_a_whole_picture_long(self):
        h, sent = self.handler()
        h.path = f"/api/art/jellyfin/{JF_WHOLE}?v=t&a={ART_EPOCH}"
        h.art(None, None, JF_WHOLE)
        self.assertEqual((sent["status"], sent["data"], sent["cache"]), (200, JPEG, "private, max-age=2592000"))


class OnePlaceMakesPosterUrls(unittest.TestCase):
    def test_every_url_carries_the_art_epoch(self):
        for url in (arr_art("show", {"id": 3}), arr_art("movie", 4), jf_art("ab" * 16, "t"), jf_art("ab" * 16), remote_art("f" * 24)):
            self.assertTrue(url.startswith("/api/art/") and url.endswith(f"a={ART_EPOCH}"), url)
        self.assertEqual(arr_art("movie", 4), arr_art("movie", {"id": 4}))

    def test_no_poster_url_built_anywhere_else(self):
        for path in Path("usher").rglob("*.py"):
            if path.name in ("titles.py", "server.py"):  # server.py only matches them
                continue
            self.assertNotIn("/api/art/", path.read_text(), path)
        self.assertNotIn("/api/art/", client_source())

    def test_downloads_and_looking_for_use_versioned_urls(self):
        src = "".join(p.read_text() for p in Path("usher/state").glob("*.py"))
        self.assertNotRegex(src, r'f"/api/art')


class OnePosterLoaderInTheApp(unittest.TestCase):
    js = client_source()

    def section(self):
        start = self.js.index("// ---- posters:")
        return start, self.js.index("// replaceChildren()", start)

    def test_artwork_images_only_come_from_the_poster_helper(self):
        start, end = self.section()
        outside = self.js[:start] + self.js[end:]
        self.assertNotIn("new Image", outside)
        for m in re.finditer(r'h\("img",\s*\{([^}]*)\}', outside):
            props = m.group(1)
            self.assertNotRegex(props, r'class:\s*[^,]*(poster|thumb)', props)
            self.assertNotRegex(props, r'\bart\b|\.art|fallback', props)
        self.assertNotRegex(outside, r'loading:\s*"lazy"')
        self.assertIn('poster(art, title, "poster", fallback)', self.js)  # shelf cards, incl. Home's media server rows
        self.assertIn("fallback: c.fallback", self.js)

    def test_shown_only_once_decoded_whole_with_one_fresh_retry(self):
        start, end = self.section()
        loader = self.js[start:end]
        for needed in ("decode()", "naturalWidth > 0", "naturalHeight > 0", '"r=1"', "replaceWith", "poster-fallback"):
            self.assertIn(needed, loader)

    def test_fade_respects_reduce_motion(self):
        css = Path("web/app.css").read_text()
        self.assertIn(".poster-in", css)
        reduce = css[css.index("prefers-reduced-motion: reduce") :]
        self.assertIn("animation: none", reduce[:200])


if __name__ == "__main__":
    unittest.main()
