"""Catálogo local de empresas e segredos isolados para a instalação self.

O catálogo contém metadados operacionais. Cada empresa mantém configuração,
token e fila SQLite próprios; o formato legado do coletor permanece intacto.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import ssl
import stat
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import collector


CNPJ = re.compile(r"[A-Z0-9]{12}[0-9]{2}\Z")
TOKEN_LIMIT = 4096
MAX_PEM_BYTES = 1024 * 1024
CERTIFICATE_GRACE_SECONDS = 24 * 60 * 60


def default_home() -> Path:
    override = os.environ.get("FISCAL_COLLECTOR_HOME")
    if override:
        return Path(override).expanduser().absolute()
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "FiscalXmlCollector"
    return Path("/var/lib/fiscal-xml-collector")


def require_home(home: Path) -> Path:
    home = Path(home).expanduser().absolute()
    if not home.is_absolute() or home.is_symlink():
        raise ValueError("Diretório de dados deve ser absoluto e não pode ser symlink")
    return home


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Diretório gerenciado não pode ser symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("Diretório gerenciado inválido")
    if os.name != "nt":
        path.chmod(0o700)


def atomic_private_write(path: Path, contents: bytes) -> None:
    """Grava um arquivo privado sem truncar a configuração ou segredo anterior."""
    if path.is_symlink():
        raise ValueError("Arquivo gerenciado não pode ser symlink")
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _certificate_bytes(filename: str, *, private_key: bool) -> bytes:
    path = collector.require_absolute_path(filename, "certificado/chave PEM")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or not 0 < info.st_size <= MAX_PEM_BYTES:
        raise ValueError("Certificado/chave PEM deve ser arquivo regular dentro do limite")
    if private_key and os.name != "nt" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("Chave PEM de origem deve ter permissão 0600")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise ValueError("Certificado/chave PEM foi alterado durante a leitura")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_PEM_BYTES + 1)
        if len(data) != opened.st_size:
            raise ValueError("Certificado/chave PEM foi alterado durante a leitura")
        return data
    finally:
        os.close(descriptor)


def _store_certificate_pair(directory: Path, sefaz_nfe: dict[str, Any]) -> dict[str, Any]:
    cert = _certificate_bytes(sefaz_nfe["cert_pem"], private_key=False)
    key = _certificate_bytes(sefaz_nfe["key_pem"], private_key=True)
    # Valida o par localmente antes de substituir os caminhos da configuração.
    try:
        ssl.create_default_context().load_cert_chain(sefaz_nfe["cert_pem"], sefaz_nfe["key_pem"])
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("Certificado/chave PEM incompatíveis") from exc
    cert_source = Path(sefaz_nfe["cert_pem"])
    key_source = Path(sefaz_nfe["key_pem"])
    if (cert_source.name == "cert.pem" and key_source.name == "key.pem"
            and cert_source.parent == key_source.parent
            and cert_source.resolve().is_relative_to((directory / "certificates").resolve())
            and not cert_source.parent.is_symlink()):
        return sefaz_nfe
    # Nome novo mantém o par anterior íntegro caso a gravação seja interrompida.
    credentials = directory / "certificates" / uuid.uuid4().hex
    private_dir(credentials)
    atomic_private_write(credentials / "cert.pem", cert)
    atomic_private_write(credentials / "key.pem", key)
    return {**sefaz_nfe, "cert_pem": str(credentials / "cert.pem"),
            "key_pem": str(credentials / "key.pem")}


def profile_paths(home: Path, company_id: str) -> dict[str, Path]:
    home = require_home(home)
    company_id = str(uuid.UUID(company_id))
    directory = home / "companies" / company_id
    return {"directory": directory, "config": directory / "config.json",
            "token": directory / "token", "state": directory / "state"}


def connect(home: Path) -> sqlite3.Connection:
    home = require_home(home)
    private_dir(home)
    path = home / "catalog.sqlite3"
    if path.is_symlink():
        raise ValueError("Banco do catálogo não pode ser symlink")
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
              company_id TEXT PRIMARY KEY,
              cnpj TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
        """)
        db.commit()
        if os.name != "nt":
            path.chmod(0o600)
        return db
    except BaseException:
        db.close()
        raise


