from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


def valid_cnpj(value: str) -> bool:
    """Módulo 11 da Receita: ASCII - 48 for the first 12 characters."""
    if len(set(value)) == 1:
        return False
    weights_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    weights_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

    def digit(chars: str, weights: tuple[int, ...]) -> int:
        remainder = sum((ord(char) - 48) * weight
                        for char, weight in zip(chars, weights, strict=True)) % 11
        return 0 if remainder < 2 else 11 - remainder

    first = digit(value[:12], weights_1)
    return value[12:] == f"{first}{digit(value[:12] + str(first), weights_2)}"


class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=1024)


class CompanyIn(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    cnpj: str

    @field_validator("cnpj")
    @classmethod
    def normalize_cnpj(cls, value: str) -> str:
        result = re.sub(r"[.\-/\s]", "", value.upper())
        if not re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", result):
            raise ValueError("CNPJ precisa conter 12 caracteres de base/ordem e dois dígitos verificadores")
        if not valid_cnpj(result):
            raise ValueError("Dígitos verificadores do CNPJ inválidos")
        return result


class QueryIn(BaseModel):
    company_id: str
    resource: str
    environment: str

    @field_validator("resource")
    @classmethod
    def resource_name(cls, value: str) -> str:
        if value not in {"debitos", "creditos", "pagamentos", "recolhimentos"}:
            raise ValueError("Recurso CBS inválido")
        return value

    @field_validator("environment")
    @classmethod
    def environment_name(cls, value: str) -> str:
        if value not in {"fixture", "restricted", "production"}:
            raise ValueError("Ambiente inválido")
        return value


class ResolveUncertainIn(BaseModel):
    action: str
    ticket: str | None = Field(default=None, max_length=200)
    note: str = Field(min_length=10, max_length=500)

    @field_validator("action")
    @classmethod
    def action_name(cls, value: str) -> str:
        if value not in {"attach_ticket", "mark_failed"}:
            raise ValueError("Ação inválida")
        return value
