"""Likes and dislikes: each account's own opinions of titles, Picked for you that follows them, and dislikes that
leave that account's Discover at once (usher/store.py, usher/opinion_routes.py, usher/picks.py, the details sheet and
Settings → Account)."""

import ast
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.webjs import WEB
from usher.cache import TTLCache
from usher.clients import ApiError
from usher.discover import Discover
from usher.server import Handler
from usher.store import OPINIONS_MAX, Store

ART = "https://image.tmdb.org/t/p/w342/made-up.jpg"


def card(tmdb, kind="movie", genres=(35,)):
    return {"kind": kind, "tmdb": tmdb, "title": f"Made-up {tmdb}", "year": 2020, "art": ART, "genre_ids": list(genres)}


class StoreCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name))
        self.addCleanup(self.store._db.close)
        self.store.add_user("alex", "Alex", "x")
        self.store.add_user("sam", "Sam", "x")
        self.alex = self.store.user_by_name("alex")["id"]
        self.sam = self.store.user_by_name("sam")["id"]

    def mine(self, uid):
        return [(o["kind"], o["tmdb"], o["liked"]) for o in self.store.opinions(uid)]


class StoreTests(StoreCase):
    def test_set_change_and_clear(self):
        self.store.set_opinion(self.alex, "movie", 101, True)
        self.assertEqual(self.mine(self.alex), [("movie", 101, 1)])
        self.store.set_opinion(self.alex, "movie", 101, False)  # one opinion per title: a dislike replaces the like
        self.assertEqual(self.mine(self.alex), [("movie", 101, 0)])
        self.assertTrue(self.store.clear_opinion(self.alex, "movie", 101))
        self.assertEqual(self.mine(self.alex), [])
        self.assertFalse(self.store.clear_opinion(self.alex, "movie", 101))

    def test_listed_newest_first(self):
        self.store.set_opinion(self.alex, "movie", 1, True)
        self.store.set_opinion(self.alex, "show", 2, False)
        self.store.run("UPDATE opinions SET at = at - 100 WHERE tmdb = 2")
        self.store.set_opinion(self.alex, "movie", 3, True)
        self.store.run("UPDATE opinions SET at = at + 100 WHERE tmdb = 3")
        self.assertEqual([t for _, t, _ in self.mine(self.alex)], [3, 1, 2])

    def test_each_account_has_its_own(self):
        self.store.set_opinion(self.alex, "movie", 101, True)
        self.store.set_opinion(self.sam, "movie", 101, False)
        self.store.clear_opinion(self.sam, "movie", 101)
        self.assertEqual(self.mine(self.alex), [("movie", 101, 1)])
        self.assertEqual(self.mine(self.sam), [])

    def test_removing_an_account_removes_its_opinions(self):
        self.store.set_opinion(self.alex, "movie", 101, True)
        self.store.set_opinion(self.sam, "show", 202, False)
        self.assertTrue(self.store.remove_user("alex"))  # what `usher-user remove` does
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM opinions WHERE user_id = ?", (self.alex,))["n"], 0)
        self.assertEqual(self.mine(self.sam), [("show", 202, 0)])

    def test_a_new_one_past_the_limit_is_refused_and_nothing_old_is_dropped(self):
        self.assertEqual(OPINIONS_MAX, 500)
        with mock.patch("usher.store.OPINIONS_MAX", 3):
            self.assertTrue(all(self.store.set_opinion(self.alex, "movie", n, True) for n in (1, 2, 3)))
            self.assertFalse(self.store.set_opinion(self.alex, "movie", 4, True))
            self.assertTrue(self.store.set_opinion(self.alex, "movie", 2, False), "changing one already kept is fine")
            self.assertTrue(self.store.set_opinion(self.sam, "movie", 4, True), "the limit is per account")
            self.store.clear_opinion(self.alex, "movie", 1)
            self.assertTrue(self.store.set_opinion(self.alex, "movie", 4, True))
        self.assertEqual(sorted(t for _, t, _ in self.mine(self.alex)), [2, 3, 4])

    def test_requests_of_one_account_newest_first(self):
        self.store.add_request("movie", 1, 11, "Made-up Film", 2020, "", self.alex)
        self.store.add_request("show", 5, 300, "Made-up Show", 2021, "latest", self.alex)
        self.store.add_request("show", 5, 300, "Made-up Show", 2021, "", self.alex)  # a season asked for later
        self.store.add_request("movie", 2, 22, "Someone Else's", 2020, "", self.sam)
        self.assertEqual([(r["kind"], r["arr_id"], r["ext_id"]) for r in self.store.requested_by(self.alex)], [("show", 5, 300), ("movie", 1, 11)])


def session(uid, name):
    return {"user_id": uid, "username": name, "csrf": "c", "id_hash": f"h-{name}"}


class RouteTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.app = mock.Mock(store=self.store)
        self.app.discover.describe.side_effect = lambda keys: {k: {"title": f"Made-up {k[1]}", "year": 2020, "art": "/api/art/remote/a"} for k in keys}

    def call(self, method, path, who=None, data=None):
        h = Handler.__new__(Handler)
        h.path, h.headers, h.command, h.app = path, {"X-CSRF-Token": "c"}, method, self.app
        h.host_ok = h.same_origin = lambda: True
        h.session = lambda: who
        h.body_json = lambda: data
        h.send = mock.Mock()
        getattr(h, f"do_{method}")()
        return h.send.call_args.args[0], h.send.call_args.args[1]

    def alex_s(self):
        return session(self.alex, "alex")

    def test_sign_in_needed(self):
        self.assertEqual(self.call("GET", "/api/opinions")[0], 401)
        self.assertEqual(self.call("POST", "/api/opinion", data={"kind": "movie", "tmdb": 1, "opinion": "like"})[0], 401)
        self.assertEqual(self.store.q("SELECT * FROM opinions"), [])

    def test_every_value_is_checked(self):
        bad = [
            {},
            {"kind": "person", "tmdb": 1, "opinion": "like"},
            {"kind": "movie", "tmdb": 0, "opinion": "like"},
            {"kind": "movie", "tmdb": -4, "opinion": "like"},
            {"kind": "movie", "tmdb": "12; DROP TABLE opinions", "opinion": "like"},
            {"kind": "movie", "tmdb": True, "opinion": "like"},
            {"kind": "movie", "tmdb": 1.5, "opinion": "like"},
            {"kind": "movie", "tmdb": 2**40, "opinion": "like"},
            {"kind": "movie", "tmdb": 1, "opinion": "love"},
            {"kind": ["movie"], "tmdb": 1, "opinion": "like"},
        ]
        for data in bad:
            status, body = self.call("POST", "/api/opinion", self.alex_s(), data)
            self.assertEqual(status, 400, data)
            self.assertIn("error", body)
        self.assertEqual(self.store.q("SELECT * FROM opinions"), [])
        self.app.discover.opinion_changed.assert_not_called()

    def test_like_dislike_and_clear(self):
        status, body = self.call("POST", "/api/opinion", self.alex_s(), {"kind": "show", "tmdb": 202, "opinion": "like"})
        self.assertEqual((status, body["opinion"]), (200, "like"))
        self.app.discover.opinion_changed.assert_called_with(self.alex)  # that account's Picked for you is rebuilt
        status, body = self.call("POST", "/api/opinion", self.alex_s(), {"kind": "show", "tmdb": 202, "opinion": "dislike"})
        self.assertEqual(body["opinion"], "dislike")
        self.assertEqual(self.mine(self.alex), [("show", 202, 0)])
        status, body = self.call("POST", "/api/opinion", self.alex_s(), {"kind": "show", "tmdb": 202, "opinion": "clear"})
        self.assertEqual((status, body["opinion"]), (200, None))
        self.assertEqual(self.mine(self.alex), [])

    def test_only_your_own_are_listed_and_changed(self):
        self.call("POST", "/api/opinion", self.alex_s(), {"kind": "movie", "tmdb": 101, "opinion": "like"})
        self.call("POST", "/api/opinion", self.alex_s(), {"kind": "show", "tmdb": 202, "opinion": "dislike"})
        sam = session(self.sam, "sam")
        self.assertEqual(self.call("GET", "/api/opinions", sam), (200, {"items": [], "next": None}))
        self.call("POST", "/api/opinion", sam, {"kind": "movie", "tmdb": 101, "opinion": "clear"})  # clears Sam's own, not Alex's
        status, body = self.call("GET", "/api/opinions", self.alex_s())
        self.assertEqual(status, 200)
        rows = sorted(body["items"], key=lambda r: r["tmdb"])
        self.assertEqual(
            rows,
            [
                {"kind": "movie", "tmdb": 101, "opinion": "like", "title": "Made-up 101", "year": 2020, "art": "/api/art/remote/a"},
                {"kind": "show", "tmdb": 202, "opinion": "dislike", "title": "Made-up 202", "year": 2020, "art": "/api/art/remote/a"},
            ],
        )

    def test_past_the_limit_a_like_is_refused_plainly(self):
        with mock.patch("usher.store.OPINIONS_MAX", 2):
            for n in (1, 2):
                self.call("POST", "/api/opinion", self.alex_s(), {"kind": "movie", "tmdb": n, "opinion": "like"})
            self.app.discover.opinion_changed.reset_mock()
            status, body = self.call("POST", "/api/opinion", self.alex_s(), {"kind": "movie", "tmdb": 3, "opinion": "dislike"})
        self.assertEqual(status, 400)
        self.assertIn("Undo some", body["error"])
        self.assertEqual(len(self.mine(self.alex)), 2)
        self.app.discover.opinion_changed.assert_not_called()

    def test_the_list_comes_a_page_at_a_time(self):
        for n in range(1, 121):
            self.store.set_opinion(self.alex, "movie", n, n % 2 == 0)
        status, first = self.call("GET", "/api/opinions", self.alex_s())
        self.assertEqual((status, len(first["items"])), (200, 50))
        self.assertEqual(len(self.app.discover.describe.call_args.args[0]), 50, "at most a page of TMDb lookups per ask")
        undone = first["items"][3]
        self.store.clear_opinion(self.alex, undone["kind"], undone["tmdb"])  # an Undo before Show more shifts nothing
        status, second = self.call("GET", f"/api/opinions?after={first['next']}", self.alex_s())
        self.assertEqual(len(second["items"]), 50)
        status, third = self.call("GET", f"/api/opinions?after={second['next']}", self.alex_s())
        self.assertEqual((len(third["items"]), third["next"]), (20, None))
        seen = [it["tmdb"] for page in (first, second, third) for it in page["items"]]
        self.assertEqual(len(seen), len(set(seen)), "no title on two pages")
        self.assertEqual(set(seen), set(range(1, 121)), "none skipped")
        for bad in ("abc", "1-", "-1-2", "1-2-3", "1" * 13 + "-1"):
            self.assertEqual(self.call("GET", f"/api/opinions?after={bad}", self.alex_s())[0], 400, bad)

    def test_details_and_search_carry_your_opinion(self):
        self.store.set_opinion(self.alex, "movie", 101, False)
        self.app.discover.item.side_effect = lambda kind, tmdb: {"kind": kind, "tmdb": tmdb, "title": "Made-up"}
        self.assertEqual(self.call("GET", "/api/discover/item?kind=movie&id=101", self.alex_s())[1]["opinion"], "dislike")
        self.assertIsNone(self.call("GET", "/api/discover/item?kind=movie&id=101", session(self.sam, "sam"))[1]["opinion"])
        self.app.actions.search.return_value = {"results": [{"kind": "movie", "tmdb": 101}, {"kind": "show", "tmdb": None}], "failed": []}
        results = self.call("GET", "/api/search?q=made&extras=0", self.alex_s())[1]["results"]
        self.assertEqual([r["opinion"] for r in results], ["dislike", None])

    def test_discover_is_served_for_the_account(self):
        self.app.discover.rows.return_value = {"enabled": True, "rows": []}
        self.call("GET", "/api/discover", self.alex_s())
        self.app.discover.rows.assert_called_once_with(self.alex)


