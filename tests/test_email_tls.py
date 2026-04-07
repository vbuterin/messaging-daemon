import ssl
import unittest
from unittest.mock import MagicMock, patch

from messaging_daemon.backends.email import EmailBackend


class EmailBackendTLSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = EmailBackend()

    def assert_verified_context(self, ctx: ssl.SSLContext) -> None:
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def assert_unverified_context(self, ctx: ssl.SSLContext) -> None:
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)

    @patch("messaging_daemon.backends.email.imaplib.IMAP4_SSL")
    def test_imap_ssl_verifies_remote_certificates(self, mock_imap_ssl: MagicMock) -> None:
        self.backend._imap_connect({
            "imap_host": "imap.example.com",
            "imap_port": "993",
            "imap_ssl": "true",
        })

        _, kwargs = mock_imap_ssl.call_args
        self.assert_verified_context(kwargs["ssl_context"])

    @patch("messaging_daemon.backends.email.imaplib.IMAP4_SSL")
    def test_imap_ssl_skips_verification_for_loopback(self, mock_imap_ssl: MagicMock) -> None:
        self.backend._imap_connect({
            "imap_host": "127.0.0.1",
            "imap_port": "1143",
            "imap_ssl": "true",
        })

        _, kwargs = mock_imap_ssl.call_args
        self.assert_unverified_context(kwargs["ssl_context"])

    @patch("messaging_daemon.backends.email.imaplib.IMAP4")
    def test_imap_starttls_verifies_remote_certificates(self, mock_imap4: MagicMock) -> None:
        conn = MagicMock()
        mock_imap4.return_value = conn

        self.backend._imap_connect({
            "imap_host": "imap.example.com",
            "imap_port": "143",
            "imap_ssl": "false",
            "imap_starttls": "true",
        })

        _, kwargs = conn.starttls.call_args
        self.assert_verified_context(kwargs["ssl_context"])

    @patch("messaging_daemon.backends.email.imaplib.IMAP4")
    def test_imap_starttls_skips_verification_for_ipv6_loopback(self, mock_imap4: MagicMock) -> None:
        conn = MagicMock()
        mock_imap4.return_value = conn

        self.backend._imap_connect({
            "imap_host": "::1",
            "imap_port": "143",
            "imap_ssl": "false",
            "imap_starttls": "true",
        })

        _, kwargs = conn.starttls.call_args
        self.assert_unverified_context(kwargs["ssl_context"])

    @patch("messaging_daemon.backends.email.smtplib.SMTP_SSL")
    def test_smtp_ssl_verifies_remote_certificates(self, mock_smtp_ssl: MagicMock) -> None:
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        mock_smtp_ssl.return_value = smtp

        with patch.object(self.backend, "get_account_config", return_value={
            "email": "bob@example.com",
            "password": "secret",
            "smtp_host": "smtp.example.com",
            "smtp_port": "465",
            "smtp_ssl": "true",
            "smtp_tls": "false",
        }):
            self.backend.send("bob@example.com", "alice@example.com", "hello", "subject")

        _, kwargs = mock_smtp_ssl.call_args
        self.assert_verified_context(kwargs["context"])

    @patch("messaging_daemon.backends.email.smtplib.SMTP_SSL")
    def test_smtp_ssl_skips_verification_for_loopback(self, mock_smtp_ssl: MagicMock) -> None:
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        mock_smtp_ssl.return_value = smtp

        with patch.object(self.backend, "get_account_config", return_value={
            "email": "bob@example.com",
            "password": "secret",
            "smtp_host": "localhost",
            "smtp_port": "1025",
            "smtp_ssl": "true",
            "smtp_tls": "false",
        }):
            self.backend.send("bob@example.com", "alice@example.com", "hello", "subject")

        _, kwargs = mock_smtp_ssl.call_args
        self.assert_unverified_context(kwargs["context"])

    @patch("messaging_daemon.backends.email.smtplib.SMTP")
    def test_smtp_starttls_verifies_remote_certificates(self, mock_smtp: MagicMock) -> None:
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        mock_smtp.return_value = smtp

        with patch.object(self.backend, "get_account_config", return_value={
            "email": "bob@example.com",
            "password": "secret",
            "smtp_host": "smtp.example.com",
            "smtp_port": "587",
            "smtp_ssl": "false",
            "smtp_tls": "true",
        }):
            self.backend.send("bob@example.com", "alice@example.com", "hello", "subject")

        _, kwargs = smtp.starttls.call_args
        self.assert_verified_context(kwargs["context"])

    @patch("messaging_daemon.backends.email.smtplib.SMTP")
    def test_smtp_starttls_skips_verification_for_loopback(self, mock_smtp: MagicMock) -> None:
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        mock_smtp.return_value = smtp

        with patch.object(self.backend, "get_account_config", return_value={
            "email": "bob@example.com",
            "password": "secret",
            "smtp_host": "127.0.0.1",
            "smtp_port": "1025",
            "smtp_ssl": "false",
            "smtp_tls": "true",
        }):
            self.backend.send("bob@example.com", "alice@example.com", "hello", "subject")

        _, kwargs = smtp.starttls.call_args
        self.assert_unverified_context(kwargs["context"])
