"""Run once after Alembic migrations: python -m app.bootstrap."""

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import Session
from app.models import Organization, User
from app.security import hash_password
from app.storage import BlobStore


async def bootstrap() -> None:
    settings = get_settings()
    settings.validate_security()
    username = settings.bootstrap_admin_email.strip().lower()
    async with Session() as session, session.begin():
        existing = await session.scalar(select(User).limit(1) if settings.self_hosted
                                        else select(User).where(User.username == username))
        if not settings.self_hosted and (not username or not settings.bootstrap_admin_password):
            raise RuntimeError("Configure BOOTSTRAP_ADMIN_EMAIL e BOOTSTRAP_ADMIN_PASSWORD como secrets")
        if existing is None:
            if not username or not settings.bootstrap_admin_password:
                raise RuntimeError("Primeira instalação exige BOOTSTRAP_ADMIN_EMAIL e BOOTSTRAP_ADMIN_PASSWORD como secrets")
            if "@" not in username:
                raise RuntimeError("BOOTSTRAP_ADMIN_EMAIL inválido")
            org = Organization(name="Administração")
            session.add(org)
            await session.flush()
            user = User(organization_id=org.id, username=username,
                        password_hash=hash_password(settings.bootstrap_admin_password), role="admin")
            session.add(user)
            print(f"Administrador criado; organização: {org.id}")
        else:
            print(f"Administrador existente; organização: {existing.organization_id}")
    await BlobStore(settings).ensure_bucket()


if __name__ == "__main__":
    asyncio.run(bootstrap())
