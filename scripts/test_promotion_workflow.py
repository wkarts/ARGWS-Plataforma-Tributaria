"""Contracts for idempotent develop-to-main PR creation."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PromotionWorkflowTests(unittest.TestCase):
    def test_automatic_pr_is_draft_and_does_not_merge_or_deploy(self):
        wf = (ROOT / ".github/workflows/develop-to-main-pr.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [develop]", wf)
        self.assertIn("pull-requests: write", wf)
        self.assertIn("compare/main...develop", wf)
        self.assertIn("gh pr list", wf)
        self.assertIn("--base main --head develop --draft", wf)
        self.assertIn("PROMOTION_PR_TOKEN || github.token", wf)
        self.assertIn("cancel-in-progress: false", wf)
        self.assertNotIn("gh pr merge", wf)
        self.assertNotIn("docker compose", wf)
        self.assertNotIn("git push", wf)


if __name__ == "__main__":
    unittest.main()
