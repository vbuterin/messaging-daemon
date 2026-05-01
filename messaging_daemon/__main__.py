"""
__main__.py — entry point, CLI wiring, and daemon startup.

Fully generic: all backend-specific CLI logic lives in each backend's
register_commands() and handle_command() methods.

Run as:
    python -m messaging_daemon [command] [args]
    messaging-daemon [command] [args]   (when installed as a package)
"""

import argparse
import asyncio
import threading

from .db import init_db

# ── Backend registry ──────────────────────────────────────────────────────────
# To add a new backend: import it and add an instance to BACKENDS.

from .backends.signal import SignalBackend
from .backends.email import EmailBackend
from .backends.telegram import TelegramBackend

BACKENDS: dict = {
    "signal":   SignalBackend(),
    "email":    EmailBackend(),
    "telegram": TelegramBackend(),
}

# ── Shared imports ────────────────────────────────────────────────────────────

from .http_api import run_api_server, set_backends
from .confirm import run_confirm_server
from .poll import poll_loop, POLL_INTERVAL


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="messaging-daemon",
        description="Unified messaging daemon",
    )
    sub = p.add_subparsers(dest="command")

    # Generic run command
    run_p = sub.add_parser("run", help="Start the daemon (default)")
    run_p.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})",
    )

    # Each backend registers its own subcommands
    for backend in BACKENDS.values():
        backend.register_commands(sub)

    return p


def main() -> None:
    init_db()
    args = build_parser().parse_args()

    # Let each backend try to handle the command; stop at the first match
    for backend in BACKENDS.values():
        if backend.handle_command(args):
            return

    # No backend claimed it — run the daemon
    interval = getattr(args, "interval", POLL_INTERVAL)
    set_backends(BACKENDS)
    threading.Thread(target=run_api_server, daemon=True).start()
    threading.Thread(target=run_confirm_server, daemon=True).start()
    asyncio.run(poll_loop(BACKENDS, interval=interval))


if __name__ == "__main__":
    main()
