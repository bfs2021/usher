"""Transmission 4.1+ speaks JSON-RPC 2.0, older ones the old protocol; Usher reads the same either way."""

import importlib
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import mock_apps
from usher.clients import ApiError, Transmission
from usher.diagnostics import Diagnostics
from usher.wiring import Wiring

FIELDS = {"hashString", "percentDone", "eta", "rateDownload", "status", "error", "errorString", "leftUntilDone", "sizeWhenDone"}


class TransmissionProtocolTests(unittest.TestCase):
    def serve(self, handler):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        handler.seen = []
        return Transmission(f"http://127.0.0.1:{srv.server_port}/transmission/rpc", "", ""), handler

    def both(self):
        m = importlib.reload(mock_apps)
        return {"4.1": self.serve(m.TrHandler), "4.0": self.serve(m.TrHandler40)}

    def test_same_torrent_list_from_either_version(self):
        lists = {}
        for name, (tr, handler) in self.both().items():
            got = tr.torrents()
            self.assertTrue(got, name)
            self.assertEqual(set(got[0]), FIELDS, name)
            lists[name] = sorted(t["hashString"] for t in got)
        self.assertEqual(lists["4.1"], lists["4.0"])

    def test_protocol_follows_the_handshake(self):
        found = self.both()
        for name, style in (("4.1", "jsonrpc"), ("4.0", "old")):
            tr, handler = found[name]
            tr.torrents()
            tr.call("session-get", fields=["version"])
            self.assertEqual({s for _, s in handler.seen}, {style}, name)

    def test_session_details_and_free_space_keep_their_old_names(self):
        for name, (tr, _) in self.both().items():
            sess = tr.call("session-get", fields=["version", "download-dir"])
            self.assertEqual(sess["download-dir"], "/data/torrents", name)
            self.assertGreater(tr.call("free-space", path="/data/torrents")["size-bytes"], 0, name)
            stats = tr.call("session-stats")
            self.assertEqual(stats["activeTorrentCount"], 2, name)

    def test_status_details_and_wiring_through_both(self):
        for name, (tr, _) in self.both().items():
            diag = Diagnostics(mock.Mock(), mock.Mock(), mock.Mock(), tr, mock.Mock())
            facts = dict(diag._downloads()["facts"])
            self.assertTrue(facts["Transmission"].startswith(name), facts)
            self.assertEqual(facts["Active"], "2")
            wiring = Wiring(mock.Mock(), mock.Mock(), mock.Mock(), tr, mock.Mock(), mock.Mock())
            self.assertEqual(wiring._transmission(), ([], []))

    def test_a_jsonrpc_error_names_the_method_and_reason(self):
        tr, _ = self.both()["4.1"]
        with self.assertRaisesRegex(ApiError, r"transmission: torrent-start: Method not found"):
            tr.call("torrent-start", ids=[1])

    def test_rechecks_the_protocol_after_every_handshake(self):
        # Transmission upgraded underneath Usher: the next 409 switches it to JSON-RPC.
        m = importlib.reload(mock_apps)
        tr, handler = self.serve(m.TrHandler40)
        tr.torrents()
        handler.RPC_VERSION = "6.0.0"
        tr._sid = "expired"
        tr.torrents()
        self.assertEqual([s for _, s in handler.seen], ["old", "jsonrpc"])


if __name__ == "__main__":
    unittest.main()
