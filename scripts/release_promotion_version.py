#!/usr/bin/env python3
"""Choose a new SemVer for a verified promotion without altering develop.

The source branch may deliberately retain its original VERSION. Releases are
immutable and a stable tag must never be reused for another commit.
"""
from __future__ import annotations

import argparse
import re

SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def parse_version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not SEMVER.fullmatch(value):
        raise ValueError(f"Versão SemVer inválida: {value!r}")
    return tuple(int(component) for component in value.split("."))  # type: ignore[return-value]


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def choose_stable_version(main_version: str, develop_version: str,
                          occupied_tags: set[str] | None = None) -> str:
    main = parse_version(main_version)
    develop = parse_version(develop_version)
    tags = occupied_tags or set()
    candidate = max((main[0], main[1], main[2] + 1), develop)
    for _ in range(10000):
        version = format_version(candidate)
        if f"v{version}" not in tags and version not in tags:
            return version
        candidate = (candidate[0], candidate[1], candidate[2] + 1)
    raise ValueError("Não foi possível reservar uma versão estável livre")


def main() -> None:
    parser = argparse.ArgumentParser(description="Selecionar a próxima versão estável, sem reutilizar tags")
    parser.add_argument("--main-version", required=True)
    parser.add_argument("--develop-version", required=True)
    parser.add_argument("--existing-tags", default="", help="Tags conhecidas, separadas por novas linhas")
    args = parser.parse_args()
    print(choose_stable_version(args.main_version, args.develop_version,
                                set(args.existing_tags.splitlines())))


if __name__ == "__main__":
    main()
