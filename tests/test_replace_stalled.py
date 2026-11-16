"""A stalled download is replaced by a healthier copy, within disk and clean-up limits."""

import tempfile
import time
import unittest
from pathlib import Path

from tests.test_state_basics import make_state
from tests.test_tidy_watched import queue_rec
from usher import config
from usher.clients import ApiError
from usher.replace import Replacer

OLD, NEW = "a" * 40, "b" * 40
HOUR = 3600
MOVIE = {"id": 7, "title": "Paper Lanterns", "year": 2025}


def release(**kw):
    r = {
        "guid": "g-new",
        "indexerId": 3,
        "title": "Paper.Lanterns.2025.1080p.WEB",
        "size": 4e9,
        "seeders": 40,
        "protocol": "torrent",
        "rejected": False,
        "rejections": [],
        "infoHash": NEW,
    }
    r.update(kw)
    return r


class FakeTransmission:
    """Torrents with real files in two download folders; removing one with its data deletes them."""

    def __init__(self, root):
        self.incomplete, self.done = Path(root) / "Incomplete", Path(root) / "Downloads"
        self.incomplete.mkdir()
        self.done.mkdir()
        self.torrents, self.free, self.calls = {}, 500e9, []

    def add(self, h, name, pct=0.4, status=4, seeders=0, size=4e9, rate=0):
        self.torrents[h] = {
            "hashString": h,
            "name": name,
            "downloadDir": str(self.done),
            "percentDone": pct,
            "status": status,
            "rateDownload": rate,
            "sizeWhenDone": size,
            "leftUntilDone": int(size * (1 - pct)),
            "peersSendingToUs": 0,
            "trackerStats": [{"seederCount": seeders}],
        }
        (self.incomplete / f"{name}.part").write_bytes(b"x")

    def finish(self, h):
        t = self.torrents[h]
        t.update(percentDone=1.0, status=6, leftUntilDone=0)
        (self.incomplete / f"{t['name']}.part").rename(self.done / t["name"])

    def remove(self, h, data):
        t = self.torrents.pop(h, None)
        if t and data:
            for p in (self.incomplete / f"{t['name']}.part", self.done / t["name"]):
                p.unlink(missing_ok=True)

    def files(self, name):
        return [p for p in (self.incomplete / f"{name}.part", self.done / name) if p.exists()]

    def call(self, method, **a):
        self.calls.append(method)
        if method == "torrent-get":
            return {"torrents": [dict(self.torrents[h]) for h in a.get("ids", []) if h in self.torrents]}
        if method == "torrent-remove":
            for h in a["ids"]:
                self.remove(h, a.get("delete-local-data"))
            return {}
        if method == "free-space":
            return {"path": a["path"], "size-bytes": self.free}
        raise ApiError(method)

    def raw(self):
        return {h: dict(t) for h, t in self.torrents.items()}


class FakeRadarr:
    name = "radarr"

    def __init__(self, tr):
        self.tr, self.queue, self.releases, self.has_file = tr, [], [release()], False
        self.grabbed, self.deleted, self.searches = [], [], 0
        self.remove_data = True  # whether removing from the client deletes the files

    def rec(self, h, qid):
        return {
            "id": qid,
            "movieId": 7,
            "downloadId": h.upper(),
            "status": "downloading",
            "protocol": "torrent",
            "trackedDownloadState": "downloading",
            "size": 4e9,
            "sizeleft": 2e9,
            "title": "Paper.Lanterns.2025",
            "movie": MOVIE,
        }

    def get(self, path, _timeout=None, **params):
        if path == "release":
            self.searches += 1
            assert params == {"movieId": 7}, params
            return self.releases
        if path == "config/mediamanagement":
            return {"minimumFreeSpaceWhenImporting": 100}
        if path == "movie/7":
            return {"id": 7, "hasFile": self.has_file}
        raise ApiError(path)

    def send(self, method, path, body=None, **params):
        if method == "POST" and path == "release":
            self.grabbed.append(body)
            return {}
        if method == "DELETE" and path.startswith("queue/"):
            qid = int(path.split("/")[1])
            rec = next(r for r in self.queue if r["id"] == qid)
            self.queue.remove(rec)
            self.deleted.append((rec["downloadId"].lower(), params))
            if params.get("removeFromClient") == "true":
                self.tr.remove(rec["downloadId"].lower(), self.remove_data)
            return {}
        raise ApiError(path)


