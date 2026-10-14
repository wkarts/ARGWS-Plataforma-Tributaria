from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import preflight
import retention
import rollback


class FakeGitHub:
    def pages(self, path: str):
        if path.endswith("/actions/caches"):
            return [
                {"id": 1, "key": "unrelated-project", "last_accessed_at": "2020-01-01T00:00:00Z"},
                {"id": 2, "key": "buildkit-api", "last_accessed_at": "2020-01-01T00:00:00Z"},
            ]
        if "reforma-tributaria-api" in path:
            return [
                {"id": 3, "created_at": "2020-01-01T00:00:00Z", "metadata": {"container": {"tags": ["sha-in-use"]}}},
                {"id": 4, "created_at": "2020-01-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
            ]
        return []


class OpsTests(unittest.TestCase):
    def test_retention_never_lists_tagged_digest_or_unrelated_cache_for_deletion(self):
        with patch.dict(retention.POLICY, {"allowed_untagged_version_ids": []}):
            report = retention.plan(FakeGitHub(), "wkarts/reforma-tributaria-platform", "user")
        self.assertEqual([entry["id"] for entry in report["caches"]], [2])
        self.assertEqual([entry["id"] for entry in report["ghcr"]], [4])
        self.assertFalse(report["ghcr"][0]["eligible"])

    def test_preflight_requires_real_admin_secret_and_restricts_prod_tag(self):
        values = {
            "SECRET_KEY": "s" * 32, "BOOTSTRAP_ADMIN_EMAIL": "admin@example.org",
            "BOOTSTRAP_ADMIN_PASSWORD": "p" * 20, "POSTGRES_PASSWORD": "p",
            "REDIS_PASSWORD": "p", "RABBITMQ_DEFAULT_PASS": "p",
            "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s" * 20,
            "S3_ENDPOINT": "objectstore:9000", "APP_TAG": "latest",
        }
        with self.assertRaisesRegex(ValueError, "APP_TAG"):
            preflight.validate(values, "production")
        values["APP_TAG"] = "v0.1.0"
        preflight.validate(values, "production")
        values["PROVIDER_MODE"] = "receita"
        with self.assertRaisesRegex(ValueError, "CBS_"):
            preflight.validate(values, "production")

    def test_rollback_only_updates_version_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            original = "APP_TAG=v0.1.0\nSECRET_KEY=private\n"
            path.write_text(original, encoding="utf-8")
            backup = rollback.set_tag(path, "sha-123456789abc")
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(path.read_text(encoding="utf-8"), "APP_TAG=sha-123456789abc\nSECRET_KEY=private\n")


if __name__ == "__main__":
    unittest.main()
