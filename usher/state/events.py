"""Title-change events: what happens when a title changes, and how open screens wait for the next snapshot."""

import json
import logging

from .helpers import iso, utcnow

log = logging.getLogger("usher.state")


class Events:
    def title_changed(self, what, titles, hide=False, season=None, gone=False, paths=(), how="Deleted"):
        """Something happened to these titles (see titles.py): drop every cache that holds them, tell the media
        server about files that went (paths), and send every open device the change with a fresh snapshot.
        hide: leave the title (or one season) out of the media server's lists until it catches up."""
        titles = [t for t in titles if t]
        for t in titles:
            if hide:
                self.titles.hide(t, season, gone=gone)
            elif what == "added":
                self.titles.unhide(t["key"], season)
            if what in ("moved", "added") and t.get("id"):
                self._recheck.add((t["kind"], t["id"]))
        if paths:
            self._try("jellyfin", lambda: self.jf.media_updated(paths, how))
        self.titles.changed(what, titles)
        with self._build_lock:
            self._publish(self._build())
        self.refresh_now()

    def _outside(self, changes):
        """Changes the poller found in Sonarr's and Radarr's lists: made outside Usher (in their own pages, an
        automatic download) or by Usher a moment ago."""
        by: dict = {}
        gone = []
        for what, t in changes:
            if what == "removed":
                if self.titles.is_hidden(t["key"]):
                    continue  # Usher removed it itself and has already said so
                self.titles.hide(t, gone=True)
                gone.append(t)
                if self.store and t.get("id"):
                    self.store.forget_item(t["kind"], t["id"])  # its request closes: nothing left to look for
                log.info("removed outside Usher: %s", t["title"])
            if what == "moved" and t.get("id"):
                self._recheck.add((t["kind"], t["id"]))
            by.setdefault(what, []).append(t)
        if gone:
            self._try("jellyfin", lambda: self.jf.media_updated([t["path"] for t in gone]))
        for what, titles in by.items():
            self.titles.changed(what, titles)

    # publishing -------------------------------------------------------------
    def _publish(self, snap):
        body = json.dumps(snap, sort_keys=True).encode()
        if body == self._json:
            return
        with self._cond:
            self._json = body
            self.version += 1
            snap["version"] = self.version
            snap["updated"] = iso(utcnow())
            self.snapshot = snap
            self._cond.notify_all()

    def wait_events(self, since: int, seq: int, timeout: float):
        """(snapshot, change notices after seq) once either moves on, or after timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self.version != since or self.titles.seq != seq, timeout=timeout)
            snap = self.snapshot
        return snap, self.titles.since(seq)

    def wait(self, since: int, timeout: float):
        with self._cond:
            self._cond.wait_for(lambda: self.version != since, timeout=timeout)
            return self.snapshot