class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tr = FakeTransmission(root)
        self.radarr = FakeRadarr(self.tr)
        self.t = 1_000_000.0
        self.settings({})
        self.r = Replacer(None, self.radarr, self.tr, root, clock=lambda: self.t)
        self.tr.add(OLD, "Paper.Lanterns.2025.1080p-OLD", seeders=0)
        self.radarr.queue.append(self.radarr.rec(OLD, 1))

    def tearDown(self):
        config.activate(None)
        self.tmp.cleanup()

    def settings(self, values):
        base = {"media_server.user_id": "a" * 32, "downloads.replace": "on", "downloads.folders": [str(self.tr.incomplete), str(self.tr.done)]}
        config.activate(config.Source("usher.toml", {**base, **values}))

    def check(self):
        self.r.check({"queue_movies": list(self.radarr.queue), "queue_tv": [], "torrents": self.tr.raw()})

    def stall(self, hours=7):
        self.check()  # first seen now
        self.t += hours * HOUR
        self.check()

    def test_stalled_movie_is_replaced_and_the_loser_cleaned_up(self):
        self.stall()
        self.assertEqual(self.radarr.grabbed, [{"guid": "g-new", "indexerId": 3}])
        # Radarr sends the new copy to Transmission; both race.
        self.tr.add(NEW, "Paper.Lanterns.2025.1080p.WEB", seeders=40)
        self.radarr.queue.append(self.radarr.rec(NEW, 2))
        self.check()
        self.assertTrue(self.r.racing(OLD) and self.r.racing(NEW))
        # The new copy finishes first and is imported: Radarr drops it from its queue.
        self.tr.finish(NEW)
        self.radarr.queue = [q for q in self.radarr.queue if q["id"] != 2]
        self.radarr.has_file = True
        self.check()
        self.assertEqual(self.radarr.deleted, [(OLD, {"removeFromClient": "true", "blocklist": "true"})])
        self.assertNotIn(OLD, self.tr.torrents)
        self.assertEqual(self.tr.files("Paper.Lanterns.2025.1080p-OLD"), [])  # nothing left for its hash
        self.assertIn(NEW, self.tr.torrents)  # the winner seeds as usual
        self.assertFalse(self.r.racing(OLD))
        self.assertEqual(self.r.attention(), [])

    def test_when_the_stalled_copy_wins_the_new_one_is_removed_without_blocklisting(self):
        self.stall()
        self.tr.add(NEW, "Paper.Lanterns.2025.1080p.WEB", seeders=40)
        self.radarr.queue.append(self.radarr.rec(NEW, 2))
        self.check()
        self.radarr.queue = [q for q in self.radarr.queue if q["id"] != 1]
        self.radarr.has_file = True
        self.check()
        self.assertEqual(self.radarr.deleted, [(NEW, {"removeFromClient": "true", "blocklist": "false"})])
        self.assertEqual(self.tr.files("Paper.Lanterns.2025.1080p.WEB"), [])

    def test_a_failed_cleanup_is_listed_with_the_path(self):
        self.radarr.remove_data = False  # Radarr removes the torrent but not its files
        self.stall()
        self.tr.add(NEW, "Paper.Lanterns.2025.1080p.WEB", seeders=40)
        self.radarr.queue.append(self.radarr.rec(NEW, 2))
        self.check()
        self.radarr.queue = [q for q in self.radarr.queue if q["id"] != 2]
        self.radarr.has_file = True
        self.check()
        left = str(self.tr.incomplete / "Paper.Lanterns.2025.1080p-OLD.part")
        self.assertEqual(len(self.r.attention()), 1)
        self.assertIn(left, self.r.attention()[0])

    def test_a_copy_gone_without_import_leaves_the_other_alone(self):
        self.stall()
        self.tr.add(NEW, "Paper.Lanterns.2025.1080p.WEB", seeders=40)
        self.radarr.queue.append(self.radarr.rec(NEW, 2))
        self.check()
        self.radarr.queue = [q for q in self.radarr.queue if q["id"] != 2]  # stopped by a person
        self.check()
        self.assertEqual(self.radarr.deleted, [])
        self.assertIn(OLD, self.tr.torrents)

    def test_not_before_stalled_after_hours(self):
        self.stall(hours=5)
        self.assertEqual((self.radarr.grabbed, self.radarr.searches), ([], 0))

    def test_no_replacement_when_the_space_check_fails(self):
        self.tr.free = 25e9  # minus 2.4 GB still to come and 4 GB for the new copy leaves less than 20 GB
        self.stall()
        self.assertEqual(self.radarr.grabbed, [])
        self.assertEqual(self.r.attention(), ["not enough space to try another copy of Paper Lanterns"])
        self.t += 7 * HOUR
        self.check()
        self.assertEqual(len(self.r.attention()), 1)  # listed once
        self.tr.torrents[OLD]["percentDone"] = 0.5  # it moves again: nothing to act on
        self.check()
        self.assertEqual(self.r.attention(), [])

    def test_no_replacement_past_max_parallel(self):
        self.settings({"downloads.max_parallel": 0})
        self.stall()
        self.assertEqual(self.radarr.grabbed, [])

    def test_no_replacement_past_max_attempts_until_retry_after_days(self):
        self.settings({"downloads.max_attempts": 1})
        self.stall()
        self.assertEqual(len(self.radarr.grabbed), 1)
        self.t += 2 * HOUR  # the new copy never shows up
        self.check()
        self.assertFalse(self.r.racing(OLD))
        self.t += 7 * HOUR
        self.check()
        self.assertEqual(len(self.radarr.grabbed), 1)
        self.t += 3 * 86400
        self.check()
        self.assertEqual(len(self.radarr.grabbed), 2)

    def test_no_replacement_for_a_paused_or_manual_download(self):
        self.tr.torrents[OLD]["status"] = 0  # paused by a person
        self.tr.add("c" * 40, "Something.Added.By.Hand", seeders=0)  # in Transmission, not in Radarr's queue
        self.stall()
        self.assertEqual((self.radarr.grabbed, self.radarr.searches), ([], 0))

    def test_never_for_a_nearly_done_download_still_moving(self):
        self.tr.torrents[OLD].update(percentDone=0.95, rateDownload=1000)
        self.stall()
        self.assertEqual(self.radarr.searches, 0)

    def test_report_only_changes_nothing_but_logs_the_decision(self):
        self.settings({"downloads.replace": "report"})
        with self.assertLogs("usher.replace", "INFO") as logs:
            self.stall()
        self.assertEqual((self.radarr.grabbed, self.radarr.deleted), ([], []))
        self.assertNotIn("torrent-remove", self.tr.calls)
        line = "\n".join(logs.output)
        self.assertIn("would try a better copy (report only)", line)
        self.assertIn("Paper Lanterns", line)
        self.assertIn("Paper.Lanterns.2025.1080p.WEB", line)
        self.assertIn("must stay free", line)

    def test_off_does_nothing(self):
        self.settings({"downloads.replace": "off"})
        self.stall()
        self.assertEqual(self.radarr.searches, 0)

    def test_only_a_clearly_healthier_acceptable_release_is_picked(self):
        self.tr.torrents[OLD]["trackerStats"] = [{"seederCount": 4}]  # needs at least 12
        self.radarr.releases = [
            release(guid="few", seeders=11),
            release(guid="rejected", seeders=90, rejected=True, rejections=["Not an upgrade for existing file"]),
            release(guid="wrong", seeders=90, title="Other.Film.2025.1080p"),
            release(guid="huge", seeders=90, size=9e9),
            release(guid="queued", seeders=30, rejected=True, rejections=[{"reason": "Release in queue already"}]),
        ]
        self.stall()
        self.assertEqual([g["guid"] for g in self.radarr.grabbed], ["queued"])

    def test_the_choice_survives_a_restart(self):
        self.stall()
        again = Replacer(None, self.radarr, self.tr, Path(self.tmp.name), clock=lambda: self.t)
        self.assertTrue(again.racing(OLD))


