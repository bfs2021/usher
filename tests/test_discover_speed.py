"""Discover opens at once: rows are built in the background, kept on disk, and marked for the library on each reply."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from usher import config
from usher.discover import Discover

AU = config.Source("usher.toml (made up)", {"discover.region": "AU"})


def _item(i, kind):
    return {
        "id": i,
        "media_type": "movie" if kind == "movie" else "tv",
        "title": f"T{i}",
        "name": f"T{i}",
        "poster_path": f"/p{i}.jpg",
        "release_date": "2024-01-01",
        "first_air_date": "2024-01-01",
    }


class FakeTmdb:
    """Every list call takes `list_delay` seconds and every title lookup `detail_delay` seconds."""

    def __init__(self, list_delay=0.0, detail_delay=0.0):
        self.list_delay, self.detail_delay, self.calls = list_delay, detail_delay, 0

    def get(self, path, **params):
        self.calls += 1
        time.sleep(self.list_delay)
        if path == "/watch/providers/tv":
            return {"results": [{"provider_id": 8, "provider_name": "Streamy", "display_priority": 1}]}
        if path.startswith("/movie") or path == "/discover/movie":
            return {"results": [_item(100 + n, "movie") for n in range(3)]}
        return {"results": [_item(200 + n, "show") for n in range(3)]}

    def details(self, kind, tmdb):
        time.sleep(self.detail_delay)
        return {"watch/providers": {"results": {"AU": {"flatrate": [{"provider_name": "Streamy"}]}}}}


def _discover(tmdb, data_dir=None, movies=()):
    actions = mock.Mock()
    actions._art_key = lambda url: f"/api/art/remote/{url}" if url else None
    state = mock.Mock()
    state._raw = {"movies": list(movies), "series": []}
    state.jf.watched.return_value = []
    with mock.patch.dict("os.environ", {"TMDB_TOKEN": "t"}), mock.patch.object(config, "_active", AU):
        d = Discover(actions, state, data_dir)
    d._get, d._details = tmdb.get, tmdb.details
    return d


def _built(d, timeout=10):
    """Runs one background build and waits for all of it (streaming marks too)."""
    d._start_rows_build()
    deadline = time.monotonic() + timeout
    while d._rows_busy.locked() and time.monotonic() < deadline:
        time.sleep(0.01)


class DiscoverSpeedTests(unittest.TestCase):
    def test_answers_from_built_copy_with_current_marks(self):
        tmdb = FakeTmdb()
        d = _discover(tmdb)
        _built(d)
        tmdb.list_delay = tmdb.detail_delay = 1.0  # TMDb turns slow
        started = time.monotonic()
        out = d.rows()
        self.assertLess(time.monotonic() - started, 0.5)
        names = [r["name"] for r in out["rows"]]
        self.assertIn("Popular movies", names)
        self.assertIn("Popular on Streamy", names)
        card = next(c for r in out["rows"] for c in r["items"] if c["tmdb"] == 100)
        self.assertFalse(card["have"])
        self.assertEqual(card["streaming"], ["Streamy"])
        self.assertTrue(card["art"].startswith("/api/art/remote/https://image.tmdb.org/"))
        d.state._raw = {"movies": [{"tmdbId": 100, "hasFile": True}], "series": []}  # downloaded since the build
        card = next(c for r in d.rows()["rows"] for c in r["items"] if c["tmdb"] == 100)
        self.assertTrue(card["have"])

    def test_first_opening_waits_for_rows_not_streaming(self):
        tmdb = FakeTmdb(detail_delay=3.0)
        d = _discover(tmdb)
        started = time.monotonic()
        out = d.rows()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(out["rows"])
        self.assertTrue(all("streaming" not in c for r in out["rows"] for c in r["items"]))

    def test_stale_copy_served_while_rebuilding(self):
        tmdb = FakeTmdb()
        d = _discover(tmdb)
        _built(d)
        d._rows = (d._rows[0] - d.ROWS_TTL - 1, d._rows[1])
        tmdb.list_delay = 1.0
        started = time.monotonic()
        self.assertTrue(d.rows()["rows"])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(d._rows_busy.locked())  # a fresh build is running behind it
        _built(d)

    def test_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            _built(_discover(FakeTmdb(), tmp))
            saved = json.loads((Path(tmp) / "discover.json").read_text())
            self.assertEqual(saved["region"], "AU")
            self.assertNotIn("have", json.dumps(saved))  # library marks are added on each reply, never kept
            tmdb = FakeTmdb(list_delay=1.0, detail_delay=1.0)
            d = _discover(tmdb, tmp)  # after a restart
            started = time.monotonic()
            out = d.rows()
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(out["rows"])
            self.assertEqual(tmdb.calls, 0)
            self.assertTrue(out["rows"][0]["items"][0]["art"].startswith("/api/art/remote/"))

    def test_saved_rows_for_another_region_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "discover.json").write_text(
                json.dumps({"region": "GB", "language": "en-US", "built": time.time(), "rows": [{"id": "x", "name": "x", "items": []}]})
            )
            self.assertIsNone(_discover(FakeTmdb(), tmp)._rows)

    def test_build_time_logged(self):
        d = _discover(FakeTmdb())
        with self.assertLogs("usher.discover", "INFO") as logs:
            _built(d)
        self.assertTrue(any("discover built in" in m for m in logs.output))
        self.assertTrue(any("discover row Popular movies took" in m for m in logs.output))

    def test_picked_for_you_asks_every_seed_at_once(self):
        d = _discover(FakeTmdb())
        d.state.jf.watched.return_value = [("movie", i, f"M{i}") for i in range(10)]
        d.state.jf.library_tmdb.return_value = set()
        active, peak, lock = [0], [0], threading.Lock()

        def recs(kind, tmdb):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.2)
            with lock:
                active[0] -= 1
            return [{"kind": "movie", "tmdb": 1000 + tmdb, "art": "x"}]

        d._recs = recs
        for_you = dict((rid, fn) for rid, _name, fn in d._personal_jobs())["foryou"]
        started = time.monotonic()
        self.assertEqual(len(for_you()), 10)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertGreater(peak[0], 1)


if __name__ == "__main__":
    unittest.main()
