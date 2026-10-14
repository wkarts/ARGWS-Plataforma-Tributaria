#!/usr/bin/env python3
"""Serviço local multiperfil, com um estado SQLite independente por empresa."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import collector
import profiles


LOGGER = logging.getLogger("fiscal_xml_worker")


def selected(home: Path, company_id: str | None = None) -> list[dict[str, Any]]:
    items = profiles.list_profiles(home)
    if company_id:
        items = [item for item in items if item["company_id"] == company_id]
        if not items:
            raise ValueError("Empresa não encontrada")
    return items


def check_one(home: Path, item: dict[str, Any]) -> dict[str, Any]:
    config = profiles.load_config(home, item["company_id"])
    checked = collector.check(config, allow_environment=False)
    return {"company_id": item["company_id"], "cnpj": item["cnpj"],
            "enabled": item["enabled"], **checked}


def status_one(home: Path, item: dict[str, Any]) -> dict[str, Any]:
    config = profiles.load_config(home, item["company_id"])
    with closing(collector.open_state(config)) as db:
        value = collector.status(db, detailed=True)
    return {"company_id": item["company_id"], "cnpj": item["cnpj"],
            "name": item["name"], "enabled": item["enabled"], **value}


def cycle_one(home: Path, item: dict[str, Any]) -> dict[str, Any]:
    config = profiles.load_config(home, item["company_id"])
    collector.check(config, allow_environment=False)
    token = collector.read_token(config, allow_environment=False)
    with collector.single_instance(Path(config["state_dir"])):
        with closing(collector.open_state(config)) as db:
            result = collector.run_cycle(config, db, token)
    return {"company_id": item["company_id"], "cnpj": item["cnpj"], **result}


def run_command(home: Path, command: str, company_id: str | None = None) -> tuple[dict[str, Any], int]:
    items = selected(home, company_id)
    if command in {"check", "once"}:
        items = [item for item in items if item["enabled"]]
        if not items:
            return {"profiles": [], "count": 0, "failed": True,
                    "error": "Nenhuma empresa ativa cadastrada"}, 1
    outcome: list[dict[str, Any]] = []
    failed = False
    for item in items:
        try:
            if command == "check":
                value = check_one(home, item)
            elif command == "status":
                value = status_one(home, item)
            else:
                value = cycle_one(home, item)
                failed |= value["delivery"]["failed"] > 0
            outcome.append(value)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            # Nunca retornar representação de um token, certificado ou XML.
            failed = True
            LOGGER.warning("Perfil %s: %s", item["company_id"], type(exc).__name__)
            outcome.append({"company_id": item["company_id"], "cnpj": item["cnpj"],
                            "error": type(exc).__name__})
    return {"profiles": outcome, "count": len(outcome), "failed": failed}, 1 if failed else 0


def serve(home: Path, *, stop: threading.Event | None = None) -> None:
    """Uma só instância percorre os perfis; mudanças entram no próximo ciclo."""
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
    next_run: dict[str, float] = {}
    versions: dict[str, float] = {}
    with collector.single_instance(home):
        LOGGER.info("Serviço local iniciado")
        while not stop.is_set():
            try:
                items = [item for item in selected(home) if item["enabled"]]
                now = time.monotonic()
                active = {item["company_id"] for item in items}
                next_run = {key: value for key, value in next_run.items() if key in active}
                versions = {key: value for key, value in versions.items() if key in active}
                for item in items:
                    if stop.is_set():
                        break
                    key = item["company_id"]
                    if versions.get(key) != item["updated_at"]:
                        next_run.pop(key, None)
                        versions[key] = item["updated_at"]
                    if next_run.get(key, 0) > now:
                        continue
                    try:
                        config = profiles.load_config(home, key)
                        result = cycle_one(home, item)
                        LOGGER.info("Ciclo %s: %s", key, json.dumps(result, ensure_ascii=False))
                        next_run[key] = time.monotonic() + config["poll_seconds"]
                    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                        LOGGER.error("Falha no perfil %s: %s", key, type(exc).__name__)
                        next_run[key] = time.monotonic() + 30
            except (OSError, ValueError, sqlite3.Error) as exc:
                LOGGER.error("Falha ao consultar catálogo: %s", type(exc).__name__)
            wait = min(5.0, max(0.5, min(next_run.values(), default=time.monotonic() + 5) - time.monotonic()))
            stop.wait(wait)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serviço local de coleta fiscal multiperfil")
    parser.add_argument("--home", type=Path, default=profiles.default_home())
    parser.add_argument("--version", action="version", version=f"fiscal-xml-worker {collector.version()}")
    parser.add_argument("command", choices=("check", "once", "status", "serve", "self-test"))
    parser.add_argument("--company-id", help="Selecionar uma empresa para diagnóstico ou execução")
    parser.add_argument("--offline", action="store_true", help="Autoteste sem rede (apenas self-test)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "self-test":
            if not args.offline:
                parser.error("self-test exige --offline")
            print(json.dumps(collector.self_test(), ensure_ascii=False))
            return 0
        home = profiles.require_home(args.home)
        if args.command == "serve":
            if args.company_id:
                parser.error("O serviço sempre gerencia todos os perfis ativos")
            serve(home)
            return 0
        company_id = str(uuid.UUID(args.company_id)) if args.company_id else None
        result, code = run_command(home, args.command, company_id)
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        LOGGER.error("Falha do serviço local: %s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
