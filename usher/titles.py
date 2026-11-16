"""One identity per title, linked across the integrations, and the change notices that keep every view in step.

The same movie or show appears all over the app: Home's rows (the media server's lists), Search, Library, Upcoming,
Downloads, Tidy up, the details sheet and notifications. Each comes from a different integration with its own ID:
Sonarr's or Radarr's ID, the media server's item IDs, TMDb, TVDB and IMDb IDs, and Usher's request IDs.

Titles links them. A title's key is its provider ID ("movie:<TMDb id>", "show:<TVDB id>"); a media server item is
matched to it by provider ID or by folder, the same matching Jellyfin.find uses. Every change to a title goes
through State.title_changed (Get it, Remove, Tidy up, a family-folder move) or is noticed by the poller (added,
removed, files changed, moved, played). Each change bumps `seq`, is kept in a short log that the live updates send
to every open device as a `changed` event, and calls the listeners that drop the server's caches holding that title.

A title that was just removed is hidden from the media server's lists until the media server notices its files
have gone (which can take until its next library scan), so it doesn't linger under Continue watching.
"""

import hashlib
import threading
import time
from collections import deque

HIDE_FOR = 24 * 3600  # seconds a removed title stays hidden from the media server's lists at most
LOG_SIZE = 200  # change notices kept for devices catching up
REAPPEAR_AFTER = 120  # seconds before a removed title seen again in Sonarr or Radarr counts as added back


def _val(x):
    """A usable ID: a non-empty string or a non-zero number (never a placeholder from a test double)."""
    if isinstance(x, bool) or not isinstance(x, (int, str)) or x in ("", 0):
        return None
    return x


def _folder(path):
    path = path if isinstance(path, str) else ""
    return path.rstrip("/") or None


def _under(path, folder):
    path, folder = _folder(path), _folder(folder)
    return bool(path and folder) and (path == folder or path.startswith(folder + "/"))


def ident(kind, item):
    """The identity of a Sonarr show or Radarr movie (a library record or a lookup result)."""
    item = item if isinstance(item, dict) else {}
    tmdb, tvdb = _val(item.get("tmdbId")), _val(item.get("tvdbId"))
    ext = tmdb if kind == "movie" else tvdb
    arr = _val(item.get("id"))
    return {
        "key": f"{kind}:{ext}" if ext else f"{kind}:arr:{arr}",
        "kind": kind,
        "id": arr,
        "ext": ext,
        "tmdb": tmdb,
        "tvdb": tvdb if kind == "show" else None,
        "imdb": _val(item.get("imdbId")),
        "path": _folder(item.get("path")),
        "title": item.get("title") if isinstance(item.get("title"), str) else "",
    }


def public(t):
    """What a device needs to find the title in its own lists: kind with the Sonarr/Radarr ID (Library, Upcoming,
    Downloads, seasons) or with the provider ID (Search), and the key (Home's cards carry it)."""
    return {k: t.get(k) for k in ("key", "kind", "id", "ext", "title")}


def signature(kind, item):
    """The facts that change what a title looks like in the app: files, size, folder, wanted or not."""
    if kind == "movie":
        return (bool(item.get("hasFile")), item.get("sizeOnDisk") or 0, _folder(item.get("path")), bool(item.get("monitored")))
    st = item.get("statistics") or {}
    return (st.get("episodeFileCount") or 0, st.get("episodeCount") or 0, st.get("sizeOnDisk") or 0, _folder(item.get("path")), bool(item.get("monitored")))


# Bumped when pictures already kept on devices must not be used again (for example, cut-off posters). Every
# poster URL carries it, so a new value means a new URL and every device fetches its pictures afresh.
ART_EPOCH = "2"


def art_url(path, version=None):
    """The one place poster URLs are made: /api/art/<path>, with the picture's own version (?v=, which lets
    the server keep it on the device for a long time) when there is one, and always the art epoch."""
    return f"/api/art/{path}?" + (f"v={version}&" if version else "") + f"a={ART_EPOCH}"


def arr_art(kind, item):
    """Sonarr's or Radarr's poster for a title, versioned by the picture itself so a new one replaces the copy
    devices keep, while an unchanged one stays cached. `item` is the title, or just its id."""
    item = item if isinstance(item, dict) else {"id": item}
    path = f"{'radarr' if kind == 'movie' else 'sonarr'}/{item.get('id')}"
    for img in item.get("images") or []:
        if isinstance(img, dict) and img.get("coverType") == "poster":
            src = img.get("url") or img.get("remoteUrl") or ""
            if src:
                return art_url(path, hashlib.sha256(src.encode()).hexdigest()[:10])
    return art_url(path)


