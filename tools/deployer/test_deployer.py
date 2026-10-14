"""Contract checks for secrets, recovery, and local Compose operations."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("deployer", Path(__file__).with_name("deployer.py"))
assert spec and spec.loader
deployer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployer)


ENV_TEMPLATE = """COMPOSE_PROJECT_NAME=reforma-docker-production
IMAGE_OWNER=wkarts
APP_TAG=CHANGE_ME_TO_RELEASE_TAG
APP_ENV=production
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=48080
POSTGRES_DB=reforma
POSTGRES_USER=reforma
POSTGRES_PASSWORD=
REDIS_PASSWORD=
RABBITMQ_DEFAULT_USER=reforma
RABBITMQ_DEFAULT_PASS=
S3_ENDPOINT=objectstore:9000
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET=fiscal-evidence
S3_REGION=us-east-1
S3_USE_SSL=false
SECRET_KEY=
BOOTSTRAP_ADMIN_EMAIL=
BOOTSTRAP_ADMIN_PASSWORD=
DATABASE_URL=postgresql+asyncpg://reforma:password@postgres:5432/reforma
REDIS_URL=redis://:password@redis:6379/0
BROKER_URL=amqp://reforma:password@rabbitmq:5672/
"""
COMPOSE = """name: ${COMPOSE_PROJECT_NAME}
services:
  api:
    image: ghcr.io/${IMAGE_OWNER}/reforma-tributaria-api:${APP_TAG}
  web:
    image: ghcr.io/${IMAGE_OWNER}/reforma-tributaria-web:${APP_TAG}
  objectstore:
    image: ghcr.io/example/rustfs:1.0.1
