"""scripts/check-changelog.py: the version gate and the release-notes extract."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_changelog", ROOT / "scripts" / "check-changelog.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

SAMPLE = """# Changelog

## [Unreleased]

- pending

## [1.1.0] - 2026-01-02

### Added

- new thing

## [1.0.0] - 2026-01-01

- first

[Unreleased]: https://example/compare/v1.1.0...HEAD
[1.1.0]: https://example/compare/v1.0.0...v1.1.0
"""


class ChangelogTest(unittest.TestCase):
    def test_middle_section_stops_at_next_heading(self):
        self.assertEqual(cc.section(SAMPLE, "1.1.0"), "### Added\n\n- new thing")

    def test_last_section_drops_link_references(self):
        self.assertEqual(cc.section(SAMPLE, "1.0.0"), "- first")

    def test_missing_version_is_none(self):
        self.assertIsNone(cc.section(SAMPLE, "2.0.0"))

    def test_heading_prefix_is_not_a_match(self):
        # "1.1" must not match the "1.1.0" section.
        self.assertIsNone(cc.section(SAMPLE, "1.1"))

    def test_the_repo_changelog_covers_the_package_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text()
        self.assertTrue(cc.section(changelog, cc.package_version()))


if __name__ == "__main__":
    unittest.main()
