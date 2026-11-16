"""One place in the app loads and refreshes every list: `lists` in web/data.js."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tests.webjs import client_source

WEB = Path(__file__).resolve().parent.parent / "web"

# A fake server and clock around the real web/data.js: `calls` counts requests per URL, `answer` says what
# each request gets (a function, so a test can hold one back or make it fail).
RIG = """
let now = 1000000;
Date.now = () => now;
const calls = {}, timers = [];
globalThis.setTimeout = (fn) => { timers.push(fn); };
let answer = (url) => ({ status: 200, body: { url, n: calls[url] } });
globalThis.fetch = async (url) => {
  calls[url] = (calls[url] || 0) + 1;
  const a = await answer(url);
  if (a.down) throw new TypeError("Load failed");
  return { status: a.status, ok: a.status < 400, json: async () => a.body };
};
const seen = { redraw: [], failed: [] };
lists.redraw = (name) => seen.redraw.push(name);
lists.failed = (name, e) => seen.failed.push([name, e instanceof SignedOut ? "signed out" : "offline"]);
const tick = () => new Promise((r) => setImmediate(r));
const out = (x) => console.log(JSON.stringify(x));
"""


def run(script):
    src = (WEB / "data.js").read_text() + RIG + script
    done = subprocess.run(["node", "--input-type=module", "-e", src], capture_output=True, text=True)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


class OnlyTheLoaderFetchesLists(unittest.TestCase):
    app = client_source(without=("data.js",))
    data = (WEB / "data.js").read_text()

    def test_app_never_asks_for_a_list_itself(self):
        for url in ("/api/library", "/api/upcoming", "/api/upcoming/all", "/api/discover", "/api/me"):
            self.assertNotRegex(self.app, r'["\'`]%s["\'`?]' % re.escape(url), url)
            self.assertEqual(self.data.count(f'url: "{url}"'), 1, url)

    def test_one_api_function_and_it_lives_with_the_loader(self):
        self.assertNotIn("fetch(", self.app)
        self.assertNotIn("async function api(", self.app)
        self.assertEqual(self.data.count("fetch("), 1)
        self.assertIn('import { SignedOut, api, lists, store } from "./data.js";', self.app)

    def test_app_keeps_no_timestamps_or_copies_of_its_own(self):
        for old in ("libraryAt", "upcomingAt", "upcomingAllAt", "discoverAt", "upcomingAllLoading"):
            self.assertNotIn(old, self.app)
        self.assertNotRegex(self.app, r"store\.(me|library|upcoming|upcomingAll|discover) = ")

    def test_every_list_has_a_url_and_a_freshness(self):
        table = self.data[self.data.index("export const LISTS = {") : self.data.index("};", self.data.index("export const LISTS = {"))]
        rows = re.findall(r"^  (\w+): \{ url: \"(/api/[\w/]+)\", fresh: [^,}]+", table, re.M)
        self.assertEqual([r[0] for r in rows], ["me", "library", "tidy", "upcoming", "upcomingAll", "discover"])

    def test_offline_shell_includes_the_loader(self):
        self.assertIn('"/data.js"', (WEB / "sw.js").read_text())


@unittest.skipUnless(shutil.which("node"), "needs node")
class LoaderBehaviour(unittest.TestCase):
    def test_fresh_copy_is_returned_without_asking_again(self):
        got = run("""
const a = await lists.load("library");
const b = await lists.load("library");
now += 2 * 60000 + 1;                                  // older than Library's two minutes
const c = await lists.load("library");
out({ same: a === b, n: c.n, calls: calls["/api/library"], held: store.library.n, redraw: seen.redraw });
""")
        self.assertEqual(got, {"same": True, "n": 2, "calls": 2, "held": 2, "redraw": ["library", "library"]})

    def test_callers_share_one_request_per_list(self):
        got = run("""
const all = await Promise.all([lists.load("upcoming"), lists.load("upcoming"), lists.load("upcoming"), lists.load("discover")]);
out({ calls, same: all[0] === all[2] });
""")
        self.assertEqual(got, {"calls": {"/api/upcoming": 1, "/api/discover": 1}, "same": True})

    def test_the_account_is_asked_for_every_time(self):
        got = run("""
await lists.load("me"); await lists.load("me");
out(calls["/api/me"]);
""")
        self.assertEqual(got, 2)

    def test_a_screen_gets_nothing_then_a_redraw(self):
        got = run("""
const first = lists.show("upcoming");
lists.show("upcoming");                                // drawn again meanwhile: still one request
await tick();
out({ first, then: lists.show("upcoming").n, calls: calls["/api/upcoming"], redraw: seen.redraw });
""")
        self.assertEqual(got, {"first": None, "then": 1, "calls": 1, "redraw": ["upcoming"]})

    def test_an_unchanged_copy_does_not_redraw(self):
        got = run("""
