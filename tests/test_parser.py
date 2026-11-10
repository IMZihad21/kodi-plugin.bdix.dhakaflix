import sys
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.parser import classify_file, parse_directory
from resources.lib.urls import URLValidationError, normalize_http_url


class DirectoryParserTests(unittest.TestCase):
    def test_parses_h5ai_entries_and_sorts_folders_first(self):
        source = """
        <div id="fallback"><table>
          <tr><td class="fb-i"><img src="folder.png"></td>
          <td class="fb-n"><a href="Year%20%282026%29/">Year (2026)</a></td>
          <td class="fb-d">2026-01-01</td><td class="fb-s"></td></tr>
          <tr><td class="fb-i"><img src="file.png"></td>
          <td class="fb-n"><a href="Film বাংলা.mkv">Film বাংলা.mkv</a></td>
          <td class="fb-d"></td><td class="fb-s"></td></tr>
          <tr><td class="fb-i"><img src="folder-parent.png"></td>
          <td class="fb-n"><a href="../">Parent Directory</a></td>
          <td class="fb-d"></td><td class="fb-s"></td></tr>
          <tr><td class="fb-i"><img src="folder.png"></td>
          <td class="fb-n"><a href="Games/">Games</a></td>
          <td class="fb-d"></td><td class="fb-s"></td></tr>
        </table></div>
        """

        entries = parse_directory(
            source,
            "http://172.16.50.7/movies/",
            excluded_keywords=("games",),
        )

        self.assertEqual(
            [(entry.name, entry.kind) for entry in entries],
            [("Year (2026)", "folder"), ("Film বাংলা.mkv", "video")],
        )
        self.assertEqual(
            entries[0].url,
            "http://172.16.50.7/movies/Year%20%282026%29/",
        )
        self.assertEqual(entries[0].modified, "2026-01-01")

    def test_generic_anchor_fallback_and_duplicate_links(self):
        source = """
        <a href="Drama/">Drama</a>
        <a href="Drama/">Drama duplicate</a>
        <a href="Movie.mp4">Movie.mp4</a>
        <a href="javascript:alert(1)">not a link</a>
        <a href="#top">jump</a>
        """
        entries = parse_directory(source, "https://media.example/catalog/")
        self.assertEqual([entry.kind for entry in entries], ["folder", "video"])
        self.assertEqual(len(entries), 2)

    def test_classifies_video_images_and_subtitles(self):
        self.assertEqual(classify_file("episode.MKV"), "video")
        self.assertEqual(classify_file("cover.webp"), "image")
        self.assertEqual(classify_file("captions.srt"), "subtitle")
        self.assertEqual(classify_file("readme.txt"), "other")

    def test_ignores_unsupported_links(self):
        source = (
            '<a href="file:///etc/passwd">secret</a>'
            '<a href="javascript:alert(1)">invalid</a>'
        )
        self.assertEqual(parse_directory(source, "https://media.example/"), [])


class URLValidationTests(unittest.TestCase):
    def test_normalizes_spaces_and_drops_fragment(self):
        self.assertEqual(
            normalize_http_url("HTTP://Example.COM/A film.mkv#part"),
            "http://example.com/A%20film.mkv",
        )

    def test_rejects_non_http_and_credentials(self):
        for value in (
            "file:///movie.mkv",
            "javascript:alert(1)",
            "http://user:pw@example.com/",
        ):
            with self.subTest(value=value), self.assertRaises(URLValidationError):
                normalize_http_url(value)

    def test_rejects_invalid_hostname(self):
        with self.assertRaises(URLValidationError):
            normalize_http_url("http://bad host/path")


if __name__ == "__main__":
    unittest.main()
