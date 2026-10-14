"""Bounded extraction of claims from fiscal XML; no schema or signature verification.

The raw bytes remain authoritative evidence. Values returned here are claims made
by the uploaded file and must be checked against official sources before use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re
from xml.etree import ElementTree as ET
from xml.parsers import expat


XMLDSIG = "http://www.w3.org/2000/09/xmldsig#"
XINCLUDE = "http://www.w3.org/2001/XInclude"
MAX_ELEMENTS = 100_000
MAX_DEPTH = 128


@dataclass(frozen=True)
class FiscalXmlMetadata:
    kind: str
    document_key: str | None
    model: str | None
    issuer_cnpj: str | None
    recipient_cnpj: str | None
    issue_at: str | None
    period: str | None
    direction: str
    total_amount: Decimal | None
    cbs_amount: Decimal | None
    ibs_amount: Decimal | None
    signature_present: bool
    authorization_status: str


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    return next((item for item in node if local_name(item.tag) == name), None)


def descendant(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    return next((item for item in node.iter() if local_name(item.tag) == name), None)


def value(node: ET.Element | None, name: str) -> str | None:
    item = child(node, name)
    return item.text.strip() if item is not None and item.text else None


def amount(node: ET.Element | None, name: str) -> Decimal | None:
    raw = value(node, name)
    if raw is None or not re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,2})?", raw):
        return None
    return Decimal(raw)


def _parse_bounded(data: bytes) -> ET.Element:
    """Reject entity declarations, DTDs and pathological tree sizes before ET."""
    parser = expat.ParserCreate()
    depth = 0
    count = 0

    def reject(*_args: object) -> None:
        raise ValueError("DTD, entidades e referências externas não são aceitas")

    def start(_name: str, _attrs: dict[str, str]) -> None:
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > MAX_DEPTH or count > MAX_ELEMENTS:
            raise ValueError("XML excede limite de profundidade ou elementos")

    def end(_name: str) -> None:
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
        root = ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ValueError("XML inválido") from exc
    if any(item.tag in {f"{{{XINCLUDE}}}include", f"{{{XINCLUDE}}}fallback"}
           for item in root.iter()):
        raise ValueError("XInclude não é aceito")
    return root


def _key(node: ET.Element | None, prefix: str) -> str | None:
    if node is None:
        return None
    identifier = node.attrib.get("Id", "")
    if re.fullmatch(re.escape(prefix) + r"[0-9]{44}", identifier):
        return identifier[len(prefix):]
    return None


def _period(issue_at: str | None) -> str | None:
    if not issue_at or not re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", issue_at):
        return None
    try:
        date.fromisoformat(issue_at[:10])
    except ValueError:
        return None
    return issue_at[:7]


def parse_fiscal_xml(data: bytes, company_cnpj: str) -> FiscalXmlMetadata:
    """Extract metadata and require the assigned CNPJ to occur in the XML.

    A CNPJ occurrence is a minimal routing check, not a proof of ownership.
    Neither an XMLDSig element nor an authorization protocol is verified here.
    """
    root = _parse_bounded(data)
    root_name = local_name(root.tag)
    if root_name in {"NFe", "nfeProc", "enviNFe", "procEventoNFe", "evento"}:
        kind, info_name, prefix, protocol_name, key_name = "nfe", "infNFe", "NFe", "protNFe", "chNFe"
    elif root_name in {"CTe", "cteProc", "enviCTe", "procEventoCTe"}:
        kind, info_name, prefix, protocol_name, key_name = "cte", "infCte", "CTe", "protCTe", "chCTe"
    elif root_name in {"MDFe", "mdfeProc", "enviMDFe", "procEventoMDFe"}:
        kind, info_name, prefix, protocol_name, key_name = "mdfe", "infMDFe", "MDFe", "protMDFe", "chMDFe"
    else:
        raise ValueError("Formato de DFe não suportado para ingestão")

    company_cnpj = company_cnpj.upper()
    observed_cnpjs = {re.sub(r"[^A-Z0-9]", "", (item.text or "").upper())
                      for item in root.iter() if local_name(item.tag) in {"CNPJ", "Cnpj"}}
    if company_cnpj not in observed_cnpjs:
        raise ValueError("CNPJ da empresa não encontrado no XML")

    info = descendant(root, info_name) if info_name else None
    if info is None and root_name not in {"evento", "procEventoNFe", "procEventoCTe", "procEventoMDFe"}:
        raise ValueError("DF-e sem grupo de informações esperado")
    if root_name in {"evento", "procEventoNFe", "procEventoCTe", "procEventoMDFe"}:
        event = descendant(root, "infEvento")
        if event is None or descendant(event, key_name) is None:
            raise ValueError("Evento fiscal sem grupo e chave do documento")
    ide = child(info, "ide")
    issuer = value(child(info, "emit"), "CNPJ") or value(child(info, "emit"), "Cnpj")
    recipient = value(child(info, "dest"), "CNPJ") or value(child(info, "dest"), "Cnpj")
    issuer = re.sub(r"[^A-Z0-9]", "", issuer.upper()) if issuer else None
    recipient = re.sub(r"[^A-Z0-9]", "", recipient.upper()) if recipient else None
    if issuer == company_cnpj and recipient == company_cnpj:
        direction = "internal"
    elif issuer == company_cnpj:
        direction = "outgoing"
    elif recipient == company_cnpj:
        direction = "incoming"
    else:
        direction = "unknown"

    # For NF-e the issuer/recipient is the only supported ownership relation.
    # The other document families have additional participant roles.
    if kind == "nfe" and info is not None and direction == "unknown":
        raise ValueError("Empresa não é emitente nem destinatária da NF-e")

    model = value(ide, "mod")
    if kind == "nfe" and model == "65":
        kind = "nfce"
    document_key = _key(info, prefix) if prefix else None
    if document_key is None and key_name:
        candidate = descendant(root, key_name)
        if candidate is not None and candidate.text and re.fullmatch(r"[0-9]{44}", candidate.text.strip()):
            document_key = candidate.text.strip()

    issue_at = value(ide, "dhEmi") or value(ide, "dEmi")
    if issue_at and len(issue_at) > 40:
        issue_at = None
    total = child(info, "total")
    total_amount = amount(child(total, "ICMSTot"), "vNF")
    if total_amount is None:
        total_amount = amount(child(info, "vPrest"), "vTPrest")
    tax_total = child(total, "IBSCBSTot")
    cbs_group = child(tax_total, "gCBS")
    ibs_group = child(tax_total, "gIBS")
    cbs_amount = amount(cbs_group, "vCBS")
    ibs_amount = amount(ibs_group, "vIBS")
    signature_present = any(item.tag == f"{{{XMLDSIG}}}Signature" for item in root.iter())

    protocol = descendant(root, protocol_name) if protocol_name else None
    inf_protocol = child(protocol, "infProt")
    protocol_status = value(inf_protocol, "cStat")
    protocol_key = value(inf_protocol, key_name) if key_name else None
    if protocol_status is None:
        authorization_status = "not_provided"
    elif document_key and protocol_key != document_key:
        authorization_status = "protocol_mismatch"
    elif protocol_status in {"100", "150"} and document_key and protocol_key == document_key:
        authorization_status = "reported_authorized"
    else:
        authorization_status = "reported_other"

    return FiscalXmlMetadata(kind, document_key, model, issuer, recipient, issue_at, _period(issue_at),
                             direction, total_amount, cbs_amount, ibs_amount,
                             signature_present, authorization_status)
