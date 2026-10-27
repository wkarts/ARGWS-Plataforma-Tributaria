from __future__ import annotations

from functools import lru_cache
import ipaddress
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deployment_mode: str = "central"
    app_env: str = "development"
    self_data_dir: str = "./data"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "fiscal"
    postgres_user: str = "fiscal"
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
    secret_key_file: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_password_file: str = ""
    provider_mode: str = "disabled"
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_password_file: str = ""
    smtp_from: str = ""
    smtp_reply_to: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    app_public_url: str = ""
    cnpj_lookup_enabled: bool = True
    cbs_restricted_enabled: bool = False
    cbs_production_enabled: bool = False
    cbs_payments_enabled: bool = False
    cbs_allowed_bases: str = ""
    cbs_credential_organization_id: str = ""
    cbs_token_url: str = ""
    cbs_client_id: str = ""
    cbs_client_secret: str = ""
    cbs_client_secret_file: str = ""
    cbs_webhook_base_url: str = ""
    cbs_download_hosts: str = "storagegw.estaleiro.serpro.gov.br,api.receitafederal.gov.br"
    cbs_max_download_bytes: int = 100_000_000
    cbs_auto_sync_enabled: bool = False
    cbs_auto_sync_environment: str = "restricted"
    cbs_auto_sync_resources: str = "debitos,creditos"
    tax_calculator_regular_url: str = ""
    tax_calculator_regular_version_url: str = ""
    tax_calculator_simples_url: str = ""
    tax_calculator_simples_version_url: str = ""

    @model_validator(mode="after")
    def resolve_secret_files(self) -> "Settings":
        for field in ("secret_key", "bootstrap_admin_password", "cbs_client_secret", "smtp_password"):
            filename = getattr(self, f"{field}_file")
            if filename:
                if getattr(self, field):
                    raise ValueError(f"Configure somente {field.upper()} ou {field.upper()}_FILE")
                setattr(self, field, Path(filename).read_text(encoding="utf-8").rstrip("\r\n"))
        return self

    @property
    def database_url(self) -> str:
        if self.self_hosted:
            return f"sqlite+aiosqlite:///{(self.data_dir / 'fiscal.sqlite3').as_posix()}"
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{quote(self.postgres_user, safe='')}:"
            f"{quote(self.postgres_password, safe='')}@{self.postgres_host}:"
            f"{self.postgres_port}/{quote(self.postgres_db, safe='')}"
        )

    @property
    def self_hosted(self) -> bool:
        return self.deployment_mode == "self"

    @property
    def data_dir(self) -> Path:
        return Path(self.self_data_dir).expanduser().resolve()

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

    def receipt_readiness(self) -> dict:
        """Safe operational status without exposing any secret value."""
        if self.provider_mode != "receita":
            return {"ready": False, "reason": "Integração Receita desabilitada", "missing": []}
        required = {
            "CBS_TOKEN_URL": self.cbs_token_url,
            "CBS_CLIENT_ID": self.cbs_client_id,
            "CBS_CLIENT_SECRET": self.cbs_client_secret,
            "CBS_WEBHOOK_BASE_URL": self.cbs_webhook_base_url,
            "CBS_ALLOWED_BASES": self.allowed_bases,
            "CBS_CREDENTIAL_ORGANIZATION_ID": self.cbs_credential_organization_id,
        }
        missing = [key for key, value in required.items() if not value]
        if self.self_hosted and not self.cbs_client_secret_file:
            missing.append("CBS_CLIENT_SECRET_FILE")
        if not self.cbs_restricted_enabled and not self.cbs_production_enabled:
            missing.append("CBS_RESTRICTED_ENABLED / CBS_PRODUCTION_ENABLED")
        try:
            self.validate_security(require_provider=True)
        except RuntimeError as exc:
            return {"ready": False, "reason": str(exc), "missing": missing}
        if missing:
            return {"ready": False, "reason": "Nenhum ambiente CBS oficial habilitado",
                    "missing": missing}
        return {"ready": True, "reason": None, "missing": []}

    def validate_security(self, *, require_provider: bool = True) -> None:
        if self.deployment_mode not in {"central", "self"}:
            raise RuntimeError("DEPLOYMENT_MODE inválido")
        if self.app_env not in {"production", "development", "develop", "test"}:
            raise RuntimeError("APP_ENV inválido")
        if self.app_env == "production" and self.provider_mode == "fixture":
            raise RuntimeError("Produção não permite PROVIDER_MODE=fixture com dados simulados")
        if self.self_hosted:
            if self.database_url_override:
                raise RuntimeError("No modo self configure SELF_DATA_DIR, não DATABASE_URL_OVERRIDE")
            if self.provider_mode == "fixture":
                raise RuntimeError("Fixture não é uma fonte oficial no modo self")
        if len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY deve ter pelo menos 32 caracteres aleatórios")
        if self.provider_mode not in {"fixture", "receita", "disabled"}:
            raise RuntimeError("PROVIDER_MODE inválido")
        if self.smtp_enabled:
            from email.utils import parseaddr
            import re
            url = urlsplit(self.app_public_url)
            if (not self.smtp_host or not 1 <= self.smtp_port <= 65535
                    or any(c in self.smtp_host for c in "\\r\\n /:@")
                    or (self.smtp_ssl and self.smtp_starttls)
                    or (self.smtp_username and not self.smtp_password)
                    or (self.smtp_password and not self.smtp_username)
                    or (self.smtp_username and not (self.smtp_ssl or self.smtp_starttls))
                    or parseaddr(self.smtp_from)[1] != self.smtp_from
                    or not re.fullmatch(r"[^\\s@]+@[^\\s@]+\\.[^\\s@]+", self.smtp_from)
                    or url.scheme != "https" or not url.hostname
                    or url.username or url.password or url.query or url.fragment
                    or url.path not in ("", "/")):
                raise RuntimeError("SMTP_ENABLED requer host, TLS/SSL, remetente e APP_PUBLIC_URL HTTPS válidos")

        if self.provider_mode == "receita" and require_provider:
            if self.self_hosted and not self.cbs_client_secret_file:
                raise RuntimeError("No modo self, informe CBS_CLIENT_SECRET_FILE protegido")
            if not all((self.cbs_token_url, self.cbs_client_id, self.cbs_client_secret,
                        self.cbs_webhook_base_url, self.allowed_bases,
                        self.cbs_credential_organization_id)):
                raise RuntimeError("Credenciais, callback e allowlist CBS obrigatórios")
            if not self.cbs_token_url.startswith("https://") or not self.cbs_webhook_base_url.startswith("https://"):
                raise RuntimeError("OAuth e webhook precisam de HTTPS")
            try:
                callback = urlsplit(self.cbs_webhook_base_url)
                token = urlsplit(self.cbs_token_url)
                callback_host = callback.hostname or ""
                if callback_host:
                    try:
                        public_host = ipaddress.ip_address(callback_host).is_global
                    except ValueError:
                        public_host = callback_host.lower() != "localhost"
                else:
                    public_host = False
                allowed = (public_host and not callback.username and not callback.password
                    and callback.port in {None, 443} and not callback.query and not callback.fragment
                    and token.hostname == "api.receitafederal.gov.br"
                    and not token.username and not token.password and token.port in {None, 443})
            except ValueError:
                allowed = False
            if not allowed:
                raise RuntimeError("Webhook CBS exige hostname HTTPS público na porta padrão")
            if self.self_hosted and not (self.cbs_restricted_enabled or self.cbs_production_enabled):
                raise RuntimeError("Habilite explicitamente um ambiente CBS no modo self")
        if self.cbs_auto_sync_enabled and require_provider:
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
