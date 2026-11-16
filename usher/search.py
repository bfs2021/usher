"""Search: what people type in the search box, answered from the library and Sonarr's and Radarr's lookups."""

import logging
import time

from .cache import MISSING
from .clients import ApiError

log = logging.getLogger("usher.actions")


class Superseded(Exception):
    """A queued lookup nobody is waiting for any more: its client has searched for something else."""


SEARCH_WAIT = 25  # seconds to wait for Sonarr or Radarr before giving up on that source
LIBRARY_RANK = 100  # library-only matches sort after the lookups' own order


def merge_results(rows):
    """One row per title: a lookup's row (which has its rank) replaces a library match for the same title."""
    by = {}
    for r in rows:
        key = (r["kind"], r["ext"])
        if key not in by or r["rank"] < by[key]["rank"]:
            by[key] = r
    return list(by.values())


def sort_results(rows, q):
    """Exact title first, then titles that start with the query, then each source's own order."""
    ql = q.lower()
    return sorted(rows, key=lambda r: (r["title"].lower() != ql, not r["title"].lower().startswith(ql), r["rank"]))


def _ratings(raw, kind):
    """IMDb, Rotten Tomatoes and Metacritic scores that Radarr already returns with a movie lookup (no extra key).
    Sonarr's score comes from the TV listings and is too sparse to show, so shows get TMDb's score instead."""
    out = {}
    if kind != "movie" or not isinstance(raw, dict):
        return out
    imdb = raw.get("imdb") or {}
    if imdb.get("value") and (imdb.get("votes") or 0) >= 50:
        out["imdb"] = round(float(imdb["value"]), 1)
    rt = raw.get("rottenTomatoes") or {}
    if rt.get("value"):
        out["rt"] = int(rt["value"])
    mc = raw.get("metacritic") or {}
    if mc.get("value"):
        out["mc"] = int(mc["value"])
    return out


def _poster(images):
    for img in images or []:
        if img.get("coverType") == "poster" and img.get("remoteUrl"):
            return img["remoteUrl"]
    return None