answer = () => ({ status: 200, body: { movies: [], shows: [] } });
await lists.load("library");
now += 3 * 60000;
const old = lists.show("library");                     // Library keeps its older copy on screen meanwhile
await tick();
now += 3 * 60000;
out({ old, redraw: seen.redraw, calls: calls["/api/library"], upcomingStale: (await lists.load("upcoming"), now += 6 * 60000, lists.show("upcoming")) });
""")
        # Every tab keeps its older copy on screen while a new one is fetched, so a second visit paints at once.
        self.assertEqual(got, {"old": {"movies": [], "shows": []}, "redraw": ["library", "upcoming"], "calls": 2, "upcomingStale": {"movies": [], "shows": []}})

    def test_changed_drops_the_lists_a_change_touches(self):
        got = run("""
for (const name of Object.keys(LISTS)) await lists.load(name);
lists.changed({ seq: 4, titles: [{ kind: "movie", id: 7 }] });
const one = Object.keys(LISTS).filter((n) => store[n] == null);
lists.changed({ seq: 5, all: true });
out({ one, all: Object.keys(LISTS).filter((n) => store[n] == null) });
""")
        self.assertEqual(got, {"one": ["library", "tidy", "upcoming", "discover"], "all": ["library", "tidy", "upcoming", "upcomingAll", "discover"]})

    def test_a_copy_asked_for_before_a_change_is_not_kept(self):
        got = run("""
let release;
answer = (url) => new Promise((r) => { release = () => r({ status: 200, body: { n: calls[url] } }); });
lists.show("library");
await tick();
const early = release;
lists.changed({ seq: 9, titles: [] });
lists.show("library");                                 // the redraw after the change asks again
await tick();
early(); await tick();
const afterOld = store.library;
release(); await tick();
out({ afterOld, held: store.library, calls: calls["/api/library"], redraw: seen.redraw });
""")
        self.assertEqual(got, {"afterOld": None, "held": {"n": 2}, "calls": 2, "redraw": ["library"]})

    def test_signed_out_is_reported_once_from_one_place(self):
        got = run("""
answer = () => ({ status: 401, body: {} });
const shown = ["library", "upcoming", "upcomingAll", "discover"].map((n) => lists.show(n));
await tick();
out({ shown, failed: seen.failed, timers: timers.length });
""")
        self.assertEqual(got["shown"], [None] * 4)
        self.assertEqual(got["failed"], [[n, "signed out"] for n in ("library", "upcoming", "upcomingAll", "discover")])
        self.assertEqual(got["timers"], 0)

    def test_cant_reach_home_is_reported_and_tried_again_later_not_at_once(self):
        got = run("""
lists.set("me", { csrf: "x" });
answer = () => ({ down: true });
lists.show("library"); await tick();
lists.show("library"); lists.show("library"); await tick();      // the redraw that follows doesn't ask again
const soon = calls["/api/library"];
now += 10000; timers.shift()();                                   // ten seconds on: the screen is redrawn…
lists.show("library"); await tick();                              // …and that asks again
answer = (url) => ({ status: 200, body: { ok: 1 } });
now += 10000;
lists.show("library"); await tick();
out({ soon, failed: seen.failed, redraw: seen.redraw, calls: calls["/api/library"], held: store.library, tried: lists.tried("library") });
""")
        self.assertEqual(
            got,
            {
                "soon": 1,
                "failed": [["library", "offline"], ["library", "offline"]],
                "redraw": ["library", "library"],
                "calls": 3,
                "held": {"ok": 1},
                "tried": False,
            },
        )

    def test_a_failed_refresh_of_a_list_on_screen_stays_quiet(self):
        got = run("""
await lists.load("library");
now += 3 * 60000;
answer = () => ({ down: true });
const old = lists.show("library"); await tick();
out({ old: old.n, failed: seen.failed });
""")
        self.assertEqual(got, {"old": 1, "failed": []})

    def test_signing_out_clears_every_list(self):
        got = run("""
for (const name of Object.keys(LISTS)) await lists.load(name);
lists.clear();
out(Object.keys(LISTS).filter((n) => store[n] != null));
""")
        self.assertEqual(got, [])

    def test_sign_in_sets_the_account_and_later_requests_carry_its_token(self):
        got = run("""
let sent;
const real = globalThis.fetch;
globalThis.fetch = (url, opts) => { sent = opts.headers["X-CSRF-Token"]; return real(url, opts); };
lists.set("me", { csrf: "made-up-token" });
await api("/api/get", { method: "POST", body: { kind: "movie" } });
out(sent);
""")
        self.assertEqual(got, "made-up-token")


if __name__ == "__main__":
    unittest.main()
