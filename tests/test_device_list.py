"""The Devices list under Account: one entry per device, old ones drop off."""

import tempfile
import time
import unittest
from pathlib import Path

from usher import auth
from usher.store import Store

PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
TABLET = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"
PASSWORD = "correct horse battery"


class DeviceListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.store.add_user("sam", "Sam", auth.hash_password(PASSWORD))
        self.store.add_user("alex", "Alex", auth.hash_password(PASSWORD))
        self.auth = auth.Auth(self.store, 90)
        self.sam = self.store.user_by_name("sam")["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def sign_in(self, username, device):
        token, err = self.auth.sign_in(username, PASSWORD, "10.0.0.20", device)
        self.assertIsNone(err)
        return token

    def test_signing_in_again_on_the_same_device_keeps_one_entry(self):
        first = self.sign_in("sam", PHONE)
        second = self.sign_in("sam", PHONE)
        self.assertEqual(len(self.store.sessions_for(self.sam)), 1)
        self.assertIsNone(self.auth.session(first))
        self.assertEqual(self.auth.session(second)["username"], "sam")

    def test_another_device_or_account_keeps_its_own_entry(self):
        self.sign_in("sam", PHONE)
        self.sign_in("sam", TABLET)
        self.sign_in("alex", PHONE)
        self.assertEqual(len(self.store.sessions_for(self.sam)), 2)
        self.assertEqual(len(self.store.sessions_for(self.store.user_by_name("alex")["id"])), 1)

    def test_a_device_not_used_for_60_days_drops_off_at_the_next_clean_up(self):
        old = self.sign_in("sam", PHONE)
        self.sign_in("sam", TABLET)
        self.store.run("UPDATE sessions SET last_seen = ? WHERE device = ?", (int(time.time()) - 61 * 86400, PHONE))
        self.store.purge_expired()
        self.assertEqual([s["device"] for s in self.store.sessions_for(self.sam)], [TABLET])
        self.assertIsNone(self.auth.session(old))

    def test_a_device_used_59_days_ago_stays(self):
        self.sign_in("sam", PHONE)
        self.store.run("UPDATE sessions SET last_seen = ?", (int(time.time()) - 59 * 86400,))
        self.store.purge_expired()
        self.assertEqual(len(self.store.sessions_for(self.sam)), 1)

    def test_sign_out_other_devices_leaves_only_this_one(self):
        self.sign_in("sam", TABLET)
        here = self.sign_in("sam", PHONE)
        keep = self.auth.session(here)["id_hash"]
        self.assertEqual(self.store.drop_other_sessions(self.sam, keep), 1)
        self.assertEqual([s["id_hash"] for s in self.store.sessions_for(self.sam)], [keep])
