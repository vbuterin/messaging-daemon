"""
backends/telegram.py — Telegram Bot API backend.

Account config stored in DB:
  telegram_bot_token   — bot token from BotFather
  telegram_update_offset — last processed update_id + 1

CLI:
    messaging-daemon telegram setup --token BOT_TOKEN
    messaging-daemon telegram list
"""

import argparse
import json
import os
import sqlite3
import ssl
import urllib.error
import urllib.parse
import urllib.request

from ..db import DB_PATH, get_config, set_config, store_message
from .base import Backend

API = "https://api.telegram.org/bot{token}/{method}"

_CERTS = os.path.expanduser("~/certs.pem")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if os.path.exists(_CERTS):
        ctx.load_verify_locations(_CERTS)
    return ctx


def _call(token: str, method: str, params: dict | None = None) -> dict:
    url = API.format(token=token, method=method)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30, context=_ssl_ctx()) as resp:
        return json.loads(resp.read())


class TelegramBackend(Backend):
    name = "telegram"

    # ── Account management ────────────────────────────────────────────────────

    def _get_token(self, db: sqlite3.Connection) -> str | None:
        return get_config(db, "telegram_bot_token")

    def accounts(self) -> list[dict]:
        db = sqlite3.connect(DB_PATH)
        token = self._get_token(db)
        db.close()
        if not token:
            return []
        try:
            info = _call(token, "getMe")
            username = info["result"].get("username", "unknown")
            return [{"account": f"@{username}", "backend": self.name}]
        except Exception:
            return [{"account": "telegram-bot", "backend": self.name}]

    # ── CLI ───────────────────────────────────────────────────────────────────

    def register_commands(self, subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser("telegram", help="Telegram backend commands")
        ts = p.add_subparsers(dest="telegram_command")

        setup = ts.add_parser("setup", help="Set bot token")
        setup.add_argument("--token", required=True, help="Bot token from BotFather")

        ts.add_parser("list", help="Show configured bot")

    def handle_command(self, args: argparse.Namespace) -> bool:
        if args.command != "telegram":
            return False
        if args.telegram_command == "setup":
            db = sqlite3.connect(DB_PATH)
            set_config(db, "telegram_bot_token", args.token)
            db.close()
            try:
                info = _call(args.token, "getMe")
                username = info["result"].get("username", "?")
                print(f"Telegram bot saved: @{username}")
            except Exception as e:
                print(f"Token saved, but could not verify: {e}")
        elif args.telegram_command == "list":
            accts = self.accounts()
            if not accts:
                print("No Telegram bot configured.")
            for a in accts:
                print(f"  {a['account']}")
        else:
            print("Usage: messaging-daemon telegram [setup|list]")
        return True

    # ── Recipient helpers ─────────────────────────────────────────────────────

    def is_self(self, account: str, recipient: str) -> bool:
        return recipient.strip().lstrip("@").lower() == account.strip().lstrip("@").lower()

    def resolve_display_name(self, account: str, recipient: str) -> str:
        return recipient

    # ── Sending ───────────────────────────────────────────────────────────────

    def send(self, account: str, recipient: str, body: str, subject: str | None = None) -> None:
        db = sqlite3.connect(DB_PATH)
        token = self._get_token(db)
        db.close()
        if not token:
            raise RuntimeError("No Telegram bot token configured")
        try:
            _call(token, "sendMessage", {"chat_id": recipient, "text": body})
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegram sendMessage failed: {e.read().decode()}")

    # ── Polling ───────────────────────────────────────────────────────────────

    def poll(self, db: sqlite3.Connection) -> int:
        db_conn = sqlite3.connect(DB_PATH)
        token = self._get_token(db_conn)
        if not token:
            print("  [telegram] No bot token configured — skipping poll.")
            db_conn.close()
            return 0

        raw_offset = get_config(db_conn, "telegram_update_offset")
        offset = int(raw_offset) if raw_offset else None

        params: dict = {"limit": 100}
        if offset is not None:
            params["offset"] = offset

        try:
            data = _call(token, "getUpdates", params)
        except Exception as exc:
            print(f"  [telegram] getUpdates error: {exc}")
            db_conn.close()
            return 0

        updates = data.get("result", [])
        count = 0
        new_offset = offset

        for update in updates:
            update_id = update["update_id"]
            new_offset = update_id + 1

            msg_data = update.get("message") or update.get("channel_post")
            if not msg_data:
                continue

            body = msg_data.get("text")
            if not body:
                continue

            chat = msg_data.get("chat", {})
            from_user = msg_data.get("from", {})
            chat_id = str(chat.get("id", ""))
            sender_id = str(from_user.get("id", chat_id))
            sender_name = (
                from_user.get("username")
                or f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip()
                or chat.get("title")
                or sender_id
            )

            msg = {
                "backend":      self.name,
                "account":      "telegram",
                "uid":          str(update_id),
                "sender":       sender_id,
                "sender_name":  sender_name,
                "recipient":    chat_id,
                "thread_id":    chat_id,
                "body":         body,
                "timestamp_ms": msg_data.get("date", 0) * 1000,
                "metadata":     update,
            }
            if store_message(db, msg):
                count += 1

        if new_offset is not None:
            set_config(db_conn, "telegram_update_offset", str(new_offset))
        db_conn.close()
        return count

    # ── Confirmation page fields ──────────────────────────────────────────────

    def confirmation_fields(self, account, recipient, body, subject):
        return [
            ("From", account),
            ("To", recipient),
            ("Message", body),
        ]
