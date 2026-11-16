"""Keeping a watched title out of Tidy up, and putting it back."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usher import tidy
from usher.actions import Actions, Problem
from usher.kept import Kept, kept_key
from tests.test_state_basics import make_state
from tests.test_tidy_watched import PLAYED, jep, movie, series

USER = {"username": "u", "user_id": 1}


def watched_world():
    """Two watched movies and one show with two watched seasons, as Radarr, Sonarr and Jellyfin report them."""
    movies = [movie(1, 10, 2e9), movie(2, 20, 5e9)]
    show = series(7, 700, [(1, {"episodeFileCount": 1, "sizeOnDisk": 3e9}), (2, {"episodeFileCount": 1, "sizeOnDisk": 4e9})])
    show["statistics"] = {"episodeFileCount": 2, "sizeOnDisk": 7e9}
    jf_movies = [{"ProviderIds": {"Tmdb": "10"}, "UserData": PLAYED}, {"ProviderIds": {"Tmdb": "20"}, "UserData": PLAYED}]
    jf_shows = [{"Id": "J7", "ProviderIds": {"Tvdb": "700"}}]
    eps = [jep("J7", 1, 1, PLAYED), jep("J7", 2, 1, PLAYED)]
    return movies, [show], jf_movies, jf_shows, eps


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_key_is_the_catalogue_id_not_the_name(self):
        self.assertEqual(kept_key("movie", {"tmdbId": 10, "title": "A"}), "movie:tmdb:10")
        self.assertEqual(kept_key("show", {"tvdbId": 700, "title": "B"}), "show:tvdb:700")
        self.assertIsNone(kept_key("movie", {"title": "No id"}))

    def test_survives_a_restart_and_is_private(self):
        Kept(self.dir).keep("movie:tmdb:10")
        again = Kept(self.dir)
        self.assertEqual(again.keys(), {"movie:tmdb:10"})
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "kept.json").st_mode), 0o600)
        again.put_back("movie:tmdb:10")
        self.assertEqual(Kept(self.dir).keys(), frozenset())

    def test_unreadable_file_starts_empty(self):
        (self.dir / "kept.json").write_text("not json", encoding="utf-8")
        self.assertEqual(Kept(self.dir).keys(), frozenset())

    def test_failed_save_is_not_kept(self):
        k = Kept(self.dir)
        with mock.patch("usher.kept.write_private", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                k.keep("movie:tmdb:10")
        self.assertEqual(k.keys(), frozenset())
        self.assertEqual(json.loads(json.dumps(sorted(k.keys()))), [])


class ListTests(unittest.TestCase):
    def test_kept_titles_leave_the_list_and_the_total(self):
        movies, shows, jm, js, eps = watched_world()
        everything = tidy.candidates(movies, shows, jm, js, eps)
        self.assertEqual([(c["kind"], c["id"]) for c in everything], [("movie", 2), ("season", 7), ("season", 7), ("movie", 1)])
        left = tidy.candidates(movies, shows, jm, js, eps, kept={"movie:tmdb:20", "show:tvdb:700"})
        self.assertEqual([(c["kind"], c["id"]) for c in left], [("movie", 1)])
        self.assertEqual(sum(c["size"] for c in left), 2e9)

    def test_kept_show_covers_a_season_watched_later(self):
        movies, shows, jm, js, eps = watched_world()
        shows[0]["seasons"].append({"seasonNumber": 3, "statistics": {"episodeFileCount": 1, "sizeOnDisk": 2e9}})
        eps.append(jep("J7", 3, 1, PLAYED))
        left = tidy.candidates([], shows, [], js, eps, kept={"show:tvdb:700"})
        self.assertEqual(left, [])

    def test_kept_titles_list_skips_what_left_the_server(self):
        movies, shows, *_ = watched_world()
        movies[0]["hasFile"] = False  # files gone
        got = tidy.kept_titles(movies, shows, {"movie:tmdb:10", "movie:tmdb:20", "movie:tmdb:99", "show:tvdb:700"})
        self.assertEqual([(g["kind"], g["key"]) for g in got], [("movie", "movie:tmdb:20"), ("show", "show:tvdb:700")])


class PutBackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = make_state()
        self.state.kept = Kept(self._tmp.name)
        self.state._raw["queue_movies"], self.state._raw["queue_tv"] = [], []
        movies, shows, jm, js, eps = watched_world()
        self.state.radarr.get.return_value = movies
        self.state.sonarr.get.return_value = shows
        patcher = mock.patch("usher.tidy.jellyfin_items", return_value=(jm, js, eps))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.actions = Actions(mock.Mock(), mock.Mock(), mock.Mock(), self.state)

    def test_keep_then_put_back_restores_the_position(self):
        before = [(i["kind"], i["id"], i["season"]) for i in self.state.tidy()["items"]]
        self.actions.tidy_call("keep", {"kind": "movie", "id": 2}, USER)
        listed = self.state.tidy()
        self.assertNotIn(("movie", 2, None), [(i["kind"], i["id"], i["season"]) for i in listed["items"]])
        self.assertEqual(listed["bytes"], 2e9 + 7e9)
        self.assertEqual([k["key"] for k in self.state.kept_titles()["items"]], ["movie:tmdb:20"])
        self.actions.tidy_call("put-back", {"key": "movie:tmdb:20"}, USER)
        self.assertEqual([(i["kind"], i["id"], i["season"]) for i in self.state.tidy()["items"]], before)
        self.assertEqual(self.state.kept_titles()["items"], [])

    def test_keeping_a_season_keeps_the_whole_show(self):
        self.actions.tidy_call("keep", {"kind": "season", "id": 7, "season": 1}, USER)
        self.assertEqual([i["kind"] for i in self.state.tidy()["items"]], ["movie", "movie"])
        self.assertEqual([k["kind"] for k in self.state.kept_titles()["items"]], ["show"])

    def test_only_listed_titles_can_be_kept_and_only_kept_ones_put_back(self):
        with self.assertRaises(Problem):
            self.actions.tidy_call("keep", {"kind": "movie", "id": 99}, USER)
        with self.assertRaises(Problem):
            self.actions.tidy_call("put-back", {"key": "movie:tmdb:10"}, USER)
        with self.assertRaises(ValueError):
            self.actions.tidy_call("keep", {"kind": "nonsense", "id": 1}, USER)


if __name__ == "__main__":
    unittest.main()
