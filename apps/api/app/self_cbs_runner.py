"""Single local CBS poller for a customer-owned SQLite installation.

Only this process may open official requests. SQLite stores the ticket and
lease before any network call; a lost opening remains uncertain for human
reconciliation rather than being posted a second time automatically.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path
import time

from app.auto_sync import schedule_due
from app.config import get_settings
from app.db import prepare_self_data
from app.worker import run_tick


def exclusive_lock(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise RuntimeError("Outro processador CBS já utiliza esta instalação") from None
    return descriptor


async def serve() -> None:
    settings = get_settings()
    settings.validate_security()
    if not settings.self_hosted:
        raise RuntimeError("O processador local requer DEPLOYMENT_MODE=self")
    prepare_self_data(settings)
    descriptor = exclusive_lock(settings.data_dir / ".cbs-worker.lock")
    last_schedule = 0.0
    heartbeat = settings.data_dir / ".cbs-worker-heartbeat"
    try:
        while True:
            heartbeat.touch(mode=0o600)
            if settings.provider_mode == "receita":
                current = time.monotonic()
                if current - last_schedule >= 900:
                    await schedule_due(settings)
                    last_schedule = current
                await run_tick()
            heartbeat.touch(mode=0o600)
            await asyncio.sleep(30)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    asyncio.run(serve())