def jf_art(item_id, tag=None, kind=None):
    """The media server's picture of an item, versioned by its image tag (which changes with the picture)."""
    tag = tag if isinstance(tag, str) else ""
    return art_url(f"jellyfin/{item_id}" + (f"/{kind}" if kind else ""), "".join(c for c in tag if c.isalnum())[:16])


def remote_art(key):
    """A picture from TMDb and the like, fetched by Usher itself (actions._art_key keeps the address)."""
    return art_url(f"remote/{key}")


class Titles:
    def __init__(self):
        self._lock = threading.Lock()
        self._known: dict = {}  # key -> identity, from Sonarr's and Radarr's lists
        self._sig: dict = {}  # key -> signature()
        self._synced: set = set()  # kinds seen at least once (the first look isn't a change)
        self._by_id: dict = {}  # ("movie", "tmdb", "123") -> key
        self._by_path: dict = {}  # folder -> key
        self._by_arr: dict = {}  # ("movie", Radarr id) -> key
        self._jf: dict = {}  # media server item ID -> key (shows and movies matched so far)
        self._hidden: list = []  # titles just removed: {"title", "season", "at", "jf"}
        self.seq = 0
        self._log: deque = deque(maxlen=LOG_SIZE)
        self.listeners: list = []  # fn(what, [identity]) for every change

    # what Sonarr and Radarr have ----------------------------------------------------------------------
    def sync(self, kind, items):
        """Take Sonarr's series or Radarr's movies as last fetched. Returns the changes since the last look, as
        [(what, identity)] with what = added | removed | moved | files."""
        if not isinstance(items, list):
            return []
        now = {}
        for it in items:
            if isinstance(it, dict) and _val(it.get("id")):
                t = ident(kind, it)
                now[t["key"]] = (t, signature(kind, it))
        changes = []
        with self._lock:
            before = {k: v for k, v in self._known.items() if v["kind"] == kind}
            first = kind not in self._synced
            if not now and len(before) > 3:
                return []  # an empty list where there were many: a glitch, not everything removed
            self._synced.add(kind)
            if not first:
                for k, t in before.items():
                    if k not in now:
                        changes.append(("removed", t))
                for k, (t, sig) in now.items():
                    old = self._sig.get(k)
                    if k not in before:
                        changes.append(("added", t))
                    elif before[k].get("path") != t.get("path"):
                        changes.append(("moved", t))
                    elif old != sig:
                        changes.append(("files", t))
            for k in before:
                if k not in now:
                    self._known.pop(k, None)
                    self._sig.pop(k, None)
            for k, (t, sig) in now.items():
                self._known[k], self._sig[k] = t, sig
            self._reindex()
            # A title removed a while ago that's back in Sonarr or Radarr was added again: show it again.
            stamp = time.time()
            self._hidden = [h for h in self._hidden if not (h["gone"] and h["title"]["key"] in now and stamp - h["at"] > REAPPEAR_AFTER)]
        return changes

    def _reindex(self):
        self._by_id, self._by_path, self._by_arr = {}, {}, {}
        for k, t in self._known.items():
            for field in ("tmdb", "tvdb", "imdb"):
                if t.get(field):
                    self._by_id[(t["kind"], field, str(t[field]).lower())] = k
            if t.get("path"):
                self._by_path[t["path"]] = k
            if t.get("id"):
                self._by_arr[(t["kind"], t["id"])] = k

    def get(self, kind, arr_id=None, ext=None):
        """A title's identity by its Sonarr/Radarr ID or its provider ID, if Usher knows it."""
        with self._lock:
            key = self._by_arr.get((kind, arr_id)) if arr_id is not None else f"{kind}:{ext}"
            return dict(self._known[key]) if key in self._known else None

    def identify(self, it):
        """The identity of a media server item (movie, show, season or episode), or None."""
        if not isinstance(it, dict):
            return None
        kind = "movie" if it.get("Type") == "Movie" else "show"
        with self._lock:
            for jid in (it.get("Id"), it.get("SeriesId")):
                key = self._jf.get(jid)
                if key in self._known:
                    return dict(self._known[key])
            if it.get("Type") in ("Movie", "Series"):
                for field, value in (it.get("ProviderIds") or {}).items():
                    key = self._by_id.get((kind, field.lower(), str(value).lower()))
                    if key:
                        self._jf[it.get("Id")] = key
                        return dict(self._known[key])
            path = _folder(it.get("Path"))
            while path:
                key = self._by_path.get(path)
                if key:
                    if it.get("Type") in ("Movie", "Series"):
                        self._jf[it.get("Id")] = key
                    return dict(self._known[key]) if self._known[key]["kind"] == kind else None
                path = path.rpartition("/")[0]
        return None

    # titles just removed --------------------------------------------------------------------------
    def hide(self, t, season=None, gone=False):
        """Leave this title (or one season of a show) out of the media server's lists until it catches up.
        gone: the title itself was deleted from Sonarr or Radarr (not just its files tidied away)."""
        with self._lock:
            jf = {j for j, k in self._jf.items() if k == t["key"]}
            self._hidden = [h for h in self._hidden if not (h["title"]["key"] == t["key"] and h["season"] == season)]
            self._hidden.append({"title": dict(t), "season": season, "at": time.time(), "jf": jf, "gone": bool(gone and season is None)})

    def unhide(self, key, season=None):
        """Asked for again: show it again. season None clears every mark for the title."""
        with self._lock:
            self._hidden = [h for h in self._hidden if not (h["title"]["key"] == key and (season is None or h["season"] in (None, season)))]

    def is_hidden(self, key):
        """Deleted from Sonarr or Radarr just now, so Search mustn't show it as in the library."""
        with self._lock:
            return any(h["gone"] and h["title"]["key"] == key for h in self._live())

    def _live(self):
        cutoff = time.time() - HIDE_FOR
        self._hidden = [h for h in self._hidden if h["at"] > cutoff]
        return self._hidden

    def hides(self, it):
        """Whether a media server item belongs to a title (or season) that was just removed."""
        if not isinstance(it, dict):
            return False
        with self._lock:
            marks = list(self._live())
        for h in marks:
            t = h["title"]
            if t["kind"] == "movie" and it.get("Type") != "Movie":
                continue
            if t["kind"] == "show" and it.get("Type") not in ("Series", "Season", "Episode"):
                continue
            if h["season"] is not None:
                if it.get("Type") == "Series":
                    continue  # the rest of the show stays
                number = it.get("ParentIndexNumber") if it.get("Type") == "Episode" else it.get("IndexNumber")
                if number != h["season"]:
                    continue
            if it.get("Id") in h["jf"] or it.get("SeriesId") in h["jf"]:
                return True
            if it.get("Type") in ("Movie", "Series"):
                have = {k.lower(): str(v).lower() for k, v in (it.get("ProviderIds") or {}).items() if v}
                if any(t.get(f) and have.get(f) == str(t[f]).lower() for f in ("tmdb", "tvdb", "imdb")):
                    return True
            if _under(it.get("Path"), t.get("path")):
                return True
        return False

    # change notices ---------------------------------------------------------------------------------
    def changed(self, what, titles):
        """Record a change to these titles and tell the listeners. Returns the change number."""
        titles = [t for t in titles if t]
        if not titles:
            return self.seq
        with self._lock:
            self.seq += 1
            self._log.append({"seq": self.seq, "what": what, "titles": [public(t) for t in titles]})
            seq = self.seq
        for fn in list(self.listeners):
            fn(what, titles)
        return seq

    def clears(self, cache, holds=None):
        """Make a change to a title clear it from `cache` (a TTLCache): everything when `holds` is not given, else the
        entries for which holds(key, value, title) is true for one of the titles."""

        def listener(what, titles):
            if holds is None:
                cache.clear()
            else:
                cache.forget(lambda key, value: any(holds(key, value, t) for t in titles))

        self.listeners.append(listener)

    def since(self, seq):
        """Change notices after `seq`, oldest first. A device too far behind gets one notice meaning "everything"."""
        with self._lock:
            if seq >= self.seq:
                return []
            if not self._log or self._log[0]["seq"] > seq + 1:
                return [{"seq": self.seq, "what": "changed", "titles": [], "all": True}]
            return [dict(n) for n in self._log if n["seq"] > seq]
