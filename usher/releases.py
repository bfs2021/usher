"""What a download's release name and queue messages say: whether it names the title, and why it wasn't imported."""

import re
import unicodedata


def _squash(text):
    """Lower case letters and digits only, accents and 'the' dropped: 'The Bear (2022)' -> 'bear'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text.replace("&", " and "))
    text = re.sub(r"['`]", "", text)
    text = re.sub(r"^the\s+", "", text.strip())
    return re.sub(r"[^a-z0-9]+", "", text)


def title_matches(release, item):
    """Whether a release name mentions the show or movie it was grabbed for, by any of its known titles.
    False means it's probably the wrong thing (Sonarr once grabbed Giant Jack for Trash Truck)."""
    rel = _squash(release)
    if not rel:
        return True  # nothing to judge by
    titles = [item.get("title"), item.get("originalTitle"), item.get("sortTitle"), item.get("cleanTitle")]
    titles += [a.get("title") for a in item.get("alternateTitles") or []]
    names = [n for n in (_squash(t) for t in titles if t) if n]
    return not names or any(n in rel for n in names)


def import_reason(recs):
    """Why a finished download hasn't gone into the library, in plain words. Returns (reason, message, can_retry)."""
    text = " ".join(m for r in recs for sm in (r.get("statusMessages") or []) for m in (sm.get("messages") or [])).lower()
    if "free space" in text or "disk space" in text:
        return ("space", "The server is out of room, so it can't be added yet. It finishes by itself once space is freed.", False)
    if "sample" in text:
        return ("sample", "The copy it found was only a preview clip. Try again to look for the full episode.", True)
    if "no files found" in text or "no video files" in text or "rar" in text:
        return ("nofile", "The copy it found didn't contain a video it could use. Try again for a different one.", True)
    if "not an upgrade" in text or "already" in text:
        return ("dupe", "It already has a copy at least this good, so this one isn't needed.", True)
    if "permission" in text or "access" in text and "denied" in text:
        return ("perm", "It couldn't move the file into the library (a permissions problem on the server).", False)
    if "matched" in text or "parse" in text or "unknown" in text:
        return ("match", "It couldn't tell which episode or movie this file is.", True)
    return ("other", "It downloaded but couldn't be added to the library.", True)
