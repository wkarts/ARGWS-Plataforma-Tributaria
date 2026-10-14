"""Consulta opcional à Distribuição DF-e da NF-e (NT 2014.002 v1.40).

Somente o modo sequencial ``distNSU`` é implementado. O chamador deve persistir
``PollState`` junto com os documentos, por CNPJ e ambiente, antes de consultar
novamente. O módulo não manifesta destinatário, valida assinatura XML ou
substitui a guarda do retorno bruto e dos XMLs pelo sistema integrador.

Referências oficiais (consultadas em 14/10/2026):
* https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=uWO2d%2FgTuWg%3D
* https://www.nfe.fazenda.gov.br/portal/webservices.aspx
* https://hom.nfe.fazenda.gov.br/portal/webservices.aspx
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import re
import ssl
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping


SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
NFE = "http://www.portalfiscal.inf.br/nfe"
ACTION = f"{WSDL}/nfeDistDFeInteresse"
ENDPOINTS = {
    "production": "www1.nfe.fazenda.gov.br",
    "homologation": "hom1.nfe.fazenda.gov.br",
}
PATH = "/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_DOC_ZIP_BYTES = 10 * 1024 * 1024
MAX_XML_BYTES = 10 * 1024 * 1024
MAX_BATCH_XML_BYTES = 40 * 1024 * 1024
MAX_DOCUMENTS = 50
COOLDOWN = timedelta(minutes=61)  # NT: mínimo 1 h após 137 ou fim da sequência
SCHEMA_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*_v[0-9]+\.[0-9]+\.xsd\Z")
CNPJ_FORMAT = re.compile(r"[A-Z0-9]{12}[0-9]{2}\Z")
ET.register_namespace("soap12", SOAP12)
ET.register_namespace("ws", WSDL)
ET.register_namespace("nfe", NFE)


class NFeDistributionError(ValueError):
    """Dados, resposta ou estado incompatíveis com o contrato de distribuição."""

    def __init__(self, message: str, *, raw_response: bytes | None = None) -> None:
        super().__init__(message)
        # Quando a análise de um SOAP recebido falhar, a evidência original
        # fica disponível para quarentena, sem alterar o checkpoint.
        self.raw_response = raw_response


class NFeTransportError(RuntimeError):
    """Falha de transporte; o chamador deve aplicar backoff sem avançar NSU."""


def _nsu(value: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 15:
        raise NFeDistributionError("NSU deve ter de 1 a 15 dígitos ASCII")
    return value.zfill(15)


def _cnpj(value: str) -> str:
    if not isinstance(value, str) or not CNPJ_FORMAT.fullmatch(value):
        raise NFeDistributionError("CNPJ deve ter 12 posições alfanuméricas e dois dígitos verificadores")
    return value


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _xml_root(raw: bytes) -> ET.Element:
    # XML assinado é mantido nos bytes originais; o parser é usado somente para
    # detectar estrutura, sem serializar novamente nem verificar assinatura.
    # A inspeção por bytes não detectaria, por exemplo, um DOCTYPE em UTF-16.
    guard = expat.ParserCreate()

    def reject(*_args: object) -> None:
        raise NFeDistributionError("DTD/ENTITY não são aceitos")

    guard.StartDoctypeDeclHandler = reject
    guard.EntityDeclHandler = reject
    guard.ExternalEntityRefHandler = reject
    guard.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        guard.Parse(raw, True)
        return ET.fromstring(raw)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise NFeDistributionError("XML de distribuição inválido") from exc


def _required(parent: ET.Element, tag: str) -> str:
    node = parent.find(_q(NFE, tag))
    if node is None or not node.text or not node.text.strip():
        raise NFeDistributionError(f"Campo obrigatório ausente: {tag}")
    return node.text.strip()


@dataclass(frozen=True)
class PollState:
    """Checkpoint serializável por CNPJ/ambiente; gravação cabe ao chamador."""

    cnpj: str
    environment: Literal["production", "homologation"]
    last_nsu: str = "000000000000000"
    next_poll_at: datetime | None = None

    def __post_init__(self) -> None:
        _cnpj(self.cnpj)
        if self.environment not in ENDPOINTS:
            raise NFeDistributionError("Ambiente inválido")
        if self.last_nsu != _nsu(self.last_nsu):
            raise NFeDistributionError("last_nsu deve ter exatamente 15 dígitos")
        if self.next_poll_at is not None and (
            not isinstance(self.next_poll_at, datetime) or self.next_poll_at.utcoffset() is None
        ):
            raise NFeDistributionError("next_poll_at precisa incluir fuso horário")

    def as_dict(self) -> dict[str, str | None]:
        return {
            "cnpj": self.cnpj,
            "environment": self.environment,
            "last_nsu": self.last_nsu,
            "next_poll_at": self.next_poll_at.isoformat() if self.next_poll_at else None,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PollState:
        next_poll_at = value.get("next_poll_at")
        if next_poll_at is not None:
            if not isinstance(next_poll_at, str):
                raise NFeDistributionError("next_poll_at inválido")
            try:
                next_poll_at = datetime.fromisoformat(next_poll_at)
            except ValueError as exc:
                raise NFeDistributionError("next_poll_at inválido") from exc
        return cls(
            cnpj=value["cnpj"],
            environment=value["environment"],
            last_nsu=value.get("last_nsu", "000000000000000"),
            next_poll_at=next_poll_at,
        )


@dataclass(frozen=True)
class DistributedDocument:
    nsu: str
    declared_schema: str
    compressed_bytes: bytes  # docZip após base64, sem alteração
    xml_bytes: bytes  # bytes descompactados, sem serialização/regravação
    sha256: str  # dos xml_bytes
    xml_root: str
    schema_matches_root: bool | None  # None: família ainda não conhecida
    signature_element_present: bool  # não é verificação de assinatura


@dataclass(frozen=True)
class DistributionResult:
    cstat: str
    reason: str
    status: Literal["found", "empty", "throttled", "rejected"]
    raw_response: bytes  # SOAP recebido, inclusive nos cStat de rejeição
    documents: tuple[DistributedDocument, ...]
    next_state: PollState
    max_nsu: str | None


def build_envelope(state: PollState) -> bytes:
    """SOAP 1.2 / distDFeInt 1.01, apenas distribuição sequencial por NSU."""

    envelope = ET.Element(_q(SOAP12, "Envelope"))
    body = ET.SubElement(envelope, _q(SOAP12, "Body"))
    method = ET.SubElement(body, _q(WSDL, "nfeDistDFeInteresse"))
    payload = ET.SubElement(method, _q(WSDL, "nfeDadosMsg"))
    request = ET.SubElement(payload, _q(NFE, "distDFeInt"), {"versao": "1.01"})
    ET.SubElement(request, _q(NFE, "tpAmb")).text = "1" if state.environment == "production" else "2"
    ET.SubElement(request, _q(NFE, "CNPJ")).text = state.cnpj
    distribution = ET.SubElement(request, _q(NFE, "distNSU"))
    ET.SubElement(distribution, _q(NFE, "ultNSU")).text = state.last_nsu
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def _post_soap(host: str, body: bytes, context: ssl.SSLContext, timeout: float) -> bytes:
    # HTTPSConnection não segue redirects nem lê configurações implícitas de proxy.
    connection = http.client.HTTPSConnection(host, timeout=timeout, context=context)
    try:
        connection.request(
            "POST",
            PATH,
            body=body,
            headers={
                "Content-Type": f'application/soap+xml; charset=utf-8; action="{ACTION}"',
                "Accept": "application/soap+xml, text/xml",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise NFeTransportError(f"Serviço NF-e respondeu HTTP {response.status}")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise NFeDistributionError("Resposta SOAP excede o limite de tamanho")
        return raw
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise NFeTransportError("Falha de conexão TLS com Distribuição DF-e") from exc
    finally:
        connection.close()


def _document(node: ET.Element) -> DistributedDocument:
    nsu = _nsu(node.attrib.get("NSU", ""))
    schema = node.attrib.get("schema", "")
    if not SCHEMA_NAME.fullmatch(schema):
        raise NFeDistributionError("schema de docZip ausente ou inválido")
    encoded = "".join((node.text or "").split())
    if not encoded or len(encoded) > ((MAX_DOC_ZIP_BYTES + 2) // 3) * 4 + 4:
        raise NFeDistributionError("docZip vazio ou excessivamente grande")
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise NFeDistributionError("docZip não contém base64 válido") from exc
    if len(compressed) > MAX_DOC_ZIP_BYTES or not compressed.startswith(b"\x1f\x8b"):
        raise NFeDistributionError("docZip não contém um gzip dentro do limite")
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        xml_bytes = inflater.decompress(compressed, MAX_XML_BYTES + 1)
        if len(xml_bytes) > MAX_XML_BYTES or inflater.unconsumed_tail:
            raise NFeDistributionError("XML descompactado excede o limite")
        xml_bytes += inflater.flush(MAX_XML_BYTES + 1 - len(xml_bytes))
        if len(xml_bytes) > MAX_XML_BYTES or not inflater.eof or inflater.unused_data:
            raise NFeDistributionError("gzip truncado, concatenado ou excessivo")
    except zlib.error as exc:
        raise NFeDistributionError("docZip contém gzip inválido") from exc
    root = _xml_root(xml_bytes)
    # O nome do XSD identifica a família, mas não fazemos validação XSD aqui.
    # procNFe_v4.00.xsd tem elemento raiz nfeProc.
    family = schema.split("_v", 1)[0]
    expected = {"procNFe": "nfeProc", "resNFe": "resNFe", "resEvento": "resEvento", "procEventoNFe": "procEventoNFe"}.get(family)
    schema_matches = (root.tag == _q(NFE, expected)) if expected else None
    signature_present = any(n.tag == _q("http://www.w3.org/2000/09/xmldsig#", "Signature") for n in root.iter())
    return DistributedDocument(
        nsu=nsu,
        declared_schema=schema,
        compressed_bytes=compressed,
        xml_bytes=xml_bytes,
        sha256=hashlib.sha256(xml_bytes).hexdigest(),
        xml_root=root.tag,
        schema_matches_root=schema_matches,
        signature_element_present=signature_present,
    )


def parse_response(raw: bytes, state: PollState, *, now: datetime | None = None) -> DistributionResult:
    """Interpreta SOAP sem avançar estado quando houver lote inconsistente."""

    if len(raw) > MAX_RESPONSE_BYTES:
        raise NFeDistributionError("Resposta SOAP excede o limite de tamanho")
    when = now or datetime.now(timezone.utc)
    if when.utcoffset() is None:
        raise NFeDistributionError("now precisa incluir fuso horário")
    root = _xml_root(raw)
    if root.tag not in (_q(SOAP12, "Envelope"), _q("http://schemas.xmlsoap.org/soap/envelope/", "Envelope")):
        raise NFeDistributionError("Envelope SOAP inválido")
    soap_ns = root.tag[1:].split("}", 1)[0]
    soap_body = root.find(_q(soap_ns, "Body"))
    if soap_body is None:
        raise NFeDistributionError("SOAP Body ausente")
    if soap_body.find(_q(soap_ns, "Fault")) is not None:
        raise NFeDistributionError("SOAP Fault da Distribuição DF-e")
    response = soap_body.find(_q(WSDL, "nfeDistDFeInteresseResponse"))
    result = response.find(_q(WSDL, "nfeDistDFeInteresseResult")) if response is not None else None
    fiscal = result.find(_q(NFE, "retDistDFeInt")) if result is not None else None
    if fiscal is None or fiscal.get("versao") != "1.01":
        raise NFeDistributionError("retDistDFeInt ausente ou versão não suportada")
    tp_amb = _required(fiscal, "tpAmb")
    if tp_amb != ("1" if state.environment == "production" else "2"):
        raise NFeDistributionError("Ambiente da resposta difere do consultado")
    cstat = _required(fiscal, "cStat")
    if not cstat.isascii() or not cstat.isdigit() or len(cstat) != 3:
        raise NFeDistributionError("cStat inválido")
    reason = _required(fiscal, "xMotivo")
    last_field = fiscal.find(_q(NFE, "ultNSU"))
    max_field = fiscal.find(_q(NFE, "maxNSU"))
    last = _nsu(last_field.text.strip()) if last_field is not None and last_field.text else None
    maximum = _nsu(max_field.text.strip()) if max_field is not None and max_field.text else None
    batch = fiscal.find(_q(NFE, "loteDistDFeInt"))
    if cstat == "138":
        if last is None or maximum is None or int(last) < int(state.last_nsu) or int(last) > int(maximum):
            raise NFeDistributionError("Retorno 138 com intervalo NSU inconsistente")
        if batch is None:
            raise NFeDistributionError("Retorno 138 sem loteDistDFeInt")
        entries = list(batch)
        if not 1 <= len(entries) <= MAX_DOCUMENTS or any(n.tag != _q(NFE, "docZip") for n in entries):
            raise NFeDistributionError("Quantidade ou estrutura docZip inválida")
        docs = tuple(_document(entry) for entry in entries)
        if len({doc.nsu for doc in docs}) != len(docs) or any(int(doc.nsu) <= int(state.last_nsu) or int(doc.nsu) > int(last) for doc in docs):
            raise NFeDistributionError("NSU de documento duplicado ou fora do intervalo")
        if sum(len(doc.xml_bytes) for doc in docs) > MAX_BATCH_XML_BYTES:
            raise NFeDistributionError("Lote descompactado excede o limite")
        next_at = when + COOLDOWN if last == maximum else None
        next_state = PollState(state.cnpj, state.environment, last, next_at)
        return DistributionResult(cstat, reason, "found", raw, docs, next_state, maximum)
    if batch is not None and len(batch):
        raise NFeDistributionError("Resposta de rejeição ou vazia não pode conter documentos")
    if cstat == "137":
        if last is not None and int(last) < int(state.last_nsu):
            raise NFeDistributionError("Retorno 137 recuou o NSU")
        if last is not None and maximum is not None and int(last) > int(maximum):
            raise NFeDistributionError("Retorno 137 ultrapassou maxNSU")
        next_state = PollState(state.cnpj, state.environment, last or state.last_nsu, when + COOLDOWN)
        return DistributionResult(cstat, reason, "empty", raw, (), next_state, maximum)
    # Bloqueio 656 e demais rejeições nunca devem avançar o checkpoint.
    next_state = PollState(state.cnpj, state.environment, state.last_nsu, when + COOLDOWN)
    return DistributionResult(cstat, reason, "throttled" if cstat == "656" else "rejected", raw, (), next_state, maximum)


class NFeDistributionClient:
    """Transporte mTLS PEM; estado/certificado permanecem sob controle do cliente."""

    def __init__(self, cert_pem: str | Path, key_pem: str | Path, *, timeout: float = 30) -> None:
        self.cert_pem = Path(cert_pem)
        self.key_pem = Path(key_pem)
        if not self.cert_pem.is_absolute() or not self.key_pem.is_absolute():
            raise NFeDistributionError("Certificado e chave PEM devem ter caminhos absolutos")
        if not 1 <= timeout <= 120:
            raise NFeDistributionError("timeout deve estar entre 1 e 120 segundos")
        self.timeout = timeout

    def fetch(self, state: PollState, *, now: datetime | None = None) -> DistributionResult:
        when = now or datetime.now(timezone.utc)
        if when.utcoffset() is None:
            raise NFeDistributionError("now precisa incluir fuso horário")
        if state.next_poll_at and when < state.next_poll_at:
            raise NFeDistributionError("Consulta adiada por cStat anterior; respeite next_poll_at")
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=str(self.cert_pem), keyfile=str(self.key_pem))
        raw = _post_soap(ENDPOINTS[state.environment], build_envelope(state), context, self.timeout)
        try:
            return parse_response(raw, state, now=when)
        except NFeDistributionError as exc:
            raise NFeDistributionError(str(exc), raw_response=raw) from exc
