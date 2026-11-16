"""Picked for you / Because you watched rows, from the shared account's watch history."""

import unittest
from unittest import mock

from usher.discover import Discover


class RecTests(unittest.TestCase):
    def test_personal_rows(self):
        state = mock.Mock()
        state.jf.watched.return_value = [("show", 1, "Scrubs"), ("movie", 2, "Dune")]
        state.jf.library_tmdb.return_value = {("show", 10)}
        d = Discover(mock.Mock(), state)

        def recs(kind, tmdb):
            base = {1: [("show", 10), ("show", 11), ("movie", 20)], 2: [("movie", 20), ("show", 1)]}[tmdb]
            return [{"kind": k, "tmdb": t, "art": "x"} for k, t in base]

        d._recs = recs
        jobs = {rid: (name, fn) for rid, name, fn in d._personal_jobs()}
        self.assertEqual(jobs["because-show-1"][0], "Because you watched Scrubs")
        mine = [(c["kind"], c["tmdb"]) for c in jobs["foryou"][1]()]
        self.assertEqual(mine[0], ("movie", 20))  # recommended by both
        self.assertNotIn(("show", 10), mine)  # already in Jellyfin
        self.assertNotIn(("show", 1), mine)  # already watched

    def test_no_history_no_rows(self):
        state = mock.Mock()
        state.jf.watched.return_value = []
        self.assertEqual(Discover(mock.Mock(), state)._personal_jobs(), [])
