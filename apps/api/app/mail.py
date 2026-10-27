"""Opt-in secure SMTP delivery. Never logs credentials or invitation tokens."""
from __future__ import annotations

import asyncio
from email.message import EmailMessage
import smtplib
import ssl

from app.config import Settings, get_settings


class MailUnavailable(Exception):
    pass


def mail_status(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    missing = []
    if not settings.smtp_enabled:
        return {"enabled": False, "ready": False, "missing": ["SMTP_ENABLED"]}
    for key, value in {
        "SMTP_HOST": settings.smtp_host,
        "SMTP_FROM": settings.smtp_from,
        "APP_PUBLIC_URL": settings.app_public_url,
    }.items():
        if not value:
            missing.append(key)
    if settings.smtp_username and not settings.smtp_password:
        missing.append("SMTP_PASSWORD")
    return {"enabled": True, "ready": not missing, "missing": missing,
            "host": settings.smtp_host, "port": settings.smtp_port,
            "sender": settings.smtp_from, "starttls": settings.smtp_starttls,
            "ssl": settings.smtp_ssl}


def send_sync(address: str, subject: str, message: str, settings: Settings) -> None:
    if not mail_status(settings)["ready"]:
        raise MailUnavailable("SMTP não configurado; configure o deployer antes de convidar usuários.")
    settings.validate_security(require_provider=False)
    envelope = EmailMessage()
    envelope["From"] = settings.smtp_from
    envelope["To"] = address
    envelope["Subject"] = subject
    envelope.set_content(message)
    try:
        if settings.smtp_ssl:
            transport = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                         timeout=12, context=ssl.create_default_context())
        else:
            transport = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12)
        with transport:
            transport.ehlo()
            if settings.smtp_starttls and not settings.smtp_ssl:
                transport.starttls(context=ssl.create_default_context())
                transport.ehlo()
            if settings.smtp_username:
                transport.login(settings.smtp_username, settings.smtp_password)
            transport.send_message(envelope)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailUnavailable("Falha ao entregar mensagem pelo SMTP configurado") from exc


async def send_mail(address: str, subject: str, message: str,
                    settings: Settings | None = None) -> None:
    await asyncio.to_thread(send_sync, address, subject, message, settings or get_settings())
