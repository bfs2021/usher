"""Replaces a stalled download with a healthier copy, within disk and clean-up limits ([downloads] in usher.toml).

A download Sonarr or Radarr started that hasn't moved for downloads.stalled_after_hours (nobody sharing it, stuck
fetching its details, or queued and never starting) gets a look at Sonarr's or Radarr's own release search for that
episode, season or movie, so every rule they apply still holds: quality profile, blocklist, their filters. A release
they accept, naming the right title, of the same scope, not too big, with clearly more people sharing it, is grabbed
alongside the stalled one. Whichever is imported first wins; the other is removed from Transmission with its files
(and blocklisted if it was the stalled one), and Usher checks nothing is left behind. A clean-up that fails, or a
replacement held back for lack of space, is listed in /healthz attention.

downloads.replace = "report" only logs each decision (Status -> Logs). A replacement already racing is always seen
through to its clean-up, whatever the mode, so nothing Usher started is left behind.
"""

import json
import logging
import threading
import time
from pathlib import Path

from . import config
from .clients import ApiError
from .files import write_private
from .releases import title_matches

log = logging.getLogger("usher.replace")

MIN_SEEDERS, SEEDER_FACTOR = 5, 3  # a healthier copy has at least this many sharing it, and this many times more
NEARLY_DONE = 0.9  # never replace a download past this that is still moving
CHECK_EVERY = 60  # seconds between passes
FIND_NEW_FOR = 3600  # how long to wait for a grabbed copy to appear in the queue
MB = 1024 * 1024
DETAIL_FIELDS = ["hashString", "name", "downloadDir", "trackerStats", "peersSendingToUs", "sizeWhenDone"]


def _gb(n):
    return f"{n / 1e9:.1f} GB"


def _rejections_ok(release):
    """Accepted by Sonarr/Radarr, or rejected only because the stalled copy is already in their queue."""
    if release.get("downloadAllowed") is False:
        return False
    texts = [r if isinstance(r, str) else (r.get("message") or r.get("reason") or "") for r in release.get("rejections") or []]
    if not release.get("rejected") and not texts:
        return True
    return bool(texts) and all("queue" in t.lower() for t in texts)


