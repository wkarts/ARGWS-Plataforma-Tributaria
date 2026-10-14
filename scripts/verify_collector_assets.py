#!/usr/bin/env python3
"""Confere os dois artefatos do coletor antes de publicar uma release."""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ARTIFACTS = {
    "linux": "fiscal-xml-collector-linux-amd64",
    "windows": "fiscal-xml-collector-windows-amd64.exe",
}


def verify(directory: Path, platform: str) -> str:
    name = ARTIFACTS[platform]
    expected = {name, "SHA256SUMS.txt"}
    if not directory.is_dir() or {entry.name for entry in directory.iterdir()} != expected:
        raise ValueError(f"{platform}: conjunto de arquivos inesperado ou incompleto")
    binary = directory / name
    manifest = directory / "SHA256SUMS.txt"
    if not binary.is_file() or binary.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        raise ValueError(f"{platform}: artefato irregular")
    line = manifest.read_text(encoding="ascii").strip("\n")
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
    if not match or match.group(2) != name:
        raise ValueError(f"{platform}: manifesto SHA-256 inválido")
    digest = hashlib.sha256()
    with binary.open("rb") as source:
        header = source.read(512)
        digest.update(header)
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != match.group(1):
        raise ValueError(f"{platform}: checksum divergente")
    if platform == "linux":
        if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
            raise ValueError("Linux: binário não é ELF x86_64")
    else:
        if len(header) < 512 or header[:2] != b"MZ":
            raise ValueError("Windows: binário não é PE x64")
        offset = int.from_bytes(header[0x3C:0x40], "little")
        if offset + 6 > len(header) or header[offset:offset + 4] != b"PE\0\0" or header[offset + 4:offset + 6] != b"\x64\x86":
            raise ValueError("Windows: binário não é PE x64")
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        print("Uso: verify_collector_assets.py <diretório-linux> <diretório-windows>", file=sys.stderr)
        return 2
    try:
        for platform, directory in zip(ARTIFACTS, sys.argv[1:]):
            print(f"{platform}: {verify(Path(directory), platform)}")
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Coletor: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
