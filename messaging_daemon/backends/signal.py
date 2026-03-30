"""
backends/signal.py — Signal backend via signal-cli.

Account config is a single phone number stored in the DB config table
under the key 'signal_account'.

CLI:
    messaging-daemon signal setup +1XXXXXXXXXX
"""

import argparse
import json
import sqlite3
import subprocess

from ..db import DB_PATH, get_config, set_config, store_message, now_ms
from .base import Backend

SIGNAL_CLI = "signal-cli"


class SignalBackend(Backend):
    name = "signal"

    # ── Account management ────────────────────────────────────────────────────

    def accounts(self) -> list[dict]:
        db = sqlite3.connect(DB_PATH)
        acct = get_config(db, "signal_account")
        db.close()
        if not acct:
            return []
        return [{"account": acct, "backend": self.name}]

    def get_account(self) -> str | None:
        db = sqlite3.connect(DB_PATH)
        acct = get_config(db, "signal_account")
        db.close()
        return acct

    def set_account(self, number: str) -> None:
        db = sqlite3.connect(DB_PATH)
        set_config(db, "signal_account", number)
        db.close()

    # ── CLI ───────────────────────────────────────────────────────────────────

    def register_commands(self, subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser("signal", help="Signal backend commands")
        ss = p.add_subparsers(dest="signal_command")
        setup = ss.add_parser("setup", help="Register Signal account number")
        setup.add_argument("number", help="Phone number e.g. +1XXXXXXXXXX")

    def handle_command(self, args: argparse.Namespace) -> bool:
        if args.command != "signal":
            return False
        if args.signal_command == "setup":
            self.set_account(args.number)
            print(f"Signal account saved: {args.number}")
        else:
            print("Usage: messaging-daemon signal setup +1XXXXXXXXXX")
        return True

    # ── Recipient helpers ─────────────────────────────────────────────────────

    def _classify(self, recipient: str) -> str:
        """Return 'number', 'username', or 'group'."""
        if recipient.startswith("+"):
            return "number"
        if "." in recipient and len(recipient) < 40:
            return "username"
        return "group"

    def is_self(self, account: str, recipient: str) -> bool:
        return recipient.strip() == account.strip()

    def resolve_display_name(self, account: str, recipient: str) -> str:
        if self._classify(recipient) != "group":
            return recipient
        try:
            result = subprocess.run(
                [SIGNAL_CLI, "-a", account, "--output=json", "listGroups"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return recipient
            # listGroups outputs a single JSON array, not one object per line
            groups = json.loads(result.stdout)
            for group in groups:
                if group.get("id") == recipient:
                    return group.get("name", recipient)
        except Exception:
            pass
        return recipient

    # ── Sending ───────────────────────────────────────────────────────────────

    def send(self, account: str, recipient: str, body: str, subject: str | None = None) -> None:
        kind = self._classify(recipient)
        if kind in ("number", "username"):
            cmd = [SIGNAL_CLI, "-a", account, "send", "-m", body, recipient]
        else:
            cmd = [SIGNAL_CLI, "-a", account, "send", "-m", body, "-g", recipient]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())

    def send_to_self(self, account: str, body: str) -> None:
        result = subprocess.run(
            [SIGNAL_CLI, "-a", account, "send", "--note-to-self", "-m", body],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())

    # ── Polling ───────────────────────────────────────────────────────────────

    def poll(self, db: sqlite3.Connection) -> int:
        account = self.get_account()
        if not account:
            print(f"  [signal] No account configured — skipping poll.")
            return 0

        result = subprocess.run(
            [SIGNAL_CLI, "-a", account, "--output=json", "receive"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  [signal] signal-cli error: {result.stderr.strip()}")
            return 0

        count = 0
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  [signal] Failed to parse line: {exc}")
                continue

            env = envelope.get("envelope", {})
            data = env.get("dataMessage", {})
            body = data.get("message")
            if not body:
                continue

            group_info = data.get("groupInfo") or {}
            ts = env.get("timestamp") or now_ms()

            msg = {
                "backend":      self.name,
                "account":      account,
                "uid":          str(ts) + "_" + (env.get("source") or "unknown"),
                "sender":       env.get("source"),
                "sender_name":  env.get("sourceName"),
                "recipient":    account,
                "thread_id":    group_info.get("groupId"),
                "body":         body,
                "timestamp_ms": ts,
                "metadata":     envelope,
            }
            if store_message(db, msg):
                count += 1

        return count

    # ── Confirmation page fields ──────────────────────────────────────────────

    def confirmation_fields(self, account, recipient, body, subject):
        return [
            ("From", account),
            ("To", self.resolve_display_name(account, recipient)),
            ("Message", body),
        ]
