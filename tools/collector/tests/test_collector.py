import hashlib
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
from sefaz_nfe import DistributedDocument, DistributionResult, PollState


SIGNED_XML = b'<NFe><Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></NFe>'


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "xml"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "state"
        self.config = collector.validate_config({
            "server_url": "https://fiscal.example.br",
            "company_id": "8813d319-2376-44c3-b147-f1e1a7061518",
            "roots": [str(self.root)], "state_dir": str(self.state),
            "stable_seconds": 0, "retry_min_seconds": 1, "retry_max_seconds": 60,
        })

    def create_xml(self, name="NFe.XML", content=SIGNED_XML):
        path = self.root / name
        path.write_bytes(content)
        os.utime(path, (time.time() - 100, time.time() - 100))
        return path

    def test_signed_bytes_are_unchanged_and_deduplicated_locally(self):
        first = self.create_xml()
        second = self.create_xml("same.xml")
        sent = []

        def fake_sender(config, token, payload, digest):
            sent.append((payload, digest, token))
            return False

        with closing(collector.open_state(self.config)) as conn:
            self.assertEqual(collector.discover(self.config, conn)["queued"], 2)
            result = collector.dispatch(self.config, conn, "test-token", fake_sender)
            self.assertEqual(result["sent"], 1)
            self.assertEqual(result["duplicate"], 1)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0], (SIGNED_XML, hashlib.sha256(SIGNED_XML).hexdigest(), "test-token"))
            self.assertEqual(collector.status(conn)["unique_sent"], 1)
            self.assertEqual(collector.discover(self.config, conn)["queued"], 0)
        self.assertEqual(first.read_bytes(), SIGNED_XML)
        self.assertEqual(second.read_bytes(), SIGNED_XML)

    def test_unstable_unsigned_malformed_and_symlink_are_not_sent(self):
        fresh = self.root / "fresh.xml"
        fresh.write_bytes(SIGNED_XML)
        unsigned = self.create_xml("unsigned.xml", b"<NFe/>")
        malformed = self.create_xml("broken.xml", b"<!DOCTYPE x><bad")
        link = self.root / "linked.xml"
        try:
            link.symlink_to(unsigned)
        except (OSError, NotImplementedError):
            pass
        self.config["stable_seconds"] = 30
        with closing(collector.open_state(self.config)) as conn:
            counts = collector.discover(self.config, conn)
            self.assertEqual(counts["queued"], 0)
            self.assertEqual(counts["ignored"], 2)
            self.assertEqual(collector.status(conn)["files"].get("pending", 0), 0)

    def test_retry_persists_without_leaking_exception_or_token(self):
        self.create_xml()
        with closing(collector.open_state(self.config)) as conn:
            collector.discover(self.config, conn)
            result = collector.dispatch(self.config, conn, "secret-token", lambda *_: (_ for _ in ()).throw(
                RuntimeError("token secret-token at https://somewhere")
            ))
            self.assertEqual(result["failed"], 1)
            row = conn.execute("SELECT attempts,next_retry_at,last_error,state FROM files").fetchone()
            self.assertEqual((row["attempts"], row["state"]), (1, "pending"))
            self.assertGreater(row["next_retry_at"], time.time())
            self.assertEqual(row["last_error"], "RuntimeError")
            self.assertEqual(collector.dispatch(self.config, conn, "secret-token", lambda *_: False)["sent"], 0)

    def test_company_cannot_reuse_another_company_state(self):
        with closing(collector.open_state(self.config)):
            pass
        changed = dict(self.config, company_id="6e9e56ae-0ca8-41fa-b195-b6b3092a7729")
        with self.assertRaisesRegex(ValueError, "outro company_id"):
            collector.open_state(changed)

    def test_http_413_is_terminal_and_401_stops_batch(self):
        self.create_xml("a.xml")
        self.create_xml("b.xml", SIGNED_XML + b"\n")
        with closing(collector.open_state(self.config)) as conn:
            collector.discover(self.config, conn)
            result = collector.dispatch(self.config, conn, "token",
                                        lambda *_: (_ for _ in ()).throw(collector.HTTPStatusError(413)))
            self.assertEqual(result["failed"], 2)
            self.assertEqual(collector.status(conn)["files"]["rejected"], 2)
            rows = conn.execute("SELECT next_retry_at,last_error FROM files").fetchall()
            self.assertTrue(all(r["next_retry_at"] == 0 and r["last_error"] == "HTTP 413" for r in rows))
            conn.execute("UPDATE files SET state='pending',next_retry_at=0")
            conn.commit()
            result = collector.dispatch(self.config, conn, "token",
                                        lambda *_: (_ for _ in ()).throw(collector.HTTPStatusError(401)))
            self.assertEqual(result["failed"], 1)
            self.assertEqual(collector.status(conn)["files"]["pending"], 2)
            self.assertGreaterEqual(conn.execute("SELECT MAX(next_retry_at) FROM files").fetchone()[0], time.time() + 290)

    def test_oversize_file_is_visible_and_never_uploaded(self):
        oversized = self.create_xml("oversize.xml", SIGNED_XML + b" " * (1025 - len(SIGNED_XML)))
        self.config["max_file_bytes"] = 1024
        with closing(collector.open_state(self.config)) as conn:
            result = collector.discover(self.config, conn)
            self.assertEqual(result["ignored"], 1)
            self.assertEqual(collector.status(conn, detailed=True)["recent_errors"][0]["last_error"],
                             "Arquivo excede limite de envio")
            self.assertEqual(collector.dispatch(self.config, conn, "token", lambda *_: self.fail())["sent"], 0)
        self.assertEqual(oversized.stat().st_size, 1025)

    def test_validate_https_and_never_embed_token(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            collector.validate_config(dict(self.config, server_url="http://example.org"))
        with self.assertRaisesRegex(ValueError, "credenciais"):
            collector.validate_config(dict(self.config, token="secret"))

    def test_token_from_file_and_permissions(self):
        path = Path(self.temp.name) / "token"
        path.write_text("unit-token\n", encoding="utf-8")
        path.chmod(0o600)
        setting = dict(self.config, token_file=str(path), token_env="UNDEFINED_COLLECTOR_TOKEN_TEST")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(collector.read_token(setting), "unit-token")
        if os.name != "nt":
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                collector.read_token(setting)

    def test_http_contract_has_raw_xml_and_hash(self):
        captured = []

        class Response:
            status = 201

            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return json.dumps({"duplicate": False, "sha256": digest}).encode()

        class Opener:
            def open(self, request, timeout):
                captured.append((request, timeout))
                return Response()

        digest = hashlib.sha256(SIGNED_XML).hexdigest()
        with patch.object(collector.urllib.request, "build_opener", return_value=Opener()):
            self.assertFalse(collector.send_https(self.config, "test-token", SIGNED_XML, digest))
        request, _ = captured[0]
        self.assertEqual(request.data, SIGNED_XML)
        self.assertTrue(request.full_url.startswith("https://fiscal.example.br/api/v1/documents/ingest?company_id="))
        self.assertEqual(request.get_header("X-content-sha256"), digest)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def test_self_test_offline(self):
        self.assertTrue(collector.self_test()["ok"])

    def test_utf16_doctype_and_entities_are_rejected(self):
        xml = '<!DOCTYPE NFe [<!ENTITY unsafe "text">]><NFe>&unsafe;</NFe>'.encode("utf-16")
        with self.assertRaisesRegex(ValueError, "DOCTYPE"):
            collector.has_signature(xml)

    def test_response_without_confirmed_hash_is_not_accepted(self):
        class Response:
            status = 201

            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"duplicate":false}'

        class Opener:
            def open(self, request, timeout): return Response()

        with patch.object(collector.urllib.request, "build_opener", return_value=Opener()):
            with self.assertRaisesRegex(RuntimeError, "sem hash"):
                collector.send_https(self.config, "token", SIGNED_XML, hashlib.sha256(SIGNED_XML).hexdigest())

    def test_sefaz_spool_checkpoint_summary_and_upload(self):
        cnpj = "12345678000195"
        self.config = collector.validate_config(dict(self.config, roots=[], sefaz_nfe={
            "cnpj": cnpj, "environment": "homologation",
            "cert_pem": str(Path(self.temp.name) / "cert.pem"),
            "key_pem": str(Path(self.temp.name) / "key.pem"),
        }))
        signed = DistributedDocument(
            "000000000000001", "procNFe_v4.00.xsd", b"raw-gzip-nfe", SIGNED_XML,
            hashlib.sha256(SIGNED_XML).hexdigest(), "{urn:nfe}nfeProc", True, True)
        summary_payload = b"<resNFe>resumo</resNFe>"
        summary = DistributedDocument(
            "000000000000002", "resNFe_v1.01.xsd", b"raw-gzip-summary", summary_payload,
            hashlib.sha256(summary_payload).hexdigest(), "{urn:nfe}resNFe", True, False)
        response = b"<soap>bruto preservado</soap>"
        end = PollState(cnpj, "homologation", "000000000000002")
        result = DistributionResult("138", "Documentos localizados", "found", response,
                                    (signed, summary), end, "000000000000003")
        calls = []

        class Client:
            def fetch(self, state, now):
                calls.append(state.last_nsu)
                return result

        with closing(collector.open_state(self.config)) as conn:
            polled = collector.poll_sefaz(self.config, conn, client_factory=lambda *_: Client())
            self.assertEqual(polled["last_nsu"], end.last_nsu)
            self.assertEqual(calls, ["000000000000000"])
            current, _ = collector._sefaz_state(self.config, conn)
            self.assertEqual(current.last_nsu, "000000000000002")
            spool = collector.sefaz_root(self.config)
            self.assertEqual(next((spool / "ready").glob("*.xml")).read_bytes(), SIGNED_XML)
            self.assertEqual(next((spool / "summary_unprocessed").glob("*.xml")).read_bytes(), summary_payload)
            self.assertEqual(next((spool / "responses").glob("*.soap.xml")).read_bytes(), response)
            self.assertEqual(next((spool / "compressed").glob("*001*.gz")).read_bytes(), b"raw-gzip-nfe")
            discovered = collector.discover(self.config, conn)
            self.assertEqual(discovered["queued"], 1)
            self.assertEqual(discovered["examined"], 1)
            upload = collector.dispatch(self.config, conn, "agent-token", lambda *_: False)
            self.assertEqual(upload["sent"], 1)
            state = collector.status(conn)["sefaz_nfe"]
            self.assertEqual(state["documents_by_classification"], {"ready": 1, "summary_unprocessed": 1})
            self.assertEqual(state["ready_uploads"], {"sent": 1})

    def test_sefaz_spool_failure_never_advances_nsu(self):
        cnpj = "12345678000195"
        self.config = collector.validate_config(dict(self.config, sefaz_nfe={
            "cnpj": cnpj, "environment": "homologation",
            "cert_pem": str(Path(self.temp.name) / "cert.pem"),
            "key_pem": str(Path(self.temp.name) / "key.pem"),
        }))
        state = PollState(cnpj, "homologation", "000000000000001")
        result = DistributionResult("138", "localizado", "found", b"<soap/>", (), state,
                                    "000000000000001")

        class Client:
            def fetch(self, state, now): return result

        with closing(collector.open_state(self.config)) as conn:
            with patch.object(collector, "durable_write", side_effect=OSError("disk failure")):
                output = collector.poll_sefaz(self.config, conn, client_factory=lambda *_: Client())
            self.assertEqual(output["status"], "error")
            current, row = collector._sefaz_state(self.config, conn)
            self.assertEqual(current.last_nsu, "000000000000000")
            self.assertEqual(row["retry_count"], 1)

    def test_sefaz_spool_limit_blocks_fetch_without_advance(self):
        self.config = collector.validate_config(dict(self.config, sefaz_nfe={
            "cnpj": "12345678000195", "environment": "homologation",
            "cert_pem": str(Path(self.temp.name) / "cert.pem"),
            "key_pem": str(Path(self.temp.name) / "key.pem"),
            "max_spool_bytes": 256 * 1024 * 1024,
        }))
        with closing(collector.open_state(self.config)) as conn:
            with patch.object(collector, "spool_bytes", return_value=150 * 1024 * 1024):
                output = collector.poll_sefaz(self.config, conn,
                                              client_factory=lambda *_: self.fail("fetch não deveria ocorrer"))
            self.assertEqual(output["status"], "storage_full")
            self.assertIsNone(conn.execute("SELECT 1 FROM sefaz_state").fetchone())


if __name__ == "__main__":
    unittest.main()
