"""Tidy-up of watched titles, "Not out yet" for shows, part-here counts, stalled and wrong-title downloads."""

import time
import unittest
from unittest import mock

from usher import tidy
from usher.actions import Actions, Problem
from usher.releases import title_matches
from usher.state.helpers import STALLED_AFTER
from tests.test_state_basics import make_state

PLAYED = {"Played": True, "LastPlayedDate": "2026-11-01T10:00:00Z"}
UNPLAYED = {"Played": False}


def movie(i, tmdb, size, has=True, path=None):
    return {"id": i, "tmdbId": tmdb, "title": f"M{i}", "year": 2020, "hasFile": has, "sizeOnDisk": size, "path": path or f"/srv/media/Movies/M{i}"}


def series(i, tvdb, seasons):
    return {
        "id": i,
        "tvdbId": tvdb,
        "title": f"S{i}",
        "path": f"/srv/media/TV Shows/S{i}",
        "seasons": [{"seasonNumber": n, "statistics": st} for n, st in seasons],
    }


def jep(series_id, season, number, ud, end=None):
    return {"SeriesId": series_id, "ParentIndexNumber": season, "IndexNumber": number, "IndexNumberEnd": end, "UserData": ud}


class CandidateTests(unittest.TestCase):
    def test_movies_watched_on_home_biggest_first(self):
        movies = [movie(1, 10, 2e9), movie(2, 20, 5e9), movie(3, 30, 9e9), movie(4, 40, 0, has=False)]
        jf = [
            {"ProviderIds": {"Tmdb": "10"}, "UserData": PLAYED},
            # the same file in Movies and Family Movies: played in one copy is enough
            {"ProviderIds": {"Tmdb": "20"}, "UserData": UNPLAYED},
            {"ProviderIds": {}, "Path": "/srv/media/Movies/M2/M2.mkv", "UserData": PLAYED},
            {"ProviderIds": {"Tmdb": "30"}, "UserData": UNPLAYED},
            {"ProviderIds": {"Tmdb": "40"}, "UserData": PLAYED},
        ]
        out = tidy.candidates(movies, [], jf, [], [])
        self.assertEqual([(c["kind"], c["id"]) for c in out], [("movie", 2), ("movie", 1)])

    def test_busy_movie_left_out(self):
        out = tidy.candidates([movie(1, 10, 2e9)], [], [{"ProviderIds": {"Tmdb": "10"}, "UserData": PLAYED}], [], [], busy_movies={1})
        self.assertEqual(out, [])

    def test_whole_seasons_only(self):
        s = series(
            7,
            700,
            [
                (1, {"episodeFileCount": 2, "sizeOnDisk": 3e9}),
                (2, {"episodeFileCount": 2, "sizeOnDisk": 4e9}),
                (3, {"episodeFileCount": 2, "sizeOnDisk": 5e9, "nextAiring": "2026-12-01T00:00:00Z"}),
                (4, {"episodeFileCount": 3, "sizeOnDisk": 6e9}),
                (5, {"episodeFileCount": 0, "sizeOnDisk": 0}),
            ],
        )
        shows = [{"Id": "J7", "ProviderIds": {"Tvdb": "700"}}]
        eps = [
            jep("J7", 1, 1, PLAYED),
            jep("J7", 1, 2, PLAYED),  # season 1: all watched
            jep("J7", 2, 1, PLAYED),
            jep("J7", 2, 2, UNPLAYED),  # season 2: one left
            jep("J7", 3, 1, PLAYED),
            jep("J7", 3, 2, PLAYED),  # season 3: still airing
            jep("J7", 4, 1, PLAYED),
            jep("J7", 4, 2, PLAYED),
        ]  # season 4: Jellyfin is missing one
        out = tidy.candidates([], [s], [], shows, eps)
        self.assertEqual([(c["kind"], c["id"], c["season"]) for c in out], [("season", 7, 1)])
        self.assertEqual(out[0]["subtitle"], "Season 1 · 2 episodes")

    def test_double_episode_and_folder_match(self):
        s = series(8, 0, [(1, {"episodeFileCount": 3, "sizeOnDisk": 2e9})])
        shows = [{"Id": "J8", "ProviderIds": {}, "Path": "/srv/media/TV Shows/S8"}]
        eps = [jep("J8", 1, 1, PLAYED, end=2), jep("J8", 1, 3, PLAYED)]
        self.assertEqual(len(tidy.candidates([], [s], [], shows, eps)), 1)