class DiscoverCase(StoreCase):
    """Discover with made-up TMDb recommendations: RECS maps a seed to the cards TMDb recommends for it."""

    RECS = {}

    def make(self, data_dir=None, series=()):
        actions = mock.Mock()
        actions.store = self.store
        actions._art_key = lambda url: f"/api/art/remote/{url}" if url else None
        state = mock.Mock()
        state._raw = {"movies": [], "series": list(series)}
        state.jf.watched.return_value = []
        with mock.patch.dict("os.environ", {"TMDB_TOKEN": "t"}):
            d = Discover(actions, state, data_dir)
        self.asked = []

        def recs(kind, tmdb):
            self.asked.append((kind, tmdb))
            return [dict(c) for c in self.RECS.get((kind, tmdb), [])]

        d._recs = recs
        d._details = lambda kind, tmdb: {}
        shared = [
            {"id": "foryou", "name": "Picked for you", "items": [card(900), card(901)]},
            {"id": "trending", "name": "Trending this week", "items": [card(50), card(51), card(500, "show")]},
        ]
        d._rows = (time.monotonic(), shared)
        self.addCleanup(self.settle, d)  # runs before the store is closed
        return d

    @staticmethod
    def settle(d, timeout=5.0):
        """Waits for every account's row being built in the background."""
        deadline = time.monotonic() + timeout
        while d._picks_jobs and time.monotonic() < deadline:
            time.sleep(0.02)

    def like(self, uid, *ids, liked=True):
        for tmdb in ids:
            self.store.set_opinion(uid, "movie", tmdb, liked)

    @staticmethod
    def row(out, rid):
        return next(([c["tmdb"] for c in r["items"]] for r in out["rows"] if r["id"] == rid), None)


