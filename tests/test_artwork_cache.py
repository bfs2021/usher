"""Artwork is kept on the device by the service worker: answered from there at once, refreshed in the background
once older than the server allows, the least recently shown dropped once the cache passes its cap, and emptied
when anyone signs out. The real web/sw.js runs in node, with a fake Cache Storage and a fake server."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"

# A fake Cache Storage shared by every copy of the worker that `boot()` starts (as after the phone stops it), a fake
# server (`answer`, counted in `calls`) and a clock. `get` sends one request through the worker the way a page would.
RIG = r"""
const vm = require("node:vm");
const ORIGIN = "https://usher.test";
let now = 1_000_000;
const calls = [];
let answer = (url) => ({ status: 200, body: "x".repeat(1000), cc: "private, max-age=86400" });
const fetchFake = async (req) => {
  const url = new URL(typeof req === "string" ? req : req.url, ORIGIN).href;
  calls.push(url.replace(ORIGIN, ""));
  const a = await answer(url);
  if (a.down) throw new TypeError("Load failed");
  return new Response(a.status === 200 ? a.body : "", { status: a.status, headers: { "Cache-Control": a.cc || "no-store" } });
};
class FakeCache {
  constructor() { this.m = new Map(); }
  key(r) { return new URL(typeof r === "string" ? r : r.url, ORIGIN).href; }
  async match(r) { const x = this.m.get(this.key(r)); return x ? x.clone() : undefined; }
  async put(r, res) { this.m.set(this.key(r), new Response(await res.arrayBuffer(), { status: res.status, headers: res.headers })); }
  async delete(r) { return this.m.delete(this.key(r)); }
  async keys() { return [...this.m.keys()].map((url) => ({ url })); }
  async addAll() {}
}
const boxes = new Map();
const caches = {
  async open(n) { if (!boxes.has(n)) boxes.set(n, new FakeCache()); return boxes.get(n); },
  async delete(n) { return boxes.delete(n); },
  async keys() { return [...boxes.keys()]; },
  async match(r) { for (const c of boxes.values()) { const x = await c.match(r); if (x) return x; } },
};
const pictures = () => [...(boxes.get("usher-art")?.m.keys() || [])].map((k) => k.replace(ORIGIN, "")).filter((k) => k.startsWith("/api/")).sort();
const tick = () => new Promise((r) => setImmediate(r));

function boot(swap = {}) {
  let src = SW;
  for (const [name, value] of Object.entries(swap)) src = src.replace(new RegExp(`const ${name} = [^;]+;`), `const ${name} = ${value};`);
  const handlers = {};
  const ctx = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    caches, fetch: fetchFake, Response, Request, URL, Promise, JSON, Map, Set, Number, Math, console,
    Date: { now: () => now }, setTimeout: (fn) => setImmediate(fn), location: new URL(ORIGIN),
    skipWaiting: async () => {}, clients: { claim: async () => {} },
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  const settle = async (waits) => { for (let i = 0; i < waits.length; i++) await waits[i]; for (let i = 0; i < 5; i++) await tick(); };
  return {
    async get(path) {
      let answered = null;
      const waits = [];
      const request = { url: new URL(path, ORIGIN).href, method: "GET", mode: "no-cors" };
      handlers.fetch({ request, respondWith: (p) => { answered = p; }, waitUntil: (p) => waits.push(p) });
      const res = answered ? await answered : null;
      const text = res ? await res.text() : null;
      await settle(waits);
      return { answered: !!answered, status: res?.status, text };
    },
    async message(data) { const waits = []; handlers.message({ data, waitUntil: (p) => waits.push(p) }); await settle(waits); },
    async activate() { const waits = []; handlers.activate({ waitUntil: (p) => waits.push(p) }); await settle(waits); },
  };
}
const out = (x) => console.log(JSON.stringify(x));
"""


def run(script):
    src = (
        "const SW = "
        + json.dumps((WEB / "sw.js").read_text())
        + ";\n"
        + RIG
        + "(async () => {\n"
        + script
        + "\n})().catch((e) => { console.error(e); process.exit(1); });"
    )
    done = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


A = "/api/art/jellyfin/" + "a" * 32 + "?v=1&a=2&w=240"
B = "/api/art/sonarr/2?v=1&a=2&w=240"
C = "/api/art/radarr/3?v=1&a=2&w=240"
D = "/api/art/remote/" + "d" * 24 + "?a=2&w=240"


@unittest.skipUnless(shutil.which("node"), "needs node")
class ArtworkStaysOnTheDevice(unittest.TestCase):
    def test_second_request_is_answered_from_the_device(self):
        got = run(f"""
const sw = boot();
const first = await sw.get("{A}");
const second = await sw.get("{A}");
const later = boot();                                   // the phone stopped the worker; a new one starts
const third = await later.get("{A}");
out({{ answered: first.answered, same: first.text === second.text && second.text === third.text, calls }});
""")
        self.assertTrue(got["answered"])
        self.assertTrue(got["same"])
        self.assertEqual(got["calls"], [A], "only the first request reaches the server")

    def test_old_copy_shows_at_once_and_is_refreshed_in_the_background(self):
        got = run(f"""
