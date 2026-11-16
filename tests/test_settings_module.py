"""The Settings screen lives in web/settings.js, a mixin assembled by web/app.js: Account, Device, Devices, About,
Advanced and Sign out. Status keeps only its own groups and a way into Settings."""

import json
import re
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
globalThis.Node = class {};
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.style = { setProperty() {} }; }
  setAttribute() {}
  addEventListener() {}
  append(...k) { this.kids.push(...k); }
  appendChild(k) { this.kids.push(k); return k; }
  replaceChildren(...k) { this.kids = k; }
}
globalThis.document = { createElement: (t) => new El(t), createElementNS: (n, t) => new El(t), createTextNode: (t) => ({ text: t }) };
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.matchMedia = () => ({ matches: false });
const { Status } = await import("%(status)s");
const { Notify } = await import("%(notify)s");
const { Advanced } = await import("%(advanced)s");
const { Updates } = await import("%(updates)s");
const { Settings } = await import("%(settings)s");
const { store } = await import("%(data)s");
const Base = class { offlineBanner() { return null; } toast() {} render() {} };
class App extends Settings(Updates(Advanced(Notify(Status(Base))))) { fillNotify() {} fillAdvanced() {} refreshUpdates() {} }
store.me = { name: "Sam", username: "sam", version: "0.0.0", csrf: "x", devices: [{ device: "Phone", current: true, lastSeen: 1 }] };
store.snap = { loading: false, status: { ok: true, parts: [{ name: "TV", ok: true, text: "fine" }], storage: { free: 5, total: 10, used: 5, breakdown: [], tidy: null } } };
const heads = [];
const walk = (n) => { if (n.tag === "h2") heads.push(n.kids.map((k) => k.text).join("")); (n.kids || []).forEach(walk); };
const app = new App();
walk(app.status());
const status = heads.splice(0);
walk(app.settingsView("Status").body);
console.log(JSON.stringify({ status, settings: heads }));
"""


class SettingsModule(unittest.TestCase):
    def setUp(self):
        self.settings = (WEB / "settings.js").read_text(encoding="utf-8")
        self.status = (WEB / "status.js").read_text(encoding="utf-8")

    def test_module_is_assembled_and_cached_offline(self):
        self.assertIn('from "./settings.js"', (WEB / "app.js").read_text(encoding="utf-8"))
        self.assertIn("Settings", re.search(r"const screens = \[(.*?)\];", (WEB / "app.js").read_text(encoding="utf-8")).group(1))
        self.assertIn('"/settings.js"', (WEB / "sw.js").read_text(encoding="utf-8"))

    def test_module_holds_the_settings_groups(self):
        for name in ("accountSection", "deviceSection", "devicesSection", "aboutSection", "signOutSection", "openInRow"):
            self.assertRegex(self.settings, rf"(?m)^  {name}\(\) \{{", name)
        for heading in ("Account", "Device", "Devices", "About"):
            self.assertIn(f'h("h2", {{}}, "{heading}")', self.settings)
        self.assertIn("this.advancedSection()", self.settings)

    def test_status_no_longer_defines_them(self):
        for name in ("deviceSection", "accountSection", "devicesSection", "aboutSection", "signOutSection", "openInRow"):
            self.assertNotRegex(self.status, rf"(?m)^  {name}\(")
        for heading in ("Device", "Account", "Devices", "About"):
            self.assertNotIn(f'h("h2", {{}}, "{heading}")', self.status)
        self.assertNotIn("this.settingsGroups()", self.status)

    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_status_and_settings_each_draw_their_groups_in_order(self):
        script = SCRIPT % {name: (WEB / f"{name}.js").as_uri() for name in ("status", "notify", "advanced", "updates", "settings", "data")}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["status"], ["Health", "Storage"])
        self.assertEqual(out["settings"], ["Account", "Device", "Devices", "About", "Advanced"])


if __name__ == "__main__":
    unittest.main()
