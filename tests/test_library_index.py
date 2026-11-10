import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.library_index import LibraryIndex, _path_key


class LibraryIndexTests(unittest.TestCase):
    def test_incremental_snapshot_replaces_changed_directories_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "library.sqlite")
            first_entries = {
                "http://media.test/old.mkv": {
                    "path": "Movies/Old/Old.strm",
                    "media_type": "movie",
                    "source_directory": "http://media.test/changed/",
                },
                "http://media.test/kept.mkv": {
                    "path": "Movies/Kept/Kept.strm",
                    "media_type": "movie",
                    "source_directory": "http://media.test/unchanged/",
                },
            }
            directories = {
                "http://media.test/changed/": {
                    "modified": "2026-01-01 00:00",
                    "parent_url": None,
                },
                "http://media.test/unchanged/": {
                    "modified": "2026-01-01 00:00",
                    "parent_url": None,
                },
            }

            with LibraryIndex(path) as index:
                index.save_snapshot(first_entries, directories, {}, True, set())
                updated_entries = {
                    "http://media.test/new.mkv": {
                        "path": "Movies/New/New.strm",
                        "media_type": "movie",
                        "source_directory": "http://media.test/changed/",
                    }
                }
                updated_directories = dict(directories)
                updated_directories["http://media.test/changed/"] = {
                    "modified": "2026-01-02 00:00",
                    "parent_url": None,
                }
                index.save_snapshot(
                    updated_entries,
                    updated_directories,
                    directories,
                    False,
                    {"http://media.test/changed/"},
                )

                entries = index.media_entries()
                self.assertEqual(
                    set(entries), set(updated_entries) | {"http://media.test/kept.mkv"}
                )
                self.assertTrue(
                    index.path_key_in_use(_path_key("Movies/Kept/Kept.strm"))
                )
                self.assertEqual(index.counts(), (2, 0))

    def test_version_one_database_gets_a_path_key_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "library.sqlite")
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE directories (url TEXT PRIMARY KEY, modified TEXT, "
                "parent_url TEXT)"
            )
            connection.execute(
                "CREATE TABLE media (url TEXT PRIMARY KEY, path TEXT NOT NULL, "
                "media_type TEXT NOT NULL, source_directory TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO media VALUES (?, ?, ?, ?)",
                (
                    "http://media.test/movie.mkv",
                    "Movies/Film/Film.strm",
                    "movie",
                    "http://media.test/movies/",
                ),
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            connection.close()

            with LibraryIndex(path) as index:
                self.assertFalse(index.needs_migration)
                self.assertTrue(
                    index.path_key_in_use(_path_key("movies/film/film.strm"))
                )
                self.assertEqual(
                    index.media_entries()["http://media.test/movie.mkv"]["path"],
                    "Movies/Film/Film.strm",
                )
                self.assertEqual(
                    index.connection.execute("PRAGMA user_version").fetchone()[0],
                    2,
                )


if __name__ == "__main__":
    unittest.main()
