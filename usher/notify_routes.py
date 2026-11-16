"""The request handler's notification settings: what a person's devices and choices are, and saving them."""


class NotifyRoutes:
    def notify_body(self, s):
        p = dict(self.app.store.prefs(s["user_id"]))
        devices = [{"device": d["device"], "since": d["created"], "lastOk": d["last_ok"], "endpoint": d["endpoint"]} for d in self.app.store.subs(s["user_id"])]
        return {
            "prefs": {k: (bool(p[k]) if k in ("ready", "problem", "space", "server") else p[k]) for k in ("ready", "problem", "space", "server", "tz_offset")},
            "devices": devices,
        }

    def save_prefs(self, s, data):
        values = {}
        for k in ("ready", "problem", "space", "server"):
            if k in data:
                values[k] = 1 if data[k] else 0
        if "tz_offset" in data:
            try:
                off = int(data["tz_offset"])
                if -14 * 60 <= off <= 14 * 60:
                    values["tz_offset"] = off
            except (TypeError, ValueError):
                pass
        self.app.store.set_prefs(s["user_id"], values)
