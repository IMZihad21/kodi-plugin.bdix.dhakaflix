"""SQLite-backed directory and media inventory for library synchronization."""

import sqlite3
import unicodedata


class LibraryIndex:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        try:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS directories (
                    url TEXT PRIMARY KEY,
                    modified TEXT,
                    parent_url TEXT
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    url TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    path_key TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    source_directory TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS media_source_directory "
                "ON media(source_directory)"
            )
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError(f"Unsupported library index version: {version}")
            if version == 1:
                with self.connection:
                    self.connection.execute(
                        "ALTER TABLE media ADD COLUMN path_key TEXT NOT NULL DEFAULT ''"
                    )
                    cursor = self.connection.execute("SELECT url, path FROM media")
                    while rows := cursor.fetchmany(1000):
                        self.connection.executemany(
                            "UPDATE media SET path_key = ? WHERE url = ?",
                            ((_path_key(path), url) for url, path in rows),
                        )
                    self.connection.execute("PRAGMA user_version = 2")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS media_path_key ON media(path_key)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS sync_metadata "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS sync_ignored_media "
                "(url TEXT PRIMARY KEY)"
            )
            self.needs_migration = version == 0
        except Exception:
            self.connection.close()
            raise

    def media_entries(self):
        return {
            row[0]: {
                "path": row[1],
                "media_type": row[2],
                "source_directory": row[3],
            }
            for row in self.connection.execute(
                "SELECT url, path, media_type, source_directory FROM media"
            )
        }

    def media_paths(self):
        return {row[0] for row in self.connection.execute("SELECT path FROM media")}

    def media_entries_for_directories(self, directory_urls):
        return self._media_entries_for_scope("source_directory", directory_urls)

    def media_entries_for_urls(self, media_urls):
        return self._media_entries_for_scope("url", media_urls)

    def _media_entries_for_scope(self, column, values):
        if column not in ("source_directory", "url"):
            raise ValueError(f"Unsupported media lookup column: {column}")
        values = set(values)
        if not values:
            return {}
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS sync_scope (value TEXT PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM sync_scope")
        self.connection.executemany(
            "INSERT OR IGNORE INTO sync_scope(value) VALUES (?)",
            ((value,) for value in values),
        )
        return {
            row[0]: {
                "path": row[1],
                "media_type": row[2],
                "source_directory": row[3],
            }
            for row in self.connection.execute(
                "SELECT media.url, media.path, media.media_type, "
                "media.source_directory FROM media "
                f"JOIN sync_scope ON media.{column} = sync_scope.value"
            )
        }

    def directory_entries(self):
        return {
            row[0]: {"modified": row[1], "parent_url": row[2]}
            for row in self.connection.execute(
                "SELECT url, modified, parent_url FROM directories"
            )
        }

    def exclude_media_urls(self, urls):
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS sync_ignored_media (url TEXT PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM sync_ignored_media")
        self.connection.executemany(
            "INSERT OR IGNORE INTO sync_ignored_media(url) VALUES (?)",
            ((url,) for url in urls),
        )

    def path_key_in_use(self, path_key):
        return (
            self.connection.execute(
                "SELECT 1 FROM media "
                "WHERE path_key = ? AND NOT EXISTS ("
                "SELECT 1 FROM sync_ignored_media "
                "WHERE sync_ignored_media.url = media.url"
                ") LIMIT 1",
                (path_key,),
            ).fetchone()
            is not None
        )

    def media_url_for_path_key(self, path_key):
        row = self.connection.execute(
            "SELECT url FROM media WHERE path_key = ? LIMIT 1",
            (path_key,),
        ).fetchone()
        return row[0] if row else None

    def counts(self):
        counts = dict(
            self.connection.execute(
                "SELECT media_type, COUNT(*) FROM media GROUP BY media_type"
            )
        )
        return counts.get("movie", 0), counts.get("series", 0)

    def import_manifest(self, entries, directories):
        with self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO media "
                "(url, path, path_key, media_type, source_directory) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        url,
                        entry["path"],
                        _path_key(entry["path"]),
                        entry["media_type"],
                        entry.get("source_directory", ""),
                    )
                    for url, entry in entries.items()
                    if isinstance(entry, dict)
                    and isinstance(entry.get("path"), str)
                    and isinstance(entry.get("media_type"), str)
                ),
            )
            self.connection.executemany(
                "INSERT OR REPLACE INTO directories "
                "(url, modified, parent_url) VALUES (?, ?, ?)",
                (
                    (
                        url,
                        directory.get("modified"),
                        directory.get("parent_url"),
                    )
                    for url, directory in directories.items()
                    if isinstance(directory, dict)
                ),
            )
            self.connection.execute("PRAGMA user_version = 2")
        self.needs_migration = False

    def save_snapshot(
        self,
        entries,
        directories,
        previous_directories,
        full_crawl,
        visited,
        transaction_id=None,
    ):
        with self.connection:
            if full_crawl:
                self.connection.execute("DELETE FROM media")
                self.connection.execute("DELETE FROM directories")
            else:
                deleted_directories = previous_directories.keys() - directories.keys()
                affected_directories = set(visited) | deleted_directories
                self.connection.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS sync_scope "
                    "(value TEXT PRIMARY KEY)"
                )
                self.connection.execute("DELETE FROM sync_scope")
                self.connection.executemany(
                    "INSERT OR IGNORE INTO sync_scope(value) VALUES (?)",
                    ((url,) for url in affected_directories),
                )
                self.connection.execute(
                    "DELETE FROM media WHERE source_directory IN "
                    "(SELECT value FROM sync_scope)"
                )
                self.connection.executemany(
                    "DELETE FROM directories WHERE url = ?",
                    ((url,) for url in deleted_directories),
                )
            self.connection.executemany(
                "INSERT OR REPLACE INTO media "
                "(url, path, path_key, media_type, source_directory) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        url,
                        entry["path"],
                        _path_key(entry["path"]),
                        entry["media_type"],
                        entry["source_directory"],
                    )
                    for url, entry in entries.items()
                ),
            )
            if not full_crawl:
                directory_updates = (
                    (url, directory)
                    for url, directory in directories.items()
                    if previous_directories.get(url) != directory
                )
            else:
                directory_updates = directories.items()
            self.connection.executemany(
                "INSERT OR REPLACE INTO directories "
                "(url, modified, parent_url) VALUES (?, ?, ?)",
                (
                    (url, directory["modified"], directory["parent_url"])
                    for url, directory in directory_updates
                ),
            )
            if transaction_id:
                self.connection.execute(
                    "INSERT OR REPLACE INTO sync_metadata(key, value) VALUES (?, ?)",
                    ("last_transaction_id", transaction_id),
                )
            self.connection.execute("PRAGMA user_version = 2")
        self.needs_migration = False

    def is_transaction_committed(self, transaction_id):
        row = self.connection.execute(
            "SELECT value FROM sync_metadata WHERE key = ?",
            ("last_transaction_id",),
        ).fetchone()
        return row is not None and row[0] == transaction_id

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def _path_key(path):
    return unicodedata.normalize("NFD", path).casefold()