class ScopeTests(unittest.TestCase):
    def scope(self, recs):
        return Replacer._scope({"kind": "show", "recs": recs})

    def test_episode_and_season_pack(self):
        one = self.scope([queue_rec(seriesId=4, episodeId=40)])
        self.assertEqual((one["item"], one["search"], one["pack"]), ("show:4:e40", {"episodeId": 40}, False))
        pack = self.scope([queue_rec(episodeId=40 + n, episode={"seasonNumber": 2, "episodeNumber": n}) for n in (1, 2)])
        self.assertEqual((pack["item"], pack["search"]), ("show:4:s2", {"seriesId": 4, "seasonNumber": 2}))

    def test_several_seasons_in_one_download_are_left_alone(self):
        self.assertIsNone(self.scope([queue_rec(episodeId=1, episode={"seasonNumber": 1}), queue_rec(episodeId=2, episode={"seasonNumber": 2})])["item"])


class ShownInAppTests(unittest.TestCase):
    def test_both_copies_read_as_trying_a_better_copy(self):
        st = make_state()
        st.replacer = Replacer(None, None, None, tempfile.mkdtemp(), clock=time.time)
        st.replacer.data["items"]["show:4:e40"] = {"active": {"old": "abc", "new": "def", "name": "x"}}
        st._raw["queue_tv"] = [queue_rec(), queue_rec(id=2, downloadId="DEF")]
        out = {d["key"]: d for d in st._downloads(st._raw)[0]}
        self.assertTrue(out["abc"]["racing"] and out["def"]["racing"])
        self.assertIn("Trying a better copy", out["abc"]["message"])
        self.assertEqual(out["abc"]["state"], "downloading")  # not a problem
        st.snapshot = {"downloading": list(out.values())}
        self.assertEqual(st.attention(), [])


if __name__ == "__main__":
    unittest.main()
