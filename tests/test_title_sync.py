"""One identity per title, and every view kept in step with it (titles.py).

Runs the real clients, poller, actions and live updates against the fake integrations in tests/mock_apps.py,
each on its own free port, with their data fresh for every test.
"""

import importlib
import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests import mock_apps
from usher.actions import Actions
from usher.clients import Arr, Jellyfin, Transmission
from usher.server import Handler
from usher.state import State
from usher.store import Store
from usher.titles import ART_EPOCH, Titles, arr_art, ident, jf_art
from tests.webjs import client_source

PADDINGTON_JF = "3" * 32  # Paddington in Peru (Radarr 12, TMDb 12000), under Continue watching
SEVERANCE_EP = "4" * 32  # Severance S2E4 (Sonarr 2), under Continue watching
SLOW_HORSES_JF = "a" * 32  # Slow Horses (Sonarr 1, TVDB 100): Latest; its episodes in Next up and Ready


def home_ids(snap, row=None):
    return {c["jf"] for r in snap.get("home", []) if row is None or r["id"] == row for c in r["items"]}


class Rig:
    """Usher's own parts wired to the fake integrations."""

    def __init__(self, test):
        self.m = importlib.reload(mock_apps)  # fresh lists for every test
        self.servers = []
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        test.addCleanup(self.close)
        self.sonarr = Arr("sonarr", self.serve(self.m.arr_handler("sonarr")), self.m.KEY)
        self.radarr = Arr("radarr", self.serve(self.m.arr_handler("radarr")), self.m.KEY)
        self.prowlarr = Arr("prowlarr", self.serve(self.m.arr_handler("prowlarr")), self.m.KEY, "v1")
        self.tr = Transmission(self.serve(self.m.TrHandler) + "/transmission/rpc", "", "")
        self.jf = Jellyfin(self.serve(self.m.JfHandler), self.m.JF_KEY, "8" * 32)
        self.store = Store(Path(tmp.name))
        self.store.add_user("alex", "Alex", "x")
        self.user = {"username": "alex", "user_id": self.store.user_by_name("alex")["id"]}
        settings = mock.Mock(media_path="/srv/media", low_space_gb=30, recycle_dir="/nonexistent", family_tv_folder="", family_movie_folder="")
        self.state = State(settings, self.sonarr, self.radarr, self.prowlarr, self.tr, self.jf, self.store)
        self.actions = Actions(self.sonarr, self.radarr, self.store, self.state)

    def serve(self, handler):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def close(self):
        self.store._db.close()
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()

    def poll(self):
        """One look at everything, as the poller does."""
        self.state._poll_slow()
        self.state._poll_fast()
        with self.state._build_lock:
            self.state._publish(self.state._build())
        return self.state.snapshot

    def request(self, kind, arr_id, ext_id, title, jf_id=None, age=0):
        rid = self.store.add_request(kind, arr_id, ext_id, title, 2024, "", self.user["user_id"])
        self.store.run("UPDATE requests SET created = ?, jf_id = ? WHERE id = ?", (int(time.time()) - age, jf_id, rid))
        return rid


