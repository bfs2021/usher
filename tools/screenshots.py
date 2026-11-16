#!/usr/bin/env python3
"""Make the pictures in README.md (docs/images/) from the fake integrations in tests/mock_apps.py.

Run it again whenever the interface changes. It is not part of Tests, not a runtime dependency and nothing imports it.

Install what it needs in a virtual environment outside this repository (the app itself needs none of it), then run
it from the repository root:

    python3 -m venv ~/screenshots-env && . ~/screenshots-env/bin/activate
    pip install -r requirements.txt -r requirements-screenshots.txt && playwright install chromium
    python3 tools/screenshots.py [--out docs/images]

It starts the fakes and a throw-away Usher in a temporary folder, with made-up settings and one made-up account
(Alex), opens the app in headless Chromium and saves each screen once in light and once in dark, as a small PNG:
Search with Discover, one request's journey, Downloads, Status and Settings on a phone, and Status on an iPad.
It starts its own fakes on ports 9901-9906, so stop any that are running first (README.md, Developing).

Nothing from a real library, account or home goes in. Posters are never fetched: every one stays the app's own
title tile, drawn here as a placeholder in a colour and plain shape made from its title. The fakes answer with
neutral names and a healthy Status; tests/mock_apps.py itself is not changed.
"""

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
BIND = "127.0.0.1:8494"  # not Usher's usual port, so it never meets a real one
MOCK_PORTS = range(9901, 9907)
USER, DISPLAY, SECRET = "alex", "Alex", "correct horse battery"
PHONE = {"width": 390, "height": 844}
# What Settings → Devices calls this one: Usher names a device from its user agent, and headless Chromium's is not a phone's.
PHONE_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
IPAD = {"width": 1180, "height": 820}
THEMES = ("light", "dark")
SCREENS = ("phone-search", "phone-journey", "phone-downloads", "phone-status", "phone-settings", "ipad-status")  # README.md shows these
KEY = "a" * 32  # the fakes' made-up keys, as in tests/mock_apps.py
JELLYFIN_KEY = "b" * 32
# Names the fakes use for real search sites, and what the pictures show instead.
NEUTRAL_NAMES = {"YTS": "Example Indexer", "EZTV": "Example Indexer 2", "1337x": "Example Indexer 3"}
FREE_BYTES = 120e9  # plenty of room, so Status shows no "getting full" warning

# The app's own look for Reduce Transparency (app.css), for the screen that is scrolled under the bar.
SOLID_BAR = ":root { --bar: var(--bg-elevated); } *, *::before, *::after { -webkit-backdrop-filter: none !important; backdrop-filter: none !important; }"

# The placeholder posters: the app's title tile with a colour from the title and one of four plain shapes. A page
# script, so a tile the app draws again later gets the same look.
POSTERS_JS = """
(() => {
  const css = `
    .poster-fallback { color: #fff !important; font-weight: 800 !important; background-color: var(--tile, #2c4a66) !important;
      background-image: var(--shape, none) !important; overflow: hidden; text-wrap: balance; text-shadow: 0 1px 2px rgba(0,0,0,.35); }
    .poster-fallback.titled { font-size: 1.05rem !important; line-height: 1.1 !important; padding: 8px !important;
      overflow-wrap: normal !important; word-break: normal !important; }
    .thumb.poster-fallback.titled { font-size: 0 !important; padding: 0 !important; }
    .thumb.poster-fallback.titled::before { content: attr(data-initial); font-size: 1.9rem; line-height: 1; }`;
  const shapes = [
    "radial-gradient(circle at 78% 18%, rgba(255,255,255,.2) 0 22%, transparent 23%)",
    "repeating-linear-gradient(135deg, rgba(255,255,255,.1) 0 10px, transparent 10px 20px)",
    "linear-gradient(160deg, transparent 58%, rgba(0,0,0,.3) 58%)",
    "radial-gradient(circle at 50% 118%, rgba(255,255,255,.22) 0 42%, transparent 43%)",
  ];
  const dress = (el) => {
    let hash = 5381;
    for (const ch of el.textContent) hash = ((hash * 33) ^ ch.charCodeAt(0)) >>> 0;
    el.style.setProperty("--tile", `hsl(${hash % 360} 42% 30%)`);
    el.style.setProperty("--shape", shapes[(hash >> 3) % shapes.length]);
    el.dataset.initial = el.textContent.trim().charAt(0).toUpperCase();
    el.dataset.dressed = "1";
  };
  const sweep = () => document.querySelectorAll(".poster-fallback:not([data-dressed])").forEach(dress);
  const start = () => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.append(style);
    new MutationObserver(sweep).observe(document.documentElement, { childList: true, subtree: true });
    sweep();
  };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
})();
"""


