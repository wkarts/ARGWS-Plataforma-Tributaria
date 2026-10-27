#!/usr/bin/env python3
"""Add newly introduced optional settings without rewriting installed credentials.

Dry-run by default. The existing .env is never parsed as shell code.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("docker", "dockge", "portainer", "cloudpanel", "canonical")
CHANNELS = ("develop", "production")
OPTIONAL_KEYS = (
    "TAX_CALCULATOR_REGULAR_URL", "TAX_CALCULATOR_REGULAR_VERSION_URL",
    "TAX_CALCULATOR_SIMPLES_URL", "TAX_CALCULATOR_SIMPLES_VERSION_URL",
)


def read_keys(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for number, row in enumerate(text.splitlines(), 1):
        line = row.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in row:
            raise ValueError(f".env linha {number} não contém '='")
        name, value = row.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in found:
            raise ValueError(f".env linha {number} contém chave inválida ou duplicada")
        found[name] = value
    return found


def template_path(target: str, channel: str) -> Path:
    if target not in TARGETS or channel not in CHANNELS:
        raise ValueError("Destino ou canal inválido")
    parent = ROOT / "deploy" if target == "canonical" else ROOT / "deploy" / target
    return parent / channel / ".env.example"


def synchronize(directory: Path, template: Path, *, apply: bool = False) -> tuple[list[str], Path | None]:
    """Only append the exact empty optional keys listed in the versioned template."""
    env = directory / ".env"
    if not env.is_file() or env.is_symlink() or directory.is_symlink():
        raise ValueError(".env existente deve ser arquivo regular, sem link simbólico")
    if not template.is_file() or template.is_symlink():
        raise ValueError("Modelo de ambiente não encontrado")
    if env.stat().st_size > 1024 * 1024:
        raise ValueError(".env excede o limite seguro de 1 MiB")
    old_bytes = env.read_bytes()
    text = old_bytes.decode("utf-8")
    current = read_keys(text)
    expected = read_keys(template.read_text(encoding="utf-8"))
    if any(expected.get(name) != "" for name in OPTIONAL_KEYS):
        raise ValueError("Modelo não define valores opcionais vazios")
    missing = [name for name in OPTIONAL_KEYS if name not in current]
    if not missing or not apply:
        return missing, None
    separator = "" if text.endswith("\n") else "\n"
    content = text + separator + (
        "\n# Novos parâmetros opcionais; habilite apenas após validar o motor local.\n"
        + "".join(f"{name}=\n" for name in missing)
    )
    backup_dir = directory / "backups"
    if backup_dir.is_symlink():
        raise ValueError("Diretório de backup não pode ser link simbólico")
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        backup_dir.chmod(0o700)
    backup = backup_dir / f"env-before-calculator-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.bak"
    with backup.open("xb") as stream:
        stream.write(old_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    backup.chmod(0o600)
    descriptor, temporary = tempfile.mkstemp(prefix=".env.", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, env)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return missing, backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Adicionar configurações opcionais a uma instalação existente")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--channel", choices=CHANNELS, required=True)
    parser.add_argument("--apply", action="store_true", help="Aplicar e fazer backup (padrão: apenas diagnóstico)")
    args = parser.parse_args()
    try:
        missing, backup = synchronize(
            args.directory.expanduser().resolve(), template_path(args.target, args.channel),
            apply=args.apply,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if missing:
        print("Chaves a acrescentar: " + ", ".join(missing))
    else:
        print("Configuração já atualizada; nenhuma alteração necessária.")
    if backup is not None:
        print(f"Backup protegido criado: {backup}")
    elif missing:
        print("Execução apenas de diagnóstico. Use --apply para atualizar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
