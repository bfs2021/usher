"""One small in-memory cache for the server: expiry, a size cap, least-recently-used eviction and forgetting.

Every server-side cache (search lookups, TMDb answers, poster fallbacks, Library/Upcoming/Tidy up, Status details)
uses this, so expiry and eviction behave the same everywhere and a cache that holds title data can be wired to the
title change notices the same way (Titles.clears in titles.py). Misses can be remembered too: store None and ask
with a default that no stored value can equal (MISSING).
"""

import threading
import time
from collections import OrderedDict

MISSING = object()  # "not cached", as opposed to a cached None (a remembered miss)


class TTLCache:
    def __init__(self, ttl, size, clock=time.monotonic):
        self.ttl, self.size, self._clock = ttl, size, clock
        self._entries: OrderedDict = OrderedDict()  # key -> (when stored, value), least recently used first
        self._lock = threading.Lock()

    def get(self, key, default=None, ttl=None):
        """The value stored under key if it is younger than ttl (the cache's own when not given), else default.
        An expired entry stays until it is replaced or evicted, so a longer ttl asked for later still finds it."""
        with self._lock:
            hit = self._entries.get(key)
            if hit is None or self._clock() - hit[0] >= (self.ttl if ttl is None else ttl):
                return default
            self._entries.move_to_end(key)
            return hit[1]

    def peek(self, key):
        """(value, seconds old) however old it is, or None. For serving an old value while a new one is built."""
        with self._lock:
            hit = self._entries.get(key)
            return None if hit is None else (hit[1], self._clock() - hit[0])

    def set(self, key, value):
        with self._lock:
            self._entries[key] = (self._clock(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)

    def forget(self, match):
        """Drop every entry for which match(key, value) is true. Returns how many went."""
        with self._lock:
            gone = [k for k, (_, v) in self._entries.items() if match(k, v)]
            for k in gone:
                del self._entries[k]
            return len(gone)

    def pop(self, key, default=None):
        with self._lock:
            return self._entries.pop(key, default)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def __contains__(self, key):
        return self.get(key, MISSING) is not MISSING

    def __len__(self):
        with self._lock:
            return len(self._entries)
