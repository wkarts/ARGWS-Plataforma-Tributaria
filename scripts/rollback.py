#!/usr/bin/env python3
"""Troca somente APP_TAG e preserva backup do .env. Execução é separada."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from version import SEMVER


def set_tag(env: Path, tag: str) -> Path:
    if not (SEMVER.fullmatch(tag.removeprefix("v")) or re.fullmatch(r"sha-[a-f0-9]{12}", tag)):
        raise ValueError("Tag inválida: use vX.Y.Z ou sha-<12 hex>")
    if not env.is_file() or env.is_symlink():
        raise ValueError(".env regular não encontrado")
    content = env.read_text(encoding="utf-8")
    rows = content.splitlines(keepends=True)
    indexes = [index for index, line in enumerate(rows) if line.startswith("APP_TAG=")]
    if len(indexes) != 1:
        raise ValueError(".env deve conter uma única linha APP_TAG=")
    rows[indexes[0]] = f"APP_TAG={tag}\n"
    backup_dir = env.parent / "backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    backup = backup_dir / f"env-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.bak"
    shutil.copy2(env, backup)
    os.chmod(backup, 0o600)
    fd, temporary = tempfile.mkstemp(prefix=".env.", dir=env.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.writelines(rows)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, env)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    backup = set_tag(args.directory / ".env", args.tag)
    print(f"APP_TAG alterada. Backup anterior: {backup}. Execute scripts/deploy.sh após avaliar schema.")
