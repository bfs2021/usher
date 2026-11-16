"""A sheet draws everything it is given, however the content is grouped: a nested list is never turned into text."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Sheets } from "%(mod)s";
class Node {
  constructor(tag) { this.tag = tag; this.kids = []; }
  append(...k) { this.kids.push(...k); }
  replaceChildren(...k) { this.kids = k; }
  setAttribute() {} addEventListener() {} showModal() {} close() {} remove() {}
}
globalThis.Node = Node;
globalThis.document = { createElement: (tag) => new Node(tag), createElementNS: (ns, tag) => new Node(tag), createTextNode: (text) => ({ text }), body: new Node("body") };
const words = (n) => (n.text ?? "") + (n.kids || []).map(words).join(" ");
const screen = new (Sheets(class {}))();
const el = (tag, text) => { const n = new Node(tag); n.append({ text }); return n; };
const sheet = screen.openSheet(() => [
  el("h2", "Nothing to tidy"),
  [el("h3", "Kept titles"), el("p", "A footnote."), [el("div", "Made-up Title")]],
  null,
]);
const drawn = words(sheet.dialog);
console.log(JSON.stringify({ drawn, object: drawn.includes("[object") }));
"""


class SheetContent(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_nested_groups_are_drawn_not_turned_into_text(self):
        script = SCRIPT % {"mod": (WEB / "sheets.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertFalse(out["object"], out["drawn"])
        for text in ("Nothing to tidy", "Kept titles", "A footnote.", "Made-up Title"):
            self.assertIn(text, out["drawn"])


if __name__ == "__main__":
    unittest.main()