def neutral(data):
    """An answer from the fakes with real site names replaced, no failing search site or stopped download, and plenty of free space."""
    if isinstance(data, list):
        return [neutral(x) for x in data if not (isinstance(x, dict) and (x.get("source") == "IndexerStatusCheck" or "disabledTill" in x))]
    if isinstance(data, dict) and data.get("error") and ("hashString" in data or "hash_string" in data):  # stopped: running instead
        running = {"error": 0, "errorString": "", "error_string": "", "status": 4, "rateDownload": 2_000_000, "rate_download": 2_000_000, "eta": 600}
        data = {**data, **{k: v for k, v in running.items() if k in data}}
    if isinstance(data, dict) and data.get("trackedDownloadStatus") == "warning":  # the same download in Sonarr or Radarr
        data = {**data, "status": "downloading", "trackedDownloadStatus": "ok", "timeleft": "00:10:00"}
    if isinstance(data, dict):
        return {k: FREE_BYTES if k == "freeSpace" and v < FREE_BYTES else neutral(v) for k, v in data.items()}
    if isinstance(data, str):
        for real, made_up in NEUTRAL_NAMES.items():
            data = data.replace(real, made_up)
    return data


# Rows of Discover that the fakes fill with a title another row already shows: the pictures give each row its own.
OTHER_TITLES = {
    "/3/movie/popular": {286217: {"id": 12001, "title": "Harbour Lantern", "release_date": "2024-05-01", "poster_path": "/m12001.jpg", "overview": "A film."}},
    "/3/tv/popular": {500: {"id": 778, "name": "Quiet Orchard", "first_air_date": "2024-01-01", "poster_path": "/p778.jpg", "overview": "A show."}},
}


def other_titles(path: str, data):
    """The fakes' answer for a Discover row, with any title a different row also shows swapped for another one."""
    swaps = OTHER_TITLES.get(path.split("?")[0])
    if not swaps or not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return data
    return {**data, "results": [swaps.get(r.get("id"), r) if isinstance(r, dict) else r for r in data["results"]]}


def run_fakes():
    """The fakes of tests/mock_apps.py with their answers made neutral and healthy, only for these pictures."""
    from tests import mock_apps

    reply = mock_apps.Base.reply

    def neutral_reply(self, data, code=200, ctype="application/json"):
        return reply(self, data if isinstance(data, bytes) else neutral(other_titles(self.path, data)), code, ctype)

    mock_apps.Base.reply = neutral_reply
    mock_apps.main()


def shrink(png: bytes) -> bytes:
    """The same picture in 256 colours: flat interface colours lose nothing visible and the file is a third the size."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


class StartError(Exception):
    pass


def wait_for(ready, what: str, seconds: float = 30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ready():
            return
        time.sleep(0.2)
    raise StartError(f"Timed out waiting for {what}.")


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://{BIND}/healthz", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def child_env(folder: Path) -> dict:
    """All the fakes and Usher get: the made-up settings and PATH, nothing from this machine's own environment."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT),
        "USHER_CONFIG": str(folder / "usher.toml"),
        "TR_PASS": "x",
        "JELLYFIN_API_KEY": JELLYFIN_KEY,
        "SONARR_API_KEY": KEY,
        "RADARR_API_KEY": KEY,
        "PROWLARR_API_KEY": KEY,
        "TMDB_TOKEN": "tmdb-test-token",
        "TMDB_API_BASE": "http://127.0.0.1:9906/3",
    }


