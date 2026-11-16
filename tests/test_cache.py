"""The one server cache (usher/cache.py) and that a title change clears every cache holding that title."""

import unittest
from unittest import mock

from usher.cache import MISSING, TTLCache
from usher.diagnostics import Diagnostics
from usher.discover import Discover
from usher.titles import Titles, ident


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class TTLCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cache = TTLCache(10, 3, clock=self.clock)

    def test_expires_after_its_age_limit(self):
        self.cache.set("a", 1)
        self.clock.now = 9
        self.assertEqual(self.cache.get("a"), 1)
        self.clock.now = 10
        self.assertIsNone(self.cache.get("a"))
        self.assertNotIn("a", self.cache)

    def test_each_read_can_ask_for_its_own_age_limit(self):
        self.cache.set("a", 1)
        self.clock.now = 20
        self.assertEqual(self.cache.get("a", ttl=60), 1)
        self.assertEqual(self.cache.get("a", ttl=5, default="old"), "old")

    def test_a_remembered_miss_is_not_a_miss(self):
        self.cache.set("a", None)
        self.assertIsNone(self.cache.get("a", MISSING))
        self.assertIs(self.cache.get("b", MISSING), MISSING)

    def test_size_cap_drops_the_least_recently_used(self):
        for key in "abc":
            self.cache.set(key, key)
        self.cache.get("a")  # a is now the freshest
        self.cache.set("d", "d")
        self.assertEqual(len(self.cache), 3)
        self.assertNotIn("b", self.cache)
        self.assertIn("a", self.cache)

    def test_forget_drops_matching_entries_only(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.assertEqual(self.cache.forget(lambda key, value: value == 2), 1)
        self.assertIn("a", self.cache)
        self.assertNotIn("b", self.cache)

    def test_peek_returns_old_values_with_their_age(self):
        self.cache.set("a", 1)
        self.clock.now = 50
        self.assertEqual(self.cache.peek("a"), (1, 50))
        self.assertIsNone(self.cache.peek("b"))


class TitleChangesClearCaches(unittest.TestCase):
    def test_clears_everything_or_only_what_holds_the_title(self):
        titles = Titles()
        everything, some = TTLCache(60, 10), TTLCache(60, 10)
        titles.clears(everything)
        titles.clears(some, lambda key, value, t: key == t["id"])
        for cache in (everything, some):
            cache.set(1, "x")
            cache.set(2, "y")
        titles.changed("removed", [ident("movie", {"id": 1, "tmdbId": 11, "title": "A"})])
        self.assertEqual((len(everything), len(some)), (0, 1))
        self.assertIn(2, some)

    def test_poster_fallbacks_and_remembered_misses_are_looked_up_again(self):
        state = mock.Mock()
        state.titles = Titles()
        with mock.patch.dict("os.environ", {"TMDB_TOKEN": "t"}):
            d = Discover(mock.Mock(), state)
        d._fallback.set(("radarr", 9), None)
        d._fallback.set(("radarr", 10), "https://example.invalid/p.jpg")
        state.titles.changed("added", [ident("movie", {"id": 9, "tmdbId": 90, "title": "Nine"})])
        self.assertNotIn(("radarr", 9), d._fallback)
        self.assertIn(("radarr", 10), d._fallback)

    def test_status_details_are_dropped_with_the_rest(self):
        titles = Titles()
        diag = Diagnostics(*[mock.Mock() for _ in range(5)])
        titles.clears(diag._cache)
        diag._cache.set("details", {"TV": {}})
        titles.changed("removed", [ident("show", {"id": 3, "tvdbId": 30, "title": "Three"})])
        self.assertNotIn("details", diag._cache)
