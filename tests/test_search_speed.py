"""Search answers per source, at once from the library, from a short cache, and never queues behind a replaced search."""

import threading
import time
import unittest
from unittest import mock

from usher.actions import Actions


def _actions(series=(), movies=()):
    sonarr, radarr, store, state = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
    sonarr.name, radarr.name = "sonarr", "radarr"
    state._raw = {"series": list(series), "movies": list(movies)}
    store.requesters.return_value = {}
    return Actions(sonarr, radarr, store, state)


def _lookup(results, delay=0.0, gate=None, calls=None):
    def get(path, term=None, **kw):
        if calls is not None:
            calls.append(term)
        if gate is not None:
            gate.wait(5)
        time.sleep(delay)
        return [dict(r) for r in results(term)] if callable(results) else [dict(r) for r in results]

    return get


SHOWS = [{"tvdbId": 5, "title": "Bluey"}]
MOVIES = [{"tmdbId": 9, "title": "Bluey: The Movie"}]


class PerSourceTests(unittest.TestCase):
    def test_fast_source_does_not_wait_for_the_slow_one(self):
        a = _actions()
        slow = threading.Event()
        a.sonarr.get.side_effect = _lookup(SHOWS, gate=slow)
        a.radarr.get.side_effect = _lookup(MOVIES)
        started = time.monotonic()
        res = a.search("bluey", part="movie")
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual([r["title"] for r in res["results"]], ["Bluey: The Movie"])
        slow.set()
        self.assertEqual([r["title"] for r in a.search("bluey", part="show")["results"]], ["Bluey"])

    def test_one_failing_source_is_named(self):
        from usher.clients import ApiError

        a = _actions()
        a.sonarr.get.side_effect = ApiError("down")
        res = a.search("bluey", part="show")
        self.assertEqual((res["results"], res["failed"]), ([], ["TV"]))

    def test_unknown_part_is_refused(self):
        with self.assertRaises(ValueError):
            _actions().search("bluey", part="music")


