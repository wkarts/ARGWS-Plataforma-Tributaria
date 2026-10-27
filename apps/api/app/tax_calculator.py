"""Execute actual item-level taxation using a configured local tax calculator.

No statutory rates, CST classifications or credits are guessed by this layer.
Local engine calls are distinct from government assessment/reconciliation.
"""
from __future__ import annotations

from hashlib import sha256
import ipaddress
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings

MAX_REQUEST_BYTES = 256_000
MAX_RESPONSE_BYTES = 3_000_000
_ALLOWED_NAMES = {"localhost", "host.docker.internal", "calculator-regular",
                  "calculator-simples", "tax-calculator"}


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def validate_local_url(raw: str) -> str:
    """Trust only a fixed local service from server configuration, never user URLs."""
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        local_address = False
        try:
            ip = ipaddress.ip_address(host)
            local_address = ip.is_loopback
        except ValueError:
            pass
        if (parsed.scheme != "http" or not host
                or not (local_address or host in _ALLOWED_NAMES)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.port or not parsed.path.startswith("/api/")):
            raise ValueError("O motor deve usar HTTP local, porta explícita e rota /api/")
    except ValueError as exc:
        raise HTTPException(503, "Configuração da calculadora local inválida") from exc
    return raw


class TaxCalculationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    regime: Literal["regular", "simples"]
    payload: dict[str, Any] = Field(min_length=1)

    @field_validator("payload")
    @classmethod
    def valid_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            size = len(canonical(value))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Requisição contém valores JSON inválidos") from exc
        if size > MAX_REQUEST_BYTES:
            raise ValueError("Requisição excede 256 kB")
        return value

    @model_validator(mode="after")
    def validate_regular_operation(self) -> "TaxCalculationIn":
        if self.regime == "regular":
            value = self.payload
            if (not isinstance(value.get("dataHoraEmissao"), str)
                or not isinstance(value.get("uf"), str)
                or not isinstance(value.get("municipio"), int)
                or isinstance(value.get("municipio"), bool)
                or not isinstance(value.get("itens"), list)
                or not 1 <= len(value["itens"]) <= 500):
                raise ValueError("Informe dataHoraEmissao, uf, municipio IBGE e itens")
            if len(value["uf"]) != 2 or not value["uf"].isalpha():
                raise ValueError("UF inválida")
            numbers: set[int] = set()
            for item in value["itens"]:
                if not isinstance(item, dict):
                    raise ValueError("Cada item deve ser um objeto JSON")
                number = item.get("numero")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in numbers:
                    raise ValueError("Número do item inválido ou repetido")
                numbers.add(number)
                if not all(isinstance(item.get(name), str) and item[name].strip()
                           for name in ("cst", "cClassTrib", "unidade")):
                    raise ValueError("Cada item requer CST, cClassTrib e unidade")
                if not item.get("ncm") and not item.get("nbs"):
                    raise ValueError("Cada item requer NCM ou NBS")
                for name in ("baseCalculo", "quantidade"):
                    raw = item.get(name)
                    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
                        raise ValueError(f"{name} inválido")
                    try:
                        number_decimal = Decimal(str(raw))
                        if not number_decimal.is_finite() or number_decimal < 0:
                            raise ValueError(f"{name} deve ser não negativo e finito")
                    except InvalidOperation as exc:
                        raise ValueError(f"{name} inválido") from exc
        return self


def calculator_urls(regime: str) -> tuple[str, str]:
    settings = get_settings()
    service = (settings.tax_calculator_regular_url if regime == "regular"
               else settings.tax_calculator_simples_url)
    version = (settings.tax_calculator_regular_version_url if regime == "regular"
               else settings.tax_calculator_simples_version_url)
    if not service or not version:
        raise HTTPException(503, "Calculadora local não configurada para este regime")
    service_url = validate_local_url(service)
    version_url = validate_local_url(version)
    left, right = urlsplit(service_url), urlsplit(version_url)
    if (left.hostname, left.port) != (right.hostname, right.port):
        raise HTTPException(503, "Versão e cálculo devem usar o mesmo serviço local")
    return service_url, version_url


def _engine_version(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(502, "Versão do motor retornada em formato inválido")
    if (not isinstance(payload.get("versaoApp"), str)
            or not isinstance(payload.get("versaoDb"), str)
            or not payload["versaoApp"] or not payload["versaoDb"]):
        raise HTTPException(502, "Motor local não retornou versões válidas da aplicação e da base")
    return payload


def _regular_totals(payload: Any) -> dict[str, str]:
    """Only pass through amounts calculated by the official engine."""
    try:
        totals = payload["total"]["tribCalc"]["IBSCBSTot"]
        result = {
            "ibs": totals["gIBS"]["vIBS"],
            "cbs": totals["gCBS"]["vCBS"],
        }
        for value in result.values():
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError("Quantidade inválida")
            d = Decimal(str(value))
            if not d.is_finite() or d < 0:
                raise ValueError("Quantidade não finita")
        return {key: str(value) for key, value in result.items()}
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise HTTPException(502, "Calculadora local não retornou totais CBS/IBS reconhecidos") from exc


async def execute_calculator(regime: str, payload: dict[str, Any]) -> tuple[dict, dict, dict]:
    """No silent fallback. Fail if engine, tax table or version cannot be checked."""
    service_url, version_url = calculator_urls(regime)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(15.0, connect=3.0)) as client:
        try:
            version = await client.get(version_url, headers={"Accept": "application/json"})
            version.raise_for_status()
            if len(version.content) > MAX_RESPONSE_BYTES:
                raise HTTPException(502, "Resposta de versão excedeu o limite")
            version_data = _engine_version(version.json())
            result = await client.post(service_url, json=payload,
                                       headers={"Accept": "application/json"})
            if result.status_code in (400, 422):
                raise HTTPException(422, "A calculadora local rejeitou os dados fiscais")
            result.raise_for_status()
            if len(result.content) > MAX_RESPONSE_BYTES:
                raise HTTPException(502, "Retorno fiscal excedeu o limite")
            calculated = result.json()
            if not isinstance(calculated, dict) or not calculated:
                raise HTTPException(502, "Calculadora local retornou resposta vazia")
            totals = _regular_totals(calculated) if regime == "regular" else {}
            return calculated, version_data, totals
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "Motor tributário local indisponível ou retorno inválido") from exc
