"""Upcoming items say whether the episode is here as `here`, never `have`.

The sheet opened from Upcoming merges the item over the show's library record,
where `have` is the episode count; a boolean `have` used to show "true of 8".
"""

import unittest
from unittest import mock

from usher.state import State
from tests.webjs import client_source


class UpcomingFieldTests(unittest.TestCase):
    def test_here_not_have(self):
        st = State.__new__(State)
        st.sonarr = mock.Mock()
        st.radarr = mock.Mock()
        st.sonarr.get.return_value = [
            {
                "seriesId": 7,
                "seasonNumber": 2,
                "episodeNumber": 3,
                "title": "Ep",
                "airDateUtc": "2026-11-07T10:00:00Z",
                "hasFile": True,
                "series": {"title": "Show"},
            }
        ]
        st.radarr.get.return_value = []
        st._cached = lambda key, ttl, build: build()
        items = st.upcoming()["items"]
        self.assertEqual(len(items), 1)
        self.assertIs(items[0]["here"], True)
        self.assertNotIn("have", items[0])


class SheetMergeTests(unittest.TestCase):
    def test_library_counts_win(self):
        js = client_source()
        self.assertIn('for (const k of ["have", "total", "seasons", "size"]) full[k] = lib[k];', js)
        self.assertNotIn("item.have ?", js)
        self.assertNotIn("it.have ?", js)


if __name__ == "__main__":
    unittest.main()
