"""Every released version and what changed in it, in plain words, for Settings → About → Changelog.

The words come from CHANGELOG.md, which the image carries next to `usher/`; nothing else is stored.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("usher.changelog")

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = re.compile(r"## \[(\d+(?:\.\d+)*)\]")
_SPAN = re.compile(r"(\*\*|`)")


def _plain(text: str) -> str:
    """The screen shows text, not Markdown: drop the bold and code marks and link targets."""
    return _SPAN.sub("", re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)).strip()


def parse(text: str) -> list[dict]:
    """[{"version", "changes": [{"kind", "text"}]}] in file order (newest first). `kind` is the heading (Added, Fixed…)."""
    out, current, kind, bullet = [], None, "", None
    for line in text.splitlines():
        if line.startswith("## "):
            found = HEADING.fullmatch(line.strip())
            current = {"version": found[1], "changes": []} if found else None  # Unreleased and the like are skipped
            if current:
                out.append(current)
            kind, bullet = "", None
        elif current is None:
            continue
        elif line.startswith("### "):
            kind, bullet = line[4:].strip(), None
        elif line.startswith("- "):
            bullet = {"kind": kind, "text": line[2:].strip()}
            current["changes"].append(bullet)
        elif bullet and line.startswith("  ") and line.strip():
            bullet["text"] += " " + line.strip()  # a bullet wrapped onto the next line
    for v in out:
        v["changes"] = [{"kind": c["kind"], "text": _plain(c["text"])} for c in v["changes"] if _plain(c["text"])]
    return out


@lru_cache(maxsize=4)
def _versions(path: Path) -> tuple:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("Can't read the changelog (%s): Settings → About → Changelog is empty", e.strerror or e)
        return ()
    return tuple((v["version"], tuple((c["kind"], c["text"]) for c in v["changes"])) for v in parse(text))


def changelog(path: Path = CHANGELOG) -> list[dict]:
    """Every version in the file, newest first, each with its changes."""
    return [{"version": version, "changes": [{"kind": k, "text": t} for k, t in changes]} for version, changes in _versions(path)]
