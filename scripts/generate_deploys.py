#!/usr/bin/env python3
"""Mantém as oito variantes e dois modelos canônicos iguais ao Compose fonte."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
TARGETS = ("docker", "dockge", "portainer", "cloudpanel")
CHANNELS = ("develop", "production")


def expected(target: str | None, channel: str) -> tuple[str, str]:
    compose = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    env = (DEPLOY / f"env.{channel}.example").read_text(encoding="utf-8")
    project = f"reforma-tributaria-{channel}"
    if target:
        env = env.replace(
            f"COMPOSE_PROJECT_NAME={project}",
            f"COMPOSE_PROJECT_NAME=reforma-tributaria-{target}-{channel}",
        )
    return compose, env


def run(check: bool) -> int:
    errors: list[str] = []
    for target in (None, *TARGETS):
        for channel in CHANNELS:
            directory = DEPLOY / target / channel if target else DEPLOY / channel
            compose, env = expected(target, channel)
            for filename, content in (("compose.yaml", compose), (".env.example", env)):
                path = directory / filename
                if check:
                    if not path.is_file() or path.read_text(encoding="utf-8") != content:
                        errors.append(str(path.relative_to(ROOT)))
                else:
                    directory.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
    if errors:
        print("Templates ausentes ou divergentes:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    print("OK: 10 diretórios completos, 8 destinos + 2 modelos canônicos")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    raise SystemExit(run(parser.parse_args().check))
