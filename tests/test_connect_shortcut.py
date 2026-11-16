"""Away from home: the Connect Shortcut's install link is an optional server setting, offered in Settings only when
set, and the manual steps wait for Tailscale before reopening Usher."""

import shutil
import subprocess
import unittest

from tests.webjs import WEB
from usher import config

LINK = "https://www.icloud.com/shortcuts/0123456789abcdef"


class ShortcutSettingTests(unittest.TestCase):
    def test_empty_by_default(self):
        self.assertEqual(config.KEYS["server.shortcut_url"].default, "")

    def test_only_an_icloud_shortcuts_link_or_nothing(self):
        check = config.KEYS["server.shortcut_url"].check
        self.assertEqual(check(LINK), "")
        self.assertEqual(check(""), "")
        self.assertNotEqual(check("https://example.org/shortcut"), "")
        self.assertNotEqual(check("javascript:alert(1)"), "")
        self.assertNotEqual(check("https://www.icloud.com/shortcuts/"), "")
        self.assertNotEqual(check(LINK + '" onclick="x'), "")
        self.assertNotEqual(check(LINK + "?a=b"), "")

    def test_the_link_reaches_the_client(self):
        source = (WEB.parent / "usher" / "auth_routes.py").read_text()
        self.assertIn('"connectShortcut": config.get("server.shortcut_url")', source)


@unittest.skipUnless(shutil.which("node"), "node is needed")
class ShortcutClientTests(unittest.TestCase):
    def run_js(self, body):
        script = f'''globalThis.document = {{ createElement: () => ({{}}) }};
const {{ away }} = await import("{WEB / "device.js"}");
{body}'''
        return subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)

    def test_no_row_without_a_link(self):
        out = self.run_js('if (away.installLink({}) !== null || away.installLink(undefined) !== null) process.exit(1);')
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_link_is_offered_when_set(self):
        out = self.run_js(f'if (away.installLink({{ connectShortcut: "{LINK}" }}) !== "{LINK}") process.exit(1);')
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_settings_shows_the_row_only_through_that_link(self):
        settings = (WEB / "settings.js").read_text()
        self.assertIn("away.installLink(store.me)", settings)
        self.assertIn("if (!link || !push.ios()) return null;", settings)

    def test_the_manual_steps_wait_before_reopening(self):
        device = (WEB / "device.js").read_text()
        self.assertLess(device.index("Add Wait"), device.index("Add Open URLs"))


if __name__ == "__main__":
    unittest.main()
