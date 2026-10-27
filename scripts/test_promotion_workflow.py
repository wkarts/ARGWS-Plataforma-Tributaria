"""Static security and dependency contracts of the promotion workflow."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PromotionWorkflowTests(unittest.TestCase):
    def test_promotion_requires_successful_develop_release(self):
        workflow = (ROOT / ".github/workflows/develop-to-main-pr.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["GHCR publish and release"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("branches: [develop]", workflow)
        self.assertNotIn("  push:\n", workflow.split("permissions:", 1)[0])
        self.assertIn("Check repository PR creation policy", workflow)
        self.assertIn("can_approve_pull_request_reviews", workflow)
        self.assertIn("PROMOTION_PR_TOKEN", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("Fiscal-Deploy-Matrix-preview.zip", workflow)
        self.assertIn("SHA256SUMS-selected.txt", workflow)
        self.assertIn("git/ref/tags/", workflow)

    def test_automatic_pr_versioning_ci_merge_and_stable_release(self):
        workflow = (ROOT / ".github/workflows/develop-to-main-pr.yml").read_text(encoding="utf-8")
        for marker in ("scripts/release_promotion_version.py", "scripts/version.py set",
                       "--base main", "gh pr create", "gh workflow run ci.yml",
                       "gh run watch", "gh pr merge", "gh workflow run ghcr-publish.yml",
                       "PROMOTION_PR_TOKEN || github.token", "actions: write",
                       "pull-requests: write"):
            self.assertIn(marker, workflow)

    def test_only_version_metadata_can_be_auto_resolved(self):
        workflow = (ROOT / ".github/workflows/develop-to-main-pr.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/resolve_release_merge.py --source origin/develop", workflow)
        self.assertNotIn('git merge --no-ff --no-edit origin/develop; then', workflow)

    def test_branch_protection_is_not_bypassed(self):
        workflow = (ROOT / ".github/workflows/develop-to-main-pr.yml").read_text(encoding="utf-8")
        for forbidden in ("git push --force", "--force-with-lease", "gh pr merge --admin",
                          "docker compose up", "gh pr merge --auto"):
            self.assertNotIn(forbidden, workflow)
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", ci)
        self.assertIn("  workflow_call:", ci)


if __name__ == "__main__":
    unittest.main()
