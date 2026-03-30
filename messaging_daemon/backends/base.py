"""
backends/base.py — abstract base class for all messaging backends.

To add a new backend:
  1. Create backends/yourbackend.py implementing Backend
  2. Add it to the BACKENDS dict in __main__.py
  3. Implement register_commands() and handle_command() for any CLI account
     management the backend needs — __main__.py calls these automatically.
"""

import argparse
import sqlite3
from abc import ABC, abstractmethod


class Backend(ABC):

    # Short identifier used as the `backend` column value and CLI/API name.
    # e.g. "signal", "email"
    name: str

    @abstractmethod
    def accounts(self) -> list[dict]:
        """
        Return a list of configured accounts (no secrets).
        Each dict should have at least an 'account' key.
        """

    @abstractmethod
    def is_self(self, account: str, recipient: str) -> bool:
        """
        Return True if recipient refers to the same identity as account
        (i.e. the send should bypass confirmation).
        """

    @abstractmethod
    def resolve_display_name(self, account: str, recipient: str) -> str:
        """
        Return a human-readable name for recipient.
        For phone numbers: return as-is.
        For group IDs: look up group name.
        For email addresses: return as-is.
        Falls back to recipient unchanged if lookup fails.
        """

    @abstractmethod
    def send(self, account: str, recipient: str, body: str, subject: str | None = None) -> None:
        """
        Actually deliver the message. Raises RuntimeError on failure.
        subject is backend-specific (used by email, ignored by signal).
        """

    @abstractmethod
    def poll(self, db: sqlite3.Connection) -> int:
        """
        Fetch new inbound messages and store them via store_message().
        Returns the count of newly stored messages.
        """

    @abstractmethod
    def register_commands(self, subparsers: argparse._SubParsersAction) -> None:
        """
        Add a subparser for this backend's account management commands.
        Called once by __main__.py during parser construction.

        Example — a backend named "widget" might add:
            p = subparsers.add_parser("widget")
            ws = p.add_subparsers(dest="widget_command")
            add = ws.add_parser("add")
            add.add_argument("--token", required=True)
        """

    @abstractmethod
    def handle_command(self, args: argparse.Namespace) -> bool:
        """
        Handle a parsed CLI command for this backend.
        Return True if the command was handled (so __main__ can exit),
        False if args did not match this backend (so __main__ can continue).
        """

    def confirmation_fields(
        self, account: str, recipient: str, body: str, subject: str | None
    ) -> list[tuple[str, str]]:
        """
        Return ordered (label, value) pairs to show on the confirmation page.
        Override to customise. Default shows From / To / Subject (if set) / Body.
        """
        fields = [
            ("From", account),
            ("To", self.resolve_display_name(account, recipient)),
        ]
        if subject:
            fields.append(("Subject", subject))
        fields.append(("Message", body))
        return fields
