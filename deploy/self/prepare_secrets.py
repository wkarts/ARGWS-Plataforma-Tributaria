"""Read root-only Compose secret mounts, then run the application as UID 10001.

The copies live only on the service's private tmpfs. No secret value is placed
in process environment variables, command arguments or an image layer.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_UID = 10001
SECRET_DIR = Path("/run/app-secrets")
SECRET_FILES = {
    "SECRET_KEY_FILE": "signing-key",
    "BOOTSTRAP_ADMIN_PASSWORD_FILE": "admin-password",
}
OPTIONAL_SECRET_FILES = {"CBS_CLIENT_SECRET_FILE": "cbs-client-secret"}
MAX_SECRET_BYTES = 4096


def stage_secret(source: Path, target: Path) -> None:
    # Open with O_NOFOLLOW when supported to reject a swapped symlink.
    with os.fdopen(os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        value = stream.read(MAX_SECRET_BYTES + 1)
    if len(value) > MAX_SECRET_BYTES:
        raise RuntimeError("Segredo de instalação excede o tamanho permitido")
    try:
        with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            os.fchown(stream.fileno(), APP_UID, APP_UID)
            stream.write(value)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main(command: list[str]) -> None:
    if not command or os.geteuid() != 0:
        raise RuntimeError("O preparo de segredos exige entrada como root e um comando")
    os.umask(0o077)
    SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(SECRET_DIR, APP_UID, APP_UID)
    SECRET_DIR.chmod(0o700)
    for variable, name in SECRET_FILES.items():
        source = os.environ.get(variable)
        if not source:
            raise RuntimeError(f"{variable} não configurado")
        target = SECRET_DIR / name
        stage_secret(Path(source), target)
        os.environ[variable] = str(target)
    for variable, name in OPTIONAL_SECRET_FILES.items():
        source = os.environ.get(variable)
        if source:
            target = SECRET_DIR / name
            stage_secret(Path(source), target)
            os.environ[variable] = str(target)
    os.setgroups([])
    os.setgid(APP_UID)
    os.setuid(APP_UID)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main(sys.argv[1:])
