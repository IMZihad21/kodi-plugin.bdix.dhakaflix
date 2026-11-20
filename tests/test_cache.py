import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.cache import (
    MAX_CACHE_ENTRIES,
    CacheError,
    ListingCache,
    clear_memory_cache,
)
from resources.lib.parser import Entry


class ListingCacheTests(unittest.TestCase):
    def setUp(self):
        clear_memory_cache()
        self.addCleanup(clear_memory_cache)

    def test_round_trip_and_ttl_expiration_across_memory_and_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=60)
            entries = [
                Entry(
                    "Film.mkv",
                    "http://media.example/Film.mkv",
                    "video",
                    "2026-01-01 12:34",
                    123456,
                )
            ]
            cache.put("http://media.example/", entries, now=100)
            self.assertEqual(cache.get("http://media.example/", now=150), entries)

            clear_memory_cache()
            self.assertEqual(cache.get("http://media.example/", now=150), entries)
            self.assertIsNone(cache.get("http://media.example/", now=161))

    def test_get_stale_and_touch_preserve_entries_and_validators(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=60)
            url = "http://media.example/"
            entries = [Entry("Film.mkv", "http://media.example/Film.mkv", "video")]
            cache.put(
                url,
                entries,
                now=100,
                etag='"v1"',
                last_modified="Mon, 01 Jan 2026 00:00:00 GMT",
            )

            stale, etag, last_modified, fresh = cache.get_stale(url, now=200)
            self.assertFalse(fresh)
            self.assertEqual(stale, entries)
            self.assertEqual(etag, '"v1"')
            self.assertEqual(last_modified, "Mon, 01 Jan 2026 00:00:00 GMT")
            self.assertEqual(cache.get_any(url), entries)

            cache.touch(url, now=200, etag='"v2"')
            clear_memory_cache()
            refreshed, new_etag, _, fresh_after = cache.get_stale(url, now=220)
            self.assertTrue(fresh_after)
            self.assertEqual(refreshed, entries)
            self.assertEqual(new_etag, '"v2"')

    def test_invalidate_and_clear_remove_entries_and_legacy_json(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=60)
            url1 = "http://media.example/a/"
            url2 = "http://media.example/b/"
            cache.put(url1, [Entry("A.mkv", url1 + "A.mkv", "video")], now=100)
            cache.put(url2, [Entry("B.mkv", url2 + "B.mkv", "video")], now=100)

            legacy_hash = hashlib.sha256(url1.encode("utf-8")).hexdigest()
            legacy_file = Path(directory) / f"{legacy_hash}.json"
            legacy_file.write_text("{}", encoding="utf-8")
            unrelated_file = Path(directory) / "user-notes.json"
            unrelated_file.write_text("{}", encoding="utf-8")

            cache.invalidate(url1)
            self.assertIsNone(cache.get(url1, now=110))
            self.assertIsNotNone(cache.get(url2, now=110))

            cache.clear()
            self.assertIsNone(cache.get(url2, now=110))
            self.assertFalse(legacy_file.exists())
            self.assertTrue(unrelated_file.exists())

    def test_eviction_caps_cache_at_max_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=3600)
            for idx in range(MAX_CACHE_ENTRIES + 5):
                url = f"http://media.example/{idx}/"
                cache.put(
                    url,
                    [Entry(f"V{idx}.mkv", f"{url}V{idx}.mkv", "video")],
                    now=1000 + idx,
                )
            clear_memory_cache()
            self.assertIsNone(cache.get("http://media.example/0/", now=2000))
            self.assertIsNotNone(
                cache.get(
                    f"http://media.example/{MAX_CACHE_ENTRIES + 4}/",
                    now=2000,
                )
            )

    def test_corrupt_cache_and_invalid_entry_raise_cache_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory)
            Path(cache.db_path).write_bytes(b"not sqlite")
            with self.assertRaises(CacheError):
                cache.get("http://media.example/")

        clear_memory_cache()
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory)
            url = "http://media.example/"
            cache.put(url, [], now=100)
            with closing(sqlite3.connect(cache.db_path)) as connection, connection:
                connection.execute(
                    "UPDATE listings SET payload = ? WHERE url = ?",
                    (json.dumps([None]), url),
                )
            clear_memory_cache()
            with self.assertRaisesRegex(CacheError, "Invalid cached listing entry"):
                cache.get(url, now=100)

    def test_zero_ttl_disables_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=0)
            cache.put("http://media.example/", [])
            self.assertIsNone(cache.get("http://media.example/"))
            self.assertIsNone(cache.get_any("http://media.example/"))


if __name__ == "__main__":
    unittest.main()
