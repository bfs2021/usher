"""Only source is tracked: no binaries or archives outside web/icons/ and the README's pictures in docs/images/."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_BINARY_DIRS = ("web/icons/", "docs/images/")
ARCHIVE_SUFFIXES = (".whl", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".egg", ".jar", ".deb", ".rpm")


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def is_binary(path):
    with open(ROOT / path, "rb") as f:
        return b"\0" in f.read(8192)


def offending(paths, binary=is_binary):
    bad = []
    for p in paths:
        if p.startswith(ALLOWED_BINARY_DIRS):
            continue
        if p.lower().endswith(ARCHIVE_SUFFIXES) or binary(p):
            bad.append(p)
    return bad


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
class TrackedFilesTests(unittest.TestCase):
    def test_no_binaries_or_archives_outside_icons(self):
        files = [p for p in tracked_files() if (ROOT / p).is_file()]
        self.assertEqual(offending(files), [], "binary or archive files are tracked; keep downloads outside the repo")

    def test_check_catches_a_wheel_and_a_binary(self):
        # The check itself: a wheel anywhere, or a file with NUL bytes, is caught; icons are allowed.
        self.assertEqual(offending(["tool-1.0-py3-none-any.whl"], binary=lambda p: False), ["tool-1.0-py3-none-any.whl"])
        self.assertEqual(offending(["deploy/blob.bin"], binary=lambda p: True), ["deploy/blob.bin"])
        self.assertEqual(offending(["usher/x.tar.gz"], binary=lambda p: False), ["usher/x.tar.gz"])
        self.assertEqual(offending(["web/icons/icon-192.png"], binary=lambda p: True), [])
        self.assertEqual(offending(["docs/images/phone-search-light.png"], binary=lambda p: True), [])
        self.assertEqual(offending(["docs/phone-search-light.png"], binary=lambda p: True), ["docs/phone-search-light.png"])

    def test_gitignore_keeps_download_artefacts_out(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for pattern in ("*.whl", "*.tar.gz", "*.zip"):
            self.assertIn(pattern, ignore)


if __name__ == "__main__":
    unittest.main()
