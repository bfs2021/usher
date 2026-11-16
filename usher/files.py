"""Every file Usher keeps in its data folder is written here, readable by Usher alone.

A write goes to a temporary file in the same folder, created with mode 0600, is flushed to disk, then replaces the
target in one step: a reader sees the old file or the new one, never half of either, and the umask never applies.
A file left by an earlier version with a looser mode is replaced, so it comes out 0600 too.
"""

import os
import tempfile
from pathlib import Path

MODE = 0o600


def write_private(path, data):
    """Write `data` (str as UTF-8, or bytes) to `path`, mode 0600, replacing whatever is there."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # mkstemp: always 0600
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), MODE)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def make_private(*paths):
    """Tighten files something else created (SQLite's database) to 0600; missing ones are skipped."""
    for p in paths:
        try:
            os.chmod(p, MODE)
        except FileNotFoundError:
            pass
