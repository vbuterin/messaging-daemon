"""
poll.py — shared async poll loop.

Iterates over all registered backends and calls backend.poll() on each.
Each backend opens and closes its own sqlite3 connection internally — see
Backend.poll's docstring for why.
"""

import asyncio
from datetime import datetime, timezone

from . import http_api

POLL_INTERVAL = 60  # seconds


async def poll_loop(backends: dict, interval: int = POLL_INTERVAL) -> None:
    while True:
        print(f"[{datetime.now(timezone.utc).isoformat()}] Polling {len(backends)} backend(s)…")
        total = 0
        for name, backend in backends.items():
            try:
                n = backend.poll()
                total += n
            except Exception as exc:
                print(f"  [{name}] Poll error: {exc}")
        http_api.set_last_poll(datetime.now(timezone.utc))
        print(f"  Total new: {total}")
        await asyncio.sleep(interval)
