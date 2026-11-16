"""Every path the Dockerfile copies must be visible to the build: a path excluded by .dockerignore makes
the image fail to build, and nothing else in the tests builds the image."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def copy_sources(dockerfile):
    """The source paths of every COPY or ADD line, without flags such as --chown."""
    found = []
    for line in dockerfile.splitlines():
        parts = line.split()
        if not parts or parts[0].upper() not in ("COPY", "ADD"):
            continue
        args = [a for a in parts[1:] if not a.startswith("--")]
        found.extend(a.rstrip("/") for a in args[:-1])
    return found


def pattern_regex(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def is_excluded(path, dockerignore):
    """Docker's rule: the last pattern that matches the path or one of its parent folders wins."""
    patterns = [line.strip() for line in dockerignore.splitlines() if line.strip() and not line.startswith("#")]
    pieces = path.split("/")
    candidates = ["/".join(pieces[: n + 1]) for n in range(len(pieces))]
    excluded = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        regex = pattern_regex(pattern.lstrip("!").strip("/"))
        if any(regex.match(c) for c in candidates):
            excluded = not negated
    return excluded


def excluded_sources(dockerfile, dockerignore):
    return [src for src in copy_sources(dockerfile) if is_excluded(src, dockerignore)]


class DockerignoreTests(unittest.TestCase):
    def test_nothing_the_dockerfile_copies_is_excluded(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("CHANGELOG.md", copy_sources(dockerfile))
        self.assertEqual(excluded_sources(dockerfile, dockerignore), [])

    def test_other_markdown_files_stay_out_of_the_image(self):
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for path in ("README.md", "CONTRIBUTING.md"):
            self.assertTrue(is_excluded(path, dockerignore), path)

    def test_a_copied_markdown_file_is_caught(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8") + "COPY README.md .\n"
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertEqual(excluded_sources(dockerfile, dockerignore), ["README.md"])

    def test_the_old_ignore_file_is_caught(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(excluded_sources(dockerfile, "*.md\n"), ["CHANGELOG.md"])


if __name__ == "__main__":
    unittest.main()
