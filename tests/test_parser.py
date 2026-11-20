import sys
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.parser import (
    DirectoryStreamParser,
    classify_file,
    format_kodi_date,
    format_size,
    parse_directory,
    parse_media_metadata,
    parse_size,
)
from resources.lib.urls import (
    URLValidationError,
    normalize_http_url,
    parent_directory_url,
)


class DirectoryParserTests(unittest.TestCase):
    def test_parses_h5ai_entries_with_size_and_sorts_folders_first(self):
        source = """
        <div id="fallback"><table>
          <tr><td class="fb-i"><img src="folder.png"></td>
          <td class="fb-n"><a href="Year%20%282026%29/">Year (2026)</a></td>
          <td class="fb-d">2026-01-01 12:34</td><td class="fb-s">-</td></tr>
          <tr><td class="fb-i"><img src="file.png"></td>
          <td class="fb-n"><a href="Film বাংলা.mkv">Film বাংলা.mkv</a></td>
          <td class="fb-d">2026-02-03 08:15</td><td class="fb-s">1.5 GB</td></tr>
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
        self.assertEqual(entries[0].modified, "2026-01-01 12:34")
        self.assertEqual(entries[0].size, 0)
        self.assertEqual(entries[1].size, int(1.5 * 1024**3))

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
        self.assertEqual(classify_file("README"), "other")

    def test_ignores_unsupported_links(self):
        source = (
            '<a href="file:///etc/passwd">secret</a>'
            '<a href="javascript:alert(1)">invalid</a>'
        )
        self.assertEqual(parse_directory(source, "https://media.example/"), [])

    def test_strict_mode_allows_failure_words_inside_valid_h5ai_listings(self):
        source = """
        <div id="fallback"><table>
          <tr><td class="fb-i"><img src="folder.png"></td>
          <td class="fb-n"><a href="The%20Forbidden%20Kingdom%20(2008)/">
            The Forbidden Kingdom (2008)
          </a></td>
          <td class="fb-d">2026-01-01</td><td class="fb-s"></td></tr>
        </table></div>
        """
        entries = parse_directory(
            source,
            "http://172.16.50.7/movies/",
            strict=True,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "The Forbidden Kingdom (2008)")

    def test_strict_mode_ignores_failure_words_in_scripts_and_styles(self):
        source = """
        <html><head>
          <style>.forbidden { color: red; }</style>
          <script>const cloudflare = "access denied";</script>
        </head><body>
          <div id="fallback"><table>
            <tr><td class="fb-i"><img src="file.png"></td>
            <td class="fb-n"><a href="film.mkv">Film.mkv</a></td>
            <td class="fb-d"></td><td class="fb-s">700 MB</td></tr>
          </table></div>
        </body></html>
        """
        entries = parse_directory(
            source,
            "http://172.16.50.7/movies/",
            strict=True,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Film.mkv")

    def test_strict_mode_rejects_error_page_without_h5ai_structure(self):
        source = "<html><body><h1>403 Forbidden - Access Denied</h1></body></html>"
        with self.assertRaisesRegex(ValueError, "access, challenge, or error page"):
            parse_directory(source, "http://172.16.50.7/movies/", strict=True)

    def test_strict_mode_rejects_error_message_even_with_h5ai_structure(self):
        source = """
        <html><body>
          <h1>403 Forbidden - Access Denied</h1>
          <div id="fallback"><table>
            <tr><td class="fb-i"><img src="file.png"></td>
            <td class="fb-n"><a href="maintenance.mkv">maintenance.mkv</a></td>
            <td class="fb-d"></td><td class="fb-s"></td></tr>
          </table></div>
        </body></html>
        """
        with self.assertRaisesRegex(ValueError, "access, challenge, or error page"):
            parse_directory(source, "http://172.16.50.7/movies/", strict=True)

    def test_streaming_parser_matches_batch_parse_across_arbitrary_chunks(self):
        source = """
        <div id="fallback"><table>
          <tr><td class="fb-i"><img src="folder.png"></td>
          <td class="fb-n"><a href="Season%201/">Season 1</a></td>
          <td class="fb-d">2026-01-01 10:00</td><td class="fb-s">-</td></tr>
          <tr><td class="fb-i"><img src="file.png"></td>
          <td class="fb-n"><a href="Show.S01E02.mkv">Show.S01E02.mkv</a></td>
          <td class="fb-d">2026-01-02 11:22</td><td class="fb-s">450 MB</td></tr>
        </table></div>
        """
        stream = DirectoryStreamParser("http://172.16.50.12/show/", strict=True)
        for index in range(0, len(source), 7):
            stream.feed(source[index : index + 7])
        self.assertEqual(
            stream.finalize(),
            parse_directory(source, "http://172.16.50.12/show/", strict=True),
        )

    def test_size_date_and_metadata_helpers(self):
        self.assertEqual(parse_size("1.5 GB"), int(1.5 * 1024**3))
        self.assertEqual(parse_size("420 KB"), 420 * 1024)
        self.assertEqual(parse_size("-"), 0)
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(420 * 1024), "420 KB")
        self.assertEqual(format_size(int(1.4 * 1024**3)), "1.4 GB")
        self.assertEqual(format_size(0), "")

        self.assertEqual(format_kodi_date("2026-01-01 12:34"), "2026-01-01 12:34:00")
        self.assertEqual(format_kodi_date("2026-01-01"), "2026-01-01 00:00:00")
        self.assertEqual(format_kodi_date("invalid"), "")

        self.assertEqual(
            parse_media_metadata("Film (2026).mkv"),
            ("Film (2026)", 2026, None, None, "movie"),
        )
        self.assertEqual(
            parse_media_metadata("Show.2023.S02E14.1080p.mkv"),
            ("Show.2023.S02E14.1080p", 2023, 2, 14, "episode"),
        )
        self.assertEqual(
            parse_media_metadata("1917.mkv"),
            ("1917", None, None, None, "video"),
        )
        self.assertEqual(
            parse_media_metadata("2012 (2009).mkv"),
            ("2012 (2009)", 2009, None, None, "movie"),
        )


class URLValidationTests(unittest.TestCase):
    def test_normalizes_spaces_and_drops_fragment(self):
        self.assertEqual(
            normalize_http_url("HTTP://Example.COM/A film.mkv#part"),
            "http://example.com/A%20film.mkv",
        )

    def test_parent_directory_url_resolves_parent_folder(self):
        self.assertEqual(
            parent_directory_url("http://172.16.50.7/movies/Film%20(2026).mkv?x=1"),
            "http://172.16.50.7/movies/",
        )
        self.assertEqual(
            parent_directory_url("http://172.16.50.7/movies/Sub/"),
            "http://172.16.50.7/movies/",
        )
        self.assertEqual(
            parent_directory_url("http://172.16.50.7/"),
            "http://172.16.50.7/",
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
