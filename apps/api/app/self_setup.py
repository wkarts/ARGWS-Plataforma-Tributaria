"""Initialize or upgrade the persistent self-hosted database and its administrator.

Run before the API starts. Repeat runs are idempotent; retain a backup of the
SQLite database and XML directory before upgrading an existing installation.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.bootstrap import bootstrap
from app.config import get_settings
from app.db import prepare_self_data


def migrate() -> None:
    settings = get_settings()
    settings.validate_security()
    if not settings.self_hosted:
        raise RuntimeError("app.self_setup é exclusivo para DEPLOYMENT_MODE=self")
    prepare_self_data(settings)
    base = Path(__file__).resolve().parents[1]
    config = Config(str(base / "alembic.ini"))
    config.set_main_option("script_location", str(base / "alembic"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    migrate()
    asyncio.run(bootstrap())
