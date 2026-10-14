#!/usr/bin/env python3
"""Coletor local de XML fiscal: leitura imutável, fila durável e envio HTTPS."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import signal
import sqlite3
import ssl
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from sefaz_nfe import NFeDistributionClient, NFeDistributionError, NFeTransportError, PollState


LOGGER = logging.getLogger("fiscal_xml_collector")
SIGNATURE_TAG = "{http://www.w3.org/2000/09/xmldsig#}Signature"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # mesmo teto do endpoint /documents/ingest
DEFAULT_TOKEN_ENV = "FISCAL_COLLECTOR_TOKEN"
REQUEST_PATH = "/api/v1/documents/ingest"
SEFAZ_BATCH_RESERVE_BYTES = 128 * 1024 * 1024
DEFAULT_SEFAZ_SPOOL_BYTES = 2 * 1024 * 1024 * 1024


class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, retry_after: int = 0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}")


def version() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def require_absolute_path(value: str, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} deve ser um caminho absoluto")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} deve ser absoluto: {value}")
    return path


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("A configuração deve ser um objeto JSON")
    if "token" in raw or "password" in raw:
        raise ValueError("Não inclua credenciais em JSON; use token_env ou token_file")
    server_url = raw.get("server_url", "")
    if not isinstance(server_url, str):
        raise ValueError("server_url deve ser texto HTTPS")
    parsed = urllib.parse.urlsplit(server_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("server_url deve ser HTTPS e não pode conter usuário ou senha")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("server_url deve conter somente a origem HTTPS, sem caminho ou query")
    try:
        company_id = str(uuid.UUID(str(raw["company_id"])))
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("company_id deve ser um UUID válido") from exc
    roots = raw.get("roots")
    if (not isinstance(roots, list) or not all(isinstance(x, str) for x in roots)
            or (not roots and not raw.get("sefaz_nfe"))):
        raise ValueError("roots deve conter diretórios absolutos, salvo coleta SEFAZ exclusiva")
    roots = [str(require_absolute_path(x, "root")) for x in roots]
    state_dir = require_absolute_path(raw.get("state_dir", ""), "state_dir")
    token_env = raw.get("token_env", DEFAULT_TOKEN_ENV)
    token_file = raw.get("token_file")
    if not isinstance(token_env, str) or not token_env.isidentifier():
        raise ValueError("token_env deve ser um nome válido de variável de ambiente")
    if token_file is not None:
        token_file = str(require_absolute_path(token_file, "token_file"))
    numeric = {
        "stable_seconds": (10, 0, 86400),
        "poll_seconds": (30, 1, 86400),
        "timeout_seconds": (30, 1, 300),
        "max_file_bytes": (DEFAULT_MAX_BYTES, 1024, DEFAULT_MAX_BYTES),
        "batch_limit": (100, 1, 10000),
        "retry_min_seconds": (10, 1, 3600),
        "retry_max_seconds": (3600, 1, 86400),
    }
    settings: dict[str, Any] = {}
    for key, (default, minimum, maximum) in numeric.items():
        val = raw.get(key, default)
        if isinstance(val, bool) or not isinstance(val, int) or not minimum <= val <= maximum:
            raise ValueError(f"{key} deve ser inteiro entre {minimum} e {maximum}")
        settings[key] = val
    if settings["retry_max_seconds"] < settings["retry_min_seconds"]:
        raise ValueError("retry_max_seconds deve ser >= retry_min_seconds")
    recursive = raw.get("recursive", True)
    signed_only = raw.get("signed_only", True)
    if not isinstance(recursive, bool) or not isinstance(signed_only, bool):
        raise ValueError("recursive e signed_only devem ser booleanos")
    sefaz = raw.get("sefaz_nfe")
    if sefaz is not None:
        if not isinstance(sefaz, dict):
            raise ValueError("sefaz_nfe deve ser um objeto de configuração")
        cnpj = sefaz.get("cnpj", "")
        environment = sefaz.get("environment", "")
        if not isinstance(cnpj, str) or not re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", cnpj):
            raise ValueError("sefaz_nfe.cnpj inválido")
        if environment not in ("production", "homologation"):
            raise ValueError("sefaz_nfe.environment inválido")
        cert_pem = str(require_absolute_path(sefaz.get("cert_pem", ""), "sefaz_nfe.cert_pem"))
        key_pem = str(require_absolute_path(sefaz.get("key_pem", ""), "sefaz_nfe.key_pem"))
        spool_limit = sefaz.get("max_spool_bytes", DEFAULT_SEFAZ_SPOOL_BYTES)
        if isinstance(spool_limit, bool) or not isinstance(spool_limit, int) or not (
            256 * 1024 * 1024 <= spool_limit <= 100 * 1024 * 1024 * 1024
        ):
            raise ValueError("sefaz_nfe.max_spool_bytes deve estar entre 256 MiB e 100 GiB")
        sefaz = {"cnpj": cnpj, "environment": environment, "cert_pem": cert_pem,
                 "key_pem": key_pem, "max_spool_bytes": spool_limit}
    return {
        "server_url": f"https://{parsed.netloc.rstrip('/')}",
        "company_id": company_id,
        "roots": roots,
        "state_dir": str(state_dir),
        "token_env": token_env,
        "token_file": token_file,
        "recursive": recursive,
        "signed_only": signed_only,
        "sefaz_nfe": sefaz,
        **settings,
    }


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Configuração não encontrada: {path}")
    return validate_config(json.loads(path.read_text(encoding="utf-8")))


def read_token(config: dict[str, Any]) -> str:
    env_token = os.environ.get(config["token_env"], "").strip()
    if env_token:
        if "\n" in env_token or "\r" in env_token:
            raise ValueError("Variável de token contém caracteres inválidos")
        return env_token
    filename = os.environ.get("FISCAL_COLLECTOR_TOKEN_FILE") or config["token_file"]
    if not filename:
        raise ValueError("Token ausente: defina a variável ou um arquivo de segredo")
    path = Path(filename)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError("O arquivo do token deve ser regular e não pode ser um symlink")
    if os.name != "nt" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("O arquivo do token deve ter permissões 0600 ou mais restritas")
    token = path.read_text(encoding="utf-8").strip()
    if not token or "\n" in token or "\r" in token:
        raise ValueError("Arquivo de token vazio ou com conteúdo inválido")
    return token


def iter_xml_files(roots: list[str], recursive: bool) -> Iterator[Path]:
    for root in roots:
        if not Path(root).is_dir():
            LOGGER.error("Diretório XML indisponível: %s", root)
            continue
        if recursive:
            def onerror(error: OSError) -> None:
                LOGGER.error("Não foi possível ler diretório XML: %s (%s)", error.filename, type(error).__name__)

            for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
                dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
                for filename in files:
                    if filename.lower().endswith(".xml"):
                        yield Path(directory) / filename
        else:
            with os.scandir(root) as entries:
                for item in entries:
                    if item.name.lower().endswith(".xml") and item.is_file(follow_symlinks=False):
                        yield Path(item.path)


def sefaz_root(config: dict[str, Any]) -> Path:
    sefaz = config["sefaz_nfe"]
    return Path(config["state_dir"]) / "sefaz_nfe" / sefaz["cnpj"] / sefaz["environment"]


def has_signature(payload: bytes) -> bool:
    parser = expat.ParserCreate()

    def reject_markup(*_: Any) -> None:
        raise ValueError("DOCTYPE e entidades externas não são aceitos em XML fiscal")

    parser.StartDoctypeDeclHandler = reject_markup
    parser.EntityDeclHandler = reject_markup
    parser.ExternalEntityRefHandler = reject_markup
    parser.Parse(payload, True)
    root = ET.fromstring(payload)
    return any(node.tag == SIGNATURE_TAG for node in root.iter())


def stable_payload(path: Path, config: dict[str, Any], now: float) -> tuple[bytes, os.stat_result] | None:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
        return None
    if before.st_size > config["max_file_bytes"] or now - before.st_mtime < config["stable_seconds"]:
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        ):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(config["max_file_bytes"] + 1)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or len(payload) > config["max_file_bytes"]:
            return None
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return payload, after
    finally:
        os.close(descriptor)


@contextmanager
def single_instance(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_dir / "collector.lock"
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError("Outra instância do coletor já está em execução") from exc
    try:
        yield
    finally:
        handle.close()


def open_state(config: dict[str, Any]) -> sqlite3.Connection:
    state_dir = Path(config["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and state_dir.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("state_dir deve ter permissões 0700 ou mais restritas")
    db_path = state_dir / "state.sqlite3"
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (
              path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
              sha256 TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              next_retry_at REAL NOT NULL DEFAULT 0, last_error TEXT,
              discovered_at REAL NOT NULL, updated_at REAL NOT NULL, uploaded_at REAL
            );
            CREATE INDEX IF NOT EXISTS files_ready ON files(state, next_retry_at);
            CREATE TABLE IF NOT EXISTS digests (sha256 TEXT PRIMARY KEY, accepted_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sefaz_state (
              cnpj TEXT NOT NULL, environment TEXT NOT NULL, state_json TEXT NOT NULL,
              last_cstat TEXT NOT NULL, last_status TEXT NOT NULL,
              response_sha256 TEXT NOT NULL, updated_at REAL NOT NULL,
              retry_count INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
              last_error TEXT,
              PRIMARY KEY(cnpj,environment)
            );
            CREATE TABLE IF NOT EXISTS sefaz_documents (
              cnpj TEXT NOT NULL, environment TEXT NOT NULL, nsu TEXT NOT NULL,
              sha256 TEXT NOT NULL, schema_name TEXT NOT NULL, classification TEXT NOT NULL,
              xml_path TEXT NOT NULL, discovered_at REAL NOT NULL,
              PRIMARY KEY(cnpj,environment,nsu,sha256)
            );
        """)
        stored = conn.execute("SELECT value FROM metadata WHERE key='company_id'").fetchone()
        if stored and stored["value"] != config["company_id"]:
            raise ValueError("state_dir já pertence a outro company_id; use diretório separado")
        conn.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES ('company_id',?)", (config["company_id"],))
        conn.commit()
        if os.name != "nt":
            os.chmod(db_path, 0o600)
        return conn
    except BaseException:
        conn.close()
        raise


