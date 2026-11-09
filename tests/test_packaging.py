import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from package import ADDON_ID, build, validate


class PackagingTests(unittest.TestCase):
    def test_metadata_validates_for_declared_kodi_python(self):
        self.assertEqual(validate(), "0.1.0")

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
                self.assertTrue(all(name.startswith(ADDON_ID + "/") for name in names))
                self.assertIsNone(archive.testzip())

            self.assertIn(first_digest, checksum)
            _, _, _ = build(output)
            second_digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
            self.assertEqual(first_digest, second_digest)


if __name__ == "__main__":
    unittest.main()
