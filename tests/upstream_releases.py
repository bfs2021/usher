"""Opens one `upstream-release` issue for each new release of an integration that the upstream check sees.

    python3 -m tests.upstream_releases results.json

Run by the Upstream check workflow on the latest images, after the contract check (GH_TOKEN and GH_REPO in the
environment). The versions come from results.json; the issues themselves are the record of what was seen before,
found by their label and their title, `Support <App> <version>`, so a rerun never opens a second one.

- A version newer than every earlier issue for that app opens `Support <App> <version>`, labelled enhancement, P1 and
  upstream-release: the previous and new version, the release notes and whether this run's calls passed on it.
- An open issue whose version passes every call is commented as verified, with its row for README's support matrix,
  and closed.
- An open issue for an older version of the same app is closed as superseded, so at most one is open per app.
- A version older than one already seen (an older image chosen by hand) is left alone.
"""

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from tests.upstream_report import APPS, matrix_row

LABEL = "upstream-release"
LABELS = ["enhancement", "P1", LABEL]
TITLE = re.compile(rf"^Support ({'|'.join(APPS)}) (\S+)$")
# What the integrations report is untrusted. Stable releases are digits and dots only: a pre-release suffix would
# compare as newer than its own stable release and hide it for good.
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,4}")
RELEASE_NOTES = {
    "Jellyfin": "https://github.com/jellyfin/jellyfin/releases/tag/v{}",
    "Sonarr": "https://github.com/Sonarr/Sonarr/releases/tag/v{}",
    "Radarr": "https://github.com/Radarr/Radarr/releases/tag/v{}",
    "Prowlarr": "https://github.com/Prowlarr/Prowlarr/releases/tag/v{}",
    "Transmission": "https://github.com/transmission/transmission/releases/tag/{}",
}


def version_key(version):
    return tuple(int(n) for n in re.findall(r"[0-9]+", version))


def title(app, version):
    return f"Support {app} {version}"


def seen(issues):
    """The release issues already filed, by app: (version, issue) pairs, oldest version first."""
    found = {}
    for issue in issues:
        if "pull_request" in issue or LABEL not in {label["name"] for label in issue.get("labels", [])}:
            continue
        match = TITLE.match(issue["title"])
        if match:
            found.setdefault(match.group(1), []).append((match.group(2), issue))
    return {app: sorted(pairs, key=lambda pair: version_key(pair[0])) for app, pairs in found.items()}


def new_body(app, version, previous, passed, upstream_issue, run_url):
    lines = [
        f"{app} {version} is out: the weekly upstream check started it for the first time.",
        "",
        f"- Previous version: {previous or 'none recorded'}",
        f"- New version: {version}",
        f"- Release notes: {RELEASE_NOTES[app].format(version)}",
    ]
    if passed:
        lines.append("- Check when opened: every call Usher makes passes.")
    else:
        where = f", see #{upstream_issue}" if upstream_issue else ""
        lines.append(f"- Check when opened: a call Usher makes fails on it{where}.")
    if run_url:
        lines.append(f"- Run: {run_url}")
    lines += [
        "",
        "Expected: Usher works with this version. The issue closes by itself once every call passes on it, and closes "
        "as superseded if a newer version comes out first. Changes the check doesn't cover yet are confirmed by hand.",
    ]
    return "\n".join(lines) + "\n"


def verified_note(app, version, run_url, today):
    lines = [f"Verified: every call Usher makes passes on {app} {version}{f' ({run_url})' if run_url else ''}.", ""]
    lines += ["The row for the support matrix in README.md:", "", "```", matrix_row(app, version, today), "```", ""]
    lines += ["`python3 -m tests.upstream_report matrix results.json` writes it, from this run's `upstream-responses-latest` artifact."]
    return "\n".join(lines) + "\n"


def sync(api, results, run_url="", today=None):
    """Opens, verifies and supersedes the release issues for the versions in `results`. Returns what it did."""
    today = today or datetime.date.today()
    failed = {f["app"] for f in results.get("failures", [])}
    filed = seen(api.release_issues())
    upstream_issue = api.open_upstream_issue() if failed else None
    done = []
    for app in APPS:
        version = results.get("versions", {}).get(app)
        if not version:
            continue
        if not (isinstance(version, str) and VERSION.fullmatch(version)):
            done.append(f"{app}: skipped, not a stable version number")
            continue
        passed = app not in failed
        earlier = filed.get(app, [])
        same = next((issue for v, issue in earlier if v == version), None)
        if same is None and earlier and version_key(earlier[-1][0]) >= version_key(version):
            continue  # an older image than one already seen
        if same is None:
            older = [v for v, _ in earlier if version_key(v) < version_key(version)]
            number = api.create(title(app, version), new_body(app, version, older[-1] if older else None, passed, upstream_issue, run_url), LABELS)
            done.append(f"{app} {version}: opened #{number}")
        else:
            number = same["number"]
        # On every run, not only after opening, so a run stopped halfway still leaves one open issue per app.
        for v, issue in earlier:
            if issue["state"] == "open" and version_key(v) < version_key(version):
                api.comment(issue["number"], f"Superseded by #{number}: {app} {version} is out.\n")
                api.close(issue["number"], "not_planned")
                done.append(f"{app} {v}: closed #{issue['number']} as superseded")
        if same is not None and same["state"] != "open":
            continue
        if passed:
            api.comment(number, verified_note(app, version, run_url, today))
            api.close(number, "completed")
            done.append(f"{app} {version}: verified, closed #{number}")
    return done


class GitHub:
    """The issues of one repository through GitHub's REST API."""

    def __init__(self, repo, token, base="https://api.github.com"):
        self.root = f"{base.rstrip('/')}/repos/{repo}/"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "usher-upstream-check",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _send(self, url, method="GET", data=None):
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(url, data=body, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response), response.headers.get("Link", "")
        except urllib.error.HTTPError as error:
            path = url.removeprefix(self.root).split("?")[0]
            try:
                message = str(json.load(error).get("message", ""))[:200]
            except (ValueError, AttributeError):
                message = ""
            # GitHub's own message says which field it rejected; the request's headers, with the token, never.
            raise RuntimeError(f"GitHub answered {error.code} for {method} {path}: {message}".rstrip(": ")) from None

    def _pages(self, path):
        url, items = self.root + path + "&per_page=100", []
        while url:
            page, links = self._send(url)
            items += page
            found = re.search(r'<([^>]+)>;\s*rel="next"', links)
            url = found.group(1) if found else None
        return items

    def release_issues(self):
        return self._pages(f"issues?labels={LABEL}&state=all")

    def open_upstream_issue(self):
        page, _ = self._send(self.root + "issues?labels=upstream&state=open&per_page=1")
        return page[0]["number"] if page else None

    def create(self, title, body, labels):
        return self._send(self.root + "issues", "POST", {"title": title, "body": body, "labels": labels})[0]["number"]

    def comment(self, number, body):
        self._send(self.root + f"issues/{number}/comments", "POST", {"body": body})

    def close(self, number, reason):
        self._send(self.root + f"issues/{number}", "PATCH", {"state": "closed", "state_reason": reason})


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    results = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    env = os.environ
    api = GitHub(env["GH_REPO"], env["GH_TOKEN"], env.get("GITHUB_API_URL", "https://api.github.com"))
    run_url = f"{env['GITHUB_SERVER_URL']}/{env['GH_REPO']}/actions/runs/{env['GITHUB_RUN_ID']}" if env.get("GITHUB_RUN_ID") else ""
    for line in sync(api, results, run_url):
        print(line)


if __name__ == "__main__":
    main(sys.argv)
