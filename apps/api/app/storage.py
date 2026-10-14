from __future__ import annotations

import asyncio
from hashlib import sha256

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class BlobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = boto3.client("s3", endpoint_url=("https://" if settings.s3_use_ssl else "http://") + settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region, config=Config(s3={"addressing_style": "path"}))

    async def ensure_bucket(self) -> None:
        def make():
            try:
                self.client.head_bucket(Bucket=self.settings.s3_bucket)
            except ClientError as exc:
                if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
                self.client.create_bucket(Bucket=self.settings.s3_bucket)
        await asyncio.to_thread(make)

    async def put(self, organization_id: str, company_id: str, query_id: str, data: bytes) -> tuple[str, str]:
        digest = sha256(data).hexdigest()
        key = f"cbs/{organization_id}/{company_id}/{query_id}/{digest}.json"
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
