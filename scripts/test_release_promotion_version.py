"""Stable version selection and release immutability regression tests."""
import unittest

from release_promotion_version import choose_stable_version, parse_version


class StableVersionTests(unittest.TestCase):
    def test_next_patch_when_develop_has_same_or_older_version(self):
        self.assertEqual(choose_stable_version("0.3.0", "0.3.0"), "0.3.1")
        self.assertEqual(choose_stable_version("0.3.3", "0.3.0"), "0.3.4")

    def test_respects_intentional_develop_minor_or_major_bump(self):
        self.assertEqual(choose_stable_version("0.3.5", "0.4.0"), "0.4.0")
        self.assertEqual(choose_stable_version("0.3.5", "1.0.0"), "1.0.0")

    def test_never_reuses_existing_git_tags(self):
        self.assertEqual(
            choose_stable_version("0.3.0", "0.3.1",
                                  {"v0.3.1", "v0.3.2", "unrelated"}),
            "0.3.3",
        )
        self.assertEqual(choose_stable_version("0.3.0", "0.3.0", {"0.3.1"}), "0.3.2")

    def test_rejects_invalid_versions_instead_of_guessing(self):
        for version in ("v0.3.0", "01.2.0", "0.3", "0.3.0-alpha.1", "", "0.3.0\n"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                parse_version(version)


if __name__ == "__main__":
    unittest.main()
