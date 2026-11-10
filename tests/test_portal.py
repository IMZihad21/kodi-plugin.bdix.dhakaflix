import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.cache import ListingCache
from resources.lib.portal import PortalClient, PortalError


class _Handler(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        type(self).requests += 1
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path.endswith("/missing/"):
            self.send_response(503)
            self.end_headers()
            return
        body = json.dumps(
            {
                "search": [
                    {"href": "/DHAKA-FLIX-7/Film%20(2026).mkv", "size": "1 GB"},
                    {"href": "/DHAKA-FLIX-7/readme.txt", "size": "1 KB"},
                    {"href": "http://example.com/other.mkv", "size": "1 GB"},
                    {"href": "/DHAKA-FLIX-7/Film%20(2026).mkv", "size": "1 GB"},
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests += 1
        if self.path == "/missing/":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/plain/":
            content_type = "text/plain"
            body = b"not html"
        else:
            content_type = "text/html; charset=utf-8"
            body = (
                b'<tr><td class="fb-i"><img src="file.png"></td>'
                b'<td class="fb-n"><a href="film.mkv">Film.mkv</a></td>'
                b'<td class="fb-d"></td><td class="fb-s"></td></tr>'
            )
            if self.path == "/large/":
                body = b" " * (8 * 1024 * 1024 + 1) + body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class PortalClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def test_fetches_and_uses_cache(self):
        _Handler.requests = 0
        client = PortalClient(self.base_url + "/")
        with self.subTest("fetch"):
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                cache = ListingCache(directory)
                first = client.listing(self.base_url + "/root/", cache)
                second = client.listing(self.base_url + "/root/", cache)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(_Handler.requests, 1)

    def test_fetches_html_for_portal_discovery(self):
        client = PortalClient(self.base_url + "/")
        html_text, final_url = client.fetch_html(self.base_url + "/root/")
        self.assertIn("film.mkv", html_text)
        self.assertEqual(final_url, self.base_url + "/root/")

    def test_rejects_directory_pages_larger_than_eight_megabytes(self):
        client = PortalClient(self.base_url + "/")
        with self.assertRaisesRegex(PortalError, "8 MiB size limit"):
            client.fetch_html(self.base_url + "/large/")

    def test_reports_http_and_content_type_errors(self):
        client = PortalClient(self.base_url + "/")
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            cache = ListingCache(directory)
            with self.assertRaisesRegex(PortalError, "HTTP 404"):
                client.listing(self.base_url + "/missing/", cache)
            with self.assertRaisesRegex(PortalError, "Expected an HTML"):
                client.listing(self.base_url + "/plain/", cache)

    def test_search_returns_unique_video_files_from_configured_host_only(self):
        client = PortalClient(self.base_url + "/")
        results = client.search_videos(self.base_url, "DHAKA-FLIX-7", "Film (2026)")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Film (2026).mkv")
        self.assertEqual(
            results[0].url,
            self.base_url + "/DHAKA-FLIX-7/Film%20(2026).mkv",
        )

    def test_search_reports_http_errors(self):
        client = PortalClient(self.base_url + "/")
        with self.assertRaisesRegex(PortalError, "HTTP 503"):
            client.search_videos(self.base_url, "missing", "Film")


if __name__ == "__main__":
    unittest.main()
