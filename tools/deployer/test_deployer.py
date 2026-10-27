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
PROVIDER_MODE=disabled
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

    def test_owner_is_auto_resolved_from_model_without_required_input(self) -> None:
        parser = deployer.make_parser()
        command = ["generate", "--target", "docker", "--channel", "production",
                   "--output", str(self.output), "--admin-email", "admin@example.org"]
        self.assertIsNone(parser.parse_args(command).owner)
        self.assertEqual(deployer.defaults("docker", "production")["owner"], "wkarts")
        self.assertEqual(deployer.defaults("docker", "production")["web_port"], 48080)
        self.assertEqual(deployer.defaults("docker", "develop")["web_port"], 48081)
        result = deployer.generate(target="docker", channel="production", output=self.output,
                                   admin_email="admin@example.org", tag="v0.1.0")
        self.assertEqual(result["owner"], "wkarts")
        self.assertEqual(result["web_port"], 48080)
        self.assertTrue(Path(result["compose_path"]).is_file())
        self.assertTrue(Path(result["env_path"]).is_file())

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

    def test_latest_selects_published_stable_tag_instead_of_mutable_latest(self) -> None:
        with patch.object(deployer, "load_catalog",
                          return_value={"latest": "v0.3.1", "versions": ["v0.3.1", "v0.3.0"]}):
            result = deployer.generate(target="docker", channel="production",
                                       output=self.output,
                                       admin_email="admin@example.org", tag="latest")
        env = deployer.parse_env((self.output / ".env").read_text(encoding="utf-8"))
        self.assertEqual(result["tag"], "v0.3.1")
        self.assertEqual(env["APP_TAG"], "v0.3.1")
        self.assertNotIn("APP_TAG=latest", (self.output / ".env").read_text())

    def test_latest_fails_closed_when_catalog_is_unavailable(self) -> None:
        from release_catalog import ReleaseCatalogError
        with patch.object(deployer, "load_catalog",
                          side_effect=ReleaseCatalogError("Serviço indisponível")):
            with self.assertRaisesRegex(deployer.DeployerError, "Serviço indisponível"):
                deployer.generate(target="docker", channel="production",
                                  output=self.output,
                                  admin_email="admin@example.org", tag="latest")
        self.assertFalse((self.output / ".env").exists())

    def test_path_with_spaces_port_override_and_manual_tag(self) -> None:
        folder = self.output.parent / "Pasta com espaços Windows" / "production"
        result = deployer.generate(target="docker", channel="production",
                                   output=folder, owner=None,
                                   tag="v0.2.0", port=48092,
                                   admin_email="admin@example.org")
        data = deployer.validate_directory(folder)
        self.assertEqual(data["port"], 48092)
        self.assertEqual(data["tag"], "v0.2.0")
        self.assertEqual(Path(result["directory"]), folder.resolve())
        self.assertTrue((folder / "compose.yaml").is_file())
        self.assertTrue((folder / ".env").is_file())

    def test_receita_mode_is_explicit_and_fixture_is_rejected_in_production(self) -> None:
        generated = self.generate(provider_mode="receita")
        configured = deployer.parse_env((self.output / ".env").read_text())
        self.assertEqual(generated["provider_mode"], "receita")
        self.assertEqual(configured["PROVIDER_MODE"], "receita")
        self.assertEqual(deployer.validate_directory(self.output)["provider_mode"], "receita")
        with self.assertRaisesRegex(deployer.DeployerError, "não aceita fixture"):
            self.generate(provider_mode="fixture", force=True)

    def test_provider_update_preserves_secrets_and_backups_existing_installation(self) -> None:
        self.generate(provider_mode="receita")
        before = deployer.parse_env((self.output / ".env").read_text())
        change = deployer.update_provider_mode(self.output, mode="disabled", confirmed=True)
        after = deployer.parse_env((self.output / ".env").read_text())
        self.assertEqual(change["provider_mode"], "disabled")
        self.assertIsNotNone(change["backup_id"])
        self.assertEqual(after["PROVIDER_MODE"], "disabled")
        for key in ("SECRET_KEY", "REDIS_PASSWORD", "POSTGRES_PASSWORD", "BOOTSTRAP_ADMIN_PASSWORD"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(deployer.update_provider_mode(
            self.output, mode="disabled", confirmed=True)["changed"], False)
        with self.assertRaises(deployer.DeployerError):
            deployer.update_provider_mode(self.output, mode="fixture", confirmed=True)
        with self.assertRaises(deployer.DeployerError):
            deployer.update_provider_mode(self.output, mode="receita", confirmed=False)

    def test_provider_update_preserves_old_fixture_secrets_on_conversion(self) -> None:
        self.generate()
        path = self.output / ".env"
        initial = path.read_text()
        path.write_text(initial.replace("PROVIDER_MODE=disabled", "PROVIDER_MODE=fixture"))
        output = deployer.update_provider_mode(self.output, mode="disabled", confirmed=True)
        self.assertTrue(output["changed"])
        self.assertEqual(deployer.parse_env(path.read_text())["PROVIDER_MODE"], "disabled")
        self.assertEqual(deployer.validate_directory(self.output)["provider_mode"], "disabled")

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
