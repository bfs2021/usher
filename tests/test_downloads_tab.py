"""Downloads is its own tab, holding everything from asked to ready to watch; Home has no In progress section."""

import json
import re
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { store } from "%(data)s";
import { Downloads } from "%(mod)s";
const kept = new Map();
globalThis.localStorage = { getItem: (k) => kept.get(k) ?? null, setItem: (k, v) => kept.set(k, String(v)) };
const screen = new (Downloads(class { updateBadges() {} }))();
const snap = (requests, downloading) => { store.snap = { requests, downloading }; };
const out = {};
snap([
  { rid: 1, kind: "movie", id: 7, step: 0 }, { rid: 2, kind: "show", id: 8, step: 2 },
  { rid: 3, kind: "movie", id: 9, step: 4 },
], [
  { key: "a", kind: "movie", id: 7, state: "downloading" },
  { key: "b", kind: "show", id: 20, state: "waiting" },
]);
out.fresh = screen.downloadsUnseen();            // never opened: everything is new
screen.markDownloadsSeen();
out.opened = screen.downloadsUnseen();           // opening the screen clears it
store.snap.requests[1].step = 3; store.snap.requests[1].progress = 0.4;
store.snap.downloading[0].progress = 0.9;
out.progress = screen.downloadsUnseen();         // progress and steps along the way are not news
snap([...store.snap.requests, { rid: 4, kind: "movie", id: 10, step: 0 }], store.snap.downloading);
out.newRequest = screen.downloadsUnseen();       // a new request comes back
screen.markDownloadsSeen();
store.snap.requests[0].step = 4;
store.snap.downloading = store.snap.downloading.filter((d) => d.key !== "a");   // its download is done with
out.finished = screen.downloadsUnseen();         // a request that finishes comes back
screen.markDownloadsSeen();
store.snap.downloading[0].state = "problem";
out.problem = screen.downloadsUnseen();          // a problem comes back
screen.markDownloadsSeen();
store.snap.requests[2].problem = true;
out.requestProblem = screen.downloadsUnseen();
screen.markDownloadsSeen();
screen.downloadsSeen = null;                     // another start of the app on the same device
out.keptOnDevice = screen.downloadsUnseen();
const before = store.snap;
snap([], []);
out.empty = screen.downloadsUnseen();
store.snap = null;
out.none = screen.downloadsUnseen();
screen.markDownloadsSeen();                      // nothing loaded yet: nothing is forgotten
store.snap = before;
screen.downloadsSeen = null;
out.notWiped = screen.downloadsUnseen();
console.log(JSON.stringify(out));
"""


class DownloadsTabTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_badge_shows_what_is_new_since_the_screen_was_opened(self):
        script = SCRIPT % {"data": (WEB / "data.js").as_uri(), "mod": (WEB / "downloads.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        # Three requests and one download nobody asked for here (the download of a requested title is not counted
        # again) are new until Downloads is opened; then only a new request, a finish or a problem brings the badge back.
        self.assertEqual(
            json.loads(run.stdout),
            {
                "fresh": 4,
                "opened": 0,
                "progress": 0,
                "newRequest": 1,
                "finished": 1,
                "problem": 1,
                "requestProblem": 1,
                "keptOnDevice": 0,
                "empty": 0,
                "none": 0,
                "notWiped": 0,
            },
        )

    def test_downloads_is_a_screen_in_the_tab_bar_and_sidebar(self):
        shell = (WEB / "shell.js").read_text()
        self.assertIn('{ path: "/downloads", name: "Downloads", icon: "downloads" }', shell)
        self.assertIn('"/downloads": () => this.downloads()', shell)
        self.assertIn("this.liveDownloads()", shell)
        self.assertIn('from "./downloads.js"', (WEB / "app.js").read_text())
        self.assertIn("repeat(6, 1fr)", (WEB / "app.css").read_text())
        self.assertIn("downloads:", (WEB / "dom.js").read_text())

    def test_every_screen_has_a_shortcut_key(self):
        self.assertIn("e.key <= String(SCREENS.length)", (WEB / "shell.js").read_text())

    def test_badge_on_the_tab_shows_what_is_unseen_and_opening_the_screen_clears_it(self):
        shell = (WEB / "shell.js").read_text()
        self.assertIn('"data-badge": s.path === "/" ? "home" : "downloads"', shell)
        self.assertIn("this.downloadsUnseen()", shell)
        self.assertNotIn("downloadsCount", shell)
        mod = (WEB / "downloads.js").read_text()
        # Seen on opening, and again for each live update while the screen is open.
        self.assertEqual(mod.count("this.markDownloadsSeen()"), 2)

    def test_home_has_no_in_progress_section(self):
        home = (WEB / "home.js").read_text()
        for gone in ("In progress", "requestRow", "downloadRow", "requestsFolded", "h-req"):
            self.assertNotIn(gone, home)
        self.assertNotIn("In progress", "\n".join(p.read_text() for p in WEB.glob("*.js")))

    def test_rows_keep_their_stop_actions_through_the_details_and_journey(self):
        mod = (WEB / "downloads.js").read_text()
        self.assertIn('this.details(d, "downloading")', mod)
        self.assertIn("this.journey(r.kind, r.id, r.rid)", mod)
        self.assertIn("this.liveDownloads", (WEB / "shell.js").read_text())
        self.assertTrue(re.search(r"Stop (looking for it|downloading)", (WEB / "sheets.js").read_text()))

    def test_the_module_is_cached_for_offline_use(self):
        self.assertIn('"/downloads.js"', (WEB / "sw.js").read_text())


if __name__ == "__main__":
    unittest.main()
