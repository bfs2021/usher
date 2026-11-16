"""Stop downloading, and Mark all as read."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usher.actions import Actions, Problem
from usher.clients import ApiError
from usher.store import Store

USER = {"username": "alex", "user_id": 1}


def _actions(app_name, ids, queue):
    sonarr, radarr, store, state = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
    state.queue_ids = {"k1": (app_name, ids)}
    for app in (sonarr, radarr):
        app.get.side_effect = lambda path, app=app, **kw: {"queue": {"records": queue}}.get(path) or app.items[path]
    return Actions(sonarr, radarr, store, state)


class CancelTests(unittest.TestCase):
    def test_show_episodes_unmonitored_and_empty_request_dropped(self):
        a = _actions("sonarr", [5, 6], [{"id": 5, "episodeId": 50, "seriesId": 17}, {"id": 6, "episodeId": 51, "seriesId": 17}])
        a.sonarr.items = {"series/17": {"title": "Mercenary", "statistics": {"episodeFileCount": 0}}}
        res = a.cancel("k1", USER)
        self.assertEqual(res["title"], "Mercenary")
        deletes = [c for c in a.sonarr.send.call_args_list if c.args[0] == "DELETE"]
        self.assertEqual([c.args[1] for c in deletes], ["queue/5", "queue/6"])
        self.assertEqual(deletes[0].kwargs["blocklist"], "false")
        self.assertEqual(deletes[0].kwargs["removeFromClient"], "true")
        a.sonarr.send.assert_any_call("PUT", "episode/monitor", {"episodeIds": [50, 51], "monitored": False})
        a.store.forget_item.assert_called_once_with("show", 17)

    def test_show_with_files_keeps_request(self):
        a = _actions("sonarr", [5], [{"id": 5, "episodeId": 50, "seriesId": 17}])
        a.sonarr.items = {"series/17": {"title": "Show", "statistics": {"episodeFileCount": 3}}}
        a.cancel("k1", USER)
        a.store.forget_item.assert_not_called()

    def test_movie_unmonitored(self):
        a = _actions("radarr", [9], [{"id": 9, "movieId": 4}])
        a.radarr.items = {"movie/4": {"title": "Film", "monitored": True, "hasFile": False}}
        a.cancel("k1", USER)
        put = [c for c in a.radarr.send.call_args_list if c.args[0] == "PUT"][0]
        self.assertEqual(put.args[1], "movie/4")
        self.assertFalse(put.args[2]["monitored"])
        a.store.forget_item.assert_called_once_with("movie", 4)

    def test_season_pack_rest_already_gone(self):
        """Removing a pack's first queue entry removes the whole download; the others then answer 404."""
        queue = [{"id": i, "episodeId": 100 + i, "seriesId": 17} for i in (5, 6, 7)]
        a = _actions("sonarr", [5, 6, 7], queue)
        a.sonarr.items = {"series/17": {"title": "Pack", "statistics": {"episodeFileCount": 0}}}
        calls = []

        def send(method, path, body=None, **kw):
            calls.append((method, path))
            if method == "DELETE" and path != "queue/5":
                raise ApiError("sonarr: HTTP 404")
            return {}

        a.sonarr.send.side_effect = send
        a.cancel("k1", USER)
        self.assertEqual(calls[0], ("PUT", "episode/monitor"))  # monitoring off before anything is removed
        a.sonarr.send.assert_any_call("PUT", "episode/monitor", {"episodeIds": [105, 106, 107], "monitored": False})
        a.store.forget_item.assert_called_once_with("show", 17)

    def test_nothing_removed_is_an_error(self):
        a = _actions("radarr", [9], [{"id": 9, "movieId": 4}])
        a.radarr.items = {"movie/4": {"title": "Film", "monitored": True, "hasFile": False}}
        a.radarr.send.side_effect = lambda method, path, body=None, **kw: (_ for _ in ()).throw(ApiError("x")) if method == "DELETE" else {}
        with self.assertRaises(Problem):
            a.cancel("k1", USER)

    def test_gone_already(self):
        a = _actions("radarr", [], [])
        with self.assertRaises(Problem):
            a.cancel("k1", USER)


class SeenTests(unittest.TestCase):
    def test_mark_all_seen_moves_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d))
            st.add_user("alex", "Alex", "x")
            uid = st.user_by_name("alex")["id"]
            first = st.baseline(uid)
            with mock.patch("time.time", return_value=first + 100):
                self.assertEqual(st.mark_all_seen(uid), first + 100)
            self.assertEqual(st.baseline(uid), first + 100)


if __name__ == "__main__":
    unittest.main()
