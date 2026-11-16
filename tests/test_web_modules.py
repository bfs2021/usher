"""The client is split into modules by screen: the offline cache must hold every one of them, and every
import must point at a file that exists, or the Home Screen app would not start without a connection."""

import re
import unittest

from tests.webjs import WEB


class WebModuleTests(unittest.TestCase):
    def test_service_worker_caches_every_module(self):
        shell = re.search(r"const SHELL = \[(.*?)\];", (WEB / "sw.js").read_text(), re.S).group(1)
        cached = set(re.findall(r'"/([\w.-]+\.js)"', shell))
        modules = {p.name for p in WEB.glob("*.js")} - {"sw.js"}
        self.assertEqual(modules - cached, set(), "add the new module to SHELL in web/sw.js")

    def test_every_import_names_a_module_that_exists(self):
        for path in WEB.glob("*.js"):
            for name in re.findall(r'^import .* from "\./([\w.-]+)";', path.read_text(), re.M):
                self.assertTrue((WEB / name).is_file(), f"{path.name} imports {name}, which doesn't exist")

    def test_every_screen_module_is_part_of_the_app(self):
        app = (WEB / "app.js").read_text()
        for name in (
            "shell",
            "home",
            "downloads",
            "upcoming",
            "status",
            "settings",
            "notify",
            "advanced",
            "updates",
            "sheets",
            "search",
            "discover",
            "account",
        ):
            self.assertIn(f'from "./{name}.js"', app)


if __name__ == "__main__":
    unittest.main()
