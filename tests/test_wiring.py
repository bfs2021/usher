"""Everything Usher points at is found by ID, and anything that goes missing is named in /healthz."""

import unittest
from unittest import mock

from usher import config
from usher.actions import Actions, Problem
from usher.clients import ApiError, Jellyfin
from usher.wiring import Wiring

UID = "8" * 32


def profile_ids(tv=0, movies=0):
    """usher.toml's quality profile IDs (0: look the profile up by name), as a patch of the active settings."""
    values = {"pvr.tv.quality_profile_id": tv, "pvr.movies.quality_profile_id": movies}
    return mock.patch.object(config, "_active", config.Source("usher.toml (made up)", values))


def arr(name, profiles=None, roots=None, clients=None, apps=None):
    a = mock.Mock()
    a.name = name
    data = {
        "system/status": {"version": "4"},
        "qualityprofile": profiles if profiles is not None else [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}],
        "rootfolder": roots if roots is not None else [{"path": "/srv/media/TV Shows" if name == "sonarr" else "/srv/media/Movies", "accessible": True}],
        "downloadclient": clients if clients is not None else [{"implementation": "Transmission", "enable": True}],
        "applications": apps if apps is not None else [{"implementation": "Sonarr"}, {"implementation": "Radarr"}],
    }
    a.get.side_effect = lambda path, **kw: data[path]
    return a


def jellyfin(user_id=UID, users=None, disabled=False):
    jf = Jellyfin("http://jf", "k", user_id)
    users = users if users is not None else [{"Name": "Parents", "Id": UID}]

    def get(path, **kw):
        if path == "/Users":
            return users
        if path.startswith("/Users/"):
            uid = path[7:]
            if not any(u["Id"] == uid for u in users):
                raise ApiError("jellyfin: HTTP Error 404: Not Found")
            return {"Id": uid, "Policy": {"IsDisabled": disabled}}
        raise AssertionError(path)

    jf.get = get
    return jf


class JellyfinIdTests(unittest.TestCase):
    def test_id_survives_rename(self):
        jf = jellyfin(users=[{"Name": "Renamed", "Id": UID}])
        self.assertEqual(jf.user_id(), UID)

    def test_missing_id_says_use_id_and_asks_nothing(self):
        jf = jellyfin(user_id="")
        jf.get = mock.Mock(side_effect=AssertionError("no account lookup without an ID"))
        with self.assertRaises(ApiError) as e:
            jf.user_id()
        self.assertIn("media_server.user_id", str(e.exception))
        jf.get.assert_not_called()

    def test_dashed_id_normalised(self):
        self.assertEqual(Jellyfin("u", "k", "88888888-8888-8888-8888-888888888888").user_id(), UID)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.a = Actions(arr("sonarr"), arr("radarr"), mock.Mock(), mock.Mock())

    def test_by_id_survives_rename(self):
        s = arr("sonarr", profiles=[{"id": 4, "name": "Renamed"}, {"id": 1, "name": "HD-1080p"}])
        with profile_ids(tv=4):
            self.assertEqual(self.a.profile_choice(s), (4, "id"))

    def test_deleted_id_is_an_error_not_a_switch(self):
        with profile_ids(tv=9):
            with self.assertRaises(Problem):
                self.a.profile_choice(arr("sonarr"))

    def test_by_name_then_fallback(self):
        with profile_ids():
            self.assertEqual(self.a.profile_choice(arr("sonarr")), (4, "name"))
            self.assertEqual(self.a.profile_choice(arr("sonarr", profiles=[{"id": 2, "name": "Any"}])), (2, "fallback"))


class WiringTests(unittest.TestCase):
    def make(self, **over):
        parts = dict(sonarr=arr("sonarr"), radarr=arr("radarr"), prowlarr=arr("prowlarr"), tr=mock.Mock(), jf=jellyfin())
        parts.update(over)
        actions = Actions(parts["sonarr"], parts["radarr"], mock.Mock(), mock.Mock())
        return Wiring(parts["sonarr"], parts["radarr"], parts["prowlarr"], parts["tr"], parts["jf"], actions)

    def setUp(self):
        ids = profile_ids(tv=4, movies=4)
        ids.start()
        self.addCleanup(ids.stop)

    def test_all_good(self):
        r = self.make().check()
        self.assertEqual((r["status"], r["problems"], r["notes"]), ("ok", [], []))

    def test_deleted_account(self):
        r = self.make(jf=jellyfin(users=[{"Name": "Parents", "Id": "7" * 32}])).check()
        self.assertEqual(r["status"], "degraded")
        self.assertTrue(any(p.startswith("Jellyfin") for p in r["problems"]))

    def test_missing_id_is_a_problem(self):
        r = self.make(jf=jellyfin(user_id="")).check()
        self.assertEqual(r["status"], "degraded")
        self.assertIn("media_server.user_id", " ".join(r["problems"]))

    def test_missing_root_client_and_link(self):
        r = self.make(
            sonarr=arr("sonarr", roots=[{"path": "/elsewhere"}], clients=[{"implementation": "Transmission", "enable": False}]),
            prowlarr=arr("prowlarr", apps=[{"implementation": "Sonarr"}]),
        ).check()
        text = " | ".join(r["problems"])
        self.assertIn("library folder /srv/media/TV Shows", text)
        self.assertIn("no download client", text)
        self.assertIn("not linked to Radarr", text)

    def test_app_down_is_named(self):
        tr = mock.Mock()
        tr.call.side_effect = ApiError("transmission: 401 Unauthorized")
        r = self.make(tr=tr).check()
        self.assertEqual(r["problems"], ["transmission: 401 Unauthorized"])


if __name__ == "__main__":
    unittest.main()
