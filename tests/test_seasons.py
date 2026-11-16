"""Seasons of a show already in the library: what's here, and asking for one season."""

import unittest
from unittest import mock

from usher.actions import Actions, season_of, season_rows

NOW = "2026-06-01T00:00:00"


def ep(i, season, aired, has=False, mon=False):
    return {"id": i, "seasonNumber": season, "hasFile": has, "monitored": mon, "airDateUtc": "2026-01-01T00:00:00Z" if aired else "2026-12-01T00:00:00Z"}


class SeasonOfTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(season_of("season:3"), 3)
        for c in ("latest", "start", "", None, "season:", "season:x"):
            self.assertIsNone(season_of(c))


class RowTests(unittest.TestCase):
    def test_counts(self):
        eps = [
            ep(1, 0, True),
            ep(2, 1, True, has=True),
            ep(3, 1, True, has=True),
            ep(4, 2, True),
            ep(5, 2, True, has=True),
            ep(6, 3, False),
            {"id": 7, "seasonNumber": 3, "hasFile": False},
        ]
        seasons = [{"seasonNumber": 1, "monitored": False}, {"seasonNumber": 2, "monitored": True}, {"seasonNumber": 3, "monitored": False}]
        rows = season_rows(seasons, eps, [{"choice": "season:3"}, {"choice": "latest"}], NOW)
        self.assertEqual([r["number"] for r in rows], [1, 2, 3])  # specials left out
        self.assertEqual([(r["aired"], r["have"], r["upcoming"]) for r in rows], [(2, 2, 0), (2, 1, 0), (0, 0, 2)])
        self.assertEqual([r["wanted"] for r in rows], [False, True, False])
        self.assertEqual([r["asked"] for r in rows], [False, False, True])


class GetSeasonTests(unittest.TestCase):
    def setUp(self):
        self.series = {
            "id": 5,
            "tvdbId": 77,
            "title": "Scrubs",
            "year": 2001,
            "monitored": True,
            "seasons": [{"seasonNumber": 1, "monitored": False}, {"seasonNumber": 2, "monitored": False}],
        }
        self.sonarr = mock.Mock()
        self.sonarr.get.side_effect = lambda path, **kw: (
            dict(self.series) if path == "series/5" else [ep(10, 2, True, mon=False), ep(11, 2, True, has=True), ep(12, 1, True)]
        )
        self.store = mock.Mock()
        self.store.open_requests.return_value = []
        self.store.add_request.return_value = 42
        self.a = Actions(self.sonarr, mock.Mock(), self.store, mock.Mock())

    def test_asks_for_one_season(self):
        res = self.a.get_season(5, 2, {"user_id": 1, "username": "u"})
        self.assertEqual(res["rid"], 42)
        calls = self.sonarr.send.call_args_list
        put = calls[0].args
        self.assertEqual(put[:2], ("PUT", "series/5"))
        self.assertEqual([s["monitored"] for s in put[2]["seasons"]], [False, True])
        self.assertEqual(calls[1].args, ("PUT", "episode/monitor", {"episodeIds": [10], "monitored": True}))
        self.assertEqual(calls[2].args, ("POST", "command", {"name": "SeasonSearch", "seriesId": 5, "seasonNumber": 2}))
        self.store.add_request.assert_called_once_with("show", 5, 77, "Scrubs · Season 2", 2001, "season:2", 1)

    def test_reuses_open_request(self):
        self.store.open_requests.return_value = [{"id": 9, "kind": "show", "arr_id": 5, "choice": "season:2"}]
        self.assertEqual(self.a.get_season(5, 2, {"user_id": 1, "username": "u"})["rid"], 9)
        self.store.add_request.assert_not_called()

    def test_unknown_season(self):
        from usher.actions import Problem

        with self.assertRaises(Problem):
            self.a.get_season(5, 9, {"user_id": 1, "username": "u"})
        with self.assertRaises(Problem):
            self.a.get_season(5, 0, {"user_id": 1, "username": "u"})


if __name__ == "__main__":
    unittest.main()


class SeasonRequestStateTests(unittest.TestCase):
    """A one-season request counts only that season, and is searched again with SeasonSearch."""

    def _state(self, req):
        from tests.test_state_basics import make_state

        st = make_state()
        st.store = mock.Mock()
        st.store.recent_requests.return_value = [req]
        st.store.open_requests.return_value = [req]
        return st

    def test_progress_counts_the_season(self):
        import time as _t
        from datetime import datetime, timezone

        now = _t.time()
        req = {
            "id": 1,
            "kind": "show",
            "arr_id": 5,
            "ext_id": 77,
            "title": "Scrubs · Season 2",
            "year": 2001,
            "choice": "season:2",
            "created": now - 600,
            "done_at": None,
            "jf_id": "a" * 32,
            "display": "J",
            "searched": None,
        }
        st = self._state(req)
        series = {
            "id": 5,
            "statistics": {"episodeFileCount": 20, "episodeCount": 30},
            "seasons": [
                {"seasonNumber": 1, "statistics": {"episodeFileCount": 20, "episodeCount": 20}},
                {"seasonNumber": 2, "statistics": {"episodeFileCount": 0, "episodeCount": 10}},
            ],
        }
        r = dict(st._raw, movies=[], series=[series])
        out = st._requests(r, datetime.now(timezone.utc), [])
        self.assertEqual(out[0]["step"], 0)  # not "Ready": season 2 has nothing yet
        series["seasons"][1]["statistics"]["episodeFileCount"] = 4
        out = st._requests(r, datetime.now(timezone.utc), [])
        self.assertEqual(out[0]["step"], 4)
        self.assertIn("4 of 10", out[0]["detail"])

    def test_research_uses_season_search(self):
        import time as _t

        RESEARCH_FIRST = 3600  # retries.first_search_after_minutes default
        req = {"id": 1, "kind": "show", "arr_id": 5, "title": "T", "choice": "season:2", "created": _t.time() - RESEARCH_FIRST - 5, "searched": None}
        st = self._state(req)
        st.sonarr.send.return_value = {}
        st._research({"requests": [{"rid": 1, "searching": True}]})
        st.sonarr.send.assert_called_once_with("POST", "command", {"name": "SeasonSearch", "seriesId": 5, "seasonNumber": 2})
