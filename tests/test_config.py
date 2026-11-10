import sys
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "plugin.bdix.dhakaflix"
sys.path.insert(0, str(ADDON_DIR))

from resources.lib.config import parse_integer_setting


class IntegerSettingTests(unittest.TestCase):
    def test_uses_default_for_empty_settings(self):
        self.assertEqual(
            parse_integer_setting(
                "",
                name="Request timeout",
                default=20,
                minimum=3,
                maximum=60,
            ),
            20,
        )

    def test_parses_valid_values_and_enforces_range(self):
        self.assertEqual(
            parse_integer_setting(
                "30",
                name="Request timeout",
                default=20,
                minimum=3,
                maximum=60,
            ),
            30,
        )
        with self.assertRaisesRegex(ValueError, "between 3 and 60"):
            parse_integer_setting(
                "61",
                name="Request timeout",
                default=20,
                minimum=3,
                maximum=60,
            )

    def test_invalid_values_raise_actionable_errors(self):
        for value in ("invalid", "2.5", True, 2.5):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "must be an integer"),
            ):
                parse_integer_setting(
                    value,
                    name="Request timeout",
                    default=20,
                    minimum=3,
                    maximum=60,
                )

    def test_unbounded_maximum_still_enforces_minimum(self):
        self.assertEqual(
            parse_integer_setting(
                "0",
                name="Listing cache duration",
                default=7200,
                minimum=0,
            ),
            0,
        )
        with self.assertRaisesRegex(ValueError, "at least 0"):
            parse_integer_setting(
                "-1",
                name="Listing cache duration",
                default=7200,
                minimum=0,
            )


if __name__ == "__main__":
    unittest.main()
