"""Titles kept out of Tidy up, shared by everyone who uses the server.

A title is kept by its catalogue ID (TMDb for movies, TVDB for shows), never by name, in `kept.json` in the data
folder. Keeping a show covers all its seasons, including ones added later. Nothing is deleted by keeping or by
putting back; a kept title that has left the server simply stops being listed.
"""

import json
import logging
import threading
from pathlib import Path

from .files import write_private

log = logging.getLogger("usher.kept")


def kept_key(kind, item):
    """The catalogue ID a title is kept by, from Radarr's movie or Sonarr's show (kind: movie | show); None without one."""
    catalogue, value = ("tmdb", item.get("tmdbId")) if kind == "movie" else ("tvdb", item.get("tvdbId"))
    return f"{kind}:{catalogue}:{value}" if value else None


class Kept:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "kept.json"
        self._lock = threading.Lock()
        self._keys = self._load()

    def _load(self):
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s, starting fresh: %s", self.path.name, e)
            return set()
        return {k for k in data if isinstance(k, str)} if isinstance(data, list) else set()

    def keys(self):
        with self._lock:
            return frozenset(self._keys)

    def _change(self, update):
        with self._lock:
            before = set(self._keys)
            update(self._keys)
            if self._keys == before:
                return
            try:
                write_private(self.path, json.dumps(sorted(self._keys)))
            except OSError:
                self._keys = before  # not saved, so not kept: the person is told rather than left thinking it is
                raise

    def keep(self, key):
        self._change(lambda keys: keys.add(key))

    def put_back(self, key):
        self._change(lambda keys: keys.discard(key))
