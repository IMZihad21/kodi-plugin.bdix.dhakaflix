import gzip
import json
import sys
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.cache import ListingCache, clear_memory_cache
from resources.lib.portal import PortalClient, PortalError


class _CountingHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass):
        self.connection_count = 0
        super().__init__(server_address, RequestHandlerClass)

    def process_request(self, request, client_address):
        self.connection_count += 1
        super().process_request(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = 0
    etag = '"v1"'

    def do_POST(self):
        type(self).requests += 1
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path.endswith("/missing/"):
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.endswith("/redirect-cross/"):
            self.send_response(302)
            self.send_header("Location", "http://example.com/search")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(
            {
                "search": [
                    {
                        "href": "/DHAKA-FLIX-7/Film%20(2026).mkv",
                        "size": 1073741824,
                        "time": 1767225600000,
                    },
                    {"href": "/DHAKA-FLIX-7/readme.txt", "size": "1 KB"},
                    {"href": "http://example.com/other.mkv", "size": "1 GB"},
                    {"href": "/DHAKA-FLIX-7/Film%20(2026).mkv", "size": "1 GB"},
                ]
            }
        ).encode("utf-8")
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            payload = gzip.compress(body)
            encoding = "gzip"
        else:
            payload = body
            encoding = None
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        type(self).requests += 1
        if self.path == "/missing/":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/redirect-outside/":
            self.send_response(302)
            self.send_header("Location", "http://example.com/evil/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/revalidate/" and (
            self.headers.get("If-None-Match") == self.etag
        ):
            self.send_response(304)
            self.send_header("ETag", self.etag)
            self.end_headers()
            return
        if self.path == "/plain/":
            content_type = "text/plain"
            body = b"not html"
        elif self.path == "/excluded/":
            content_type = "text/html; charset=utf-8"
            body = (
                b'<div id="fallback"><table>'
                b'<tr><td class="fb-i"><img src="folder.png"></td>'
                b'<td class="fb-n"><a href="Movies/">Movies</a></td>'
                b'<td class="fb-d"></td><td class="fb-s"></td></tr>'
                b'<tr><td class="fb-i"><img src="folder.png"></td>'
                b'<td class="fb-n"><a href="Games/">Games</a></td>'
                b'<td class="fb-d"></td><td class="fb-s"></td></tr>'
                b"</table></div>"
            )
        else:
            content_type = "text/html; charset=utf-8"
            body = (
                b'<tr><td class="fb-i"><img src="file.png"></td>'
                b'<td class="fb-n"><a href="film.mkv">Film.mkv</a></td>'
                b'<td class="fb-d">2026-01-01 12:00</td>'
                b'<td class="fb-s">500 MB</td></tr>'
            )
            if self.path == "/large/":
                body = b" " * (8 * 1024 * 1024 + 1) + body

        encoding = None
        payload = body
        if self.path == "/gzip/":
            payload = gzip.compress(body)
            encoding = "gzip"
        elif self.path == "/deflate/":
            payload = zlib.compress(body)
            encoding = "deflate"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if self.path == "/revalidate/":
            self.send_header("ETag", self.etag)
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass


class PortalClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _CountingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def setUp(self):
        clear_memory_cache()
        self.addCleanup(clear_memory_cache)

    def test_fetches_and_uses_cache(self):
        _Handler.requests = 0
        with (
            PortalClient(self.base_url + "/") as client,
            tempfile.TemporaryDirectory() as directory,
        ):
            cache = ListingCache(directory)
            first = client.listing(self.base_url + "/root/", cache)
            second = client.listing(self.base_url + "/root/", cache)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].size, 500 * 1024**2)
        self.assertEqual(_Handler.requests, 1)

    def test_fetches_html_for_portal_discovery(self):
        with PortalClient(self.base_url + "/") as client:
            html_text, final_url = client.fetch_html(self.base_url + "/root/")
        self.assertIn("film.mkv", html_text)
        self.assertEqual(final_url, self.base_url + "/root/")

    def test_rejects_invalid_request_timeout(self):
        with self.assertRaisesRegex(ValueError, "between 3 and 60"):
            PortalClient(self.base_url + "/", timeout=2)

    def test_fetches_directory_pages_larger_than_eight_megabytes(self):
        with PortalClient(self.base_url + "/") as client:
            html_text, _ = client.fetch_html(self.base_url + "/large/")
        self.assertIn("film.mkv", html_text)

    def test_reports_http_and_content_type_errors(self):
        with (
            PortalClient(self.base_url + "/") as client,
            tempfile.TemporaryDirectory() as directory,
        ):
            cache = ListingCache(directory)
            with self.assertRaisesRegex(PortalError, "HTTP 404"):
                client.listing(self.base_url + "/missing/", cache)
            with self.assertRaisesRegex(PortalError, "Expected an HTML"):
                client.listing(self.base_url + "/plain/", cache)

    def test_search_returns_unique_video_files_from_configured_host_only(self):
        with PortalClient(self.base_url + "/") as client:
            results = client.search_videos(self.base_url, "DHAKA-FLIX-7", "Film (2026)")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Film (2026).mkv")
        self.assertEqual(
            results[0].url,
            self.base_url + "/DHAKA-FLIX-7/Film%20(2026).mkv",
        )
        self.assertEqual(results[0].size, 1073741824)
        self.assertEqual(results[0].modified, "2026-01-01 00:00")

    def test_search_reports_http_errors(self):
        with PortalClient(self.base_url + "/") as client:
            with self.assertRaisesRegex(PortalError, "HTTP 503"):
                client.search_videos(self.base_url, "missing", "Film")

    def test_listing_handles_gzip_and_deflate_compression(self):
        with PortalClient(self.base_url + "/") as client:
            gzip_entries = client.listing(self.base_url + "/gzip/", None)
            deflate_entries = client.listing(self.base_url + "/deflate/", None)
        self.assertEqual(len(gzip_entries), 1)
        self.assertEqual(len(deflate_entries), 1)
        self.assertEqual(gzip_entries[0].name, deflate_entries[0].name)
        self.assertEqual(gzip_entries[0].kind, deflate_entries[0].kind)
        self.assertEqual(gzip_entries[0].modified, deflate_entries[0].modified)
        self.assertEqual(gzip_entries[0].size, deflate_entries[0].size)
        self.assertTrue(gzip_entries[0].url.endswith("/gzip/film.mkv"))
        self.assertTrue(deflate_entries[0].url.endswith("/deflate/film.mkv"))

    def test_listing_revalidates_expired_cache_with_304_not_modified(self):
        _Handler.requests = 0
        url = self.base_url + "/revalidate/"
        with (
            PortalClient(self.base_url + "/") as client,
            tempfile.TemporaryDirectory() as directory,
        ):
            cache = ListingCache(directory, ttl_seconds=60)
            first = client.listing(url, cache)
            cache.touch(url, now=0)
            second = client.listing(url, cache)
            third = client.listing(url, cache)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(_Handler.requests, 2)

    def test_keep_alive_pool_reuses_single_tcp_connection(self):
        start_connections = self.server.connection_count
        with PortalClient(self.base_url + "/") as client:
            client.listing(self.base_url + "/a/", None)
            client.listing(self.base_url + "/b/", None)
            client.search_videos(self.base_url, "DHAKA-FLIX-7", "Film")
        self.assertEqual(self.server.connection_count - start_connections, 1)

    def test_excluded_keywords_apply_dynamically_to_cached_listings(self):
        url = self.base_url + "/excluded/"
        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory, ttl_seconds=3600)
            with PortalClient(self.base_url + "/", excluded_keywords=()) as client_all:
                entries_all = client_all.listing(url, cache)
            self.assertEqual([entry.name for entry in entries_all], ["Games", "Movies"])

            with PortalClient(
                self.base_url + "/", excluded_keywords=("games",)
            ) as client_filtered:
                entries_filtered = client_filtered.listing(url, cache)
            self.assertEqual([entry.name for entry in entries_filtered], ["Movies"])

    def test_refuses_redirect_to_unapproved_or_cross_origin_server(self):
        with PortalClient(self.base_url + "/") as client:
            with self.assertRaisesRegex(PortalError, "unapproved"):
                client.fetch_html(self.base_url + "/redirect-outside/")
            with self.assertRaisesRegex(PortalError, "unapproved"):
                client.search_videos(self.base_url, "redirect-cross", "Film")


if __name__ == "__main__":
    unittest.main()
