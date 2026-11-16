"""Discover's rows (Search with an empty box), built in the background so opening Discover never waits for TMDb.

A build runs soon after start-up and then every hour; `/api/discover` answers from the last finished build at
once, and a stale copy is served while a fresh one builds behind it. The last build is kept on disk in the data
folder (public TMDb data only), so a restart or deploy doesn't make the next opening slow. In library and
Requested marks, and the poster addresses, are added on each reply, so they follow the library as it changes.
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .clients import ApiError
from .files import write_private

log = logging.getLogger("usher.discover")
FILE = "discover.json"


class DiscoverRows:
    """Mixed into Discover, which supplies `_get`, `_card`, `_details`, `_streams`, `_mark_library` and the settings."""

    ROWS_TTL = 3600  # rebuilt every hour; TMDb's lists are cached for 6 hours, details for a day
    ROWS_FIRST_WAIT = 60  # the first request after a fresh install waits this long at most, for rows only
    STREAMING_WAIT = 120  # a build waits this long for every title's streaming services
    SEEDS, BECAUSE_ROWS = 10, 3

    def _rows_init(self, data_dir):
        self._rows = None  # (monotonic time built, rows), with poster URLs, no library marks
        self._rows_busy = threading.Lock()  # held for a whole build
        self._rows_cv = threading.Condition()
        self._rows_building = False  # True until a build's rows are ready (before its streaming marks)
        self._rows_pool = ThreadPoolExecutor(max_workers=16)  # its own, so search and upcoming never queue behind it
        self._rows_file = Path(data_dir) / FILE if data_dir else None
        self._load_rows()

    # serving --------------------------------------------------------------------------------
    def rows(self, uid=None):
        """Discover's rows; for a signed-in account (uid), with its own Picked for you and without its dislikes."""
        if not self.enabled:
            return {"enabled": False, "rows": []}
        with self._rows_cv:
            hit = self._rows
        if hit is None:
            self._start_rows_build()
            with self._rows_cv:  # only the very first opening ever waits, and for rows only
                self._rows_cv.wait_for(lambda: self._rows is not None or not self._rows_building, self.ROWS_FIRST_WAIT)
                hit = self._rows
        elif time.monotonic() - hit[0] >= self.ROWS_TTL:
            self._start_rows_build()  # the old rows are served meanwhile
        rows = []
        shared = hit[1] if hit else []
        for row in self._rows_for(uid, shared) if uid is not None else shared:
            items = [dict(c, art=self.actions._art_key(c["art"])) for c in row["items"]]
            self._mark_library(items)
            rows.append(dict(row, items=items))
        return {"enabled": True, "region": self.region, "rows": rows}

    # building -------------------------------------------------------------------------------
    def _start_rows_build(self):
        """Starts a build in the background unless one is running. True if it started one."""
        if not self._rows_busy.acquire(blocking=False):
            return False
        with self._rows_cv:
            self._rows_building = True
        threading.Thread(target=self._run_rows_build, name="discover", daemon=True).start()
        return True

    def _run_rows_build(self):
        started = time.monotonic()
        try:
            rows = self._build_rows()
            if rows:  # nothing at all is usually TMDb failing; keep what we had
                self._publish_rows(rows)
            self._done_building()
            if rows:
                self._mark_streaming([c for row in rows for c in row["items"]], wait=self.STREAMING_WAIT)
                self._publish_rows(rows, save=True)
            log.info("discover built in %.1fs (%d rows)", time.monotonic() - started, len(rows))
        except Exception as e:  # noqa: BLE001  the next build tries again
            log.warning("discover build: %s", e)
        finally:
            self._done_building()
            self._rows_busy.release()

    def _done_building(self):
        with self._rows_cv:
            self._rows_building = False
            self._rows_cv.notify_all()

    def _publish_rows(self, rows, save=False):
        with self._rows_cv:
            self._rows = (time.monotonic(), rows)
            self._rows_cv.notify_all()
        if save:
            self._save_rows(rows)

    def _row_card(self, it, kind=None):
        """A card whose `art` is TMDb's own poster address; each reply swaps it for one through Usher's poster proxy."""
        c = self._card(it, kind)
        if c is not None:
            c["art"] = f"https://image.tmdb.org/t/p/w342{it['poster_path']}" if it.get("poster_path") else None
            # TMDb's genre ids, already in its list answers: Picked for you ranks lower what only matches dislikes.
            c["genre_ids"] = [g for g in it.get("genre_ids") or [] if isinstance(g, int) and not isinstance(g, bool)][:10]
        return c

    def providers(self):
        """The region's main streaming services, in TMDb's own order of prominence."""
        data = self._get("/watch/providers/tv", ttl=24 * 3600, watch_region=self.region)
        out = []
        for p in data.get("results", []):
            prio = (p.get("display_priorities") or {}).get(self.region, p.get("display_priority", 999))
            out.append((prio, p["provider_id"], p["provider_name"], p.get("logo_path")))
        out.sort()
        return out[:8]

    def _build_rows(self):
        card = self._row_card
        jobs = [
            ("trending", "Trending this week", lambda: [card(i) for i in self._get("/trending/all/week")["results"]]),
            ("movies", "Popular movies", lambda: [card(i, "movie") for i in self._get("/movie/popular", region=self.region)["results"]]),
            ("shows", "Popular TV", lambda: [card(i, "show") for i in self._get("/tv/popular")["results"]]),
        ]
        try:
            services = self.providers()
        except ApiError as e:
            log.warning("discover providers: %s", e)
            services = []
        for _prio, pid, name, _logo in services:

            def on_service(pid=pid):
                common = dict(watch_region=self.region, with_watch_providers=str(pid), with_watch_monetization_types="flatrate", sort_by="popularity.desc")
                shows = [card(i, "show") for i in self._get("/discover/tv", **common)["results"][:10]]
                movies = [card(i, "movie") for i in self._get("/discover/movie", **common)["results"][:10]]
                mixed = [x for pair in zip(shows, movies) for x in pair] + shows[len(movies) :] + movies[len(shows) :]
                return mixed[:20]

            jobs.append((f"p{pid}", f"Popular on {name}", on_service))
        jobs = self._personal_jobs() + jobs

        def timed(name, fn):
            started = time.monotonic()
            try:
                return fn()
            finally:
                log.info("discover row %s took %.2fs", name, time.monotonic() - started)

        futures = [(rid, name, self._rows_pool.submit(timed, name, fn)) for rid, name, fn in jobs]
        rows = []
        for rid, name, fut in futures:
            try:
                items = [c for c in fut.result(timeout=60) if c and c["art"]]
            except Exception as e:  # noqa: BLE001  one row TMDb didn't answer for is left out
                log.warning("discover row %s: %s", name, e)
                continue
            if items:
                rows.append({"id": rid, "name": name, "items": items})
        return rows

    def _mark_streaming(self, cards, wait=8.0, quiet=False):
        """Adds `streaming` (service names) to Discover's cards, for its Streaming filter. Each title is looked up once
        (the details are kept a day, shared with search and the sheet). A title TMDb hasn't answered for within `wait`
        seconds gets no `streaming`, which the filter treats as unknown. quiet: the cards are one account's own picks,
        so a failure is logged without naming the title."""
        by = {}
        for c in cards:
            if c.get("tmdb"):
                by.setdefault((c["kind"], c["tmdb"]), []).append(c)
        jobs = [(key, self._rows_pool.submit(self._details, *key)) for key in by]
        deadline = time.monotonic() + wait
        for key, fut in jobs:
            try:
                info = fut.result(timeout=max(0.05, deadline - time.monotonic()))
            except Exception as e:  # noqa: BLE001  the filter treats it as unknown
                if quiet:
                    log.info("discover streaming for one account's picks: %s", e)
                else:
                    log.info("discover streaming for %s %s: %s", key[0], key[1], e)
                continue
            names = [p["name"] for p in self._streams(info)[0]]
            for c in by[key]:
                c["streaming"] = names

    # kept on disk across restarts --------------------------------------------------------------
    def _rows_key(self):
        return {"region": self.region, "language": self.language}

    def _save_rows(self, rows):
        if not self._rows_file:
            return
        try:
            write_private(self._rows_file, json.dumps(dict(self._rows_key(), built=time.time(), rows=rows)))
        except OSError as e:
            log.warning("discover: can't keep rows on disk: %s", e)

    def _load_rows(self):
        if not self._rows_file or not self.enabled:
            return
        try:
            saved = json.loads(self._rows_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("discover: saved rows unreadable: %s", e)
            return
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in self._rows_key().items()):
            return  # another region or language: build afresh
        rows, age = saved.get("rows"), time.time() - float(saved.get("built") or 0)
        if isinstance(rows, list) and rows:
            self._rows = (time.monotonic() - max(0.0, age), rows)  # an old copy is served, then rebuilt behind it

    # recommendations from what the viewing account watched -------------------------------------
    # Seeds come from Jellyfin's watch history for the shared account; only TMDb ids go to TMDb, which Usher
    # already uses. Anything already in Jellyfin, or already watched, is left out.
    def _recs(self, kind, tmdb):
        path = f"/movie/{tmdb}/recommendations" if kind == "movie" else f"/tv/{tmdb}/recommendations"
        return [c for c in (self._row_card(i, kind) for i in self._get(path, ttl=24 * 3600).get("results", [])) if c]

    def _personal_jobs(self):
        jf = self.state.jf
        try:
            watched = jf.watched()[: self.SEEDS]
            owned = jf.library_tmdb()
        except (ApiError, KeyError, TypeError) as e:
            log.warning("discover history: %s", e)
            return []
        if not watched:
            return []
        skip = owned | {(k, t) for k, t, _ in watched}
        fresh = lambda cards: [c for c in cards if (c["kind"], c["tmdb"]) not in skip]  # noqa: E731

        def seed_recs(seed):
            try:
                return fresh(self._recs(seed[0], seed[1]))
            except ApiError:
                return []

        def for_you():
            with ThreadPoolExecutor(max_workers=len(watched)) as ex:  # every seed at once
                all_recs = list(ex.map(seed_recs, watched))
            return self._score_recs(all_recs)

        jobs = [("foryou", "Picked for you", for_you)]
        for kind, tmdb, title in watched[: self.BECAUSE_ROWS]:
            jobs.append((f"because-{kind}-{tmdb}", f"Because you watched {title}", lambda kind=kind, tmdb=tmdb: fresh(self._recs(kind, tmdb))[:20]))
        return jobs

    @staticmethod
    def _score_recs(all_recs, weights=None, halved=None):
        """The best 20 of the seeds' recommendations. all_recs: one list of cards per seed, the most recent seed first.
        weights: how much each seed counts (1 each by default). halved(card): true for a card whose score is halved."""
        score, cards = {}, {}
        for rank, recs in enumerate(all_recs):
            weight = weights[rank] if weights else 1.0
            for pos, c in enumerate(recs[:20]):
                key = (c["kind"], c["tmdb"])
                # Titles recommended by several seeds, and by recent ones, rank higher.
                score[key] = score.get(key, 0) + weight * (1.0 / (1 + rank * 0.3)) * (1.0 / (1 + pos * 0.1))
                cards[key] = c
        if halved:
            for key, c in cards.items():
                if halved(c):
                    score[key] /= 2
        return [cards[k] for k in sorted(score, key=score.get, reverse=True)][:20]
