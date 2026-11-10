import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.library_index import LibraryIndex
from resources.lib.parser import Entry
from resources.lib.sync import (
    FULL_CRAWL_INTERVAL,
    MAX_COMPONENT_BYTES,
    MAX_LIBRARY_PATH_LENGTH,
    Media,
    SyncCancelled,
    _atomic_write,
    _limit_output_path,
    _media_output_path,
    _path_length,
    _safe_component,
    crawl_catalog,
    discover_categories,
    run_sync,
)


def _read_index(library_dir):
    with LibraryIndex(os.path.join(library_dir, "library.sqlite")) as index:
        return {
            "entries": index.media_entries(),
            "directories": index.directory_entries(),
        }


def _set_directory_modified(client, url, modified):
    client.pages[url] = [
        Entry(entry.name, entry.url, entry.kind, modified)
        if entry.kind == "folder"
        else entry
        for entry in client.pages[url]
    ]


class _FakeClient:
    origin_url = "http://127.0.0.1:8000/"

    def __init__(self, fail_tv=False, movie_name="Film (1995).mkv"):
        self.fail_tv = fail_tv
        self.movie_name = movie_name
        movie_folder = self.movie_name.rsplit(".", 1)[0] + " 720p"
        movie_folder_url = movie_folder.replace(" ", "%20")
        self.pages = {
            "http://127.0.0.1:8000/movies/": [
                Entry("1995", "http://127.0.0.1:8000/movies/1995/", "folder")
            ],
            "http://127.0.0.1:8000/movies/1995/": [
                Entry(
                    movie_folder,
                    f"http://127.0.0.1:8000/movies/1995/{movie_folder_url}/",
                    "folder",
                )
            ],
            f"http://127.0.0.1:8000/movies/1995/{movie_folder_url}/": [
                Entry(
                    self.movie_name,
                    "http://127.0.0.1:8000/media/"
                    + self.movie_name.replace(" ", "%20"),
                    "video",
                )
            ],
            "http://127.0.0.1:8000/series/": [
                Entry("A-L", "http://127.0.0.1:8000/series/A-L/", "folder")
            ],
            "http://127.0.0.1:8000/series/A-L/": [
                Entry(
                    "Example Show (TV Series 2020)",
                    "http://127.0.0.1:8000/series/A-L/show/",
                    "folder",
                )
            ],
            "http://127.0.0.1:8000/series/A-L/show/": [
                Entry(
                    "Season 1",
                    "http://127.0.0.1:8000/series/A-L/show/Season%201/",
                    "folder",
                )
            ],
            "http://127.0.0.1:8000/series/A-L/show/Season%201/": [
                Entry(
                    "Example.Show.S01E01.1080p.mkv",
                    "http://127.0.0.1:8000/media/Example.Show.S01E01.1080p.mkv",
                    "video",
                )
            ],
        }

    def fetch_html(self, url):
        html_text = (
            '<a href="http://127.0.0.1:8000/movies/">English Movies</a>'
            '<a href="http://127.0.0.1:8000/series/">TV &amp; WEB Series</a>'
            '<a href="http://127.0.0.1:8000/games/">PC Games</a>'
            '<a href="https://public.example/movies/">Movies mirror</a>'
        )
        return html_text, url

    def listing(self, url, cache, refresh=False):
        if self.fail_tv and url == "http://127.0.0.1:8000/series/":
            from resources.lib.portal import PortalError

            raise PortalError("node unavailable")
        return self.pages.get(url, [])


