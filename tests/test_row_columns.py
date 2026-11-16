"""A list row is one line: it has a column for each of its parts, so a row with a dot, a value and a chevron
(Status → Health) does not push its chevron onto a second line. Rows are built by the client's own code and
checked against the grid the stylesheet gives them."""

import json
import re
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Status } from "%(status)s";
import { titleRow } from "%(rows)s";
import { storageSection } from "%(storage)s";
globalThis.Node = class {};
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.className = ""; this.style = { setProperty() {} }; }
  setAttribute() {}
  addEventListener() {}
  append(...k) { this.kids.push(...k); }
  appendChild(k) { this.kids.push(k); return k; }
  replaceChildren(...k) { this.kids = k; }
}
globalThis.document = { createElement: (t) => new El(t), createElementNS: (_, t) => new El(t), createTextNode: (t) => ({ text: t }) };
globalThis.window = globalThis;
const rows = [];
const walk = (el) => {
  if (!(el instanceof El)) return;
  const classes = el.className.split(/\\s+/).filter(Boolean);
  if (classes.includes("row")) rows.push({ classes, children: el.kids.length, tag: el.tag });
  el.kids.forEach(walk);
};
const screen = new (Status(class {}))();
const st = { ok: false, parts: [{ name: "Downloads", ok: true, text: "" }, { name: "Search", ok: false, text: "x" }, { name: "Storage", ok: false, text: "y" }] };
walk(screen.healthSection(st));
walk(screen.summaryCard(st));
walk(titleRow({ art: null, title: "A", subtitle: "B", onclick: () => {} }));
walk(titleRow({ art: null, title: "A", meta: "May 1", onclick: () => {} }));
walk(titleRow({ art: null, title: "A", trailing: new El("div") }));
walk(storageSection({ total: 100e9, free: 50e9, lowAt: 1e9, split: [{ key: "movies", bytes: 5e9 }] }, screen, null));
console.log(JSON.stringify(rows));
"""


def declarations(css, wanted):
    """What the stylesheet sets, by property, for rules whose selector is one plain compound selector for `wanted` classes."""
    out = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        for sel in (s.strip() for s in selector.split(",")):
            classes = re.findall(r"\.([\w-]+)", sel)
            if " " in sel or not classes or not set(classes) <= wanted or ":" in sel or sel.startswith(("fieldset", "button")):
                continue
            for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body):
                out[prop] = value.strip()
    return out


def columns(rule):
    """How many parts fit on one line in this row's grid; more than any row has when extra parts add auto columns."""
    if rule.get("display", "grid") != "grid":
        return None
    if rule.get("grid-auto-flow", "").startswith("column") and rule.get("grid-auto-columns"):
        return 99
    return len(rule["grid-template-columns"].split())


class RowColumns(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_every_row_has_a_column_for_each_of_its_parts(self):
        script = SCRIPT % {
            "status": (WEB / "status.js").as_uri(),
            "rows": (WEB / "rows.js").as_uri(),
            "storage": (WEB / "storage.js").as_uri(),
        }
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = json.loads(run.stdout)
        css = "\n".join(
            (WEB / name).read_text(encoding="utf-8")
            for name in ("app.css", "account.css", "sheets.css", "discover.css", "media.css", "status.css", "storage.css")
        )
        self.assertTrue(any(r["children"] >= 4 for r in rows), "Health rows (dot, name, value, chevron) are among those checked")
        for row in rows:
            fits = columns(declarations(css, set(row["classes"])))
            if fits is None:
                continue
            self.assertGreaterEqual(fits, row["children"], f"a row of {row['children']} parts ({' '.join(row['classes'])}) wraps: its grid has {fits} columns")


if __name__ == "__main__":
    unittest.main()
