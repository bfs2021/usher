"""Artwork is served at the size the device shows it: the device asks for a width (?w=), and the server picks the
nearest size the source makes itself (the media server resizes on request, Sonarr and Radarr keep two sizes, TMDb
has a set of widths). No picture is ever sent at full size, and a width that isn't a number changes nothing."""

import unittest
import urllib.error
from unittest import mock

from usher import artwork, clients
from usher.clients import Jellyfin
from usher.server import Handler
from usher.titles import ART_EPOCH

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
TMDB = "https://image.tmdb.org/t/p/w342/made-up.jpg"


class WidthAskedFor(unittest.TestCase):
    def test_rounded_up_to_a_known_width_and_capped(self):
        cases = {"w=240": 240, "w=250": 320, "w=1": 120, "w=5000": 960, "a=2&w=96&v=x": 120}
        for query, width in cases.items():
            self.assertEqual(artwork.asked_width(query), width, query)

    def test_anything_else_means_the_usual_size(self):
        for query in ("", "w=", "w=abc", "w=-5", "w=0", "w=1e3", "w=" + "9" * 40):
            self.assertIsNone(artwork.asked_width(query), query)


class SourceSizes(unittest.TestCase):
    def test_sonarr_and_radarr_keep_two_sizes(self):
        self.assertEqual(artwork.arr_file("7", None), "7/poster-250.jpg")
        self.assertEqual(artwork.arr_file("7", 240), "7/poster-250.jpg")
        self.assertEqual(artwork.arr_file("7", 320), "7/poster-500.jpg")

    def test_tmdb_widths(self):
        self.assertEqual(artwork.tmdb_sized(TMDB, None), TMDB)
        self.assertEqual(artwork.tmdb_sized(TMDB, 120), "https://image.tmdb.org/t/p/w154/made-up.jpg")
        self.assertEqual(artwork.tmdb_sized(TMDB, 240), TMDB)
        self.assertEqual(artwork.tmdb_sized(TMDB, 480), "https://image.tmdb.org/t/p/w500/made-up.jpg")
        self.assertEqual(artwork.tmdb_sized(TMDB, 960), "https://image.tmdb.org/t/p/w780/made-up.jpg")
        other = "https://artworks.thetvdb.com/banners/posters/made-up.jpg"
        self.assertEqual(artwork.tmdb_sized(other, 120), other, "only TMDb's addresses carry a width")

    def test_media_server_is_asked_for_the_width(self):
        jf = Jellyfin("http://127.0.0.1:9", "k" * 32, user_id="a" * 32)
        asked = []

        def refuse(req, *a, **k):
            asked.append(req.full_url)
            raise urllib.error.URLError("no server here")

        with mock.patch.object(clients, "_open", refuse):
            jf.image("b" * 32)
            jf.image("b" * 32, "Backdrop")
            jf.image("b" * 32, "Primary", 160)
        self.assertIn("fillWidth=320", asked[0])
        self.assertIn("fillWidth=640", asked[1])
        self.assertIn("fillWidth=160", asked[2])


class ServerAnswersAtTheSize(unittest.TestCase):
    def handler(self, path):
        h = object.__new__(Handler)
        h.app = mock.Mock()
        h.app.jf.image.return_value = (JPEG, "image/jpeg")
        h.app.sonarr.image.return_value = (JPEG, "image/jpeg")
        h.app.actions.remote_art = {"f" * 24: TMDB}
        h.path = path
        sent = {}
        h.send = lambda status, data, ctype=None, headers=None: sent.update(status=status, cache=(headers or {}).get("Cache-Control"))
        return h, sent

    def test_each_source_is_asked_for_the_size(self):
        h, sent = self.handler(f"/api/art/jellyfin/{'c' * 32}?v=t&a={ART_EPOCH}&w=240")
        h.art(None, None, "c" * 32)
        h.app.jf.image.assert_called_with("c" * 32, "Primary", 240)
        self.assertEqual((sent["status"], sent["cache"]), (200, "private, max-age=2592000"))

        h, sent = self.handler(f"/api/art/sonarr/7?v=t&a={ART_EPOCH}&w=480")
        h.art("sonarr", "7", None)
        h.app.sonarr.image.assert_called_with("7/poster-500.jpg")

        h, sent = self.handler(f"/api/art/remote/{'f' * 24}?a={ART_EPOCH}&w=120")
        with mock.patch("usher.artwork.remote_image", return_value=(JPEG, "image/jpeg")) as fetched:
            h.remote_art("f" * 24)
        fetched.assert_called_with("https://image.tmdb.org/t/p/w154/made-up.jpg")
        self.assertEqual((sent["status"], sent["cache"]), (200, "private, max-age=604800"))


if __name__ == "__main__":
    unittest.main()
