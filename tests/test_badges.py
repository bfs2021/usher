"""Search extras: scores, In library / Requested marks and where it's streaming."""

import unittest
from unittest import mock

from usher import config
from usher.actions import _ratings
from usher.discover import Discover

AU = config.Source("usher.toml (made up)", {"discover.region": "AU"})


class RatingTests(unittest.TestCase):
    def test_movie_scores(self):
        raw = {
            "imdb": {"value": 8.03, "votes": 900000},
            "rottenTomatoes": {"value": 91, "votes": 0},
            "metacritic": {"value": 0},
            "tmdb": {"value": 7.7, "votes": 20000},
        }
        self.assertEqual(_ratings(raw, "movie"), {"imdb": 8.0, "rt": 91})

    def test_few_votes_and_shows(self):
        self.assertEqual(_ratings({"imdb": {"value": 9.9, "votes": 3}}, "movie"), {})
        self.assertEqual(_ratings({"value": 8, "votes": 100}, "show"), {})
        self.assertEqual(_ratings(None, "movie"), {})


def _discover(movies=(), series=()):
    state = mock.Mock()
    state._raw = {"movies": list(movies), "series": list(series)}
    with mock.patch.dict("os.environ", {"TMDB_TOKEN": "t"}), mock.patch.object(config, "_active", AU):
        return Discover(mock.Mock(), state)


class MarkTests(unittest.TestCase):
    def test_have_and_asked(self):
        d = _discover(
            movies=[{"tmdbId": 1, "hasFile": True}, {"tmdbId": 2, "hasFile": False, "monitored": True}],
            series=[{"tmdbId": 3, "statistics": {"episodeFileCount": 4}}, {"tmdbId": 4, "statistics": {"episodeFileCount": 0}}],
        )
        items = [{"kind": k, "tmdb": t} for k, t in (("movie", 1), ("movie", 2), ("show", 3), ("show", 4), ("show", 5))]
        d._mark_library(items)
        self.assertEqual([(c["have"], c["asked"]) for c in items], [(True, False), (False, True), (True, False), (False, True), (False, False)])


class AnnotateTests(unittest.TestCase):
    def test_streaming_and_scores(self):
        d = _discover()
        info = {
            "vote_average": 8.24,
            "vote_count": 500,
            "watch/providers": {
                "results": {"AU": {"flatrate": [{"provider_name": "Netflix"}], "ads": [{"provider_name": "Netflix"}, {"provider_name": "SBS On Demand"}]}}
            },
        }
        d._details = lambda kind, tmdb: info if tmdb == 9 else {}
        res = [
            {"kind": "show", "tmdb": 9, "ratings": {}},
            {"kind": "movie", "tmdb": 8, "ratings": {"imdb": 7.0}},
            {"kind": "show", "tmdb": None, "ratings": {}},
        ]
        d.annotate(res)
        self.assertEqual(res[0]["streaming"], ["Netflix", "SBS On Demand"])
        self.assertEqual(res[0]["ratings"], {"tmdb": 8.2})
        self.assertEqual(res[1]["streaming"], [])
        self.assertEqual(res[1]["ratings"], {"imdb": 7.0})  # IMDb kept, TMDb not added
        self.assertNotIn("streaming", res[2])  # no TMDb id: no badge, still shown

    def test_slow_tmdb_gives_no_badge(self):
        import time

        d = _discover()
        d._details = lambda kind, tmdb: time.sleep(0.5) or {}
        res = [{"kind": "movie", "tmdb": 1, "ratings": {}}]
        d.annotate(res, wait=0.1)
        self.assertNotIn("streaming", res[0])

    def test_off_without_token(self):
        state = mock.Mock()
        with mock.patch.dict("os.environ", {"TMDB_TOKEN": ""}):
            d = Discover(mock.Mock(), state)
        res = [{"kind": "movie", "tmdb": 1}]
        self.assertIs(d.annotate(res), res)
        self.assertNotIn("streaming", res[0])


class DiscoverStreamingTests(unittest.TestCase):
    def test_each_title_looked_up_once_and_marked_in_every_row(self):
        d = _discover()
        calls = []
        info = {"watch/providers": {"results": {"AU": {"flatrate": [{"provider_name": "Stan"}]}}}}

        def details(kind, tmdb):
            calls.append((kind, tmdb))
            return info if tmdb == 1 else {}

        d._details = details
        a, b, c = {"kind": "show", "tmdb": 1}, {"kind": "show", "tmdb": 1}, {"kind": "movie", "tmdb": 2}
        d._mark_streaming([a, b, c])
        self.assertEqual(sorted(calls), [("movie", 2), ("show", 1)])
        self.assertEqual((a["streaming"], b["streaming"], c["streaming"]), (["Stan"], ["Stan"], []))

    def test_slow_title_left_unknown(self):
        import time

        d = _discover()
        d._details = lambda kind, tmdb: time.sleep(0.5) or {}
        card = {"kind": "movie", "tmdb": 1}
        d._mark_streaming([card], wait=0.1)
        self.assertNotIn("streaming", card)
