"""Checks that everything Usher points at still exists: its Jellyfin account, each app's key, the quality
profile and library folder new requests use, the download client, and Prowlarr's links to Sonarr and Radarr.

Things are referred to by ID wherever the integrations give one, so renaming anything in an app's web UI never breaks
Usher. What a rename or a deleted item *does* break shows up here, and /healthz reports it, so the daily check
names the cause the next morning.

Runs in the background every few minutes; /healthz only reads the last result, so it always answers at once.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("usher.wiring")

INTERVAL = 300  # seconds between checks
TIMEOUT = 20  # per app


class Wiring:
    def __init__(self, sonarr, radarr, prowlarr, tr, jf, actions):
        self.sonarr, self.radarr, self.prowlarr, self.tr, self.jf, self.actions = sonarr, radarr, prowlarr, tr, jf, actions
        self.result = {"status": "checking", "problems": [], "notes": [], "checked": None}
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=4)

    # the checks -------------------------------------------------------------------
    def _jellyfin(self):
        problems, notes = [], []
        uid = self.jf.user_id()  # raises if media_server.user_id isn't set
        try:
            user = self.jf.get(f"/Users/{uid}")
        except Exception as e:
            if "404" in str(e) or "400" in str(e):
                return [f"Jellyfin: the viewing account {uid} no longer exists (media_server.user_id)"], notes
            raise
        if (user.get("Policy") or {}).get("IsDisabled"):
            problems.append("Jellyfin: the viewing account is disabled")
        return problems, notes

    def _arr(self, app, kind):
        problems, notes = [], []
        app.get("system/status")  # the key works
        try:
            pid, how = self.actions.profile_choice(app)
            if how == "name":
                notes.append(
                    f"{app.name.title()}: quality profile found by name; set {self.actions.profile_env(app)} = {pid} in usher.toml so a rename can't break it"
                )
            elif how == "fallback":
                problems.append(f"{app.name.title()}: no quality profile named {self.actions.quality!r}; new requests would use profile {pid}")
        except Exception as e:  # a configured ID that no longer exists
            problems.append(str(e))
        roots = {r.get("path", "").rstrip("/"): r for r in app.get("rootfolder")}
        family = getattr(self.actions, "family_tv_root" if kind == "tv" else "family_movie_root", "")
        for wanted in [self.actions.tv_root if kind == "tv" else self.actions.movie_root] + ([family] if family else []):
            root = roots.get(wanted.rstrip("/"))
            if not root:
                problems.append(f"{app.name.title()}: library folder {wanted} is not set up in the app")
            elif root.get("accessible") is False:
                problems.append(f"{app.name.title()}: can't reach library folder {wanted}")
        clients = [c for c in app.get("downloadclient") if c.get("enable")]
        if not any((c.get("implementation") or "").lower() == "transmission" for c in clients):
            problems.append(f"{app.name.title()}: no download client for Transmission is turned on")
        return problems, notes

    def _prowlarr(self):
        problems = []
        apps = {(a.get("implementation") or "").lower() for a in self.prowlarr.get("applications") if a.get("syncLevel", "fullSync") != "disabled"}
        for name in ("sonarr", "radarr"):
            if name not in apps:
                problems.append(f"Prowlarr: not linked to {name.title()}, so it gets no search sites")
        return problems, []

    def _transmission(self):
        self.tr.call("session-get", fields=["version"])  # the login works
        return [], []

    def check(self):
        jobs = {
            "Jellyfin": self._jellyfin,
            "Sonarr": lambda: self._arr(self.sonarr, "tv"),
            "Radarr": lambda: self._arr(self.radarr, "movies"),
            "Prowlarr": self._prowlarr,
            "Transmission": self._transmission,
        }
        futures = {name: self._pool.submit(fn) for name, fn in jobs.items()}
        problems, notes = [], []
        for name, fut in futures.items():
            try:
                p, n = fut.result(timeout=TIMEOUT)
                problems += p
                notes += n
            except Exception as e:  # an app down or a key rejected: say which
                msg = str(e)
                problems.append(msg if msg.lower().startswith(name.lower()) else f"{name}: {msg}")
        result = {"status": "degraded" if problems else "ok", "problems": problems, "notes": notes, "checked": int(time.time())}
        if problems and problems != self.result.get("problems"):
            log.warning("wiring: %s", "; ".join(problems))
        for n in notes:
            if n not in self.result.get("notes", []):
                log.info("wiring: %s", n)
        self.result = result
        return result

    # background loop ------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, name="wiring", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        self._stop.wait(10)  # let the integrations settle after a restart
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:
                log.exception("wiring check failed")
            self._stop.wait(INTERVAL)
