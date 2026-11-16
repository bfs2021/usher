"""Issues, pull requests, commits and repo text follow standard public conventions: one author
identity set outside the repo, no private links or tool boilerplate, Conventional Commits titles without
a version, and text written for someone with no private context."""

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB = ROOT / ".github"
# Where the conventions are written down, and the text that gets copied into issues, pull requests and commits.
CONVENTION_FILES = [
    ROOT / "CLAUDE.md",
    ROOT / "CONTRIBUTING.md",
    GITHUB / "pull_request_template.md",
    *sorted((GITHUB / "ISSUE_TEMPLATE").glob("*.yml")),
]


def read(path):
    return path.read_text(encoding="utf-8")


def tracked_text_files():
    """Every file in git's index, so an untracked leftover on disk doesn't count."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:  # not a git checkout (a copy of the index): every file on disk is tracked
        return [p for p in ROOT.rglob("*") if p.is_file() and not {".git", "__pycache__"} & set(p.parts)]
    return [ROOT / name for name in listed.stdout.split("\0") if name and (ROOT / name).is_file()]


# The release history keeps its past entries, so it may still describe the old wording of the rule.
HISTORY = ROOT / "CHANGELOG.md"
ATTRIBUTION = re.compile(r"(?i)\bai (coding )?assistance|\bai use\b|co-authored-by|co-author trailer|session link|claude-session|generated (by|with) \[claude")


class NoAttributionTests(unittest.TestCase):
    def test_settings_switch_off_the_tools_attribution(self):
        """The three-key form: `"attribution": false` makes older versions of the tool skip the whole file."""
        settings = json.loads(read(ROOT / ".claude" / "settings.json"))
        self.assertEqual(settings["attribution"], {"commit": "", "pr": "", "sessionUrl": False})
        self.assertNotIn("includeCoAuthoredBy", settings)

    def test_pointer_file_says_no_attribution_and_sessions_open_their_own_pull_request(self):
        text = read(ROOT / "CLAUDE.md")
        self.assertIn("Commits and pull requests carry no private links or tool boilerplate", text)
        self.assertIn("opens its own draft pull request", text)
        self.assertNotIn("Never open a pull request", text)
        self.assertNotIn("Open pull request workflow", text)
        self.assertNotIn("COMMIT_AUTHOR_EMAIL", text)  # the author identity is set where the session starts
        self.assertNotIn("Commit author: `Claude", text)

    def test_readme_and_contributing_state_the_same_ai_tools_sentence(self):
        sentence = (
            "Usher is built with AI coding tools: a maintainer directs the work and reviews every change, and the tests and checks run on each pull request."
        )
        for name in ("README.md", "CONTRIBUTING.md"):
            self.assertIn(sentence, read(ROOT / name), name)

    def test_no_file_describes_the_attribution_lines(self):
        for path in tracked_text_files():
            if path in (Path(__file__).resolve(), HISTORY) or path.suffix in {".png", ".ico", ".woff2"}:
                continue
            match = ATTRIBUTION.search(path.read_text(encoding="utf-8", errors="replace"))
            self.assertIsNone(match, f"{path.relative_to(ROOT)}: {match and match.group(0)!r}")

    def test_contributing_still_has_sessions_open_their_own_pull_request(self):
        text = read(ROOT / "CONTRIBUTING.md")
        self.assertIn("opens its own draft pull request", text)
        self.assertNotIn("never opens a pull request", text)


class TitleTests(unittest.TestCase):
    def test_pull_request_titles_are_conventional_commits_without_a_version(self):
        for path in (ROOT / "CLAUDE.md", ROOT / "CONTRIBUTING.md", GITHUB / "pull_request_template.md"):
            text = read(path)
            self.assertNotIn("<version>: <summary>", text, path.name)
            self.assertNotIn("no `feat:` prefix", text, path.name)
        self.assertIn("Conventional Commits", read(GITHUB / "pull_request_template.md"))
        self.assertIn("Conventional Commits", read(ROOT / "CONTRIBUTING.md"))


class IssueFormTests(unittest.TestCase):
    def test_issues_come_from_the_forms_only(self):
        self.assertRegex(read(GITHUB / "ISSUE_TEMPLATE" / "config.yml"), r"(?m)^blank_issues_enabled: false$")

    def test_forms_carry_no_private_notes(self):
        for path in sorted((GITHUB / "ISSUE_TEMPLATE").glob("*.yml")):
            text = read(path)
            self.assertNotRegex(text, r"(?i)\btype: (bug|enhancement|feature)|decided on|maintainer", path.name)


class PublicTextRuleTests(unittest.TestCase):
    RULE = "written for a public reader with no private context"

    def test_rule_is_standing_in_the_pointer_file_and_contributing(self):
        self.assertIn(self.RULE, read(ROOT / "CLAUDE.md"))
        self.assertIn(self.RULE, read(ROOT / "CONTRIBUTING.md"))

    def test_contributing_describes_the_public_flow_not_a_session(self):
        text = read(ROOT / "CONTRIBUTING.md")
        self.assertNotIn("/fix-issue", text)
        self.assertIn("squash", text)

    def test_conventions_files_have_no_private_dates(self):
        for path in CONVENTION_FILES:
            self.assertNotRegex(read(path), r"(?i)decided on \d", path.name)


if __name__ == "__main__":
    unittest.main()


class QuestionFormTests(unittest.TestCase):
    def test_question_form_sets_the_question_label(self):
        text = read(GITHUB / "ISSUE_TEMPLATE" / "question.yml")
        self.assertRegex(text, r"(?m)^labels: \[question\]$")
        self.assertIn("Settings → About", text)

    def test_support_page_points_to_the_form_not_a_title_prefix(self):
        text = read(ROOT / "SUPPORT.md")
        self.assertIn("Question form", text)
        self.assertNotIn('"Question:"', text)

    def test_issue_chooser_links_support_and_security_with_blank_issues_off(self):
        text = read(GITHUB / "ISSUE_TEMPLATE" / "config.yml")
        self.assertRegex(text, r"(?m)^blank_issues_enabled: false$")
        self.assertRegex(text, r"(?m)^\s+url: \S+/SUPPORT\.md$")
        self.assertRegex(text, r"(?m)^\s+url: \S+/SECURITY\.md$")