def start(folder: Path, procs: list):
    """The fakes and Usher, each its own process with only the made-up settings in its environment."""
    busy = [p for p in (*MOCK_PORTS, int(BIND.rsplit(":", 1)[1])) if listening(p)]
    if busy:
        raise StartError(f"Port(s) {', '.join(map(str, busy))} already in use: stop what is using them first.")
    data = folder / "data"
    data.mkdir()
    (folder / "usher.toml").write_text(
        f'''[server]
origin = "http://{BIND}"
bind = "{BIND}"
data_dir = "{data}"
[media_server]
url = "http://127.0.0.1:9905"
user_id = "{'8' * 32}"
[pvr.tv]
url = "http://127.0.0.1:9901"
[pvr.movies]
url = "http://127.0.0.1:9902"
[indexer_manager]
url = "http://127.0.0.1:9903"
[download_client]
url = "http://127.0.0.1:9904/transmission/rpc"
user = "x"
''',
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT))
    from usher.auth import hash_password
    from usher.store import Store

    Store(data).add_user(USER, DISPLAY, hash_password(SECRET))
    for name, command, ready, what in (
        ("fakes", [str(Path(__file__).resolve()), "--fakes"], lambda: all(listening(p) for p in MOCK_PORTS), "the fake integrations"),
        ("usher", ["-m", "usher.server"], healthy, "Usher"),
    ):
        with open(folder / f"{name}.log", "wb") as log:
            procs.append(subprocess.Popen([sys.executable, *command], stdout=log, stderr=log, cwd=ROOT, env=child_env(folder)))
        try:
            wait_for(ready, what)
        except StartError:
            lines = (folder / f"{name}.log").read_text(errors="replace").splitlines()
            raise StartError("Timed out waiting for " + what + ". Its last lines:\n" + "\n".join(lines[-8:])) from None


def sign_in(page):
    page.goto(f"http://{BIND}/")
    page.fill("input[name=username]", USER)
    page.fill("input[name=password]", SECRET)
    page.click("button[type=submit]")
    page.wait_for_selector("nav .tab")


def settle(page, ms: int = 1200):
    """Let the screen's lists and posters arrive."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def report_update_check():
    """Tell the throw-away Usher that the update script has just looked and found nothing newer, as the script does:
    from this machine, with no sign-in. About → Updates then reads "Up to date" instead of "Can't tell"."""
    from usher import __version__

    body = json.dumps({"result": "checked", "newest": __version__, "held": False, "failed": False}).encode()
    request = urllib.request.Request(f"http://{BIND}/internal/update-result", body, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except OSError as e:
        raise StartError(f"Usher refused the update check report: {e}") from None


def context(browser, viewport, scale, state=None, agent=None):
    """A device with the same time zone and language everywhere, and no motion so every picture is the same."""
    ctx = browser.new_context(
        viewport=viewport,
        device_scale_factor=scale,
        locale="en-US",
        timezone_id="UTC",
        reduced_motion="reduce",
        storage_state=state,
        bypass_csp=True,
        user_agent=agent,
    )
    # bypass_csp lets the page script add its style; the page still loads nothing from anywhere but the app.
    # No poster is ever fetched, the fakes' own included: the app keeps its title tile, which POSTERS_JS dresses
    # (a small tile gets the title's first letter).
    ctx.route("**/api/art/**", lambda route: route.fulfill(status=404))
    ctx.add_init_script(POSTERS_JS)
    return ctx


def save(page, out: Path, name: str, saved: list):
    """The current screen once per theme: the app follows the device's setting live, so no reload. Both are taken
    before either is shrunk, so a download's progress is the same in both."""
    raw = {}
    for theme in THEMES:
        page.emulate_media(color_scheme=theme)
        page.wait_for_timeout(150)
        raw[theme] = page.screenshot()
    for theme, png in raw.items():
        path = out / f"{name}-{theme}.png"
        path.write_bytes(shrink(png))
        saved.append(path)


