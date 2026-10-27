#!/usr/bin/env python3
"""Prepare, inspect and operate an isolated fiscal Compose stack.

This file deliberately uses only the Python standard library so the same CLI
can run from source or be bundled alongside the desktop GUI on Windows.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from release_catalog import ReleaseCatalogError, load_catalog


TARGETS = ("docker", "dockge", "portainer", "cloudpanel")
CHANNELS = ("develop", "production")
SEMVER = re.compile(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
PRODUCT_SEMVER = re.compile(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")
SHA_TAG = re.compile(r"sha-[0-9a-f]{12}$")
OWNER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
PLACEHOLDER = re.compile(r"(?i)(?:change[_-]?me|replace[_-]?me|todo|your[_-]?secret|<[^>]+>)")
SECRET_KEYS = {
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(36),
    "REDIS_PASSWORD": lambda: secrets.token_urlsafe(36),
    "RABBITMQ_DEFAULT_PASS": lambda: secrets.token_urlsafe(36),
    "RABBITMQ_PASSWORD": lambda: secrets.token_urlsafe(36),
    "S3_ACCESS_KEY": lambda: "ak" + secrets.token_hex(14),
    "S3_SECRET_ACCESS_KEY": lambda: secrets.token_urlsafe(36),
    "S3_SECRET_KEY": lambda: secrets.token_urlsafe(36),
    "JWT_SECRET": lambda: secrets.token_urlsafe(48),
    "SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "APP_SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "ENCRYPTION_KEY": lambda: secrets.token_urlsafe(48),
    "WEBHOOK_SECRET": lambda: secrets.token_urlsafe(48),
    "BOOTSTRAP_ADMIN_PASSWORD": lambda: secrets.token_urlsafe(32),
}
META_DIR = ".deployer"
ENV_NAME = ".env"


class DeployerError(Exception):
    pass


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def product_version() -> str:
    path = project_root() / "VERSION"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DeployerError("VERSION não está disponível no pacote.") from exc
    if not PRODUCT_SEMVER.fullmatch(value):
        raise DeployerError("VERSION precisa conter uma versão SemVer válida.")
    return value.removeprefix("v")


def template_path(target: str, channel: str) -> Path:
    if target not in TARGETS or channel not in CHANNELS:
        raise DeployerError("Alvo ou canal inválido.")
    return project_root() / "deploy" / target / channel


def defaults(target: str, channel: str) -> dict:
    """Use the built-in model as authority for the GHCR namespace and web port."""
    path = template_path(target, channel) / ".env.example"
    try:
        env = parse_env(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeployerError("Modelos de instalação não encontrados no pacote.") from exc
    owner = env.get("IMAGE_OWNER", "").strip().lower()
    port = env.get("WEB_PORT", "")
    if not OWNER.fullmatch(owner):
        raise DeployerError("O modelo não possui namespace GHCR válido.")
    if not port.isdecimal() or not 40000 <= int(port) <= 49999:
        raise DeployerError("O modelo não possui porta web padrão válida.")
    return {
        "target": target, "channel": channel, "owner": owner,
        "web_port": int(port),
        "web_bind": env.get("WEB_BIND_ADDRESS", "127.0.0.1"),
        "embedded_version": product_version(),
        "default_tag": "latest" if channel == "production" else "develop",
    }


def catalog(target: str, channel: str, owner: str | None = None) -> dict:
    actual_owner = owner or defaults(target, channel)["owner"]
    try:
        return load_catalog(actual_owner, channel)
    except ReleaseCatalogError as exc:
        raise DeployerError(str(exc)) from exc


def image_tag(target: str, channel: str, owner: str, tag: str | None) -> str:
    """Resolve latest to a real stable tag before writing a production .env."""
    if tag == "latest":
        if channel != "production":
            return "develop"
        pinned = catalog(target, "production", owner)["latest"]
        if not pinned:
            raise DeployerError("Nenhuma release estável completa foi encontrada no GitHub.")
        return pinned
    return tag or ("develop" if channel == "develop" else product_version())


def parse_env(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise DeployerError("O arquivo .env contém uma linha sem '='.")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key) or key in values:
            raise DeployerError("O arquivo .env contém uma chave inválida ou repetida.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def render_env(template: str, values: dict[str, str]) -> str:
    lines = []
    seen = set()
    for line in template.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        if "=" not in line:
            raise DeployerError("Modelo .env.example inválido.")
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in seen:
            raise DeployerError("Modelo .env.example contém chaves repetidas.")
        seen.add(key)
        if key in values:
            value = values[key]
            if "\n" in value or "\r" in value or "\x00" in value:
                raise DeployerError("Valor de ambiente contém caractere de controle.")
            if re.search(r"[\s#$'\"\\]", value):
                raise DeployerError(f"{key} precisa conter um valor seguro para Compose dotenv.")
            lines.append(f"{key}={value}")
        else:
            lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def replace_env_values(content: str, updates: dict[str, str]) -> str:
    lines = []
    found = set()
    for line in content.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                found.add(key)
                continue
        lines.append(line)
    if set(updates) != found:
        raise DeployerError("O .env existente não contém APP_TAG; recupere o arquivo original.")
    return "\n".join(lines).rstrip() + "\n"


def secret_values(template: str) -> dict[str, str]:
    present = parse_env(template)
    result = {key: factory() for key, factory in SECRET_KEYS.items() if key in present}
    return result


def prepare_values(template: str, *, target: str, channel: str, owner: str,
                   tag: str | None, bind: str | None, port: int | None,
                   admin_email: str) -> dict[str, str]:
    if not OWNER.fullmatch(owner):
        raise DeployerError("IMAGE_OWNER deve ser um identificador GHCR minúsculo válido.")
    chosen = tag or ("develop" if channel == "develop" else product_version())
    if channel == "production" and not (SEMVER.fullmatch(chosen) or SHA_TAG.fullmatch(chosen)):
        raise DeployerError("Produção exige SemVer estável ou sha-<12 hex> publicado.")
    if channel == "develop" and chosen != "develop" and not (SEMVER.fullmatch(chosen) or SHA_TAG.fullmatch(chosen)):
        raise DeployerError("Tag develop, SemVer estável ou sha-<12 hex> inválida.")
    if bind is not None and bind not in ("127.0.0.1", "::1"):
        raise DeployerError("WEB_BIND_ADDRESS deve manter o serviço somente no loopback.")
    if port is not None and not 40000 <= port <= 49999:
        raise DeployerError("WEB_PORT deve estar entre 40000 e 49999.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", admin_email):
        raise DeployerError("Informe um e-mail válido do administrador inicial.")
    source = parse_env(template)
    values = secret_values(template)
    values.update({"IMAGE_OWNER": owner, "APP_TAG": chosen, "BOOTSTRAP_ADMIN_EMAIL": admin_email.lower()})
    if bind is not None:
        values["WEB_BIND_ADDRESS"] = bind
    if port is not None:
        values["WEB_PORT"] = str(port)
    # DSNs are rendered with exactly the credentials saved in this environment.
    merged = {**source, **values}
    pg_user = merged.get("POSTGRES_USER", "app")
    pg_db = merged.get("POSTGRES_DB", "app")
    mq_user = merged.get("RABBITMQ_DEFAULT_USER", merged.get("RABBITMQ_USER", "app"))
    if "DATABASE_URL" in source and "POSTGRES_PASSWORD" in values:
        scheme = "postgresql+asyncpg" if "+asyncpg" in source["DATABASE_URL"] else "postgresql"
        values["DATABASE_URL"] = f"{scheme}://{quote(pg_user, safe='')}:{quote(values['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{quote(pg_db, safe='')}"
    if "REDIS_URL" in source and "REDIS_PASSWORD" in values:
        values["REDIS_URL"] = f"redis://:{quote(values['REDIS_PASSWORD'], safe='')}@redis:6379/0"
    for name in ("BROKER_URL", "RABBITMQ_URL"):
        mq_secret = values.get("RABBITMQ_DEFAULT_PASS", values.get("RABBITMQ_PASSWORD"))
        if name in source and mq_secret:
            values[name] = f"amqp://{quote(mq_user, safe='')}:{quote(mq_secret, safe='')}@rabbitmq:5672/"
    return values


def lock_down(path: Path, *, directory: bool = False) -> None:
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)


def atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(name)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def metadata_folder(directory: Path) -> Path:
    folder = directory / META_DIR
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise DeployerError("Diretório interno de recuperação não pode ser link simbólico.")
    lock_down(folder, directory=True)
    return folder


def backup_files(directory: Path, reason: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"{stamp}-{secrets.token_hex(4)}"
    folder = metadata_folder(directory) / "backups" / backup_id
    folder.mkdir(parents=True, mode=0o700)
    lock_down(folder, directory=True)
    for filename in ("compose.yaml", ENV_NAME):
        path = directory / filename
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise DeployerError("Arquivo de implantação inválido; backup interrompido.")
            atomic_write(folder / filename, path.read_bytes())
    atomic_write(folder / "reason.txt", (reason + "\n").encode())
    return backup_id


def ensure_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise DeployerError("O destino não pode ser link simbólico.")
    for filename in ("compose.yaml", ENV_NAME):
        path = directory / filename
        if path.is_symlink() or path.exists() and not path.is_file():
            raise DeployerError(f"{filename} deve ser um arquivo comum.")


def generate(*, target: str, channel: str, output: Path, owner: str | None = None,
             tag: str | None = None, bind: str | None = None, port: int | None = None,
             admin_email: str, force: bool = False) -> dict:
    templates = template_path(target, channel)
    compose_template = templates / "compose.yaml"
    env_template = templates / ".env.example"
    try:
        compose = compose_template.read_bytes()
        template = env_template.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeployerError("Modelos Compose ou .env.example ausentes para o alvo.") from exc
    version = product_version()
    actual_owner = owner or defaults(target, channel)["owner"]
    pinned = image_tag(target, channel, actual_owner, tag)
    values = prepare_values(template, target=target, channel=channel,
                            owner=actual_owner, tag=pinned, bind=bind, port=port,
                            admin_email=admin_email)
    output = output.expanduser().resolve()
    ensure_directory(output)
    compose_path, env_path = output / "compose.yaml", output / ENV_NAME
    if compose_path.exists() and not force:
        raise DeployerError("compose.yaml já existe; use --force para atualizar o modelo sem trocar o .env.")
    old_compose = compose_path.read_bytes() if compose_path.exists() else None
    old_env = env_path.read_bytes() if env_path.exists() else None
    env_data = (old_env if old_env is not None else render_env(template, values).encode("utf-8"))
    validate_content(compose.decode("utf-8"), env_data.decode("utf-8"), expected_channel=channel)
    backup_id = backup_files(output, "generate") if old_compose is not None or old_env is not None else None
    try:
        atomic_write(compose_path, compose)
        if old_env is None:
            atomic_write(env_path, env_data)
    except Exception:
        if old_compose is None:
            compose_path.unlink(missing_ok=True)
        else:
            atomic_write(compose_path, old_compose)
        if old_env is None:
            env_path.unlink(missing_ok=True)
        raise
    persisted = parse_env(env_data.decode("utf-8"))
    return {"directory": str(output), "target": target, "channel": channel,
            "version": version, "tag": persisted["APP_TAG"],
            "owner": persisted["IMAGE_OWNER"], "web_port": int(persisted["WEB_PORT"]),
            "compose_path": str(compose_path), "env_path": str(env_path),
            "files_generated": [str(compose_path), str(env_path)],
            "env_preserved": old_env is not None, "backup_id": backup_id}


def validate_content(compose: str, env_text: str, *, expected_channel: str | None = None) -> dict:
    env = parse_env(env_text)
    required = ("COMPOSE_PROJECT_NAME", "IMAGE_OWNER", "APP_TAG", "WEB_BIND_ADDRESS", "WEB_PORT",
                "POSTGRES_PASSWORD", "REDIS_PASSWORD", "RABBITMQ_DEFAULT_PASS",
                "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_REGION", "S3_USE_SSL",
                "SECRET_KEY",
                "BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_PASSWORD")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise DeployerError("Parâmetros obrigatórios ausentes: " + ", ".join(missing))
    for key in required:
        if PLACEHOLDER.search(env[key]):
            raise DeployerError(f"{key} ainda contém um valor de exemplo.")
    if not OWNER.fullmatch(env["IMAGE_OWNER"]):
        raise DeployerError("IMAGE_OWNER inválido.")
    if env["WEB_BIND_ADDRESS"] not in ("127.0.0.1", "::1"):
        raise DeployerError("WEB_BIND_ADDRESS deve apontar apenas ao loopback.")
    try:
        port = int(env["WEB_PORT"])
    except ValueError as exc:
        raise DeployerError("WEB_PORT deve ser numérica.") from exc
    if not 40000 <= port <= 49999:
        raise DeployerError("WEB_PORT deve ficar entre 40000 e 49999.")
    tag = env["APP_TAG"]
    environment = env.get("APP_ENV")
    normalized_environment = "develop" if environment == "development" else environment
    if expected_channel is None:
        expected_channel = normalized_environment or ("production" if env["COMPOSE_PROJECT_NAME"].endswith("-production") else None)
    if expected_channel not in CHANNELS:
        raise DeployerError("APP_ENV precisa ser development ou production.")
    if normalized_environment and normalized_environment != expected_channel:
        raise DeployerError("APP_ENV não corresponde ao canal do modelo.")
    if expected_channel == "production" and not (SEMVER.fullmatch(tag) or SHA_TAG.fullmatch(tag)):
        raise DeployerError("Produção exige SemVer estável ou sha-<12 hex> fixo.")
    if not (SEMVER.fullmatch(tag) or SHA_TAG.fullmatch(tag) or tag == "develop"):
        raise DeployerError("APP_TAG deve ser develop, SemVer estável ou sha-<12 hex>.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", env["COMPOSE_PROJECT_NAME"]):
        raise DeployerError("COMPOSE_PROJECT_NAME inválido.")
    for key in set(SECRET_KEYS) & set(env):
        if len(env[key]) < 20:
            raise DeployerError(f"{key} precisa ter pelo menos 20 caracteres.")
    if env["S3_ENDPOINT"] != "objectstore:9000":
        raise DeployerError("S3_ENDPOINT deve apontar para objectstore:9000 nesta stack.")
    if env["S3_USE_SSL"].lower() not in ("true", "false"):
        raise DeployerError("S3_USE_SSL deve ser true ou false.")
    if len(env["S3_ACCESS_KEY"]) < 16:
        raise DeployerError("S3_ACCESS_KEY precisa ter pelo menos 16 caracteres.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", env["S3_BUCKET"]):
        raise DeployerError("S3_BUCKET inválido.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", env["BOOTSTRAP_ADMIN_EMAIL"]):
        raise DeployerError("BOOTSTRAP_ADMIN_EMAIL inválido.")
    if "services:" not in compose or any(f"  {name}:" not in compose for name in ("api", "web", "objectstore")):
        raise DeployerError("O Compose não define os serviços obrigatórios api, web e objectstore.")
    if "${APP_TAG" not in compose or "${IMAGE_OWNER" not in compose:
        raise DeployerError("O Compose não utiliza IMAGE_OWNER e APP_TAG do arquivo .env.")
    compose_required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", compose))
    compose_missing = sorted(name for name in compose_required if not env.get(name))
    if compose_missing:
        raise DeployerError("O Compose exige variáveis ausentes: " + ", ".join(compose_missing))
    # URLs are checked for consistency without printing their credential fields.
    for name, marker in (("DATABASE_URL", env["POSTGRES_PASSWORD"]),
                         ("REDIS_URL", env["REDIS_PASSWORD"]),
                         ("BROKER_URL", env["RABBITMQ_DEFAULT_PASS"])):
        if name in env and env[name] and marker not in env[name] and quote(marker, safe="") not in env[name]:
            raise DeployerError(f"{name} não corresponde ao segredo configurado.")
    return {"project": env["COMPOSE_PROJECT_NAME"], "owner": env["IMAGE_OWNER"],
            "tag": tag, "bind": env["WEB_BIND_ADDRESS"], "port": port}


def validate_directory(directory: Path) -> dict:
    directory = directory.expanduser().resolve()
    compose, env_file = directory / "compose.yaml", directory / ENV_NAME
    if not compose.is_file() or not env_file.is_file() or compose.is_symlink() or env_file.is_symlink():
        raise DeployerError("A pasta precisa conter compose.yaml e .env comuns.")
    return validate_content(compose.read_text(encoding="utf-8"), env_file.read_text(encoding="utf-8"))


def safe_process(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployerError("Docker Compose indisponível ou demorou além do limite.") from exc
    if result.returncode:
        # Docker can echo interpolated URLs and passwords in error output.
        stage = next((part for part in args if part in ("config", "pull", "up", "ps")), "operação")
        raise DeployerError(f"Docker Compose falhou na etapa {stage} (código {result.returncode}); veja os logs locais com cuidado.")
    return result


def compose_command(directory: Path, *operation: str) -> list[str]:
    return ["docker", "compose", "--env-file", ENV_NAME, "-f", "compose.yaml", *operation]


def state_path(directory: Path) -> Path:
    return metadata_folder(directory) / "state.json"


def read_state(directory: Path) -> dict:
    path = state_path(directory)
    if not path.exists():
        return {"last_deployed_tag": None, "previous_deployed_tag": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployerError("Histórico do deployer inválido; restaure o backup antes de operar.") from exc
    if not isinstance(data, dict):
        raise DeployerError("Histórico do deployer inválido.")
    return data


def record_deployment(directory: Path, tag: str) -> dict:
    data = read_state(directory)
    old = data.get("last_deployed_tag")
    if old != tag:
        data["previous_deployed_tag"] = old
    data["last_deployed_tag"] = tag
    data["deployed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    atomic_write(state_path(directory), (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    return data


def deploy(directory: Path, *, confirmed: bool) -> dict:
    if not confirmed:
        raise DeployerError("Implantação exige --yes e confirmação na interface gráfica.")
    directory = directory.expanduser().resolve()
    config = validate_directory(directory)
    safe_process(compose_command(directory, "config", "--quiet"), directory)
    safe_process(compose_command(directory, "pull"), directory)
    safe_process(compose_command(directory, "up", "-d", "--wait", "--wait-timeout", "180"), directory)
    state = record_deployment(directory, config["tag"])
    return {"project": config["project"], "tag": config["tag"], "deployed_at": state["deployed_at"]}


def rollback(directory: Path, *, tag: str | None, confirmed: bool) -> dict:
    if not confirmed:
        raise DeployerError("Rollback exige --yes e confirmação na interface gráfica.")
    directory = directory.expanduser().resolve()
    chosen = tag or read_state(directory).get("previous_deployed_tag")
    if not chosen or not (SEMVER.fullmatch(chosen) or SHA_TAG.fullmatch(chosen)):
        raise DeployerError("Informe --tag SemVer estável ou sha-<12 hex>, ou implante duas versões antes.")
    return switch_image_tag(directory, tag=chosen, reason="rollback")


def upgrade(directory: Path, *, tag: str | None, confirmed: bool) -> dict:
    if not confirmed:
        raise DeployerError("Atualização exige --yes e confirmação na interface gráfica.")
    current = validate_directory(directory)
    chosen = image_tag("docker", "production", current["owner"], tag)
    if not (SEMVER.fullmatch(chosen) or SHA_TAG.fullmatch(chosen)):
        raise DeployerError("Atualização exige SemVer estável ou sha-<12 hex>.")
    return switch_image_tag(directory.expanduser().resolve(), tag=chosen, reason="upgrade")


def switch_image_tag(directory: Path, *, tag: str, reason: str) -> dict:
    config = validate_directory(directory)
    chosen = tag
    if chosen == config["tag"]:
        raise DeployerError("A versão solicitada já está selecionada.")
    env_path = directory / ENV_NAME
    old = env_path.read_bytes()
    backup_id = backup_files(directory, reason)
    atomic_write(env_path, replace_env_values(old.decode("utf-8"), {"APP_TAG": chosen}).encode("utf-8"))
    try:
        result = deploy(directory, confirmed=True)
    except Exception:
        atomic_write(env_path, old)
        raise
    return {**result, "previous_tag": config["tag"], "backup_id": backup_id}


def recover(directory: Path, *, backup_id: str | None, confirmed: bool) -> dict:
    if not confirmed:
        raise DeployerError("Recuperação exige --yes e confirmação na interface gráfica.")
    directory = directory.expanduser().resolve()
    backups = metadata_folder(directory) / "backups"
    if not backups.is_dir():
        raise DeployerError("Não há backup de configuração disponível.")
    candidates = sorted(path for path in backups.iterdir() if path.is_dir() and not path.is_symlink())
    chosen = next((path for path in candidates if path.name == backup_id), None) if backup_id else (candidates[-1] if candidates else None)
    if chosen is None or not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", chosen.name):
        raise DeployerError("Backup solicitado não existe.")
    files = {name: (chosen / name).read_bytes() for name in ("compose.yaml", ENV_NAME) if (chosen / name).is_file()}
    if not files:
        raise DeployerError("Backup vazio.")
    if "compose.yaml" in files and ENV_NAME in files:
        validate_content(files["compose.yaml"].decode("utf-8"), files[ENV_NAME].decode("utf-8"))
    backup_files(directory, "before-recover")
    previous = {name: (directory / name).read_bytes() if (directory / name).is_file() else None for name in files}
    try:
        for name, contents in files.items():
            atomic_write(directory / name, contents)
    except Exception:
        for name, contents in previous.items():
            if contents is None:
                (directory / name).unlink(missing_ok=True)
            else:
                atomic_write(directory / name, contents)
        raise
    return {"restored": chosen.name, "directory": str(directory), "files": sorted(files)}


def status(directory: Path) -> dict:
    directory = directory.expanduser().resolve()
    config = validate_directory(directory)
    state = read_state(directory)
    result: dict = {**config, "state": state}
    try:
        checked = safe_process(compose_command(directory, "ps", "--format", "json"), directory)
        services = []
        body = checked.stdout.strip()
        if body:
            try:
                rows = json.loads(body)
                rows = rows if isinstance(rows, list) else [rows]
            except json.JSONDecodeError:
                rows = [json.loads(line) for line in body.splitlines() if line.strip()]
            services = [{"name": item.get("Service"), "state": item.get("State")}
                        for item in rows if isinstance(item, dict)]
        result["services"] = services
    except (DeployerError, json.JSONDecodeError):
        result["services"] = "indisponível (Docker Compose não acessível)"
    return result


def preview(*, target: str, channel: str, owner: str | None = None,
            tag: str | None = None, bind: str | None = None, port: int | None = None) -> dict:
    owner = owner or defaults(target, channel)["owner"]
    if not OWNER.fullmatch(owner):
        raise DeployerError("IMAGE_OWNER inválido.")
    if bind is not None and bind not in ("127.0.0.1", "::1"):
        raise DeployerError("WEB_BIND_ADDRESS deve manter o serviço somente no loopback.")
    if port is not None and not 40000 <= port <= 49999:
        raise DeployerError("WEB_PORT deve estar entre 40000 e 49999.")
    template = template_path(target, channel)
    try:
        env = (template / ".env.example").read_text(encoding="utf-8")
        compose = (template / "compose.yaml").read_text(encoding="utf-8")
    except OSError as exc:
        raise DeployerError("Modelo de deploy ausente.") from exc
    chosen = image_tag(target, channel, owner, tag)
    if channel == "production" and not (SEMVER.fullmatch(chosen) or SHA_TAG.fullmatch(chosen)):
        raise DeployerError("Produção exige SemVer estável ou sha-<12 hex>.")
    if channel == "develop" and chosen != "develop" and not (SEMVER.fullmatch(chosen) or SHA_TAG.fullmatch(chosen)):
        raise DeployerError("Tag develop, SemVer estável ou sha-<12 hex> inválida.")
    public = parse_env(env)
    service_block = compose.split("\nservices:\n", 1)[-1].split("\nnetworks:\n", 1)[0]
    return {"target": target, "channel": channel, "product_version": product_version(),
            "image_owner": owner, "image_tag": chosen,
            "web_bind": bind or public.get("WEB_BIND_ADDRESS", "127.0.0.1"),
            "web_port": port or public.get("WEB_PORT"),
            "services": sorted(set(re.findall(r"^  ([a-z0-9-]+):\s*$", service_block, re.MULTILINE))),
            "files": ["compose.yaml", ENV_NAME], "secret_names": sorted(set(public) & set(SECRET_KEYS))}


def export_zip(directory: Path, output: Path) -> dict:
    directory = directory.expanduser().resolve()
    config = validate_directory(directory)
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".zip":
        raise DeployerError("O arquivo exportado precisa terminar em .zip.")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".deployer-export-", suffix=".zip", dir=output.parent)
    os.close(fd)
    temp = Path(name)
    try:
        lock_down(temp)
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename in ("compose.yaml", ENV_NAME):
                info = zipfile.ZipInfo(filename)
                info.external_attr = (0o600 << 16) if filename == ENV_NAME else (0o644 << 16)
                archive.writestr(info, (directory / filename).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return {"archive": str(output), "tag": config["tag"], "contains_secrets": True}


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fiscal-deployer", description="Gerador e operador local de stacks Compose")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="Ler somente o arquivo VERSION do projeto")
    commands.add_parser("list", help="Listar alvos e canais")
    for name in ("defaults", "versions"):
        item = commands.add_parser(name, help="Defaults do modelo ou catálogo de releases")
        item.add_argument("--target", choices=TARGETS, default="docker")
        item.add_argument("--channel", choices=CHANNELS, default="production")
        if name == "versions":
            item.add_argument("--owner", help="Namespace GHCR opcional")
    for name in ("generate", "preview"):
        item = commands.add_parser(name)
        item.add_argument("--target", choices=TARGETS, required=True)
        item.add_argument("--channel", choices=CHANNELS, required=True)
        item.add_argument("--owner", help="Namespace GHCR opcional (padrão do modelo)")
        item.add_argument("--tag")
        item.add_argument("--bind")
        item.add_argument("--port", type=int)
        if name == "generate":
            item.add_argument("--output", required=True, type=Path)
            item.add_argument("--admin-email", required=True, help="E-mail do administrador inicial")
            item.add_argument("--force", action="store_true", help="Atualizar o Compose e preservar integralmente o .env")
    for name in ("validate", "status", "deploy", "upgrade", "rollback", "recover", "export"):
        item = commands.add_parser(name)
        item.add_argument("--directory", required=True, type=Path)
        if name in ("deploy", "upgrade", "rollback", "recover"):
            item.add_argument("--yes", action="store_true", help="Confirmar operação local")
        if name in ("upgrade", "rollback"):
            item.add_argument("--tag", help="Tag SemVer estável ou sha-<12 hex> já publicada")
        if name == "recover":
            item.add_argument("--backup-id")
        if name == "export":
            item.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = make_parser().parse_args(argv)
        if args.command == "version":
            output = {"product_version": product_version()}
        elif args.command == "list":
            output = {"targets": TARGETS, "channels": CHANNELS}
        elif args.command == "defaults":
            output = defaults(args.target, args.channel)
        elif args.command == "versions":
            output = catalog(args.target, args.channel, owner=args.owner)
        elif args.command == "generate":
            output = generate(target=args.target, channel=args.channel, output=args.output,
                              owner=args.owner, tag=args.tag, bind=args.bind, port=args.port,
                              admin_email=args.admin_email, force=args.force)
        elif args.command == "preview":
            output = preview(target=args.target, channel=args.channel, owner=args.owner,
                             tag=args.tag, bind=args.bind, port=args.port)
        elif args.command == "validate":
            output = validate_directory(args.directory)
        elif args.command == "status":
            output = status(args.directory)
        elif args.command == "deploy":
            output = deploy(args.directory, confirmed=args.yes)
        elif args.command == "rollback":
            output = rollback(args.directory, tag=args.tag, confirmed=args.yes)
        elif args.command == "upgrade":
            output = upgrade(args.directory, tag=args.tag, confirmed=args.yes)
        elif args.command == "recover":
            output = recover(args.directory, backup_id=args.backup_id, confirmed=args.yes)
        elif args.command == "export":
            output = export_zip(args.directory, args.output)
        else:
            raise DeployerError("Comando desconhecido.")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (DeployerError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
