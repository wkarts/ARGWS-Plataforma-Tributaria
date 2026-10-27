#!/usr/bin/env python3
"""Valida contratos estáticos de deploy e, com --docker, parse do Docker Compose."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from generate_deploys import CHANNELS, DEPLOY, ROOT, TARGETS, run as check_generated
from version import read_version


def redis_startup_contract(compose: str) -> list[str]:
    """Prevent a Compose string command from being split into shell arguments."""
    errors: list[str] = []
    segment = re.search(r"(?ms)^  redis:\\n(.*?)(?=^  [a-z][a-z0-9_-]*:\\n|\\Z)", compose)
    if segment is None:
        return ["Serviço Redis ausente no Compose"]
    redis = segment.group(1)
    if '    entrypoint: ["/bin/sh", "-ec"]' not in redis:
        errors.append("Redis deve executar script com entrypoint /bin/sh -ec")
    if "    command:\\n      - |\\n" not in redis:
        errors.append("Redis command deve ser uma lista com script multilinha único, não uma string")
    for clause in (
        "        umask 077\\n",
        "        printf 'requirepass %s\\\\n' \"$REDIS_PASSWORD\" > /tmp/redis.conf\\n",
        "        exec redis-server /tmp/redis.conf --appendonly yes\\n",
    ):
        if clause not in redis:
            errors.append("Redis sem instrução obrigatória: " + clause.strip().split(" ", 1)[0])
    return errors


def static_checks() -> list[str]:
    errors: list[str] = []
    version = read_version()
    compose = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    errors.extend(redis_startup_contract(compose))
    for service in ("migrate", "bootstrap", "api", "worker", "scheduler", "web", "postgres", "redis", "rabbitmq", "objectstore"):
        if f"  {service}:\n" not in compose:
            errors.append(f"Serviço ausente: {service}")
    for required in ("service_completed_successfully", "service_healthy", "no-new-privileges:true", "${APP_TAG:?", "volumes:", "WEB_BIND_ADDRESS"):
        if required not in compose:
            errors.append(f"Contrato de Compose ausente: {required}")
    for prohibited in ("/var/run/docker.sock", "privileged: true", "down -v", "network_mode: host", "container_name:", "--forwarded-allow-ips\", \"*"):
        if prohibited in compose:
            errors.append(f"Configuração de risco: {prohibited}")
    for target in (None, *TARGETS):
        for channel in CHANNELS:
            directory = DEPLOY / target / channel if target else DEPLOY / channel
            env = (directory / ".env.example").read_text(encoding="utf-8")
            if channel == "production" and "APP_TAG=CHANGE_ME_TO_RELEASE_TAG" not in env:
                errors.append(f"Produção sem tag explícita: {directory}")
            if channel == "develop" and "APP_TAG=develop" not in env:
                errors.append(f"Develop sem tag: {directory}")
            for key in ("SECRET_KEY", "BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_PASSWORD", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "RABBITMQ_DEFAULT_PASS", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
                if not re.search(rf"^{key}=$", env, re.MULTILINE):
                    errors.append(f"Segredo com default ou ausente: {directory}/{key}")
            if not env.count("COMPOSE_PROJECT_NAME=") == 1:
                errors.append(f"Projeto Compose inválido: {directory}")
    calculator_keys = (
        "TAX_CALCULATOR_REGULAR_URL", "TAX_CALCULATOR_REGULAR_VERSION_URL",
        "TAX_CALCULATOR_SIMPLES_URL", "TAX_CALCULATOR_SIMPLES_VERSION_URL",
    )
    for key in calculator_keys:
        if f"  {key}: ${{{key}:-}}" not in compose:
            errors.append(f"Compose sem repasse de {key}")
        for channel in CHANNELS:
            env_model = (DEPLOY / f"env.{channel}.example").read_text(encoding="utf-8")
            if not re.search(rf"^{key}=$", env_model, re.MULTILINE):
                errors.append(f"Modelo {channel} não declara {key}")
    if 'extra_hosts: ["host.docker.internal:host-gateway"]' not in compose:
        errors.append("API sem mapeamento de host para motor tributário local")
    self_compose = (DEPLOY / "self" / "compose.yaml").read_text(encoding="utf-8")
    if 'extra_hosts: ["host.docker.internal:host-gateway"]' not in self_compose:
        errors.append("Instalação self sem mapeamento de host para motor local")
    manifest = (ROOT / ".github" / "base-images.json")
    if not manifest.is_file():
        errors.append("Manifesto de imagens-base ausente")
    if not version:
        errors.append("VERSION ausente")
    return errors


def docker_checks() -> list[str]:
    errors: list[str] = []
    for target in (None, *TARGETS):
        for channel in CHANNELS:
            directory = DEPLOY / target / channel if target else DEPLOY / channel
            content = (directory / ".env.example").read_text(encoding="utf-8")
            content = content.replace("APP_TAG=CHANGE_ME_TO_RELEASE_TAG", f"APP_TAG=v{read_version()}")
            for key in ("SECRET_KEY", "BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_PASSWORD", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "RABBITMQ_DEFAULT_PASS", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
                content = re.sub(rf"^{key}=$", f"{key}=testvalue-for-compose-only", content, flags=re.MULTILINE)
            with tempfile.TemporaryDirectory() as tmp:
                env = Path(tmp) / "test.env"
                env.write_text(content, encoding="utf-8")
                result = subprocess.run(
                    ["docker", "compose", "--project-directory", str(directory), "--env-file", str(env), "-f", str(directory / "compose.yaml"), "config", "--format", "json"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
                )
                if result.returncode:
                    errors.append(f"{directory.relative_to(ROOT)}: {result.stderr.strip()[:300]}")
                else:
                    try:
                        resolved = json.loads(result.stdout)
                        redis_service = resolved["services"]["redis"]
                        command = redis_service["command"]
                        if (redis_service.get("entrypoint") != ["/bin/sh", "-ec"]
                                or not isinstance(command, list)
                                or len(command) != 1
                                or "umask 077" not in command[0]
                                or "exec redis-server" not in command[0]
                                or "$REDIS_PASSWORD" not in command[0]):
                            errors.append(
                                f"{directory.relative_to(ROOT)}: Redis command foi fragmentado no Docker Compose"
                            )
                    except (ValueError, TypeError, KeyError, IndexError) as exc:
                        errors.append(
                            f"{directory.relative_to(ROOT)}: não foi possível verificar o comando Redis ({type(exc).__name__})"
                        )
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", action="store_true")
    args = parser.parse_args()
    errors = []
    if check_generated(True):
        errors.append("Templates divergentes")
    errors.extend(static_checks())
    if args.docker:
        errors.extend(docker_checks())
    if errors:
        print("\n".join("ERRO: " + e for e in errors), file=sys.stderr)
        raise SystemExit(1)
    print("OK: contratos de infra e variantes de deploy")
