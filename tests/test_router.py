import sys
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.router import RouteError, parse_route, plugin_url


class RouterTests(unittest.TestCase):
    def test_round_trip_encodes_url_safely(self):
        base = "plugin://plugin.bdix.dhakaflix/"
        target = "http://172.16.50.7/Film (2026) [BD].mkv"
        route = plugin_url(base, "browse", target)
        query = route.split("?", 1)[1]
        parsed = parse_route(base, query)
        self.assertEqual(parsed.action, "browse")
        self.assertEqual(
            parsed.target_url, "http://172.16.50.7/Film%20(2026)%20%5BBD%5D.mkv"
        )

    def test_requires_target_url_for_non_root_actions(self):
        with self.assertRaises(RouteError):
            parse_route("plugin://plugin.bdix.dhakaflix/", "?action=play")

    def test_rejects_unknown_action(self):
        with self.assertRaises(RouteError):
            parse_route("plugin://plugin.bdix.dhakaflix/", "?action=execute")

    def test_round_trip_catalog_and_search_routes(self):
        base = "plugin://plugin.bdix.dhakaflix/"
        catalog = plugin_url(base, "catalog", media_type="series")
        self.assertEqual(
            parse_route(base, catalog.split("?", 1)[1]).media_type, "series"
        )

        search = plugin_url(base, "search", media_type="movie")
        route = parse_route(base, search.split("?", 1)[1])
        self.assertEqual(route.action, "search")
        self.assertEqual(route.media_type, "movie")

    def test_rejects_search_route_without_type_or_inline_query(self):
        base = "plugin://plugin.bdix.dhakaflix/"
        with self.assertRaises(RouteError):
            parse_route(base, "?action=search&type=other")
        with self.assertRaises(RouteError):
            parse_route(base, "?action=search&type=movie&query=secret")


if __name__ == "__main__":
    unittest.main()
