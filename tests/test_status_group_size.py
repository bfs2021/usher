"""Coming back to Status redraws the notification rows from the last known answer at once, so the This device
group keeps its size while the answer is fetched again."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Notify } from "%(notify)s";
globalThis.Node = class {};
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.style = { setProperty() {} }; }
  setAttribute() {}
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
const data = { prefs: { ready: true, problem: true, space: true, server: true },
  devices: [{ endpoint: "e1" }] };
let calls = 0;
globalThis.fetch = async () => { calls += 1; return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => data }; };
const app = new (Notify(class {}))();
const count = (el) => (el.kids || []).reduce((n, k) => n + 1 + count(k), 0);
const first = app.notifySection();
const checking = count(first);
await new Promise((r) => setTimeout(r, 50));
const loaded = count(first);
const again = app.notifySection();            // coming back: drawn before anything is fetched
console.log(JSON.stringify({ checking, loaded, again: count(again), calls }));
"""


class StatusGroupKeepsSize(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_notification_rows_are_drawn_before_the_recheck_answers(self):
        run = subprocess.run(
            ["node", "--input-type=module"],
            input=SCRIPT % {"notify": (WEB / "notify.js").as_uri()},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertGreater(out["loaded"], out["checking"])
        self.assertEqual(out["again"], out["loaded"])


if __name__ == "__main__":
    unittest.main()
