"""The request handler's like and dislike routes: one account's own opinions of titles, set, cleared and listed.

Every route answers for the signed-in account only; nothing here takes an account id from the client, and nothing
is logged: likes and dislikes are that account's own.
"""

import re
from http import HTTPStatus
from urllib.parse import parse_qs

from .store import OPINIONS_MAX

KINDS = ("movie", "show")
CHOICES = {"like": True, "dislike": False, "clear": None}
MAX_TMDB = 2**31 - 1
CURSOR = re.compile(r"(\d{1,12})-(\d{1,12})")
PAGE = 50  # titles in one page of Settings' list, so one opening asks TMDb for at most this many


def parse_opinion(data):
    """(kind, tmdb, liked) from a request body; liked is None to clear. ValueError, with a plain message, otherwise."""
    kind, tmdb, choice = data.get("kind"), data.get("tmdb"), data.get("opinion")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Unknown kind of item.")
    if type(tmdb) is not int or not 0 < tmdb <= MAX_TMDB:  # bool is an int too, and is refused
        raise ValueError("Unknown title.")
    if not isinstance(choice, str) or choice not in CHOICES:
        raise ValueError("Choose like, dislike or clear.")
    return kind, tmdb, CHOICES[choice]


def label(liked):
    return None if liked is None else "like" if liked else "dislike"


class OpinionRoutes:
    def set_opinion(self, s, data):
        try:
            kind, tmdb, liked = parse_opinion(data)
        except ValueError as e:
            return self.err(HTTPStatus.BAD_REQUEST, str(e))
        uid = s["user_id"]
        if liked is None:
            self.app.store.clear_opinion(uid, kind, tmdb)
        elif not self.app.store.set_opinion(uid, kind, tmdb, liked):
            return self.err(HTTPStatus.BAD_REQUEST, f"You can keep up to {OPINIONS_MAX} likes and dislikes. Undo some in Settings → Account first.")
        self.app.discover.opinion_changed(uid)
        return self.send(200, {"kind": kind, "tmdb": tmdb, "opinion": label(liked)})

    def list_opinions(self, s, query=""):
        """One page, newest first, with enough to show a row: title, year and poster from TMDb's details (kept a day).
        A page at a time, so opening the list asks TMDb for at most PAGE titles. after: the `next` of the page before,
        which names its last title, so an Undo meanwhile never shifts the next page."""
        after = parse_qs(query).get("after", [None])[0]
        if after is not None:
            m = CURSOR.fullmatch(after)
            if not m:
                return self.err(HTTPStatus.BAD_REQUEST, "That request didn't make sense.")
            after = (int(m[1]), int(m[2]))
        rows = self.app.store.opinions(s["user_id"], limit=PAGE + 1, after=after)
        more, rows = len(rows) > PAGE, rows[:PAGE]
        about = self.app.discover.describe([(r["kind"], r["tmdb"]) for r in rows])
        items = []
        for r in rows:
            known = about.get((r["kind"], r["tmdb"])) or {}
            items.append(
                {
                    "kind": r["kind"],
                    "tmdb": r["tmdb"],
                    "opinion": label(bool(r["liked"])),
                    "title": known.get("title"),
                    "year": known.get("year"),
                    "art": known.get("art"),
                }
            )
        return self.send(200, {"items": items, "next": f"{rows[-1]['at']}-{rows[-1]['rid']}" if more else None})

    def mark_opinions(self, s, items):
        """Adds `opinion` (like, dislike or None) for the signed-in account to titles going to the details sheet."""
        mine = {(r["kind"], r["tmdb"]): label(bool(r["liked"])) for r in self.app.store.opinions(s["user_id"])}
        for it in items:
            it["opinion"] = mine.get((it.get("kind"), it.get("tmdb")))
        return items
