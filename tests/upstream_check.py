"""Contract check against real, running copies of the integrations: the calls Usher makes and the fields it reads.

    python3 -m tests.upstream_check OUT_DIR

Addresses and keys come from environment variables of this script's own (SONARR_URL, SONARR_API_KEY, ...);
the server reads its addresses from usher.toml instead.
Writes OUT_DIR/results.json (versions and failures) and, in OUT_DIR/responses/, every real answer, which is
what the fixtures in tests/mock_apps.py are refreshed from. Exits 1 when a call fails. Run by the Upstream
check workflow; the same checks run against the mock apps in tests/test_upstream_check.py."""

import json
import os
import re
import sys
from pathlib import Path

from usher.clients import ApiError, Arr, Jellyfin, Transmission

LOOKUP = {"Sonarr": ("series/lookup", "tvdb:81189"), "Radarr": ("movie/lookup", "tmdb:603")}


class Run:
    """Runs named calls, keeps what they answered and what failed, and never lets one failure hide the next."""

    def __init__(self, out, secrets):
        self.out, self.secrets = Path(out), [s for s in secrets if s]
        self.versions, self.failures = {}, []
        (self.out / "responses").mkdir(parents=True, exist_ok=True)

    def scrub(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "…")
        return text

    def call(self, app, name, fn, fields=()):
        """fn returns the answer; `fields` are the keys Usher reads from it (a dict, or the first item of a list)."""
        try:
            answer = fn()
            sample = answer[0] if isinstance(answer, list) and answer else answer
            for field in fields:
                if isinstance(sample, dict) and field not in sample:
                    raise KeyError(f"the answer has no {field!r}")
        except (ApiError, KeyError, TypeError, ValueError, OSError) as e:
            self.failures.append({"app": app, "call": name, "error": self.scrub(f"{type(e).__name__}: {e}")})
            return None
        slug = re.sub(r"[^a-z0-9]+", "-", f"{app}-{name}".lower()).strip("-")
        (self.out / "responses" / f"{slug}.json").write_text(json.dumps(answer, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return answer

    def finish(self):
        (self.out / "results.json").write_text(json.dumps({"versions": self.versions, "failures": self.failures}, indent=1) + "\n", encoding="utf-8")
        return 1 if self.failures else 0


def check_arr(run, app, client, lookup=None):
    status = run.call(app, "system/status", lambda: client.get("system/status"), ("version",))
    if status:
        run.versions[app] = status["version"]
    run.call(app, "health", lambda: client.get("health"))
    if app == "Prowlarr":
        for path in ("indexerstatus", "indexer", "applications"):
            run.call(app, path, lambda path=path: client.get(path))
        return
    run.call(app, "queue", lambda: client.get("queue", pageSize=1), ("totalRecords", "records"))
    run.call(app, "qualityprofile", lambda: client.get("qualityprofile"))
    run.call(app, "rootfolder", lambda: client.get("rootfolder"))
    if app == "Sonarr":
        run.call(app, "diskspace", lambda: client.get("diskspace"))
    run.call(app, "series" if app == "Sonarr" else "movie", lambda: client.get("series" if app == "Sonarr" else "movie"))
    run.call(app, "calendar", lambda: client.get("calendar", start="2030-01-01", end="2030-01-08"))
    path, term = LOOKUP[app]
    run.call(app, path, lambda: client.get(path, term=term))


def check_transmission(run, tr):
    session = run.call("Transmission", "session-get", lambda: tr.call("session-get", fields=["version", "download-dir"]), ("version", "download-dir"))
    if session:
        run.versions["Transmission"] = str(session["version"]).split(" ")[0]
        run.call("Transmission", "free-space", lambda: tr.call("free-space", path=session["download-dir"]), ("size-bytes",))
    run.call("Transmission", "session-stats", lambda: tr.call("session-stats"), ("downloadSpeed",))
    run.call("Transmission", "torrent-get", tr.torrents)


def check_jellyfin(run, jf):
    public = run.call("Jellyfin", "/System/Info/Public", lambda: jf.get("/System/Info/Public"), ("Id", "Version"))
    if public:
        run.versions["Jellyfin"] = public["Version"]
    run.call("Jellyfin", "/System/Info", lambda: jf.get("/System/Info"), ("Version",))
    run.call("Jellyfin", "/Users", lambda: jf.get("/Users"), ("Id", "Name"))
    run.call("Jellyfin", "/Sessions", lambda: jf.get("/Sessions", activeWithinSeconds="600"))
    run.call("Jellyfin", "/UserViews", jf.views)
    run.call("Jellyfin", "/UserItems/Resume", jf.resume)
    run.call("Jellyfin", "/Shows/NextUp", jf.next_up)
    run.call("Jellyfin", "/Items (unplayed)", jf.unplayed)
    run.call("Jellyfin", "/Items (library)", lambda: list(jf.library_tmdb()))


def viewing_account_id(env):
    """The check's own account (JELLYFIN_USER) is found by name here; Usher itself only ever takes an ID."""
    wanted = env.get("JELLYFIN_USER", "")
    users = Jellyfin(env["JELLYFIN_URL"], env["JELLYFIN_API_KEY"]).get("/Users")
    for u in users:
        if u.get("Name", "").lower() == wanted.lower():
            return u["Id"]
    raise SystemExit(f"upstream check: Jellyfin has no account named {wanted!r}; was it seeded?")


def run_all(out, env=os.environ):
    keys = [env.get(k, "") for k in ("SONARR_API_KEY", "RADARR_API_KEY", "PROWLARR_API_KEY", "JELLYFIN_API_KEY", "TR_PASS")]
    run = Run(out, keys)
    check_arr(run, "Sonarr", Arr("sonarr", env["SONARR_URL"], env["SONARR_API_KEY"]))
    check_arr(run, "Radarr", Arr("radarr", env["RADARR_URL"], env["RADARR_API_KEY"]))
    check_arr(run, "Prowlarr", Arr("prowlarr", env["PROWLARR_URL"], env["PROWLARR_API_KEY"], version="v1"))
    check_transmission(run, Transmission(env["TRANSMISSION_URL"], env["TR_USER"], env["TR_PASS"]))
    check_jellyfin(run, Jellyfin(env["JELLYFIN_URL"], env["JELLYFIN_API_KEY"], viewing_account_id(env)))
    return run


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(run_all(sys.argv[1]).finish())
