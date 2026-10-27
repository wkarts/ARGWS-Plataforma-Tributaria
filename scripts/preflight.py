#!/usr/bin/env python3
"""Valida configurações sem imprimir seus valores secretos nem executar dotenv como shell."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from version import SEMVER

REQUIRED = (
    "SECRET_KEY", "BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_PASSWORD",
    "POSTGRES_PASSWORD", "REDIS_PASSWORD", "RABBITMQ_DEFAULT_PASS",
    "S3_ACCESS_KEY", "S3_SECRET_KEY",
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f".env linha {number} sem '='")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError(f".env linha {number} com chave inválida ou duplicada")
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def validate(values: dict[str, str], channel: str) -> None:
    absent = [name for name in REQUIRED if not values.get(name)]
    if absent:
        raise ValueError("Configure as variáveis obrigatórias: " + ", ".join(absent))
    if len(values["SECRET_KEY"]) < 32:
        raise ValueError("SECRET_KEY deve ter ao menos 32 caracteres")
    if len(values["BOOTSTRAP_ADMIN_PASSWORD"]) < 16:
        raise ValueError("BOOTSTRAP_ADMIN_PASSWORD deve ter ao menos 16 caracteres")
    if len(values["S3_SECRET_KEY"]) < 16:
        raise ValueError("S3_SECRET_KEY deve ter ao menos 16 caracteres")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["BOOTSTRAP_ADMIN_EMAIL"]):
        raise ValueError("BOOTSTRAP_ADMIN_EMAIL inválido")
    if values.get("S3_USE_SSL", "false").lower() not in {"true", "false"}:
        raise ValueError("S3_USE_SSL deve ser true ou false")
    if not values.get("S3_ENDPOINT", "").strip():
        raise ValueError("S3_ENDPOINT é obrigatório")
    mode = values.get("PROVIDER_MODE", "disabled")
    if mode not in {"fixture", "receita", "disabled"}:
        raise ValueError("PROVIDER_MODE inválido")
    if channel == "production" and mode == "fixture":
        raise ValueError("Produção não permite dados simulados (PROVIDER_MODE=fixture)")
    if values.get("PROVIDER_MODE") == "receita":
        required = ("CBS_ALLOWED_BASES", "CBS_CREDENTIAL_ORGANIZATION_ID", "CBS_TOKEN_URL", "CBS_CLIENT_ID", "CBS_CLIENT_SECRET", "CBS_WEBHOOK_BASE_URL")
        missing = [key for key in required if not values.get(key)]
        if missing:
            raise ValueError("Integração Receita incompleta: " + ", ".join(missing))
        if not values["CBS_TOKEN_URL"].startswith("https://") or not values["CBS_WEBHOOK_BASE_URL"].startswith("https://"):
            raise ValueError("URLs de OAuth e webhook devem usar HTTPS")
    tag = values.get("APP_TAG", "")
    if channel == "production" and not (
        SEMVER.fullmatch(tag.removeprefix("v")) or re.fullmatch(r"sha-[0-9a-f]{12}", tag)
    ):
        raise ValueError("Produção exige APP_TAG=vX.Y.Z, X.Y.Z ou sha-<12 hex>")
    if channel == "develop" and tag != "develop":
        raise ValueError("Develop exige APP_TAG=develop")
    if values.get("WEB_BIND_ADDRESS", "127.0.0.1") not in {"127.0.0.1", "::1"}:
        raise ValueError("WEB_BIND_ADDRESS deve ser loopback; configure proxy reverso para acesso externo")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--channel", choices=("develop", "production"), required=True)
    args = parser.parse_args()
    validate(parse_env(args.env_file), args.channel)
    print("OK: configuração validada sem expor segredos")