class RemoveTests(unittest.TestCase):
    def test_movie_unmonitored_then_files_deleted(self):
        radarr = mock.Mock()
        radarr.get.side_effect = lambda path, **kw: {"id": 3, "title": "M3", "monitored": True} if path == "movie/3" else [{"id": 31}]
        self.assertEqual(tidy.remove(mock.Mock(), radarr, "movie", 3), "M3")
        put, delete = radarr.send.call_args_list
        self.assertEqual(put.args[:2], ("PUT", "movie/3"))
        self.assertFalse(put.args[2]["monitored"])
        self.assertEqual(delete.args, ("DELETE", "moviefile/31"))

    def test_season_unmonitored_and_only_its_files_deleted(self):
        sonarr = mock.Mock()
        s = {"id": 5, "title": "Scrubs", "seasons": [{"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": True}]}

        def get(path, **kw):
            if path == "series/5":
                return s
            if path == "episode":
                return [{"id": 1, "seasonNumber": 1, "monitored": True}, {"id": 2, "seasonNumber": 2, "monitored": True}]
            return [{"id": 11, "seasonNumber": 1}, {"id": 12, "seasonNumber": 2}]

        sonarr.get.side_effect = get
        self.assertEqual(tidy.remove(sonarr, mock.Mock(), "season", 5, 1), "Scrubs · Season 1")
        calls = [c.args for c in sonarr.send.call_args_list]
        self.assertEqual([x["monitored"] for x in calls[0][2]["seasons"]], [False, True])
        self.assertEqual(calls[1], ("PUT", "episode/monitor", {"episodeIds": [1], "monitored": False}))
        self.assertEqual(calls[2], ("DELETE", "episodefile/bulk", {"episodeFileIds": [11]}))

    def test_only_listed_items_can_go(self):
        state = mock.Mock()
        state.tidy.return_value = {"items": [{"kind": "movie", "id": 1, "season": None}]}
        a = Actions(mock.Mock(), mock.Mock(), mock.Mock(), state)
        with self.assertRaises(Problem):
            a.tidy_remove("movie", 2, None, {"username": "u", "user_id": 1})


class TitleTests(unittest.TestCase):
    def test_matches(self):
        self.assertTrue(title_matches("Trash.Truck.S01E03.1080p.WEB.h264", {"title": "Trash Truck"}))
        self.assertTrue(title_matches("Bear.S03E01.1080p", {"title": "The Bear"}))
        self.assertTrue(title_matches("Mission.Impossible.The.Final.Reckoning.2025.1080p", {"title": "Mission: Impossible – The Final Reckoning"}))
        self.assertTrue(title_matches("Pokemon.Horizons.S01E01", {"title": "Pokémon Horizons"}))
        self.assertTrue(title_matches("Shrinking.S02E01", {"title": "Shrinking (2023)"}))
        self.assertTrue(title_matches("La.Casa.de.Papel.S01", {"title": "Money Heist", "alternateTitles": [{"title": "La Casa de Papel"}]}))
        self.assertTrue(title_matches("", {"title": "Anything"}))

    def test_wrong_show(self):
        self.assertFalse(title_matches("Giant.Jack.S01E01.1080p.WEB", {"title": "Trash Truck"}))


def queue_rec(**kw):
    rec = {
        "id": 1,
        "downloadId": "ABC",
        "seriesId": 4,
        "episodeId": 40,
        "size": 100,
        "sizeleft": 60,
        "status": "downloading",
        "trackedDownloadState": "downloading",
        "trackedDownloadStatus": "ok",
        "title": "Trash.Truck.S01E01.1080p",
        "series": {"title": "Trash Truck"},
        "episode": {"seasonNumber": 1, "episodeNumber": 1},
    }
    rec.update(kw)
    return rec


