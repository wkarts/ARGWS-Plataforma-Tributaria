"""Check safe additive upgrade of installed, private Compose settings."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from sync_deploy_env import OPTIONAL_KEYS, read_keys, synchronize, template_path


class SyncDeployEnvTests(unittest.TestCase):
    def test_all_ten_deployment_templates_provide_blank_calculator_parameters(self):
        for target in ("canonical", "docker", "dockge", "portainer", "cloudpanel"):
            for channel in ("develop", "production"):
                with self.subTest(target=target, channel=channel):
                    found = read_keys(template_path(target, channel).read_text(encoding="utf-8"))
                    self.assertTrue(all(found.get(key) == "" for key in OPTIONAL_KEYS))
        self.assertTrue(all(read_keys(template_path("self", "self").read_text()).get(key) == ""
                            for key in OPTIONAL_KEYS))
        with self.assertRaises(ValueError):
            template_path("self", "production")
        with self.assertRaises(ValueError):
            template_path("docker", "self")

    def test_update_preserves_existing_values_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            location = Path(tmp)
            env = location / ".env"
            original = (
                "COMPOSE_PROJECT_NAME=internal-develop\n"
                "POSTGRES_PASSWORD=existing-secret-never-replace\n"
                "TAX_CALCULATOR_REGULAR_URL=http://calculator-regular:8080/api/calculadora/regime-geral\n"
                "MY_CUSTOM_VALUE=preserve-me\n"
            )
            env.write_text(original, encoding="utf-8")
            model = template_path("docker", "develop")
            missing, backup = synchronize(location, model)
            self.assertEqual(backup, None)
            self.assertEqual(len(missing), 3)
            self.assertEqual(env.read_text(encoding="utf-8"), original)
            missing, backup = synchronize(location, model, apply=True)
            self.assertEqual(len(missing), 3)
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            new = env.read_text(encoding="utf-8")
            self.assertTrue(new.startswith(original))
            self.assertEqual(read_keys(new)["POSTGRES_PASSWORD"], "existing-secret-never-replace")
            self.assertEqual(read_keys(new)["MY_CUSTOM_VALUE"], "preserve-me")
            self.assertTrue(all(key in read_keys(new) for key in OPTIONAL_KEYS))
            self.assertEqual(synchronize(location, model, apply=True), ([], None))
            if os.name != "nt":
                self.assertEqual(env.stat().st_mode & 0o077, 0)
                self.assertEqual(backup.stat().st_mode & 0o077, 0)

    def test_duplicate_key_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            location = Path(tmp)
            env = location / ".env"
            env.write_text("APP_TAG=develop\nAPP_TAG=production\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicada"):
                synchronize(location, template_path("docker", "develop"), apply=True)
            env.unlink()
            outside = location / "private-original"
            outside.write_text("APP_TAG=develop\n")
            try:
                env.symlink_to(outside)
            except (OSError, NotImplementedError):
                return
            with self.assertRaisesRegex(ValueError, "sem link"):
                synchronize(location, template_path("docker", "develop"), apply=True)
            self.assertEqual(outside.read_text(), "APP_TAG=develop\n")


if __name__ == "__main__":
    unittest.main()