class PicksTests(DiscoverCase):
    RECS = {
        ("movie", 1): [card(11), card(12)],
        ("movie", 2): [card(12), card(13)],
        ("movie", 3): [card(14)],
        ("movie", 4): [card(41)],
        ("movie", 5): [card(42)],
        ("movie", 6): [card(43), card(50)],
        ("movie", 7): [card(77)],
    }

    def test_picked_for_you_follows_each_account(self):
        d = self.make()
        self.like(self.alex, 1, 2, 3)
        self.like(self.sam, 4, 5, 6)
        alex, sam = self.row(d.rows(self.alex), "foryou"), self.row(d.rows(self.sam), "foryou")
        self.assertEqual(alex[0], 12)  # recommended by two of Alex's likes
        self.assertEqual(set(alex), {11, 12, 13, 14})
        self.assertEqual(set(sam), {41, 42, 43, 50})
        self.assertEqual(self.row(d.rows(), "foryou"), [900, 901], "without an account, the shared row")
        self.assertIsInstance(d._picks, TTLCache)  # kept in memory only

    def test_fewer_than_three_seeds_falls_back_to_the_shared_row(self):
        d = self.make()
        self.like(self.alex, 1, 2)
        self.assertEqual(self.row(d.rows(self.alex), "foryou"), [900, 901])

    def test_a_dislike_leaves_that_accounts_rows_at_once(self):
        d = self.make()
        self.like(self.sam, 4, 5, 6)
        d.rows(self.alex), d.rows(self.sam)
        self.store.set_opinion(self.alex, "movie", 50, False)
        self.store.set_opinion(self.alex, "show", 500, False)
        self.store.set_opinion(self.alex, "movie", 900, False)
        alex = d.rows(self.alex)
        self.assertEqual(self.row(alex, "trending"), [51])
        self.assertEqual(self.row(alex, "foryou"), [901])
        self.assertIn(50, self.row(d.rows(self.sam), "trending"))
        self.assertIn(50, self.row(d.rows(self.sam), "foryou"))

    def test_a_new_account_given_a_removed_accounts_id_starts_afresh(self):
        d = self.make()
        self.like(self.sam, 4, 5, 6)
        self.assertEqual(set(self.row(d.rows(self.sam), "foryou")), {41, 42, 43, 50})
        self.store.remove_user("sam")
        self.store.add_user("kai", "Kai", "x")
        self.store.run("UPDATE users SET created = created + 60 WHERE username = 'kai'")  # made later
        kai = self.store.user_by_name("kai")["id"]
        self.assertEqual(kai, self.sam, "SQLite gives the removed account's id to the next one")
        self.assertEqual(self.row(d.rows(kai), "foryou"), [900, 901])

    def test_a_dislike_is_never_a_seed_nor_a_pick(self):
        d = self.make()
        self.like(self.alex, 1, 2, 3)
        self.store.add_request("movie", 9, 7, "Made-up Film", 2020, "", self.alex)
        self.store.set_opinion(self.alex, "movie", 7, False)
        self.store.set_opinion(self.alex, "movie", 13, False)
        mine = self.row(d.rows(self.alex), "foryou")
        self.assertNotIn(("movie", 7), self.asked)
        self.assertNotIn(13, mine)

    def test_seeds_are_likes_then_requests_at_most_ten(self):
        series = [{"id": 5, "tvdbId": 300, "tmdbId": 3000}, {"id": 6, "tvdbId": 400}]
        d = self.make(series=series)
        found = []

        def get(path, **params):
            found.append(path)
            return {"tv_results": [{"id": 4000}]} if path == "/find/400" else {}

        d._get = get
        self.store.add_request("movie", 1, 11, "Made-up Film", 2020, "", self.alex)
        self.store.add_request("show", 5, 300, "Made-up Show", 2021, "", self.alex)
        self.store.add_request("show", 6, 400, "Another Show", 2022, "", self.alex)
        self.store.run("UPDATE requests SET created = created - id")
        self.like(self.alex, 21)
        self.assertEqual(d._seeds(self.alex), [("movie", 21, 2.0), ("movie", 11, 1.0), ("show", 3000, 1.0), ("show", 4000, 1.0)])
        self.assertEqual(found, ["/find/400"], "Sonarr's own TMDb id first; TMDb's /find only when it has none")
        self.like(self.alex, *range(100, 112))
        seeds = d._seeds(self.alex)
        self.assertEqual(len(seeds), 10)
        self.assertTrue(all(w == 2.0 for *_, w in seeds))

    def test_a_like_counts_twice_as_much_as_a_request(self):
        d = self.make()
        first, second = [card(1)], [card(2)]
        self.assertEqual([c["tmdb"] for c in d._score_recs([first, second])], [1, 2])
        self.assertEqual([c["tmdb"] for c in d._score_recs([first, second], weights=[1.0, 2.0])], [2, 1])

    def test_titles_only_in_disliked_genres_rank_lower(self):
        d = self.make()
        self.RECS = {("movie", n): [card(60, genres=(27,)), card(61, genres=(27, 35)), card(62, genres=(18,))] for n in (1, 2, 3)}
        asked_here = []

        def details(kind, tmdb):  # TMDb's details, which the build asks for itself: no reliance on what was cached
            asked_here.append(threading.current_thread() is threading.main_thread())
            return {"genres": [{"id": 27, "name": "Horror"}]} if tmdb == 99 else {"genres": [{"id": 18}]}

        d._details = details
        self.like(self.alex, 1, 2, 3)
        self.assertEqual(self.row(d.rows(self.alex), "foryou"), [60, 61, 62])
        self.store.set_opinion(self.alex, "movie", 99, False)
        d.opinion_changed(self.alex)
        self.assertEqual(self.row(d.rows(self.alex), "foryou"), [61, 62, 60])
        self.assertTrue(asked_here)
        self.assertNotIn(True, asked_here, "details are asked for in the background build, never while a page waits")

    def test_a_tmdb_failure_keeps_the_last_good_row(self):
        d = self.make()
        self.like(self.alex, 1, 2, 3)
        good = self.row(d.rows(self.alex), "foryou")
        works = d._recs

        def failing(kind, tmdb):
            if tmdb == 2:
                raise ApiError("tmdb: HTTP 503", status=503)
            return works(kind, tmdb)

        d._recs = failing
        self.like(self.alex, 7)
        with self.assertLogs("usher.discover", "WARNING") as logged:
            d.opinion_changed(self.alex)
            self.assertEqual(self.row(d.rows(self.alex), "foryou"), good, "not a row short of one seed's answers")
        self.assertEqual(logged.output, ["WARNING:usher.discover:discover: one account's Picked for you: tmdb: HTTP 503"])

    def test_a_tmdb_failure_before_any_row_is_tried_again_at_the_next_opening(self):
        d = self.make()
        self.like(self.alex, 1, 2, 3)
        works = d._recs
        d._recs = lambda kind, tmdb: (_ for _ in ()).throw(ApiError("tmdb: HTTP 503", status=503))
        with self.assertLogs("usher.discover", "WARNING"):
            self.assertEqual(self.row(d.rows(self.alex), "foryou"), [900, 901])
            self.settle(d)
        self.assertEqual(len(d._picks), 0, "nothing kept for an hour")
        d._recs = works
        self.assertEqual(self.row(d.rows(self.alex), "foryou")[0], 12)

    def test_a_title_tmdb_does_not_have_is_skipped_not_a_failure(self):
        d = self.make()
        works, details = d._recs, d._details

        def recs(kind, tmdb):
            if tmdb == 2:
                raise ApiError("tmdb: HTTP 404", status=404)
            return works(kind, tmdb)

        def genres(kind, tmdb):
            if tmdb == 98:
                raise ApiError("tmdb: HTTP 404", status=404)
            return details(kind, tmdb)

        d._recs, d._details = recs, genres
        self.like(self.alex, 1, 2, 3)
        self.store.set_opinion(self.alex, "show", 98, False)  # a movie's id given as a show, say
        with self.assertNoLogs("usher.discover", "WARNING"):
            self.assertEqual(set(self.row(d.rows(self.alex), "foryou")), {11, 12, 14})
        self.settle(d)
        self.assertEqual(len(d._picks), 1, "kept, so the next opening neither builds again nor waits")

    def test_tmdb_says_which_status_it_answered(self):
        with mock.patch.dict("os.environ", {"TMDB_TOKEN": "t"}):
            d = Discover(mock.Mock(), mock.Mock())
        missing = urllib.error.HTTPError("https://example.invalid/3/movie/1", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=missing):
            with self.assertRaises(ApiError) as caught:
                d._details("movie", 1)
        self.assertEqual((caught.exception.status, str(caught.exception)), (404, "tmdb: HTTP 404"))
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(ApiError) as caught:
                d._details("movie", 2)
        self.assertIsNone(caught.exception.status)

    def test_find_without_an_answer_fails_but_without_a_match_is_none(self):
        d = self.make(series=[{"id": 6}])
        d._get = mock.Mock(side_effect=ApiError("tmdb: HTTP 404", status=404))
        self.assertIsNone(d._show_tmdb(6, 400))
        d._get = mock.Mock(side_effect=ApiError("tmdb: HTTP 429", status=429))
        with self.assertRaises(ApiError):
            d._show_tmdb(6, 400)

    def test_no_genre_lookups_without_a_dislike(self):
        d = self.make()
        d._genres_of = mock.Mock(wraps=d._genres_of)
        self.like(self.alex, 1, 2, 3)
        self.row(d.rows(self.alex), "foryou")
        d._genres_of.assert_not_called()
        self.store.set_opinion(self.alex, "movie", 99, False)
        d.opinion_changed(self.alex)
        self.row(d.rows(self.alex), "foryou")
        self.assertEqual(sorted(c.args[0][1] for c in d._genres_of.call_args_list), [1, 2, 3, 99])

    def test_streaming_marks_go_on_the_row_served_when_a_rebuild_fails(self):
        d = self.make()
        self.like(self.alex, 1, 2, 3)
        self.row(d.rows(self.alex), "foryou")
        self.settle(d)
        key = d._picks_key(self.alex)
        d._mark_streaming = mock.Mock()
        d._recs = lambda kind, tmdb: (_ for _ in ()).throw(ApiError("tmdb: HTTP 503", status=503))
        job = d._picks_jobs[key] = {"done": threading.Event(), "again": False, "changed": False}
        with self.assertLogs("usher.discover", "WARNING"):
            d._run_picks(key, job)
        d._mark_streaming.assert_called_once()
        self.assertIs(d._mark_streaming.call_args.args[0], d._picks.peek(key)[0])

    def test_an_account_whose_titles_have_no_recommendations_keeps_the_shared_row(self):
        d = self.make()
        self.like(self.alex, 31, 32, 33)  # TMDb recommends nothing for these
        self.assertEqual(self.row(d.rows(self.alex), "foryou"), [900, 901])

    def test_a_show_without_a_tvdb_id_is_not_looked_up(self):
        d = self.make(series=[{"id": 6}])
        d._get = mock.Mock(return_value={"tv_results": [{"id": 4000}]})
        for tvdb in (0, None, -1, "400"):
            self.assertIsNone(d._show_tmdb(6, tvdb))
        d._get.assert_not_called()
        self.assertEqual(d._show_tmdb(6, 400), 4000)
        self.assertEqual(d._get.call_args.args[0], "/find/400")

    def test_a_new_like_does_not_wait_for_the_streaming_marks(self):
        d = self.make()
        d.PICKS_STREAMING_WAIT = 3
        d._details = lambda kind, tmdb: (time.sleep(3) if tmdb >= 10 else None) or {}  # slow only for the picks' streaming
        self.like(self.alex, 1, 2, 3)
        self.row(d.rows(self.alex), "foryou")
        self.like(self.alex, 7)
        started = time.monotonic()
        d.opinion_changed(self.alex)
        self.assertIn(77, self.row(d.rows(self.alex), "foryou"))
        self.assertLess(time.monotonic() - started, 1.5)
        started = time.monotonic()
        self.row(d.rows(self.alex), "foryou")
        self.assertLess(time.monotonic() - started, 0.5, "once the row is built, later openings don't wait")

    def test_a_stale_row_asked_for_during_a_build_does_not_build_again(self):
        d = self.make()
        key = d._picks_key(self.alex)
        job = d._picks_jobs[key] = {"done": threading.Event(), "again": False, "changed": False}
        d._start_picks(key)
        self.assertFalse(job["again"])
        d._start_picks(key, changed=True)
        self.assertTrue(job["again"] and job["changed"])
        del d._picks_jobs[key]

    def test_a_new_like_refreshes_that_accounts_row_in_the_background(self):
        d = self.make()
        self.like(self.alex, 1, 2, 3)
        self.assertNotIn(77, self.row(d.rows(self.alex), "foryou"))
        self.like(self.alex, 7)
        d.opinion_changed(self.alex)
        self.assertIn(77, self.row(d.rows(self.alex), "foryou"), "the app's next ask, right after the like, waits for the new row")

    def test_an_hour_old_row_is_served_while_it_is_rebuilt(self):
        d = self.make()
        now = [1000.0]
        d._picks = TTLCache(d.ROWS_TTL, 200, clock=lambda: now[0])
        self.like(self.alex, 1, 2, 3)
        before = self.row(d.rows(self.alex), "foryou")
        slow = d._recs
        d._recs = lambda kind, tmdb: (time.sleep(1.0), slow(kind, tmdb))[1]
        now[0] += d.ROWS_TTL + 1
        started = time.monotonic()
        self.assertEqual(self.row(d.rows(self.alex), "foryou"), before)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_nothing_about_likes_is_kept_in_discover_json(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = self.make(data_dir=tmp.name)
        d._get = lambda path, **params: (
            {"results": []}
            if path == "/watch/providers/tv"
            else {"results": [{"id": 51, "media_type": "movie", "title": "Made-up", "poster_path": "/p.jpg", "genre_ids": [35]}]}
        )
        self.like(self.alex, 1, 2, 3)
        self.store.set_opinion(self.alex, "movie", 51, False)
        self.assertIn(12, self.row(d.rows(self.alex), "foryou"))
        d._start_rows_build()
        deadline = time.monotonic() + 10
        while (d._rows_busy.locked() or not (Path(tmp.name) / "discover.json").exists()) and time.monotonic() < deadline:
            time.sleep(0.02)
        saved = (Path(tmp.name) / "discover.json").read_text(encoding="utf-8")
        tmdb_ids = {c["tmdb"] for r in json.loads(saved)["rows"] for c in r["items"]}
        self.assertIn(51, tmdb_ids, "a dislike is left out at serve time only, for that account")
        self.assertFalse(tmdb_ids & {11, 12, 13, 14})
        for word in ("opinion", "liked", "dislike"):
            self.assertNotIn(word, saved)


def logs(node):
    """A call such as log.info(...) or log.warning(...)."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"


class OpinionsStayPrivate(unittest.TestCase):
    def test_not_in_status_details(self):
        self.assertNotIn("opinion", (WEB.parent / "usher" / "diagnostics.py").read_text(encoding="utf-8"))

    def test_no_log_line_names_a_title_or_an_opinion(self):
        """The only value a log call in these files may format is the error itself (`e`)."""
        root = WEB.parent / "usher"
        for name in ("picks.py", "opinion_routes.py"):
            calls = [n for n in ast.walk(ast.parse((root / name).read_text(encoding="utf-8"))) if logs(n)]
            for call in calls:
                values = call.args[1:] + [k.value for k in call.keywords]
                self.assertTrue(all(isinstance(v, ast.Name) and v.id == "e" for v in values), f"{name}:{call.lineno} logs {ast.unparse(call)}")
                self.assertIsInstance(call.args[0], ast.Constant, f"{name}:{call.lineno} builds its message from values")

    def test_the_check_catches_a_logged_title(self):
        tree = ast.parse('log.info("liked %s %s", kind, tmdb)\nlog.warning(f"x {o}")\nlog.warning("ok: %s", e)')
        bad = [
            c
            for c in ast.walk(tree)
            if logs(c) and not (isinstance(c.args[0], ast.Constant) and all(isinstance(v, ast.Name) and v.id == "e" for v in c.args[1:]))
        ]
        self.assertEqual(len(bad), 2)


class WebTests(unittest.TestCase):
    def read(self, name):
        return (WEB / name).read_text(encoding="utf-8")

    def test_thumbs_icons(self):
        dom = self.read("dom.js")
        self.assertRegex(dom, r"(?m)^  like: '<")
        self.assertRegex(dom, r"(?m)^  dislike: '<")

    def test_the_details_sheet_has_like_and_dislike(self):
        sheets = self.read("sheets.js")
        for text in (
            "opinionButtons(r)",
            '"aria-pressed"',
            '"/api/opinion"',
            "icon(which)",
            'button("like", "Like")',
            'button("dislike", "Dislike")',
            'lists.drop("discover")',
        ):
            self.assertTrue(text in sheets, f"sheets.js lacks {text}")
        self.assertIn("this.opinionButtons(r)", self.read("discover.js"))
        for name in ("sheets.js", "settings.js"):
            self.assertNotIn("fetch(", self.read(name), f"{name} calls the server through api only")

    def test_the_buttons_have_a_selected_state_in_both_themes(self):
        css = self.read("sheets.css")
        self.assertIn('.opinion-btn[aria-pressed="true"]', css)
        self.assertIn("var(--accent)", css)
        self.assertIn("var(--on-accent)", css)
        self.assertNotIn("!important", css)
        self.assertNotIn("#", css.split(".opinions", 1)[1], "colours come from the palette's tokens")

    def test_settings_lists_likes_and_dislikes(self):
        settings = self.read("settings.js")
        for text in ("Likes and dislikes", "/api/opinions?after=", 'this.setOpinion(it, "clear")', "Try again"):
            self.assertTrue(text in settings, f"settings.js lacks {text}")

    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_a_second_tap_on_the_chosen_one_clears_it(self):
        script = """
import { Sheets } from "%(mod)s";
class Node {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.on = {}; }
  append(...k) { this.kids.push(...k); }
  replaceChildren(...k) { this.kids = k; }
  setAttribute(k, v) { this.attrs[k] = v; } addEventListener(n, f) { this.on[n] = f; }
}
globalThis.Node = Node;
globalThis.document = { createElement: (t) => new Node(t), createElementNS: (n, t) => new Node(t), createTextNode: (text) => ({ text }) };
const sent = [];
globalThis.fetch = async (path, opts) => {
  const body = opts.body ? JSON.parse(opts.body) : null;
  if (path === "/api/opinion") sent.push(body.opinion);
  return { ok: true, status: 200, json: async () => (path === "/api/opinion" ? { opinion: body.opinion === "clear" ? null : body.opinion } : { enabled: true, rows: [] }) };
};
const screen = new (Sheets(class {}))();
const r = { kind: "movie", tmdb: 101, opinion: null };
const shown = { kind: "movie", tmdb: 101, opinion: null };   // the same title, still listed in search results
screen.searchResults = { results: [shown, { kind: "show", tmdb: 101, opinion: null }] };
const drawn = screen.opinionButtons(r);   // drawn once: the buttons change in place, so focus stays where it is
const buttons = []; const walk = (n) => { if (!n || typeof n !== "object") return; if (n.tag === "button") buttons.push(n); (n.kids || []).forEach(walk); }; drawn.forEach(walk);
const pressed = () => buttons.map((b) => `${b.attrs["aria-label"]}:${b.attrs["aria-pressed"]}`);
const states = [pressed()];
const listed = [];
await buttons[0].on.click({});
states.push(pressed()); listed.push(shown.opinion);
await buttons[0].on.click({});
states.push(pressed()); listed.push(shown.opinion);
await buttons[1].on.click({});
states.push(pressed()); listed.push(shown.opinion, screen.searchResults.results[1].opinion);
console.log(JSON.stringify({ sent, states, listed, note: drawn[1].textContent, none: screen.opinionButtons({ kind: "movie", tmdb: null }) }));
""" % {"mod": (WEB / "sheets.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["sent"], ["like", "clear", "dislike"])
        self.assertEqual(
            out["states"],
            [["Like:false", "Dislike:false"], ["Like:true", "Dislike:false"], ["Like:false", "Dislike:false"], ["Like:false", "Dislike:true"]],
        )
        self.assertEqual(out["listed"], ["like", None, "dislike", None], "search results on screen follow; another kind of title doesn't")
        self.assertIn("Tap Dislike again to undo", out["note"])
        self.assertIsNone(out["none"], "no buttons without a TMDb id")

    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_undo_in_settings_keeps_focus_pages_the_list_and_tries_again(self):
        script = """
