"""
backends/telegram.py — Telegram backend via Telethon (MTProto user client).

Multiple accounts supported. Configs stored as JSON list in DB under
the key 'telegram_accounts'. Each entry has:
    phone, api_id, api_hash, session (StringSession), dialog_limit

CLI:
    messaging-daemon telegram add \\
        --phone +1XXXXXXXXXX --api-id NNN --api-hash XXX
    messaging-daemon telegram remove --phone +1XXXXXXXXXX
    messaging-daemon telegram list

`add` runs an interactive login (code over Telegram + optional 2FA password)
and stores the resulting StringSession in the DB.
"""

import argparse
import asyncio
import json
import sqlite3
import threading
from getpass import getpass

from ..db import DB_PATH, get_config, set_config, store_message, now_ms
from .base import Backend

DEFAULT_DIALOG_LIMIT = 50      # dialogs to scan per poll (most recently active)
DEFAULT_MESSAGE_LIMIT = 30     # messages to fetch per dialog per poll


# ── Async runner ──────────────────────────────────────────────────────────────
# Telethon is async. The Backend interface is sync (called from inside another
# asyncio loop in poll_loop). We give Telethon its own thread+loop and submit
# coroutines via run_coroutine_threadsafe so neither side has to know about the
# other.

class _AsyncRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, daemon=True, name="telegram-loop"
            )
            self._thread.start()
            return self._loop

    def run(self, coro):
        loop = self._ensure()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()


_runner = _AsyncRunner()


# ── Backend ───────────────────────────────────────────────────────────────────