"""


class DeployerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        (self.root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
        for channel in ("production", "develop"):
            templates = self.root / "deploy" / "docker" / channel
            templates.mkdir(parents=True)
            model = ENV_TEMPLATE.replace("production", channel).replace("48080", "48081" if channel == "develop" else "48080")
            if channel == "develop":
                model = model.replace("APP_TAG=CHANGE_ME_TO_RELEASE_TAG", "APP_TAG=develop")
            (templates / ".env.example").write_text(model, encoding="utf-8")
            (templates / "compose.yaml").write_text(COMPOSE, encoding="utf-8")
        self.output = Path(self.temp.name) / "stack"
        patcher = patch.object(deployer, "project_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def generate(self, **options: object) -> dict:
        return deployer.generate(target="docker", channel="production", output=self.output,
                                 admin_email="admin@example.org", owner="wkarts", **options)

    def test_owner_must_be_explicit_in_new_invocation(self) -> None:
        parser = deployer.make_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["generate", "--target", "docker", "--channel", "production",
                               "--output", str(self.output), "--admin-email", "admin@example.org"])
        self.assertEqual(parser.parse_args(["generate", "--target", "docker", "--channel", "production",
                                            "--output", str(self.output), "--admin-email", "admin@example.org",
                                            "--owner", "wkarts"]).owner, "wkarts")

    def test_generate_uses_version_and_url_credentials_agree(self) -> None:
        result = self.generate()
        values = deployer.parse_env((self.output / ".env").read_text(encoding="utf-8"))
        self.assertEqual(result["tag"], "0.1.0")
        self.assertEqual(values["APP_TAG"], "0.1.0")
        self.assertIn(values["POSTGRES_PASSWORD"], values["DATABASE_URL"])
        self.assertIn(values["REDIS_PASSWORD"], values["REDIS_URL"])
        self.assertIn(values["RABBITMQ_DEFAULT_PASS"], values["BROKER_URL"])
        self.assertNotEqual(values["POSTGRES_PASSWORD"], values["REDIS_PASSWORD"])
        self.assertEqual(values["BOOTSTRAP_ADMIN_EMAIL"], "admin@example.org")
        self.assertGreaterEqual(len(values["BOOTSTRAP_ADMIN_PASSWORD"]), 20)
        self.assertGreaterEqual(len(values["SECRET_KEY"]), 20)
        self.assertGreaterEqual(len(values["S3_ACCESS_KEY"]), 16)
        self.assertGreaterEqual(len(values["S3_SECRET_KEY"]), 20)
        self.assertEqual(values["S3_ENDPOINT"], "objectstore:9000")
        self.assertFalse(any(key.startswith("MINIO_") for key in values))
        self.assertEqual(deployer.validate_directory(self.output)["tag"], "0.1.0")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((self.output / ".env").stat().st_mode), 0o600)

    def test_force_updates_compose_and_preserves_env_byte_for_byte(self) -> None:
        self.generate()
        previous_env = (self.output / ".env").read_bytes()
        (self.root / "deploy" / "docker" / "production" / "compose.yaml").write_text(COMPOSE + "# updated\n")
        result = self.generate(force=True, tag="0.2.0")
        self.assertEqual((self.output / ".env").read_bytes(), previous_env)
        self.assertEqual(result["tag"], "0.1.0")
        self.assertTrue((self.output / ".deployer" / "backups" / result["backup_id"] / ".env").is_file())
        self.assertIn("# updated", (self.output / "compose.yaml").read_text())

    def test_recovery_restores_previous_compose_and_env(self) -> None:
        self.generate()
        original = (self.output / "compose.yaml").read_bytes()
        changed = self.generate(force=True)
        (self.output / "compose.yaml").write_text("corrompido", encoding="utf-8")
        self.assertRaises(deployer.DeployerError, deployer.recover, self.output,
                          backup_id=changed["backup_id"], confirmed=False)
        restored = deployer.recover(self.output, backup_id=changed["backup_id"], confirmed=True)
        self.assertEqual(restored["restored"], changed["backup_id"])
        self.assertEqual((self.output / "compose.yaml").read_bytes(), original)

    def test_deploy_requires_confirmation_and_records_tag_only_after_success(self) -> None:
        self.generate()
        with patch.object(deployer, "safe_process") as run:
            self.assertRaises(deployer.DeployerError, deployer.deploy, self.output, confirmed=False)
            self.assertEqual(run.call_count, 0)
            result = deployer.deploy(self.output, confirmed=True)
            self.assertEqual(result["tag"], "0.1.0")
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[-1].args[0][-5:],
                             ["up", "-d", "--wait", "--wait-timeout", "180"])
        state = json.loads((self.output / ".deployer" / "state.json").read_text())
        self.assertEqual(state["last_deployed_tag"], "0.1.0")

    def test_failed_rollback_restores_env_and_does_not_advance_state(self) -> None:
        self.generate()
        original = (self.output / ".env").read_bytes()
        with patch.object(deployer, "safe_process", side_effect=deployer.DeployerError("falha")):
            with self.assertRaises(deployer.DeployerError):
                deployer.rollback(self.output, tag="0.0.9", confirmed=True)
        self.assertEqual((self.output / ".env").read_bytes(), original)
        self.assertEqual(deployer.read_state(self.output)["last_deployed_tag"], None)

    def test_upgrade_keeps_secrets_and_enables_previous_tag_rollback(self) -> None:
        self.generate()
        before = deployer.parse_env((self.output / ".env").read_text(encoding="utf-8"))
        with patch.object(deployer, "safe_process"):
            deployer.deploy(self.output, confirmed=True)
            result = deployer.upgrade(self.output, tag="sha-012345abcdef", confirmed=True)
        after = deployer.parse_env((self.output / ".env").read_text(encoding="utf-8"))
        self.assertEqual(result["tag"], "sha-012345abcdef")
        self.assertEqual(deployer.read_state(self.output)["previous_deployed_tag"], "0.1.0")
        self.assertEqual({key: value for key, value in before.items() if key != "APP_TAG"},
                         {key: value for key, value in after.items() if key != "APP_TAG"})

    def test_export_contains_only_compose_and_env_with_secret(self) -> None:
        self.generate()
        archive = Path(self.temp.name) / "handoff.zip"
        result = deployer.export_zip(self.output, archive)
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(set(bundle.namelist()), {"compose.yaml", ".env"})
            self.assertIn(b"POSTGRES_PASSWORD=", bundle.read(".env"))
        self.assertTrue(result["contains_secrets"])

    def test_preview_never_includes_secrets_or_generates_files(self) -> None:
        data = deployer.preview(target="docker", channel="production", owner="wkarts", tag=None,
                                bind=None, port=None)
        self.assertEqual(data["image_tag"], "0.1.0")
        self.assertIn("POSTGRES_PASSWORD", data["secret_names"])
        self.assertIn("objectstore", data["services"])
        self.assertNotIn("internal", data["services"])
        self.assertNotIn("POSTGRES_PASSWORD=", json.dumps(data))
        self.assertFalse(self.output.exists())

    def test_rejects_placeholder_and_mutable_production_tag(self) -> None:
        self.generate()
        env = (self.output / ".env").read_text(encoding="utf-8")
        (self.output / ".env").write_text(env.replace("APP_TAG=0.1.0", "APP_TAG=CHANGE_ME_TO_RELEASE_TAG"))
        self.assertRaises(deployer.DeployerError, deployer.validate_directory, self.output)
        self.assertRaises(deployer.DeployerError, self.generate, tag="stable", force=True)

    def test_production_accepts_pinned_sha12_and_rejects_short_sha(self) -> None:
        made = self.generate(tag="sha-012345abcdef")
        self.assertEqual(made["tag"], "sha-012345abcdef")
        self.assertEqual(deployer.validate_directory(self.output)["tag"], "sha-012345abcdef")
        self.assertRaises(deployer.DeployerError, self.generate, tag="sha-0123", force=True)

    def test_all_eight_real_templates_generate_and_validate(self) -> None:
        real_root = Path(__file__).resolve().parents[2]
        if not all((real_root / "deploy" / target / channel / "compose.yaml").is_file()
                   for target in deployer.TARGETS for channel in deployer.CHANNELS):
            self.skipTest("os oito modelos de implantação ainda não estão disponíveis")
        with patch.object(deployer, "project_root", return_value=real_root):
            version = deployer.product_version()
            stable_tag = version if deployer.SEMVER.fullmatch(version) else "0.1.0"
            for target in deployer.TARGETS:
                for channel in deployer.CHANNELS:
                    with self.subTest(target=target, channel=channel):
                        output = Path(self.temp.name) / target / channel
                        made = deployer.generate(target=target, channel=channel, output=output,
                                                 admin_email="admin@example.org",
                                                 owner="wkarts",
                                                 tag=stable_tag if channel == "production" else None)
                        checked = deployer.validate_directory(output)
                        self.assertEqual(checked["tag"], "develop" if channel == "develop" else stable_tag)
                        self.assertEqual(made["target"], target)
                        values = deployer.parse_env((output / ".env").read_text(encoding="utf-8"))
                        self.assertEqual(values["BOOTSTRAP_ADMIN_EMAIL"], "admin@example.org")
                        self.assertNotEqual(values["SECRET_KEY"], values["BOOTSTRAP_ADMIN_PASSWORD"])


if __name__ == "__main__":
    unittest.main()
