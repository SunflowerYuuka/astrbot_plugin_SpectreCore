import unittest

from tests._orchestration_helpers import ROOT


class VersionConsistencyTest(unittest.TestCase):
    def test_release_versions_match(self):
        self.assertIn('"2.6.4"', (ROOT / "main.py").read_text("utf-8"))
        self.assertIn("version: 2.6.4", (ROOT / "metadata.yaml").read_text("utf-8"))
        self.assertIn("version-v2.6.4-", (ROOT / "README.md").read_text("utf-8"))
        self.assertIn("## v2.6.4", (ROOT / "CHANGELOG.md").read_text("utf-8"))
        self.assertTrue((ROOT / "changelogs/v2.6.4.md").exists())
