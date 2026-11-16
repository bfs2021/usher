"""TMDb's poster stands in when Sonarr or Radarr has none (often a show that hasn't aired yet)."""

import unittest
from unittest import mock

from usher import config
from usher.clients import ApiError
from usher.discover import IMG, Discover

AU = config.Source("usher.toml (made up)", {"discover.region": "AU"})


def _discover(token="t"):
    actions = mock.Mock()
    actions._art_key = lambda url: f"key:{url}" if url else None
    with mock.patch.dict("os.environ", {"TMDB_TOKEN": token}), mock.patch.object(config, "_active", AU):
        return Discover(actions, mock.Mock())


class ArrFallbackTests(unittest.TestCase):
    def test_show_by_tmdb_id(self):
        d = _discover()
        d.actions.sonarr.get.return_value = {"tmdbId": 77, "tvdbId": 5}
        d._details = mock.Mock(return_value={"poster_path": "/merc.jpg"})
        self.assertEqual(d.arr_fallback("sonarr", 3), f"{IMG}/w342/merc.jpg")
        d.actions.sonarr.get.assert_called_once_with("series/3")
        d._details.assert_called_once_with("show", 77)

    def test_show_by_tvdb_id_when_no_tmdb_id(self):
        d = _discover()
        d.actions.sonarr.get.return_value = {"tmdbId": 0, "tvdbId": 5}
        d._get = mock.Mock(return_value={"tv_results": [{"id": 77, "poster_path": "/found.jpg"}]})
        self.assertEqual(d.arr_fallback("sonarr", 3), f"{IMG}/w342/found.jpg")
        self.assertEqual(d._get.call_args[0][0], "/find/5")

    def test_movie(self):
        d = _discover()
        d.actions.radarr.get.return_value = {"tmdbId": 42}
        d._details = mock.Mock(return_value={"poster_path": "/m.jpg"})
        self.assertEqual(d.arr_fallback("radarr", 9), f"{IMG}/w342/m.jpg")
        d._details.assert_called_once_with("movie", 42)

    def test_misses_are_remembered(self):
        d = _discover()
        d.actions.radarr.get.return_value = {"tmdbId": 42}
        d._details = mock.Mock(return_value={})
        self.assertIsNone(d.arr_fallback("radarr", 9))
        self.assertIsNone(d.arr_fallback("radarr", 9))
        self.assertEqual(d._details.call_count, 1)

    def test_errors_give_no_poster(self):
        d = _discover()
        d.actions.sonarr.get.side_effect = ApiError("sonarr: down")
        self.assertIsNone(d.arr_fallback("sonarr", 3))

    def test_off_without_token(self):
        d = _discover(token="")
        d.actions.radarr.get.return_value = {"tmdbId": 42}
        self.assertIsNone(d.arr_fallback("radarr", 9))


class SearchPosterTests(unittest.TestCase):
    def test_search_result_without_art_gets_tmdb_poster(self):
        d = _discover()
        d._details = lambda kind, tmdb: {"poster_path": "/p.jpg"}
        res = [{"kind": "show", "tmdb": 1, "ratings": {}, "art": None}, {"kind": "show", "tmdb": 2, "ratings": {}, "art": "/api/art/remote/own"}]
        d.annotate(res)
        self.assertEqual(res[0]["art"], f"key:{IMG}/w342/p.jpg")
        self.assertEqual(res[1]["art"], "/api/art/remote/own")  # their own picture is kept


if __name__ == "__main__":
    unittest.main()
