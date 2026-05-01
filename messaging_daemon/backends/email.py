"""
backends/email.py — IMAP/SMTP email backend.

Multiple accounts supported. Configs stored as JSON list in DB under
the key 'email_accounts'.

CLI:
    messaging-daemon email add --email you@example.com --password ... \
        --imap-host 127.0.0.1 --imap-port 1143 --imap-ssl false \
        --smtp-host 127.0.0.1 --smtp-port 1025
    messaging-daemon email remove --email you@example.com
    messaging-daemon email list
"""

import argparse
import email as email_lib
import imaplib
import json
import smtplib
import sqlite3
import ssl
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

from ..db import DB_PATH, get_config, set_config, store_message
from .base import Backend


class EmailBackend(Backend):
    name = "email"

    # ── Account management ────────────────────────────────────────────────────

    def _load_accounts(self, db: sqlite3.Connection) -> list[dict]:
        raw = get_config(db, "email_accounts")
        return json.loads(raw) if raw else []

    def _save_accounts(self, db: sqlite3.Connection, accts: list[dict]) -> None:
        set_config(db, "email_accounts", json.dumps(accts))

    def accounts(self) -> list[dict]:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        db.close()
        return [
            {**{k: v for k, v in a.items() if k != "password"}, "account": a["email"]}
            for a in accts
        ]

    def add_account(self, acct: dict) -> None:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        accts = [a for a in accts if a["email"] != acct["email"]]
        accts.append(acct)
        self._save_accounts(db, accts)
        db.close()
        print(f"Account saved: {acct['email']}")

    def remove_account(self, email: str) -> None:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        before = len(accts)
        accts = [a for a in accts if a["email"] != email]
        if len(accts) == before:
            print(f"Account not found: {email}")
        else:
            self._save_accounts(db, accts)
            print(f"Account removed: {email}")
        db.close()

    def get_account_config(self, email: str | None) -> dict | None:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        db.close()
        if not accts:
            return None
        if email:
            matches = [a for a in accts if a["email"] == email]
            return matches[0] if matches else None
        return accts[0] if len(accts) == 1 else None

    def list_folders(self, acct: dict) -> list[str]:
        conn = self._imap_connect(acct)
        try:
            _, folder_list = conn.list()
            folders = []
            for item in folder_list:
                if item:
                    parts = item.decode().split('"')
                    name = parts[-2] if len(parts) >= 2 else item.decode().split()[-1]
                    folders.append(name)
            return folders
        finally:
            conn.logout()

    # ── CLI ───────────────────────────────────────────────────────────────────

    def register_commands(self, subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser("email", help="Email backend commands")
        es = p.add_subparsers(dest="email_command")

        add = es.add_parser("add", help="Add or update an email account")
        add.add_argument("--email", required=True)
        add.add_argument("--password", required=True)
        add.add_argument("--imap-host", required=True)
        add.add_argument("--imap-port", type=int, required=True)
        add.add_argument("--imap-ssl", choices=["true", "false"], default="false")
        add.add_argument("--imap-starttls", choices=["true", "false"], default="true")
        add.add_argument("--smtp-host", required=True)
        add.add_argument("--smtp-port", type=int, required=True)
        add.add_argument("--smtp-ssl", choices=["true", "false"], default="false")
        add.add_argument("--smtp-tls", choices=["true", "false"], default="true")
        add.add_argument("--poll-folders", default="INBOX")

        rm = es.add_parser("remove", help="Remove an email account")
        rm.add_argument("--email", required=True)

        es.add_parser("list", help="List configured email accounts")

    def handle_command(self, args: argparse.Namespace) -> bool:
        if args.command != "email":
            return False
        if args.email_command == "add":
            self.add_account({
                "email":         args.email,
                "password":      args.password,
                "imap_host":     args.imap_host,
                "imap_port":     str(args.imap_port),
                "imap_ssl":      args.imap_ssl,
                "imap_starttls": args.imap_starttls,
                "smtp_host":     args.smtp_host,
                "smtp_port":     str(args.smtp_port),
                "smtp_ssl":      args.smtp_ssl,
                "smtp_tls":      args.smtp_tls,
                "poll_folders":  args.poll_folders,
            })
        elif args.email_command == "remove":
            self.remove_account(args.email)
        elif args.email_command == "list":
            accts = self.accounts()
            if not accts:
                print("No email accounts configured.")
            for a in accts:
                print(f"  {a['account']}  IMAP {a['imap_host']}:{a['imap_port']}"
                      f"  SMTP {a['smtp_host']}:{a['smtp_port']}"
                      f"  folders={a.get('poll_folders', 'INBOX')}")
        else:
            print("Usage: messaging-daemon email [add|remove|list]")
        return True

    # ── Recipient helpers ─────────────────────────────────────────────────────

    def is_self(self, account: str, recipient: str) -> bool:
        return recipient.strip().lower() == account.strip().lower()

    def resolve_display_name(self, account: str, recipient: str) -> str:
        return recipient

    # ── IMAP helpers ──────────────────────────────────────────────────────────

    def _imap_connect(self, acct: dict) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        host = acct["imap_host"]
        port = int(acct["imap_port"])
        use_ssl = acct.get("imap_ssl", "true").lower() == "true"

        if use_ssl:
            if host in ("127.0.0.1", "localhost"):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
            else:
                conn = imaplib.IMAP4_SSL(host, port)
        else:
            conn = imaplib.IMAP4(host, port)
            if acct.get("imap_starttls", "true").lower() == "true":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                conn.starttls(ssl_context=ctx)

        conn.login(acct["email"], acct["password"])
        return conn

    @staticmethod
    def _decode_header(raw: str | bytes | None) -> str:
        if raw is None:
            return ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        parts = decode_header(raw)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                for enc in [charset, "utf-8", "latin-1"]:
                    if not enc:
                        continue
                    try:
                        decoded.append(part.decode(enc, errors="replace"))
                        break
                    except (LookupError, TypeError):
                        continue
                else:
                    decoded.append(part.decode("latin-1", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    @staticmethod
    def _decode_payload(payload: bytes, charset: str | None) -> str:
        for enc in [charset, "utf-8", "latin-1"]:
            if not enc:
                continue
            try:
                return payload.decode(enc, errors="replace")
            except (LookupError, TypeError):
                continue
        return payload.decode("latin-1", errors="replace")

    @staticmethod
    def _get_plain_body(msg: email_lib.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    if "attachment" in str(part.get("Content-Disposition", "")):
                        continue
                    payload = part.get_payload(decode=True)
                    if payload:
                        return EmailBackend._decode_payload(payload, part.get_content_charset())
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return EmailBackend._decode_payload(payload, msg.get_content_charset())
        return ""

    @staticmethod
    def _parse_timestamp(date_str: str) -> int | None:
        if not date_str:
            return None
        try:
            dt = parsedate_to_datetime(date_str)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    # ── Sending ───────────────────────────────────────────────────────────────

    def send(self, account: str, recipient: str, body: str, subject: str | None = None) -> None:
        acct = self.get_account_config(account)
        if not acct:
            raise RuntimeError(f"No config found for account {account}")

        host = acct["smtp_host"]
        port = int(acct["smtp_port"])
        use_ssl = acct.get("smtp_ssl", "false").lower() == "true"
        use_tls = acct.get("smtp_tls", "true").lower() == "true"

        msg = MIMEMultipart("alternative")
        msg["From"] = account
        msg["To"] = recipient
        msg["Subject"] = subject or "(no subject)"
        msg.attach(MIMEText(body, "plain"))

        if use_ssl:
            if host in ("127.0.0.1", "localhost"):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                smtp = smtplib.SMTP_SSL(host, port, context=ctx)
            else:
                smtp = smtplib.SMTP_SSL(host, port)
        else:
            smtp = smtplib.SMTP(host, port)
            if use_tls:
                smtp.starttls()

        with smtp:
            smtp.login(account, acct["password"])
            smtp.sendmail(account, recipient, msg.as_string())

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll_account_folder(self, db: sqlite3.Connection, acct: dict, folder: str) -> int:
        conn = self._imap_connect(acct)
        try:
            conn.login(acct["email"], acct["password"])  # Authenticate before selecting folder
            conn.select(f'"{folder}"')
            _, data = conn.uid("search", None, "ALL")
            uids = (data[0].split() if data[0] else [])[-100:]

            known = {
                row[0]
                for row in db.execute(
                    "SELECT uid FROM messages WHERE backend = ? AND account = ?",
                    (self.name, acct["email"]),
                ).fetchall()
            }

            count = 0
            for uid_bytes in uids:
                uid = uid_bytes.decode()
                full_uid = f"{folder}:{uid}"
                if full_uid in known:
                    continue

                _, msg_data = conn.uid("fetch", uid_bytes, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                parsed = email_lib.message_from_bytes(raw)

                msg = {
                    "backend":      self.name,
                    "account":      acct["email"],
                    "uid":          full_uid,
                    "sender":       self._decode_header(parsed.get("From")),
                    "sender_name":  None,
                    "recipient":    self._decode_header(parsed.get("To")),
                    "subject":      self._decode_header(parsed.get("Subject")),
                    "body":         self._get_plain_body(parsed),
                    "thread_id":    parsed.get("In-Reply-To") or parsed.get("Message-ID"),
                    "timestamp_ms": self._parse_timestamp(parsed.get("Date", "")),
                    "metadata":     {"folder": folder, "message_id": parsed.get("Message-ID", "")},
                }
                if store_message(db, msg):
                    count += 1
            return count
        finally:
            conn.logout()

    def poll(self, db: sqlite3.Connection) -> int:
        db_conn = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db_conn)
        db_conn.close()

        if not accts:
            print(f"  [email] No accounts configured — skipping poll.")
            return 0

        total = 0
        for acct in accts:
            folders = acct.get("poll_folders", "INBOX").split(",")
            for folder in folders:
                folder = folder.strip()
                try:
                    n = self._poll_account_folder(db, acct, folder)
                    if n:
                        print(f"  [email] {acct['email']} / {folder}: {n} new")
                    total += n
                except Exception as exc:
                    print(f"  [email] Error polling {acct['email']} / {folder}: {exc}")
        return total

    # ── Confirmation page fields ──────────────────────────────────────────────

    def confirmation_fields(self, account, recipient, body, subject):
        fields = [
            ("From", account),
            ("To", recipient),
        ]
        if subject:
            fields.append(("Subject", subject))
        fields.append(("Body", body))
        return fields
