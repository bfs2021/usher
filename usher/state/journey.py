"""Requests: the journey from "Get it" to "Ready to watch"."""

from datetime import datetime, timezone

from ..actions import season_of
from ..titles import arr_art
from .helpers import couldnt_find_after, iso, local_time, parse_time, research_every


class Journey:
    # requests: the journey from "Get it" to "Ready to watch" ---------------
    STEPS = ["Asked", "Found", "Downloading", "Getting it ready", "Ready to watch"]

    def _requests(self, r, now, downloading):
        if not self.store:
            return []
        movies = {m["id"]: m for m in r["movies"]}
        series = {s["id"]: s for s in r["series"]}
        dls: dict = {}
        for d in downloading:
            dls.setdefault((d["kind"], d["id"]), []).append(d)
        out = []
        for req in self.store.recent_requests():
            kind, arr_id = req["kind"], req["arr_id"]
            age = now.timestamp() - req["created"]
            item = movies.get(arr_id) if kind == "movie" else series.get(arr_id)
            if item is None:
                if age > 300 and (movies if kind == "movie" else series):
                    continue  # removed from the library since
                item = {}
            mine = dls.get((kind, arr_id), [])
            j = {
                "rid": req["id"],
                "kind": kind,
                "id": arr_id,
                "title": req["title"],
                "year": req["year"],
                "art": arr_art(kind, item or arr_id),
                "by": req["display"],
                "asked": iso(datetime.fromtimestamp(req["created"], timezone.utc)),
                "progress": None,
                "eta": None,
                "detail": "",
                "problem": False,
                "stuck": False,
                "key": None,
            }
            st = (item.get("statistics") or {}) if kind == "show" else {}
            season = season_of(req["choice"]) if kind == "show" else None
            if season is not None:  # one season of a show already here: count that season only
                st = next(((x.get("statistics") or {}) for x in item.get("seasons") or [] if x.get("seasonNumber") == season), {})
            have, total = st.get("episodeFileCount", 0), st.get("episodeCount", 0)
            done_files = item.get("hasFile") if kind == "movie" else (total > 0 and have >= total)

            problem = next((d for d in mine if d["state"] == "problem"), None)
            active = [d for d in mine if d["state"] in ("downloading", "waiting")]
            preparing = [d for d in mine if d["state"] == "preparing"]
            if problem:
                waiting_space = problem.get("reason") == "space"
                j.update(
                    step=3 if waiting_space else 2,
                    label="Waiting for space" if waiting_space else "Download problem",
                    detail=problem["message"],
                    problem=True,
                    key=problem["key"],
                    canRetry=problem.get("canRetry"),
                )
            elif active:
                j["keys"] = [d["key"] for d in active]  # so the journey can offer Stop downloading
                live = [d for d in active if d["state"] == "downloading"]
                if live:
                    pct = sum(d["progress"] for d in live) / len(live)
                    etas = [d["eta"] for d in live if d["eta"] is not None]
                    j.update(step=2, label=f"Downloading {int(pct * 100)}%", progress=round(pct, 3), eta=max(etas) if etas else None)
                    if kind == "show":
                        eps = sum(d.get("episodes", 1) for d in active)
                        j["detail"] = f"{have} of {total} ready, {eps} downloading" if have and total else f"{eps} episode{'s' if eps != 1 else ''} on the way"
                    if all(d.get("stalled") for d in live):
                        j.update(label=f"Waiting for someone to share it · {int(pct * 100)}%", eta=None, detail=live[0]["message"])
                else:
                    j.update(step=1, label="Found it, waiting to start", detail="It starts as soon as the downloads ahead of it finish.")
            elif preparing:
                j.update(step=3, label="Getting it ready", progress=1.0, detail="Renaming it and adding it to the library.")
            elif kind == "show" and item and req["choice"] != "new" and total == 0 and not have and self._not_aired(item, season, now):
                first = self._first_airing(item, season)
                what = "The first episode" if season is None else f"Season {season}"
                j.update(
                    step=0,
                    label="Not out yet",
                    detail=(
                        f"{what} {'airs' if season is None else 'starts'} {first.strftime('%-d %b %Y')}. It downloads by itself after that."
                        if first
                        else "It downloads by itself once episodes start airing."
                    ),
                )
            elif done_files or (kind == "show" and have):
                if req["jf_id"]:
                    j.update(step=4, label="Ready to watch")
                    if kind == "show" and total and have < total:
                        j["detail"] = f"{have} of {total} episodes ready. Still looking for the rest."
                else:
                    j.update(
                        step=3, label="Adding to Jellyfin", progress=1.0, detail="It's downloaded. Jellyfin usually picks it up within a couple of minutes."
                    )
            elif kind == "show" and req["choice"] == "new" and total == 0:
                nxt = parse_time(item.get("nextAiring"))
                j.update(
                    step=0,
                    label="Waiting for new episodes",
                    detail=f"The next one airs {nxt.strftime('%-d %b')}." if nxt else "Episodes will download as they air.",
                )
            elif kind == "movie" and item and not item.get("isAvailable"):
                rel = parse_time(item.get("digitalRelease") or item.get("physicalRelease"))
                j.update(
                    step=0,
                    label="Not out yet",
                    detail=f"It can be downloaded from {rel.strftime('%-d %b %Y')}." if rel else "It will download when it's released to watch at home.",
                )
            elif age < 180:
                j.update(step=0, label="Searching…", detail="Checking the search sites for a good copy.")
            else:
                stuck = age > couldnt_find_after()
                last = req["searched"] or req["created"]
                nxt = last + research_every(bool(req["searched"]), stuck)
                when = local_time(nxt)
                j.update(
                    step=0,
                    label="Still looking" if stuck else "Looking for it",
                    stuck=stuck,
                    searching=True,
                    detail=(
                        f"Nothing suitable yet. It searches again once a day; next at {when}."
                        if stuck
                        else f"Nothing suitable yet. It searches again every few hours; next at {when}."
                    ),
                )
            wrong = next((d for d in mine if d.get("wrongTitle")), None)
            if wrong:
                j["wrongTitle"] = wrong["wrongTitle"]
                j["warning"] = (
                    f"The copy downloading is called “{wrong['wrongTitle']}”, which doesn't look like "
                    f"{req['title']}. If it's the wrong thing, stop it and ask again."
                )
            j["steps"] = self.STEPS
            j["done"] = bool(req["done_at"]) or j["step"] == 4
            # Finished once it's first watchable; it then stays on Home for two days (SHOW_DONE_FOR).
            if j["step"] == 4 and not req["done_at"]:
                self.store.run("UPDATE requests SET done_at = ? WHERE id = ?", (int(now.timestamp()), req["id"]))
            out.append(j)
        return out

    @staticmethod
    def _first_airing(item, season):
        if season is not None:
            st = next(((x.get("statistics") or {}) for x in item.get("seasons") or [] if x.get("seasonNumber") == season), {})
            return parse_time(st.get("previousAiring") or st.get("nextAiring"))
        return parse_time(item.get("firstAired") or item.get("nextAiring"))

    def _not_aired(self, item, season, now):
        """Nothing of this show (or season) has aired yet, so there's nothing to look for."""
        first = self._first_airing(item, season)
        if first:
            return first > now
        return (item.get("status") or "").lower() == "upcoming"
