"""The pictures in README.md: every one exists, is a small PNG with alt text and comes in light and dark, and they are
the ones tools/screenshots.py makes. The script itself is not run by Tests."""

import importlib.util
import io
import struct
import unittest
import unittest.mock
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"
MAX_BYTES = 150 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class Pictures(HTMLParser):
    """What the README's <picture> elements point at: (light src, alt, dark srcset or None) for each."""

    def __init__(self):
        super().__init__()
        self.found, self._dark = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "picture":
            self._dark = None
        elif tag == "source" and "dark" in (a.get("media") or ""):
            self._dark = a.get("srcset")
        elif tag == "img":
            self.found.append((a.get("src") or "", a.get("alt") or "", self._dark))
            self._dark = None


def readme_pictures():
    parser = Pictures()
    parser.feed((ROOT / "README.md").read_text(encoding="utf-8"))
    return parser.found


def load_tool():
    spec = importlib.util.spec_from_file_location("screenshots", ROOT / "tools" / "screenshots.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def png_size(path):
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


class ReadmePicturesTests(unittest.TestCase):
    def test_readme_shows_what_the_script_makes_in_light_and_dark(self):
        pictures = readme_pictures()
        names = [src.rsplit("/", 1)[-1].rsplit("-", 1)[0] for src, _, _ in pictures]
        self.assertEqual(sorted(names), sorted(load_tool().SCREENS), "README.md and tools/screenshots.py disagree on the screens")
        self.assertTrue(any(n.startswith(("ipad-", "mac-")) for n in names), "one iPad or Mac layout")
        for src, _, dark in pictures:
            self.assertTrue(src.endswith("-light.png"), src)
            self.assertEqual(dark, src.replace("-light.png", "-dark.png"), f"{src} has no matching dark picture")

    def test_every_picture_has_alt_text(self):
        for src, alt, _ in readme_pictures():
            self.assertGreaterEqual(len(alt.split()), 8, f"{src} needs alt text that says what the screen shows")

    def test_every_picture_exists_under_docs_images_and_is_small(self):
        for src, _, dark in readme_pictures():
            for name in (src, dark):
                self.assertTrue(name.startswith("docs/images/"), name)
                path = ROOT / name
                self.assertTrue(path.is_file(), f"README shows {name}, which doesn't exist")
                width, height = png_size(path)
                self.assertGreater(width, 300, name)
                self.assertGreater(height, 300, name)
                self.assertLessEqual(path.stat().st_size, MAX_BYTES, f"{name} is over {MAX_BYTES // 1024} KB: make it smaller")

    def test_light_and_dark_of_a_screen_are_the_same_size(self):
        for src, _, dark in readme_pictures():
            self.assertEqual(png_size(ROOT / src), png_size(ROOT / dark), src)

    def test_nothing_in_docs_images_goes_unused(self):
        used = {Path(n).name for src, _, dark in readme_pictures() for n in (src, dark)}
        self.assertEqual(sorted(p.name for p in IMAGES.iterdir() if p.name not in used), [], "unused file in docs/images")

    def test_how_to_redo_the_pictures_is_for_contributors_not_the_opening(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        opening, developing = text.split("## Features")[0], text.split("## Developing")[1]
        self.assertNotIn("screenshots.py", opening)
        self.assertIn("tools/screenshots.py", developing)

    def test_the_script_is_named_in_the_readme(self):
        self.assertIn("tools/screenshots.py", (ROOT / "README.md").read_text(encoding="utf-8"))


class ScriptEnvironmentTests(unittest.TestCase):
    def test_what_the_script_starts_gets_only_made_up_settings(self):
        # The fakes and Usher must not see this machine's environment (in a coding session it holds secrets).
        with unittest.mock.patch.dict("os.environ", {"SOME_SECRET_TOKEN": "x", "HOME": "/home/someone", "PATH": "/bin"}):
            env = load_tool().child_env(Path("/tmp/x"))
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(
            sorted(env),
            sorted(
                [
                    "PATH",
                    "PYTHONPATH",
                    "USHER_CONFIG",
                    "TR_PASS",
                    "JELLYFIN_API_KEY",
                    "SONARR_API_KEY",
                    "RADARR_API_KEY",
                    "PROWLARR_API_KEY",
                    "TMDB_TOKEN",
                    "TMDB_API_BASE",
                ]
            ),
        )
        self.assertTrue(env["TMDB_API_BASE"].startswith("http://127.0.0.1:"), "the fake TMDb, never the real one")


class NeutralFakesTests(unittest.TestCase):
    """The pictures show made-up, healthy answers; the fakes in tests/mock_apps.py keep their own data for the tests."""

    def setUp(self):
        self.tool = load_tool()

    def test_real_site_names_are_replaced_everywhere(self):
        answer = {"indexers": [{"name": "YTS"}, {"name": "EZTV"}], "message": "Indexers unavailable due to failures: 1337x"}
        text = str(self.tool.neutral(answer))
        for real in ("YTS", "EZTV", "1337x"):
            self.assertNotIn(real, text)
        self.assertIn("Example Indexer", text)

    def test_indexer_warnings_and_failing_sites_are_left_out_and_other_health_kept(self):
        health = [{"source": "IndexerStatusCheck", "message": "x"}, {"source": "UpdateCheck", "message": "y"}, {"indexerId": 2, "disabledTill": "t"}]
        self.assertEqual(self.tool.neutral(health), [{"source": "UpdateCheck", "message": "y"}])

    def test_a_stopped_download_runs_instead(self):
        stopped = {"hashString": "ab", "error": 3, "errorString": "No data found", "status": 0, "percentDone": 0.3}
        fixed = self.tool.neutral({"torrents": [stopped]})["torrents"][0]
        self.assertEqual((fixed["error"], fixed["errorString"], fixed["status"], fixed["percentDone"]), (0, "", 4, 0.3))
        snake = {"hash_string": "ab", "error": 3, "error_string": "No data found", "status": 0}  # Transmission's newer JSON-RPC
        self.assertEqual(self.tool.neutral(snake), {"hash_string": "ab", "error": 0, "error_string": "", "status": 4})
        queued = self.tool.neutral({"status": "warning", "trackedDownloadStatus": "warning", "timeleft": None, "size": 3})
        self.assertEqual((queued["status"], queued["trackedDownloadStatus"], queued["size"]), ("downloading", "ok", 3))

    def test_a_nearly_full_disk_gets_room_but_a_roomy_one_is_kept(self):
        self.assertEqual(self.tool.neutral([{"freeSpace": 28.4e9, "totalSpace": 243e9}]), [{"freeSpace": self.tool.FREE_BYTES, "totalSpace": 243e9}])
        self.assertEqual(self.tool.neutral({"freeSpace": 500e9}), {"freeSpace": 500e9})

    def test_each_discover_row_shows_its_own_titles(self):
        movies = {"results": [{"id": 12000, "title": "Paddington in Peru"}, {"id": 286217, "title": "The Martian"}]}
        shows = {"results": [{"id": 500, "name": "Shrinking"}, {"id": 777, "name": "No Listings Show"}]}
        swapped = self.tool.other_titles("/3/movie/popular?region=US", movies)["results"]
        self.assertEqual([r["id"] for r in swapped], [12000, 12001])
        self.assertNotIn("The Martian", str(swapped))
        self.assertNotIn("Shrinking", str(self.tool.other_titles("/3/tv/popular", shows)))
        trending = {"results": [{"id": 286217, "title": "The Martian"}]}  # the Trending row keeps its titles
        self.assertEqual(self.tool.other_titles("/3/trending/all/week", trending), trending)

    def test_the_fakes_themselves_are_not_changed(self):
        # tests/mock_apps.py still has the data its other users expect: only the script's copy is made neutral.
        self.assertIn("YTS", (ROOT / "tests" / "mock_apps.py").read_text(encoding="utf-8"))

    def test_posters_are_never_fetched(self):
        source = (ROOT / "tools" / "screenshots.py").read_text(encoding="utf-8")
        self.assertIn('"**/api/art/**", lambda route: route.fulfill(status=404)', source)


class SettingsPictureTests(unittest.TestCase):
    """The Settings picture reads as a healthy, ordinary phone, not as a fault."""

    def test_the_pictures_phone_is_named_iphone(self):
        from usher.auth_routes import describe_device

        self.assertEqual(describe_device(load_tool().PHONE_AGENT), "iPhone")

    def test_the_update_check_is_reported_the_way_the_update_script_does(self):
        import json
        import tempfile

        from usher import __version__
        from usher.update_result import UpdateResult, local_only

        tool, sent = load_tool(), []

        def fake_open(request, timeout):
            sent.append(request)
            return io.BytesIO(b"{}")

        with unittest.mock.patch.object(tool.urllib.request, "urlopen", fake_open):
            tool.report_update_check()
        (request,) = sent
        self.assertEqual(request.full_url, f"http://{tool.BIND}/internal/update-result")  # this machine's own address, as the script posts to
        self.assertTrue(local_only(tool.BIND.split(":")[0], request.get_header("X-forwarded-for") or ""))
        with tempfile.TemporaryDirectory() as tmp:
            updates = UpdateResult(Path(tmp))
            updates.report(json.loads(request.data))
            self.assertEqual(updates.state(), "up_to_date")
        self.assertEqual(json.loads(request.data)["newest"], __version__)

    def test_the_settings_picture_has_a_solid_top_bar(self):
        # Scrolled under a translucent bar, the end of the group above would show through; the app's Reduce Transparency look hides it.
        self.assertIn("--bar: var(--bg-elevated)", load_tool().SOLID_BAR)
        self.assertIn("backdrop-filter: none", load_tool().SOLID_BAR)


class ScreenshotRequirementsTests(unittest.TestCase):
    def pins(self):
        text = (ROOT / "requirements-screenshots.txt").read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]

    def test_the_tools_are_pinned_exactly(self):
        pins = self.pins()
        self.assertEqual(sorted(p.split("==")[0] for p in pins), ["pillow", "playwright"])
        for pin in pins:
            self.assertRegex(pin, r"^[a-z]+==\d+(\.\d+)+$")

    def test_they_are_not_a_runtime_or_test_dependency(self):
        for name in ("requirements.txt", "requirements-dev.txt", "Dockerfile"):
            text = (ROOT / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("playwright", text, name)
            self.assertNotIn("requirements-screenshots", text, name)

    def test_the_script_says_how_to_install_and_run_it(self):
        header = (ROOT / "tools" / "screenshots.py").read_text(encoding="utf-8").split('"""')[1]
        self.assertIn("pip install -r requirements.txt -r requirements-screenshots.txt", header)
        self.assertIn("playwright install chromium", header)
        self.assertIn("python3 tools/screenshots.py", header)


@unittest.skipUnless(importlib.util.find_spec("PIL"), "needs Pillow")
class ScriptHelperTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_shrink_keeps_a_valid_smaller_png(self):
        from PIL import Image

        img = Image.new("RGB", (390, 844), (242, 242, 247))
        raw = io.BytesIO()
        img.save(raw, "PNG")
        small = self.tool.shrink(raw.getvalue())
        self.assertTrue(small.startswith(PNG_SIGNATURE))
        self.assertLessEqual(len(small), len(raw.getvalue()))
        self.assertEqual(Image.open(io.BytesIO(small)).size, (390, 844))


if __name__ == "__main__":
    unittest.main()
