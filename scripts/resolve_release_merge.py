#!/usr/bin/env python3
"""Merge validated develop into main, safely resolving only version metadata.

All non-version conflicts remain blocking. The next step MUST update all four
version files together with scripts/version.py; no fiscal data is modified.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


VERSION_FILES = frozenset({
    "VERSION",
    "apps/api/pyproject.toml",
    "apps/web/package.json",
    "apps/web/package-lock.json",
})
SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_\.-]{0,190}\Z")


class PromotionConflict(RuntimeError):
    pass


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)


def must(*args: str, cwd: Path | None = None) -> bytes:
    result = git(*args, cwd=cwd)
    if result.returncode:
        raise PromotionConflict("Falha no Git: " + " ".join(args) +
                                " (código " + str(result.returncode) + ")")
    return result.stdout


def merge_versioned(source: str, *, cwd: Path | None = None) -> list[str]:
    if not SOURCE_NAME.fullmatch(source) or ".." in source:
        raise PromotionConflict("Referência Git inválida para promoção")
    if must("status", "--porcelain", cwd=cwd).strip():
        raise PromotionConflict("O workspace contém alterações não confirmadas")
    result = git("merge", "--no-ff", "--no-edit", source, cwd=cwd)
    if result.returncode == 0:
        return []

    conflicts_bytes = must("diff", "--name-only", "--diff-filter=U", "-z", cwd=cwd)
    conflicts = sorted(set(name.decode("utf-8") for name in
                           conflicts_bytes.split(b"\x00") if name))
    unexpected = sorted(set(conflicts) - VERSION_FILES)
    if not conflicts or unexpected:
        git("merge", "--abort", cwd=cwd)
        detail = ", ".join(unexpected or conflicts) if conflicts else "sem caminhos identificáveis"
        raise PromotionConflict(
            "Conflito em arquivos que exigem revisão; nenhum conteúdo foi descartado: " + detail
        )

    try:
        for name in conflicts:
            # Keep the already-published main side. The validated release
            # version will be applied consistently to all four files next.
            must("checkout", "--ours", "--", name, cwd=cwd)
            must("add", "--", name, cwd=cwd)
        must("commit", "--no-edit", cwd=cwd)
    except PromotionConflict:
        git("merge", "--abort", cwd=cwd)
        raise
    return conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge fiscal com resolução restrita a versões")
    parser.add_argument("--source", default="origin/develop")
    args = parser.parse_args()
    try:
        fixed = merge_versioned(args.source)
    except PromotionConflict as exc:
        print("ERRO: " + str(exc), file=sys.stderr)
        return 1
    if fixed:
        print("Merge concluído; conflitos resolvidos apenas nos metadados: " +
              ", ".join(fixed))
    else:
        print("Merge concluído sem conflitos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
