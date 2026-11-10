import hashlib
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from package import ADDON_ID, build, validate


class PackagingTests(unittest.TestCase):
    def test_metadata_validates_for_kodi_21_python_api(self):
        self.assertEqual(validate(), "0.4.0")

    def test_library_sync_settings_default_to_movies_only(self):
        settings_path = ROOT / ADDON_ID / "resources" / "settings.xml"
        settings = ET.parse(settings_path).getroot()
        defaults = {
            setting.attrib["id"]: setting.findtext("default")
            for setting in settings.findall(".//setting")
        }

        self.assertEqual(defaults["sync_movies"], "true")
        self.assertEqual(defaults["sync_series"], "false")

    def test_settings_only_expose_sync_and_discovery_controls(self):
        settings_path = ROOT / ADDON_ID / "resources" / "settings.xml"
        settings = ET.parse(settings_path).getroot()
        setting_ids = {
            setting.attrib["id"] for setting in settings.findall(".//setting")
        }

        self.assertEqual(
            setting_ids,
            {
                "excluded_keywords",
                "cache_ttl",
                "request_timeout",
                "server_7_url",
                "server_10_url",
                "server_12_url",
                "server_14_url",
                "sync_movies",
                "sync_series",
            },
        )
        self.assertEqual(
            {group.attrib["label"] for group in settings.findall(".//group")},
            {"30015", "30018"},
        )

    def test_build_is_valid_reproducible_and_checksummed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "addon.zip"
            _, package_path, checksum_path = build(output)
            first_digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
            checksum = checksum_path.read_text(encoding="ascii")

            with zipfile.ZipFile(package_path) as archive:
                names = archive.namelist()
                self.assertIn(ADDON_ID + "/addon.xml", names)
                self.assertIn(ADDON_ID + "/default.py", names)
                self.assertIn(ADDON_ID + "/service.py", names)
                self.assertIn(ADDON_ID + "/resources/lib/library_index.py", names)
                self.assertIn(ADDON_ID + "/resources/lib/sync/__init__.py", names)
                self.assertIn(ADDON_ID + "/resources/lib/sync/runner.py", names)
                self.assertIn(ADDON_ID + "/resources/icon.png", names)
                self.assertNotIn(
                    ADDON_ID + "/resources/lib/addon_logging.py",
                    names,
                )
                self.assertIn(
                    b"FULL_CRAWL_INTERVAL",
                    archive.read(ADDON_ID + "/resources/lib/sync/runner.py"),
                )
                self.assertTrue(all(name.startswith(ADDON_ID + "/") for name in names))
                self.assertTrue(all(not name.endswith("/") for name in names))
                self.assertIsNone(archive.testzip())

            self.assertIn(first_digest, checksum)
            _, _, _ = build(output)
            second_digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
            self.assertEqual(first_digest, second_digest)


if __name__ == "__main__":
    unittest.main()
