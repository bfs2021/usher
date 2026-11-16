"""Settings → About → Changelog: every version in CHANGELOG.md, newest first, in the changelog's own words."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from usher import __version__
from usher.changelog import changelog, parse
from tests.test_settings_screen import run_app
from tests.test_update_result import Request
from tests.webjs import WEB, client_source

ROOT = Path(__file__).resolve().parent.parent

LOG = """# Changelog

## [Unreleased]

### Added
- Not out yet.

## [2.0.0]

### Added
- A **Shelf** row on Home. It has `two` lines
  that wrap onto a second line.
- Details are [explained here](docs/x.md).

### Fixed
- Posters no longer flicker.

## [1.9.0]

### Changed
- Older change.
"""

SCRIPT = """
import { h } from "%(dom)s";
import { versionBlocks } from "%(mod)s";
class Node { constructor(tag) { this.tag = tag; this.kids = []; } append(...k) { this.kids.push(...k); } setAttribute() {} addEventListener() {} }
globalThis.Node = Node;
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ text }) };
const words = (n) => (n.text ?? "") + (n.kids || []).map(words).join(" ");
const blocks = versionBlocks(%(versions)s);
console.log(JSON.stringify({ count: blocks.length, text: blocks.map(words) }));
"""


class Parsing(unittest.TestCase):
    def test_every_release_newest_first_in_plain_words(self):
        got = parse(LOG)
        self.assertEqual([v["version"] for v in got], ["2.0.0", "1.9.0"])  # Unreleased is not a version
        self.assertEqual([c["kind"] for c in got[0]["changes"]], ["Added", "Added", "Fixed"])
        self.assertEqual(got[0]["changes"][0]["text"], "A Shelf row on Home. It has two lines that wrap onto a second line.")
        self.assertEqual(got[0]["changes"][1]["text"], "Details are explained here.")
        self.assertEqual(got[1]["changes"], [{"kind": "Changed", "text": "Older change."}])

    def test_a_missing_file_is_logged_and_lists_nothing(self):
        with tempfile.TemporaryDirectory() as d, self.assertLogs("usher.changelog", "WARNING"):
            self.assertEqual(changelog(Path(d) / "nope.md"), [])

    def test_the_screen_lists_every_version_in_the_changelog(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        in_file = re.findall(r"(?m)^## \[(\d+(?:\.\d+)*)\]$", text)
        served = [v["version"] for v in changelog()]
        self.assertEqual(served, in_file)
        self.assertLessEqual(tuple(map(int, served[0].split("."))), tuple(map(int, __version__.split("."))), "CHANGELOG.md is ahead of the app")
        self.assertTrue(all(v["changes"] for v in changelog()), "every version lists its changes")

    def test_the_changelog_starts_at_the_first_release_with_a_summary(self):
        versions = changelog()
        self.assertEqual(versions[-1]["version"], "1.0.0", "nothing older than 1.0.0 is carried")
        self.assertGreaterEqual(len(versions[-1]["changes"]), 1, "1.0.0 opens with a summary")

    def test_the_image_carries_the_file_and_the_route_serves_it(self):
        self.assertIn("COPY CHANGELOG.md", (ROOT / "Dockerfile").read_text())
        self.assertIn('"/api/changelog"', (ROOT / "usher" / "server.py").read_text())
        req = Request(None)
        req.update_get("/api/changelog")
        self.assertEqual([v["version"] for v in req.answer[1]["versions"]], [v["version"] for v in changelog()])


class Screen(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_each_version_is_a_heading_with_its_changes_under_their_kind(self):
        versions = [
            {"version": "2.0.0", "changes": [{"kind": "Added", "text": "A new row."}, {"kind": "Fixed", "text": "A fix."}]},
            {"version": "1.9.0", "changes": [{"kind": "Changed", "text": "Older change."}]},
        ]
        script = SCRIPT % {"dom": (WEB / "dom.js").as_uri(), "mod": (WEB / "changelog.js").as_uri(), "versions": json.dumps(versions)}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["count"], 2)
        first, second = out["text"]
        for text in ("2.0.0", "Added", "A new row.", "Fixed", "A fix."):
            self.assertIn(text, first)
        self.assertLess(first.index("Added"), first.index("Fixed"))
        self.assertIn("1.9.0", second)
        self.assertNotIn("2.0.0", second)

    def test_about_opens_it_as_a_pushed_screen_with_a_back_button(self):
        settings = (WEB / "settings.js").read_text()
        self.assertIn('"Changelog"', settings)
        self.assertIn("this.openChangelog()", settings)
        src = (WEB / "changelog.js").read_text()
        shell = (WEB / "shell.js").read_text()
        self.assertIn('this.pushScreen("changelog")', src)
        self.assertIn("history.pushState", shell)  # pushing a screen adds a history entry
        self.assertIn("nav-back", src)
        self.assertIn("this.changelogView(", shell)

    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_opened_from_settings_its_back_button_reads_settings(self):
        out = run_app(
            'app.show("/status", false); app.openSettings(); out.settings = back();'
            " app.openChangelog(); out.stack = app.pushed; out.title = document.title; out.back = back();"
            ' app.popScreen(); app.show("/status", false); out.after = { stack: app.pushed, back: back() };'
        )
        self.assertEqual(out["settings"], "Status")
        self.assertEqual(out["stack"], ["settings", "changelog"])
        self.assertEqual(out["title"], "Changelog · Usher")
        self.assertEqual(out["back"], "Settings")
        self.assertEqual(out["after"], {"stack": ["settings"], "back": "Status"})

    def test_nothing_pops_up_after_an_update(self):
        src = client_source()
        for gone in ("maybeShowWhatsNew", "whatsNewSheet", "whatsNewSeen", "whatsNew", "shouldShow"):
            self.assertNotIn(gone, src)
        self.assertFalse((WEB / "whatsnew.js").exists())
        self.assertFalse((ROOT / "usher" / "whatsnew.py").exists())
        self.assertNotIn("whatsNew", (ROOT / "usher" / "server.py").read_text())

    def test_the_stylesheet_and_module_are_in_the_shell(self):
        self.assertIn("/changelog.css", (WEB / "index.html").read_text())
        sw = (WEB / "sw.js").read_text()
        for path in ('"/changelog.css"', '"/changelog.js"'):
            self.assertIn(path, sw)
        self.assertNotIn("whatsnew", sw)


if __name__ == "__main__":
    unittest.main()
