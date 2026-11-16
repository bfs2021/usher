"""Source files stay small: at most 500 lines each, so a session reads and edits only what it needs.

A file already past the limit is allowlisted at its current length: it may shrink, never grow. When a split
brings one under 500, take it off the list.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 500
DIRS = ("usher/", "web/", "deploy/")
SUFFIXES = (".py", ".js", ".css")
ALLOWLIST = {}  # file: ceiling (its length when the rule came in)


def lines(path):
    with open(ROOT / path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def too_long(paths, length=lines, allow=ALLOWLIST):
    """Messages naming each file that is over its limit."""
    bad = []
    for p in paths:
        if not (p.startswith(DIRS) and p.endswith(SUFFIXES)):
            continue
        n, cap = length(p), allow.get(p, LIMIT)
        if n > cap:
            bad.append(f"{p} has {n} lines, limit {cap}" + (" (allowlisted: it may shrink, not grow)" if p in allow else ""))
    return bad


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
class FileSizeTests(unittest.TestCase):
    def tracked(self):
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [p for p in out.decode("utf-8").split("\0") if p and (ROOT / p).is_file()]

    def test_source_files_are_within_their_limit(self):
        self.assertEqual(too_long(self.tracked()), [], "split the file as part of the change (see CONTRIBUTING.md)")

    def test_allowlist_names_only_files_still_over_the_limit(self):
        for p, cap in ALLOWLIST.items():
            self.assertTrue((ROOT / p).is_file(), f"{p} is gone; take it off the allowlist")
            self.assertGreater(lines(p), LIMIT, f"{p} is now within {LIMIT} lines; take it off the allowlist")

    def test_check_catches_growth(self):
        allow = {"usher/old.py": 600}
        size = {
            "usher/new.py": 501,
            "usher/old.py": 601,
            "web/ok.css": 500,
            "tests/test_big.py": 9000,
            "deploy/setup.sh": 9000,
        }
        self.assertEqual(
            too_long(size, length=size.get, allow=allow),
            [
                "usher/new.py has 501 lines, limit 500",
                "usher/old.py has 601 lines, limit 600 (allowlisted: it may shrink, not grow)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
