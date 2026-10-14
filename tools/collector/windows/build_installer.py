#!/usr/bin/env python3
"""Compila o instalador Windows com metadados de marca validados localmente.

WinSW e Inno Setup são obtidos/pinados pelo CI antes deste comando. Um build
personalizado recompila a GUI para substituir ícone e metadados do executável.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


WIN_SW_SHA256 = "05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da"
DEFAULT_NAME = "Fiscal XML Collector"
DEFAULT_PUBLISHER = "Fiscal XML Collector"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?\Z")


def validate_label(value: str, field: str) -> str:
    if (not 1 <= len(value) <= 80 or value != value.strip() or not value[0].isalnum()
            or not all(ch.isalnum() or ch in " ._()-&+" for ch in value)):
        raise ValueError(f"{field}: use 1-80 letras, números, espaços, pontos, _()-&+; inicie com letra/número")
    return value


def validate_path(value: Path, *, icon: bool = False) -> Path:
    resolved = value.resolve(strict=True)
    if any(ch in str(resolved) for ch in '\r\n";#{}') or len(str(resolved)) > 240:
        raise ValueError("Caminho não permitido em parâmetro de compilação")
    if icon:
        if not resolved.is_file() or resolved.suffix.lower() != ".ico" or resolved.stat().st_size > 2_000_000:
            raise ValueError("Ícone deve ser um arquivo ICO válido de até 2 MB")
        with resolved.open("rb") as stream:
            if stream.read(4) != b"\0\0\1\0":
                raise ValueError("Cabeçalho ICO inválido")
    return resolved


def version_numbers(value: str) -> tuple[int, int, int, int]:
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError("Versão deve ser SemVer")
    numbers = tuple(int(match.group(i)) for i in (1, 2, 3))
    if any(number > 65535 for number in numbers):
        raise ValueError("Componente de versão excede recurso PE")
    return (*numbers, 0)


def version_resource(version: str, name: str, publisher: str) -> str:
    numbers = version_numbers(version)
    fields = {
        "CompanyName": publisher,
        "FileDescription": name + " - Interface gráfica",
        "FileVersion": version,
        "InternalName": "fiscal-xml-gui-windows-x64",
        "OriginalFilename": "fiscal-xml-gui-windows-x64.exe",
        "ProductName": name,
        "ProductVersion": version,
    }
    labels = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    return ("VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(numbers) + ", prodvers=" + repr(numbers)
            + ", mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
            + "kids=[StringFileInfo([StringTable('040904B0', [\n" + labels
            + "\n])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n")


def compiler_args(compiler: Path, root: Path, version: str, name: str,
                  publisher: str, icon: Path) -> list[str]:
    return [str(compiler), f"/DAPP_VERSION={version}", f"/DSOURCE_ROOT={root}",
            f"/DAPP_DISPLAY_NAME={name}", f"/DAPP_PUBLISHER={publisher}",
            f"/DAPP_ICON_PATH={icon}", str(root / "tools/collector/windows/installer.iss")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--compiler", required=True, type=Path, help="ISCC.exe verificado")
    parser.add_argument("--version", required=True)
    parser.add_argument("--display-name", default=DEFAULT_NAME)
    parser.add_argument("--publisher", default=DEFAULT_PUBLISHER)
    parser.add_argument("--icon", type=Path)
    args = parser.parse_args()
    try:
        if sys.platform != "win32":
            raise ValueError("Compile o pacote no Windows x64")
        name = validate_label(args.display_name, "Nome")
        publisher = validate_label(args.publisher, "Publisher")
        version_numbers(args.version)
        root = validate_path(args.source_root)
        compiler = validate_path(args.compiler)
        icon = validate_path(args.icon or root / "tools/collector/windows/collector.ico", icon=True)
        dist = root / "dist/collector"
        worker = dist / "fiscal-xml-worker-windows-x64.exe"
        wrapper = dist / "FiscalXmlCollector.exe"
        if not worker.is_file() or not wrapper.is_file():
            raise ValueError("Compile o worker e obtenha o WinSW verificado antes do instalador")
        if hashlib.sha256(wrapper.read_bytes()).hexdigest() != WIN_SW_SHA256:
            raise ValueError("Hash do WinSW diverge da versão verificada")
        if (name != DEFAULT_NAME or publisher != DEFAULT_PUBLISHER or args.icon is not None):
            # O título, o ícone e ProductName são metadados PE estáticos.
            dist.mkdir(parents=True, exist_ok=True)
            resource = dist / "fiscal-gui-version-info.txt"
            resource.write_text(version_resource(args.version, name, publisher), encoding="utf-8")
            subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                            "--onefile", "--windowed", "--uac-admin",
                            "--name", "fiscal-xml-gui-windows-x64", "--distpath", str(dist),
                            "--icon", str(icon), "--version-file", str(resource),
                            "--add-data", "VERSION;.", str(root / "tools/collector/gui.py")],
                           cwd=root, check=True)
        if not (dist / "fiscal-xml-gui-windows-x64.exe").is_file():
            raise ValueError("GUI frozen ausente")
        subprocess.run(compiler_args(compiler, root, args.version, name, publisher, icon),
                       cwd=root, check=True)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Build do instalador recusado: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
