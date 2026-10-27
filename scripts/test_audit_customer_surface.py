"""Regression checks for distributed customer-facing identities."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from audit_customer_surface import audit, main


class CustomerSurfaceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.web = self.base / "dist"
        self.web.mkdir()
        for name in ("index.html", "manifest.webmanifest", "config.js", "sw.js"):
            (self.web / name).write_text("Fiscal XML\n", encoding="utf-8")
        self.self_dir = self.base / "self"
        self.self_dir.mkdir()
        (self.self_dir / "compose.yaml").write_text("name: fiscal-local\n", encoding="utf-8")
        self.release = self.base / "release"
        self.release.mkdir()
        (self.release / "fiscal-xml-collector-setup-windows-x64.exe").write_bytes(b"MZ")

    def test_neutral_web_self_and_asset_names(self) -> None:
        self.assertEqual(audit(self.web, self.self_dir, self.release), [])
        self.assertEqual(main(["--web-dist", str(self.web), "--self-dir", str(self.self_dir),
                               "--release-dir", str(self.release)]), 0)

    def test_web_manifest_config_service_worker_and_nested_assets(self) -> None:
        samples = {"index.html": "Reforma Tributária", "manifest.webmanifest": "ARGWS",
                   "config.js": "wkarts", "sw.js": "cache-develop"}
        for filename, value in samples.items():
            (self.web / filename).write_text(value, encoding="utf-8")
        assets = self.web / "assets"
        assets.mkdir()
        (assets / "PIGE360-icon.svg").write_text("<svg/>", encoding="utf-8")
        issues = audit(self.web)
        for filename in samples:
            self.assertTrue(any(filename in issue for issue in issues), (filename, issues))
        self.assertTrue(any("PIGE360-icon.svg" in issue for issue in issues))

    def test_self_template_and_release_names(self) -> None:
        (self.self_dir / "compose.yaml").write_text("name: reforma_tributaria\n", encoding="utf-8")
        (self.release / "ARGWS-Collector-main.exe").write_bytes(b"MZ")
        issues = audit(self.web, self.self_dir, self.release)
        self.assertTrue(any("compose.yaml" in issue for issue in issues))
        self.assertTrue(any("ARGWS-Collector-main.exe" in issue for issue in issues))

    def test_missing_artifacts_fail_closed(self) -> None:
        (self.web / "sw.js").unlink()
        self.assertTrue(any("sw.js" in issue for issue in audit(self.web)))
        self.assertTrue(audit(self.base / "absent"))

    def test_self_archive_inspects_embedded_sources(self) -> None:
        archive = self.release / "Fiscal-Self-Deploy-preview.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("apps/api/.env.example", "POSTGRES_USER=reforma\n")
        self.assertTrue(any("apps/api/.env.example" in issue
                            for issue in audit(self.web, self.self_dir, self.release)))
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("apps/api/.env.example", "POSTGRES_USER=fiscal\n")
        self.assertEqual(audit(self.web, self.self_dir, self.release), [])


    def test_technical_source_in_self_archive_can_mention_channel_and_fiscal_subject(self) -> None:
        archive = self.release / "Fiscal-Self-Deploy-preview.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("apps/api/app/central_reforma_api.py",
                            "router_tag = 'central-reforma'\n")
            output.writestr("scripts/sync_deploy_env.py",
                            "CHANNELS = ('develop', 'production', 'self')\n")
        self.assertEqual(audit(self.web, self.self_dir, self.release), [])

        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("apps/api/app/fiscal_operations_api.py",
                            "BRANDING_OWNER = 'wkarts'\n")
        self.assertTrue(any("identidade comercial fixa" in issue
                            for issue in audit(self.web, self.self_dir, self.release)))

    def test_backend_source_does_not_exempt_env_or_visible_frontend(self) -> None:
        archive = self.release / "Fiscal-Self-Deploy-preview.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("apps/api/.env.example", "POSTGRES_USER=reforma\n")
            output.writestr("apps/web/src/theme.ts", "export const product = 'ARGWS';\n")
        issues = audit(self.web, self.self_dir, self.release)
        self.assertTrue(any("apps/api/.env.example" in issue for issue in issues))
        self.assertTrue(any("apps/web/src/theme.ts" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
