import sys
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.catalog import NODE_URLS, PORTAL_URL, categories, search_nodes


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {
            "7": "http://172.16.50.7/",
            "10": "http://172.16.50.10/",
            "12": "http://172.16.50.12/",
            "14": "http://172.16.50.14/",
        }

    def test_categories_route_to_the_configured_node(self):
        movie_categories = dict(categories("movie", self.nodes))
        series_categories = dict(categories("series", self.nodes))
        self.assertEqual(
            movie_categories["English Movies (720p)"],
            "http://172.16.50.7/DHAKA-FLIX-7/English%20Movies/",
        )
        self.assertEqual(
            series_categories["TV & Web Series"],
            "http://172.16.50.12/DHAKA-FLIX-12/TV-WEB-Series/",
        )

    def test_empty_node_setting_disables_its_categories_and_search(self):
        self.nodes["7"] = ""
        movie_categories = categories("movie", self.nodes)
        self.assertNotIn(
            "English Movies (720p)", [label for label, _ in movie_categories]
        )
        self.assertNotIn(
            "7", [node_id for node_id, _, _ in search_nodes("movie", self.nodes)]
        )
        self.assertIn(
            "14", [node_id for node_id, _, _ in search_nodes("movie", self.nodes)]
        )

    def test_search_nodes_follow_supported_media_types(self):
        self.assertEqual(
            [node_id for node_id, _, _ in search_nodes("series", self.nodes)],
            ["10", "12", "14"],
        )

    def test_portal_and_node_urls_are_fixed(self):
        self.assertEqual(PORTAL_URL, "http://172.16.50.7/")
        self.assertEqual(
            NODE_URLS,
            {
                "7": "http://172.16.50.7/",
                "10": "http://172.16.50.10/",
                "12": "http://172.16.50.12/",
                "14": "http://172.16.50.14/",
            },
        )


if __name__ == "__main__":
    unittest.main()
