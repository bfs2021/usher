"""The summary is the first group on Status: one card that says how things are and lists what needs attention."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
globalThis.Node = class {};
class El extends Node {
  constructor(tag) { super(); this.tag = tag; this.kids = []; this.attrs = {}; this.className = ""; this.style = { setProperty() {} }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...k) { this.kids.push(...k); }
  appendChild(k) { this.kids.push(k); return k; }
}
globalThis.document = { createElement: (t) => new El(t), createElementNS: (n, t) => new El(t), createTextNode: (t) => ({ text: t }) };
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.matchMedia = () => ({ matches: false });
const { Status } = await import("%(status)s");
const { store } = await import("%(data)s");
const { h } = await import("%(dom)s");
const text = (n) => (n.text ?? "") + (n.kids || []).filter((k) => !(k.className === "sr-only")).map(text).join("");
const find = (n, f, out = []) => { if (f(n)) out.push(n); (n.kids || []).forEach((k) => find(k, f, out)); return out; };
const has = (n, c) => (n.className || "").split(" ").includes(c);
const describe = (n) => ({
  tag: n.tag, text: text(n), cls: n.className.replace(/ ?(warn)\b/, ""),
  hidden: find(n, (k) => k.attrs?.["aria-hidden"] === "true").length > 0,
  chev: find(n, (k) => has(k, "chev")).length,
});
let online = true;
const Base = class { offlineBanner() { return online ? null : h("div", { class: "banner offline" }); } logsSheet() {} };
class App extends Status(Base) {}
const part = (name, ok, t) => ({ name, ok, text: t });
const storage = { free: 5, total: 10, used: 5, breakdown: [], tidy: null };
const run = (snap, off) => {
  online = !off; store.snap = snap;
  const content = new App().status();
  const sections = content.kids.filter((k) => k.tag === "section");
  const first = sections[0];
  const rows = first ? find(first, (k) => has(k, "row")).map((r) => ({ ...describe(r), button: r.tag === "button" })) : [];
  return {
    banner: content.kids.some((k) => k.className === "banner offline"),
    firstIsCard: !!first && find(first, (k) => has(k, "list") && has(k, "summary")).length === 1,
    heads: sections.map((s) => find(s, (k) => k.tag === "h2").map(text).join("")),
    // An unheaded group other than the card.
    attentionSections: sections.filter((s) => find(s, (k) => has(k, "list") && !has(k, "summary")).length && !find(s, (k) => k.tag === "h2").length).length,
    rows, stray: content.kids.filter((k) => k.tag === "p").map(text),
  };
};
const ok = { loading: false, status: { ok: true, parts: [part("TV", true, "fine")], storage } };
const bad = { loading: false, status: { ok: false, storage, parts: [part("TV", false, "Sonarr is stopped"), part("Storage", false, "Almost full"), part("Movies", true, "fine")] } };
const noPart = { loading: false, status: { ok: false, storage, parts: [part("TV", true, "fine")] } };
console.log(JSON.stringify({ ok: run(ok), bad: run(bad), noPart: run(noPart), checking: run(null), offline: run(null, true) }));
"""


@unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
class SummaryCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = SCRIPT % {name: (WEB / f"{name}.js").as_uri() for name in ("status", "data", "dom")}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        cls.out = json.loads(run.stdout)

    def test_all_working_is_one_card_with_the_headline(self):
        out = self.out["ok"]
        self.assertTrue(out["firstIsCard"])
        self.assertEqual(out["heads"][:2], ["", "Health"])  # the card has no heading; Health follows it
        self.assertEqual([r["text"] for r in out["rows"]], ["Everything's working."])
        self.assertTrue(out["rows"][0]["hidden"], "the dot is decorative")
        self.assertEqual(out["rows"][0]["chev"], 0)
        self.assertEqual(out["stray"], [])

    def test_attention_rows_join_the_card_and_there_is_no_separate_section(self):
        out = self.out["bad"]
        self.assertTrue(out["firstIsCard"])
        self.assertEqual(out["heads"], ["", "Health", "Storage"])
        self.assertEqual(out["attentionSections"], 0)
        head, tv, storage = out["rows"]
        self.assertEqual(head["text"], "2 things need attention. Usher keeps working around it where it can.")
        self.assertTrue(head["hidden"], "the warning symbol is decorative")
        self.assertEqual((tv["tag"], tv["chev"]), ("button", 1))
        self.assertIn("TV", tv["text"])
        self.assertIn("Sonarr is stopped", tv["text"])
        self.assertEqual((storage["tag"], storage["chev"]), ("div", 0))
        self.assertIn("Almost full", storage["text"])

    def test_one_thing_and_failing_without_a_failing_part(self):
        one = self.out["noPart"]["rows"]
        self.assertEqual([r["text"] for r in one], ["1 thing needs attention. Usher keeps working around it where it can."])

    def test_checking_is_a_card_of_the_same_shape(self):
        out = self.out["checking"]
        self.assertTrue(out["firstIsCard"])
        self.assertEqual([r["text"] for r in out["rows"]], ["Checking…"])
        self.assertEqual(out["rows"][0]["cls"], self.out["ok"]["rows"][0]["cls"])
        self.assertEqual(out["stray"], [])

    def test_offline_banner_replaces_checking(self):
        out = self.out["offline"]
        self.assertTrue(out["banner"])
        self.assertFalse(out["firstIsCard"])
        self.assertEqual(out["rows"], [], "nothing but the banner: Settings is the account button at the top right, not a row")


class SummaryStyles(unittest.TestCase):
    def test_the_card_has_its_own_stylesheet_loaded_and_kept_offline(self):
        css = (WEB / "status.css").read_text()
        self.assertIn(".summary .lead", css)
        self.assertNotIn(".summary", (WEB / "app.css").read_text())
        self.assertIn('href="/status.css"', (WEB / "index.html").read_text())
        self.assertIn('"/status.css"', (WEB / "sw.js").read_text())


if __name__ == "__main__":
    unittest.main()
