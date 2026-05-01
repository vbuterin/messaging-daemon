"""
confirm.py — human-facing confirmation server (port 7000).

Completely backend-agnostic. Pending sends are stored in _pending keyed by
a random token. Each entry carries enough info to call backend.send() and
to render a confirmation page via backend.confirmation_fields().

This module is intentionally free of any Signal or email imports.

macOS notes
-----------
Port 7000 is used by macOS AirPlay Receiver, which will prevent the server
from binding. To free it:
  System Settings → General → AirDrop & Handoff → AirPlay Receiver → Off

Alternatively change CONFIRM_PORT to any unused port (e.g. 7001), but note
that Chrome blocks several ports (6000, 7000 among others) — use Safari or
Firefox if you keep ports in that range.

The confirmation URL uses 127.0.0.1 rather than localhost because macOS
resolves localhost to ::1 (IPv6) while the server binds to IPv4 only,
causing Safari to get a blank page.
"""

import html as html_lib
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

CONFIRM_PORT = 7000

_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()


def enqueue(
    backend,        # Backend instance
    account: str,
    recipient: str,
    body: str,
    subject: str | None = None,
) -> str:
    """
    Store a pending send and return the confirmation URL.
    The caller should return this URL to whoever made the /send request.
    """
    token = secrets.token_urlsafe(32)
    with _pending_lock:
        _pending[token] = {
            "backend":   backend,
            "account":   account,
            "recipient": recipient,
            "body":      body,
            "subject":   subject,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    url = f"http://127.0.0.1:{CONFIRM_PORT}/confirm?token={token}"
    subprocess.Popen(["open", "-a", "Safari", url], close_fds=True)
    return url


def pending_count() -> int:
    with _pending_lock:
        return len(_pending)


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _page(title: str, body_content: str) -> str:
    e = html_lib.escape
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{e(title)}</title>
<style>
  body {{ font-family: sans-serif; max-width: 640px; margin: 4em auto; padding: 0 1em; color: #111; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.5em 0; }}
  td {{ padding: 10px 12px; vertical-align: top; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  .label {{ font-weight: bold; width: 80px; }}
  .body-text {{ white-space: pre-wrap; font-family: monospace; font-size: 0.9em; }}
  .actions {{ display: flex; gap: 1em; margin-top: 2em; }}
  a.btn {{ padding: 12px 28px; text-decoration: none; border-radius: 6px; font-size: 1.05em; color: white; }}
  a.send {{ background: #2563eb; }}
  a.deny {{ background: #dc2626; }}
  .meta {{ color: #888; font-size: 0.85em; margin-top: 2em; }}
</style>
</head><body>
{body_content}
</body></html>"""


# ── HTTP handler ──────────────────────────────────────────────────────────────

class ConfirmHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().isoformat()}] confirm: {' '.join(str(a) for a in args)}")

    def send_html(self, content: str, status: int = 200) -> None:
        data = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        token = qs.get("token", [None])[0]
        e = html_lib.escape

        if parsed.path == "/confirm":
            if not token:
                self.send_html(_page("Error", "<h2>Missing token</h2>"), 400)
                return
            with _pending_lock:
                pending = _pending.get(token)
            if not pending:
                self.send_html(_page("Not found", "<h2>&#x274C; Not found</h2><p>This link is invalid or has already been used.</p>"), 404)
                return

            fields = pending["backend"].confirmation_fields(
                pending["account"], pending["recipient"],
                pending["body"], pending["subject"],
            )
            rows = "".join(
                f'<tr><td class="label">{e(label)}</td>'
                f'<td class="body-text">{e(value)}</td></tr>'
                for label, value in fields
            )
            body_content = f"""
<h2>&#x1F4EC; Confirm outbound message</h2>
<table>{rows}</table>
<div class="actions">
  <a class="btn send" href="/approve?token={e(token)}">&#x2714; Send</a>
  <a class="btn deny" href="/deny?token={e(token)}">&#x2716; Don't send</a>
</div>
<p class="meta">Requested at {e(pending['created_at'])}</p>"""
            self.send_html(_page("Confirm send", body_content))

        elif parsed.path == "/approve":
            if not token:
                self.send_html(_page("Error", "<h2>Missing token</h2>"), 400)
                return
            with _pending_lock:
                pending = _pending.pop(token, None)
            if not pending:
                self.send_html(_page("Already handled", "<h2>&#x274C; Already handled</h2><p>This link is invalid or has already been used.</p>"), 404)
                return
            try:
                pending["backend"].send(
                    pending["account"], pending["recipient"],
                    pending["body"], pending["subject"],
                )
                display = pending["backend"].resolve_display_name(
                    pending["account"], pending["recipient"]
                )
                print(f"[{datetime.now().isoformat()}] Confirmed send to {pending['recipient']}")
                body_content = f"<h2>&#x2705; Sent</h2><p>Message delivered to <strong>{e(display)}</strong>.</p>"
                self.send_html(_page("Sent", body_content))
            except Exception as ex:
                body_content = f"<h2>&#x274C; Send failed</h2><pre>{e(str(ex))}</pre>"
                self.send_html(_page("Error", body_content), 500)

        elif parsed.path == "/deny":
            if not token:
                self.send_html(_page("Error", "<h2>Missing token</h2>"), 400)
                return
            with _pending_lock:
                pending = _pending.pop(token, None)
            if not pending:
                self.send_html(_page("Already handled", "<h2>&#x274C; Already handled</h2><p>This link is invalid or has already been used.</p>"), 404)
                return
            display = pending["backend"].resolve_display_name(
                pending["account"], pending["recipient"]
            )
            print(f"[{datetime.now().isoformat()}] Denied send to {pending['recipient']}")
            body_content = f"<h2>&#x1F6AB; Cancelled</h2><p>Message to <strong>{e(display)}</strong> was not sent.</p>"
            self.send_html(_page("Cancelled", body_content))

        else:
            self.send_html(_page("Not found", "<h1>Not found</h1>"), 404)


def run_confirm_server() -> None:
    print(f"Confirmation server listening on http://localhost:{CONFIRM_PORT} (human-facing)")
    HTTPServer(("127.0.0.1", CONFIRM_PORT), ConfirmHandler).serve_forever()
