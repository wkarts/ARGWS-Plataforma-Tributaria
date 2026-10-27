#!/usr/bin/env python3
"""Check customer-facing self-hosted artifacts for fixed upstream identity.

This checks rendered web text, the self-hosted deployment template, and release
asset names. Registry provenance, Git history, and legacy deployment templates
are intentionally outside this customer-facing gate.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
import zipfile
from pathlib import Path


TEXT_SUFFIXES = {".css", ".html", ".ini", ".js", ".json", ".map", ".md",
                 ".py", ".sh", ".ps1", ".svg", ".toml", ".ts", ".txt",
                 ".vue", ".webmanifest", ".xml", ".yaml", ".yml"}
WEB_REQUIRED = {"index.html", "manifest.webmanifest", "config.js", "sw.js"}
MAX_TEXT_BYTES = 32 * 1024 * 1024
MARKERS = {
    "identidade de origem": re.compile(r"argws|wkarts|pige360|reforma[\s_.-]*tributaria|\breforma\b"),
    "branch de desenvolvimento": re.compile(r"(?<![a-z])develop(?![a-z])"),
    "referência de branch": re.compile(r"refs/heads/main\b|branch[\s:=/_-]+main\b"),
}


# Machine-readable source files can legitimately reference a development channel
# or the generic tax-reform domain. Fixed vendor identities are not permitted.
SOURCE_MARKERS = {
    "identidade comercial fixa no código": re.compile(r"argws|wkarts|pige360|fersoft"),
}
TECHNICAL_SOURCE_SUFFIXES = {".py", ".sh", ".ps1"}


def normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def markers(value: str, *, technical_source: bool = False) -> list[str]:
    value = normalized(value)
    marker_set = SOURCE_MARKERS if technical_source else MARKERS
    return [name for name, pattern in marker_set.items() if pattern.search(value)]


def inspect_text(path: Path, display: str, *, technical_source: bool = False) -> list[str]:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return [f"{display}: arquivo de texto excede {MAX_TEXT_BYTES} bytes"]
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"{display}: leitura inválida ({type(exc).__name__})"]
    return [f"{display}: {marker}" for marker in markers(content, technical_source=technical_source)]


def inspect_tree(root: Path, *, web: bool = False) -> list[str]:
    if not root.is_dir() or root.is_symlink():
        return [f"{root}: diretório ausente ou inválido"]
    errors = []
    if web:
        for name in sorted(WEB_REQUIRED):
            if not (root / name).is_file():
                errors.append(f"{root / name}: recurso web obrigatório ausente")
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            errors.append(f"{item}: link simbólico não auditado")
            continue
        if not item.is_file():
            continue
        display = str(item.relative_to(root))
        technical_source = item.suffix.lower() in TECHNICAL_SOURCE_SUFFIXES
        errors.extend(f"{display}: {marker}" for marker in markers(display, technical_source=technical_source))
        if item.suffix.lower() in TEXT_SUFFIXES or item.name.startswith(".env"):
            errors.extend(inspect_text(item, display, technical_source=technical_source))
    return errors


def inspect_release_names(directory: Path) -> list[str]:
    if not directory.is_dir() or directory.is_symlink():
        return [f"{directory}: diretório de assets ausente ou inválido"]
    entries = list(directory.iterdir())
    if not entries:
        return [f"{directory}: nenhum asset para auditar"]
    errors = []
    for item in entries:
        if not item.is_file() or item.is_symlink():
            errors.append(f"{item.name}: asset inválido")
        errors.extend(f"{item.name}: {marker}" for marker in markers(item.name))
        if item.name.startswith("Fiscal-Self-Deploy-") and item.suffix == ".zip":
            try:
                with zipfile.ZipFile(item) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        technical_source = Path(member.filename).suffix.lower() in TECHNICAL_SOURCE_SUFFIXES
                        errors.extend(f"{item.name}/{member.filename}: {marker}"
                                      for marker in markers(member.filename, technical_source=technical_source))
                        if member.file_size > MAX_TEXT_BYTES:
                            continue
                        suffix = Path(member.filename).suffix.lower()
                        if suffix in TEXT_SUFFIXES or Path(member.filename).name.startswith(".env"):
                            try:
                                content = archive.read(member).decode("utf-8")
                            except (UnicodeError, OSError, RuntimeError, zipfile.BadZipFile):
                                errors.append(f"{item.name}/{member.filename}: leitura inválida")
                                continue
                            errors.extend(f"{item.name}/{member.filename}: {marker}"
                                          for marker in markers(content, technical_source=technical_source))
            except (OSError, zipfile.BadZipFile):
                errors.append(f"{item.name}: ZIP inválido")
    return errors


def audit(web_dist: Path, self_dir: Path | None = None,
          release_dir: Path | None = None) -> list[str]:
    errors = inspect_tree(web_dist, web=True)
    if self_dir is not None:
        errors.extend(inspect_tree(self_dir))
    if release_dir is not None:
        errors.extend(inspect_release_names(release_dir))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audita nomes e textos visíveis na distribuição self-hosted")
    parser.add_argument("--web-dist", type=Path, required=True)
    parser.add_argument("--self-dir", type=Path)
    parser.add_argument("--release-dir", type=Path)
    args = parser.parse_args(argv)
    errors = audit(args.web_dist, args.self_dir, args.release_dir)
    if errors:
        for error in errors:
            print(f"ERRO: {error}", file=sys.stderr)
        return 1
    print("OK: superfícies de cliente auditadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
