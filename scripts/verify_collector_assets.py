#!/usr/bin/env python3
"""Verify the exact customer-facing GUI packages before release promotion."""
from __future__ import annotations

import hashlib
import re
import sys
import tarfile
from pathlib import Path

ARTIFACTS = {
    "linux": "fiscal-xml-collector-gui-linux-x64.tar.gz",
    "windows": "fiscal-xml-collector-setup-windows-x64.exe",
}
LINUX_FILES = {
    "fiscal-xml-collector/fiscal-xml-gui-linux-x64",
    "fiscal-xml-collector/fiscal-xml-worker-linux-x64",
    "fiscal-xml-collector/fiscal-xml-self.service",
    "fiscal-xml-collector/self-install.sh",
    "fiscal-xml-collector/self-uninstall.sh",
    "fiscal-xml-collector/README.md",
}


def _is_elf_x64(header: bytes) -> bool:
    return len(header) >= 20 and header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b"\x3e\x00"


def _verify_linux_package(binary: Path) -> None:
    with tarfile.open(binary, "r:gz") as archive:
        members = archive.getmembers()
        names = [item.name for item in members if item.isfile()]
        if len(names) != len(set(names)) or set(names) != LINUX_FILES:
            raise ValueError("Linux: pacote GUI/worker incompleto ou com arquivos inesperados")
        for item in members:
            if not (item.isfile() or item.isdir()) or item.name.startswith("/") or ".." in Path(item.name).parts:
                raise ValueError("Linux: pacote contém link, device ou caminho inseguro")
        for name in ("fiscal-xml-gui-linux-x64", "fiscal-xml-worker-linux-x64"):
            member = archive.getmember(f"fiscal-xml-collector/{name}")
            stream = archive.extractfile(member)
            if stream is None or not _is_elf_x64(stream.read(64)):
                raise ValueError(f"Linux: {name} não é ELF x64")


def _verify_windows_package(binary: Path) -> None:
    with binary.open("rb") as handle:
        header = handle.read(4096)
    if len(header) < 512 or header[:2] != b"MZ":
        raise ValueError("Windows: instalador não é PE")
    offset = int.from_bytes(header[0x3C:0x40], "little")
    # The installer bootstrap may be PE32 even when the payload is strictly x64.
    if offset + 6 > len(header) or header[offset:offset + 4] != b"PE\0\0" or header[offset + 4:offset + 6] not in (b"\x4c\x01", b"\x64\x86"):
        raise ValueError("Windows: cabeçalho PE inválido")


def verify(directory: Path, platform: str) -> str:
    name = ARTIFACTS[platform]
    expected = {name, "SHA256SUMS.txt"}
    if not directory.is_dir() or {entry.name for entry in directory.iterdir()} != expected:
        raise ValueError(f"{platform}: conjunto de arquivos inesperado ou incompleto")
    binary = directory / name
    manifest = directory / "SHA256SUMS.txt"
    if not binary.is_file() or binary.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        raise ValueError(f"{platform}: artefato irregular")
    line = manifest.read_text(encoding="ascii").rstrip("\r\n")
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
    if not match or match.group(2) != name:
        raise ValueError(f"{platform}: manifesto SHA-256 inválido")
    digest = hashlib.sha256()
    with binary.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != match.group(1):
        raise ValueError(f"{platform}: checksum divergente")
    if platform == "linux":
        _verify_linux_package(binary)
    else:
        _verify_windows_package(binary)
    return digest.hexdigest()


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "--single" and args[1] in ARTIFACTS:
        jobs = [(args[1], Path(args[2]))]
    elif len(args) == 2:
        jobs = list(zip(ARTIFACTS, map(Path, args)))
    else:
        print("Uso: verify_collector_assets.py [--single linux|windows dir] ou <dir-linux> <dir-windows>", file=sys.stderr)
        return 2
    try:
        for platform, directory in jobs:
            print(f"{platform}: {verify(directory, platform)}")
    except (KeyError, OSError, UnicodeError, ValueError, tarfile.TarError) as exc:
        print(f"Coletor: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
