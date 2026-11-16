"""A sheet follows a finger dragged down and closes past a threshold or springs back; scrolling its content
never closes it; with Reduce Motion it closes without animating."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { dragToClose } from "%(mod)s";
let clock = 0;
globalThis.performance = { now: () => clock };
globalThis.matchMedia = () => ({ matches: REDUCED });
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
const timers = [];
globalThis.setTimeout = (fn) => timers.push(fn);
const make = () => {
  const on = {};
  const dialog = { style: {}, closed: 0, close() { this.closed++; }, getBoundingClientRect: () => ({ height: 400 }), addEventListener: (n, f) => { on[n] = f; } };
  const scroller = { scrollTop: 0, addEventListener() {} };
  dragToClose(dialog, scroller);
  const touch = (name, y, ms) => { clock += ms; const e = { touches: [{ clientY: y }], prevented: false, preventDefault() { this.prevented = true; } }; on[name](e); return e; };
  return { dialog, scroller, on, touch, flush: () => timers.splice(0).forEach((f) => f()) };
};
const drag = (s, to, ms) => { s.touch("touchstart", 100, 0); s.touch("touchmove", 100 + to / 2, ms / 2); const e = s.touch("touchmove", 100 + to, ms / 2); e.transform = s.dialog.style.transform; s.on.touchend({}); return e; };
const out = {};
let s = make(); let e = drag(s, 300, 1000); out.followsFinger = e.prevented && e.transform;
s.flush(); out.closedPastThreshold = s.dialog.closed;
s = make(); drag(s, 60, 1000); out.slowShortClosed = s.dialog.closed; s.flush(); out.slowShortClosed += s.dialog.closed; out.slowShortBack = s.dialog.style.transform;
s = make(); drag(s, 60, 50); s.flush(); out.flickClosed = s.dialog.closed;
s = make(); s.scroller.scrollTop = 120; e = drag(s, 300, 1000); s.flush(); out.scrolledClosed = s.dialog.closed; out.scrolledPrevented = e.prevented;
s = make(); e = drag(s, -300, 300); s.flush(); out.upClosed = s.dialog.closed; out.upPrevented = e.prevented;
console.log(JSON.stringify(out));
"""


def run(reduced):
    script = SCRIPT.replace("REDUCED", reduced) % {"mod": (WEB / "sheetdrag.js").as_uri()}
    done = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
class SheetDrag(unittest.TestCase):
    def test_follows_the_finger_and_closes_past_the_threshold(self):
        out = run("false")
        self.assertEqual(out["followsFinger"], "translateY(300px)")
        self.assertEqual(out["closedPastThreshold"], 1)

    def test_short_slow_drag_springs_back_and_a_flick_closes(self):
        out = run("false")
        self.assertEqual(out["slowShortClosed"], 0)
        self.assertEqual(out["slowShortBack"], "")
        self.assertEqual(out["flickClosed"], 1)

    def test_scrolling_the_content_never_closes_the_sheet(self):
        out = run("false")
        self.assertEqual((out["scrolledClosed"], out["scrolledPrevented"]), (0, False))
        self.assertEqual((out["upClosed"], out["upPrevented"]), (0, False))

    def test_reduce_motion_closes_at_once(self):
        out = run("true")
        self.assertEqual(out["closedPastThreshold"], 1)


if __name__ == "__main__":
    unittest.main()