const sw = boot();
await sw.get("{A}");
answer = () => ({{ status: 200, body: "new", cc: "private, max-age=86400" }});
now += 3600 * 1000;                                     // still within its day: no refresh
const young = await sw.get("{A}");
now += 86400 * 1000;                                    // past what the server allowed
const stale = await sw.get("{A}");
const fresh = await sw.get("{A}");
out({{ young: young.text.length, stale: stale.text.length, fresh: fresh.text, calls: calls.length }});
""")
        self.assertEqual(got["young"], 1000)
        self.assertEqual(got["stale"], 1000, "the old copy is shown at once, not after the network")
        self.assertEqual(got["fresh"], "new", "the background refresh replaced it")
        self.assertEqual(got["calls"], 2)

    def test_a_retry_goes_to_the_network_and_replaces_the_copy(self):
        got = run(f"""
const sw = boot();
await sw.get("{A}");
answer = () => ({{ status: 200, body: "whole", cc: "private, max-age=86400" }});
const retried = await sw.get("{A}&r=1");
const next = await sw.get("{A}");
out({{ retried: retried.text, next: next.text, kept: pictures() }});
""")
        self.assertEqual(got["retried"], "whole")
        self.assertEqual(got["next"], "whole")
        self.assertEqual(got["kept"], [A], "one copy per picture, under its address without the retry mark")

    def test_least_recently_shown_go_first_once_past_the_byte_cap(self):
        got = run(f"""
const sw = boot({{ ART_BYTES: 3000 }});
await sw.get("{A}"); await sw.get("{B}"); await sw.get("{C}");
await sw.get("{A}");                                    // shown again: now the most recent
await sw.get("{D}");                                    // over the cap: B, the least recently shown, goes
const kept = pictures();
const later = boot({{ ART_BYTES: 3000 }});              // the order survives the worker being stopped
await later.get("{C}");
await later.get("{B}");                                 // fetched again; A is now the least recently shown
out({{ kept, after: pictures(), bytes: [...boxes.get("usher-art").m.entries()].filter(([k]) => k.includes("/api/")).length * 1000 }});
""")
        self.assertEqual(got["kept"], sorted([A, C, D]))
        self.assertEqual(got["after"], sorted([B, C, D]))
        self.assertLessEqual(got["bytes"], 3000)

    def test_never_more_pictures_than_the_item_cap(self):
        got = run(f"""
const sw = boot({{ ART_ITEMS: 2 }});
for (const p of ["{A}", "{B}", "{C}", "{D}"]) await sw.get(p);
out(pictures());
""")
        self.assertEqual(got, sorted([C, D]))

    def test_missing_pictures_and_other_api_requests_are_not_kept(self):
        got = run(f"""
const sw = boot();
answer = (url) => url.includes("/radarr/") ? {{ status: 404, cc: "private, max-age=600" }} : {{ status: 200, body: "{{}}" }};
const missing = await sw.get("{C}");
const lib = await sw.get("/api/library");
const events = await sw.get("/api/events");
out({{ missing: missing.status, lib: lib.answered, events: events.answered, kept: pictures() }});
""")
        self.assertEqual(got["missing"], 404)
        self.assertFalse(got["lib"], "lists go straight to the network")
        self.assertFalse(got["events"])
        self.assertEqual(got["kept"], [])

    def test_signed_out_empties_the_artwork_and_the_saved_lists(self):
        got = run(f"""
const sw = boot();
await sw.get("{A}");
await (await caches.open("usher-data")).put("/saved/library", new Response("{{}}"));
await sw.message({{ type: "forget" }});
const afterForget = [...boxes.keys()];
await sw.get("{B}");
now += 40 * 86400 * 1000;
answer = () => ({{ status: 401 }});                     // the session ended on the server
await (await caches.open("usher-data")).put("/saved/library", new Response("{{}}"));
await sw.get("{B}");
out({{ afterForget, after401: [...boxes.keys()], pictures: pictures() }});
""")
        self.assertNotIn("usher-data", got["afterForget"])
        self.assertNotIn("usher-art", got["afterForget"])
        self.assertNotIn("usher-data", got["after401"])
        self.assertEqual(got["pictures"], [])

    def test_a_picture_arriving_after_sign_out_is_not_kept(self):
        got = run(f"""
const sw = boot();
let release;
answer = () => new Promise((r) => {{ release = () => r({{ status: 200, body: "late", cc: "private, max-age=86400" }}); }});
const pending = sw.get("{A}");
while (!release) await tick();
await sw.message({{ type: "forget" }});
release();
await pending;
out(pictures());
""")
        self.assertEqual(got, [])

    def test_an_update_keeps_the_artwork_and_drops_old_shells(self):
        got = run(f"""
const sw = boot();
await sw.get("{A}");
await caches.open("usher-0.1.0");
await caches.open("usher-data");
await sw.activate();
out([...boxes.keys()].sort());
""")
        self.assertIn("usher-art", got)
        self.assertIn("usher-data", got)
        self.assertNotIn("usher-0.1.0", got)


class CacheIsDescribed(unittest.TestCase):
    sw = (WEB / "sw.js").read_text()

    def test_caps_are_set_and_small_enough_for_a_phone(self):
        size = re.search(r"const ART_BYTES = (\d+) \* 1024 \* 1024;", self.sw)
        items = re.search(r"const ART_ITEMS = (\d+);", self.sw)
        self.assertTrue(size and items, "ART_BYTES and ART_ITEMS set the cap")
        self.assertLessEqual(int(size.group(1)), 50)
        self.assertLessEqual(int(items.group(1)), 1000)

    def test_the_app_and_the_worker_name_the_same_caches(self):
        data = (WEB / "data.js").read_text()
        for name in ("usher-art", "usher-data"):
            self.assertIn(f'"{name}"', self.sw)
            self.assertIn(f'"{name}"', data)


if __name__ == "__main__":
    unittest.main()
