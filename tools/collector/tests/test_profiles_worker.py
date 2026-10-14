"""Contratos locais: isolamento por empresa, segredos e serviço multiperfil."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector
import gui
import profiles
import worker


FIRST = "8813d319-2376-44c3-b147-f1e1a7061518"
SECOND = "6e9e56ae-0ca8-41fa-b195-b6b3092a7729"


class ProfileWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "application"
        self.xml = Path(temp.name) / "xml"
        self.xml.mkdir()

    def save(self, company_id: str = FIRST, cnpj: str = "12345678000195", *,
             token: str | None = "rta_secret_first", enabled: bool = True) -> dict:
        return profiles.save_profile(self.home, company_id=company_id, cnpj=cnpj,
            name="Cliente de teste", server_url="https://fiscal.example.org",
            roots=[str(self.xml)], token=token, enabled=enabled, poll_seconds=15)

    def test_two_companies_have_isolated_databases_and_secrets(self) -> None:
        self.save()
        self.save(SECOND, "98765432000199", token="rta_secret_second")
        one = profiles.load_config(self.home, FIRST)
        two = profiles.load_config(self.home, SECOND)
        self.assertNotEqual(one["state_dir"], two["state_dir"])
        self.assertNotEqual(one["token_file"], two["token_file"])
        with closing(collector.open_state(one)) as db:
            db.execute("INSERT INTO digests(sha256,accepted_at) VALUES (?,?)", ("a" * 64, 1))
            db.commit()
        with closing(collector.open_state(two)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM digests").fetchone()[0], 0)
        self.assertEqual(len(profiles.list_profiles(self.home)), 2)
        self.assertNotIn("rta_secret_first", (self.home / "catalog.sqlite3").read_bytes().decode("latin-1"))
        self.assertNotIn("rta_secret_first", profiles.profile_paths(self.home, FIRST)["config"].read_text())
        if os.name != "nt":
            self.assertEqual(profiles.profile_paths(self.home, FIRST)["token"].stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)

    def test_token_rotation_preserves_other_company_and_state(self) -> None:
        self.save()
        self.save(SECOND, "98765432000199", token="rta_secret_second")
        with closing(collector.open_state(profiles.load_config(self.home, FIRST))) as db:
            pass
        state = profiles.profile_paths(self.home, FIRST)["state"] / "state.sqlite3"
        self.save(token="rta_new_first")
        self.assertTrue(state.is_file())
        self.assertEqual(collector.read_token(profiles.load_config(self.home, FIRST),
                                              allow_environment=False), "rta_new_first")
        self.assertEqual(collector.read_token(profiles.load_config(self.home, SECOND),
                                              allow_environment=False), "rta_secret_second")
        self.save(token=None)
        self.assertEqual(collector.read_token(profiles.load_config(self.home, FIRST),
                                              allow_environment=False), "rta_new_first")

    def test_worker_never_substitutes_global_token_file_for_company_token(self) -> None:
        self.save()
        self.save(SECOND, "98765432000199", token="rta_secret_second")
        outsider = self.home / "outsider-token"
        outsider.write_text("rta_wrong\n", encoding="utf-8")
        outsider.chmod(0o600)
        with patch.dict(os.environ, {"FISCAL_COLLECTOR_TOKEN_FILE": str(outsider),
                                      "FISCAL_COLLECTOR_PROFILE_" + FIRST.replace("-", "").upper(): "wrong"}):
            received = []

            def fake_cycle(config, _db, token):
                received.append((config["company_id"], token))
                return {"delivery": {"failed": 0}}

            with patch.object(collector, "run_cycle", side_effect=fake_cycle):
                value, code = worker.run_command(self.home, "once")
        self.assertEqual(code, 0)
        self.assertEqual(value["count"], 2)
        self.assertEqual(set(received), {(FIRST, "rta_secret_first"), (SECOND, "rta_secret_second")})

    def test_disabled_profiles_not_collected_and_no_active_profile_fails_check(self) -> None:
        self.save(enabled=False)
        value, code = worker.run_command(self.home, "check")
        self.assertEqual((code, value["count"], value["failed"]), (1, 0, True))
        value, code = worker.run_command(self.home, "status")
        self.assertEqual((code, value["count"], value["profiles"][0]["enabled"]), (0, 1, False))
        profiles.set_enabled(self.home, FIRST, True)
        checked, code = worker.run_command(self.home, "check")
        self.assertEqual(code, 0)
        self.assertTrue(checked["profiles"][0]["config_valid"])

    def test_invalid_profiles_do_not_corrupt_catalog(self) -> None:
        self.save()
        with self.assertRaisesRegex(ValueError, "cadastrado"):
            self.save(SECOND, "12345678000195", token="rta_other")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            profiles.save_profile(self.home, company_id=SECOND, cnpj="98765432000199",
                name="Outro", server_url="http://insecure.example", roots=[str(self.xml)], token="secret")
        self.assertEqual(len(profiles.list_profiles(self.home)), 1)
        paths = profiles.profile_paths(self.home, FIRST)
        paths["config"].unlink()
        paths["config"].symlink_to(Path(__file__))
        with self.assertRaisesRegex(ValueError, "regular"):
            profiles.load_config(self.home, FIRST)

    def test_sefaz_certificate_pair_is_copied_into_private_company_directory(self) -> None:
        cert = self.home.parent / "source-cert.pem"
        key = self.home.parent / "source-key.pem"
        cert.write_bytes(b"synthetic certificate")
        key.write_bytes(b"synthetic private key")
        key.chmod(0o600)
        sefaz = {"cnpj": "12.345.678/0001-95", "environment": "homologation",
                 "cert_pem": str(cert), "key_pem": str(key)}
        with patch.object(profiles.ssl, "create_default_context"):
            profiles.save_profile(self.home, company_id=FIRST, cnpj="12345678000195",
                name="Cliente de teste", server_url="https://fiscal.example.org", roots=[],
                token="rta_123", sefaz_nfe=sefaz)
        config = profiles.load_config(self.home, FIRST)
        stored = config["sefaz_nfe"]
        self.assertEqual(stored["cnpj"], "12345678000195")
        self.assertNotEqual(stored["key_pem"], str(key))
        self.assertTrue(Path(stored["key_pem"]).is_relative_to(self.home / "companies" / FIRST))
        self.assertEqual(Path(stored["key_pem"]).read_bytes(), key.read_bytes())
        if os.name != "nt":
            self.assertEqual(Path(stored["key_pem"]).stat().st_mode & 0o777, 0o600)

    def test_rotation_prunes_only_expired_unreferenced_pairs_after_commit(self) -> None:
        cert = self.home.parent / "source-cert.pem"
        key = self.home.parent / "source-key.pem"
        cert.write_bytes(b"synthetic certificate")
        key.write_bytes(b"synthetic private key")
        key.chmod(0o600)
        sefaz = {"cnpj": "12345678000195", "environment": "homologation",
                 "cert_pem": str(cert), "key_pem": str(key), "max_spool_bytes": 512 * 1024 * 1024}

        def rotate() -> Path:
            with patch.object(profiles.ssl, "create_default_context"):
                profiles.save_profile(self.home, company_id=FIRST, cnpj="12345678000195",
                    name="Cliente de teste", server_url="https://fiscal.example.org", roots=[],
                    token="rta_123", sefaz_nfe=sefaz)
            return Path(profiles.load_config(self.home, FIRST)["sefaz_nfe"]["key_pem"]).parent

        first = rotate()
        second = rotate()
        self.assertTrue(first.is_dir())  # rollback imediato permanece possível.
        os.utime(first, (time.time() - 2 * 86400, time.time() - 2 * 86400))
        third = rotate()
        self.assertFalse(first.exists())
        self.assertTrue(second.is_dir())
        self.assertTrue(third.is_dir())
        backup = json.loads((self.home / "companies" / FIRST / "config.previous.json").read_text())
        self.assertEqual(Path(backup["sefaz_nfe"]["key_pem"]).parent, second)
        self.assertEqual(profiles.load_config(self.home, FIRST)["sefaz_nfe"]["max_spool_bytes"],
                         512 * 1024 * 1024)
        # Diretório de uma gravação interrompida é reaproveitável por 24 h.
        orphan = third.parent / ("f" * 32)
        orphan.mkdir()
        (orphan / "key.pem").write_bytes(b"orphan")
        self.assertEqual(profiles.prune_old_certificates(self.home, FIRST), 0)
        self.assertEqual(profiles.prune_old_certificates(self.home, FIRST,
            now=time.time() + profiles.CERTIFICATE_GRACE_SECONDS + 1), 1)
        self.assertFalse(orphan.exists())

    def test_existing_numeric_configuration_is_preserved_and_validated(self) -> None:
        self.save()
        path = profiles.profile_paths(self.home, FIRST)["config"]
        config = json.loads(path.read_text())
        config.update(stable_seconds=17, timeout_seconds=45, max_file_bytes=2097152,
                      batch_limit=17, retry_min_seconds=20, retry_max_seconds=180,
                      poll_seconds=73)
        profiles.atomic_private_write(path, json.dumps(config).encode())
        profiles.save_profile(self.home, company_id=FIRST, cnpj="12345678000195",
            name="Cliente de teste", server_url="https://fiscal.example.org",
            roots=[str(self.xml)], token=None)
        stored = profiles.load_config(self.home, FIRST)
        for name in ("stable_seconds", "timeout_seconds", "max_file_bytes", "batch_limit",
                     "retry_min_seconds", "retry_max_seconds", "poll_seconds"):
            self.assertEqual(stored[name], config[name])
        # A GUI pode alterar um campo específico sem mudar os demais.
        profiles.save_profile(self.home, company_id=FIRST, cnpj="12345678000195",
            name="Cliente de teste", server_url="https://fiscal.example.org",
            roots=[str(self.xml)], token=None, poll_seconds=15)
        self.assertEqual(profiles.load_config(self.home, FIRST)["poll_seconds"], 15)
        with self.assertRaisesRegex(ValueError, "retry_max_seconds"):
            profiles.save_profile(self.home, company_id=FIRST, cnpj="12345678000195",
                name="Cliente de teste", server_url="https://fiscal.example.org",
                roots=[str(self.xml)], token=None, retry_min_seconds=500,
                retry_max_seconds=100)
        self.assertEqual(profiles.load_config(self.home, FIRST)["retry_max_seconds"], 180)

    def test_interrupted_rotation_keeps_previous_certificate_usable(self) -> None:
        cert = self.home.parent / "cert.pem"
        key = self.home.parent / "key.pem"
        cert.write_bytes(b"cert A")
        key.write_bytes(b"key A")
        key.chmod(0o600)
        source = {"cnpj": "12345678000195", "environment": "homologation",
                  "cert_pem": str(cert), "key_pem": str(key)}

        def save() -> None:
            profiles.save_profile(self.home, company_id=FIRST, cnpj="12345678000195",
                name="Empresa", server_url="https://fiscal.example.org", roots=[],
                token="rta_token", sefaz_nfe=source)

        with patch.object(profiles.ssl, "create_default_context"):
            save()
        before = profiles.load_config(self.home, FIRST)
        original_write = profiles.atomic_private_write

        def interrupted(path: Path, data: bytes) -> None:
            if path.name == "config.json":
                raise OSError("simulated power failure")
            original_write(path, data)

        cert.write_bytes(b"cert B")
        key.write_bytes(b"key B")
        with patch.object(profiles.ssl, "create_default_context"), patch.object(
                profiles, "atomic_private_write", side_effect=interrupted):
            with self.assertRaises(OSError):
                save()
        self.assertEqual(profiles.load_config(self.home, FIRST), before)
        self.assertEqual(Path(before["sefaz_nfe"]["key_pem"]).read_bytes(), b"key A")
        self.assertEqual(profiles.prune_old_certificates(self.home, FIRST,
            now=time.time() + profiles.CERTIFICATE_GRACE_SECONDS + 1), 1)
        self.assertTrue(Path(before["sefaz_nfe"]["key_pem"]).is_file())

    def test_branding_is_local_neutral_and_rejects_traversal(self) -> None:
        self.home.mkdir()
        self.assertEqual(gui.branding(self.home)["display_name"], "Gestão Fiscal")
        (self.home / "branding.json").write_text(json.dumps({"display_name": "Meu escritório",
            "logo_png": "../../escape.png", "primary_color": "red"}), encoding="utf-8")
        brand = gui.branding(self.home)
        self.assertEqual(brand["display_name"], "Meu escritório")
        self.assertEqual(brand["primary_color"], "#255896")
        self.assertNotIn("logo_png", brand)


if __name__ == "__main__":
    unittest.main()