class RemovedTitlesLeaveHome(unittest.TestCase):
    """a removed title disappears from Home straight away, Continue watching included, and stays gone."""

    def test_removed_movie_leaves_continue_watching_and_media_server_is_told(self):
        rig = Rig(self)
        snap = rig.poll()
        self.assertIn(PADDINGTON_JF, home_ids(snap, "resume"))
        rig.actions.remove("movie", 12, rig.user)
        # At once, before the poller looks again
        self.assertNotIn(PADDINGTON_JF, home_ids(rig.state.snapshot))
        self.assertIn({"Path": "/srv/media/Movies/Paddington in Peru (2024)", "UpdateType": "Deleted"}, rig.m.JF_UPDATES)
        # The fake media server still lists it (it hasn't scanned yet); a reload still leaves it out.
        snap = rig.poll()
        self.assertNotIn(PADDINGTON_JF, home_ids(snap))
        self.assertNotIn(PADDINGTON_JF, {r["id"] for r in snap["ready"]})
        self.assertIn(SEVERANCE_EP, home_ids(snap, "resume"))  # nothing else goes with it

    def test_tidied_season_leaves_home_but_the_show_stays(self):
        rig = Rig(self)
        rig.poll()
        listed = {"items": [{"kind": "season", "id": 2, "season": 2}], "bytes": 0}
        with mock.patch.object(rig.state, "tidy", return_value=listed):
            rig.actions.tidy_remove("season", 2, 2, rig.user)
        snap = rig.poll()
        self.assertNotIn(SEVERANCE_EP, home_ids(snap))
        self.assertIn("c" * 32, home_ids(snap))  # Severance itself is still under Latest
        self.assertIn({"Path": "/srv/media/TV Shows/Severance", "UpdateType": "Modified"}, rig.m.JF_UPDATES)

    def test_deleted_in_radarr_directly_is_gone_within_one_poll(self):
        rig = Rig(self)
        rid = rig.request("movie", 12, 12000, "Paddington in Peru", jf_id=PADDINGTON_JF)
        rig.poll()
        seq = rig.state.titles.seq
        rig.m.MOVIES[:] = [m for m in rig.m.MOVIES if m["id"] != 12]  # removed in Radarr's own page
        snap = rig.poll()
        self.assertNotIn(PADDINGTON_JF, home_ids(snap))
        self.assertIn({"Path": "/srv/media/Movies/Paddington in Peru (2024)", "UpdateType": "Deleted"}, rig.m.JF_UPDATES)
        self.assertIsNone(rig.store.one("SELECT id FROM requests WHERE id = ?", (rid,)))  # its request closed
        notices = rig.state.titles.since(seq)
        self.assertEqual([(n["what"], n["titles"][0]["key"]) for n in notices], [("removed", "movie:12000")])

    def test_card_whose_item_has_gone_is_dropped(self):
        rig = Rig(self)
        rig.poll()
        rig.state.art_missing(PADDINGTON_JF)  # its picture 404s and it really isn't there
        self.assertIn(PADDINGTON_JF, home_ids(rig.state.snapshot))
        with mock.patch.object(rig.jf, "existing", return_value=set()):
            rig.state.art_missing(SEVERANCE_EP)
        self.assertNotIn(SEVERANCE_EP, home_ids(rig.state.snapshot))