def scroll_to(page, target, gap: int = 24):
    """Scroll the screen so the target sits `gap` pixels below the top."""
    page.evaluate(
        "([el, gap]) => { const main = document.querySelector('main'); main.scrollTop += el.getBoundingClientRect().top - main.getBoundingClientRect().top - gap; }",
        [target.element_handle(), gap],
    )


def end_on(page, target, gap: int = 16):
    """Make the phone just tall enough that the screen ends `gap` pixels under the target, so the last visible row is a whole one."""
    extra = page.evaluate(
        "([el, gap]) => el.getBoundingClientRect().bottom + gap - document.querySelector('main').getBoundingClientRect().bottom",
        [target.element_handle(), gap],
    )
    page.set_viewport_size({"width": PHONE["width"], "height": round(PHONE["height"] + extra)})
    page.wait_for_timeout(300)


def ask_for_a_show(page):
    """Open a Discover title and tap Get it, so Downloads has a request of Usher's own with its journey."""
    page.click("text=Shrinking >> nth=0")
    page.click("button.btn:has-text('Get it')")
    page.wait_for_selector("ol.journey")


def capture(browser, out: Path):
    saved: list[Path] = []
    ctx = context(browser, PHONE, 2, agent=PHONE_AGENT)
    page = ctx.new_page()
    sign_in(page)
    settle(page)
    page.click("nav a.tab[href='/search']")
    settle(page)
    end_on(page, page.locator("section", has_text="Popular movies").locator(".sub").first)
    save(page, out, SCREENS[0], saved)
    page.set_viewport_size(PHONE)

    ask_for_a_show(page)
    page.wait_for_selector("ol.journey li.now:has-text('Downloading')", timeout=60000)
    page.wait_for_selector("ol.journey .progress")
    settle(page, 500)
    save(page, out, SCREENS[1], saved)
    page.keyboard.press("Escape")

    for tab, name in (("downloads", SCREENS[2]), ("status", SCREENS[3])):
        page.click(f"nav a.tab[href='/{tab}']")
        settle(page)
        if tab == "status":
            end_on(page, page.get_by_text("Plenty of room for now"))
        save(page, out, name, saved)
        page.set_viewport_size(PHONE)
    report_update_check()
    page.click("button.nav-action")
    settle(page)
    # Open titles in, Devices, About and Updates: the notification choices read as off in a browser with no home-screen app, which looks like a fault.
    scroll_to(page, page.locator("h3", has_text="Open titles in").first, 64)  # clear of the bar with the screen's title
    # Scrolled, the top bar would show the end of the group above through its blur; solid, as with Reduce Transparency, it shows nothing.
    page.add_style_tag(content=SOLID_BAR)
    end_on(page, page.locator(".row", has_text="Changelog").first, 8)
    save(page, out, SCREENS[4], saved)

    wide = context(browser, IPAD, 1.5, ctx.storage_state())
    page = wide.new_page()
    page.goto(f"http://{BIND}/status")
    page.wait_for_selector("nav .side-link")
    settle(page, 2000)
    save(page, out, SCREENS[5], saved)
    ctx.close()
    wide.close()
    return saved


def main():
    parser = argparse.ArgumentParser(description="Make the pictures in README.md from the fake integrations.")
    parser.add_argument("--out", type=Path, default=OUT, help="where the PNGs go (default: docs/images)")
    parser.add_argument("--fakes", action="store_true", help=argparse.SUPPRESS)  # how this script starts the fakes
    args = parser.parse_args()
    if args.fakes:
        return run_fakes()
    out = args.out
    try:
        import PIL  # noqa: F401  (used to shrink the pictures)
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        sys.exit(f"Needs {e.name}: pip install -r requirements.txt playwright pillow && playwright install chromium")
    out.mkdir(parents=True, exist_ok=True)
    procs: list = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            start(Path(tmp), procs)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                saved = capture(browser, out)
                browser.close()
        except StartError as e:
            sys.exit(str(e))
        finally:
            for proc in procs:
                proc.terminate()
            for proc in procs:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    for path in saved:
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
