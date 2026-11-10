"""Small, atomic, TTL-based persistent cache for parsed listings."""

import json
import os
import tempfile
import time
from contextlib import suppress
from hashlib import sha256

from resources.lib.parser import Entry

MAX_CACHE_ENTRIES = 500


class CacheError(OSError):
    """Raised when a listing cache cannot be read or written."""


class ListingCache:
    def __init__(self, directory, ttl_seconds=7200):
        self.directory = directory
        self.ttl_seconds = max(0, int(ttl_seconds))

    def _path(self, url):
        digest = sha256(url.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, digest + ".json")

    def get(self, url, now=None):
        path = self._path(url)
        try:
            with open(path, encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise CacheError(f"Unable to read listing cache: {error}") from error

        if not isinstance(payload, dict) or payload.get("url") != url:
            raise CacheError("Listing cache entry is invalid")
        timestamp = payload.get("timestamp")
        entries = payload.get("entries")
        if not isinstance(timestamp, (int, float)) or not isinstance(entries, list):
            raise CacheError("Listing cache entry is invalid")
        current_time = time.time() if now is None else now
        if self.ttl_seconds == 0 or current_time - timestamp > self.ttl_seconds:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise CacheError(f"Unable to expire listing cache: {error}") from error
            return None
        try:
            return [Entry.from_dict(entry) for entry in entries]
        except (TypeError, ValueError) as error:
            raise CacheError(f"Listing cache entry is invalid: {error}") from error

    def put(self, url, entries, now=None):
        if self.ttl_seconds == 0:
            return
        try:
            os.makedirs(self.directory, exist_ok=True)
            payload = {
                "url": url,
                "timestamp": time.time() if now is None else now,
                "entries": [entry.to_dict() for entry in entries],
            }
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=".listing-", suffix=".tmp", dir=self.directory
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as cache_file:
                    json.dump(
                        payload, cache_file, ensure_ascii=False, separators=(",", ":")
                    )
                    cache_file.flush()
                    os.fsync(cache_file.fileno())
                destination = self._path(url)
                os.replace(temporary_path, destination)
                self._prune(destination)
            except Exception:
                with suppress(FileNotFoundError):
                    os.remove(temporary_path)
                raise
        except (OSError, TypeError, ValueError) as error:
            raise CacheError(f"Unable to write listing cache: {error}") from error

    def _prune(self, keep_path):
        cache_paths = [
            os.path.join(self.directory, name)
            for name in os.listdir(self.directory)
            if name.endswith(".json")
        ]
        if len(cache_paths) <= MAX_CACHE_ENTRIES:
            return
        cache_paths.sort(key=os.path.getmtime)
        remaining = len(cache_paths)
        for path in cache_paths:
            if remaining <= MAX_CACHE_ENTRIES:
                break
            if path == keep_path:
                continue
            with suppress(FileNotFoundError):
                os.remove(path)
            remaining -= 1
