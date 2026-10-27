"""Regressions for Redis Compose startup across every deployment target.

The old command was a scalar, which Compose split into several argv entries;
with entrypoint sh -ec, only "umask" was executed and the container restarted.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap
import unittest

from validate_deploys import DEPLOY, CHANNELS, TARGETS, redis_startup_contract


class RedisComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canonical = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
        self.first = self.canonical.index("  redis:\n")
        self.last = self.canonical.index("  rabbitmq:\n", self.first)
        self.redis = self.canonical[self.first:self.last]

    def test_all_ten_profiles_use_the_canonical_single_script(self) -> None:
        self.assertEqual(redis_startup_contract(self.canonical), [])
        for target in (None, *TARGETS):
            for channel in CHANNELS:
                folder = DEPLOY / target / channel if target else DEPLOY / channel
                with self.subTest(target=target, channel=channel):
                    content = (folder / "compose.yaml").read_text(encoding="utf-8")
                    self.assertEqual(content, self.canonical)
                    self.assertEqual(redis_startup_contract(content), [])

    def test_regression_old_scalar_command_is_rejected(self) -> None:
        original = self.redis[self.redis.index("    command:\n"):
                              self.redis.index("    volumes:")]
        old = ("    command: 'umask 077; printf \"requirepass %s\\n\" "
               "\"$$REDIS_PASSWORD\" > /tmp/redis.conf; "
               "exec redis-server /tmp/redis.conf --appendonly yes'\n")
        invalid = self.canonical.replace(original, old)
        self.assertTrue(any("lista" in error
                            for error in redis_startup_contract(invalid)))

    def test_unescaped_password_is_rejected_before_compose_interpolates_it(self) -> None:
        invalid = self.canonical.replace(
            '"$$REDIS_PASSWORD" > /tmp/redis.conf',
            '"$REDIS_PASSWORD" > /tmp/redis.conf',
        )
        self.assertNotEqual(invalid, self.canonical)
        self.assertTrue(any("escape" in error
                            for error in redis_startup_contract(invalid)))

    @unittest.skipUnless(os.name == "posix", "POSIX shell available only in Linux CI")
    def test_single_argument_script_has_valid_shell_syntax(self) -> None:
        body = self.redis.split("    command:\n      - |\n", 1)[1]
        body = body.split("\n    volumes:", 1)[0]
        script = textwrap.dedent(body)
        result = subprocess.run(["/bin/sh", "-n", "-c", script],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exec redis-server /tmp/redis.conf --appendonly yes", script)
        self.assertIn("$$REDIS_PASSWORD", script)


if __name__ == "__main__":
    unittest.main()
