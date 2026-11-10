import json
import sys
import tempfile
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.cache import CacheError, ListingCache
from resources.lib.parser import Entry


class ListingCacheTests(unittest.TestCase):
    def test_round_trip_and_ttl_expiration(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=60)
            entries = [
                Entry(
                    "Film.mkv",
                    "http://media.example/Film.mkv",
                    "video",
                    "2026-01-01 12:34",
                )
            ]
            cache.put("http://media.example/", entries, now=100)
            self.assertEqual(cache.get("http://media.example/", now=150), entries)
            self.assertIsNone(cache.get("http://media.example/", now=161))

    def test_corrupt_cache_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory)
            path = cache._path("http://media.example/")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("{invalid", encoding="utf-8")
            with self.assertRaises(CacheError):
                cache.get("http://media.example/")

    def test_invalid_cached_entry_type_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory)
            url = "http://media.example/"
            path = cache._path(url)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(
                json.dumps({"url": url, "timestamp": 100, "entries": [None]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CacheError, "Invalid cached listing entry"):
                cache.get(url, now=100)

    def test_zero_ttl_disables_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=0)
            cache.put("http://media.example/", [])
            self.assertIsNone(cache.get("http://media.example/"))


if __name__ == "__main__":
    unittest.main()
