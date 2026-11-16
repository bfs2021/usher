"""New episodes that RSS missed are searched for, even after the show's request is finished."""

import tempfile
import unittest
from unittest import mock

from usher.clients import ApiError
from usher.missing import MissingSearcher

NOW = 1_800_000_000.0
HOUR = 3600


def iso(ts):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def episode(eid, aged_hours, series=4, season=1, **kw):
    rec = {
        "id": eid,
        "seriesId": series,
        "seasonNumber": season,
        "monitored": True,
        "airDateUtc": iso(NOW - aged_hours * HOUR),
        "series": {"title": "Trash Truck"},
    }
    rec.update(kw)
    return rec


class MissingSearchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.sonarr = mock.Mock()
        self.clock = [NOW]
        self.m = MissingSearcher(self.sonarr, self.dir.name, clock=lambda: self.clock[0])

    def run_pass(self, missing, queue=()):
        self.m.tick({"missing_tv": list(missing), "queue_tv": list(queue)}, force=True)

    def searches(self):
        return [c.args[2] for c in self.sonarr.send.call_args_list]

    def test_searches_after_grace_then_backs_off(self):
        ep = episode(10, 4)
        self.run_pass([episode(10, 2)])
        self.assertEqual(self.searches(), [])  # RSS gets the first chance
        self.run_pass([ep])
        self.assertEqual(self.searches(), [{"name": "EpisodeSearch", "episodeIds": [10]}])
        self.clock[0] += 5 * HOUR
        self.run_pass([ep])
        self.assertEqual(len(self.searches()), 1)  # back-off not due yet
        self.clock[0] += 2 * HOUR
        self.run_pass([ep])
        self.assertEqual(len(self.searches()), 2)  # 6 hours on

    def test_back_off_survives_a_restart(self):
        self.run_pass([episode(10, 4)])
        again = MissingSearcher(self.sonarr, self.dir.name, clock=lambda: NOW + HOUR)
        again.tick({"missing_tv": [episode(10, 5)], "queue_tv": []}, force=True)
        self.assertEqual(len(self.searches()), 1)

    def test_never_unmonitored_unaired_or_queued(self):
        self.run_pass([episode(1, 4, monitored=False), episode(2, -5), episode(3, 9), episode(4, 9, airDateUtc=None)], queue=[{"episodeId": 3}])
        self.assertEqual(self.searches(), [])

    def test_one_search_per_season(self):
        self.run_pass([episode(1, 5), episode(2, 6), episode(3, 7, season=2)])
        self.assertEqual(self.searches(), [{"name": "EpisodeSearch", "episodeIds": [1, 2]}, {"name": "EpisodeSearch", "episodeIds": [3]}])

    def test_caps_searches_per_pass(self):
        self.run_pass([episode(i, 5, series=i) for i in range(1, 9)])
        self.assertEqual(len(self.searches()), 5)
        self.run_pass([episode(i, 5, series=i) for i in range(1, 9)])
        self.assertEqual(len(self.searches()), 8)  # the rest on the next pass, none twice

    def test_failed_search_is_tried_again_next_pass(self):
        self.sonarr.send.side_effect = ApiError("sonarr: down")
        self.run_pass([episode(10, 5)])
        self.sonarr.send.side_effect = None
        self.run_pass([episode(10, 5)])
        self.assertEqual(len(self.searches()), 2)


if __name__ == "__main__":
    unittest.main()
