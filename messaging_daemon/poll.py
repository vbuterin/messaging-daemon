"""
poll.py — shared async poll loop.

Iterates over all registered backends, calls backend.poll(db) for each,
and re-reads backend account lists from the DB on every iteration so that
newly added accounts take effect without a restart.
"""

import asyncio
import sqlite3
from datetime import datetime, timezone

from .db import DB_PATH, init_db
from . import http_api

POLL_INTERVAL = 60  # seconds


async def poll_loop(backends: dict, interval: int = POLL_INTERVAL) -> None:
    while True:
        print(f"[{datetime.now(timezone.utc).isoformat()}] Polling {len(backends)} backend(s)…")
        db = sqlite3.connect(DB_PATH)
        total = 0
        for name, backend in backends.items():
            try:
                n = backend.poll(db)
                total += n
            except Exception as exc:
                print(f"  [{name}] Poll error: {exc}")
        db.close()
        http_api.set_last_poll(datetime.now(timezone.utc))
        print(f"  Total new: {total}")
        await asyncio.sleep(interval)
