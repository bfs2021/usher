"""Files in the data folder are readable by Usher alone: one helper writes them all, 0600, whatever the umask."""

import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usher import files

ROOT = Path(__file__).resolve().parent.parent


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class LooseUmask(unittest.TestCase):
    def setUp(self):
        self._old = os.umask(0o022)
        self.addCleanup(os.umask, self._old)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class HelperTests(LooseUmask):
    def test_new_file_is_0600(self):
        p = self.dir / "x.json"
        files.write_private(p, '{"a": 1}')
        self.assertEqual(p.read_text(encoding="utf-8"), '{"a": 1}')
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(os.listdir(self.dir), ["x.json"])  # no temporary file left behind

    def test_bytes_and_old_loose_file_tightened(self):
        p = self.dir / "old.bin"
        p.write_bytes(b"old")
        os.chmod(p, 0o644)
        files.write_private(p, b"new")
        self.assertEqual(p.read_bytes(), b"new")
        self.assertEqual(mode(p), 0o600)

    def test_temporary_file_is_0600_and_fsynced(self):
        seen = []
        real_fsync = os.fsync

        def fsync(fd):
            seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return real_fsync(fd)

        with mock.patch("usher.files.os.fsync", fsync):
            files.write_private(self.dir / "t.json", "{}")
        self.assertEqual(seen, [0o600])

    def test_failed_write_leaves_target_and_no_temporary(self):
        p = self.dir / "keep.json"
        files.write_private(p, "old")
        with mock.patch("usher.files.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                files.write_private(p, "new")
        self.assertEqual(p.read_text(encoding="utf-8"), "old")
        self.assertEqual(os.listdir(self.dir), ["keep.json"])

    def test_make_private_skips_missing(self):
        p = self.dir / "db"
        p.write_text("x")
        files.make_private(p, self.dir / "missing")
        self.assertEqual(mode(p), 0o600)


class WritersUseItTests(LooseUmask):
    def test_replacements_json(self):
        from usher.replace import Replacer

        r = Replacer.__new__(Replacer)
        r.path = self.dir / "replacements.json"
        r.data = {"items": {}, "problems": {}}
        r._save()
        self.assertEqual(json.loads(r.path.read_text(encoding="utf-8"))["items"], {})
        self.assertEqual(mode(r.path), 0o600)

    def test_discover_json(self):
        from usher.discover_rows import DiscoverRows

        d = DiscoverRows.__new__(DiscoverRows)
        d._rows_file = self.dir / "discover.json"
        d.region, d.language = "XX", "xx"
        d._save_rows([])
        self.assertEqual(json.loads(d._rows_file.read_text(encoding="utf-8"))["rows"], [])
        self.assertEqual(mode(d._rows_file), 0o600)

    def test_vapid_key(self):
        from usher.push import Vapid

        Vapid(self.dir, "mailto:someone@example.invalid")
        self.assertEqual(mode(self.dir / "vapid.pem"), 0o600)

    def test_sqlite_database(self):
        from usher.store import Store

        Store(self.dir)
        for name in os.listdir(self.dir):
            self.assertEqual(mode(self.dir / name), 0o600, name)


class NoOtherWritersTests(unittest.TestCase):
    WRITES = re.compile(
        r"\.write_text\(|\.write_bytes\(|\bos\.open\(|\bos\.fdopen\(|\btempfile\.|"
        r"(?<![\w.])open\([^)]*['\"][rb]?[wax+]"
    )

    def test_only_the_helper_writes_files(self):
        for p in sorted((ROOT / "usher").rglob("*.py")):
            if p.name == "files.py":
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(self.WRITES.search(line), f"usher/{p.name}:{n} writes a file; use usher.files.write_private: {line.strip()}")

    def test_pattern_catches_writes(self):
        for line in ('p.write_text("x")', "p.write_bytes(b)", "os.open(p, 1)", 'open(p, "w")', "open(p, 'ab')", 'with open(path, "wb") as f:'):
            self.assertIsNotNone(self.WRITES.search(line), line)
        for line in ('open(p, encoding="utf-8")', "opener.open(req, timeout=5)", "urllib.request.urlopen(req)"):
            self.assertIsNone(self.WRITES.search(line), line)


if __name__ == "__main__":
    unittest.main()
