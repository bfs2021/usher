"""Writes a release's notes: that version's section of CHANGELOG.md, as Settings → About → Changelog shows it.

The words are read with the parser the app uses (`usher/changelog.py`), so the release page and the app can't
differ. A version with no section, or a section with no entries (an app change that carried `Changelog: not
needed`), gets one line saying so.

The script runs without the workflow's token and only reads CHANGELOG.md; the Release workflow gives the token to
the one step that creates the release from the file this writes. Nothing from a pull request's text reaches a
shell: the version is checked here, and the notes go to a file that `gh release create --notes-file` reads.
tests/test_release_notes.py has the cases.

Usage: python3 .github/scripts/release_notes.py <version> <output file> (run from the repository root)
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from usher.changelog import parse  # noqa: E402  (the import needs the path above)

VERSION = re.compile(r"\d+(?:\.\d+)*", re.ASCII)
NO_CHANGES = "This release has no changes to notice."


def notes(version: str, changelog_text: str) -> str:
    """The release body for `version`: its changes grouped under the changelog's own headings."""
    if not VERSION.fullmatch(version):
        raise ValueError(f"Not a version number: {version!r}")
    section = next((v for v in parse(changelog_text) if v["version"] == version), None)
    if not section or not section["changes"]:
        return NO_CHANGES + "\n"
    out, kind = [], None
    for change in section["changes"]:
        if change["kind"] != kind:
            kind = change["kind"]
            if kind:
                out += ([""] if out else []) + [f"### {kind}", ""]
        out.append(f"- {change['text']}")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: release_notes.py <version> <output file>", file=sys.stderr)
        return 2
    version, target = argv[1], Path(argv[2])
    try:
        body = notes(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"::error::Can't build the release notes for {version}: {e}")
        return 1
    target.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
