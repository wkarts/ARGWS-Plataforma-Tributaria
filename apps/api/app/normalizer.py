"""Lossless snapshots with a small, explicitly mapped fiscal projection.

The projection is descriptive. It never calculates an entitlement or moves a
commercial installment into a fiscal credit balance.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json


class InvalidReceipt(ValueError):
    pass


def decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidReceipt("Valor monetário booleano")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidReceipt("Valor monetário inválido") from None
    if not result.is_finite() or abs(result) >= Decimal("1e16"):
        raise InvalidReceipt("Valor monetário fora dos limites")
    return result


def normalize(resource: str, payload: dict, *, ticket: str, cnpj_base: str, digest: str) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("tiqueteSolicitacao") != ticket:
        raise InvalidReceipt("Tíquete divergente ou retorno inválido")
    if str(payload.get("ni", "")) != cnpj_base:
        raise InvalidReceipt("Identificação do contribuinte divergente")
    apuracoes = payload.get("apuracao")
    if not isinstance(apuracoes, list):
        raise InvalidReceipt("Lista de apurações ausente")
    if len(apuracoes) > 100_000:
        raise InvalidReceipt("Retorno excede limite de apurações")
    result = []
    field = {"debitos": "debitos", "creditos": "creditos", "pagamentos": "pagamentos",
             "recolhimentos": "pagamentos"}.get(resource)
    if field is None:
        raise InvalidReceipt("Recurso desconhecido")
    for group_index, group in enumerate(apuracoes):
        if not isinstance(group, dict):
            raise InvalidReceipt("Grupo de apuração inválido")
        if field not in group:
            raise InvalidReceipt("Lista de registros ausente na apuração")
        entries = group[field]
        if not isinstance(entries, list):
            raise InvalidReceipt("Lista de registros inválida")
        for item_index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise InvalidReceipt("Registro inválido")
            canonical = json.dumps(item, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":"), default=str)
            fingerprint = sha256(canonical.encode()).hexdigest()
            identity = str(item.get("chave") or item.get("numeroDARF") or "SEM-CHAVE")[:70]
            # Multiple debits may share a DFe; blank DARF values are common.
            # A snapshot identity includes origin/type, content and position.
            key = f"{identity}:{item.get('origem', item.get('tipo', ''))}:{fingerprint[:16]}:{group_index}:{item_index}"
            period = str(group.get("pa") or "")
            cbs = item.get("cbs") or {}
            if not isinstance(cbs, dict):
                raise InvalidReceipt("Grupo CBS inválido")
            appropriation = cbs.get("apropriacao") or {}
            if not isinstance(appropriation, dict):
                raise InvalidReceipt("Grupo de apropriação inválido")
            utilization = appropriation.get("utilizacao") or {}
            if not isinstance(utilization, dict):
                raise InvalidReceipt("Grupo de utilização inválido")
            common = {
                "resource": resource,
                "credit_to_appropriate": decimal_or_none(appropriation.get("aApropriar")) if resource == "creditos" else None,
                "credit_appropriated": decimal_or_none(appropriation.get("apropriado")) if resource == "creditos" else None,
                "credit_used": decimal_or_none(utilization.get("utilizado")) if resource == "creditos" else None,
                "credit_category": "government_reported" if resource == "creditos" else None,
                "provenance": {"ticket": ticket, "receipt_sha256": digest,
                    "generated_at": payload.get("geradoEm"), "source": "cbs",
                    "collection_at": group.get("dataArrecadacao"),
                    "group_index": group_index, "item_index": item_index},
            }
            if resource in {"pagamentos", "recolhimentos"}:
                components = item.get("composicao")
                if not isinstance(components, list) or not components:
                    raise InvalidReceipt("Composição de pagamento ausente")
                for component_index, component in enumerate(components):
                    if not isinstance(component, dict):
                        raise InvalidReceipt("Composição de pagamento inválida")
                    component_json = json.dumps(component, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":"), default=str)
                    row = common | {
                        "period": str(component.get("pa") or "")[:12],
                        "source_key": (key[:112] + f":{component_index}:" +
                                       sha256(component_json.encode()).hexdigest()[:16]),
                        "amount": decimal_or_none(component.get("total")),
                        "principal": decimal_or_none(component.get("principal")),
                        "penalty": decimal_or_none(component.get("multa")),
                        "interest": decimal_or_none(component.get("juros")),
                        "provenance": common["provenance"] | {"composition_index": component_index,
                            "darf": item.get("numeroDARF"), "payment_type": item.get("tipo"),
                            "document_key": component.get("chaveDFE")},
                        "raw": {"payment": json.loads(canonical),
                                "composition": json.loads(component_json)},
                    }
                    result.append(row)
            else:
                result.append(common | {
                    "period": period[:12], "source_key": key[:160],
                    "amount": decimal_or_none(cbs.get("apurado")),
                    "principal": None, "penalty": None, "interest": None,
                    "raw": json.loads(canonical),
                })
            if len(result) > 200_000:
                raise InvalidReceipt("Retorno excede limite de registros")
    return result
