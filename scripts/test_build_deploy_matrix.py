"""Executable checks for the exact deployment artifacts sent to customers."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from build_deploy_matrix import REQUIRED, build, collect, verify


class BuildDeployMatrixTests(unittest.TestCase):
    def test_archive_covers_ten_profiles_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = (Path(temp) / name for name in ("matrix-a.zip", "matrix-b.zip"))
            report = build(first)
            self.assertGreaterEqual(report["files"], len(REQUIRED))
            build(second)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            with ZipFile(first) as archive:
                self.assertEqual(set(REQUIRED) - set(archive.namelist()), set())
                self.assertFalse(any(item.filename.endswith("/.env") for item in archive.infolist()))
                for target in ("", "docker/", "dockge/", "portainer/", "cloudpanel/"):
                    for channel in ("develop", "production"):
                        root = f"deploy/{target}{channel}/"
                        text = archive.read(root + "compose.yaml").decode("utf-8")
                        env = archive.read(root + ".env.example").decode("utf-8")
                        for key in (
                            "TAX_CALCULATOR_REGULAR_URL",
                            "TAX_CALCULATOR_REGULAR_VERSION_URL",
                            "TAX_CALCULATOR_SIMPLES_URL",
                            "TAX_CALCULATOR_SIMPLES_VERSION_URL",
                        ):
                            self.assertIn(key, text)
                            self.assertIn(f"{key}=", env)

    def test_archive_verifier_refuses_modified_or_incomplete_content(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "valid.zip"
            build(target)
            expected = collect()
            verify(target, expected)
            altered = dict(expected)
            removed = next(iter(altered))
            altered.pop(removed)
            with self.assertRaisesRegex(ValueError, "extras ou ausentes"):
                verify(target, altered)


if __name__ == "__main__":
    unittest.main()
