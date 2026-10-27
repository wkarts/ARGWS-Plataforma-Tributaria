"""Run real Git merges to protect fiscal release automation from history drift."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from resolve_release_merge import PromotionConflict, merge_versioned, VERSION_FILES


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError("git " + " ".join(args) + ": " + result.stderr)
    return result.stdout.strip()


def write(root: Path, path: str, value: str) -> None:
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(value, encoding="utf-8")


class ReleaseMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "fiscal-ci")
        git(self.repo, "config", "user.email", "ci@example.invalid")
        for name in VERSION_FILES:
            write(self.repo, name, "base\n")
        write(self.repo, "fiscal_rules.py", "base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "common")
        git(self.repo, "branch", "develop")

    def commit(self, name: str, contents: dict[str, str]) -> None:
        git(self.repo, "checkout", name)
        for path, value in contents.items():
            write(self.repo, path, value)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", name + " update")

    def test_version_conflicts_are_resolved_consistently_using_main_side(self) -> None:
        self.commit("develop", {name: "0.4.0\n" for name in VERSION_FILES})
        self.commit("main", {name: "0.3.1\n" for name in VERSION_FILES})
        fixed = merge_versioned("develop", cwd=self.repo)
        self.assertEqual(set(fixed), VERSION_FILES)
        self.assertEqual(len(git(self.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)
        for name in VERSION_FILES:
            self.assertEqual((self.repo / name).read_text(), "0.3.1\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        # After promotion, scripts/version.py synchronizes all values to the chosen SemVer.

    def test_unknown_conflicts_fail_closed_without_wiping_working_history(self) -> None:
        self.commit("develop", {"fiscal_rules.py": "develop rules\n"})
        self.commit("main", {"fiscal_rules.py": "main rules\n"})
        original = git(self.repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(PromotionConflict, "fiscal_rules.py"):
            merge_versioned("develop", cwd=self.repo)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), original)
        self.assertEqual((self.repo / "fiscal_rules.py").read_text(), "main rules\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_clean_merge_is_preserved(self) -> None:
        self.commit("develop", {"feature.py": "new\n"})
        git(self.repo, "checkout", "main")
        self.assertEqual(merge_versioned("develop", cwd=self.repo), [])
        self.assertEqual((self.repo / "feature.py").read_text(), "new\n")

    def test_invalid_source_and_dirty_workspace_are_rejected(self) -> None:
        git(self.repo, "checkout", "main")
        with self.assertRaises(PromotionConflict):
            merge_versioned("--upload-pack=x", cwd=self.repo)
        write(self.repo, "VERSION", "unsaved\n")
        with self.assertRaisesRegex(PromotionConflict, "workspace"):
            merge_versioned("develop", cwd=self.repo)


if __name__ == "__main__":
    unittest.main()