class TelegramBackend(Backend):
    name = "telegram"

    def __init__(self) -> None:
        self._clients: dict[str, object] = {}  # phone -> TelegramClient
        self._clients_lock = threading.Lock()
        # phone -> {"id": int, "username": str|None}; populated on first client use
        # Written from the Telethon runner thread, read from the API/confirm/poll
        # threads, so all access goes through _me_cache_lock.
        self._me_cache: dict[str, dict] = {}
        self._me_cache_lock = threading.Lock()

    def _get_me(self, phone: str) -> dict | None:
        with self._me_cache_lock:
            entry = self._me_cache.get(phone)
            return dict(entry) if entry else None

    def _set_me(self, phone: str, me: dict) -> None:
        with self._me_cache_lock:
            self._me_cache[phone] = me

    # ── Account management ────────────────────────────────────────────────────

    def _load_accounts(self, db: sqlite3.Connection) -> list[dict]:
        raw = get_config(db, "telegram_accounts")
        return json.loads(raw) if raw else []

    def _save_accounts(self, db: sqlite3.Connection, accts: list[dict]) -> None:
        set_config(db, "telegram_accounts", json.dumps(accts))

    def accounts(self) -> list[dict]:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        db.close()
        return [
            {
                "account":       a["phone"],
                "phone":         a["phone"],
                "api_id":        a["api_id"],
                "dialog_limit":  a.get("dialog_limit", DEFAULT_DIALOG_LIMIT),
                "message_limit": a.get("message_limit", DEFAULT_MESSAGE_LIMIT),
            }
            for a in accts
        ]

    def _get_account_config(self, phone: str | None) -> dict | None:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        db.close()
        if not accts:
            return None
        if phone:
            for a in accts:
                if a["phone"] == phone:
                    return a
            return None
        return accts[0] if len(accts) == 1 else None

    def add_account(self, acct: dict) -> None:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        accts = [a for a in accts if a["phone"] != acct["phone"]]
        accts.append(acct)
        self._save_accounts(db, accts)
        db.close()
        print(f"Telegram account saved: {acct['phone']}")

    def remove_account(self, phone: str) -> None:
        db = sqlite3.connect(DB_PATH)
        accts = self._load_accounts(db)
        before = len(accts)
        accts = [a for a in accts if a["phone"] != phone]
        if len(accts) == before:
            print(f"Account not found: {phone}")
        else:
            self._save_accounts(db, accts)
            print(f"Account removed: {phone}")
        db.close()
        with self._clients_lock:
            self._clients.pop(phone, None)

    # ── CLI ───────────────────────────────────────────────────────────────────

    def register_commands(self, subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser("telegram", help="Telegram backend commands")
        ts = p.add_subparsers(dest="telegram_command")

        add = ts.add_parser("add", help="Interactively log in and save a Telegram account")
        add.add_argument("--phone", required=True, help="E.164 phone number, e.g. +1XXXXXXXXXX")
        add.add_argument("--api-id", required=True, type=int,
                         help="api_id from https://my.telegram.org/apps")
        add.add_argument("--api-hash", required=True,
                         help="api_hash from https://my.telegram.org/apps")
        add.add_argument("--dialog-limit", type=int, default=DEFAULT_DIALOG_LIMIT,
                         help=f"Dialogs scanned per poll (default {DEFAULT_DIALOG_LIMIT})")
        add.add_argument("--message-limit", type=int, default=DEFAULT_MESSAGE_LIMIT,
                         help=f"Messages fetched per dialog per poll (default {DEFAULT_MESSAGE_LIMIT})")

        rm = ts.add_parser("remove", help="Remove a Telegram account")
        rm.add_argument("--phone", required=True)

        ts.add_parser("list", help="List configured Telegram accounts")

    def handle_command(self, args: argparse.Namespace) -> bool:
        if args.command != "telegram":
            return False
        if args.telegram_command == "add":
            session_str = self._interactive_login(
                phone=args.phone, api_id=args.api_id, api_hash=args.api_hash,
            )
            self.add_account({
                "phone":         args.phone,
                "api_id":        args.api_id,
                "api_hash":      args.api_hash,
                "session":       session_str,
                "dialog_limit":  args.dialog_limit,
                "message_limit": args.message_limit,
            })
        elif args.telegram_command == "remove":
            self.remove_account(args.phone)
        elif args.telegram_command == "list":
            accts = self.accounts()
            if not accts:
                print("No Telegram accounts configured.")
            for a in accts:
                print(f"  {a['phone']}  api_id={a['api_id']}"
                      f"  dialog_limit={a['dialog_limit']}"
                      f"  message_limit={a['message_limit']}")
        else:
            print("Usage: messaging-daemon telegram [add|remove|list]")
        return True

    # ── Telethon plumbing ─────────────────────────────────────────────────────

    def _import_telethon(self):
        try:
            from telethon import TelegramClient                    # noqa: F401
            from telethon.sessions import StringSession             # noqa: F401
            from telethon.errors import SessionPasswordNeededError  # noqa: F401
            import telethon
            return telethon
        except ImportError as exc:
            raise RuntimeError(
                "telethon is not installed. Install with: pip install telethon"
            ) from exc

    def _interactive_login(self, phone: str, api_id: int, api_hash: str) -> str:
        """Run interactive login on the runner thread; return StringSession."""
        self._import_telethon()
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import SessionPasswordNeededError

        async def go() -> str:
            client = TelegramClient(StringSession(), api_id, api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(phone)
                # Read from stdin in a worker thread so the runner loop
                # doesn't block while waiting on user input.
                code = await asyncio.to_thread(
                    input, f"Enter the login code Telegram sent to {phone}: "
                )
                try:
                    await client.sign_in(phone=phone, code=code.strip())
                except SessionPasswordNeededError:
                    password = await asyncio.to_thread(
                        getpass, "Two-step verification password: "
                    )
                    await client.sign_in(password=password)
            session_str = client.session.save()
            await client.disconnect()
            return session_str

        return _runner.run(go())

    async def _client_for(self, acct: dict):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        phone = acct["phone"]
        with self._clients_lock:
            client = self._clients.get(phone)
        if client is not None and client.is_connected():
            return client

        client = TelegramClient(
            StringSession(acct.get("session") or ""),
            int(acct["api_id"]),
            acct["api_hash"],
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(
                f"Telegram session for {phone} is not authorized. "
                f"Re-run: messaging-daemon telegram add --phone {phone} ..."
            )
        me = await client.get_me()
        self._set_me(phone, {
            "id":       getattr(me, "id", None),
            "username": (getattr(me, "username", None) or "").lower() or None,
        })
        with self._clients_lock:
            self._clients[phone] = client
        return client

    async def _drop_client(self, phone: str) -> None:
        """Disconnect and forget the cached client so the next call reconnects."""
        with self._clients_lock:
            client = self._clients.pop(phone, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    # ── Recipient helpers ─────────────────────────────────────────────────────

    SELF_ALIASES = {"me", "self", "saved", "saved_messages"}

    def is_self(self, account: str, recipient: str) -> bool:
        r = recipient.strip().lower()
        if r in self.SELF_ALIASES:
            return True
        if r == account.strip().lower():
            return True
        me = self._get_me(account)
        if me:
            if me.get("username") and r.lstrip("@") == me["username"]:
                return True
            if me.get("id") is not None and r == str(me["id"]):
                return True
        return False

    def _resolve_entity_target(self, recipient: str):
        """Map a recipient string into an argument suitable for client.get_entity."""
        r = recipient.strip()
        if r.lower() in self.SELF_ALIASES:
            return "me"
        if r.startswith("@"):
            return r
        if r.startswith("+"):
            return r
        # Numeric chat/user id (Telethon accepts ints)
        try:
            return int(r)
        except ValueError:
            return r  # username without @, or invite link / title

    def resolve_display_name(self, account: str, recipient: str) -> str:
        acct = self._get_account_config(account)
        if not acct:
            return recipient
        try:
            return _runner.run(self._resolve_display_name_async(acct, recipient))
        except Exception:
            return recipient

    async def _resolve_display_name_async(self, acct: dict, recipient: str) -> str:
        client = await self._client_for(acct)
        target = self._resolve_entity_target(recipient)
        try:
            entity = await client.get_entity(target)
        except Exception:
            return recipient
        return self._entity_display(entity) or recipient

    @staticmethod
    def _entity_display(entity) -> str:
        # User
        first = getattr(entity, "first_name", None) or ""
        last = getattr(entity, "last_name", None) or ""
        full = (first + " " + last).strip()
        if full:
            return full
        # Chat / Channel
        title = getattr(entity, "title", None)
        if title:
            return title
        username = getattr(entity, "username", None)
        if username:
            return f"@{username}"
        return ""

    # ── Sending ───────────────────────────────────────────────────────────────

    def send(self, account: str, recipient: str, body: str, subject: str | None = None) -> None:
        acct = self._get_account_config(account)
        if not acct:
            raise RuntimeError(f"No Telegram config found for {account}")
        _runner.run(self._send_async(acct, recipient, body))

    async def _send_async(self, acct: dict, recipient: str, body: str) -> None:
        target = self._resolve_entity_target(recipient)
        try:
            client = await self._client_for(acct)
            await client.send_message(target, body)
        except (ConnectionError, OSError):
            await self._drop_client(acct["phone"])
            client = await self._client_for(acct)
            await client.send_message(target, body)

    # ── Polling ───────────────────────────────────────────────────────────────

    def poll(self) -> int:
        db = sqlite3.connect(DB_PATH)
        try:
            accts = self._load_accounts(db)
        finally:
            db.close()
        if not accts:
            print("  [telegram] No accounts configured — skipping poll.")
            return 0

        total = 0
        for acct in accts:
            try:
                n = _runner.run(self._poll_account(acct))
                if n:
                    print(f"  [telegram] {acct['phone']}: {n} new")
                total += n
            except (ConnectionError, OSError) as exc:
                print(f"  [telegram] Connection error for {acct['phone']}: {exc} — retrying once")
                try:
                    _runner.run(self._drop_client(acct["phone"]))
                    n = _runner.run(self._poll_account(acct))
                    if n:
                        print(f"  [telegram] {acct['phone']}: {n} new (after reconnect)")
                    total += n
                except Exception as retry_exc:
                    print(f"  [telegram] Retry failed for {acct['phone']}: {retry_exc}")
            except Exception as exc:
                print(f"  [telegram] Error polling {acct['phone']}: {exc}")
        return total

    async def _poll_account(self, acct: dict) -> int:
        # Connections cannot cross threads; open one local to the runner thread.
        db = sqlite3.connect(DB_PATH)
        try:
            return await self._poll_account_inner(db, acct)
        finally:
            db.close()

    async def _poll_account_inner(self, db: sqlite3.Connection, acct: dict) -> int:
        client = await self._client_for(acct)
        my_id = (self._get_me(acct["phone"]) or {}).get("id")

        dialog_limit = int(acct.get("dialog_limit", DEFAULT_DIALOG_LIMIT))
        message_limit = int(acct.get("message_limit", DEFAULT_MESSAGE_LIMIT))

        # Build {chat_id: max_message_id} for what we've already stored.
        # Do the per-thread MAX in SQLite so we don't pull every row into Python.
        # uid format is "{chat_id}:{message_id}"; substr(uid, instr+1) extracts
        # the numeric suffix. Rows without ':' produce 0 from CAST and don't
        # affect the max (real message ids start at 1).
        known_max: dict[str, int] = {
            thread_id: max_id
            for thread_id, max_id in db.execute(
                """SELECT thread_id,
                          MAX(CAST(substr(uid, instr(uid, ':') + 1) AS INTEGER))
                     FROM messages
                    WHERE backend = ? AND account = ? AND thread_id IS NOT NULL
                    GROUP BY thread_id""",
                (self.name, acct["phone"]),
            )
            if max_id is not None
        }

        count = 0
        async for dialog in client.iter_dialogs(limit=dialog_limit):
            chat = dialog.entity
            chat_id = str(getattr(chat, "id", ""))
            if not chat_id:
                continue
            min_id = known_max.get(chat_id, 0)

            try:
                msgs = await client.get_messages(
                    chat, limit=message_limit, min_id=min_id,
                )
            except Exception as exc:
                print(f"  [telegram] {acct['phone']} dialog {chat_id}: {exc}")
                continue

            for m in msgs:
                body = (m.message or "").strip()
                if not body:
                    continue
                sender = await m.get_sender() if m.sender_id else None
                sender_name = self._entity_display(sender) if sender else None
                sender_id = str(m.sender_id) if m.sender_id else None
                ts_ms = int(m.date.timestamp() * 1000) if m.date else now_ms()
                is_group = bool(getattr(chat, "title", None))
                outgoing = bool(getattr(m, "out", False)) or (
                    my_id is not None and m.sender_id == my_id
                )
                recipient = (
                    chat_id if is_group
                    else (acct["phone"] if not outgoing else chat_id)
                )

                msg = {
                    "backend":      self.name,
                    "account":      acct["phone"],
                    "uid":          f"{chat_id}:{m.id}",
                    "sender":       sender_id,
                    "sender_name":  sender_name,
                    "recipient":    recipient,
                    "subject":      None,
                    "body":         body,
                    "thread_id":    chat_id,
                    "timestamp_ms": ts_ms,
                    "metadata": {
                        "chat_id":    chat_id,
                        "chat_title": getattr(chat, "title", None),
                        "outgoing":   outgoing,
                    },
                }
                if store_message(db, msg):
                    count += 1
        return count

    # ── Confirmation page fields ──────────────────────────────────────────────

    def confirmation_fields(self, account, recipient, body, subject):
        return [
            ("From",    account),
            ("To",      self.resolve_display_name(account, recipient)),
            ("Message", body),
        ]
