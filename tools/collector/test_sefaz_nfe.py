"""Testes locais sintéticos da distribuição NF-e; nenhum acesso à SEFAZ."""

from __future__ import annotations

import base64
import gzip
import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).with_name("sefaz_nfe.py")
SPEC = importlib.util.spec_from_file_location("sefaz_nfe", MODULE_PATH)
assert SPEC and SPEC.loader
NFE_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = NFE_MODULE
SPEC.loader.exec_module(NFE_MODULE)
from sefaz_nfe import (  # noqa: E402
    NFE,
    SOAP12,
    WSDL,
    NFeDistributionClient,
    NFeDistributionError,
    PollState,
    build_envelope,
    parse_response,
)


NOW = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)
CNPJ = "ABCDEFGHIJKL12"  # sintético; nunca usado contra ambiente real
STATE = PollState(CNPJ, "homologation")
XML = (
    b'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
    b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">'
    b'<SignedInfo/></Signature></nfeProc>'
)


def response(cstat: str, *, docs: list[tuple[str, str, bytes]] | None = None,
             last: str = "000000000000001", maximum: str = "000000000000001", environment: str = "2") -> bytes:
    """Gera envelope local; ZIP sempre criado a partir de bytes sintéticos."""

    ET.register_namespace("soap12", SOAP12)
    ET.register_namespace("ws", WSDL)
    ET.register_namespace("nfe", NFE)
    q = lambda ns, name: f"{{{ns}}}{name}"
    env = ET.Element(q(SOAP12, "Envelope"))
    body = ET.SubElement(env, q(SOAP12, "Body"))
    wrapper = ET.SubElement(body, q(WSDL, "nfeDistDFeInteresseResponse"))
    result = ET.SubElement(wrapper, q(WSDL, "nfeDistDFeInteresseResult"))
    fiscal = ET.SubElement(result, q(NFE, "retDistDFeInt"), {"versao": "1.01"})
    for key, val in [("tpAmb", environment), ("cStat", cstat), ("xMotivo", "Resposta sintética"),
                     ("ultNSU", last), ("maxNSU", maximum)]:
        ET.SubElement(fiscal, q(NFE, key)).text = val
    if docs:
        batch = ET.SubElement(fiscal, q(NFE, "loteDistDFeInt"))
        for nsu, schema, xml in docs:
            doc = ET.SubElement(batch, q(NFE, "docZip"), {"NSU": nsu, "schema": schema})
            doc.text = base64.b64encode(gzip.compress(xml, mtime=0)).decode("ascii")
    return ET.tostring(env, encoding="utf-8", xml_declaration=True)


