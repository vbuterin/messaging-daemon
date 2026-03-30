"""
http_api.py — main HTTP API server (port 6000).

Endpoints:
  GET /messages   — query messages across all backends
  GET /send       — send a message (may require confirmation)
  GET /accounts   — list all configured accounts per backend
  GET /status     — daemon health and stats

All endpoints accept ?backend= to filter/select a specific backend.
"""

import json
import sqlite3
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from .db import DB_PATH, query_messages, init_db
from .confirm import enqueue, pending_count

PORT = 6000

_start_time = datetime.now(timezone.utc)
_last_poll: datetime | None = None
_backends: dict = {}  # populated by __main__


def set_backends(backends: dict) -> None:
    global _backends
    _backends = backends


def set_last_poll(dt: datetime) -> None:
    global _last_poll
    _last_poll = dt


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().isoformat()}] {' '.join(str(a) for a in args)}")

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        def first(key: str) -> str | None:
            return qs[key][0] if key in qs else None

        if parsed.path == "/messages":
            try:
                limit = int(first("limit") or 100)
            except ValueError:
                self.send_json({"error": "'limit' must be an integer"}, 400)
                return
            try:
                since_ms = int(first("since")) if first("since") else None
                until_ms = int(first("until")) if first("until") else None
            except ValueError:
                self.send_json({"error": "'since' and 'until' must be ms timestamps"}, 400)
                return

            messages = query_messages(
                backend=first("backend"),
                account=first("account"),
                sender=first("sender"),
                subject=first("subject"),
                thread_id=first("thread_id"),
                since_ms=since_ms,
                until_ms=until_ms,
                limit=limit,
            )
            self.send_json({"count": len(messages), "messages": messages})

        elif parsed.path == "/send":
            backend_name = first("backend")
            if not backend_name:
                if len(_backends) == 1:
                    backend_name = next(iter(_backends))
                else:
                    self.send_json({"error": f"specify ?backend= — available: {list(_backends)}"}, 400)
                    return

            backend = _backends.get(backend_name)
            if not backend:
                self.send_json({"error": f"unknown backend '{backend_name}'"}, 404)
                return

            account = first("from") or first("account")
            recipient = first("to")
            body = first("body") or first("message")
            subject = first("subject")

            if not recipient or not body:
                self.send_json({"error": "missing 'to' and 'body' parameters"}, 400)
                return

            # Resolve account if not given (single-account backends)
            if not account:
                accts = backend.accounts()
                if len(accts) == 1:
                    account = accts[0]["account"]
                else:
                    self.send_json({"error": "specify ?from= to disambiguate account"}, 400)
                    return

            # Self-send: bypass confirmation
            if backend.is_self(account, recipient):
                try:
                    # Signal has a special note-to-self command
                    if hasattr(backend, "send_to_self"):
                        backend.send_to_self(account, body)
                    else:
                        backend.send(account, recipient, body, subject)
                    self.send_json({"ok": True, "backend": backend_name,
                                    "from": account, "to": recipient})
                except Exception as exc:
                    self.send_json({"error": str(exc)}, 500)
                return

            # Non-self: queue for confirmation
            confirm_url = enqueue(backend, account, recipient, body, subject)
            display = backend.resolve_display_name(account, recipient)
            print(f"[{datetime.now().isoformat()}] Confirmation required: {confirm_url}")
            self.send_json({
                "pending": True,
                "confirm_url": confirm_url,
                "backend": backend_name,
                "from": account,
                "to": recipient,
                "display_name": display,
                "message": "Open confirm_url in your browser to approve or deny.",
            })

        elif parsed.path == "/accounts":
            result = {}
            for name, backend in _backends.items():
                result[name] = backend.accounts()
            self.send_json(result)

        elif parsed.path == "/status":
            db = sqlite3.connect(DB_PATH)
            count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            db.close()
            uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()
            self.send_json({
                "uptime_seconds": int(uptime),
                "last_poll": _last_poll.isoformat() if _last_poll else None,
                "message_count": count,
                "pending_confirmations": pending_count(),
                "backends": {
                    name: [a["account"] for a in b.accounts()]
                    for name, b in _backends.items()
                },
            })

        else:
            self.send_json({"error": "Not found"}, 404)


def run_api_server() -> None:
    print(f"API server listening on http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
