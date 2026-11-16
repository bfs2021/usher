"""Turns the upstream check's results into text: the `upstream` issue and README's support matrix.

python3 -m tests.upstream_report issue  results.json   # the issue body, on stdout
python3 -m tests.upstream_report title  results.json   # the issue title
python3 -m tests.upstream_report matrix results.json   # rewrite the matrix in README.md
python3 -m tests.upstream_report matrix results.json --stdout   # print it instead
"""

import datetime
import json
import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- support-matrix:start -->", "<!-- support-matrix:end -->"
APPS = ("Jellyfin", "Sonarr", "Radarr", "Prowlarr", "Transmission")


def issue_title(failures):
    apps = sorted({f["app"] for f in failures})
    return f"Upstream check failing: {', '.join(apps)}"


def issue_body(results):
    """What broke, for a reader who has never seen the run: app, version, the call and what came back."""
    lines = ["The scheduled check against the latest releases of the integrations found changes Usher doesn't handle.", ""]
    lines += ["| App | Version | Call | What happened |", "| --- | --- | --- | --- |"]
    for f in results["failures"]:
        version = results["versions"].get(f["app"]) or "unknown"
        error = f["error"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {f['app']} | {version} | `{f['call']}` | {error} |")
    lines += ["", f"Images checked: {', '.join(f'{a} {v}' for a, v in sorted(results['versions'].items()))}.", ""]
    lines += ["Expected: every call passes, and this issue closes by itself on the next green run."]
    return "\n".join(lines) + "\n"


def matrix(results, today=None):
    """The README table: current and previous major of each app, and the version the last green run saw."""
    failed = {f["app"] for f in results["failures"]}
    rows = ["| App | Supported | Last green run |", "| --- | --- | --- |"]
    for app in APPS:
        version = results["versions"].get(app)
        rows.append(matrix_row(app, version if app not in failed else None, today))
    return "\n".join(rows)


def matrix_row(app, version, today=None):
    """One app's row of the README table; no version means no green run yet."""
    day = (today or datetime.date.today()).isoformat()
    return f"| {app} | current and previous major version | {f'{version} ({day})' if version else 'not yet'} |"


def write_matrix(results, readme=README, today=None):
    text = Path(readme).read_text(encoding="utf-8")
    block = f"{START}\n{matrix(results, today)}\n{END}"
    new, count = re.subn(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    if count != 1:
        raise SystemExit("README.md has no support matrix markers")
    Path(readme).write_text(new, encoding="utf-8")


def main(argv):
    if len(argv) not in (3, 4) or argv[1] not in ("issue", "title", "matrix"):
        raise SystemExit(__doc__)
    results = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    if argv[1] == "issue":
        sys.stdout.write(issue_body(results))
    elif argv[1] == "title":
        print(issue_title(results["failures"]))
    elif argv[3:] == ["--stdout"]:
        print(matrix(results))
    else:
        write_matrix(results)


if __name__ == "__main__":
    main(sys.argv)
