"""Two-tier (in-memory LRU + SQLite) TTL cache for parsed listings."""

import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import closing, suppress
from dataclasses import dataclass

from resources.lib.parser import Entry

MAX_CACHE_ENTRIES = 500
MAX_MEMORY_CACHE_ENTRIES = 64
CACHE_DB_FILENAME = "listing_cache.sqlite"


@dataclass(frozen=True, slots=True)
class _CacheRecord:
    entries: tuple[Entry, ...]
    fetched_at: float
    etag: str
    last_modified: str


_MEMORY_LOCK = threading.Lock()
_MEMORY_CACHE: OrderedDict[tuple[str, str], _CacheRecord] = OrderedDict()


class CacheError(OSError):
    """Raised when a listing cache cannot be read or written."""


def clear_memory_cache():
    """Clear the process-wide in-memory listing cache."""
    with _MEMORY_LOCK:
        _MEMORY_CACHE.clear()


def _put_memory(key, record):
    with _MEMORY_LOCK:
        _MEMORY_CACHE[key] = record
        _MEMORY_CACHE.move_to_end(key)
        while len(_MEMORY_CACHE) > MAX_MEMORY_CACHE_ENTRIES:
            _MEMORY_CACHE.popitem(last=False)


def _get_memory(key):
    with _MEMORY_LOCK:
        record = _MEMORY_CACHE.get(key)
        if record is not None:
            _MEMORY_CACHE.move_to_end(key)
        return record


def _delete_memory(key):
    with _MEMORY_LOCK:
        _MEMORY_CACHE.pop(key, None)


def _clear_memory_for_directory(directory_abs):
    with _MEMORY_LOCK:
        for cache_key in [key for key in _MEMORY_CACHE if key[0] == directory_abs]:
            del _MEMORY_CACHE[cache_key]