class OneChangeEveryView(unittest.TestCase):
    """a change to a title updates Search, Library, Upcoming and Home in the same live update."""

    def test_remove_updates_search_library_upcoming_and_home_together(self):
        rig = Rig(self)
        rig.poll()
        lib = rig.actions.search("paddington", part="library")["results"]
        self.assertTrue(lib and lib[0]["inLibrary"])
        self.assertIn(12, [m["id"] for m in rig.state.library()["movies"]])
        rig.state.upcoming()
        seq, version = rig.state.titles.seq, rig.state.version
        rig.actions.remove("movie", 12, rig.user)
        snap, notices = rig.state.wait_events(version, seq, 1)
        self.assertEqual(notices[0]["what"], "removed")
        self.assertEqual(notices[0]["titles"][0], {"key": "movie:12000", "kind": "movie", "id": 12, "ext": 12000, "title": "Paddington in Peru"})
        self.assertNotIn(PADDINGTON_JF, home_ids(snap))
        self.assertEqual(snap["changes"], notices[-1]["seq"])
        self.assertEqual(rig.actions.search("paddington", part="library")["results"], [])
        self.assertNotIn(12, [m["id"] for m in rig.state.library()["movies"]])
        self.assertNotIn("upcoming", rig.state._cache)

    def test_get_it_drops_the_cached_search_lookup(self):
        """A just-added title doesn't show Get it again from a lookup cached before."""
        rig = Rig(self)
        rig.poll()
        before = rig.actions.search("martian", part="movie")["results"][0]
        self.assertFalse(before["inLibrary"])
        rig.actions.get("movie", 286217, "", rig.user)
        after = rig.actions.search("martian", part="movie")["results"][0]
        self.assertTrue(after["inLibrary"])

    def test_two_devices_get_the_change_and_new_state_in_one_update(self):
        """Both signed-in devices hear about the change over the live updates, before the new state."""
        rig = Rig(self)
        rig.poll()
        real_wait = rig.state.wait_events
        rig.state.wait_events = lambda v, s, t: real_wait(v, s, 0.05)
        signed_out = threading.Event()

        def device(out, since, alive=None):
            app = mock.Mock(state=rig.state)
            app.store.session.side_effect = (lambda _h: next(alive)) if alive else (lambda _h: None if signed_out.is_set() else True)
            h = object.__new__(Handler)
            h.app, h.path, h.wfile = app, f"/api/events?since={since}", out
            h.send_response = h.send_header = h.end_headers = lambda *a: None
            h.events({"id_hash": "x"})

        outs = [io.BytesIO(), io.BytesIO()]
        threads = [threading.Thread(target=device, args=(out, rig.state.titles.seq)) for out in outs]
        for t in threads:
            t.start()
        time.sleep(0.2)
        rig.actions.remove("movie", 12, rig.user)
        time.sleep(0.3)
        signed_out.set()
        for t in threads:
            t.join(2)
        for out in outs:
            text = out.getvalue().decode()
            changed = text.index("event: changed")
            self.assertIn('"key":"movie:12000"', text[changed:])
            after = text.index("event: state", changed)
            snap = json.loads(text[after:].split("data: ", 1)[1].split("\n", 1)[0])
            self.assertNotIn(PADDINGTON_JF, home_ids(snap))
        # A device that was away reconnects with the last change it saw and catches up.
        out = io.BytesIO()
        device(out, 0, iter([True, None]))
        self.assertIn('"key":"movie:12000"', out.getvalue().decode())

    def test_client_drops_its_copies_on_a_change(self):
        js = client_source()
        self.assertIn('es.addEventListener("changed"', js)
        body = js[js.index("  titlesChanged(n) {") : js.index("  updateBadges() {")]
        for copy in ("lists.changed(n)", "delete seasons[id]", "this.sheet.refresh()", "this.runSearch()", "this.sheet.dialog.close()"):
            self.assertIn(copy, body)
        self.assertIn("sheet.title = { kind: full.kind, id: full.id", js)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_change_drops_discovers_marks_and_redraws_it(self):
        """A got/removed title no longer shows the old in-your-library / asked-for mark on Discover."""
        js = client_source()
        body = js[js.index("  titlesChanged(n) {") : js.index("  updateBadges() {")]
        data = Path("web/data.js").read_text()
        script = (
            data + "for (const name of ['discover', 'library', 'upcoming']) lists.set(name, { enabled: true, rows: [] });\n"
            "class App {\n" + body + "}\n"
            "const app = Object.assign(new App(), { current: '/search', searchResults: null, shown: 0,\n"
            "  showResults() { this.shown++; }, render() {}, runSearch() {}, main: { isConnected: true } });\n"
            "app.titlesChanged({ seq: 3, what: 'got', titles: [{ key: 'movie:12000', kind: 'movie', id: 7, ext: 12000 }] });\n"
            "console.log(JSON.stringify({ discover: store.discover, shown: app.shown }));\n"
        )
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), {"discover": None, "shown": 1})