def list_profiles(home: Path) -> list[dict[str, Any]]:
    with closing(connect(home)) as db:
        return [{"company_id": row["company_id"], "cnpj": row["cnpj"],
                 "name": row["name"], "enabled": bool(row["enabled"]),
                 "updated_at": row["updated_at"]}
                for row in db.execute("SELECT company_id,cnpj,name,enabled,updated_at FROM profiles ORDER BY name,company_id")]


def load_config(home: Path, company_id: str) -> dict[str, Any]:
    paths = profile_paths(home, company_id)
    if (paths["directory"].is_symlink() or paths["state"].is_symlink()
            or paths["token"].is_symlink()):
        raise ValueError("Perfil gerenciado não pode conter symlink")
    config_file = paths["config"]
    info = config_file.lstat()
    if not stat.S_ISREG(info.st_mode) or config_file.is_symlink():
        raise ValueError("Configuração de empresa deve ser arquivo regular")
    config = collector.load_config(config_file)
    if (config["company_id"] != str(uuid.UUID(company_id))
            or config["state_dir"] != str(paths["state"])
            or config["token_file"] != str(paths["token"])):
        raise ValueError("Perfil possui caminhos ou identificador inconsistentes")
    return config


def _certificates_in_config(path: Path, base: Path) -> set[Path] | None:
    if path.is_symlink():
        return None  # Falha fechada: não remover credenciais sem referências confiáveis.
    if not path.exists():
        return set()
    if not path.is_file():
        return None
    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
        source = contents.get("sefaz_nfe")
        if not source:
            return set()
        cert = Path(source["cert_pem"])
        key = Path(source["key_pem"])
        if cert.parent != key.parent:
            return None
        parent = cert.parent.resolve()
        if not parent.is_relative_to(base.resolve()):
            return set()  # Credencial legada externa não está sob nossa gestão.
        return {parent}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def prune_old_certificates(home: Path, company_id: str, *,
                           now: float | None = None) -> int:
    """Remove pares órfãos após 24 h; preserva atual e configuração anterior.

    Pode ser chamado repetidamente. Somente apaga diretórios gerados pelo
    próprio programa e que contenham exclusivamente cert.pem/key.pem.
    """
    paths = profile_paths(home, company_id)
    base = paths["directory"] / "certificates"
    if not base.is_dir() or base.is_symlink():
        return 0
    current = _certificates_in_config(paths["config"], base)
    previous = _certificates_in_config(paths["directory"] / "config.previous.json", base)
    if current is None or previous is None:
        return 0
    keep = current | previous
    cutoff = (time.time() if now is None else now) - CERTIFICATE_GRACE_SECONDS
    removed = 0
    for directory in base.iterdir():
        if not re.fullmatch(r"[0-9a-f]{32}", directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        if directory.resolve() in keep or directory.stat().st_mtime > cutoff:
            continue
        children = list(directory.iterdir())
        if any(child.name not in {"cert.pem", "key.pem"} or child.is_symlink() or not child.is_file()
               for child in children):
            continue
        try:
            for child in children:
                child.unlink()
            directory.rmdir()
            removed += 1
        except OSError:
            continue  # Interrupção: uma nova execução termina o diretório órfão.
    return removed


def save_profile(home: Path, *, company_id: str, cnpj: str, name: str,
                 server_url: str, roots: list[str], token: str | None,
                 enabled: bool = True, recursive: bool = True, signed_only: bool = True,
                 poll_seconds: int | None = None, sefaz_nfe: dict[str, Any] | None = None,
                 stable_seconds: int | None = None, timeout_seconds: int | None = None,
                 max_file_bytes: int | None = None, batch_limit: int | None = None,
                 retry_min_seconds: int | None = None,
                 retry_max_seconds: int | None = None) -> dict[str, Any]:
    """Atualiza um perfil sem inserir credencial no JSON ou no SQLite."""
    paths = profile_paths(home, company_id)
    company_id = str(uuid.UUID(company_id))
    cnpj = re.sub(r"[^A-Z0-9]", "", cnpj.strip().upper())
    name = name.strip()
    if not CNPJ.fullmatch(cnpj):
        raise ValueError("CNPJ deve ter 12 posições alfanuméricas e dois dígitos verificadores")
    if not 2 <= len(name) <= 180:
        raise ValueError("Nome da empresa deve ter entre 2 e 180 caracteres")
    if not isinstance(enabled, bool):
        raise ValueError("enabled deve ser booleano")
    if token is not None:
        if not isinstance(token, str) or not token.strip() or len(token) > TOKEN_LIMIT or "\n" in token or "\r" in token:
            raise ValueError("Token inválido")
        token = token.strip()
    elif not paths["token"].is_file() or paths["token"].is_symlink():
        raise ValueError("Informe token da empresa para criar o perfil")
    if sefaz_nfe and re.sub(r"[^A-Z0-9]", "", str(sefaz_nfe.get("cnpj", "")).upper()) != cnpj:
        raise ValueError("CNPJ da SEFAZ deve corresponder ao perfil da empresa")
    previous_config = None
    if paths["config"].exists():
        previous_config = load_config(home, company_id)
    raw = {**(previous_config or {}), "server_url": server_url, "company_id": company_id,
           "roots": roots, "state_dir": str(paths["state"]),
           "token_env": "FISCAL_COLLECTOR_PROFILE_" + uuid.UUID(company_id).hex.upper(),
           "token_file": str(paths["token"]), "recursive": recursive,
           "signed_only": signed_only,
           "sefaz_nfe": ({**((previous_config or {}).get("sefaz_nfe") or {}),
                           **sefaz_nfe, "cnpj": cnpj} if sefaz_nfe else None)}
    for field, value in (("poll_seconds", poll_seconds), ("stable_seconds", stable_seconds),
                         ("timeout_seconds", timeout_seconds), ("max_file_bytes", max_file_bytes),
                         ("batch_limit", batch_limit), ("retry_min_seconds", retry_min_seconds),
                         ("retry_max_seconds", retry_max_seconds)):
        if value is not None:
            raw[field] = value
    config = collector.validate_config(raw)
    private_dir(require_home(home))
    private_dir(require_home(home) / "companies")
    private_dir(paths["directory"])
    private_dir(paths["state"])
    # A constraint UNIQUE(cnpj) impede dupla coleta NSU por perfis diferentes.
    with closing(connect(home)) as db:
        existing = db.execute("SELECT 1 FROM profiles WHERE cnpj=? AND company_id<>?", (cnpj, company_id)).fetchone()
        if existing:
            raise ValueError("CNPJ já cadastrado em outro perfil")
        if sefaz_nfe:
            config["sefaz_nfe"] = _store_certificate_pair(paths["directory"], config["sefaz_nfe"])
        if previous_config:
            atomic_private_write(paths["directory"] / "config.previous.json",
                                 (json.dumps(previous_config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if token is not None:
            atomic_private_write(paths["token"], (token + "\n").encode("utf-8"))
        atomic_private_write(paths["config"],
                             (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        now = time.time()
        db.execute("""
            INSERT INTO profiles(company_id,cnpj,name,enabled,created_at,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(company_id) DO UPDATE SET cnpj=excluded.cnpj,name=excluded.name,
              enabled=excluded.enabled,updated_at=excluded.updated_at
        """, (company_id, cnpj, name, int(enabled), now, now))
        db.commit()
    try:
        prune_old_certificates(home, company_id)
    except OSError:
        pass  # Um problema de limpeza não reverte configuração já confirmada.
    return {"company_id": company_id, "cnpj": cnpj, "name": name, "enabled": enabled}


def set_enabled(home: Path, company_id: str, enabled: bool) -> None:
    if not isinstance(enabled, bool):
        raise ValueError("enabled deve ser booleano")
    company_id = str(uuid.UUID(company_id))
    with closing(connect(home)) as db:
        cursor = db.execute("UPDATE profiles SET enabled=?,updated_at=? WHERE company_id=?",
                            (int(enabled), time.time(), company_id))
        if cursor.rowcount != 1:
            raise ValueError("Empresa não encontrada")
        db.commit()
