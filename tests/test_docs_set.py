"""The standard documentation set. Every setting is documented, links resolve, and no installation history."""

import re
import unittest
from pathlib import Path

from usher.config import KEYS

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["README.md", "CHANGELOG.md", "SECURITY.md", "SUPPORT.md", "CONTRIBUTING.md", "LICENSE"] + [
    f"docs/{n}.md" for n in ("configuration", "architecture", "interface", "security", "features", "glossary", "credits")
]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class DocsSetTests(unittest.TestCase):
    def test_every_page_exists(self):
        for page in PAGES:
            self.assertTrue((ROOT / page).exists(), page)

    def test_every_config_key_is_in_configuration_md(self):
        text = read("docs/configuration.md")
        for name in KEYS:
            self.assertIn(f"`{name}`", text, f"docs/configuration.md is missing {name}")

    def test_every_config_key_is_in_the_example_file(self):
        example = read("deploy/usher.example.toml")
        for name in KEYS:
            self.assertIn(name.rsplit(".", 1)[1], example, name)

    def test_relative_links_resolve(self):
        for page in PAGES:
            for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)\s]+)\)", read(page)):
                path = (ROOT / page).parent / target.split("#")[0]
                self.assertTrue(path.exists(), f"{page} links to {target}, which doesn't exist")

    def test_readme_is_about_the_project_not_one_installation(self):
        text = read("README.md")
        for stale in ("## Milestones", "describes a home server", "Keep it private"):
            self.assertNotIn(stale, text)

    def test_security_policy_says_how_to_report(self):
        self.assertIn("Report a vulnerability", read("SECURITY.md"))

    def test_support_page_says_where_to_ask_and_what_to_include(self):
        text = read("SUPPORT.md")
        for needed in ("question", "Settings → About", "Status → Logs", "docs/configuration.md", "deploy/README.md", "SECURITY.md"):
            self.assertIn(needed, text)
        self.assertIn("(SUPPORT.md)", read("README.md"))

    def test_changelog_is_not_ahead_of_the_app(self):
        """A version that changed nothing a person would notice has no entry, so the newest entry may be older."""
        from usher import __version__
        from usher.changelog import parse

        def number(version):
            return tuple(int(n) for n in version.split("."))

        newest = parse(read("CHANGELOG.md"))[0]["version"]
        self.assertLessEqual(number(newest), number(__version__))

    def test_licence_is_mit_with_a_generic_copyright_line(self):
        text = read("LICENSE")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Usher contributors", text)

    def test_licence_is_stated_and_none_left_undecided(self):
        self.assertIn("(LICENSE)", read("README.md"))
        self.assertIn("(LICENSE)", read("CONTRIBUTING.md"))
        self.assertIn("own terms", read("docs/credits.md"))
        for page in PAGES:
            self.assertNotIn("Not yet chosen", read(page))


if __name__ == "__main__":
    unittest.main()
