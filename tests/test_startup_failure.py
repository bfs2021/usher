"""Usher always gets past the start-up screen after an update, or says why it can't. Every file the page or a module
loads is in the service worker's cache list; shell files are never swapped into a version's cache one at a time; and a
module that fails to load, or a start-up that never finishes, leaves a message and a Reload button instead of the logo."""

import json
import re
import shutil
import subprocess
import unittest

from tests.webjs import WEB

NODE = shutil.which("node")


def shell_files():
    shell = re.search(r"const SHELL = \[(.*?)\];", (WEB / "sw.js").read_text(), re.S).group(1)
    return set(re.findall(r'"(/[^"]*)"', shell))


def loaded_by_the_page():
    """Everything index.html links, plus every module reached by a static or dynamic import, however deep."""
    html = (WEB / "index.html").read_text()
    found = {"/", "/index.html"}
    found |= set(re.findall(r'(?:href|src)="(/[^"]+)"', html))
    queue = [p for p in found if p.endswith(".js")]
    seen = set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        source = (WEB / name.lstrip("/")).read_text()
        for target in re.findall(r"""(?:\bfrom\s*|\bimport\s*\(?\s*|^import\s+)["']\./([\w.-]+)["']""", source, re.M):
            found.add("/" + target)
            queue.append("/" + target)
    return found


class CacheListTests(unittest.TestCase):
    def test_every_file_the_page_loads_is_in_the_cache_list(self):
        missing = loaded_by_the_page() - shell_files()
        self.assertEqual(missing, set(), "add these to SHELL in web/sw.js")

    def test_the_cache_list_names_only_files_that_exist(self):
        for path in shell_files() - {"/"}:
            self.assertTrue((WEB / path.lstrip("/")).is_file(), f"SHELL names {path}, which doesn't exist")

    def test_the_check_notices_a_file_left_out(self):
        self.assertIn("/startup.js", loaded_by_the_page())
        self.assertEqual({"/nothing.js"} - shell_files(), {"/nothing.js"})
        self.assertEqual((loaded_by_the_page() | {"/nothing.js"}) - shell_files(), {"/nothing.js"})


SW_RIG = r"""
const vm = require("node:vm");
const files = new Map();
const cache = { async match(r) { return files.get(new URL(r.url).pathname)?.clone(); }, async put(r, res) { files.set(new URL(r.url).pathname, res); } };
let served = "new";
const handlers = {};
const ctx = {
  addEventListener: (t, f) => { handlers[t] = f; }, Response, URL, Promise, Map, Set, console, setTimeout,
  caches: { open: async () => cache, keys: async () => [], delete: async () => true, match: async () => undefined },
  fetch: async () => new Response(served), location: new URL("https://usher.test"),
};
ctx.self = ctx;
vm.createContext(ctx);
vm.runInContext(%(sw)s, ctx);
(async () => {
  files.set("/data.js", new Response("old"));
  const ask = async (path) => {
    let p;
    handlers.fetch({ request: { url: "https://usher.test" + path, method: "GET", mode: "no-cors" }, respondWith: (x) => { p = x; }, waitUntil() {} });
    const res = await p;
    return res.text();
  };
  const first = await ask("/data.js");
  await new Promise((r) => setImmediate(r));
  const second = await ask("/data.js");
  console.log(JSON.stringify({ first, second, stored: await (files.get("/data.js")).text() }));
})();
"""


@unittest.skipUnless(NODE, "node is not installed")
class ShellCopyTests(unittest.TestCase):
    def test_a_shell_file_is_never_refreshed_into_the_cache_it_was_installed_with(self):
        script = SW_RIG % {"sw": json.dumps((WEB / "sw.js").read_text())}
        out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got, {"first": "old", "second": "old", "stored": "old"})