class NFeDistributionTests(unittest.TestCase):
    def test_request_soap12_uses_distnse_layout_and_alpha_cnpj(self) -> None:
        request = ET.fromstring(build_envelope(STATE))
        fiscal = request.find(
            f"{{{SOAP12}}}Body/{{{WSDL}}}nfeDistDFeInteresse/"
            f"{{{WSDL}}}nfeDadosMsg/{{{NFE}}}distDFeInt"
        )
        self.assertIsNotNone(fiscal)
        assert fiscal is not None
        self.assertEqual(fiscal.get("versao"), "1.01")
        self.assertEqual(fiscal.findtext(f"{{{NFE}}}tpAmb"), "2")
        self.assertEqual(fiscal.findtext(f"{{{NFE}}}CNPJ"), CNPJ)
        self.assertEqual(fiscal.findtext(f"{{{NFE}}}distNSU/{{{NFE}}}ultNSU"), STATE.last_nsu)
        self.assertIsNone(fiscal.find(f"{{{NFE}}}consChNFe"))

    def test_document_preserves_exact_bytes_and_does_not_claim_signature_verification(self) -> None:
        raw = response("138", docs=[("000000000000001", "procNFe_v4.00.xsd", XML)])
        result = parse_response(raw, STATE, now=NOW)
        self.assertEqual(result.status, "found")
        self.assertEqual(result.raw_response, raw)
        self.assertEqual(result.next_state.last_nsu, "000000000000001")
        self.assertEqual(result.next_state.next_poll_at, NOW + timedelta(minutes=61))
        doc = result.documents[0]
        self.assertEqual(doc.xml_bytes, XML)
        self.assertEqual(gzip.decompress(doc.compressed_bytes), XML)
        self.assertTrue(doc.schema_matches_root)
        self.assertTrue(doc.signature_element_present)
        self.assertEqual(len(doc.sha256), 64)

    def test_resumo_unsigned_is_valid_distribution_document(self) -> None:
        xml = b'<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"/>'
        parsed = parse_response(response("138", docs=[("1", "resNFe_v1.01.xsd", xml)]), STATE, now=NOW)
        self.assertFalse(parsed.documents[0].signature_element_present)
        self.assertTrue(parsed.documents[0].schema_matches_root)

    def test_continues_only_with_returned_last_nsu(self) -> None:
        raw = response("138", docs=[("1", "resNFe_v1.01.xsd", b'<resNFe xmlns="http://www.portalfiscal.inf.br/nfe"/>')], maximum="9")
        result = parse_response(raw, STATE, now=NOW)
        self.assertIsNone(result.next_state.next_poll_at)
        self.assertEqual(result.next_state.last_nsu, "000000000000001")

    def test_empty_and_abuse_throttle_are_serializable_and_block_requery(self) -> None:
        for cstat, status in [("137", "empty"), ("656", "throttled"), ("999", "rejected")]:
            with self.subTest(cstat=cstat):
                result = parse_response(response(cstat), STATE, now=NOW)
                self.assertEqual(result.status, status)
                self.assertEqual(result.next_state.next_poll_at, NOW + timedelta(minutes=61))
                if cstat != "137":
                    self.assertEqual(result.next_state.last_nsu, STATE.last_nsu)
                restored = PollState.from_mapping(result.next_state.as_dict())
                self.assertEqual(restored, result.next_state)
                client = NFeDistributionClient("/synthetic/cert.pem", "/synthetic/key.pem")
                with self.assertRaisesRegex(NFeDistributionError, "adiada"):
                    client.fetch(restored, now=NOW + timedelta(minutes=1))

    def test_bad_gzip_base64_and_xml_are_rejected_without_checkpoint(self) -> None:
        for xml in [
            b"<!DOCTYPE a><a/>",
            '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a><a/>'.encode("utf-16"),
            b"<a>",
            b"x" * (10 * 1024 * 1024 + 1),
        ]:
            with self.subTest(length=len(xml)):
                raw = response("138", docs=[("1", "resNFe_v1.01.xsd", xml)])
                with self.assertRaises(NFeDistributionError):
                    parse_response(raw, STATE, now=NOW)
        raw = response("138", docs=[("1", "resNFe_v1.01.xsd", XML)])
        with self.assertRaisesRegex(NFeDistributionError, "base64"):
            parse_response(raw.replace(b"H4s", b"!4s", 1), STATE, now=NOW)

    def test_schema_mismatch_is_reported_without_rewriting_xml(self) -> None:
        raw = response("138", docs=[("1", "resNFe_v1.01.xsd", XML)])
        doc = parse_response(raw, STATE, now=NOW).documents[0]
        self.assertFalse(doc.schema_matches_root)
        self.assertEqual(doc.xml_bytes, XML)

    def test_duplicate_nsu_and_environment_mismatch_are_rejected(self) -> None:
        bad = response("138", docs=[("1", "procNFe_v4.00.xsd", XML), ("1", "procNFe_v4.00.xsd", XML)])
        with self.assertRaisesRegex(NFeDistributionError, "duplicado"):
            parse_response(bad, STATE, now=NOW)
        with self.assertRaisesRegex(NFeDistributionError, "Ambiente"):
            parse_response(response("137", environment="1"), STATE, now=NOW)

    def test_transport_uses_standard_tls_context_with_client_pem(self) -> None:
        client = NFeDistributionClient("/synthetic/cert.pem", "/synthetic/key.pem")
        fake = Mock()
        fake.minimum_version = None
        raw = response("137")
        with patch.object(NFE_MODULE.ssl, "create_default_context", return_value=fake), patch.object(
            NFE_MODULE, "_post_soap", return_value=raw
        ) as sent:
            result = client.fetch(STATE, now=NOW)
        self.assertEqual(result.cstat, "137")
        self.assertEqual(fake.minimum_version, NFE_MODULE.ssl.TLSVersion.TLSv1_2)
        fake.load_cert_chain.assert_called_once_with(certfile="/synthetic/cert.pem", keyfile="/synthetic/key.pem")
        self.assertEqual(sent.call_args.args[0], "hom1.nfe.fazenda.gov.br")

    def test_http_request_is_soap12_post_and_does_not_follow_redirect(self) -> None:
        connection = Mock()
        connection.getresponse.return_value.status = 302
        with patch.object(NFE_MODULE.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaises(NFE_MODULE.NFeTransportError):
                NFE_MODULE._post_soap("hom1.nfe.fazenda.gov.br", b"<soap/>", Mock(), 30)
        self.assertEqual(connection.request.call_args.args[:2], ("POST", NFE_MODULE.PATH))
        headers = connection.request.call_args.kwargs["headers"]
        self.assertIn('application/soap+xml; charset=utf-8; action="', headers["Content-Type"])
        connection.close.assert_called_once()

    def test_fetch_exposes_invalid_raw_response_for_quarantine(self) -> None:
        client = NFeDistributionClient("/synthetic/cert.pem", "/synthetic/key.pem")
        raw = b"<?xml version='1.0'?><broken>"
        with patch.object(NFE_MODULE.ssl, "create_default_context"), patch.object(
            NFE_MODULE, "_post_soap", return_value=raw
        ):
            with self.assertRaises(NFeDistributionError) as caught:
                client.fetch(STATE, now=NOW)
        self.assertEqual(caught.exception.raw_response, raw)


if __name__ == "__main__":
    unittest.main()