class SearchActions:
    # search ----------------------------------------------------------------------
    # Each source answers on its own (part=library|show|movie), so the app shows titles as they arrive:
    # library matches at once from the poller's data, then TV and movies from Sonarr's and Radarr's lookups.
    # Lookups are cached for SEARCH_TTL by query; badges are always worked out fresh from current state.
    def search(self, q: str, part=None, client=None):
        """part None = everything merged (one reply); "library", "show" or "movie" = just that source.
        client: who's asking (one per app tab); a lookup still queued when that client has moved on is skipped."""
        q = " ".join(q.split())[:100]
        if len(q) < 2:
            return {"results": [], "failed": []}
        started = time.monotonic()
        if client is not None:
            with self._lock:
                self._latest[client] = q.lower()
                self._latest.move_to_end(client)
                while len(self._latest) > 500:
                    self._latest.popitem(last=False)
        if part == "library":
            out, failed = self._library_rows(q), []
        elif part in ("show", "movie"):
            try:
                out, failed = self._source_rows(part, q, client), []
            except Superseded:
                return {"results": [], "failed": [], "superseded": True}
            except (ApiError, TimeoutError) as e:
                log.warning("%s search failed: %s", "TV" if part == "show" else "movie", e)
                out, failed = [], ["TV" if part == "show" else "movies"]
        elif part is None:
            out, failed = self._library_rows(q), []
            jobs = [(k, self._lookup_future(k, q, None)) for k in ("show", "movie")]
            for kind, (fut, cached) in jobs:
                try:
                    out += self._rows(kind, cached if fut is None else fut.result(timeout=SEARCH_WAIT))
                except (ApiError, TimeoutError) as e:
                    log.warning("%s search failed: %s", "TV" if kind == "show" else "movie", e)
                    failed.append("TV" if kind == "show" else "movies")
            out = merge_results(out)
        else:
            raise ValueError(f"unknown search part {part!r}")
        out = sort_results(out, q)
        log.info("search %s for %r: %d results in %.2fs", part or "all", q, len(out), time.monotonic() - started)
        return {"results": out[:24], "failed": failed}

    def _source_rows(self, kind, q, client):
        fut, cached = self._lookup_future(kind, q, client)
        return self._rows(kind, cached if fut is None else fut.result(timeout=SEARCH_WAIT))

    def _lookup_future(self, kind, q, client):
        """(None, cached lookup) when it's fresh in the cache, else (future, None). The same lookup already
        under way is shared rather than sent twice."""
        key = (kind, q.lower())
        with self._lock:
            hit = self._search_cache.get(key, MISSING, ttl=self.search_ttl())
            if hit is not MISSING:
                return None, hit
            job = self._inflight.get(key)
            if job is None:
                job = self._inflight[key] = [None, {client}]  # who's waiting is known before the lookup can start
                job[0] = self._search_pool.submit(self._run_lookup, kind, q, key)
            else:
                job[1].add(client)
            return job[0], None

    def _run_lookup(self, kind, q, key):
        try:
            with self._lock:
                askers = self._inflight[key][1]
                if None not in askers and all(self._latest.get(c) != key[1] for c in askers):
                    log.info("%s lookup for %r skipped: a newer search replaced it", kind, q)
                    raise Superseded()
            started = time.monotonic()
            app, path = (self.sonarr, "series/lookup") if kind == "show" else (self.radarr, "movie/lookup")
            found = app.get(path, term=q) or []
            log.info("%s lookup for %r took %.2fs", kind, q, time.monotonic() - started)
            with self._lock:
                self._search_cache.set(key, found)
            return found
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def _library_rows(self, q):
        """Titles already in the library whose name holds every word typed, from what the poller last saw."""
        words = q.lower().split()

        def hit(x):
            title = (x.get("title") or "").lower()
            return all(w in title for w in words)

        gone = self.titles.is_hidden  # removed a moment ago, before the poller has looked again
        shows = [s for s in self.state._raw.get("series", []) if s.get("tvdbId") and hit(s) and not gone(f"show:{s['tvdbId']}")][:10]
        movies = [m for m in self.state._raw.get("movies", []) if m.get("tmdbId") and hit(m) and not gone(f"movie:{m['tmdbId']}")][:10]
        return self._rows("show", shows, LIBRARY_RANK) + self._rows("movie", movies, LIBRARY_RANK)

    def _rows(self, kind, found, rank_from=0):
        """Search rows for one source's lookup (or library) objects. Badges come from current state each time."""
        asked = self.store.requesters()
        out = []
        if kind == "show":
            series_lib = {s["id"]: s for s in self.state._raw.get("series", [])}
            for rank, s in enumerate(found[:15]):
                if not s.get("tvdbId"):
                    continue
                self._remember("show", s["tvdbId"], s)
                lib_id = None if self.titles.is_hidden(f"show:{s['tvdbId']}") else (s.get("id") or None)
                st = (series_lib.get(lib_id) or {}).get("statistics") or s.get("statistics") or {}
                by = asked.get(("show", lib_id))
                out.append(
                    {
                        "kind": "show",
                        "ext": s["tvdbId"],
                        "tmdb": s.get("tmdbId") or None,
                        "ratings": {},
                        "title": s.get("title", ""),
                        "year": s.get("year"),
                        "overview": (s.get("overview") or "")[:500],
                        "network": s.get("network"),
                        "seasons": len([x for x in s.get("seasons", []) if x.get("seasonNumber")]),
                        "ended": s.get("ended", s.get("status") == "ended"),
                        "cert": s.get("certification") or None,
                        "art": self._art_key(_poster(s.get("images"))),
                        "inLibrary": bool(lib_id),
                        "libId": lib_id,
                        "have": st.get("episodeFileCount", 0),
                        "total": st.get("episodeCount", 0),
                        "by": by[0] if by else None,
                        "rank": rank_from + rank,
                    }
                )
        else:
            for rank, m in enumerate(found[:15]):
                if not m.get("tmdbId"):
                    continue
                self._remember("movie", m["tmdbId"], m)
                lib_id = None if self.titles.is_hidden(f"movie:{m['tmdbId']}") else (m.get("id") or None)
                by = asked.get(("movie", lib_id))
                out.append(
                    {
                        "kind": "movie",
                        "ext": m["tmdbId"],
                        "tmdb": m["tmdbId"],
                        "ratings": _ratings(m.get("ratings"), "movie"),
                        "title": m.get("title", ""),
                        "year": m.get("year"),
                        "overview": (m.get("overview") or "")[:500],
                        "runtime": m.get("runtime"),
                        "studio": m.get("studio"),
                        "cert": m.get("certification") or None,
                        "art": self._art_key(_poster(m.get("images"))),
                        "inLibrary": bool(lib_id),
                        "libId": lib_id,
                        "hasFile": bool(m.get("hasFile")),
                        "monitored": bool(m.get("monitored")),
                        "available": m.get("isAvailable", True),
                        "by": by[0] if by else None,
                        "rank": rank_from + rank,
                    }
                )
        return out