GUARD_RIG = r"""
const timers = [];
const listeners = {};
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.handlers = {}; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, f) { this.handlers[t] = f; }
}
let launch = new El("div");
launch.attrs["aria-hidden"] = "true";
const calls = [];
const view = () => {
  const find = (el, out) => { out.push(el); el.children.forEach((c) => find(c, out)); return out; };
  const all = launch ? find(launch, []) : [];
  return { shown: !!all.find((e) => e.className === "launch-failed"), button: all.find((e) => e.tagName === "BUTTON"), alert: launch?.attrs.role, hidden: launch?.attrs["aria-hidden"] };
};
globalThis.document = { createElement: (t) => new El(t), querySelector: (s) => (s === ".launch" ? launch : null) };
globalThis.addEventListener = (t, f) => { listeners[t] = f; };
globalThis.setTimeout = (f, ms) => { timers.push({ f, ms }); };
Object.defineProperty(globalThis, "navigator", { value: { onLine: true, serviceWorker: { getRegistration: async () => ({ update: async () => calls.push("update"), unregister: async () => calls.push("unregister") }) } }, configurable: true, writable: true });
globalThis.caches = { keys: async () => ["usher-0.1.0", "usher-art"], delete: async (k) => calls.push("delete " + k) };
globalThis.window = globalThis;
globalThis.location = { reload: () => calls.push("reload") };
require(%(path)s);
(async () => {
  const out = {};
  out.before = view().shown;
  %(act)s
  out.after = view();
  out.after = { shown: out.after.shown, hasButton: !!out.after.button, hidden: out.after.hidden };
  const b = view().button;
  if (b) { b.handlers.click(); await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r)); }
  out.calls = calls;
  console.log(JSON.stringify(out));
})();
"""


def guard(act):
    script = GUARD_RIG % {"path": json.dumps(str(WEB / "startup.js")), "act": act}
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class StartupGuardTests(unittest.TestCase):
    def test_the_page_runs_the_guard_without_modules(self):
        html = (WEB / "index.html").read_text()
        self.assertRegex(html, r'<script src="/startup\.js" defer></script>')
        self.assertLess(html.index("/startup.js"), html.index("/app.js"), "a stalled module must not hold the guard back")

    def test_a_start_up_that_never_finishes_shows_a_message_and_reload(self):
        got = guard("timers.forEach((t) => t.f());")
        self.assertFalse(got["before"])
        self.assertEqual(got["after"], {"shown": True, "hasButton": True})
        self.assertEqual(got["calls"], ["update", "delete usher-0.1.0", "reload"])

    def test_it_waits_a_few_seconds_rather_than_flashing_over_a_slow_start(self):
        got = guard("out.wait = Math.min(...timers.map((t) => t.ms));")
        self.assertGreaterEqual(got["wait"], 8000)

    def test_a_module_that_fails_to_load_shows_it_at_once(self):
        got = guard('listeners.error({ target: { tagName: "SCRIPT" } });')
        self.assertTrue(got["after"]["shown"])

    def test_a_module_that_loads_but_does_not_fit_the_others_shows_it(self):
        got = guard("listeners.error({ target: globalThis });")
        self.assertTrue(got["after"]["shown"])

    def test_reload_keeps_notifications_artwork_and_saved_lists(self):
        got = guard("timers.forEach((t) => t.f());")
        self.assertNotIn("unregister", got["calls"])
        self.assertNotIn("delete usher-art", got["calls"])
        self.assertNotIn("delete usher-data", got["calls"])
        self.assertIn("update", got["calls"])

    def test_reload_offline_keeps_the_saved_app_files(self):
        got = guard("navigator.onLine = false; timers.forEach((t) => t.f());")
        self.assertEqual(got["calls"], ["reload"])

    def test_other_errors_leave_the_logo_alone(self):
        got = guard('listeners.error({ target: { tagName: "IMG" } });')
        self.assertFalse(got["after"]["shown"])

    def test_nothing_shows_once_the_app_has_replaced_the_logo(self):
        got = guard("launch = null; timers.forEach((t) => t.f());")
        self.assertFalse(got["after"]["shown"])


if __name__ == "__main__":
    unittest.main()
