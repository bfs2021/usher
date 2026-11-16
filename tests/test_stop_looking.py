"""Stop looking for it: withdraw a request that hasn't downloaded anything, without deleting anything."""

import unittest
from unittest import mock

from usher.actions import Actions, Problem
from usher.clients import ApiError

USER = {"username": "alex", "user_id": 1}


def _actions(req, items, queue=()):
    sonarr, radarr, store, state = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
    store.one.return_value = req
    for app in (sonarr, radarr):

        def get(path, **kw):
            if path == "queue":
                return {"records": list(queue)}
            if path not in items:
                raise ApiError("HTTP 404")
            return items[path]

        app.get.side_effect = get
    return Actions(sonarr, radarr, store, state)


def _req(kind, arr_id, choice=""):
    return {"id": 12, "kind": kind, "arr_id": arr_id, "title": "Thing", "choice": choice}


class StopLookingTests(unittest.TestCase):
    def test_show_with_nothing_here_is_taken_out_without_deleting_files(self):
        a = _actions(_req("show", 22, "start"), {"series/22": {"statistics": {"episodeFileCount": 0}}})
        self.assertTrue(a.stop_looking(12, USER)["ok"])
        a.sonarr.send.assert_called_once_with("DELETE", "series/22", deleteFiles="false", addImportListExclusion="false")
        a.store.forget_item.assert_called_once_with("show", 22)

    def test_one_season_of_a_show_already_here_is_only_switched_off(self):
        series = {
            "statistics": {"episodeFileCount": 8},
            "monitored": True,
            "seasons": [{"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": True}],
        }
        eps = [
            {"id": 1, "seasonNumber": 1, "hasFile": True, "monitored": True},
            {"id": 2, "seasonNumber": 2, "hasFile": False, "monitored": True},
            {"id": 3, "seasonNumber": 2, "hasFile": False, "monitored": True},
        ]
        a = _actions(_req("show", 22, "season:2"), {"series/22": series, "episode": eps})
        a.stop_looking(12, USER)
        self.assertFalse([c for c in a.sonarr.send.call_args_list if c.args[0] == "DELETE"])
        put = a.sonarr.send.call_args_list[0]
        self.assertEqual(put.args[:2], ("PUT", "series/22"))
        self.assertEqual([x["monitored"] for x in put.args[2]["seasons"]], [True, False])
        self.assertTrue(put.args[2]["monitored"])
        a.sonarr.send.assert_any_call("PUT", "episode/monitor", {"episodeIds": [2, 3], "monitored": False})
        a.store.forget_item.assert_not_called()
        a.store.run.assert_called_once_with("DELETE FROM requests WHERE id = ?", (12,))

    def test_movie_without_file_is_taken_out(self):
        a = _actions(_req("movie", 4), {"movie/4": {"hasFile": False, "monitored": True}})
        a.stop_looking(12, USER)
        a.radarr.send.assert_called_once_with("DELETE", "movie/4", deleteFiles="false", addImportExclusion="false")

    def test_movie_with_file_is_kept_and_unmonitored(self):
        a = _actions(_req("movie", 4), {"movie/4": {"hasFile": True, "monitored": True}})
        a.stop_looking(12, USER)
        put = a.radarr.send.call_args_list[0]
        self.assertEqual(put.args[:2], ("PUT", "movie/4"))
        self.assertFalse(put.args[2]["monitored"])

    def test_already_gone_from_sonarr_just_forgets(self):
        a = _actions(_req("show", 22), {})
        a.stop_looking(12, USER)
        a.sonarr.send.assert_not_called()
        a.store.forget_item.assert_called_once_with("show", 22)

    def test_unknown_request(self):
        a = _actions(None, {})
        with self.assertRaises(Problem):
            a.stop_looking(99, USER)


if __name__ == "__main__":
    unittest.main()
