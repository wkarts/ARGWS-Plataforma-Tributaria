#!/usr/bin/env python3
"""Build and verify an identical, secret-free deployment matrix for every release.

This archive contains only the configurations and operation tools for GHCR
deployments. The self-hosted bundle ships separately with API/web source.
"""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ASSET_PATHS = (
    "deploy/compose.yaml", "deploy/env.develop.example",
    "deploy/env.production.example", "deploy/README.md",
    "deploy/develop", "deploy/production", "deploy/docker",
    "deploy/dockge", "deploy/portainer", "deploy/cloudpanel",
    "scripts/deploy.sh", "scripts/backup.sh", "scripts/rollback.py",
    "scripts/preflight.py", "scripts/version.py", "scripts/sync_deploy_env.py",
    "tools/deployer", "docs/CALCULO-TRIBUTARIO-LOCAL.md", "VERSION",
)
EXCLUDED_PARTS = {"secrets", "backups", "volumes", ".git", "__pycache__", ".venv",
                  "node_modules", "dist"}
EXCLUDED_SUFFIXES = {".db", ".sqlite3", ".pem", ".p12", ".pfx", ".pyc", ".log"}
REQUIRED = {
    "deploy/compose.yaml", "deploy/env.develop.example",
    "deploy/env.production.example", "deploy/README.md",
    "scripts/deploy.sh", "scripts/backup.sh", "scripts/rollback.py",
    "scripts/preflight.py", "scripts/version.py", "scripts/sync_deploy_env.py",
    "tools/deployer/deployer.py", "tools/deployer/gui.py",
    "docs/CALCULO-TRIBUTARIO-LOCAL.md", "VERSION",
}
for _target in ("", "docker/", "dockge/", "portainer/", "cloudpanel/"):
    for _channel in ("develop", "production"):
        for _name in ("compose.yaml", ".env.example"):
            REQUIRED.add(f"deploy/{_target}{_channel}/{_name}")


def _safe(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and not (set(parts) & EXCLUDED_PARTS) and (
        parts[-1] != ".env" and PurePosixPath(name).suffix.lower() not in EXCLUDED_SUFFIXES
    )


def collect(root: Path = ROOT) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name in ASSET_PATHS:
        item = root / name
        if not item.exists() or item.is_symlink():
            raise ValueError(f"Arquivo de distribuição ausente ou inválido: {name}")
        candidates = sorted(item.rglob("*")) if item.is_dir() else [item]
        for path in candidates:
            if path.is_symlink():
                raise ValueError(f"Link simbólico não permitido no pacote: {path.relative_to(root)}")
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _safe(rel):
                result[rel] = path
    missing = REQUIRED - result.keys()
    if missing:
        raise ValueError(f"Pacote sem recursos obrigatórios: {sorted(missing)}")
    return dict(sorted(result.items()))


def verify(path: Path, expected: dict[str, Path]) -> None:
    with ZipFile(path) as archive:
        infos = archive.infolist()
        names = [value.filename for value in infos]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Pacote tem arquivos duplicados, extras ou ausentes")
        if any(not _safe(name) for name in names):
            raise ValueError("Arquivo privado encontrado no pacote de distribuição")
        for name, source in expected.items():
            if archive.read(name) != source.read_bytes():
                raise ValueError(f"Conteúdo diverge da árvore versionada: {name}")


def build(output: Path, root: Path = ROOT) -> dict[str, int]:
    sources = collect(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("O destino não pode ser um link simbólico")
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6,
                 allowZip64=True) as archive:
        for name, source in sources.items():
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = ((0o755 if name.endswith(".sh") else 0o644) << 16)
            archive.writestr(info, source.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=6)
    verify(output, sources)
    return {"files": len(sources), "bytes": output.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description="Empacotar 10 modelos Compose validados e utilitários")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        details = build(args.output.expanduser().resolve())
    except (OSError, ValueError) as exc:
        print(f"Falha na distribuição: {exc}", file=sys.stderr)
        return 1
    print(f"Pacote de deploy validado: {details['files']} arquivos, {details['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