class OutsideChanges(unittest.TestCase):
    """changes made outside Usher (an automatic download, their own pages, watching) reach every view."""

    def test_new_file_in_radarr_drops_library_and_is_announced(self):
        rig = Rig(self)
        rig.poll()
        self.assertEqual(next(m for m in rig.state.library()["movies"] if m["id"] == 11)["state"], "looking")
        seq = rig.state.titles.seq
        next(m for m in rig.m.MOVIES if m["id"] == 11).update(hasFile=True, sizeOnDisk=5e9)
        rig.poll()
        self.assertEqual([(n["what"], n["titles"][0]["id"]) for n in rig.state.titles.since(seq)], [("files", 11)])
        self.assertEqual(next(m for m in rig.state.library()["movies"] if m["id"] == 11)["state"], "ready")

    def test_watched_drops_tidy_up(self):
        rig = Rig(self)
        rig.poll()
        rig.state._cache.set("tidy", {"items": [], "bytes": 0})
        seq = rig.state.titles.seq
        rig.m.JF_ITEMS[:] = [it for it in rig.m.JF_ITEMS if it["Id"] != PADDINGTON_JF]  # played now
        rig.poll()
        self.assertNotIn("tidy", rig.state._cache)
        self.assertEqual([(n["what"], n["titles"][0]["key"]) for n in rig.state.titles.since(seq)], [("played", "movie:12000")])


class SavedMediaServerIds(unittest.TestCase):
    """a request's media server ID is checked again, and follows the title to a new item."""

    def test_stale_id_is_found_again_and_opened_marks_follow(self):
        rig = Rig(self)
        old = "d" * 32
        rig.request("movie", 12, 12000, "Paddington in Peru", jf_id=old)
        rig.store.mark_opened(rig.user["user_id"], old)
        rig.poll()
        self.assertEqual(rig.store.one("SELECT jf_id FROM requests")["jf_id"], PADDINGTON_JF)
        self.assertIn(PADDINGTON_JF, rig.store.opened(rig.user["user_id"]))
        self.assertNotIn(old, rig.store.opened(rig.user["user_id"]))

    def test_moved_title_is_looked_up_again(self):
        rig = Rig(self)
        rig.request("movie", 12, 12000, "Paddington in Peru", jf_id=PADDINGTON_JF)
        rig.poll()
        movie = next(m for m in rig.m.MOVIES if m["id"] == 12)
        movie["path"] = "/srv/media/Family Movies/Paddington in Peru (2024)"  # moved to the family folder
        new = "f" * 32
        rig.m.JF_ITEMS.append({"Id": new, "Type": "Movie", "Name": "Paddington in Peru", "Path": movie["path"] + "/Paddington in Peru.mkv"})
        rig.m.JF_ITEMS[:] = [it for it in rig.m.JF_ITEMS if it["Id"] != PADDINGTON_JF]
        seq = rig.state.titles.seq
        rig.poll()
        self.assertIn("moved", [n["what"] for n in rig.state.titles.since(seq)])
        self.assertEqual(rig.store.one("SELECT jf_id FROM requests")["jf_id"], new)


class OrphanRequests(unittest.TestCase):
    """a request whose title is no longer in Sonarr or Radarr is closed, not searched again."""

    def test_request_for_missing_title_is_closed(self):
        rig = Rig(self)
        rid = rig.request("movie", 99, 99000, "Gone Movie", age=3600 * 5)
        snap = rig.poll()
        self.assertIsNone(rig.store.one("SELECT id FROM requests WHERE id = ?", (rid,)))
        with mock.patch.object(rig.radarr, "send") as send:
            rig.state._research(snap)
        send.assert_not_called()


class EveryHomeRow(unittest.TestCase):
    """Next up, Latest and Ready to watch drop a removed show too, not just Continue watching."""

    def test_removed_show_leaves_every_row(self):
        rig = Rig(self)
        snap = rig.poll()
        self.assertIn("2" * 32, home_ids(snap, "nextup"))
        self.assertIn(SLOW_HORSES_JF, home_ids(snap))
        self.assertIn("Slow Horses", {r["title"] for r in snap["ready"]})
        rig.actions.remove("show", 1, rig.user)
        snap = rig.poll()
        self.assertNotIn("2" * 32, home_ids(snap))
        self.assertNotIn(SLOW_HORSES_JF, home_ids(snap))
        self.assertNotIn("Slow Horses", {r["title"] for r in snap["ready"]})
        self.assertIn("c" * 32, home_ids(snap))
        # Home's cards name their title, so a device can match them to a change
        self.assertEqual({c["key"] for r in snap["home"] for c in r["items"] if c["jf"] == "c" * 32}, {"show:200"})