class CategoryDiscoveryTests(unittest.TestCase):
    def test_discovers_movie_and_series_links_and_excludes_other_content(self):
        categories = discover_categories(
            '<a href="/Movies/">English Movies</a>'
            '<a href="/Series/">TV &amp; WEB Series</a>'
            '<a href="/Games/">PC Games</a>'
            '<a href="/Documentary/">Documentary</a>'
            '<a href="/WWE/">WWE</a>'
            '<a href="/Awards/">Awards &amp; TV Shows</a>'
            '<a href="https://example.com/Movies/">Movies mirror</a>',
            "http://127.0.0.1:8000/",
        )
        self.assertEqual(
            [(category.label, category.media_type) for category in categories],
            [("English Movies", "movie"), ("TV & WEB Series", "series")],
        )

    def test_crawls_folder_trees_and_classifies_numbered_episodes(self):
        categories, media, page_count, errors, _, _ = crawl_catalog(
            _FakeClient(), "http://127.0.0.1:8000/"
        )
        self.assertEqual(len(categories), 2)
        self.assertEqual(page_count, 7)
        self.assertFalse(errors)
        self.assertEqual(
            [(entry.name, entry.media_type) for entry in media],
            [
                ("Example.Show.S01E01.1080p.mkv", "series"),
                ("Film (1995).mkv", "movie"),
            ],
        )

    def test_media_type_selection_limits_crawl_to_movies(self):
        client = _FakeClient()
        categories, media, _, errors, directories, _ = crawl_catalog(
            client,
            "http://127.0.0.1:8000/",
            media_types={"movie"},
        )

        self.assertFalse(errors)
        self.assertEqual([(category.media_type) for category in categories], ["movie"])
        self.assertEqual({item.media_type for item in media}, {"movie"})
        self.assertNotIn("http://127.0.0.1:8000/series/", directories)

    def test_skips_excluded_series_subtrees_and_media(self):
        class TrackingClient(_FakeClient):
            def __init__(self):
                super().__init__()
                self.requested = []
                self.pages["http://127.0.0.1:8000/series/A-L/"].extend(
                    [
                        Entry(
                            "Documentary Series",
                            "http://127.0.0.1:8000/series/A-L/documentary/",
                            "folder",
                        ),
                        Entry(
                            "WWE",
                            "http://127.0.0.1:8000/series/A-L/wwe/",
                            "folder",
                        ),
                        Entry(
                            "AEW",
                            "http://127.0.0.1:8000/series/A-L/aew/",
                            "folder",
                        ),
                        Entry(
                            "Award Winners",
                            "http://127.0.0.1:8000/series/A-L/award-winners/",
                            "folder",
                        ),
                        Entry(
                            "WWE Event.mkv",
                            "http://127.0.0.1/media/WWE.Event.mkv",
                            "video",
                        ),
                    ]
                )

            def listing(self, url, cache, refresh=False):
                self.requested.append(url)
                return super().listing(url, cache, refresh)

        client = TrackingClient()
        _, media, page_count, errors, _, _ = crawl_catalog(
            client,
            "http://127.0.0.1:8000/",
        )
        self.assertEqual(page_count, 8)
        self.assertFalse(errors)
        self.assertFalse(any("WWE" in entry.name for entry in media))
        self.assertIn(
            "http://127.0.0.1:8000/series/A-L/award-winners/",
            client.requested,
        )
        self.assertNotIn(
            "http://127.0.0.1:8000/series/A-L/documentary/",
            client.requested,
        )
        self.assertNotIn(
            "http://127.0.0.1:8000/series/A-L/wwe/",
            client.requested,
        )
        self.assertNotIn(
            "http://127.0.0.1:8000/series/A-L/aew/",
            client.requested,
        )

    def test_crawl_has_no_page_count_limit(self):
        page_total = 20001

        class ManyPagesClient(_FakeClient):
            def listing(self, url, cache, refresh=False):
                if url == "http://127.0.0.1:8000/movies/":
                    return [
                        Entry(
                            f"Collection {number}",
                            f"http://127.0.0.1:8000/movies/collection-{number}/",
                            "folder",
                        )
                        for number in range(page_total)
                    ]
                return super().listing(url, cache, refresh)

        _, _, page_count, errors, _, _ = crawl_catalog(
            ManyPagesClient(), "http://127.0.0.1:8000/"
        )
        self.assertEqual(page_count, page_total + 5)
        self.assertFalse(errors)

    def test_crawl_has_no_folder_depth_limit(self):
        depth = 40

        class DeepTreeClient(_FakeClient):
            def listing(self, url, cache, refresh=False):
                prefix = "http://127.0.0.1:8000/movies/"
                if url.startswith(prefix):
                    suffix = url[len(prefix) :].strip("/")
                    current_depth = 0 if not suffix else len(suffix.split("/"))
                    if current_depth < depth:
                        return [
                            Entry(
                                f"Level {current_depth + 1}",
                                f"{prefix}{suffix + '/' if suffix else ''}"
                                f"level-{current_depth + 1}/",
                                "folder",
                            )
                        ]
                    return [
                        Entry(
                            "Deep Film.mkv",
                            "http://127.0.0.1:8000/media/deep-film.mkv",
                            "video",
                        )
                    ]
                return super().listing(url, cache, refresh)

        _, media, _, errors, _, _ = crawl_catalog(
            DeepTreeClient(), "http://127.0.0.1:8000/"
        )
        self.assertTrue(any(item.name == "Deep Film.mkv" for item in media))
        self.assertFalse(errors)

    def test_crawl_discovers_all_media_in_tree(self):
        class ManyVideosClient(_FakeClient):
            def listing(self, url, cache, refresh=False):
                if url == "http://127.0.0.1:8000/movies/":
                    return [
                        Entry(
                            f"Film {number}.mkv",
                            f"http://127.0.0.1:8000/media/Film{number}.mkv",
                            "video",
                        )
                        for number in range(25)
                    ]
                return super().listing(url, cache, refresh)

        _, media, _, errors, _, _ = crawl_catalog(
            ManyVideosClient(), "http://127.0.0.1:8000/"
        )
        self.assertEqual(len(media), 26)
        self.assertFalse(any("media limit" in error for error in errors))

    def test_distinguishes_movie_and_episode_paths(self):
        movie_path, movie_type = _media_output_path(
            Media(
                "Film (1995).mkv",
                "http://127.0.0.1/Film.mkv",
                "movie",
                "English Movies",
                ("(1995)", "Film (1995) 720p"),
            )
        )
        episode_path, episode_type = _media_output_path(
            Media(
                "Example.Show.S01E01.1080p.mkv",
                "http://127.0.0.1/Example.Show.S01E01.1080p.mkv",
                "series",
                "TV Series",
                ("A-L", "Example Show (TV Series 2020)", "Season 1"),
            )
        )
        self.assertTrue(movie_path.startswith(os.path.join("Movies", "English Movies")))
        self.assertTrue(movie_path.endswith(".strm"))
        self.assertEqual(movie_type, "movie")
        self.assertIn(
            os.path.join("TV Shows", "Example Show", "Season 1"), episode_path
        )
        self.assertIn("S01E01", episode_path)
        self.assertEqual(episode_type, "series")

    def test_routes_series_without_episode_codes_to_tv_shows(self):
        path, media_type = _media_output_path(
            Media(
                "Season Special.mkv",
                "http://127.0.0.1/Season.Special.mkv",
                "series",
                "TV & WEB Series",
                ("A-L", "Example Show"),
            )
        )
        self.assertEqual(media_type, "series")
        self.assertEqual(
            path.split(os.sep)[:2],
            ["TV Shows", "Example Show"],
        )
        self.assertTrue(path.endswith("Season Special.strm"))

    def test_normalizes_season_folders_with_suffixes(self):
        path, media_type = _media_output_path(
            Media(
                "Special.mkv",
                "http://127.0.0.1/Special.mkv",
                "series",
                "TV & WEB Series",
                ("A-L", "Example Show", "Season 1 (Hindi Language)"),
            )
        )
        self.assertEqual(media_type, "series")
        self.assertIn(
            os.path.join("TV Shows", "Example Show", "Season 1"),
            path,
        )

    def test_keeps_distinct_movie_urls_at_the_same_category_path(self):
        first = Media(
            "Film (1995).mkv",
            "http://127.0.0.1/first.mkv",
            "movie",
            "English Movies",
            ("(1995)", "Film (1995) 720p"),
        )
        second = Media(
            "Film (1995).mkv",
            "http://127.0.0.1/second.mkv",
            "movie",
            "English Movies",
            ("(1995)", "Film (1995) 720p"),
        )
        first_path, _ = _media_output_path(first)
        second_path, _ = _media_output_path(second, disambiguate=True)
        self.assertNotEqual(first_path, second_path)

    def test_decodes_escaped_url_filename_for_library_title(self):
        media = Media(
            "The Film (1995).mkv",
            "http://127.0.0.1/1995/The%20Film%20%281995%29.mkv",
            "movie",
            "Movies",
            ("1995",),
        )
        path, _ = _media_output_path(media)
        self.assertIn("The Film (1995).strm", path)

    def test_long_managed_paths_are_shortened_with_stable_names(self):
        library_dir = os.path.join(
            tempfile.gettempdir(),
            "x" * 120,
        )
        media = Media(
            "Shin-Chan Movies Collection (1993) [1080p].mkv",
            "http://127.0.0.1/Shin-Chan.mkv",
            "movie",
            "Animation Movies",
            ("Animation Movies", "Shin-Chan Movies Collection (1993) [1080p]"),
        )
        relative_path, _ = _media_output_path(media)

        shortened = _limit_output_path(library_dir, relative_path)
        absolute_path = os.path.abspath(os.path.join(library_dir, shortened))

        self.assertLessEqual(_path_length(absolute_path), MAX_LIBRARY_PATH_LENGTH)
        self.assertTrue(shortened.endswith(".strm"))
        self.assertEqual(shortened, _limit_output_path(library_dir, relative_path))

    def test_long_canonical_library_path_is_used_for_shortening(self):
        with tempfile.TemporaryDirectory() as directory:
            library_dir = os.path.join(directory, "profile-alias")
            canonical_library_dir = os.path.join(
                directory, "profile-" + "canonical-" * 10
            )
            relative_path = os.path.join(
                "Movies",
                "Animation Movies",
                "Doraemon-Nobita Movies Collection (2008) [1080p]",
                "Doraemon-Nobita Movies Collection (2008) [1080p].strm",
            )
            original_realpath = os.path.realpath

            def resolve_alias(path):
                absolute_path = os.path.abspath(path)
                try:
                    suffix = os.path.relpath(absolute_path, library_dir)
                except ValueError:
                    return original_realpath(path)
                if suffix == os.curdir:
                    return canonical_library_dir
                if suffix == os.pardir or suffix.startswith(os.pardir + os.sep):
                    return original_realpath(path)
                return os.path.join(canonical_library_dir, suffix)

            with mock.patch(
                "resources.lib.sync.os.path.realpath",
                side_effect=resolve_alias,
            ):
                shortened = _limit_output_path(library_dir, relative_path)

            resolved_output = os.path.join(canonical_library_dir, shortened)
            self.assertLessEqual(
                _path_length(os.path.abspath(resolved_output)),
                MAX_LIBRARY_PATH_LENGTH,
            )

    def test_long_unicode_components_fit_filesystem_byte_limits(self):
        component = _safe_component("影" * 120, "Fallback")

        self.assertLessEqual(len(component.encode("utf-8")), MAX_COMPONENT_BYTES)
        expected_hash = hashlib.sha256(("影" * 120).encode("utf-8")).hexdigest()[:8]
        self.assertTrue(component.endswith("~" + expected_hash))

    def test_paths_that_collide_on_case_insensitive_filesystems_are_disambiguated(
        self,
    ):
        class CaseCollisionClient(_FakeClient):
            def listing(self, url, cache, refresh=False):
                if url == "http://127.0.0.1:8000/movies/":
                    return [
                        Entry(
                            "Film (2020)",
                            "http://127.0.0.1:8000/movies/upper/",
                            "folder",
                        ),
                        Entry(
                            "film (2020)",
                            "http://127.0.0.1:8000/movies/lower/",
                            "folder",
                        ),
                    ]
                if url.endswith("/upper/"):
                    return [
                        Entry(
                            "Film.mkv",
                            "http://127.0.0.1:8000/media/upper.mkv",
                            "video",
                        )
                    ]
                if url.endswith("/lower/"):
                    return [
                        Entry(
                            "Film.mkv",
                            "http://127.0.0.1:8000/media/lower.mkv",
                            "video",
                        )
                    ]
                if url == "http://127.0.0.1:8000/series/":
                    return []
                return super().listing(url, cache, refresh)

        with tempfile.TemporaryDirectory() as library_dir:
            result = run_sync(CaseCollisionClient(), library_dir)
            self.assertTrue(result.complete)
            entries = _read_index(library_dir)["entries"]
            paths = [entry["path"] for entry in entries.values()]

        self.assertEqual(len(paths), 2)
        self.assertEqual(len({path.casefold() for path in paths}), 2)