class ListingCache:
    def __init__(self, directory, ttl_seconds=7200):
        self.directory = directory
        self.directory_abs = os.path.abspath(directory)
        self.db_path = os.path.join(directory, CACHE_DB_FILENAME)
        self.ttl_seconds = max(0, int(ttl_seconds))
        self._db_lock = threading.Lock()

    def _key(self, url):
        return (self.directory_abs, url)

    def _connect(self, create=False):
        if create:
            os.makedirs(self.directory, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    url TEXT PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    etag TEXT NOT NULL DEFAULT '',
                    last_modified TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS listings_accessed_at "
                "ON listings(accessed_at)"
            )
        except Exception:
            connection.close()
            raise
        return connection

    def _deserialize(self, payload):
        try:
            raw_entries = json.loads(payload)
        except ValueError as error:
            raise CacheError(f"Listing cache entry is invalid: {error}") from error
        if not isinstance(raw_entries, list):
            raise CacheError("Listing cache entry is invalid")
        try:
            return [Entry.from_dict(item) for item in raw_entries]
        except (TypeError, ValueError) as error:
            raise CacheError(f"Listing cache entry is invalid: {error}") from error

    def get_stale(self, url, now=None):
        """Return (entries, etag, last_modified, is_fresh) for a cached URL."""
        current_time = time.time() if now is None else now
        key = self._key(url)
        memory_record = _get_memory(key)
        if memory_record is not None:
            if self.ttl_seconds == 0:
                self.invalidate(url)
                return None, "", "", False
            is_fresh = current_time - memory_record.fetched_at <= self.ttl_seconds
            return (
                list(memory_record.entries),
                memory_record.etag,
                memory_record.last_modified,
                is_fresh,
            )

        if not os.path.isfile(self.db_path):
            return None, "", "", False

        try:
            with self._db_lock, closing(self._connect(create=False)) as connection:
                row = connection.execute(
                    "SELECT fetched_at, etag, last_modified, payload "
                    "FROM listings WHERE url = ?",
                    (url,),
                ).fetchone()
                if row is None:
                    return None, "", "", False
                fetched_at, etag, last_modified, payload = row
                if (
                    not isinstance(fetched_at, (int, float))
                    or not isinstance(etag, str)
                    or not isinstance(last_modified, str)
                    or not isinstance(payload, str)
                ):
                    raise CacheError("Listing cache entry is invalid")
                entries = self._deserialize(payload)
                if self.ttl_seconds == 0:
                    with connection:
                        connection.execute(
                            "DELETE FROM listings WHERE url = ?",
                            (url,),
                        )
                    return None, "", "", False
        except CacheError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise CacheError(f"Unable to read listing cache: {error}") from error

        _put_memory(
            key,
            _CacheRecord(tuple(entries), float(fetched_at), etag, last_modified),
        )
        is_fresh = current_time - fetched_at <= self.ttl_seconds
        return entries, etag, last_modified, is_fresh

    def get(self, url, now=None):
        entries, _, _, is_fresh = self.get_stale(url, now=now)
        if not is_fresh:
            return None
        return entries

    def get_any(self, url):
        """Return cached entries regardless of TTL, unless caching is disabled."""
        if self.ttl_seconds == 0:
            return None
        entries, _, _, _ = self.get_stale(url)
        return entries

    def put(self, url, entries, now=None, etag="", last_modified=""):
        if self.ttl_seconds == 0:
            return
        current_time = time.time() if now is None else float(now)
        etag_value = etag or ""
        last_modified_value = last_modified or ""
        entries_tuple = tuple(entries)
        try:
            payload = json.dumps(
                [entry.to_dict() for entry in entries_tuple],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise CacheError(f"Unable to write listing cache: {error}") from error

        evicted_urls = []
        try:
            with self._db_lock, closing(self._connect(create=True)) as connection:
                with connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO listings "
                        "(url, fetched_at, accessed_at, etag, last_modified, payload) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            url,
                            current_time,
                            current_time,
                            etag_value,
                            last_modified_value,
                            payload,
                        ),
                    )
                    total = connection.execute(
                        "SELECT COUNT(*) FROM listings"
                    ).fetchone()[0]
                    if total > MAX_CACHE_ENTRIES:
                        overflow = total - MAX_CACHE_ENTRIES
                        evicted_urls = [
                            row[0]
                            for row in connection.execute(
                                "SELECT url FROM listings WHERE url != ? "
                                "ORDER BY accessed_at ASC, rowid ASC LIMIT ?",
                                (url, overflow),
                            ).fetchall()
                        ]
                        if evicted_urls:
                            connection.executemany(
                                "DELETE FROM listings WHERE url = ?",
                                ((evicted,) for evicted in evicted_urls),
                            )
        except (sqlite3.Error, OSError) as error:
            raise CacheError(f"Unable to write listing cache: {error}") from error

        for evicted in evicted_urls:
            _delete_memory(self._key(evicted))
        _put_memory(
            self._key(url),
            _CacheRecord(
                entries_tuple,
                current_time,
                etag_value,
                last_modified_value,
            ),
        )

    def touch(self, url, now=None, etag=None, last_modified=None):
        """Refresh TTL and validators for an existing cached entry on HTTP 304."""
        if self.ttl_seconds == 0:
            return
        current_time = time.time() if now is None else float(now)
        key = self._key(url)
        with _MEMORY_LOCK:
            record = _MEMORY_CACHE.get(key)
            if record is not None:
                _MEMORY_CACHE[key] = _CacheRecord(
                    record.entries,
                    current_time,
                    etag if etag is not None else record.etag,
                    (
                        last_modified
                        if last_modified is not None
                        else record.last_modified
                    ),
                )
                _MEMORY_CACHE.move_to_end(key)

        if not os.path.isfile(self.db_path):
            return
        try:
            with (
                self._db_lock,
                closing(self._connect(create=False)) as connection,
                connection,
            ):
                connection.execute(
                    "UPDATE listings SET "
                    "fetched_at = ?, "
                    "accessed_at = ?, "
                    "etag = COALESCE(?, etag), "
                    "last_modified = COALESCE(?, last_modified) "
                    "WHERE url = ?",
                    (current_time, current_time, etag, last_modified, url),
                )
        except (sqlite3.Error, OSError) as error:
            raise CacheError(f"Unable to refresh listing cache: {error}") from error

    def invalidate(self, url):
        _delete_memory(self._key(url))
        if not os.path.isfile(self.db_path):
            return
        try:
            with (
                self._db_lock,
                closing(self._connect(create=False)) as connection,
                connection,
            ):
                connection.execute("DELETE FROM listings WHERE url = ?", (url,))
        except (sqlite3.Error, OSError) as error:
            raise CacheError(f"Unable to invalidate listing cache: {error}") from error

    def clear(self):
        _clear_memory_for_directory(self.directory_abs)
        if os.path.isfile(self.db_path):
            try:
                with (
                    self._db_lock,
                    closing(self._connect(create=False)) as connection,
                    connection,
                ):
                    connection.execute("DELETE FROM listings")
            except (sqlite3.Error, OSError) as error:
                raise CacheError(f"Unable to clear listing cache: {error}") from error

        if os.path.isdir(self.directory):
            for filename in os.listdir(self.directory):
                if (
                    len(filename) == 69
                    and filename.endswith(".json")
                    and all(char in "0123456789abcdef" for char in filename[:-5])
                ):
                    with suppress(FileNotFoundError, OSError):
                        os.remove(os.path.join(self.directory, filename))
