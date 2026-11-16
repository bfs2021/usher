"""Every GitHub Action a workflow uses is pinned to a full commit hash, so a moved tag can't change what runs.
Dependabot keeps the hashes and their version comments current."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"\s+# v\d+(\.\d+)*$")


def unpinned(text):
    """The `uses:` lines of a workflow that name a tag or branch instead of a commit hash."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        match = USES.match(line)
        if not match:
            continue
        target, rest = match.groups()
        if target.startswith("./"):
            continue  # a reusable workflow in this repository runs at the same commit as the caller
        if not PINNED.fullmatch(target) or not VERSION_COMMENT.fullmatch(rest):
            found.append(f"{number}: {line.strip()}")
    return found


class ActionPinTests(unittest.TestCase):
    def test_every_action_is_pinned_to_a_commit_with_its_version(self):
        workflows = sorted(WORKFLOWS.glob("*.y*ml"))
        self.assertTrue(workflows)
        for path in workflows:
            with self.subTest(workflow=path.name):
                self.assertEqual(unpinned(path.read_text(encoding="utf-8")), [])

    def test_the_check_catches_tags_and_branches_and_exempts_local_workflows(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(len(unpinned("      - uses: actions/checkout@v7\n")), 1)
        self.assertEqual(len(unpinned("        uses: actions/setup-python@main\n")), 1)
        self.assertEqual(len(unpinned(f"      - uses: actions/checkout@{sha[:12]} # v7\n")), 1)
        self.assertEqual(len(unpinned(f"      - uses: actions/checkout@{sha}\n")), 1)  # version comment missing
        self.assertEqual(unpinned(f"      - uses: actions/checkout@{sha} # v7\n"), [])
        self.assertEqual(unpinned(f"        uses: actions/upload-artifact@{sha} # v4.6.2\n"), [])
        self.assertEqual(unpinned("    uses: ./.github/workflows/release.yml\n"), [])

    def test_dependabot_updates_the_pins_in_the_monthly_group(self):
        text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        block = re.search(r"(?m)^  - package-ecosystem: github-actions\n((?:    .*\n)+)", text)
        self.assertIsNotNone(block, "Dependabot doesn't update GitHub Actions")
        block = block.group(1)
        self.assertRegex(block, r"(?m)^    directory: /$")
        self.assertRegex(block, r"(?m)^      interval: monthly$")
        self.assertRegex(block, r'(?m)^        patterns: \["\*"\]$')


if __name__ == "__main__":
    unittest.main()