class LibraryTests(unittest.TestCase):
    LIB_SHOW = {"id": 3, "tvdbId": 5, "title": "Bluey", "statistics": {"episodeFileCount": 4, "episodeCount": 10}}

    def test_library_matches_come_without_any_lookup(self):
        a = _actions(
            series=[self.LIB_SHOW, {"id": 4, "tvdbId": 6, "title": "Other"}], movies=[{"id": 8, "tmdbId": 9, "title": "The Bluey Film", "hasFile": True}]
        )
        res = a.search("bluey", part="library")
        a.sonarr.get.assert_not_called()
        a.radarr.get.assert_not_called()
        self.assertEqual([(r["title"], r["inLibrary"]) for r in res["results"]], [("Bluey", True), ("The Bluey Film", True)])
        self.assertEqual(res["results"][0]["have"], 4)

    def test_merged_results_have_no_duplicates(self):
        a = _actions(series=[self.LIB_SHOW])
        a.sonarr.get.side_effect = _lookup([dict(SHOWS[0], id=3), {"tvdbId": 7, "title": "Bluey Extra"}])
        a.radarr.get.side_effect = _lookup(MOVIES)
        res = a.search("bluey")
        keys = [(r["kind"], r["ext"]) for r in res["results"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sorted(keys), [("movie", 9), ("show", 5), ("show", 7)])
        self.assertTrue(res["results"][0]["inLibrary"])


class CacheTests(unittest.TestCase):
    def test_repeat_query_comes_from_the_cache(self):
        a = _actions()
        calls = []
        a.sonarr.get.side_effect = _lookup(SHOWS, calls=calls)
        a.search("Bluey", part="show")
        a.search(" bluey ", part="show")
        self.assertEqual(calls, ["Bluey"])

    def test_badges_are_fresh_not_cached(self):
        a = _actions()
        a.sonarr.get.side_effect = _lookup(SHOWS)
        self.assertIsNone(a.search("bluey", part="show")["results"][0]["by"])
        a.store.requesters.return_value = {("show", None): ["Sam"]}
        self.assertEqual(a.search("bluey", part="show")["results"][0]["by"], "Sam")

    def test_cache_expires(self):
        a = _actions()
        calls = []
        a.sonarr.get.side_effect = _lookup(SHOWS, calls=calls)
        a.search("bluey", part="show")
        with mock.patch("usher.actions.SEARCH_TTL", 0):
            a.search("bluey", part="show")
        self.assertEqual(len(calls), 2)


class SupersededTests(unittest.TestCase):
    def test_second_search_does_not_wait_for_the_first(self):
        a = _actions()
        stuck = threading.Event()
        a.sonarr.get.side_effect = _lookup(lambda term: SHOWS if term == "bluey" else [], gate=None)
        a.radarr.get.side_effect = lambda path, term=None, **kw: stuck.wait(5) and [] if term == "blu" else [dict(MOVIES[0])]
        first = threading.Thread(target=a.search, args=("blu",), kwargs={"part": "movie", "client": "1:t"})
        first.start()
        time.sleep(0.05)
        started = time.monotonic()
        res = a.search("bluey", part="movie", client="1:t")
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual([r["ext"] for r in res["results"]], [9])
        stuck.set()
        first.join(5)

    def test_queued_lookup_for_a_replaced_search_is_skipped(self):
        a = _actions()
        calls = []
        hold = threading.Event()
        a.radarr.get.side_effect = _lookup(MOVIES, gate=hold, calls=calls)
        # Fill the search pool so the next lookup has to queue.
        busy = [a._search_pool.submit(hold.wait, 5) for _ in range(6)]
        out = {}
        t = threading.Thread(target=lambda: out.update(a.search("blu", part="movie", client="1:t")))
        t.start()
        time.sleep(0.05)
        a.search("bluey", part="library", client="1:t")  # the same tab moved on
        hold.set()
        t.join(5)
        for f in busy:
            f.result(5)
        self.assertTrue(out.get("superseded"))
        self.assertNotIn("blu", calls)

    def test_a_lookup_another_tab_still_wants_runs(self):
        a = _actions()
        calls = []
        hold = threading.Event()
        a.radarr.get.side_effect = _lookup(MOVIES, calls=calls)
        busy = [a._search_pool.submit(hold.wait, 5) for _ in range(6)]
        outs = []
        ts = [threading.Thread(target=lambda c=c: outs.append(a.search("blu", part="movie", client=c))) for c in ("1:a", "2:b")]
        for t in ts:
            t.start()
        time.sleep(0.05)
        a.search("bluey", part="library", client="1:a")
        hold.set()
        for t in ts:
            t.join(5)
        for f in busy:
            f.result(5)
        self.assertEqual(calls, ["blu"])
        self.assertTrue(any(o.get("results") for o in outs))


class LoggingTests(unittest.TestCase):
    def test_lookup_and_search_times_are_logged(self):
        a = _actions()
        a.sonarr.get.side_effect = _lookup(SHOWS)
        with self.assertLogs("usher.actions", "INFO") as logs:
            a.search("bluey", part="show")
        text = "\n".join(logs.output)
        self.assertIn("show lookup for 'bluey' took", text)
        self.assertIn("search show for 'bluey': 1 results in", text)


class EndpointTests(unittest.TestCase):
    """/api/search passes part and a per-tab client (the signed-in account plus the tab's id) through."""

    def _get(self, url, session):
        from usher.server import Handler

        h = Handler.__new__(Handler)
        h.path, h.headers = url, {}
        h.app = mock.Mock()
        h.app.actions.search.return_value = {"results": [], "failed": []}
        h.app.store.opinions.return_value = []  # the account's likes and dislikes, marked on the results
        h.host_ok = lambda: True
        h.session = lambda: session
        h.send = mock.Mock()
        h.do_GET()
        return h

    def test_part_and_client_reach_search(self):
        import sqlite3

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT 7 AS user_id, 'x' AS csrf").fetchone()  # sessions come back as sqlite rows
        h = self._get("/api/search?q=bluey&extras=0&part=movie&c=tab1", row)
        h.app.actions.search.assert_called_once_with("bluey", part="movie", client="7:tab1")
        self.assertEqual(h.send.call_args.args[0], 200)

    def test_without_a_tab_id_nothing_is_skipped(self):
        h = self._get("/api/search?q=bluey&extras=0", {"user_id": 7})
        h.app.actions.search.assert_called_once_with("bluey", part=None, client=None)


if __name__ == "__main__":
    unittest.main()