def discover(config: dict[str, Any], conn: sqlite3.Connection, now: float | None = None) -> dict[str, int]:
    now = time.time() if now is None else now
    counts = {"examined": 0, "queued": 0, "ignored": 0, "already_sent": 0}
    roots = list(config["roots"])
    if config["sefaz_nfe"]:
        ready = sefaz_root(config) / "ready"
        if ready.is_dir():
            roots.append(str(ready))
    for path in iter_xml_files(roots, config["recursive"]):
        counts["examined"] += 1
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size < 1:
                continue
            old = conn.execute("SELECT size,mtime_ns,state FROM files WHERE path=?", (str(path),)).fetchone()
            if old and old["size"] == info.st_size and old["mtime_ns"] == info.st_mtime_ns:
                continue
            if info.st_size > config["max_file_bytes"]:
                conn.execute("""
                    INSERT INTO files(path,size,mtime_ns,sha256,state,attempts,next_retry_at,last_error,discovered_at,updated_at)
                    VALUES (?,?,?,'','ignored',0,0,?,?,?)
                    ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,
                    sha256='',state='ignored',attempts=0,next_retry_at=0,
                    last_error=excluded.last_error,updated_at=excluded.updated_at,uploaded_at=NULL
                """, (str(path), info.st_size, info.st_mtime_ns,
                      "Arquivo excede limite de envio", now, now))
                counts["ignored"] += 1
                continue
            data = stable_payload(path, config, now)
            if data is None:
                continue
            payload, info = data
            digest = hashlib.sha256(payload).hexdigest()
            try:
                signed = has_signature(payload)
            except (ET.ParseError, expat.ExpatError, ValueError) as exc:
                reason = f"XML inválido: {type(exc).__name__}"
                signed = False
            else:
                reason = "Sem elemento XMLDSig Signature" if config["signed_only"] and not signed else None
            accepted = conn.execute("SELECT 1 FROM digests WHERE sha256=?", (digest,)).fetchone()
            if reason or (config["signed_only"] and not signed):
                state = "ignored"
                counts["ignored"] += 1
            elif accepted:
                state = "sent"
                counts["already_sent"] += 1
            else:
                state = "pending"
                counts["queued"] += 1
            conn.execute("""
                INSERT INTO files(path,size,mtime_ns,sha256,state,attempts,next_retry_at,last_error,discovered_at,updated_at,uploaded_at)
                VALUES (?,?,?,?,?,0,0,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                size=excluded.size,mtime_ns=excluded.mtime_ns,sha256=excluded.sha256,state=excluded.state,
                attempts=0,next_retry_at=0,last_error=excluded.last_error,updated_at=excluded.updated_at,
                uploaded_at=excluded.uploaded_at
            """, (str(path), info.st_size, info.st_mtime_ns, digest, state, reason,
                  now, now, now if state == "sent" else None))
        except (OSError, PermissionError) as exc:
            LOGGER.warning("Arquivo indisponível em %s: %s", path, type(exc).__name__)
    conn.commit()
    return counts


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return  # os.replace e fsync do arquivo; o Windows não oferece fsync de diretório via os.open.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError("Conflito no spool; documento existente difere do retorno recebido")
        _sync_directory(path.parent)
        return
    fd, temp_path = tempfile.mkstemp(prefix=".spool-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def spool_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for directory, _, files in os.walk(root, followlinks=False):
        for filename in files:
            path = Path(directory) / filename
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
            except OSError:
                continue
    return total


def _sefaz_state(config: dict[str, Any], conn: sqlite3.Connection) -> tuple[PollState, sqlite3.Row | None]:
    setting = config["sefaz_nfe"]
    row = conn.execute("SELECT * FROM sefaz_state WHERE cnpj=? AND environment=?",
                       (setting["cnpj"], setting["environment"])).fetchone()
    if row:
        state = PollState.from_mapping(json.loads(row["state_json"]))
        if (state.cnpj, state.environment) != (setting["cnpj"], setting["environment"]):
            raise ValueError("Checkpoint SEFAZ pertence a outro CNPJ/ambiente")
        return state, row
    return PollState(setting["cnpj"], setting["environment"]), None


def _record_sefaz_failure(config: dict[str, Any], conn: sqlite3.Connection,
                          state: PollState, old: sqlite3.Row | None, detail: str) -> None:
    count = (old["retry_count"] if old else 0) + 1
    delay = retry_delay(config, count)
    with conn:
        conn.execute("""INSERT INTO sefaz_state(cnpj,environment,state_json,last_cstat,last_status,
            response_sha256,updated_at,retry_count,retry_at,last_error) VALUES (?,?,?,'','error','',?,?,?,?)
            ON CONFLICT(cnpj,environment) DO UPDATE SET retry_count=excluded.retry_count,
            retry_at=excluded.retry_at,last_error=excluded.last_error,updated_at=excluded.updated_at""",
            (state.cnpj, state.environment, json.dumps(state.as_dict()), time.time(), count,
             time.time() + delay, detail))


def poll_sefaz(config: dict[str, Any], conn: sqlite3.Connection,
               client_factory: Callable[..., NFeDistributionClient] = NFeDistributionClient) -> dict[str, Any]:
    setting = config["sefaz_nfe"]
    if not setting:
        return {"status": "disabled"}
    state, old = _sefaz_state(config, conn)
    now = datetime.now(timezone.utc)
    if state.next_poll_at and now < state.next_poll_at:
        return {"status": "deferred", "until": state.next_poll_at.isoformat(), "last_nsu": state.last_nsu}
    if old and old["retry_at"] > time.time():
        return {"status": "retry_deferred", "until_utc_epoch": old["retry_at"], "last_nsu": state.last_nsu}
    root = sefaz_root(config)
    used = spool_bytes(root)
    if used + SEFAZ_BATCH_RESERVE_BYTES > setting["max_spool_bytes"]:
        return {"status": "storage_full", "spool_bytes": used, "max_spool_bytes": setting["max_spool_bytes"]}
    if shutil.disk_usage(config["state_dir"]).free < SEFAZ_BATCH_RESERVE_BYTES:
        return {"status": "disk_low", "spool_bytes": used}
    try:
        result = client_factory(setting["cert_pem"], setting["key_pem"]).fetch(state, now=now)
        raw_hash = hashlib.sha256(result.raw_response).hexdigest()
        prefix = f"{state.last_nsu}-{raw_hash}"
        durable_write(root / "responses" / f"{prefix}.soap.xml", result.raw_response)
        manifest: dict[str, Any] = {
            "before": state.as_dict(), "after": result.next_state.as_dict(),
            "cstat": result.cstat, "status": result.status,
            "raw_response_sha256": raw_hash, "max_nsu": result.max_nsu,
            "documents": [], "stored_at": now.isoformat(),
        }
        items: list[tuple[str, str, str, str, str]] = []
        for doc in result.documents:
            if doc.schema_matches_root is True and doc.declared_schema.startswith(("resNFe_", "resEvento_")):
                item_status = "summary_unprocessed"
            elif (doc.schema_matches_root is True and doc.signature_element_present
                  and doc.declared_schema.startswith(("procNFe_", "procEventoNFe_"))):
                item_status = "ready"
            elif doc.schema_matches_root is False or doc.schema_matches_root is None:
                item_status = "schema_quarantine"
            else:
                item_status = "unsigned_unprocessed"
            stem = f"{doc.nsu}-{doc.sha256}"
            xml_path = root / item_status / f"{stem}.xml"
            durable_write(root / "compressed" / f"{stem}.gz", doc.compressed_bytes)
            durable_write(xml_path, doc.xml_bytes)
            manifest["documents"].append({"nsu": doc.nsu, "schema": doc.declared_schema,
                "sha256": doc.sha256, "status": item_status, "xml_path": str(xml_path),
                "signature_element_present": doc.signature_element_present,
                "schema_matches_root": doc.schema_matches_root})
            items.append((doc.nsu, doc.sha256, doc.declared_schema, item_status, str(xml_path)))
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        durable_write(root / "responses" / f"{prefix}-{manifest_hash}.manifest.json", manifest_bytes)
        used_after = spool_bytes(root)
        with conn:
            for nsu, digest, schema, item_status, path in items:
                conn.execute("""INSERT OR IGNORE INTO sefaz_documents
                    (cnpj,environment,nsu,sha256,schema_name,classification,xml_path,discovered_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (state.cnpj, state.environment, nsu, digest, schema, item_status, path, time.time()))
            conn.execute("""INSERT INTO sefaz_state(cnpj,environment,state_json,last_cstat,last_status,
                response_sha256,updated_at,retry_count,retry_at,last_error)
                VALUES (?,?,?,?,?,?,?,0,0,NULL)
                ON CONFLICT(cnpj,environment) DO UPDATE SET state_json=excluded.state_json,
                last_cstat=excluded.last_cstat,last_status=excluded.last_status,
                response_sha256=excluded.response_sha256,updated_at=excluded.updated_at,
                retry_count=0,retry_at=0,last_error=NULL""",
                (state.cnpj, state.environment, json.dumps(result.next_state.as_dict()),
                 result.cstat, result.status, raw_hash, time.time()))
        return {"status": result.status, "cstat": result.cstat, "documents": len(items),
                "last_nsu": result.next_state.last_nsu, "spool_bytes": used_after}
    except (NFeDistributionError, NFeTransportError, OSError, ValueError, sqlite3.Error) as exc:
        raw_response = getattr(exc, "raw_response", None)
        if isinstance(raw_response, bytes):
            try:
                digest = hashlib.sha256(raw_response).hexdigest()
                durable_write(root / "quarantine" / f"invalid-response-{digest}.soap.xml", raw_response)
            except OSError:
                pass
        # O erro da biblioteca pode conter host/caminho, nunca texto de resposta nem credenciais.
        detail = type(exc).__name__
        _record_sefaz_failure(config, conn, state, old, detail)
        LOGGER.warning("Consulta SEFAZ NF-e falhou: %s", detail)
        return {"status": "error", "error": detail, "last_nsu": state.last_nsu}


def send_https(config: dict[str, Any], token: str, payload: bytes, digest: str) -> bool:
    query = urllib.parse.urlencode({"company_id": config["company_id"]})
    url = config["server_url"] + REQUEST_PATH + "?" + query
    request = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/xml",
        "X-Content-SHA256": digest,
        "X-Idempotency-Key": digest,
        "Accept": "application/json",
    })

    class RejectRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), RejectRedirects())
    try:
        with opener.open(request, timeout=config["timeout_seconds"]) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"HTTP {response.status}")
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise RuntimeError("Resposta muito grande")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise RuntimeError("Resposta JSON inválida")
            reported_hash = result.get("sha256")
            if not isinstance(reported_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", reported_hash):
                raise RuntimeError("Confirmação sem hash válido")
            if reported_hash.lower() != digest:
                raise RuntimeError("Hash da confirmação difere do XML transmitido")
            if not isinstance(result.get("duplicate"), bool):
                raise RuntimeError("Confirmação sem indicador de duplicidade")
            return result["duplicate"]
    except urllib.error.HTTPError as exc:
        retry_header = exc.headers.get("Retry-After", "") if exc.headers else ""
        retry_after = min(int(retry_header), 86400) if retry_header.isdecimal() else 0
        raise HTTPStatusError(exc.code, retry_after) from exc


def retry_delay(config: dict[str, Any], attempts: int) -> float:
    base = min(config["retry_max_seconds"], config["retry_min_seconds"] * 2 ** min(attempts - 1, 20))
    return min(config["retry_max_seconds"], base * random.uniform(0.8, 1.2))


def dispatch(config: dict[str, Any], conn: sqlite3.Connection, token: str,
             sender: Callable[[dict[str, Any], str, bytes, str], bool] = send_https,
             now: float | None = None) -> dict[str, int]:
    now = time.time() if now is None else now
    counts = {"sent": 0, "duplicate": 0, "failed": 0, "changed": 0}
    rows = conn.execute("""
        SELECT path,size,mtime_ns,sha256,attempts FROM files
        WHERE state='pending' AND next_retry_at<=? ORDER BY discovered_at,path LIMIT ?
    """, (now, config["batch_limit"])).fetchall()
    for row in rows:
        try:
            data = stable_payload(Path(row["path"]), config, time.time())
            if data is None:
                raise RuntimeError("Arquivo indisponível ou ainda em gravação")
            payload, info = data
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row["sha256"] or info.st_size != row["size"] or info.st_mtime_ns != row["mtime_ns"]:
                conn.execute("DELETE FROM files WHERE path=?", (row["path"],))
                counts["changed"] += 1
                continue
            if conn.execute("SELECT 1 FROM digests WHERE sha256=?", (digest,)).fetchone():
                duplicate = True
            else:
                duplicate = sender(config, token, payload, digest)
                conn.execute("INSERT OR IGNORE INTO digests(sha256,accepted_at) VALUES (?,?)", (digest, time.time()))
            conn.execute("UPDATE files SET state='sent',last_error=NULL,uploaded_at=?,updated_at=? WHERE path=?",
                         (time.time(), time.time(), row["path"]))
            counts["duplicate" if duplicate else "sent"] += 1
        except Exception as exc:
            attempts = row["attempts"] + 1
            # Never persist request/response bodies, URLs or tokens in local status/logs.
            message = str(exc)
            detail = message if re.fullmatch(r"HTTP [1-5][0-9]{2}", message) else type(exc).__name__
            terminal = isinstance(exc, HTTPStatusError) and exc.status in (400, 404, 413, 415, 422)
            delay = retry_delay(config, attempts)
            if isinstance(exc, HTTPStatusError):
                delay = max(delay, exc.retry_after)
                if exc.status in (401, 403):
                    delay = max(delay, 300)
            conn.execute("""UPDATE files SET state=?,attempts=?,next_retry_at=?,last_error=?,updated_at=?
                            WHERE path=?""", ("rejected" if terminal else "pending", attempts,
                                               0 if terminal else time.time() + delay,
                                               detail[:160], time.time(), row["path"]))
            LOGGER.warning("Falha no envio: %s (%s)", row["path"], detail[:160])
            counts["failed"] += 1
            if isinstance(exc, HTTPStatusError) and exc.status in (401, 403, 429, 500, 502, 503, 504):
                conn.commit()
                break
        conn.commit()
    return counts


def status(conn: sqlite3.Connection, detailed: bool = False) -> dict[str, Any]:
    totals = {row["state"]: row["count"] for row in conn.execute(
        "SELECT state,COUNT(*) AS count FROM files GROUP BY state")}
    result = {
        "files": totals,
        "unique_sent": conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0],
        "pending_retries": conn.execute("SELECT COUNT(*) FROM files WHERE state='pending' AND attempts>0").fetchone()[0],
        "last_success_utc_epoch": conn.execute("SELECT MAX(accepted_at) FROM digests").fetchone()[0],
    }
    sefaz_rows = conn.execute("SELECT cnpj,environment,state_json,last_cstat,last_status,"
                              "retry_at,last_error FROM sefaz_state ORDER BY cnpj,environment").fetchall()
    if sefaz_rows:
        counts = {row["classification"]: row["count"] for row in conn.execute(
            "SELECT classification,COUNT(*) AS count FROM sefaz_documents GROUP BY classification")}
        uploads = {row["state"]: row["count"] for row in conn.execute("""
            SELECT COALESCE(f.state,'awaiting_scan') AS state,COUNT(*) AS count
            FROM sefaz_documents AS d LEFT JOIN files AS f ON f.path=d.xml_path
            WHERE d.classification='ready' GROUP BY COALESCE(f.state,'awaiting_scan')
        """)}
        result["sefaz_nfe"] = {"sources": [{"cnpj": row["cnpj"],
            "environment": row["environment"], "state": json.loads(row["state_json"]),
            "last_cstat": row["last_cstat"], "last_status": row["last_status"],
            "retry_at_utc_epoch": row["retry_at"], "last_error": row["last_error"]}
            for row in sefaz_rows], "documents_by_classification": counts,
            "ready_uploads": uploads}
    if detailed:
        result["recent_errors"] = [dict(row) for row in conn.execute("""
            SELECT path,state,last_error,attempts,next_retry_at FROM files
            WHERE last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 20
        """)]
    return result


def check(config: dict[str, Any], *, needs_token: bool = True) -> dict[str, Any]:
    missing = [x for x in config["roots"] if not Path(x).is_dir()]
    if missing:
        raise ValueError(f"Diretórios de XML não encontrados: {', '.join(missing)}")
    unreadable = [x for x in config["roots"] if not os.access(x, os.R_OK | os.X_OK)]
    if unreadable:
        raise ValueError(f"Sem permissão para ler diretórios XML: {', '.join(unreadable)}")
    if needs_token:
        read_token(config)
    sefaz = config["sefaz_nfe"]
    if sefaz:
        for field in ("cert_pem", "key_pem"):
            path = Path(sefaz[field])
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                raise ValueError(f"sefaz_nfe.{field} deve ser arquivo regular sem symlink")
            if field == "key_pem" and os.name != "nt" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise ValueError("Chave PEM SEFAZ deve ter permissão 0600 ou mais restrita")
        try:
            ssl.create_default_context().load_cert_chain(sefaz["cert_pem"], sefaz["key_pem"])
        except (OSError, ssl.SSLError) as exc:
            raise ValueError("Certificado/chave PEM SEFAZ indisponível ou incompatível") from exc
    return {"config_valid": True, "roots": len(config["roots"]),
            "token_available": needs_token, "sefaz_nfe_configured": bool(sefaz), "network_called": False}


def run_cycle(config: dict[str, Any], conn: sqlite3.Connection, token: str) -> dict[str, Any]:
    sefaz_result = poll_sefaz(config, conn)
    return {"sefaz_nfe": sefaz_result, "discovery": discover(config, conn),
            "delivery": dispatch(config, conn, token), "status": status(conn)}


def configure(args: argparse.Namespace) -> None:
    output = Path(args.config).expanduser().resolve()
    raw = {
        "server_url": args.server_url, "company_id": args.company_id,
        "roots": [str(Path(p).expanduser().resolve()) for p in args.root],
        "state_dir": str(Path(args.state_dir).expanduser().resolve()),
        "token_env": args.token_env, "recursive": not args.flat,
        "signed_only": not args.include_unsigned,
    }
    if args.token_file:
        raw["token_file"] = str(Path(args.token_file).expanduser().resolve())
    sefaz_args = (args.sefaz_cnpj, args.sefaz_env, args.sefaz_cert_pem, args.sefaz_key_pem)
    if any(value is not None for value in sefaz_args):
        if not all(value is not None for value in sefaz_args):
            raise ValueError("Informe CNPJ, ambiente, cert PEM e key PEM juntos para habilitar SEFAZ")
        raw["sefaz_nfe"] = {"cnpj": args.sefaz_cnpj, "environment": args.sefaz_env,
            "cert_pem": str(Path(args.sefaz_cert_pem).expanduser().resolve()),
            "key_pem": str(Path(args.sefaz_key_pem).expanduser().resolve())}
        if args.sefaz_max_spool_bytes is not None:
            raw["sefaz_nfe"]["max_spool_bytes"] = args.sefaz_max_spool_bytes
    validated = validate_config(raw)
    if output.exists() and not args.force:
        raise ValueError("Configuração existente; use --force para substituí-la")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".collector-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, output)
        if os.name != "nt":
            os.chmod(output, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"Configuração gravada em {output}; nenhum token foi armazenado.")


def self_test() -> dict[str, Any]:
    sample = b'<NFe><Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></NFe>'
    assert has_signature(sample)
    assert hashlib.sha256(sample).hexdigest() == hashlib.sha256(sample).hexdigest()
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE smoke (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO smoke(id) VALUES (1)")
        assert conn.execute("SELECT COUNT(*) FROM smoke").fetchone()[0] == 1
    return {"ok": True, "network_called": False, "version": version()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coletor local de XML fiscal para a plataforma Reforma Tributária")
    parser.add_argument("--version", action="version", version=f"fiscal-xml-collector {version()}")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure", help="Criar configuração sem armazenar credenciais")
    setup.add_argument("--config", required=True)
    setup.add_argument("--server-url", required=True)
    setup.add_argument("--company-id", required=True)
    setup.add_argument("--root", action="append", default=[], help="Diretório local (repetível; opcional com SEFAZ)")
    setup.add_argument("--state-dir", required=True)
    setup.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    setup.add_argument("--token-file")
    setup.add_argument("--flat", action="store_true", help="Não percorrer subdiretórios")
    setup.add_argument("--include-unsigned", action="store_true", help="Permitir XML sem elemento XMLDSig")
    setup.add_argument("--sefaz-cnpj", help="Habilitar Distribuição NF-e para o CNPJ informado")
    setup.add_argument("--sefaz-env", choices=("production", "homologation"))
    setup.add_argument("--sefaz-cert-pem", help="Certificado PEM público; caminho absoluto")
    setup.add_argument("--sefaz-key-pem", help="Chave privada PEM separada; caminho absoluto")
    setup.add_argument("--sefaz-max-spool-bytes", type=int, help="Teto de spool privado, >= 256 MiB")
    setup.add_argument("--force", action="store_true")
    for command in ("check", "once", "serve", "status"):
        sub_parser = sub.add_parser(command)
        sub_parser.add_argument("--config", required=True)
        if command == "check":
            sub_parser.add_argument("--no-token", action="store_true", help="Validar apenas caminhos e configuração")
            sub_parser.add_argument("--expect-state-dir", help="Exigir diretório de estado fixo no serviço")
    sub.add_parser("self-test").add_argument("--offline", action="store_true", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "configure":
            configure(args)
            return 0
        if args.command == "self-test":
            print(json.dumps(self_test()))
            return 0
        config = load_config(Path(args.config))
        if args.command == "check":
            if args.expect_state_dir and Path(config["state_dir"]) != Path(args.expect_state_dir):
                raise ValueError("state_dir não corresponde ao diretório permitido pelo serviço")
            print(json.dumps(check(config, needs_token=not args.no_token), ensure_ascii=False))
            return 0
        if args.command == "status":
            with closing(open_state(config)) as conn:
                print(json.dumps(status(conn, detailed=True), ensure_ascii=False))
            return 0
        check(config)
        token = read_token(config)
        with single_instance(Path(config["state_dir"])):
            with closing(open_state(config)) as conn:
                if args.command == "once":
                    result = run_cycle(config, conn, token)
                    print(json.dumps(result, ensure_ascii=False))
                    return 1 if result["delivery"]["failed"] else 0
                stop = threading.Event()
                signal.signal(signal.SIGTERM, lambda *_: stop.set())
                signal.signal(signal.SIGINT, lambda *_: stop.set())
                LOGGER.info("Coletor iniciado; %d raízes", len(config["roots"]))
                while not stop.is_set():
                    try:
                        result = run_cycle(config, conn, read_token(config))
                        LOGGER.info("Ciclo: %s", json.dumps(result, ensure_ascii=False))
                    except (OSError, sqlite3.Error, ValueError) as exc:
                        LOGGER.error("Falha no ciclo: %s", type(exc).__name__)
                    stop.wait(config["poll_seconds"])
        return 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
