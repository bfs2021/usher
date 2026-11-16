"""Each account's own Picked for you, and its dislikes kept out of Discover (a mixin of Discover).

Seeds are the account's likes, newest first, then its requests still in the library, never a disliked title, at most
ten. Each seed's TMDb recommendations are the same cached lookups the shared rows use (kept a day, shared across
accounts), and only TMDb ids go to TMDb. An account with fewer than three seeds sees the shared Picked for you, from
the viewing account's watch history. Each account's row is kept in memory only, never in discover.json, and nothing
here logs a title: likes and dislikes are that account's own.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .cache import TTLCache
from .clients import ApiError

log = logging.getLogger("usher.discover")
MIN_SEEDS = 3
LIKED, REQUESTED = 2.0, 1.0  # a like counts twice as much as a request
FIND_TTL = 24 * 3600  # as poster_for's /find lookup, so the two share one cached answer


def gone(e):
    """TMDb answered that it has no such title, as opposed to not answering (timeout, 5xx, 429, no connection)."""
    return e.status == 404


class Picks:
    """Mixed into Discover, which supplies `_recs`, `_get`, `_card`, `_details`, `_score_recs`,
    `_mark_streaming`, the shared rows and `actions.store`."""

    PICKS_WAIT = 4.0  # the first opening for an account waits this long at most for its row, then gets the shared one
    PICKS_STREAMING_WAIT = 30
    GENRE_TITLES = 40  # the newest likes and dislikes, each, whose genres steer the ranking

    def _picks_init(self):
        # (account id, when the account was made) -> its cards, or None: too few seeds for its own row. The time is in
        # the key because SQLite may give a removed account's id to the next one made.
        self._picks = TTLCache(self.ROWS_TTL, 200)
        self._picks_lock = threading.Lock()
        self._picks_jobs = {}  # the same key -> {"done": Event, "again": bool, "changed": bool} while its row is being built

    # serving ----------------------------------------------------------------------------------
    def _rows_for(self, uid, rows):
        """The shared rows as one account sees them: its own Picked for you, and none of its dislikes. New lists; the
        shared rows are left as they are."""
        disliked = {(o["kind"], o["tmdb"]) for o in self.actions.store.opinions(uid) if not o["liked"]}
        mine = self._picks_for(uid)
        out = [dict(row) for row in rows]
        if mine:
            own = {"id": "foryou", "name": "Picked for you", "items": mine}
            at = next((i for i, row in enumerate(out) if row["id"] == "foryou"), None)
            if at is None:
                out.insert(0, own)
            else:
                out[at] = own
        for row in out:
            row["items"] = [c for c in row["items"] if (c["kind"], c["tmdb"]) not in disliked and c.get("art")]
        return [row for row in out if row["items"]]

    def _picks_key(self, uid):
        user = self.actions.store.user_by_id(uid)
        return (uid, user["created"]) if user else None

    def _picks_for(self, uid):
        key = self._picks_key(uid)
        if key is None:
            return None
        hit = self._picks.peek(key)
        with self._picks_lock:
            job = self._picks_jobs.get(key)
        if hit is None or (job and job["changed"]):
            # The first opening for this account since start-up, or the one just after a like or dislike (the app asks
            # again at once), waits a moment for its row.
            (job["done"] if job else self._start_picks(key)).wait(self.PICKS_WAIT)
            hit = self._picks.peek(key)
        elif hit[1] >= self.ROWS_TTL:
            self._start_picks(key)  # the old row is served meanwhile
        return hit[0] if hit else None

    def opinion_changed(self, uid):
        """A like or dislike changed: that account's row is built again in the background, without waiting for the hour."""
        key = self._picks_key(uid)
        if self.enabled and key:
            self._start_picks(key, changed=True)

    # building ---------------------------------------------------------------------------------
    def _start_picks(self, key, changed=False):
        """Builds one account's row in the background. Returns an Event set once the row is ready. changed: a like or
        dislike changed, so the build runs once more if one is under way, and the next opening waits for it."""
        with self._picks_lock:
            job = self._picks_jobs.get(key)
            if job:
                if changed:  # a stale row asked for meanwhile needs nothing more: the build under way refreshes it
                    job["again"] = job["changed"] = True
                    job["done"].clear()  # whoever waits now waits for the build that includes the latest change
                return job["done"]
            job = self._picks_jobs[key] = {"done": threading.Event(), "again": False, "changed": changed}
        threading.Thread(target=self._run_picks, args=(key, job), name="discover-picks", daemon=True).start()
        return job["done"]

    def _run_picks(self, key, job):
        try:
            while True:
                try:
                    self._picks.set(key, self._build_picks(key[0]))
                except Exception as e:  # noqa: BLE001  its last row, or the shared one, is served until the next build
                    # Nothing is kept, so a good row isn't replaced by one built without some of TMDb's answers, and
                    # the next opening tries again.
                    log.warning("discover: one account's Picked for you: %s", e)
                with self._picks_lock:
                    if not job["again"]:  # done: later openings find no job, so none of them waits
                        del self._picks_jobs[key]
                        break
                    job["again"] = False  # a like or dislike came in meanwhile: build again before anything else
        finally:
            job["done"].set()
            with self._picks_lock:
                if self._picks_jobs.get(key) is job:
                    del self._picks_jobs[key]
        had = self._picks.peek(key)  # the row being served: this build's, or an earlier one's if this one failed
        if had and had[0]:  # after the row is served, so a new like never waits for this
            self._mark_streaming(had[0], wait=self.PICKS_STREAMING_WAIT, quiet=True)

    def _seeds(self, uid):
        """(kind, tmdb, weight) of up to SEEDS titles: likes newest first, then requests still in the library."""
        store = self.actions.store
        opinions = store.opinions(uid)
        seen = {(o["kind"], o["tmdb"]) for o in opinions if not o["liked"]}  # a dislike is never a seed
        seeds = []

        def add(kind, tmdb, weight):
            if tmdb and (kind, tmdb) not in seen and len(seeds) < self.SEEDS:
                seen.add((kind, tmdb))
                seeds.append((kind, tmdb, weight))

        for o in opinions:
            if o["liked"]:
                add(o["kind"], o["tmdb"], LIKED)
        for r in store.requested_by(uid):
            if len(seeds) >= self.SEEDS:
                break
            tmdb = r["ext_id"] if r["kind"] == "movie" else self._show_tmdb(r["arr_id"], r["ext_id"])
            add(r["kind"], tmdb, REQUESTED)
        return seeds

    def _show_tmdb(self, arr_id, tvdb):
        """A requested show's TMDb id: Sonarr's own `tmdbId`, else TMDb's /find by TheTVDB id (cached, as for posters)."""
        series = next((x for x in self.state._raw.get("series", []) if x.get("id") == arr_id), None)
        if series and isinstance(series.get("tmdbId"), int) and series["tmdbId"] > 0:
            return series["tmdbId"]
        if type(tvdb) is not int or tvdb <= 0:
            return None  # no TheTVDB id to look up by
        try:
            found = self._get(f"/find/{tvdb}", ttl=FIND_TTL, external_source="tvdb_id")
        except ApiError as e:
            if gone(e):
                return None
            raise  # TMDb didn't answer: the build keeps the last row rather than one short of this seed
        hit = (found.get("tv_results") or [None])[0] or {}
        return hit.get("id") if isinstance(hit.get("id"), int) else None

    def _build_picks(self, uid):
        """One account's Picked for you; None when it has fewer than MIN_SEEDS seeds. Raises ApiError when TMDb didn't
        answer for a seed or a genre, so a row short of some answers is never kept. A title TMDb doesn't have (it
        answers 404) is skipped instead, or one such like would stop the account's row for good."""
        seeds = self._seeds(uid)
        if len(seeds) < MIN_SEEDS:
            return None
        opinions = self.actions.store.opinions(uid)
        skip = {(o["kind"], o["tmdb"]) for o in opinions} | {(k, t) for k, t, _ in seeds} | self._library_keys()
        disliked = [(o["kind"], o["tmdb"]) for o in opinions if not o["liked"]][: self.GENRE_TITLES]
        # The likes' genres only matter against some dislikes': without one there is no genre lookup at all.
        liked = [(o["kind"], o["tmdb"]) for o in opinions if o["liked"]][: self.GENRE_TITLES] if disliked else []

        def seed_recs(seed):
            try:
                return [c for c in self._recs(seed[0], seed[1]) if (c["kind"], c["tmdb"]) not in skip]
            except ApiError as e:
                if gone(e):
                    return []  # TMDb has no such title: it can't be a seed, now or on the next build
                raise  # TMDb didn't answer: the build keeps the last row

        with ThreadPoolExecutor(max_workers=len(seeds)) as ex:  # every seed at once
            all_recs = list(ex.map(seed_recs, seeds))
            genres = dict(zip(disliked + liked, ex.map(self._genres_of, disliked + liked)))
        down = set().union(set(), *(genres[k] for k in disliked)) - set().union(set(), *(genres[k] for k in liked))

        def halved(c):  # every one of its genres is only among the dislikes' genres
            found = set(c.get("genre_ids") or [])
            return bool(found) and found <= down

        return self._score_recs(all_recs, [w for *_, w in seeds], halved if down else None)

    def _genres_of(self, key):
        """A title's TMDb genre ids, from its details: the sheet's own lookup, kept a day and shared. Asked for in the
        background build only, never while a page waits."""
        try:
            info = self._details(*key)
        except ApiError as e:
            if gone(e):
                return set()  # TMDb has no such title: it has no genres to count
            raise
        return {g.get("id") for g in info.get("genres") or [] if isinstance(g, dict) and isinstance(g.get("id"), int)}

    def _library_keys(self):
        """(kind, TMDb id) of everything Sonarr and Radarr already have, from the last poll."""
        raw = self.state._raw
        return {("movie", m.get("tmdbId")) for m in raw.get("movies", [])} | {("show", x.get("tmdbId")) for x in raw.get("series", [])}

    # Settings → Account → Likes and dislikes ------------------------------------------------------
    def describe(self, keys, wait=6.0):
        """(kind, tmdb) -> {title, year, art} from TMDb's details (kept a day, shared with the sheet). A title TMDb
        hasn't answered for within `wait` seconds is left out; the list shows it without its name."""
        if not self.enabled or not keys:
            return {}
        jobs = [(key, self._stream_pool.submit(self._details, *key)) for key in keys]
        deadline = time.monotonic() + wait
        out = {}
        for key, fut in jobs:
            try:
                info = fut.result(timeout=max(0.05, deadline - time.monotonic()))
            except Exception as e:  # noqa: BLE001  the row shows without its name
                log.info("likes and dislikes: a title's details didn't arrive: %s", e)
                continue
            c = self._card(info, key[0]) or {}
            out[key] = {"title": c.get("title") or None, "year": c.get("year"), "art": c.get("art")}
        return out
