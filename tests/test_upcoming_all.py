"""Upcoming → All upcoming: every English-language show premiering or starting a new season soon, from TMDb."""

import unittest
from datetime import date, timedelta
from unittest import mock

from usher import config
from usher.clients import ApiError
from usher.discover import Discover

AU = config.Source("usher.toml (made up)", {"discover.region": "AU"})
TODAY = date(2026, 11, 6)
SOON = (TODAY + timedelta(days=10)).isoformat()
LATER = (TODAY + timedelta(days=90)).isoformat()


def _discover():
    actions = mock.Mock()
    actions._art_key = lambda url: f"key:{url}" if url else None
    state = mock.Mock()
    state._raw = {"series": [{"tmdbId": 2, "statistics": {"episodeFileCount": 4}}], "movies": []}
    with mock.patch.dict("os.environ", {"TMDB_TOKEN": "t"}), mock.patch.object(config, "_active", AU):
        d = Discover(actions, state)
    d._today = lambda: TODAY
    return d


def _show(i, name, first, popularity=10.0, poster=True):
    return {
        "id": i,
        "name": name,
        "first_air_date": first,
        "poster_path": f"/p{i}.jpg" if poster else None,
        "overview": f"{name}.",
        "popularity": popularity,
        "vote_average": 0,
        "vote_count": 0,
    }


class UpcomingAllTests(unittest.TestCase):
    def setUp(self):
        self.d = _discover()
        self.lists = {
            "new": [_show(1, "Brand New", SOON, 50), _show(5, "No Picture", SOON, poster=False) | {"overview": ""}],
            "season": [
                _show(1, "Brand New", SOON),
                _show(2, "Returning", "2020-01-01", 80),
                _show(3, "Mid Season", "2021-01-01"),
                _show(4, "Too Late", "2019-01-01"),
            ],
        }
        details = {
            1: {
                "id": 1,
                "name": "Brand New",
                "first_air_date": SOON,
                "poster_path": "/p1.jpg",
                "networks": [{"name": "Netflix"}],
                "genres": [{"name": "Drama"}],
                "watch/providers": {"results": {"AU": {"flatrate": [{"provider_name": "Netflix"}]}}},
            },
            2: {
                "id": 2,
                "name": "Returning",
                "first_air_date": "2020-01-01",
                "poster_path": "/p2.jpg",
                "vote_average": 8.4,
                "vote_count": 900,
                "next_episode_to_air": {"season_number": 3, "episode_number": 1, "air_date": SOON},
            },
            3: {"id": 3, "name": "Mid Season", "next_episode_to_air": {"season_number": 2, "episode_number": 5, "air_date": SOON}},
            4: {"id": 4, "name": "Too Late", "next_episode_to_air": {"season_number": 2, "episode_number": 1, "air_date": LATER}},
        }

        def get(path, **params):
            self.assertEqual(path, "/discover/tv")
            self.assertEqual(params["with_original_language"], "en")
            self.assertIn("10763", params["without_genres"])
            if params.get("page", 1) > 1:
                return {"results": []}
            return {"results": self.lists["new" if "first_air_date.gte" in params else "season"]}

        self.d._get = mock.Mock(side_effect=get)
        self.d._details = mock.Mock(side_effect=lambda kind, i: details[i])

    def test_new_shows_and_new_seasons_only(self):
        out = self.d.upcoming_shows()
        self.assertTrue(out["enabled"])
        by = {c["tmdb"]: c for c in out["items"]}
        self.assertEqual(sorted(by), [1, 2])  # mid-season, too far off and blank listings left out
        self.assertTrue(by[1]["new"])
        self.assertEqual((by[1]["season"], by[1]["when"], by[1]["network"]), (1, SOON, "Netflix"))
        self.assertEqual(by[1]["streaming"], ["Netflix"])
        self.assertEqual(by[1]["genres"], ["Drama"])
        self.assertFalse(by[2]["new"])
        self.assertEqual(by[2]["season"], 3)
        self.assertEqual(by[2]["ratings"], {"tmdb": 8.4})

    def test_library_marks(self):
        by = {c["tmdb"]: c for c in self.d.upcoming_shows()["items"]}
        self.assertTrue(by[2]["have"])
        self.assertFalse(by[1]["have"] or by[1]["asked"])

    def test_kept_for_a_while(self):
        self.d.upcoming_shows()
        calls = self.d._get.call_count
        self.d.upcoming_shows()
        self.assertEqual(self.d._get.call_count, calls)

    def test_tmdb_down_gives_empty_list_and_retries(self):
        self.d._get = mock.Mock(side_effect=ApiError("tmdb: HTTP 503"))
        self.assertEqual(self.d.upcoming_shows()["items"], [])
        self.d.upcoming_shows()
        self.assertEqual(self.d._get.call_count, 4)  # not cached: both lists asked for again

    def test_poster_from_images_when_listing_has_none(self):
        self.lists["new"].append(_show(6, "Blank Poster", SOON, poster=False))
        get = self.d._get.side_effect

        def with_images(path, **params):
            if path == "/tv/6/images":
                return {"posters": [{"file_path": "/alt6.jpg", "vote_average": 5}]}
            return get(path, **params)

        self.d._get = mock.Mock(side_effect=with_images)
        details = self.d._details.side_effect
        self.d._details = mock.Mock(side_effect=lambda kind, i: {"id": 6, "name": "Blank Poster", "first_air_date": SOON} if i == 6 else details(kind, i))
        by = {c["tmdb"]: c for c in self.d.upcoming_shows()["items"]}
        self.assertTrue(by[6]["art"].endswith("/alt6.jpg"))

    def test_show_with_no_picture_at_all_left_out(self):
        self.lists["new"].append(_show(7, "Nothing Yet", SOON, poster=False))
        get = self.d._get.side_effect
        self.d._get = mock.Mock(side_effect=lambda path, **params: {"posters": [], "backdrops": []} if path == "/tv/7/images" else get(path, **params))
        details = self.d._details.side_effect
        self.d._details = mock.Mock(side_effect=lambda kind, i: {"id": 7, "name": "Nothing Yet", "first_air_date": SOON} if i == 7 else details(kind, i))
        by = {c["tmdb"]: c for c in self.d.upcoming_shows()["items"]}
        self.assertNotIn(7, by)
        self.assertIn(1, by)

    def test_old_list_served_while_rebuilding(self):
        self.d.upcoming_shows()
        cache = self.d._upcoming
        stamp, items = cache._entries["list"]
        cache._entries["list"] = (stamp - 4 * 3600, items)
        with mock.patch("usher.discover.threading.Thread") as thread:
            out = self.d.upcoming_shows()
        self.assertEqual(len(out["items"]), 2)  # answered straight away from the old list
        thread.return_value.start.assert_called_once()  # and a rebuild started in the background

    def test_not_set_up(self):
        self.d.token = ""
        self.assertEqual(self.d.upcoming_shows(), {"enabled": False, "items": []})


if __name__ == "__main__":
    unittest.main()
