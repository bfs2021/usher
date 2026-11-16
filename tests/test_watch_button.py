"""A title in Jellyfin has one Watch button and a "…" menu; Settings → This device chooses which way Watch opens."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { watchLinks } from "%(watch)s";
const app = { server: "s1", user: "u1" }, web = "https://media.example";
const pick = (...a) => { const r = watchLinks(...a); return [r.main?.href ?? null, r.other?.href ?? null]; };
console.log(JSON.stringify({
  appFirst: pick(app, web, "i1"),
  appByDefault: pick(app, web, "i1", undefined),
  webFirst: pick(app, web, "i1", "web"),
  junkIsApp: pick(app, web, "i1", "other"),
  noWeb: pick(app, null, "i1"),
  noWebAndWebPreferred: pick(app, null, "i1", "web"),
  noApp: pick(null, web, "i1"),
  noAppAndAppPreferred: pick(null, web, "i1", "app"),
  neither: pick(null, null, "i1"),
  mainIsApp: [watchLinks(app, web, "i1").main.app, watchLinks(app, web, "i1", "web").main.app],
}));
"""

APP = "swiftfin://s1/u1/item/i1"
WEBLINK = "https://media.example/web/#/details?id=i1"


class WatchLinks(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_which_way_is_main_and_which_is_in_the_menu(self):
        run = subprocess.run(["node", "--input-type=module"], input=SCRIPT % {"watch": (WEB / "watch.js").as_uri()}, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["appFirst"], [APP, WEBLINK])
        self.assertEqual(out["appByDefault"], [APP, WEBLINK])
        self.assertEqual(out["webFirst"], [WEBLINK, APP])
        self.assertEqual(out["junkIsApp"], [APP, WEBLINK])
        # One way in: Watch uses it, whatever the setting, and the menu is hidden.
        self.assertEqual(out["noWeb"], [APP, None])
        self.assertEqual(out["noWebAndWebPreferred"], [APP, None])
        self.assertEqual(out["noApp"], [WEBLINK, None])
        self.assertEqual(out["noAppAndAppPreferred"], [WEBLINK, None])
        self.assertEqual(out["neither"], [None, None])
        # The note about adding the server to the app is only for while the app is the main action.
        self.assertEqual(out["mainIsApp"], [True, False])


class WatchSheetAndSetting(unittest.TestCase):
    def test_details_sheet_has_watch_and_a_menu_not_two_buttons(self):
        src = (WEB / "home.js").read_text()
        self.assertIn('"Watch"', src)
        self.assertIn("watch.other ? menu(", src)
        self.assertIn("watch.main ? h(", src, "no button when there is no way in")
        self.assertIn("watch.main.app ? h(\"p\", { class: \"footnote\" }", src, "the note shows only while the app is main")
        self.assertNotIn("Open in the Jellyfin app\") : null", src)

    def test_menu_is_its_own_module_and_follows_the_interface_rules(self):
        js = (WEB / "menu.js").read_text()
        for needed in ('"aria-haspopup": "menu"', '"aria-expanded"', 'role: "menu"', 'role: "menuitem"', '"Escape"'):
            self.assertIn(needed, js)
        css = (WEB / "menu.css").read_text()
        self.assertIn("min-height: 44px", css)
        self.assertIn("prefers-reduced-motion: no-preference", css)
        self.assertIn("/menu.css", (WEB / "index.html").read_text())
        self.assertIn('"/menu.css"', (WEB / "sw.js").read_text())

    def test_setting_is_per_device_in_status_account(self):
        status = (WEB / "settings.js").read_text()
        self.assertIn('"Open titles in"', status)
        self.assertIn('"Jellyfin app"', status)
        self.assertIn('"Website"', status)
        self.assertIn("prefs.set(OPEN_IN, value)", status)  # local storage, never the server
        self.assertNotIn("api(", status[status.index("openInRow()") : status.index("// Credits TMDb")])

    def test_open_in_row_shows_its_label_in_full_and_keeps_its_buttons_one_size(self):
        status = (WEB / "settings.js").read_text()
        row = status[status.index("openInRow()") : status.index("// Credits TMDb")]
        self.assertIn("choice-row", row)
        self.assertNotIn("min-width", row)
        css = (WEB / "app.css").read_text()
        self.assertIn("grid-auto-columns: minmax(0, 1fr)", css, "both choices get the same width")
        notify_css = (WEB / "notify.css").read_text()
        self.assertIn(".choice-row { display: flex; flex-wrap: wrap; }", notify_css, "the control wraps under the label")
        self.assertIn(".choice-row .title, .choice-row .sub { white-space: normal; overflow: visible; }", notify_css)
        self.assertIn("@container shell (min-width: 700px) { .choice-row .segmented", notify_css)


if __name__ == "__main__":
    unittest.main()
