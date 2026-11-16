"""Run on the server: sudo usher-user add|passwd|remove|list [username], ids, and config check."""

import getpass
import re
import sys
from datetime import datetime

from . import config
from .auth import MIN_PASSWORD, hash_password
from .store import Store

USAGE = "usage: usher-user add <username> [display name] | passwd <username> | remove <username> | list | ids\n       usher-user config check"


def ask_password() -> str:
    while True:
        pw = getpass.getpass(f"New password (at least {MIN_PASSWORD} characters): ")
        if len(pw) < MIN_PASSWORD:
            print(f"Too short: use at least {MIN_PASSWORD} characters.")
            continue
        if pw != getpass.getpass("Same password again: "):
            print("Those didn't match. Try again.")
            continue
        return pw


def print_ids(settings) -> int:
    """The IDs usher.toml can hold, so renaming anything in the integrations never breaks Usher."""
    from .clients import Arr, Jellyfin, jellyfin_device_id

    jf = Jellyfin(settings.jellyfin_url, settings.jellyfin_key, settings.jellyfin_user_id, jellyfin_device_id(settings.data_dir))
    try:
        print("Jellyfin accounts (media_server.user_id):")
        for u in jf.get("/Users"):
            mark = " <- in use" if u["Id"] == jf._uid else ""
            print(f"  {u['Id']}  {u.get('Name', '')}{mark}")
    except Exception as e:
        print(f"  ! {e}")
    for name, url, key, env in (
        ("Sonarr", settings.sonarr_url, settings.sonarr_key, "pvr.tv.quality_profile_id"),
        ("Radarr", settings.radarr_url, settings.radarr_key, "pvr.movies.quality_profile_id"),
    ):
        try:
            print(f"{name} quality profiles ({env}):")
            for p in Arr(name.lower(), url, key).get("qualityprofile"):
                print(f"  {p['id']:<4} {p.get('name', '')}")
        except Exception as e:
            print(f"  ! {e}")
    return 0


def config_command(args) -> int:
    """config check: read and check usher.toml as start-up would."""
    if args == ["check"]:
        try:
            source = config.read()
            config.activate(source)
            notes = config.check(source)
        except config.ConfigError as e:
            print(f"Problem: {e}")
            return 1
        for n in notes:
            print(f"Note: {n}")
        print(f"Settings OK ({source.path}).")
        return 0
    print(USAGE)
    return 2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(USAGE)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "config":
        return config_command(args)
    try:
        settings = config.load(strict=False)  # lets `ids` run while media_server.user_id is still empty
    except config.ConfigError as e:
        print(f"Problem with the settings: {e}")
        return 1
    if cmd == "ids":
        return print_ids(settings)
    store = Store(settings.data_dir)
    if cmd == "list":
        for u in store.users():
            made = datetime.fromtimestamp(u["created"]).strftime("%d %b %Y")
            devices = len(store.sessions_for(u["id"]))
            print(f"{u['username']:<16} {u['display']:<16} added {made}, signed in on {devices} device(s)")
        return 0
    if not args:
        print(USAGE)
        return 2
    name = args[0].strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,31}", name):
        print("Usernames are 2–32 characters: letters, numbers, dot, dash or underscore.")
        return 2
    if cmd == "add":
        if store.user_by_name(name):
            print(f"{name} already exists. Use: usher-user passwd {name}")
            return 1
        display = " ".join(args[1:]).strip() or name.capitalize()
        store.add_user(name, display[:40], hash_password(ask_password()))
        print(f"Added {name} ({display}).")
        return 0
    if cmd == "passwd":
        if not store.user_by_name(name):
            print(f"No user called {name}.")
            return 1
        store.set_password(name, hash_password(ask_password()))
        uid = store.user_by_name(name)["id"]
        store.run("DELETE FROM sessions WHERE user_id = ?", (uid,))
        print(f"Password changed for {name}; their devices will need to sign in again.")
        return 0
    if cmd == "remove":
        print(f"Removed {name}." if store.remove_user(name) else f"No user called {name}.")
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
