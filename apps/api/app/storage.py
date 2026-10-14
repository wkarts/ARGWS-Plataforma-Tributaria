from __future__ import annotations

import asyncio
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class BlobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.self_hosted:
            self.client = None
            return
        self.client = boto3.client("s3", endpoint_url=("https://" if settings.s3_use_ssl else "http://") + settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region, config=Config(s3={"addressing_style": "path"}))

    async def ensure_bucket(self) -> None:
        if self.settings.self_hosted:
            def make_local():
                self._local_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    self._local_root.chmod(0o700)
            await asyncio.to_thread(make_local)
            return
        def make():
            try:
                self.client.head_bucket(Bucket=self.settings.s3_bucket)
            except ClientError as exc:
                if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
                self.client.create_bucket(Bucket=self.settings.s3_bucket)
        await asyncio.to_thread(make)

    @property
    def _local_root(self) -> Path:
        return self.settings.data_dir / "blobs"

    def _local_path(self, key: str, category: str) -> Path:
        # Keys are generated from server UUIDs and a digest; reject any key
        # loaded from a compromised database before touching the filesystem.
        identifier = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        pattern = (rf"documents/{identifier}/{identifier}/[0-9a-f]{{64}}\.xml" if category == "documents"
                   else rf"cbs/{identifier}/{identifier}/{identifier}/[0-9a-f]{{64}}\.json")
        if not re.fullmatch(pattern, key):
            raise ValueError("Chave de arquivo inválida")
        root = self._local_root.resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Arquivo fora do diretório de dados")
        return path

    def _local_write(self, key: str, category: str, data: bytes, digest: str) -> None:
        path = self._local_path(key, category)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._local_path(key, category)  # Recheck after creating parents.
        if os.name != "nt":
            path.parent.chmod(0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".incoming-",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-linking publishes a complete object without replacing an
            # existing one. This works on local NTFS/ext4 volumes.
            try:
                os.link(temporary, path)
            except FileExistsError:
                with path.open("rb") as existing:
                    stored = existing.read(len(data) + 1)
                if len(stored) != len(data) or sha256(stored).hexdigest() != digest:
                    raise RuntimeError("Objeto fiscal divergente") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _local_read(self, key: str, category: str, digest: str, max_bytes: int) -> bytes:
        path = self._local_path(key, category)
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes or sha256(data).hexdigest() != digest:
            raise RuntimeError("Falha na integridade do arquivo bruto")
        return data

    async def put(self, organization_id: str, company_id: str, query_id: str, data: bytes) -> tuple[str, str]:
        digest = sha256(data).hexdigest()
        key = f"cbs/{organization_id}/{company_id}/{query_id}/{digest}.json"
        if self.settings.self_hosted:
            await asyncio.to_thread(self._local_write, key, "cbs", data, digest)
            return key, digest
        def write():
            # Key is content-addressed and query-specific. If this exact object
            # exists, require its recorded digest to match; never replace it.
            try:
                existing = self.client.head_object(Bucket=self.settings.s3_bucket, Key=key)
                if existing.get("Metadata", {}).get("sha256") != digest:
                    raise RuntimeError("Objeto bruto divergente")
                return
            except ClientError as exc:
                if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
            self.client.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=data,
                ContentType="application/json", Metadata={"sha256": digest}, IfNoneMatch="*")
        await asyncio.to_thread(write)
        return key, digest

    async def get(self, key: str, expected_digest: str) -> bytes:
        if self.settings.self_hosted:
            return await asyncio.to_thread(self._local_read, key, "cbs", expected_digest,
                                           self.settings.cbs_max_download_bytes)
        def read() -> bytes:
            body = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"]
            data = body.read(self.settings.cbs_max_download_bytes + 1)
            body.close()
            if len(data) > self.settings.cbs_max_download_bytes or sha256(data).hexdigest() != expected_digest:
                raise RuntimeError("Falha na integridade do arquivo bruto")
            return data
        return await asyncio.to_thread(read)

    async def put_document(self, organization_id: str, company_id: str,
                           data: bytes) -> tuple[str, str]:
        """Preserve original XML bytes at a content-addressed, tenant-scoped key."""
        digest = sha256(data).hexdigest()
        key = f"documents/{organization_id}/{company_id}/{digest}.xml"
        if self.settings.self_hosted:
            await asyncio.to_thread(self._local_write, key, "documents", data, digest)
            return key, digest

        def write() -> None:
            try:
                existing = self.client.head_object(Bucket=self.settings.s3_bucket, Key=key)
                if existing.get("Metadata", {}).get("sha256") != digest:
                    raise RuntimeError("Objeto fiscal divergente")
                return
            except ClientError as exc:
                if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
            try:
                self.client.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=data,
                    ContentType="application/xml", Metadata={"sha256": digest}, IfNoneMatch="*")
            except ClientError as exc:
                # A concurrent upload may have created exactly the same object.
                if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") not in {409, 412}:
                    raise
                existing = self.client.head_object(Bucket=self.settings.s3_bucket, Key=key)
                if existing.get("Metadata", {}).get("sha256") != digest:
                    raise RuntimeError("Objeto fiscal divergente") from exc

        await asyncio.to_thread(write)
        return key, digest

    async def get_document(self, organization_id: str, company_id: str,
                           key: str, expected_digest: str, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        if not key.startswith(f"documents/{organization_id}/{company_id}/"):
            raise ValueError("Chave fiscal fora do escopo")
        if self.settings.self_hosted:
            return await asyncio.to_thread(self._local_read, key, "documents",
                                           expected_digest, max_bytes)

        def read() -> bytes:
            body = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"]
            try:
                data = body.read(max_bytes + 1)
            finally:
                body.close()
            if len(data) > max_bytes or sha256(data).hexdigest() != expected_digest:
                raise RuntimeError("Falha na integridade do XML original")
            return data

        return await asyncio.to_thread(read)