class SyncRunTests(unittest.TestCase):
    def test_incremental_sync_skips_unchanged_subtrees_and_updates_changed_ones(self):
        first_client = _FakeClient()
        _set_directory_modified(
            first_client, "http://127.0.0.1:8000/movies/", "2026-01-01 10:00"
        )
        _set_directory_modified(
            first_client,
            "http://127.0.0.1:8000/movies/1995/",
            "2026-01-01 10:00",
        )
        _set_directory_modified(
            first_client, "http://127.0.0.1:8000/series/", "2026-01-01 10:00"
        )
        _set_directory_modified(
            first_client,
            "http://127.0.0.1:8000/series/A-L/",
            "2026-01-01 10:00",
        )
        _set_directory_modified(
            first_client,
            "http://127.0.0.1:8000/series/A-L/show/",
            "2026-01-01 10:00",
        )
        _set_directory_modified(
            first_client,
            "http://127.0.0.1:8000/series/A-L/show/Season%201/",
            "2026-01-01 10:00",
        )

        with tempfile.TemporaryDirectory() as library_dir:
            first_result = run_sync(first_client, library_dir)
            self.assertTrue(first_result.complete)

            second_client = _FakeClient(movie_name="New Film (1995).mkv")
            _set_directory_modified(
                second_client, "http://127.0.0.1:8000/movies/", "2026-01-02 10:00"
            )
            _set_directory_modified(
                second_client,
                "http://127.0.0.1:8000/movies/1995/",
                "2026-01-02 10:00",
            )
            _set_directory_modified(
                second_client, "http://127.0.0.1:8000/series/", "2026-01-01 10:00"
            )
            with mock.patch.object(
                LibraryIndex,
                "media_entries",
                side_effect=AssertionError("incremental sync loaded the full index"),
            ):
                result = run_sync(second_client, library_dir)

            self.assertTrue(result.complete)
            self.assertEqual(result.page_count, 4)
            self.assertEqual(result.pruned_count, 1)
            entries = _read_index(library_dir)["entries"]
            self.assertIn(
                "http://127.0.0.1:8000/media/New%20Film%20(1995).mkv",
                entries,
            )
            self.assertIn(
                "http://127.0.0.1:8000/media/Example.Show.S01E01.1080p.mkv",
                entries,
            )

    def test_full_crawl_runs_after_seven_day_audit_interval(self):
        client = _FakeClient()
        for url in client.pages:
            _set_directory_modified(client, url, "2026-01-01 10:00")

        with tempfile.TemporaryDirectory() as library_dir:
            run_sync(client, library_dir)
            state_path = os.path.join(library_dir, "state.json")
            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)
            state["last_full_crawl"] = time.time() - FULL_CRAWL_INTERVAL
            with open(state_path, "w", encoding="utf-8") as target:
                json.dump(state, target)

            result = run_sync(client, library_dir)

            self.assertTrue(result.complete)
            self.assertEqual(result.page_count, 7)
            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)
            self.assertTrue(state["last_result"]["full_crawl"])

    def test_atomic_write_supports_a_relative_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            try:
                os.chdir(directory)
                _atomic_write("relative.strm", "http://example.test/video\n")
                with open("relative.strm", encoding="utf-8") as source:
                    self.assertEqual(source.read(), "http://example.test/video\n")
            finally:
                os.chdir(previous_directory)

    def test_atomic_write_cleans_temporary_file_after_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "entry.strm")
            with open(target, "w", encoding="utf-8") as destination:
                destination.write("existing\n")

            with mock.patch(  # noqa: SIM117
                "resources.lib.sync.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    r"replace failed.*destination_length=.*temporary_length="
                    r".*parent_exists=True.*temporary_exists=True",
                ):
                    _atomic_write(target, "updated\n")

            with open(target, encoding="utf-8") as destination:
                self.assertEqual(destination.read(), "existing\n")
            self.assertEqual(
                [
                    name
                    for name in os.listdir(directory)
                    if name.startswith(".dhakaflix-")
                ],
                [],
            )

    def test_long_nested_output_path_is_bounded_and_atomically_written(self):
        with tempfile.TemporaryDirectory() as directory:
            library_dir = os.path.join(directory, "profile-" + "p" * 70)
            relative_path = os.path.join(
                "Movies",
                "Animation " + "Category " * 9,
                "Collection " + "Folder " * 13,
                "Collection " + "Movie " * 13 + ".strm",
            )
            shortened_path = _limit_output_path(library_dir, relative_path)
            destination = os.path.abspath(os.path.join(library_dir, shortened_path))
            self.assertLessEqual(_path_length(destination), MAX_LIBRARY_PATH_LENGTH)

            original_replace = os.replace
            observed = {}

            def checked_replace(temporary, target):
                observed["temporary"] = temporary
                self.assertEqual(target, destination)
                self.assertTrue(os.path.isdir(os.path.dirname(target)))
                self.assertTrue(os.path.isfile(temporary))
                if os.name == "nt":
                    self.assertLessEqual(_path_length(temporary), 259)
                return original_replace(temporary, target)

            with mock.patch(
                "resources.lib.sync.os.replace",
                side_effect=checked_replace,
            ):
                _atomic_write(destination, "http://example.test/video\n")

            self.assertTrue(os.path.isfile(destination))
            self.assertIn("temporary", observed)
            with open(destination, encoding="utf-8") as source:
                self.assertEqual(source.read(), "http://example.test/video\n")

    def test_failed_sync_preserves_existing_library_files_and_index(self):
        with tempfile.TemporaryDirectory() as library_dir:
            run_sync(_FakeClient(), library_dir)
            manifest_before = _read_index(library_dir)
            existing_path = next(iter(manifest_before["entries"].values()))["path"]
            output_path = os.path.join(library_dir, existing_path)
            with open(output_path, encoding="utf-8") as source:
                content_before = source.read()

            original_replace = os.replace

            def fail_media_replace(temporary, target):
                if target.endswith(".strm"):
                    raise OSError("replace failed")
                return original_replace(temporary, target)

            with mock.patch(  # noqa: SIM117
                "resources.lib.sync.os.replace",
                side_effect=fail_media_replace,
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    run_sync(
                        _FakeClient(movie_name="Changed Film (1995).mkv"),
                        library_dir,
                    )

            self.assertEqual(_read_index(library_dir), manifest_before)
            with open(output_path, encoding="utf-8") as source:
                self.assertEqual(source.read(), content_before)

    def test_complete_run_writes_streams_and_prunes_stale_managed_files(self):
        with tempfile.TemporaryDirectory() as library_dir:
            first = run_sync(_FakeClient(), library_dir)
            self.assertTrue(first.complete)
            self.assertEqual((first.movie_count, first.episode_count), (1, 1))
            old_manifest = _read_index(library_dir)
            old_movie_path = old_manifest["entries"][
                "http://127.0.0.1:8000/media/Film%20(1995).mkv"
            ]["path"]
            second = run_sync(
                _FakeClient(movie_name="New Film (1995).mkv"), library_dir
            )
            self.assertTrue(second.complete)
            self.assertEqual(second.pruned_count, 1)
            self.assertFalse(os.path.exists(os.path.join(library_dir, old_movie_path)))
            self.assertFalse(
                os.path.isdir(
                    os.path.join(library_dir, os.path.dirname(old_movie_path))
                )
            )
            self.assertTrue(os.path.isdir(os.path.join(library_dir, "Movies")))

    def test_sync_supports_a_symlinked_library_root(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            actual_library = Path(temporary_root) / "library"
            actual_library.mkdir()
            library_alias = Path(temporary_root) / "library-alias"
            try:
                library_alias.symlink_to(actual_library, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Directory symlinks are unavailable: {error}")

            result = run_sync(_FakeClient(), str(library_alias))

            self.assertTrue(result.complete)
            self.assertTrue((actual_library / "library.sqlite").is_file())
            self.assertEqual(len(list(actual_library.rglob("*.strm"))), 2)

    def test_disabling_series_sync_prunes_only_existing_series_entries(self):
        with tempfile.TemporaryDirectory() as library_dir:
            initial = run_sync(_FakeClient(), library_dir)
            self.assertTrue(initial.complete)
            self.assertEqual((initial.movie_count, initial.episode_count), (1, 1))

            movies_only = run_sync(
                _FakeClient(),
                library_dir,
                media_types={"movie"},
            )

            self.assertTrue(movies_only.complete)
            self.assertEqual((movies_only.movie_count, movies_only.episode_count), (1, 0))
            self.assertEqual(movies_only.pruned_count, 1)
            self.assertEqual(len(list(Path(library_dir).rglob("*.strm"))), 1)

            series_only = run_sync(
                _FakeClient(),
                library_dir,
                media_types={"series"},
            )

            self.assertTrue(series_only.complete)
            self.assertEqual((series_only.movie_count, series_only.episode_count), (0, 1))
            self.assertEqual(series_only.pruned_count, 1)
            self.assertEqual(len(list(Path(library_dir).rglob("*.strm"))), 1)

    def test_existing_layout_is_reclassified_on_next_scheduled_full_crawl(self):
        with tempfile.TemporaryDirectory() as library_dir:
            client = _FakeClient()
            for url in client.pages:
                _set_directory_modified(client, url, "2026-01-01 10:00")
            run_sync(client, library_dir)
            entries = _read_index(library_dir)["entries"]
            media_url = "http://127.0.0.1:8000/media/Example.Show.S01E01.1080p.mkv"
            previous_path = entries[media_url]["path"]
            legacy_path = os.path.join(
                "Movies",
                "TV & WEB Series",
                "Example Show",
                "Example.Show.S01E01.strm",
            )
            legacy_output = os.path.join(library_dir, legacy_path)
            os.makedirs(os.path.dirname(legacy_output), exist_ok=True)
            with open(legacy_output, "w", encoding="utf-8") as target:
                target.write(media_url + "\n")
            index = LibraryIndex(os.path.join(library_dir, "library.sqlite"))
            with index, index.connection:
                index.connection.execute(
                    "UPDATE media SET path = ? WHERE url = ?",
                    (legacy_path, media_url),
                )

            state_path = os.path.join(library_dir, "state.json")
            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)
            state["last_full_crawl"] = time.time()
            with open(state_path, "w", encoding="utf-8") as target:
                json.dump(state, target)

            incremental_result = run_sync(client, library_dir)
            self.assertTrue(incremental_result.complete)
            self.assertEqual(incremental_result.pruned_count, 0)
            self.assertEqual(
                _read_index(library_dir)["entries"][media_url]["path"],
                legacy_path,
            )
            self.assertTrue(os.path.exists(legacy_output))

            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)
            state["last_full_crawl"] = time.time() - FULL_CRAWL_INTERVAL
            with open(state_path, "w", encoding="utf-8") as target:
                json.dump(state, target)

            result = run_sync(client, library_dir)

            self.assertTrue(result.complete)
            self.assertEqual(result.pruned_count, 1)
            self.assertFalse(os.path.exists(legacy_output))
            self.assertFalse(os.path.isdir(os.path.dirname(legacy_output)))
            self.assertTrue(os.path.isfile(os.path.join(library_dir, previous_path)))
            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)
            self.assertTrue(state["last_result"]["full_crawl"])

    def test_unchanged_stream_files_are_not_rewritten_and_missing_files_are_restored(
        self,
    ):
        with tempfile.TemporaryDirectory() as library_dir:
            first = run_sync(_FakeClient(), library_dir)
            self.assertTrue(first.complete)
            entries = _read_index(library_dir)["entries"]
            missing_path = os.path.join(
                library_dir,
                entries["http://127.0.0.1:8000/media/Film%20(1995).mkv"]["path"],
            )
            os.remove(missing_path)

            original_replace = os.replace
            replaced_streams = []

            def observe_replace(temporary, target):
                if target.endswith(".strm"):
                    replaced_streams.append(target)
                return original_replace(temporary, target)

            with mock.patch(
                "resources.lib.sync.os.replace",
                side_effect=observe_replace,
            ):
                result = run_sync(_FakeClient(), library_dir)

            self.assertTrue(result.complete)
            expected_suffix = os.path.normcase(
                entries["http://127.0.0.1:8000/media/Film%20(1995).mkv"]["path"]
            )
            self.assertEqual(len(replaced_streams), 1)
            self.assertTrue(
                os.path.normcase(replaced_streams[0]).endswith(expected_suffix)
            )
            self.assertTrue(os.path.isfile(missing_path))

    def test_changed_url_replaces_same_path_even_when_content_size_is_unchanged(self):
        class UpdatedUrlClient(_FakeClient):
            allowed_origins = frozenset(
                {
                    ("http", "127.0.0.1", 8000),
                    ("http", "127.0.0.2", 8000),
                }
            )

            def listing(self, url, cache, refresh=False):
                entries = super().listing(url, cache, refresh)
                if url.endswith("/Film%20(1995)%20720p/"):
                    return [
                        Entry(
                            "Film (1995).mkv",
                            "http://127.0.0.2:8000/media/Film%20(1995).mkv",
                            "video",
                        )
                    ]
                return entries

        with tempfile.TemporaryDirectory() as library_dir:
            first = run_sync(_FakeClient(), library_dir)
            self.assertTrue(first.complete)
            old_entry = _read_index(library_dir)["entries"][
                "http://127.0.0.1:8000/media/Film%20(1995).mkv"
            ]
            stream_path = os.path.join(library_dir, old_entry["path"])

            second = run_sync(UpdatedUrlClient(), library_dir)

            self.assertTrue(second.complete)
            with open(stream_path, encoding="utf-8") as source:
                self.assertEqual(
                    source.read(),
                    "http://127.0.0.2:8000/media/Film%20(1995).mkv\n",
                )

    def test_partial_crawl_keeps_files_and_last_success(self):
        with tempfile.TemporaryDirectory() as library_dir:
            complete = run_sync(_FakeClient(), library_dir)
            manifest_before = _read_index(library_dir)
            with open(
                os.path.join(library_dir, "state.json"), encoding="utf-8"
            ) as source:
                state_before = json.load(source)

            partial = run_sync(
                _FakeClient(fail_tv=True, movie_name="Changed.mkv"), library_dir
            )
            self.assertFalse(partial.complete)
            self.assertEqual(_read_index(library_dir), manifest_before)
            with open(
                os.path.join(library_dir, "state.json"), encoding="utf-8"
            ) as source:
                state_after = json.load(source)
            self.assertEqual(state_after["last_success"], state_before["last_success"])
            self.assertTrue(complete.complete)

    def test_empty_media_crawl_cannot_prune_existing_manifest(self):
        class EmptyClient(_FakeClient):
            def listing(self, url, cache, refresh=False):
                if url in (
                    "http://127.0.0.1:8000/movies/",
                    "http://127.0.0.1:8000/series/",
                ):
                    return []
                return super().listing(url, cache, refresh)

        with tempfile.TemporaryDirectory() as library_dir:
            run_sync(_FakeClient(), library_dir)
            result = run_sync(EmptyClient(), library_dir)
            self.assertFalse(result.complete)
            self.assertEqual(result.pruned_count, 0)

    def test_sync_writes_all_discovered_media(self):
        class ManyVideosClient(_FakeClient):
            def listing(self, url, cache, refresh=False):
                if url == "http://127.0.0.1:8000/movies/":
                    return [
                        Entry(
                            f"Film {number}.mkv",
                            f"http://127.0.0.1:8000/media/Film{number}.mkv",
                            "video",
                        )
                        for number in range(25)
                    ]
                if url == "http://127.0.0.1:8000/series/":
                    return []
                return super().listing(url, cache, refresh)

        with tempfile.TemporaryDirectory() as library_dir:
            result = run_sync(ManyVideosClient(), library_dir)
            self.assertTrue(result.complete)
            self.assertEqual(result.movie_count, 25)
            self.assertEqual(result.episode_count, 0)
            manifest = _read_index(library_dir)
            self.assertEqual(len(manifest["entries"]), 25)
            for entry in manifest["entries"].values():
                self.assertTrue(
                    os.path.isfile(os.path.join(library_dir, entry["path"]))
                )

    def test_complete_run_migrates_a_legacy_unbounded_manifest_path(self):
        show_name = "Example Show " + "Long " * 18
        media_url = "http://127.0.0.1:8000/media/Example.Show.S01E01.mkv"
        client = _FakeClient()
        client.pages["http://127.0.0.1:8000/series/A-L/"] = [
            Entry(show_name, "http://127.0.0.1:8000/series/long-show/", "folder")
        ]
        client.pages["http://127.0.0.1:8000/series/long-show/"] = [
            Entry(
                "Season 1",
                "http://127.0.0.1:8000/series/long-show/Season%201/",
                "folder",
            )
        ]
        client.pages["http://127.0.0.1:8000/series/long-show/Season%201/"] = [
            Entry(
                "Example.Show.S01E01.mkv",
                media_url,
                "video",
            )
        ]
        client.pages["http://127.0.0.1:8000/movies/"] = []

        with tempfile.TemporaryDirectory() as temporary_dir:
            library_dir = os.path.join(temporary_dir, "profile-" + "x" * 100)
            os.makedirs(library_dir)
            media = Media(
                "Example.Show.S01E01.mkv",
                media_url,
                "series",
                "TV & WEB Series",
                ("A-L", show_name, "Season 1"),
            )
            legacy_path, media_type = _media_output_path(media)
            self.assertGreater(
                _path_length(os.path.abspath(os.path.join(library_dir, legacy_path))),
                MAX_LIBRARY_PATH_LENGTH,
            )
            manifest_path = os.path.join(library_dir, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as target:
                json.dump(
                    {
                        "entries": {
                            media_url: {
                                "path": legacy_path,
                                "media_type": media_type,
                            }
                        }
                    },
                    target,
                )

            result = run_sync(client, library_dir)

            self.assertTrue(result.complete)
            self.assertFalse(os.path.exists(manifest_path))
            entry = _read_index(library_dir)["entries"][media_url]
            self.assertLessEqual(
                _path_length(os.path.abspath(os.path.join(library_dir, entry["path"]))),
                MAX_LIBRARY_PATH_LENGTH,
            )
            self.assertTrue(os.path.isfile(os.path.join(library_dir, entry["path"])))

    def test_cancellation_does_not_publish_a_successful_sync(self):
        with tempfile.TemporaryDirectory() as library_dir:
            with self.assertRaises(SyncCancelled):
                run_sync(
                    _FakeClient(),
                    library_dir,
                    cancelled=lambda: True,
                )
            with open(
                os.path.join(library_dir, "state.json"), encoding="utf-8"
            ) as source:
                state = json.load(source)
            self.assertNotIn("last_success", state)
            self.assertIn("cancelled", state["last_error"].casefold())


if __name__ == "__main__":
    unittest.main()
