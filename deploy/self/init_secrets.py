"""Create private, installation-specific secrets for the local Compose profile."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import secrets


HERE = Path(__file__).resolve().parent


def write_new(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preparar os segredos da instalação self")
    parser.add_argument("--project", help="Identificador desta instalação no Docker Compose")
    parser.add_argument("--email", help="E-mail do primeiro administrador")
    parser.add_argument("--erase-bootstrap-password", action="store_true",
                        help="Esvaziar o segredo inicial após confirmar o primeiro login")
    args = parser.parse_args()
    secrets_dir = HERE / "secrets"
    password_file = secrets_dir / "admin-password"

    if args.erase_bootstrap_password:
        if not password_file.is_file() or password_file.is_symlink():
            parser.error("Segredo inicial não encontrado")
        with password_file.open("wb"):
            pass
        print("Senha de bootstrap esvaziada; a senha do administrador no banco permanece válida.")
        return

    if not args.project or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", args.project):
        parser.error("--project deve ter de 2 a 63 caracteres: letras minúsculas, números, '-' ou '_'")
    if not args.email or not re.fullmatch(r"[^\s@]+@[^\s@]+", args.email):
        parser.error("--email inválido")
    if any(path.exists() for path in (HERE / ".env", secrets_dir / "signing-key", password_file)):
        parser.error("Instalação já inicializada; segredos existentes não serão sobrescritos")

    password = getpass.getpass("Senha inicial do administrador (mínimo 12 caracteres): ")
    if len(password) < 12:
        parser.error("A senha deve ter pelo menos 12 caracteres")
    if password != getpass.getpass("Repita a senha: "):
        parser.error("As senhas não conferem")

    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        secrets_dir.chmod(0o700)
    write_new(secrets_dir / "signing-key", secrets.token_urlsafe(48) + "\n")
    write_new(password_file, password + "\n")
    write_new(HERE / ".env", f"COMPOSE_PROJECT_NAME={args.project}\n"
              f"BOOTSTRAP_ADMIN_EMAIL={args.email.lower()}\n"
              "WEB_BIND_ADDRESS=127.0.0.1\nWEB_PORT=48080\n")
    print("Instalação preparada. Inicie com: docker compose --env-file deploy/self/.env "
          "-f deploy/self/compose.yaml up -d --build")


if __name__ == "__main__":
    main()
