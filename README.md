# messaging-daemon

A unified local daemon that polls Signal and email, stores all messages in a single SQLite database, and exposes them through a simple HTTP API on `localhost:6000`.

Designed to give AI agents and local software safe, structured access to your messages. Reading messages is immediate. Sending to yourself is immediate. Sending to anyone else requires explicit human approval through a confirmation page on `localhost:7000` — keeping untrusted software from sending messages on your behalf without your knowledge.

The recommended usage is to run AI agents and other untrusted software from inside a sandbox (eg. bwrap), and give the sandbox access to port 6000 and other needed ports (eg. localhost LLM provider port) but NOT port 7000.

**Backends supported:**
- **Signal** via `signal-cli`
- **Email** via IMAP/SMTP (tested with Protonmail Bridge; works with any standard provider)

## Installation

### NixOS

Add `messaging-daemon.nix` to your imports and rebuild:

```nix
imports = [
  ./messaging-daemon.nix
  ./protonmail-bridge.nix  # if using Protonmail
];
```

### macOS

This project is mostly portable Python code and can run on macOS without the NixOS module.

Requirements:
- Python 3.11+
- `signal-cli` installed and already linked or registered
- access to your IMAP/SMTP provider (for Gmail, use an app password)

Install the package locally:

```bash
cd /path/to/messaging-daemon
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run the daemon in the foreground:

```bash
messaging-daemon run
```

Configure Signal:

```bash
signal-cli link -n "my-mac"
messaging-daemon signal setup +1XXXXXXXXXX
```

Configure Gmail:

```bash
messaging-daemon email add \
  --email you@gmail.com \
  --password YOUR_GMAIL_APP_PASSWORD \
  --imap-host imap.gmail.com --imap-port 993 --imap-ssl true \
  --smtp-host smtp.gmail.com --smtp-port 587
```

Check the daemon:

```bash
curl "http://localhost:6000/accounts"
curl "http://localhost:6000/status"
```

Optional: to keep it running in the background on macOS, create a `launchd` agent that starts `python -m messaging_daemon run` or the installed `messaging-daemon run` command.

## Documentation

See [SKILL.md](./SKILL.md) for the full HTTP API reference, query examples, timestamp utilities, and step-by-step setup instructions for both backends.