class Node {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.on = {}; this.className = "";
    this.style = { setProperty() {} }; this.classList = { add() {}, toggle() {} }; }
  append(...k) { this.kids.push(...k); } replaceChildren(...k) { this.kids = k; }
  setAttribute(k, v) { this.attrs[k] = v; } addEventListener(n, f) { this.on[n] = f; }
  showModal() {} close() {} remove() {} focus() { globalThis.focused = this; }
}
globalThis.Node = Node; globalThis.window = globalThis;
globalThis.document = { createElement: (t) => new Node(t), createElementNS: (n, t) => new Node(t), createTextNode: (text) => ({ text }), body: new Node("body") };
globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
globalThis.matchMedia = () => ({ matches: false });
globalThis.localStorage = { getItem: () => null, setItem() {} };
const asked = [];
let failFirst = true, release = null;
const pages = {
  "/api/opinions": { items: [{ kind: "movie", tmdb: 1, opinion: "like", title: "Made-up A" }, { kind: "show", tmdb: 2, opinion: "dislike", title: "Made-up B" }], next: "c1" },
  "/api/opinions?after=c1": { items: [{ kind: "movie", tmdb: 3, opinion: "like", title: "Made-up C" }], next: null },
};
globalThis.fetch = async (path, o) => {
  asked.push(o.body ? `${path} ${JSON.parse(o.body).tmdb} ${JSON.parse(o.body).opinion}` : path);
  if (path === "/api/opinions" && failFirst) {
    failFirst = false;
    return { ok: false, status: 502, json: async () => ({ error: "The server couldn't reach one of its apps." }) };
  }
  if (path.includes("after=")) await new Promise((r) => { release = r; });   // Show more is still on its way
  const data = pages[path] || (path === "/api/opinion" ? { opinion: null } : { enabled: true, rows: [] });
  return { ok: true, status: 200, json: async () => data };
};
const { Sheets } = await import("%(sheets)s");
const { Settings } = await import("%(settings)s");
const app = new (Settings(Sheets(class {})))();
const tick = () => new Promise((r) => setTimeout(r, 10));
const find = (pred) => { let hit = null; const walk = (n) => { if (!n || typeof n !== "object" || hit) return; if (pred(n)) hit = n; (n.kids || []).forEach(walk); }; walk(app.sheet.dialog); return hit; };
const words = (n) => (n.text ?? "") + " " + (n.kids || []).map(words).join(" ");
const labelled = (text) => find((n) => n.tag === "button" && n.kids.some((k) => k.text === text));
const undo = (name) => find((n) => n.tag === "button" && (n.attrs["aria-label"] || "").endsWith(name));
const where = () => globalThis.focused?.attrs["aria-label"] || globalThis.focused?.tag;
const focus = [];
app.opinionsSheet();
await tick();
const failed = words(app.sheet.dialog).includes("couldn't reach") && !!labelled("Try again");
await labelled("Try again").on.click(); await tick(); focus.push(where());
await undo("Made-up A").on.click(); await tick(); focus.push(where());
const more = labelled("Show more").on.click();   // in flight while B is undone
await tick();
await undo("Made-up B").on.click(); await tick();
release(); await more; await tick(); focus.push(where());
const shown = words(app.sheet.dialog).includes("Made-up C");
const moreLeft = !!labelled("Show more");
await undo("Made-up C").on.click(); await tick(); focus.push(where());
console.log(JSON.stringify({ asked, focus, failed, shown, moreLeft, title: globalThis.focused.attrs.tabindex }));
""" % {"sheets": (WEB / "sheets.js").as_uri(), "settings": (WEB / "settings.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertTrue(out["failed"], "a first page that can't be had says so, with Try again")
        self.assertEqual(
            out["focus"],
            ["h2", "Undo dislike of Made-up B", "Undo like of Made-up C", "h2"],
            "focus goes to the title after Try again, to the Undo now in a removed row's place, to the first new title, then the title",
        )
        self.assertEqual(out["title"], "-1")
        self.assertTrue(out["shown"], "the page that arrived after an Undo is shown whole")
        self.assertFalse(out["moreLeft"], "no Show more after the last page")
        opinions = [a for a in out["asked"] if a.startswith("/api/opinion")]
        self.assertEqual(
            opinions,
            ["/api/opinions", "/api/opinions", "/api/opinion 1 clear", "/api/opinions?after=c1", "/api/opinion 2 clear", "/api/opinion 3 clear"],
            "the next page starts after the last title had, whatever was undone meanwhile",
        )


if __name__ == "__main__":
    unittest.main()
