import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.library import (
    SourceRegistrationError,
    enqueue_manual_sync,
    ensure_library_paths,
    library_paths,
    register_sources,
)


class LibraryPathTests(unittest.TestCase):
    def test_builds_separate_managed_movie_and_tv_roots(self):
        paths = library_paths("C:\\Kodi\\addon_data\\plugin.bdix.dhakaflix")
        self.assertEqual(paths["movies"], os.path.join(paths["state"], "Movies"))
        self.assertEqual(paths["tv"], os.path.join(paths["state"], "TV Shows"))

    def test_ensures_source_paths_exist_as_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = library_paths(directory)

            ensure_library_paths(paths)

            self.assertTrue(os.path.isdir(paths["movies"]))
            self.assertTrue(os.path.isdir(paths["tv"]))

    def test_rejects_source_path_that_is_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            movies_path = os.path.join(directory, "Movies")
            Path(movies_path).write_text("not a directory", encoding="utf-8")
            paths = {
                "movies": movies_path,
                "tv": os.path.join(directory, "TV Shows"),
            }

            with self.assertRaises(SourceRegistrationError):
                ensure_library_paths(paths)

    def test_registration_preserves_existing_sources_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            sources_path = os.path.join(directory, "sources.xml")
            Path(sources_path).write_text(
                "<sources><video><default>Existing</default><source>"
                "<name>Existing</name><path>/media/existing</path>"
                "</source></video><music><source><name>Music</name>"
                "<path>/music</path></source></music></sources>",
                encoding="utf-8",
            )
            movies = os.path.join(directory, "library", "Movies")
            tv = os.path.join(directory, "library", "TV Shows")

            self.assertTrue(register_sources(sources_path, movies, tv))
            self.assertTrue(Path(sources_path + ".dhakaflix.bak").is_file())
            root = ET.parse(sources_path).getroot()
            names = [
                source.findtext("name") for source in root.findall("./video/source")
            ]
            self.assertEqual(
                names,
                ["Existing", "DhakaFlix Movies", "DhakaFlix TV Shows"],
            )
            self.assertIsNotNone(root.find("./music/source[name='Music']"))
            self.assertFalse(register_sources(sources_path, movies, tv))

    def test_registration_rejects_malformed_xml_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            sources_path = os.path.join(directory, "sources.xml")
            Path(sources_path).write_text("<sources>", encoding="utf-8")
            with self.assertRaises(SourceRegistrationError):
                register_sources(
                    sources_path,
                    os.path.join(directory, "Movies"),
                    os.path.join(directory, "TV Shows"),
                )
            self.assertEqual(
                Path(sources_path).read_text(encoding="utf-8"), "<sources>"
            )

    def test_manual_sync_request_is_idempotently_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(enqueue_manual_sync(directory))
            self.assertFalse(enqueue_manual_sync(directory))
            self.assertTrue(Path(directory, "sync.request").is_file())


if __name__ == "__main__":
    unittest.main()