class DownloadFlagTests(unittest.TestCase):
    def test_stalled_after_hours_without_progress(self):
        st = make_state()
        st._raw["queue_tv"] = [queue_rec()]
        d = st._downloads(st._raw)[0][0]
        self.assertFalse(d["stalled"])
        st._progress["abc"] = (0.4, time.time() - STALLED_AFTER - 60)
        d = st._downloads(st._raw)[0][0]
        self.assertTrue(d["stalled"])
        self.assertEqual(d["state"], "downloading")
        st._raw["queue_tv"] = [queue_rec(sizeleft=10)]  # it moved again
        self.assertFalse(st._downloads(st._raw)[0][0]["stalled"])

    def test_wrong_title_flagged_and_reported(self):
        st = make_state()
        st._raw["queue_tv"] = [queue_rec(title="Giant.Jack.S01E01.1080p")]
        d = st._downloads(st._raw)[0][0]
        self.assertEqual(d["wrongTitle"], "Giant.Jack.S01E01.1080p")
        st.snapshot = {"downloading": [d]}
        self.assertEqual(st.attention(), ['wrong title? Trash Truck (S1E1) is downloading "Giant.Jack.S01E01.1080p"'])


class RequestLabelTests(unittest.TestCase):
    def state_with(self, series_obj, choice="start", downloads=()):
        st = make_state()
        st.store = mock.Mock()
        st.store.recent_requests.return_value = [
            {
                "id": 1,
                "kind": "show",
                "arr_id": 4,
                "title": "T",
                "year": 2026,
                "display": "Sam",
                "created": time.time() - 3600,
                "choice": choice,
                "jf_id": None,
                "searched": None,
                "done_at": None,
            }
        ]
        st._raw["series"] = [series_obj]
        from datetime import datetime, timezone

        return st._requests(st._raw, datetime.now(timezone.utc), list(downloads))

    def test_unreleased_show_reads_not_out_yet(self):
        j = self.state_with(
            {"id": 4, "title": "T", "status": "upcoming", "firstAired": "2099-03-01T00:00:00Z", "statistics": {"episodeFileCount": 0, "episodeCount": 0}}
        )[0]
        self.assertEqual(j["label"], "Not out yet")
        self.assertIn("1 Mar 2099", j["detail"])
        self.assertFalse(j.get("searching"))

    def test_aired_show_still_looks(self):
        j = self.state_with({"id": 4, "title": "T", "firstAired": "2020-03-01T00:00:00Z", "statistics": {"episodeFileCount": 0, "episodeCount": 0}})[0]
        self.assertEqual(j["label"], "Looking for it")

    def test_part_here_counts(self):
        dl = {"kind": "show", "id": 4, "state": "downloading", "progress": 0.5, "eta": 60, "episodes": 12, "key": "k"}
        j = self.state_with(
            {"id": 4, "title": "T", "firstAired": "2020-01-01T00:00:00Z", "statistics": {"episodeFileCount": 16, "episodeCount": 28}}, downloads=[dl]
        )[0]
        self.assertEqual(j["detail"], "16 of 28 ready, 12 downloading")

    def test_stalled_request(self):
        dl = {
            "kind": "show",
            "id": 4,
            "state": "downloading",
            "progress": 0.43,
            "eta": None,
            "episodes": 1,
            "key": "k",
            "stalled": True,
            "message": "Waiting for someone to share it.",
        }
        j = self.state_with(
            {"id": 4, "title": "T", "firstAired": "2020-01-01T00:00:00Z", "statistics": {"episodeFileCount": 0, "episodeCount": 1}}, downloads=[dl]
        )[0]
        self.assertEqual(j["label"], "Waiting for someone to share it · 43%")


if __name__ == "__main__":
    unittest.main()
