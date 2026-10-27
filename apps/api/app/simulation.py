"""Parametric reform scenarios; never official CBS/IBS calculation or credit booking.

All rates and tax bases come from the operator. No rate is inferred from a tax
year, tax regime, XML evidence or a government receipt. Results are illustrative.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
AMOUNT = re.compile(r"(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,2})?\Z")
RATE = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?\Z")
PERIOD = re.compile(r"20[0-9]{2}-(?:0[1-9]|1[0-2])\Z")


def _decimal(value: object, pattern: re.Pattern[str], label: str) -> Decimal:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label}: informe uma string decimal sem separador de milhares")
    return Decimal(value)


def amount(value: object) -> Decimal:
    return _decimal(value, AMOUNT, "Valor")


def rate(value: object) -> Decimal:
    result = _decimal(value, RATE, "Alíquota")
    if result > HUNDRED:
        raise ValueError("Alíquota não pode exceder 100%")
    return result


def money(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def tax(base: Decimal, percentage: Decimal) -> Decimal:
    return (base * percentage / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)


class SimulationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    period: str
    mode: Literal["regular", "simples"]
    revenue: Decimal
    purchases: Decimal
    cbs_sales_rate: Decimal
    ibs_sales_rate: Decimal
    cbs_purchase_rate: Decimal
    ibs_purchase_rate: Decimal
    das_effective_rate: Decimal | None = None
    das_cbs_component_rate: Decimal | None = None
    das_ibs_component_rate: Decimal | None = None

    @field_validator("period")
    @classmethod
    def check_period(cls, value: str) -> str:
        if not PERIOD.fullmatch(value):
            raise ValueError("Competência deve estar em AAAA-MM")
        return value

    @field_validator("revenue", "purchases", mode="before")
    @classmethod
    def check_amount(cls, value: object) -> Decimal:
        return amount(value)

    @field_validator("cbs_sales_rate", "ibs_sales_rate",
                     "cbs_purchase_rate", "ibs_purchase_rate",
                     "das_effective_rate", "das_cbs_component_rate",
                     "das_ibs_component_rate", mode="before")
    @classmethod
    def check_rate(cls, value: object) -> Decimal | None:
        return None if value is None else rate(value)

    @model_validator(mode="after")
    def check_mode(self) -> "SimulationIn":
        das_fields = (
            self.das_effective_rate,
            self.das_cbs_component_rate,
            self.das_ibs_component_rate,
        )
        if self.mode == "simples":
            if any(value is None for value in das_fields):
                raise ValueError(
                    "No Simples, informe alíquota efetiva do DAS e parcelas "
                    "CBS e IBS fornecidas pelo responsável tributário"
                )
            if self.das_cbs_component_rate + self.das_ibs_component_rate > self.das_effective_rate:
                raise ValueError("Parcelas CBS/IBS não podem superar a alíquota efetiva do DAS")
        elif any(value is not None for value in das_fields):
            raise ValueError("Campos de DAS são exclusivos da comparação com Simples Nacional")
        return self


def calculate_simulation(data: SimulationIn) -> dict:
    """Decimal-only scenario. A hypothetical purchase tax is NOT an appropriated credit."""
    cbs_debit = tax(data.revenue, data.cbs_sales_rate)
    ibs_debit = tax(data.revenue, data.ibs_sales_rate)
    cbs_potential = tax(data.purchases, data.cbs_purchase_rate)
    ibs_potential = tax(data.purchases, data.ibs_purchase_rate)
    illustrative_regular = cbs_debit + ibs_debit - cbs_potential - ibs_potential
    result: dict = {
        "company_id": str(data.company_id),
        "period": data.period,
        "mode": data.mode,
        "source": "operator_defined_hypothesis",
        "official_assessment": False,
        "official_coverage_proven": False,
        "regular": {
            "cbs_debit": money(cbs_debit),
            "ibs_debit": money(ibs_debit),
            "cbs_potential_purchase_tax": money(cbs_potential),
            "ibs_potential_purchase_tax": money(ibs_potential),
            "theoretical_net": money(illustrative_regular),
            "credit_to_appropriate": None,
            "credit_appropriated": None,
            "credit_used": None,
        },
        "simples": None,
        "warnings": [
            "Valores e alíquotas são hipóteses informadas pelo operador; "
            "não foram consultados ou validados com Receita/CGIBS.",
            "Tributos destacados nas compras não são créditos automaticamente "
            "apropriáveis. Crédito a apropriar, apropriado e utilizado dependem "
            "de fatos e validações distintos.",
            "Saldo teórico não é apuração, declaração, DARF nem valor a pagar. "
            "Desconsidera reduções, exceções, arredondamentos por item, "
            "diferimentos, créditos condicionados, devoluções e regimes específicos.",
        ],
    }
    if data.mode == "simples":
        das_total = tax(data.revenue, data.das_effective_rate)
        das_cbs = tax(data.revenue, data.das_cbs_component_rate)
        das_ibs = tax(data.revenue, data.das_ibs_component_rate)
        das_other = das_total - das_cbs - das_ibs
        alternative = das_other + illustrative_regular
        result["simples"] = {
            "das_total_estimate": money(das_total),
            "das_cbs_component_estimate": money(das_cbs),
            "das_ibs_component_estimate": money(das_ibs),
            "das_other_component_estimate": money(das_other),
            "regular_option_combined_hypothesis": money(alternative),
            "difference_regular_minus_simples": money(alternative - das_total),
        }
        result["warnings"].append(
            "Comparação Simples: as parcelas DAS são parâmetros manuais, "
            "não percentuais oficiais calculados pelo PGDAS-D. A alternativa "
            "regular não constitui orientação para opção tributária e não "
            "considera todas as regras da LC 123/2006 e LC 214/2025."
        )
    return result
