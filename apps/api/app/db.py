from collections.abc import AsyncIterator
import os

from sqlalchemy import event

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings


def prepare_self_data(settings: Settings) -> None:
    """Create private persistent storage before SQLite opens its database file."""
    if not settings.self_hosted:
        return
    base = settings.data_dir
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        base.chmod(0o700)
    database = base / "fiscal.sqlite3"
    database.touch(mode=0o600, exist_ok=True)
    if os.name != "nt":
        database.chmod(0o600)


settings = get_settings()
prepare_self_data(settings)

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
if engine.url.get_backend_name() == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            if settings.self_hosted:
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

Session = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with Session() as session:
        yield session
