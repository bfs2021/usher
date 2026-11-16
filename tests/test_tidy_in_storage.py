"""On Status, Tidy up is a row inside the Storage group, with how many titles it can free."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { storageSection } from "%(storage)s";
globalThis.Node = class {};
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.attrs = {}; this.style = { setProperty() {} }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...k) { this.kids.push(...k); }
  appendChild(k) { this.kids.push(k); return k; }
}
globalThis.document = { createElement: (t) => new El(t), createElementNS: (_, t) => new El(t), createTextNode: (t) => ({ text: t }) };
globalThis.window = globalThis;
const walk = (el, f) => { if (!el || typeof el !== "object") return; f(el); (el.kids || []).forEach((k) => walk(k, f)); };
const text = (el) => { let s = ""; walk(el, (n) => { if (n.text !== undefined) s += n.text + " "; }); return s; };
const storage = { total: 1e12, free: 4e11, split: [{ key: "movies", bytes: 3e11 }], lowAt: 1e11, low: false };
const draw = (tidy) => storageSection(storage, { tidySheet() {}, emptyRecycle() {} }, tidy);
const lists = (section) => { const out = []; walk(section, (n) => { if ((n.className || "").split(" ").includes("list")) out.push(n); }); return out; };
const summary = (tidy) => {
  const section = draw(tidy);
  const inStorage = lists(section)[0];
  let row = null;
  walk(inStorage, (n) => { if (n.tag === "button" && text(n).includes("Tidy up")) row = n; });
  return { lists: lists(section).length, inStorage: !!row, text: row ? text(row) : "", chevron: !!row && text(row) !== "" };
};
console.log(JSON.stringify({ some: summary({ items: [{}, {}], bytes: 5e9 }), one: summary({ items: [{}], bytes: 2e9 }),
  none: summary({ items: [], bytes: 0 }), unknown: summary(null) }));
"""


class TidyUpInsideStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("needs Node to run the client's code")
        run = subprocess.run(
            ["node", "--input-type=module"],
            input=SCRIPT % {"storage": (WEB / "storage.js").as_uri()},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.out = json.loads(run.stdout)

    def test_the_row_is_in_the_storage_group_and_nothing_stands_apart(self):
        for case in self.out.values():
            self.assertEqual(case["lists"], 1, "Tidy up must not be a separate group")
            self.assertTrue(case["inStorage"])

    def test_the_row_says_how_many_titles_it_can_free(self):
        self.assertIn("2 titles", self.out["some"]["text"])
        self.assertIn("5.0 GB", self.out["some"]["text"])
        self.assertIn("1 title ", self.out["one"]["text"])
        self.assertIn("Nothing to tidy", self.out["none"]["text"])

    def test_the_row_still_reads_while_the_count_is_not_known(self):
        self.assertIn("finished watching", self.out["unknown"]["text"])

    def test_status_loads_the_count_and_a_change_drops_it(self):
        client = "\n".join(p.read_text() for p in WEB.glob("*.js") if p.name != "sw.js")
        self.assertIn('storageSection(st.storage, this, lists.show("tidy"))', client)
        self.assertIn('tidy: { url: "/api/tidy"', (WEB / "data.js").read_text())
        self.assertIn('lists.drop("library", "tidy")', (WEB / "sheets.js").read_text())


if __name__ == "__main__":
    unittest.main()
