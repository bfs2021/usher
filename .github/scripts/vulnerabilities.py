"""Turn the weekly vulnerability scans into the text of the `vulnerabilities` issue.

Reads pip-audit's JSON report (requirements.txt) and Grype's JSON report (the built image), keeps the known
vulnerabilities that have a fix available, and drops those accepted on purpose in
.github/accepted-vulnerabilities.toml until their expiry date. An expired entry no longer applies and is named in
the issue, so it is renewed with a new reason or removed. Prints the issue body, or nothing when all is clear; an
invalid accepted-findings file stops with the reason.
Usage: vulnerabilities.py pip-audit.json grype.json
"""

import datetime
import json
import re
import sys
import tomllib
from pathlib import Path

ACCEPTED = Path(__file__).resolve().parent.parent / "accepted-vulnerabilities.toml"
ACCEPTED_NAME = ".github/accepted-vulnerabilities.toml"
FIELDS = ("id", "reason", "expires")
# CVE-…, GHSA-…, PYSEC-…, DSA-… and the other advisory databases pip-audit and Grype report from.
ADVISORY = re.compile(r"[A-Z][A-Z0-9]*-[A-Za-z0-9][A-Za-z0-9.:-]*")


def parse(text):
    return tomllib.loads(text)


def problems(data):
    """What is wrong with an accepted-findings file, as readable lines; empty when it is valid."""
    found = [f"unknown table or key: {key}" for key in data if key != "accepted"]
    entries = data.get("accepted", [])
    if not isinstance(entries, list):
        return found + ["accepted must be a list of [[accepted]] entries"]
    seen = set()
    for number, entry in enumerate(entries, 1):
        where = f"entry {number}"
        if not isinstance(entry, dict):
            found.append(f"{where}: not a table")
            continue
        found += [f"{where}: unknown field {key}" for key in entry if key not in FIELDS]
        found += [f"{where}: missing {key}" for key in FIELDS if key not in entry]
        advisory = entry.get("id")
        if "id" in entry and not (isinstance(advisory, str) and ADVISORY.fullmatch(advisory)):
            found.append(f"{where}: id must be an advisory ID such as CVE-2030-0001 or GHSA-xxxx-xxxx-xxxx")
        elif advisory in seen:
            found.append(f"{where}: {advisory} is listed twice")
        else:
            seen.add(advisory)
        if "reason" in entry and not (isinstance(entry["reason"], str) and entry["reason"].strip()):
            found.append(f"{where}: reason must say why the finding is accepted")
        expires = entry.get("expires")
        # A TOML date without a time; datetime is a subclass of date, so it is ruled out first.
        if "expires" in entry and (isinstance(expires, datetime.datetime) or not isinstance(expires, datetime.date)):
            found.append(f"{where}: expires must be a date such as 2030-12-31, without quotes or a time")
    return found


def expired(data, today):
    """Accepted entries whose expiry date has passed; an entry still holds on its expiry day."""
    return [entry for entry in data.get("accepted", []) if entry["expires"] < today]


def findings(pip_audit, grype):
    """Every known vulnerability with a fix available, from both reports, as plain dictionaries."""
    found = []
    for dependency in pip_audit.get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            if not vuln.get("fix_versions"):
                continue
            found.append(
                {
                    "source": "requirements.txt",
                    "package": dependency["name"],
                    "version": dependency.get("version", ""),
                    "id": vuln["id"],
                    "aliases": set(vuln.get("aliases", [])),
                    "fixed_in": ", ".join(vuln["fix_versions"]),
                }
            )
    for match in grype.get("matches", []):
        vuln = match["vulnerability"]
        fix = vuln.get("fix") or {}
        if fix.get("state") != "fixed" or not fix.get("versions"):
            continue
        artifact = match["artifact"]
        found.append(
            {
                "source": "Image",
                "package": artifact["name"],
                "version": artifact.get("version", ""),
                "id": vuln["id"],
                "aliases": {related["id"] for related in match.get("relatedVulnerabilities", [])},
                "fixed_in": ", ".join(fix["versions"]),
            }
        )
    return found


def unaccepted(found, data, today):
    """The findings that no current accepted entry covers, by advisory ID or any of its aliases."""
    accepted = {entry["id"] for entry in data.get("accepted", []) if entry["expires"] >= today}
    return [f for f in found if not ({f["id"]} | f["aliases"]) & accepted]


def _cell(text):
    return str(text).replace("|", "/").replace("\n", " ")


def report(pip_audit, grype, data, today):
    """The issue body, or an empty string when nothing needs a person."""
    wrong = problems(data)
    if wrong:
        raise SystemExit(f"{ACCEPTED_NAME} is not valid:\n" + "\n".join(wrong))
    left = sorted(unaccepted(findings(pip_audit, grype), data, today), key=lambda f: (f["source"], f["package"], f["id"]))
    stale = expired(data, today)
    if not left and not stale:
        return ""
    lines = ["The weekly checks found known vulnerabilities that have a fix available.", ""]
    if left:
        lines += ["| Where | Package | Version | Advisory | Fixed in |", "| --- | --- | --- | --- | --- |"]
        for f in left:
            lines.append(f"| {f['source']} | {_cell(f['package'])} | {_cell(f['version'])} | {_cell(f['id'])} | {_cell(f['fixed_in'])} |")
        lines.append("")
    if stale:
        lines += [f"These accepted findings in `{ACCEPTED_NAME}` have expired and no longer apply:", ""]
        lines += [f"- {_cell(entry['id'])}, expired {entry['expires'].isoformat()}: {_cell(entry['reason'])}" for entry in stale]
        lines.append("")
    lines += [
        "Update the package or the base image to a fixed version. A finding that doesn't affect Usher can be "
        f"accepted in `{ACCEPTED_NAME}` with a reason and an expiry date; an expired entry is renewed or removed.",
        "",
        "Expected: no finding is left, and this issue closes by itself on the next green run.",
    ]
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    pip_audit, grype = (json.loads(Path(p).read_text(encoding="utf-8")) for p in argv[1:])
    sys.stdout.write(report(pip_audit, grype, parse(ACCEPTED.read_text(encoding="utf-8")), datetime.date.today()))


if __name__ == "__main__":
    main(sys.argv)
