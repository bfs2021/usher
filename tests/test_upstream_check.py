"""The upstream check's own checks, run against the mock apps, and the text it produces."""

import datetime
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests import mock_apps, upstream_check, upstream_report

ROOT = Path(__file__).resolve().parent.parent


class MockedUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handlers = {
            "SONARR": mock_apps.arr_handler("sonarr"),
            "RADARR": mock_apps.arr_handler("radarr"),
            "PROWLARR": mock_apps.arr_handler("prowlarr"),
            "TRANSMISSION": mock_apps.TrHandler,
            "JELLYFIN": mock_apps.JfHandler,
        }
        cls.servers, cls.env = [], {}
        for name, handler in handlers.items():
            srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.servers.append(srv)
            cls.env[f"{name}_URL"] = f"http://127.0.0.1:{srv.server_port}" + ("/transmission/rpc" if name == "TRANSMISSION" else "")
        cls.env.update(
            SONARR_API_KEY=mock_apps.KEY, RADARR_API_KEY=mock_apps.KEY, PROWLARR_API_KEY=mock_apps.KEY,
            JELLYFIN_API_KEY=mock_apps.JF_KEY, JELLYFIN_USER="Viewer", TR_USER="x", TR_PASS="x",
        )  # fmt: skip

    @classmethod
    def tearDownClass(cls):
        for srv in cls.servers:
            srv.shutdown()
            srv.server_close()

    def test_the_mock_apps_pass_every_call_and_report_a_version_for_each_app(self):
        with tempfile.TemporaryDirectory() as out:
            run = upstream_check.run_all(out, self.env)
            self.assertEqual([f for f in run.failures if f["call"] not in upstream_check.LOOKUP.values()], run.failures)
            self.assertEqual(run.finish(), 0, run.failures)
            self.assertEqual(sorted(run.versions), ["Jellyfin", "Prowlarr", "Radarr", "Sonarr", "Transmission"])
            self.assertTrue(list((Path(out) / "responses").glob("*.json")))

    def test_a_missing_field_names_the_app_the_call_and_the_field(self):
        with tempfile.TemporaryDirectory() as out:
            run = upstream_check.Run(out, [])
            run.call("Sonarr", "queue", lambda: {"records": []}, ("totalRecords", "records"))
            self.assertEqual(len(run.failures), 1)
            self.assertEqual((run.failures[0]["app"], run.failures[0]["call"]), ("Sonarr", "queue"))
            self.assertIn("totalRecords", run.failures[0]["error"])
            self.assertEqual(run.finish(), 1)

    def test_a_failure_never_carries_a_key(self):
        with tempfile.TemporaryDirectory() as out:
            run = upstream_check.Run(out, ["s3cretvalue"])

            def broken():
                raise ValueError("rejected s3cretvalue")

            run.call("Radarr", "health", broken)
            self.assertNotIn("s3cretvalue", json.dumps(run.failures))


class ReportTests(unittest.TestCase):
    RESULTS = {
        "versions": {"Sonarr": "4.0.1", "Radarr": "5.2.0", "Jellyfin": "12.1.0"},
        "failures": [{"app": "Sonarr", "call": "queue", "error": "KeyError: the answer has no 'records'"}],
    }

    def test_issue_names_app_version_and_failing_call(self):
        body = upstream_report.issue_body(self.RESULTS)
        self.assertIn("| Sonarr | 4.0.1 | `queue` |", body)
        self.assertEqual(upstream_report.issue_title(self.RESULTS["failures"]), "Upstream check failing: Sonarr")

    def test_matrix_shows_green_versions_only(self):
        table = upstream_report.matrix(self.RESULTS, datetime.date(2030, 1, 2))
        self.assertIn("| Radarr | current and previous major version | 5.2.0 (2030-01-02) |", table)
        self.assertIn("| Sonarr | current and previous major version | not yet |", table)  # failed this run
        self.assertIn("| Prowlarr | current and previous major version | not yet |", table)  # no version seen

    def test_readme_matrix_is_rewritten_between_its_markers(self):
        with tempfile.TemporaryDirectory() as d:
            readme = Path(d) / "README.md"
            readme.write_text(f"before\n{upstream_report.START}\nold\n{upstream_report.END}\nafter\n", encoding="utf-8")
            upstream_report.write_matrix(self.RESULTS, readme, datetime.date(2030, 1, 2))
            text = readme.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("before\n") and text.endswith("\nafter\n"))
            self.assertIn("5.2.0 (2030-01-02)", text)
            self.assertNotIn("old", text)

    def test_readme_has_the_markers(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(upstream_report.START), 1)
        self.assertEqual(text.count(upstream_report.END), 1)


if __name__ == "__main__":
    unittest.main()
