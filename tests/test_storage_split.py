"""Status → Storage splits the used space of the library disk by what fills it."""

import unittest
from unittest import mock

from tests.test_state_basics import make_state
from tests.webjs import WEB
from usher.state.storage import storage_split

GB = 1_000_000_000


def settings(tv="", movie=""):
    return mock.Mock(family_tv_folder=tv, family_movie_folder=movie)


class SplitTests(unittest.TestCase):
    def raw(self, **kw):
        base = {
            "movies": [
                {"path": "/srv/media/Movies/A (2020)", "sizeOnDisk": 10 * GB},
                {"path": "/srv/media/Family Movies/B (2019)", "sizeOnDisk": 4 * GB},
            ],
            "series": [{"path": "/srv/media/TV Shows/C", "statistics": {"sizeOnDisk": 20 * GB}}],
            "torrents": {"x": {"sizeWhenDone": 8 * GB, "leftUntilDone": 3 * GB}},
            "recycle": {"items": 2, "bytes": GB},
        }
        return {**base, **kw}

    def split(self, used, **kw):
        return {p["key"]: p["bytes"] for p in storage_split(settings(movie="/srv/media/Family Movies"), self.raw(**kw), used)}

    def test_each_category_has_its_own_size(self):
        got = self.split(100 * GB)
        self.assertEqual(got, {"movies": 10 * GB, "tv": 20 * GB, "family": 4 * GB, "recycle": GB, "downloads": 5 * GB, "other": 60 * GB})

    def test_a_family_folder_that_is_off_counts_with_movies_and_tv(self):
        got = {p["key"]: p["bytes"] for p in storage_split(settings(), self.raw(), 100 * GB)}
        self.assertEqual((got["movies"], got["family"]), (14 * GB, 0))

    def test_parts_never_add_up_to_more_than_the_disk_holds(self):
        # Downloads still shared with the library are the same data, so they give way first.
        parts = storage_split(settings(), self.raw(), 36 * GB)
        self.assertEqual(sum(p["bytes"] for p in parts), 36 * GB)
        self.assertEqual({p["key"]: p["bytes"] for p in parts}["downloads"], 1 * GB)

    def test_status_carries_the_split_and_the_warning_limit(self):
        st = make_state()
        r = st._raw
        r["ok"].update(transmission=True, prowlarr=True, sonarr=True, radarr=True, jellyfin_up=True)
        r["disk"] = [{"path": "/", "freeSpace": 50 * GB, "totalSpace": 150 * GB}]
        r["movies"], r["series"], r["recycle"] = self.raw()["movies"], self.raw()["series"], {"items": 0, "bytes": 0}
        storage = st._status(r)["storage"]
        self.assertEqual(storage["lowAt"], 30 * GB)
        self.assertEqual(sum(p["bytes"] for p in storage["split"]), 100 * GB)
        self.assertEqual(storage["split"][-1]["key"], "other")


class StorageScreenTests(unittest.TestCase):
    def test_the_section_is_its_own_module_with_a_sized_usage_bar(self):
        js = (WEB / "storage.js").read_text()
        self.assertIn("Family libraries", js)
        self.assertIn("storage.lowAt", js)  # the warning names the real limit, not a fixed number
        self.assertNotIn("25 GB", (WEB / "status.js").read_text() + js)
        self.assertIn("/storage.css", (WEB / "index.html").read_text())
        self.assertIn('"/storage.css"', (WEB / "sw.js").read_text())


if __name__ == "__main__":
    unittest.main()
