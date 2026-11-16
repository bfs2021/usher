"""Jellyfin matching with a year in the title, Status showing search-site problems, searching again."""

import time
from datetime import datetime, timezone
import unittest
from unittest import mock

from usher.clients import Jellyfin
from usher.state import State
from usher.state.helpers import sites_not_responding

RESEARCH_FIRST = 3600  # retries.first_search_after_minutes default


class FindTests(unittest.TestCase):
    def jf(self, search_items, all_items):
        j = Jellyfin("http://x", "k")
        j.user_id = lambda: "u"
        j.get = lambda path, **p: {"Items": search_items if "searchTerm" in p else all_items}
        return j

    def test_year_dropped_and_ids_matched(self):
        seen = {}
        j = Jellyfin("http://x", "k")
        j.user_id = lambda: "u"

        def get(path, **p):
            seen.setdefault("terms", []).append(p.get("searchTerm"))
            return {"Items": [{"Id": "A", "ProviderIds": {"Tvdb": "465690"}}]}

        j.get = get
        self.assertEqual(j.find("show", "Scrubs (2026)", {"Tvdb": 465690}), "A")
        self.assertEqual(seen["terms"][0], "Scrubs")

    def test_tmdb_or_path_fallback(self):
        j = self.jf([], [{"Id": "B", "ProviderIds": {"Tmdb": "295778"}}])
        self.assertEqual(j.find("show", "X", {"Tvdb": 1, "Tmdb": 295778}), "B")
        j = self.jf([], [{"Id": "C", "ProviderIds": {}, "Path": "/srv/media/Movies/Y (2020)/Y.mkv"}])
        self.assertEqual(j.find("movie", "Y", {"Tmdb": 9}, "/srv/media/Movies/Y (2020)"), "C")
        self.assertIsNone(j.find("movie", "Y", {"Tmdb": 9}, "/srv/media/Movies/Z"))


def make_state():
    settings = mock.Mock(media_path="/srv/media", low_space_gb=30, recycle_dir="/nonexistent", family_tv_folder="", family_movie_folder="")
    st = State(settings, mock.Mock(name="sonarr"), mock.Mock(name="radarr"), mock.Mock(), mock.Mock(), mock.Mock())
    st.sonarr.name, st.radarr.name = "sonarr", "radarr"
    return st


class StatusTests(unittest.TestCase):
    def test_radarr_indexer_problem_shows_under_search(self):
        st = make_state()
        r = st._raw
        r["ok"].update(transmission=True, prowlarr=True, sonarr=True, radarr=True, jellyfin_up=True)
        r["health"] = {
            "Search": [],
            "TV": [],
            "Movies": [{"type": "warning", "source": "IndexerLongTermStatusCheck", "message": "Indexers unavailable: YTS (Prowlarr)"}],
        }
        parts = {p["name"]: p for p in st._status(r)["parts"]}
        self.assertFalse(parts["Search"]["ok"])
        self.assertTrue(parts["Movies"]["ok"])

    def _search_part(self, sites_down, health):
        st = make_state()
        r = st._raw
        r["ok"].update(transmission=True, prowlarr=True, sonarr=True, radarr=True, jellyfin_up=True)
        r["sites_down"] = sites_down
        r["health"] = {"Search": health, "TV": [], "Movies": []}
        return {p["name"]: p for p in st._status(r)["parts"]}["Search"]

    def test_no_search_warning_when_every_site_works(self):
        stale = [
            {"type": "warning", "source": "IndexerProxyStatusCheck", "message": "Indexer proxies unavailable due to failures: Helper"},
            {"type": "warning", "source": "IndexerStatusCheck", "message": "Indexers unavailable due to failures: YTS"},
        ]
        part = self._search_part([], stale)
        self.assertTrue(part["ok"])
        self.assertEqual(part["text"], "Working")

    def test_search_warning_names_the_failing_site(self):
        part = self._search_part(["YTS"], [])
        self.assertFalse(part["ok"])
        self.assertIn("YTS", part["text"])

    def test_sites_not_responding_reads_prowlarrs_site_state(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        indexers = [{"id": 1, "name": "A", "enable": True}, {"id": 2, "name": "B", "enable": True}, {"id": 3, "name": "C", "enable": False}]
        statuses = [
            {"indexerId": 1, "disabledTill": "2025-12-31T00:00:00Z"},
            {"indexerId": 2, "disabledTill": "2026-01-02T00:00:00Z"},
            {"indexerId": 3, "disabledTill": "2026-01-02T00:00:00Z"},
        ]
        self.assertEqual(sites_not_responding(indexers, statuses, now), ["B"])


class ResearchTests(unittest.TestCase):
    def test_searches_after_an_hour_then_waits(self):
        st = make_state()
        now = time.time()
        req = {"id": 1, "kind": "show", "arr_id": 18, "title": "T", "created": now - RESEARCH_FIRST - 5, "searched": None}
        st.store = mock.Mock()
        st.store.open_requests.return_value = [req]
        st.sonarr.send.return_value = {"id": 99}
        st._research({"requests": [{"rid": 1, "searching": True}]})
        st.sonarr.send.assert_called_once_with("POST", "command", {"name": "SeriesSearch", "seriesId": 18})
        req["searched"] = int(now)
        st.sonarr.send.reset_mock()
        st._research({"requests": [{"rid": 1, "searching": True}]})
        st.sonarr.send.assert_not_called()

    def test_not_searched_unless_waiting(self):
        st = make_state()
        st.store = mock.Mock()
        st.store.open_requests.return_value = [{"id": 1, "kind": "movie", "arr_id": 3, "title": "M", "created": 0, "searched": None}]
        st._research({"requests": [{"rid": 1}]})
        st.radarr.send.assert_not_called()
