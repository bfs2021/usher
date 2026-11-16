"""Searches for monitored episodes that have aired and still have no file.

Sonarr only watches the new-release feeds once a show is added, and a feed doesn't always carry an episode
(public indexers, a release posted late), so such an episode would stay missing for good. Usher gives the feeds a
grace period (retries.missing_grace_hours after it aired), then asks Sonarr to search, backing off on the same
schedule as a waiting request ([retries]). Episodes in the queue are left alone, one search covers every due
episode of a season, and at most retries.missing_max_searches are sent per pass so indexers aren't flooded.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from . import config
from .clients import ApiError
from .files import write_private

log = logging.getLogger("usher.missing")

CHECK_EVERY = 60  # seconds between passes
KEEP_FOR = 30 * 86400  # forget an episode this long after Sonarr stopped listing it


def _aired(rec):
    try:
        return datetime.fromisoformat(rec["airDateUtc"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, ValueError):
        return None


class MissingSearcher:
    def __init__(self, sonarr, data_dir, clock=time.time):
        self.sonarr = sonarr
        self.path = Path(data_dir) / "missing-searches.json"
        self.now = clock
        self._last_pass = 0.0
        self.searched = self._load()  # episode id (text) -> {"at": when last searched, "seen": when last listed}

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s, starting fresh: %s", self.path.name, e)
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def _save(self):
        try:
            write_private(self.path, json.dumps(self.searched, sort_keys=True))
        except OSError as e:
            log.warning("couldn't save missing-episode searches: %s", e)

    def _due(self, rec, now):
        aired = _aired(rec)
        if aired is None:
            return False  # no air date: can't tell whether it is out
        last = (self.searched.get(str(rec["id"])) or {}).get("at")
        if last is None:
            return now - aired >= config.get("retries.missing_grace_hours") * 3600
        stuck = now - aired >= config.get("retries.early_for_days") * 86400
        every = config.get("retries.late_every_hours" if stuck else "retries.early_every_hours") * 3600
        return now - last >= every

    def tick(self, raw, force=False):
        """From the poll loop: at most one pass a minute."""
        now = self.now()
        if not force and now - self._last_pass < CHECK_EVERY:
            return
        self._last_pass = now
        self._search(raw, now)

    def _search(self, raw, now):
        queued = {r.get("episodeId") for r in raw.get("queue_tv") or [] if r.get("episodeId")}
        listed = [r for r in raw.get("missing_tv") or [] if isinstance(r, dict) and r.get("id")]
        for rec in listed:
            self.searched.setdefault(str(rec["id"]), {})["seen"] = now
        seasons: dict = {}
        for rec in listed:
            if rec.get("monitored") is False or rec["id"] in queued or not self._due(rec, now):
                continue
            seasons.setdefault((rec.get("seriesId"), rec.get("seasonNumber")), []).append(rec)
        sent = 0
        for (series_id, season), eps in seasons.items():
            if sent >= config.get("retries.missing_max_searches") or not isinstance(series_id, int):
                break
            ids = sorted(e["id"] for e in eps)
            title = (eps[0].get("series") or {}).get("title", f"series {series_id}")
            try:
                self.sonarr.send("POST", "command", {"name": "EpisodeSearch", "episodeIds": ids})
            except ApiError as e:
                log.warning("couldn't search for missing episodes of %s season %s: %s", title, season, e)
                continue
            sent += 1
            for i in ids:
                self.searched[str(i)]["at"] = now
            log.info("searching for %d missing episode(s) of %s, season %s", len(ids), title, season)
        self.searched = {k: v for k, v in self.searched.items() if now - v.get("seen", 0) < KEEP_FOR}
        self._save()
