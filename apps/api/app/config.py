from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "reforma"
    postgres_user: str = "reforma"
    postgres_password: str = ""
    database_url_override: str = ""
    rabbitmq_host: str = "localhost"
    rabbitmq_default_user: str = "guest"
    rabbitmq_default_pass: str = "guest"
    redis_host: str = "localhost"
    redis_password: str = ""
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "cbs-raw"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    secret_key: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    provider_mode: str = "fixture"
    cbs_restricted_enabled: bool = False
    cbs_production_enabled: bool = False
    cbs_payments_enabled: bool = False
    cbs_allowed_bases: str = ""
    cbs_credential_organization_id: str = ""
    cbs_token_url: str = ""
    cbs_client_id: str = ""
    cbs_client_secret: str = ""
    cbs_webhook_base_url: str = ""
    cbs_download_hosts: str = "storagegw.estaleiro.serpro.gov.br,api.receitafederal.gov.br"
    cbs_max_download_bytes: int = 100_000_000
    cbs_auto_sync_enabled: bool = False
    cbs_auto_sync_environment: str = "restricted"
    cbs_auto_sync_resources: str = "debitos,creditos"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{quote(self.postgres_user, safe='')}:"
            f"{quote(self.postgres_password, safe='')}@{self.postgres_host}:"
            f"{self.postgres_port}/{quote(self.postgres_db, safe='')}"
        )

    @property
    def celery_broker(self) -> str:
        return (
            f"amqp://{quote(self.rabbitmq_default_user, safe='')}:"
            f"{quote(self.rabbitmq_default_pass, safe='')}@{self.rabbitmq_host}:5672//"
        )

    @property
    def allowed_bases(self) -> set[str]:
        return {value.strip().upper() for value in self.cbs_allowed_bases.split(",") if value.strip()}

    @property
    def download_hosts(self) -> set[str]:
        return {value.strip().lower() for value in self.cbs_download_hosts.split(",") if value.strip()}

    @property
    def auto_sync_resources(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip().lower() for value in
                                   self.cbs_auto_sync_resources.split(",") if value.strip()))

    def validate_security(self) -> None:
        if len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY deve ter pelo menos 32 caracteres aleatórios")
        if self.provider_mode not in {"fixture", "receita"}:
            raise RuntimeError("PROVIDER_MODE inválido")
        if self.provider_mode == "receita":
            if not all((self.cbs_token_url, self.cbs_client_id, self.cbs_client_secret,
                        self.cbs_webhook_base_url, self.allowed_bases,
                        self.cbs_credential_organization_id)):
                raise RuntimeError("Credenciais, callback e allowlist CBS obrigatórios")
            if not self.cbs_token_url.startswith("https://") or not self.cbs_webhook_base_url.startswith("https://"):
                raise RuntimeError("OAuth e webhook precisam de HTTPS")
        if self.cbs_auto_sync_enabled:
            if self.provider_mode != "receita":
                raise RuntimeError("Sincronização automática exige provedor Receita habilitado")
            if self.cbs_auto_sync_environment not in {"restricted", "production"}:
                raise RuntimeError("Ambiente da sincronização automática inválido")
            if (self.cbs_auto_sync_environment == "restricted" and not self.cbs_restricted_enabled
                    or self.cbs_auto_sync_environment == "production" and not self.cbs_production_enabled):
                raise RuntimeError("Ambiente da sincronização automática desabilitado")
            if not self.auto_sync_resources or set(self.auto_sync_resources) - {
                    "debitos", "creditos", "pagamentos", "recolhimentos"}:
                raise RuntimeError("Recursos da sincronização automática inválidos")
            if set(self.auto_sync_resources) & {"pagamentos", "recolhimentos"} and not self.cbs_payments_enabled:
                raise RuntimeError("Pagamentos e recolhimentos exigem liberação específica")


@lru_cache
def get_settings() -> Settings:
    return Settings()
