#!/usr/bin/env python3
"""Lê a versão estável canônica; canais de release não mudam VERSION."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


def read_version() -> str:
    value = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if SEMVER.fullmatch(value) is None:
        raise ValueError("VERSION deve ser MAJOR.MINOR.PATCH sem sufixos")
    return value


def package_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    api = ROOT / "apps/api/pyproject.toml"
    if api.is_file():
        metadata = tomllib.loads(api.read_text(encoding="utf-8"))
        result[str(api.relative_to(ROOT))] = metadata["project"]["version"]
    web = ROOT / "apps/web/package.json"
    if web.is_file():
        result[str(web.relative_to(ROOT))] = json.loads(web.read_text(encoding="utf-8"))["version"]
    lock = ROOT / "apps/web/package-lock.json"
    if lock.is_file():
        content = json.loads(lock.read_text(encoding="utf-8"))
        result[str(lock.relative_to(ROOT))] = content["version"]
        result[f"{lock.relative_to(ROOT)} packages['']"] = content["packages"][""]["version"]
    return result


def atomic_write(path: Path, content: str) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def set_version(version: str) -> None:
    if not SEMVER.fullmatch(version):
        raise ValueError("Nova versão deve ser MAJOR.MINOR.PATCH")
    updates: dict[Path, str] = {ROOT / "VERSION": version + "\n"}
    api = ROOT / "apps/api/pyproject.toml"
    if api.is_file():
        lines = api.read_text(encoding="utf-8").splitlines(keepends=True)
        section = ""
        indexes: list[int] = []
        for index, line in enumerate(lines):
            if line.lstrip().startswith("["):
                section = line.strip()
            elif section == "[project]" and line.lstrip().startswith("version ="):
                indexes.append(index)
        if len(indexes) != 1:
            raise ValueError("pyproject.toml precisa de um único project.version")
        lines[indexes[0]] = f'version = "{version}"\n'
        updates[api] = "".join(lines)
    web = ROOT / "apps/web/package.json"
    if web.is_file():
        data = json.loads(web.read_text(encoding="utf-8"))
        data["version"] = version
        updates[web] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    lock = ROOT / "apps/web/package-lock.json"
    if lock.is_file():
        data = json.loads(lock.read_text(encoding="utf-8"))
        data["version"] = version
        data["packages"][""]["version"] = version
        updates[lock] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    for path, content in updates.items():
        atomic_write(path, content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("validate", "current", "next", "check", "set"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    version = read_version()
    if args.action == "next":
        if args.value not in ("major", "minor", "patch"):
            parser.error("next exige major, minor ou patch")
        major, minor, patch = map(int, version.split("."))
        if args.value == "major":
            print(f"{major + 1}.0.0")
        elif args.value == "minor":
            print(f"{major}.{minor + 1}.0")
        else:
            print(f"{major}.{minor}.{patch + 1}")
    elif args.action == "check":
        drift = {path: item for path, item in package_versions().items() if item != version}
        if drift:
            parser.error("Metadados fora de VERSION: " + ", ".join(f"{name}={value}" for name, value in drift.items()))
        print(version)
    elif args.action == "set":
        if args.value is None:
            parser.error("set exige versão X.Y.Z")
        set_version(args.value)
        print(args.value)
    else:
        print(version)


if __name__ == "__main__":
    main()
