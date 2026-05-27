# messaging-daemon

A unified local daemon that polls Signal, email, and Telegram, stores all messages in a single SQLite database, and exposes them through a simple HTTP API on `localhost:6000`.

Designed to give AI agents and local software safe, structured access to your messages. Reading messages is immediate. Sending to yourself is immediate. Sending to anyone else requires explicit human approval through a confirmation page on `localhost:7000` — keeping untrusted software from sending messages on your behalf without your knowledge.

The recommended usage is to run AI agents and other untrusted software from inside a sandbox (eg. bwrap), and give the sandbox access to port 6000 and other needed ports (eg. localhost LLM provider port) but NOT port 7000.

**Backends supported:**
- **Signal** via `signal-cli`
- **Email** via IMAP/SMTP (tested with Protonmail Bridge; works with any standard provider)
- **Telegram** via Telethon (MTProto user client)

## Installation (NixOS)

Add `messaging-daemon.nix` to your imports and rebuild:

```nix
imports = [
  ./messaging-daemon.nix
  ./protonmail-bridge.nix  # if using Protonmail
];
```

## Documentation

See [SKILL.md](./SKILL.md) for the full HTTP API reference, query examples, timestamp utilities, and step-by-step setup instructions for all backends.
