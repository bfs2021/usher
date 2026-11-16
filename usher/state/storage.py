"""What fills the library disk, split the way Status → Storage draws it: Movies, TV, family libraries, downloads,
recycle bin and everything else."""


def _under(path, folder):
    folder = folder.rstrip("/")
    return bool(folder) and (path or "").startswith(folder + "/")


def _size(item):
    stats = item.get("statistics") or {}
    return item.get("sizeOnDisk") or stats.get("sizeOnDisk") or 0


def storage_split(settings, raw, used):
    """Bytes per category, in drawing order, summing to at most `used` (the disk's own used space).

    Files the downloader still shares with the library are hard links to the same data, so downloads are cut back
    to what is left once the library and recycle bin are counted rather than counting a file twice."""
    sums = {"movies": 0, "tv": 0, "family": 0}
    for items, key, family in ((raw["movies"], "movies", settings.family_movie_folder), (raw["series"], "tv", settings.family_tv_folder)):
        for item in items or []:
            sums["family" if _under(item.get("path"), family) else key] += _size(item)
    torrents = (raw["torrents"] or {}).values()
    downloads = sum(max(0, (t.get("sizeWhenDone") or 0) - (t.get("leftUntilDone") or 0)) for t in torrents)
    recycle = ((raw.get("recycle") or {}).get("bytes")) or 0
    sums["recycle"] = recycle
    sums["downloads"] = downloads
    # Cut back in this order if the parts add up to more than the disk holds.
    room = max(0, used)
    out = {}
    for key in ("movies", "tv", "family", "recycle", "downloads"):
        out[key] = min(sums[key], room)
        room -= out[key]
    out["other"] = room
    return [{"key": k, "bytes": int(v)} for k, v in out.items()]
