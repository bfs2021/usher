"""Waiting is normal: no reminders, warnings or attention lines for a request that's slow to find or download."""

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tests import test_notify
from tests.test_state_basics import make_state
from tests.test_notify import req, snap
from tests.test_tidy_watched import queue_rec
from usher.notify import Notifier
from usher.store import Store


class NoReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.store.add_user("alex", "Alex", "x")
        self.n = Notifier(self.store, test_notify.Settings(self.tmp.name), "https://usher.example")
        self.sent = []
        self.n._send = lambda user, msg, topic=None: self.sent.append((user["username"], msg))

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, s):
        with mock.patch("usher.notify.datetime") as dt:  # midday
            dt.now.return_value = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
            self.n.check(s, {"ready": []})

    def test_no_notification_for_a_request_still_looking_after_four_days(self):
        self.run_check(snap([req(1, 0, stuck=True)]))
        self.run_check(snap([req(1, 0, stuck=True)]))
        self.assertEqual(self.sent, [])

    def test_still_looking_is_not_a_notification_setting(self):
        from usher.store import PREF_KINDS

        self.assertNotIn("stuck", PREF_KINDS)
        self.store.set_prefs(1, {"stuck": 0, "ready": 0})  # an old client sending it is harmless
        self.assertEqual(self.store.prefs(1)["ready"], 0)


class StalledDownloadTests(unittest.TestCase):
    def stalled(self, **kw):
        st = make_state()
        st._raw["queue_tv"] = [queue_rec(**kw)]
        st._downloads(st._raw)
        st._progress["abc"] = (0.4, time.time() - 5 * 3600)  # five hours without progress
        d = st._downloads(st._raw)[0][0]
        st.snapshot = {"downloading": [d]}
        return st, d

    def test_stalled_download_shows_as_waiting_not_a_problem(self):
        st, d = self.stalled()
        self.assertTrue(d["stalled"])
        self.assertEqual(d["state"], "downloading")
        self.assertIn("Waiting for someone to share it", d["message"])
        self.assertNotIn("Stop it", d["message"])
        self.assertEqual(st.attention(), [])

    def test_wrong_title_still_needs_attention(self):
        st, d = self.stalled(title="Giant.Jack.S01E01.1080p")
        self.assertEqual(st.attention(), ['wrong title? Trash Truck (S1E1) is downloading "Giant.Jack.S01E01.1080p"'])


class RequestLabelTests(unittest.TestCase):
    def journey(self, created_ago, downloads=()):
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
                "created": time.time() - created_ago,
                "choice": "start",
                "jf_id": None,
                "searched": None,
                "done_at": None,
            }
        ]
        st._raw["series"] = [{"id": 4, "title": "T", "firstAired": "2020-01-01T00:00:00Z", "statistics": {"episodeFileCount": 0, "episodeCount": 1}}]
        return st._requests(st._raw, datetime.now(timezone.utc), list(downloads))[0]

    def test_stalled_request_reads_as_waiting(self):
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
        j = self.journey(3600, [dl])
        self.assertEqual(j["label"], "Waiting for someone to share it · 43%")
        self.assertFalse(j["stuck"])
        self.assertFalse(j["problem"])
        self.assertEqual(j["keys"], ["k"])  # Stop downloading stays available

    def test_long_search_reads_calmly(self):
        j = self.journey(4 * 86400)
        self.assertEqual(j["label"], "Still looking")
        self.assertFalse(j["problem"])


if __name__ == "__main__":
    unittest.main()
