"""In Status → This device, notifications are one group: the switch is its first row and the four options follow,
locked and greyed while it is off. Open titles in is its own group, under its own heading."""

import json
import re
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Notify } from "%(notify)s";
globalThis.Node = class {};
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.attrs = {}; this.className = ""; this.style = { setProperty() {} }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...k) { this.kids.push(...k); }
  appendChild(k) { this.kids.push(k); return k; }
  replaceChildren(...k) { this.kids = k; }
}
globalThis.document = { createElement: (t) => new El(t), createTextNode: (t) => ({ text: t }) };
Object.defineProperty(globalThis, "navigator", { value: { serviceWorker: { ready: Promise.resolve({ pushManager: { getSubscription: async () => ({ endpoint: "e1" }) } }) } }, configurable: true });
globalThis.window = globalThis;
globalThis.PushManager = class {};
globalThis.Notification = { permission: "granted" };
globalThis.matchMedia = () => ({ matches: true });
const data = { prefs: { ready: true, problem: true, space: true, server: true }, devices: [{ endpoint: "e1" }] };
const run = (here) => {
  const app = new (Notify(class {}))();
  const body = new El("div");
  app.drawNotify(body, data, here);
  const lists = body.kids.filter((k) => k.className === "list");
  const rows = lists[0].kids;
  const inputs = rows.map((r) => r.kids.find((k) => k.tag === "input"));
  return { lists: lists.length, rows: rows.length, switchDisabled: "disabled" in inputs[0].attrs,
    optionsDisabled: inputs.slice(1).map((i) => "disabled" in i.attrs),
    optionsOff: rows.slice(1).map((r) => r.className.includes(" off")), switchOff: rows[0].className.includes(" off") };
};
console.log(JSON.stringify({ on: run(true), off: run(false) }));
"""


class NotificationsAreOneGroup(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_switch_and_options_share_a_group_and_lock_when_off(self):
        run = subprocess.run(
            ["node", "--input-type=module"],
            input=SCRIPT % {"notify": (WEB / "notify.js").as_uri()},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        for state in ("on", "off"):
            self.assertEqual(out[state]["lists"], 1)
            self.assertEqual(out[state]["rows"], 5)
            self.assertFalse(out[state]["switchDisabled"])
            self.assertFalse(out[state]["switchOff"])
        self.assertEqual(out["on"]["optionsDisabled"], [False] * 4)
        self.assertEqual(out["on"]["optionsOff"], [False] * 4)
        self.assertEqual(out["off"]["optionsDisabled"], [True] * 4)
        self.assertEqual(out["off"]["optionsOff"], [True] * 4)


class OpenTitlesInIsApart(unittest.TestCase):
    def test_own_group_with_its_own_heading_after_notifications(self):
        settings = (WEB / "settings.js").read_text(encoding="utf-8")
        device = re.search(r"deviceSection\(\) \{.*?\n  \}\n", settings, re.S).group(0)
        self.assertLess(device.index('"Notifications"'), device.index("this.notifySection()"))
        self.assertLess(device.index("this.notifySection()"), device.index('"Open titles in"'))
        self.assertLess(device.index('"Open titles in"'), device.index("this.openInRow()"))
        self.assertEqual(device.count('class: "group-head"'), 2)
        self.assertIn('"Device")', device)
        self.assertNotIn('"This device")', device)
        self.assertIn('"Away from home"', settings)


if __name__ == "__main__":
    unittest.main()
