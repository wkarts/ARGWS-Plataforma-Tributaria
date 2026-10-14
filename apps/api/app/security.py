from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User


password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Senha deve ter ao menos 12 caracteres")
    return password_hasher.hash(password)


def verify_password(stored: str, candidate: str) -> bool:
    try:
        return password_hasher.verify(stored, candidate)
    except VerificationError:
        return False


def issue_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({"iss": "fiscal-platform", "sub": user.id,
        "org": user.organization_id, "ver": user.token_version,
        "iat": now, "exp": now + timedelta(hours=1)},
        settings.secret_key, algorithm="HS256")


async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                       session: AsyncSession = Depends(get_session)) -> User:
    if credentials is None:
        raise HTTPException(401, "Autenticação requerida", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(credentials.credentials, get_settings().secret_key,
                            algorithms=["HS256"], issuer="fiscal-platform")
        user = await session.scalar(select(User).where(User.id == claims["sub"],
            User.organization_id == claims["org"], User.active.is_(True)))
        if user is None or user.token_version != claims.get("ver"):
            raise ValueError()
        return user
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Token inválido", headers={"WWW-Authenticate": "Bearer"}) from None


def admin_only(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Privilégio de administração requerido")
    return user


def webhook_token(query_id: str) -> str:
    return hmac.new(get_settings().secret_key.encode(),
                    b"cbs-webhook-v1:" + query_id.encode(), "sha256").hexdigest()


def webhook_token_hash(query_id: str) -> str:
    return sha256(webhook_token(query_id).encode()).hexdigest()