class VersionedPosters(unittest.TestCase):
    """poster URLs carry the picture's version, so a new one replaces the copy on the device at once."""

    def test_versions_follow_the_picture(self):
        item = {"id": 5, "images": [{"coverType": "poster", "url": "/MediaCover/5/poster.jpg?lastWrite=1"}]}
        first = arr_art("movie", item)
        self.assertTrue(first.startswith("/api/art/radarr/5?v="))
        self.assertEqual(first, arr_art("movie", item))
        item["images"][0]["url"] = "/MediaCover/5/poster.jpg?lastWrite=2"
        self.assertNotEqual(first, arr_art("movie", item))
        self.assertEqual(arr_art("movie", {"id": 5}), f"/api/art/radarr/5?a={ART_EPOCH}")
        self.assertEqual(jf_art("ab" * 16, "t1", "Primary"), f"/api/art/jellyfin/{'ab' * 16}/Primary?v=t1&a={ART_EPOCH}")

    def test_home_and_library_use_versions_and_server_keeps_them_longer(self):
        rig = Rig(self)
        snap = rig.poll()
        card = next(c for r in snap["home"] for c in r["items"] if c["jf"] == PADDINGTON_JF and r["id"] == "resume")
        self.assertTrue(card["art"].endswith(f"?v=p1&a={ART_EPOCH}") or "?v=" in card["fallback"])
        self.assertIn("?v=", next(m for m in rig.state.library()["movies"] if m["id"] == 12)["art"])
        h = object.__new__(Handler)
        h.app = mock.Mock()
        h.app.radarr.image.return_value = (b"img", "image/jpeg")
        sent = {}
        h.send = lambda status, data, ctype=None, headers=None: sent.update(status=status, headers=headers)
        h.path = "/api/art/radarr/12?v=abc"
        h.art("radarr", "12", None)
        self.assertEqual(sent["headers"]["Cache-Control"], "private, max-age=2592000")
        h.path = "/api/art/radarr/12"
        h.art("radarr", "12", None)
        self.assertEqual(sent["headers"]["Cache-Control"], "private, max-age=86400")


class TitleIdentity(unittest.TestCase):
    def test_media_server_items_matched_by_provider_id_or_folder(self):
        t = Titles()
        t.sync("movie", [{"id": 12, "tmdbId": 12000, "title": "P", "path": "/srv/media/Movies/P (2024)"}])
        t.sync("show", [{"id": 2, "tvdbId": 200, "title": "S", "path": "/srv/media/TV Shows/S"}])
        self.assertEqual(t.identify({"Id": "x", "Type": "Movie", "ProviderIds": {"Tmdb": "12000"}})["id"], 12)
        self.assertEqual(t.identify({"Id": "y", "Type": "Episode", "Path": "/srv/media/TV Shows/S/Season 1/e.mkv"})["key"], "show:200")
        self.assertIsNone(t.identify({"Id": "z", "Type": "Movie", "Path": "/srv/media/Movies/Other/o.mkv"}))
        self.assertEqual(ident("show", {"id": 2, "tvdbId": 200})["key"], "show:200")

    def test_device_far_behind_is_told_everything_changed(self):
        t = Titles()
        for n in range(300):
            t.changed("files", [{"key": f"movie:{n}", "kind": "movie", "id": n, "ext": n, "title": ""}])
        self.assertEqual(t.since(1), [{"seq": 300, "what": "changed", "titles": [], "all": True}])
        self.assertEqual(len(t.since(290)), 10)
        self.assertEqual(t.since(300), [])


if __name__ == "__main__":
    unittest.main()
