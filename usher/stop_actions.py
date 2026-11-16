"""Stopping a download: retry a stuck one, cancel one, or stop looking for a request."""

import logging

from .clients import ApiError
from .problem import Problem

log = logging.getLogger("usher.actions")


def season_of(choice):
    """The season number of a single-season request ("season:3"), or None for a whole-show request."""
    if isinstance(choice, str) and choice.startswith("season:") and choice[7:].isdigit():
        return int(choice[7:])
    return None


class StopActions:
    def retry(self, key: str, user):
        app_name, ids = self.state.queue_ids.get(key, (None, []))
        if not ids:
            raise Problem("That download has already moved on. Check Home again in a moment.")
        app = self.sonarr if app_name == "sonarr" else self.radarr
        for qid in ids:
            # Remove the stuck download, never try that copy again, and look for another one straight away.
            app.send("DELETE", f"queue/{qid}", removeFromClient="true", blocklist="true", skipRedownload="false")
        log.info("%s retried download %s", user["username"], key[:12])
        self.state.refresh_now()
        return {"ok": True}

    def cancel_many(self, keys, user):
        title = ""
        for key in keys:
            title = self.cancel(key, user).get("title") or title
        return {"ok": True, "title": title}

    def cancel(self, key: str, user):
        """Stop a download and don't look for it again: removes it from Transmission (and its partial files),
        and switches off monitoring for just those episodes, or that movie, so Sonarr or Radarr won't grab
        another copy. Anything already downloaded stays. A request that now has nothing coming is dropped."""
        app_name, ids = self.state.queue_ids.get(key, (None, []))
        if not ids:
            raise Problem("That download has already moved on. Check Home again in a moment.")
        app = self.sonarr if app_name == "sonarr" else self.radarr
        try:
            recs = [r for r in app.get("queue", pageSize=200)["records"] if r.get("id") in ids]
        except ApiError:
            recs = []
        # Switch monitoring off first, so that even if a removal below fails, Sonarr or Radarr won't fetch it again.
        if app_name == "sonarr":
            eps = sorted({r["episodeId"] for r in recs if r.get("episodeId")})
            if eps:
                self.sonarr.send("PUT", "episode/monitor", {"episodeIds": eps, "monitored": False})
        else:
            movie_id = next((r.get("movieId") for r in recs if r.get("movieId")), None)
            m = self.radarr.get(f"movie/{movie_id}") if movie_id else None
            if m and m.get("monitored"):
                m["monitored"] = False
                self.radarr.send("PUT", f"movie/{movie_id}", m)
        # A season pack is one download with a queue entry per episode. Removing the first entry removes the
        # download, and Sonarr drops the other entries with it, so they answer "not found": that's fine.
        removed = 0
        for qid in ids:
            try:
                app.send("DELETE", f"queue/{qid}", removeFromClient="true", blocklist="false", skipRedownload="true")
                removed += 1
            except ApiError as e:
                log.info("stop downloading: queue %s already gone (%s)", qid, e)
        if not removed:
            raise Problem("Couldn't stop that download. Try again in a moment.")
        title = ""
        if app_name == "sonarr":
            series_id = next((r.get("seriesId") for r in recs if r.get("seriesId")), None)
            if series_id:
                s = self.sonarr.get(f"series/{series_id}")
                title = s.get("title", "")
                if not ((s.get("statistics") or {}).get("episodeFileCount")):
                    self.store.forget_item("show", series_id)  # nothing here and nothing coming
        elif m:
            title = m.get("title", "")
            if not m.get("hasFile"):
                self.store.forget_item("movie", movie_id)
        self.state.drop_cache()
        log.info("%s stopped download %s (%s)", user["username"], key[:12], title or app_name)
        if app_name == "sonarr" and series_id:
            self._changed("changed", "show", s)
        elif app_name != "sonarr" and m:
            self._changed("changed", "movie", m)
        self.state.refresh_now()
        return {"ok": True, "title": title}

    def stop_looking(self, rid: int, user):
        """Withdraw a request that hasn't downloaded anything yet. Nothing on disk is touched: a movie or show
        with no files is taken out of Radarr or Sonarr; one season of a show already here is just switched off
        (with its missing episodes), so the rest of the show stays. The request leaves Home."""
        req = self.store.one("SELECT * FROM requests WHERE id = ?", (rid,))
        if req is None:
            raise Problem("That request has already gone. Check Home again in a moment.")
        kind, arr_id, title = req["kind"], req["arr_id"], req["title"]
        season = season_of(req["choice"]) if kind == "show" else None
        app = self.sonarr if kind == "show" else self.radarr
        path = f"series/{arr_id}" if kind == "show" else f"movie/{arr_id}"
        try:
            item = app.get(path)
        except ApiError:
            item = None  # already gone from Sonarr or Radarr: just forget the request
        if item is not None:
            field = "seriesId" if kind == "show" else "movieId"
            try:
                queue = app.get("queue", pageSize=200)["records"]
            except ApiError:
                queue = []
            for rec in queue:
                if rec.get(field) == arr_id and (season is None or rec.get("seasonNumber") == season):
                    app.send(
                        "DELETE", f"queue/{rec['id']}", removeFromClient="true", blocklist="false", **({"skipRedownload": "true"} if kind == "show" else {})
                    )
            if kind == "movie":
                if item.get("hasFile"):
                    item["monitored"] = False
                    app.send("PUT", path, item)
                else:
                    app.send("DELETE", path, deleteFiles="false", addImportExclusion="false")
            elif season is None and not (item.get("statistics") or {}).get("episodeFileCount"):
                app.send("DELETE", path, deleteFiles="false", addImportListExclusion="false")
            else:
                # Keep what's here; stop wanting the rest (the asked-for season, or the whole show).
                eps = [
                    e["id"]
                    for e in app.get("episode", seriesId=arr_id)
                    if not e.get("hasFile") and e.get("monitored") and (season is None or e.get("seasonNumber") == season)
                ]
                for x in item.get("seasons") or []:
                    if season is None or x.get("seasonNumber") == season:
                        x["monitored"] = False
                if season is None:
                    item["monitored"] = False
                app.send("PUT", path, item)
                if eps:
                    app.send("PUT", "episode/monitor", {"episodeIds": eps, "monitored": False})
        if season is None:
            self.store.forget_item(kind, arr_id)
        else:
            self.store.run("DELETE FROM requests WHERE id = ?", (rid,))
        self.state.drop_cache()
        log.info("%s stopped looking for %s %s", user["username"], kind, title)
        if item is not None:
            self._changed("changed", kind, item)
        self.state.refresh_now()
        return {"ok": True, "title": title}
