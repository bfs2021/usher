"""Coming back to Usher from another app leaves Status as it was: a live update that changes nothing on screen
does not redraw it, and one that changes something still does."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Shell } from "%(shell)s";
import { store } from "%(data)s";
globalThis.document = { title: "" };
globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
let sources = [];
globalThis.EventSource = class {
  constructor() { this.l = {}; sources.push(this); }
  addEventListener(n, f) { this.l[n] = f; }
  close() {}
};
EventSource.CLOSED = 2;
class Screens {
  status() { return { tag: "content" }; }
  unreadCount() { return 0; }
  downloadsUnseen() { return 0; }
  querySelectorAll() { return []; }
}
const app = new (Shell(Screens))();
app.current = "/status";
app.main = { scrollTop: 0, firstChild: {}, querySelector: () => ({ replaceWith() {} }) };
store.me = { name: "Sam" };
const snap = (free) => JSON.stringify({ changes: 1, status: { ok: true, parts: [], storage: { free } } });
let draws = 0;
const render = app.render.bind(app);
app.render = (full) => { draws += 1; render(full); };
app.listen();
store.snap = JSON.parse(snap(5e12));
app.render(false);                   // Status is on screen, drawn from the last update
draws = 0;
sources[0].l.state({ data: snap(5e12) });   // first update after opening or coming back: nothing changed
const same = draws;
sources[0].l.state({ data: snap(5e12 + 1e6) });   // bytes moving a little is not a change
const small = draws;
sources[0].l.state({ data: snap(9e12) });   // a real change
console.log(JSON.stringify({ same, small, real: draws }));
"""


class ResumeNoFlicker(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_status_redraws_only_when_something_on_it_changed(self):
        script = SCRIPT % {"shell": (WEB / "shell.js").as_uri(), "data": (WEB / "data.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["same"], 0)
        self.assertEqual(out["small"], 0)
        self.assertEqual(out["real"], 1)


if __name__ == "__main__":
    unittest.main()
