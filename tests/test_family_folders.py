"""Family-friendly folders: G/PG titles are filed there by themselves; a missing one is never a silent fallback."""

import unittest
from unittest import mock

from usher import config
from usher.actions import Actions, Problem

USER = {"username": "alex", "user_id": 1}
ROOTS = [{"path": "/media/TV"}, {"path": "/media/Movies"}, {"path": "/media/Family TV"}, {"path": "/media/Family Movies"}]


def _actions(values, lookup, existing=None):
    """Actions with these usher.toml values, and Sonarr and Radarr answering with lookup (and existing)."""
    sonarr, radarr, store, state = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
    state._raw = {"series": [], "movies": [existing] if existing else []}
    store.open_requests.return_value = []
    store.add_request.return_value = 7
    for app, name in ((sonarr, "sonarr"), (radarr, "radarr")):
        app.name = name

        def get(path, _app=app, **kw):
            if path == "rootfolder":
                return ROOTS
            if path == "qualityprofile":
                return [{"id": 4, "name": "HD-1080p"}]
            if path.endswith("/lookup"):
                return [dict(lookup)]
            if path.startswith("movie/"):
                return dict(existing)
            raise AssertionError(path)

        app.get.side_effect = get
        app.send.return_value = {"id": 11, "title": lookup.get("title")}
    with mock.patch.object(config, "_active", config.Source("usher.toml (made up)", values)):
        a = Actions(sonarr, radarr, store, state)
    a.profile_choice = lambda app: (4, "id")
    return a


FAMILY = {
    "library.tv_folder": "/media/TV",
    "library.movie_folder": "/media/Movies",
    "family.tv_folder": "/media/Family TV",
    "family.movie_folder": "/media/Family Movies",
}


class FamilyFolderTests(unittest.TestCase):
    def test_show_family_friendly_goes_in_the_family_folder(self):
        a = _actions(FAMILY, {"tvdbId": 5, "title": "Bluey"})
        a.get("show", 5, "start", USER, family=True)
        body = a.sonarr.send.call_args.args[2]
        self.assertEqual(body["rootFolderPath"], "/media/Family TV")

    def test_rated_show_goes_in_the_family_folder_by_itself(self):
        a = _actions(FAMILY, {"tvdbId": 5, "title": "Bluey", "certification": "TV-Y"})
        a.get("show", 5, "start", USER)
        self.assertEqual(a.sonarr.send.call_args.args[2]["rootFolderPath"], "/media/Family TV")

    def test_pg_movie_goes_in_the_family_folder_by_itself(self):
        a = _actions(FAMILY, {"tmdbId": 9, "title": "Paddington", "certification": "PG"})
        a.get("movie", 9, "", USER)
        self.assertEqual(a.radarr.send.call_args.args[2]["rootFolderPath"], "/media/Family Movies")

    def test_adult_or_unrated_titles_go_in_the_main_folder(self):
        for cert in ("TV-MA", "M", None):
            a = _actions(FAMILY, {"tmdbId": 9, "title": "Something", "certification": cert})
            a.get("movie", 9, "", USER)
            self.assertEqual(a.radarr.send.call_args.args[2]["rootFolderPath"], "/media/Movies")

    def test_unrated_show_goes_in_the_main_folder(self):
        a = _actions(FAMILY, {"tvdbId": 5, "title": "Severance"})
        a.get("show", 5, "latest", USER)
        self.assertEqual(a.sonarr.send.call_args.args[2]["rootFolderPath"], "/media/TV")

    def test_new_movie_family_friendly(self):
        a = _actions(FAMILY, {"tmdbId": 9, "title": "Stick Man"})
        a.get("movie", 9, "", USER, family=True)
        self.assertEqual(a.radarr.send.call_args.args[2]["rootFolderPath"], "/media/Family Movies")

    def test_known_movie_without_file_is_refiled_family_friendly(self):
        existing = {
            "id": 3,
            "tmdbId": 9,
            "title": "Stick Man",
            "hasFile": False,
            "monitored": False,
            "path": "/media/Movies/Stick Man (2015)",
            "rootFolderPath": "/media/Movies",
        }
        a = _actions(FAMILY, {"tmdbId": 9, "title": "Stick Man", "id": 3}, existing)
        a.get("movie", 9, "", USER, family=True)
        put = a.radarr.send.call_args_list[0]
        self.assertEqual(put.args[:2], ("PUT", "movie/3"))
        self.assertEqual(put.args[2]["path"], "/media/Family Movies/Stick Man (2015)")
        self.assertTrue(put.args[2]["monitored"])

    def test_missing_family_folder_is_an_error_not_a_fallback(self):
        a = _actions(dict(FAMILY, **{"family.tv_folder": "/media/Nope"}), {"tvdbId": 5, "title": "Bluey"})
        with self.assertRaises(Problem):
            a.get("show", 5, "start", USER, family=True)
        a.sonarr.send.assert_not_called()

    def test_switch_hidden_and_ignored_when_not_set_up(self):
        a = _actions({"family.tv_folder": "", "family.movie_folder": ""}, {"tvdbId": 5, "title": "Bluey"})
        self.assertEqual(a.family_enabled(), {"show": False, "movie": False})
        a.get("show", 5, "start", USER, family=True)
        self.assertNotIn("Family", a.sonarr.send.call_args.args[2]["rootFolderPath"])


class CertificationTests(unittest.TestCase):
    def test_search_results_carry_the_rating(self):
        a = _actions(FAMILY, {"tvdbId": 5, "title": "Bluey", "certification": "TV-Y"})
        a.radarr.get.side_effect = lambda path, **kw: [{"tmdbId": 9, "title": "Stick Man", "certification": "G"}] if path == "movie/lookup" else []
        a.sonarr.get.side_effect = lambda path, **kw: [{"tvdbId": 5, "title": "Bluey", "certification": "TV-Y"}] if path == "series/lookup" else []
        a.store.requesters.return_value = {}
        certs = {r["title"]: r["cert"] for r in a.search("st")["results"]}
        self.assertEqual(certs, {"Bluey": "TV-Y", "Stick Man": "G"})


if __name__ == "__main__":
    unittest.main()
