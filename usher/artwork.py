"""Artwork at the size a device shows it. The device asks for a width (?w=); each source makes that size itself: the
media server resizes on request, Sonarr and Radarr keep a small and a large poster, and TMDb has a set of widths.
So no picture is sent at full size, and Usher needs no image library of its own."""

import re
from urllib.parse import parse_qs

from .clients import remote_image

WIDTHS = (120, 160, 240, 320, 480, 640, 960)  # what a device may ask for; anything in between is rounded up
TMDB_WIDTHS = (92, 154, 185, 342, 500, 780)  # TMDb's poster widths; "original" (full size) is never asked for
TMDB_SIZED = re.compile(r"^(https://image\.tmdb\.org/t/p/)w\d+(/[^/?#]+)$")


def asked_width(query: str):
    """The width the device asked for, rounded up to one of WIDTHS (at most the largest), or None for the usual."""
    raw = parse_qs(query).get("w", [""])[0]
    if not raw.isdigit() or len(raw) > 5 or int(raw) <= 0:
        return None
    return next((w for w in WIDTHS if w >= int(raw)), WIDTHS[-1])


def arr_file(num_id, width):
    """Sonarr's and Radarr's own copies are 250 and 500 pixels wide."""
    return f"{num_id}/poster-{250 if width is None or width <= 250 else 500}.jpg"


def tmdb_sized(url, width):
    """A TMDb picture address at the nearest width not below the one asked for; other addresses as they are."""
    m = TMDB_SIZED.match(url or "")
    if not m or width is None:
        return url
    size = next((w for w in TMDB_WIDTHS if w >= width), TMDB_WIDTHS[-1])
    return f"{m.group(1)}w{size}{m.group(2)}"


def picture(app, app_name, num_id, jf_id, jf_kind, query):
    """(status, bytes, content type, Cache-Control) for a poster from Sonarr, Radarr or the media server.

    A versioned URL (?v=, from titles.arr_art and jf_art) names one picture: a new picture gets a new URL, so this
    one can stay on the device for a long time."""
    width = asked_width(query)
    keep = "private, max-age=2592000" if parse_qs(query).get("v") else "private, max-age=86400"
    if jf_id:
        data, ctype = app.jf.image(jf_id, jf_kind or "Primary", width)
        if not data:
            app.state.art_missing(jf_id)  # if the item itself has gone, Home drops its card
    else:
        client = app.sonarr if app_name == "sonarr" else app.radarr
        data, ctype = client.image(arr_file(num_id, width))
        if not data:
            # Sonarr or Radarr has no picture (often a show that hasn't aired yet): use TMDb's, through the same
            # allow-listed fetch as search posters. Cached for a shorter time, so their own picture takes over
            # once they get one.
            url = app.discover.arr_fallback(app_name, num_id)
            data, ctype = remote_image(tmdb_sized(url, width)) if url else (None, None)
            if data:
                return 200, data, ctype, "private, max-age=21600"
    if not data:
        return 404, b"", None, "private, max-age=600"
    return 200, data, ctype, keep


def remote_picture(app, key, query):
    """(status, bytes, content type, Cache-Control) for a search or Discover poster, by its key (titles.remote_art)."""
    url = app.actions.remote_art.get(key)
    data, ctype = remote_image(tmdb_sized(url, asked_width(query))) if url else (None, None)
    if not data:
        return 404, b"", None, "private, max-age=600"
    return 200, data, ctype, "private, max-age=604800"
