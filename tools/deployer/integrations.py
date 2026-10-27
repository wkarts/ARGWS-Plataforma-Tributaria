"""Validated Receita CBS and SMTP configuration for the offline Windows deployer.

Credentials are received through stdin JSON only; never command-line arguments.
Values are preserved in private .env, not echoed in results or diagnostics.
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlsplit

RECEITA_KEYS = frozenset({
    "PROVIDER_MODE", "CBS_TOKEN_URL", "CBS_CLIENT_ID", "CBS_CLIENT_SECRET",
    "CBS_WEBHOOK_BASE_URL", "CBS_CREDENTIAL_ORGANIZATION_ID", "CBS_ALLOWED_BASES",
    "CBS_RESTRICTED_ENABLED", "CBS_PRODUCTION_ENABLED", "CBS_PAYMENTS_ENABLED",
    "CBS_AUTO_SYNC_ENABLED", "CBS_AUTO_SYNC_ENVIRONMENT", "CBS_AUTO_SYNC_RESOURCES",
})
SMTP_KEYS = frozenset({
    "SMTP_ENABLED", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
    "SMTP_FROM", "SMTP_REPLY_TO", "SMTP_STARTTLS", "SMTP_SSL",
    "APP_PUBLIC_URL", "CNPJ_LOOKUP_ENABLED",
})
ALLOWED_KEYS = RECEITA_KEYS | SMTP_KEYS
SECRET_KEYS = frozenset({"CBS_CLIENT_SECRET", "SMTP_PASSWORD"})
BOOLEAN_KEYS = frozenset({
    "CBS_RESTRICTED_ENABLED", "CBS_PRODUCTION_ENABLED", "CBS_PAYMENTS_ENABLED",
    "CBS_AUTO_SYNC_ENABLED", "SMTP_ENABLED", "SMTP_STARTTLS", "SMTP_SSL",
    "CNPJ_LOOKUP_ENABLED",
})


class ConfigurationError(ValueError):
    pass


def dotenv_literal(value: str) -> str:
    if any(ord(ch) < 32 for ch in value):
        raise ConfigurationError("Caractere de controle não permitido em .env")
    if not re.search(r"[\s#$'\"\\]", value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def dotenv_unquote(value: str) -> str:
    if len(value) > 1 and value[0] == value[-1] == "'":
        return value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    if len(value) > 1 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def read_config_stdin() -> dict:
    data = sys.stdin.read(65537)
    if len(data) > 65536:
        raise ConfigurationError("Configuração maior que 64 KiB")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise ConfigurationError("Payload JSON das integrações inválido") from None
    if not isinstance(value, dict):
        raise ConfigurationError("Configuração deve ser objeto JSON")
    return value


def normalize_updates(updates: dict, channel: str,
                      current: dict[str, str] | None = None) -> dict[str, str]:
    if not isinstance(updates, dict) or len(updates) > len(ALLOWED_KEYS):
        raise ConfigurationError("Estrutura inválida de configuração")
    unknown = set(updates) - ALLOWED_KEYS
    if unknown:
        raise ConfigurationError("Parâmetro desconhecido: " + ", ".join(sorted(unknown)))
    normalized: dict[str, str] = {}
    for name, raw in updates.items():
        if raw is None:
            continue
        if isinstance(raw, bool):
            value = str(raw).lower()
        elif isinstance(raw, int) and name == "SMTP_PORT":
            value = str(raw)
        elif isinstance(raw, str):
            value = raw if name in SECRET_KEYS else raw.strip()
        else:
            raise ConfigurationError(name + " não é valor de ambiente válido")
        if len(value) > 4096 or any(ord(ch) < 32 for ch in value):
            raise ConfigurationError(name + " contém valor inválido")
        if name in SECRET_KEYS and not value and (current or {}).get(name):
            continue  # Empty GUI secret field means keep the existing secret.
        if name in BOOLEAN_KEYS and value not in {"true", "false"}:
            raise ConfigurationError(name + " deve ser true ou false")
        if name == "SMTP_PORT" and (not value.isdecimal() or not 1 <= int(value) <= 65535):
            raise ConfigurationError("SMTP_PORT fora do intervalo permitido")
        if name == "PROVIDER_MODE":
            if value not in {"disabled", "receita", "fixture"}:
                raise ConfigurationError("PROVIDER_MODE inválido")
            if channel == "production" and value == "fixture":
                raise ConfigurationError("Produção não pode executar fixture")
        normalized[name] = value
    return normalized


def status(values: dict[str, str], channel: str) -> dict:
    mode = values.get("PROVIDER_MODE", "disabled")
    if mode not in {"disabled", "receita", "fixture"} or channel == "production" and mode == "fixture":
        raise ConfigurationError("PROVIDER_MODE inválido para o canal")
    receita = {"mode": mode, "ready": False, "missing": []}
    if mode == "receita":
        for name in ("CBS_TOKEN_URL", "CBS_CLIENT_ID", "CBS_CLIENT_SECRET",
                     "CBS_WEBHOOK_BASE_URL", "CBS_CREDENTIAL_ORGANIZATION_ID",
                     "CBS_ALLOWED_BASES"):
            if not values.get(name):
                receita["missing"].append(name)
        if (values.get("CBS_RESTRICTED_ENABLED") != "true" and
                values.get("CBS_PRODUCTION_ENABLED") != "true"):
            receita["missing"].append("CBS_RESTRICTED_ENABLED / CBS_PRODUCTION_ENABLED")
        receita["ready"] = not receita["missing"]
    smtp = {"enabled": values.get("SMTP_ENABLED", "false") == "true",
            "ready": False, "missing": []}
    if smtp["enabled"]:
        for name in ("SMTP_HOST", "SMTP_FROM", "APP_PUBLIC_URL"):
            if not values.get(name):
                smtp["missing"].append(name)
        if values.get("SMTP_USERNAME") and not values.get("SMTP_PASSWORD"):
            smtp["missing"].append("SMTP_PASSWORD")
        smtp["ready"] = not smtp["missing"]
    return {"receita": receita, "smtp": smtp}


def validate(values: dict[str, str], channel: str) -> dict:
    state = status(values, channel)
    for name in ("CBS_TOKEN_URL", "CBS_WEBHOOK_BASE_URL", "APP_PUBLIC_URL"):
        value = values.get(name, "")
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                raise ValueError("invalid")
            if name == "CBS_TOKEN_URL" and parsed.hostname != "api.receitafederal.gov.br":
                raise ValueError("wrong-host")
            if name == "CBS_WEBHOOK_BASE_URL" and (
                    parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                    or parsed.query or parsed.port not in {None, 443}):
                raise ValueError("not-public")
            if name == "APP_PUBLIC_URL" and (parsed.path not in {"", "/"} or parsed.query):
                raise ValueError("not-origin")
        except ValueError:
            raise ConfigurationError(name + " deve ser URL HTTPS permitida") from None
    host = values.get("SMTP_HOST", "")
    if host and re.search(r"[\s/:@]", host):
        raise ConfigurationError("SMTP_HOST deve conter nome DNS/IP sem esquema")
    if values.get("SMTP_STARTTLS", "true") == "true" and values.get("SMTP_SSL", "false") == "true":
        raise ConfigurationError("SMTP SSL e STARTTLS não podem ser usados simultaneamente")
    if state["smtp"]["enabled"]:
        if not state["smtp"]["ready"]:
            raise ConfigurationError("SMTP incompleto: " + ", ".join(state["smtp"]["missing"]))
        if bool(values.get("SMTP_USERNAME")) != bool(values.get("SMTP_PASSWORD")):
            raise ConfigurationError("SMTP_USERNAME e SMTP_PASSWORD devem ser informados juntos")
    return state


def append_or_replace_env(source: str, updates: dict[str, str]) -> str:
    output = []
    found = set()
    for line in source.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                output.append(key + "=" + dotenv_literal(updates[key]))
                found.add(key)
                continue
        output.append(line)
    missing = set(updates) - found
    if missing:
        output.extend(("", "# Opções de integração adicionadas pelo deployer."))
        output.extend(name + "=" + dotenv_literal(updates[name]) for name in sorted(missing))
    return "\n".join(output).rstrip() + "\n"