class Replacer:
    def __init__(self, sonarr, radarr, transmission, data_dir, clock=time.time):
        self.apps = {"show": sonarr, "movie": radarr}
        self.tr = transmission
        self.path = Path(data_dir) / "replacements.json"
        self.now = clock
        self._busy = threading.Lock()
        self._last_pass = 0.0
        self._moved: dict = {}  # torrent hash -> (progress, when it last moved)
        self._looked: dict = {}  # item key -> when its releases were last searched
        self._reported: dict = {}  # item key -> the last report-only line, so the log isn't repeated
        self.data = self._load()  # {"items": {item key: {...}}, "problems": {id: line}}

    # persistence ---------------------------------------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"items": data.get("items") or {}, "problems": data.get("problems") or {}}
        except (OSError, ValueError):
            pass
        return {"items": {}, "problems": {}}

    def _save(self):
        try:
            write_private(self.path, json.dumps(self.data, sort_keys=True))
        except OSError as e:
            log.warning("couldn't save replacements: %s", e)

    # what the rest of Usher asks -------------------------------------------------------------------------------
    def racing(self, key):
        """Whether this download (by hash) is one of two copies racing each other."""
        key = (key or "").lower()
        return any(key and key in (a.get("old"), a.get("new")) for a in (i.get("active") for i in self.data["items"].values()) if a)

    def attention(self):
        return sorted(self.data["problems"].values())

    def tick(self, raw):
        """From the poll loop: a pass in the background at most once a minute (release searches are slow)."""
        if time.monotonic() - self._last_pass < CHECK_EVERY or self._busy.locked():
            return
        self._last_pass = time.monotonic()
        snap = {k: raw.get(k) for k in ("queue_tv", "queue_movies", "torrents")}
        threading.Thread(target=self.check, args=(snap,), name="replace", daemon=True).start()

    def check(self, raw):
        """One pass: finish replacements that have a winner, then look for downloads to replace."""
        if not self._busy.acquire(blocking=False):
            return
        try:
            groups = self._groups(raw)
            if not groups and not any(i.get("active") for i in self.data["items"].values()):
                return
            self._track(groups)
            self._resolve(groups)
            mode = config.get("downloads.replace")
            if mode != "off":
                self._start(groups, raw, mode)
            self._save()
        finally:
            self._busy.release()

    # the downloads, one per torrent ----------------------------------------------------------------------------
    def _groups(self, raw):
        groups: dict = {}
        torrents = raw.get("torrents") or {}
        for kind, recs in (("show", raw.get("queue_tv") or []), ("movie", raw.get("queue_movies") or [])):
            for rec in recs:
                h = (rec.get("downloadId") or "").lower()
                if h and (rec.get("protocol") or "torrent") == "torrent":
                    groups.setdefault(h, {"kind": kind, "hash": h, "recs": [], "torrent": torrents.get(h)})["recs"].append(rec)
        for g in groups.values():
            g.update(self._scope(g))
        return groups

    @staticmethod
    def _scope(g):
        """What the download is for: its item key, search, and the title to check release names against."""
        rec = g["recs"][0]
        if g["kind"] == "movie":
            mid = rec.get("movieId")
            return {
                "item": f"movie:{mid}" if mid else None,
                "search": {"movieId": mid},
                "pack": False,
                "about": rec.get("movie") or {},
                "name": (rec.get("movie") or {}).get("title") or rec.get("title"),
            }
        sid = rec.get("seriesId")
        eps = [(r.get("episode") or {}, r.get("episodeId")) for r in g["recs"]]
        seasons = {e.get("seasonNumber") for e, _ in eps}
        series = rec.get("series") or {}
        name = series.get("title") or rec.get("title")
        if not sid or len(seasons) != 1 or not all(i for _, i in eps):
            return {"item": None}
        season = seasons.pop()
        if len(eps) == 1:
            e = eps[0][0]
            return {
                "item": f"show:{sid}:e{eps[0][1]}",
                "search": {"episodeId": eps[0][1]},
                "pack": False,
                "about": series,
                "episodes": [eps[0][1]],
                "name": f"{name} S{season}E{e.get('episodeNumber')}",
            }
        return {
            "item": f"show:{sid}:s{season}",
            "search": {"seriesId": sid, "seasonNumber": season},
            "pack": True,
            "about": series,
            "season": season,
            "episodes": [i for _, i in eps],
            "name": f"{name} season {season}",
        }

    def _track(self, groups):
        now = self.now()
        for h, g in groups.items():
            pct = (g["torrent"] or {}).get("percentDone") or 0.0
            was = self._moved.get(h)
            if was is None or pct > was[0] + 0.0005:
                self._moved[h] = (pct, now)
        for gone in set(self._moved) - set(groups):
            self._moved.pop(gone, None)

    # finishing a race ------------------------------------------------------------------------------------------
    def _resolve(self, groups):
        now = self.now()
        for key, item in self.data["items"].items():
            a = item.get("active")
            if not a:
                continue
            if not a.get("new"):
                found = next((h for h, g in groups.items() if g.get("item") == key and h != a["old"]), None)
                if found:
                    a["new"] = found
                elif now - a["started"] > FIND_NEW_FOR:
                    log.info("the other copy of %s never started downloading; keeping the first", a["name"])
                    item["active"] = None
                continue
            old_here, new_here = a["old"] in groups, a["new"] in groups
            if old_here and new_here:
                continue
            item["active"] = None
            if not old_here and not new_here:
                log.info("both copies of %s are gone; nothing to clean up", a["name"])
                continue
            loser = a["old"] if old_here else a["new"]
            if not self._imported(key, a):
                log.info("one copy of %s went without being added to the library; leaving the other to finish", a["name"])
                continue
            stalled = loser == a["old"]
            log.info("%s: the %s copy was added to the library first; removing the other", a["name"], "new" if stalled else "first")
            self._remove(groups[loser], blocklist=stalled, name=a["name"])

    def _imported(self, key, a):
        """Whether the episode(s) or movie now have a file in the library."""
        kind = key.split(":")[0]
        app = self.apps[kind]
        try:
            if kind == "movie":
                return bool(app.get(f"movie/{a['arr_id']}").get("hasFile"))
            eps = {e.get("id"): e for e in app.get("episode", seriesId=a["arr_id"])}
            return all((eps.get(i) or {}).get("hasFile") for i in a.get("episodes") or [])
        except (ApiError, AttributeError, TypeError) as e:
            log.warning("couldn't check whether %s was added to the library: %s", a["name"], e)
            return False

    def _remove(self, g, blocklist, name):
        """Remove a copy from Sonarr/Radarr and Transmission with its files, then check nothing is left."""
        h, app = g["hash"], self.apps[g["kind"]]
        detail = self._detail(h) or {}
        try:
            app.send("DELETE", f"queue/{g['recs'][0]['id']}", removeFromClient="true", blocklist="true" if blocklist else "false")
        except ApiError as e:
            log.warning("%s couldn't remove the other copy of %s: %s", app.name, name, e)
        if self._detail(h):  # still in Transmission: remove it there directly
            try:
                self.tr.call("torrent-remove", ids=[h], **{"delete-local-data": True})
            except ApiError as e:
                log.warning("Transmission couldn't remove the other copy of %s: %s", name, e)
        problem = f"cleanup:{h}"
        if self._detail(h):
            self.data["problems"][problem] = f"couldn't remove the other copy of {name} from Transmission ({h})"
            return
        left = self._leftovers(detail.get("name"))
        if left:
            self.data["problems"][problem] = f"files left behind by the other copy of {name}: {', '.join(left)}"
            log.warning("files left behind by the other copy of %s: %s", name, ", ".join(left))
            return
        self.data["problems"].pop(problem, None)
        log.info("removed the other copy of %s%s; nothing left on disk", name, " and blocklisted it" if blocklist else "")

    def _detail(self, h):
        try:
            got = self.tr.call("torrent-get", ids=[h], fields=DETAIL_FIELDS).get("torrents") or []
        except ApiError:
            return None
        return got[0] if got else None

    @staticmethod
    def _leftovers(name):
        if not name or "/" in name or name in (".", ".."):
            return []
        out = []
        for folder in config.get("downloads.folders"):
            for p in (Path(folder) / name, Path(folder) / f"{name}.part"):
                if p.exists():
                    out.append(str(p))
        return out

    # starting one ----------------------------------------------------------------------------------------------
    def _start(self, groups, raw, mode):
        now = self.now()
        hours = config.get("downloads.stalled_after_hours")
        items = self.data["items"]
        racing = sum(1 for i in items.values() if i.get("active"))
        stalled = {h for h, g in groups.items() if g.get("item") and g["torrent"] and self._stalled(g, hours)}
        still = {groups[h]["item"] for h in stalled}
        for pid in [p for p in self.data["problems"] if p.startswith("space:") and p[6:] not in still]:
            self.data["problems"].pop(pid)
        for h in stalled:
            g = groups[h]
            key = g["item"]
            item = items.get(key) or {}
            if item.get("active"):
                continue
            if item.get("attempts", 0) >= config.get("downloads.max_attempts"):
                if now - item.get("last_attempt", 0) < config.get("downloads.retry_after_days") * 86400:
                    continue
                item["attempts"] = 0
            if now - self._looked.get(key, -1e18) < hours * 3600:
                continue
            if racing >= config.get("downloads.max_parallel"):
                log.info("%s is stalled, but %d replacements are already running", g["name"], racing)
                return
            self._looked[key] = now
            detail = self._detail(h) or {}
            better = self._better(g, detail)
            if not better:
                log.info("%s is stalled; no healthier copy found yet", g["name"])
                continue
            ok, space = self._space(g, better, detail, raw)
            line = (
                f"{g['name']}: no progress for {int((now - self._moved[h][1]) // 3600)} hours, "
                f"{self._seeders(detail)} sharing it; \"{better['title']}\" has {better.get('seeders')} "
                f"({_gb(better.get('size') or 0)}). Space: {space}"
            )
            if mode == "report":
                if self._reported.get(key) != line:
                    log.info("would try a better copy (report only)%s: %s", "" if ok else ", but not enough space", line)
                    self._reported[key] = line
                continue
            if not ok:
                if f"space:{key}" not in self.data["problems"]:
                    log.info("not enough space to try a better copy: %s", line)
                self.data["problems"][f"space:{key}"] = f"not enough space to try another copy of {g['name']}"
                continue
            self.data["problems"].pop(f"space:{key}", None)
            try:
                self.apps[g["kind"]].send("POST", "release", {"guid": better["guid"], "indexerId": better["indexerId"]})
            except (ApiError, KeyError) as e:
                log.warning("couldn't start a better copy of %s: %s", g["name"], e)
                continue
            arr_id = g["recs"][0].get("movieId" if g["kind"] == "movie" else "seriesId")
            items[key] = {
                "attempts": item.get("attempts", 0) + 1,
                "last_attempt": now,
                "active": {
                    "old": h,
                    "new": None,
                    "started": now,
                    "name": g["name"],
                    "arr_id": arr_id,
                    "episodes": g.get("episodes") or [],
                    "release": better["title"],
                },
            }
            racing += 1
            log.info("trying a better copy: %s", line)

    def _stalled(self, g, hours):
        t, rec = g["torrent"], g["recs"][0]
        if t.get("status") in (0, 5, 6) or (rec.get("status") or "").lower() == "paused":
            return False  # paused by a person, or finished
        if (t.get("percentDone") or 0) >= 0.999:
            return False
        if (t.get("percentDone") or 0) > NEARLY_DONE and (t.get("rateDownload") or 0) > 0:
            return False
        moved = self._moved.get(g["hash"])
        return bool(moved) and self.now() - moved[1] >= hours * 3600

    @staticmethod
    def _seeders(detail):
        counts = [s.get("seederCount") or 0 for s in detail.get("trackerStats") or []]
        return max(counts + [detail.get("peersSendingToUs") or 0, 0])

    def _better(self, g, detail):
        """The healthiest release Sonarr/Radarr accept for the same episode, season or movie, if clearly better."""
        app = self.apps[g["kind"]]
        try:
            releases = app.get("release", _timeout=180, **g["search"])
        except ApiError as e:
            log.warning("couldn't search for a better copy of %s: %s", g["name"], e)
            return None
        size = detail.get("sizeWhenDone") or g["recs"][0].get("size") or 0
        need = max(MIN_SEEDERS, SEEDER_FACTOR * self._seeders(detail))
        limit = config.get("downloads.max_size_factor") * size if size else None
        current = {(r.get("title") or "").lower() for r in g["recs"]}

        def fits(r):
            if (r.get("protocol") or "torrent") != "torrent" or not _rejections_ok(r):
                return False
            if (r.get("infoHash") or "").lower() == g["hash"] or (r.get("title") or "").lower() in current:
                return False
            if not title_matches(r.get("title"), g["about"]) or (r.get("seeders") or 0) < need:
                return False
            if limit is not None and (r.get("size") or 0) > limit:
                return False
            if g["kind"] == "show":
                if g["pack"]:
                    return bool(r.get("fullSeason")) and r.get("seasonNumber") == g["season"]
                return not r.get("fullSeason") and len(r.get("episodeNumbers") or [1]) == 1
            return True

        found = [r for r in releases or [] if isinstance(r, dict) and fits(r)]
        return max(found, key=lambda r: r.get("seeders") or 0) if found else None

    def _space(self, g, release, detail, raw):
        """(enough, how it was worked out): free space after this copy and every download still running."""
        try:
            free = self.tr.call("free-space", path=detail.get("downloadDir") or "")["size-bytes"]
            floor = (self.apps[g["kind"]].get("config/mediamanagement").get("minimumFreeSpaceWhenImporting") or 0) * MB
        except (ApiError, KeyError, TypeError, AttributeError) as e:
            return False, f"couldn't check the free space ({e})"
        running = sum(t.get("leftUntilDone") or 0 for t in (raw.get("torrents") or {}).values() if t.get("status") in (3, 4))
        after = free - running - (release.get("size") or 0)
        need = floor + config.get("downloads.min_free_margin_gb") * 1e9
        return after >= need, (f"{_gb(free)} free, {_gb(running)} still to download, leaving {_gb(after)} after this copy; {_gb(need)} must stay free")
