---
name: messaging-daemon
description: "Use this skill whenever you need to read or send messages via Signal or email. Triggers include: reading recent Signal messages or emails, querying messages by sender/subject/group, sending a message or email to self or others, listing email folders, or any interaction with the local messaging-daemon running on port 6000. If accounts are not yet configured, this skill explains how to set them up from scratch."
---

# Messaging Daemon HTTP API

## Overview

A unified local daemon runs on `http://localhost:6000` and handles both Signal and email. All messages — regardless of backend — are stored in a single SQLite database and queryable through the same endpoints.

Two HTTP servers run:
- **Port 6000** — main API (safe to expose to untrusted software)
- **Port 7000** — confirmation UI (human-facing only; keep away from untrusted software)

**Important:** Sending to self completes immediately on both backends. Sending to anyone else returns a `confirm_url` that **must be shown to the user** — the message is not sent until the user opens that URL in their browser and clicks "Send".

> **Warning:** Message responses can be very long. In many circumstances it is more efficient to save the output to a file and then search or chunk it rather than reading it directly:
> ```bash
> curl "http://localhost:6000/messages" > /tmp/messages.json
> cat /tmp/messages.json | python3 -c "import json,sys; msgs = json.load(sys.stdin)['messages']; [print(m['timestamp_ms'], m['backend'], m['sender'], m.get('subject',''), m['body'][:60]) for m in msgs]"
> ```

---

## Checking What's Configured

Before doing anything, check what backends and accounts are active:

```bash
curl "http://localhost:6000/accounts"
```

```json
{
  "signal": [{"account": "+1234567890", "backend": "signal"}],
  "email":  [{"account": "bob@proton.me", "imap_host": "127.0.0.1", ...}]
}
```

If either backend shows an empty list, setup is required. **Do not attempt to run setup commands yourself.** Instead, present the relevant instructions from the Setup section below to the user and ask them to run the commands in their own terminal. Setup requires interactive steps (entering passwords, receiving SMS codes) that cannot be done from within an agent sandbox.

---

## Setup (instructions to give to the user — do not run these yourself)

> **Agent note:** If setup is needed, copy the relevant instructions below and present them to the user. Remind them that these commands must be run in their own terminal, not inside any sandbox or agent environment. Wait for them to confirm setup is complete before proceeding.
>
> **Platform note:** The original service-management examples below assume NixOS/systemd. On macOS, use the same `messaging-daemon ...` account-setup commands, but run the daemon directly (`messaging-daemon run`) or via `launchd` instead of `systemctl`.

---

### Setting up Signal

Ask the user to run the following in their terminal:

**Step 1 — Ensure signal-cli is installed**

On NixOS, add `pkgs.signal-cli` to `environment.systemPackages` and rebuild. On other systems, download from https://github.com/AsamK/signal-cli/releases.

**Step 2 — Register or link with Signal** (only needed once per machine)

To register a new number:
```bash
signal-cli -a +1XXXXXXXXXX register
signal-cli -a +1XXXXXXXXXX verify THE-CODE-FROM-SMS
```

Or to link to an existing Signal account on another device:
```bash
signal-cli link -n "my-computer"
# scan the QR code / open the URL on your phone
```

**Step 3 — Register the account with the daemon**
```bash
sudo -u messaging-daemon messaging-daemon signal setup +1XXXXXXXXXX
```

**Step 4 — Restart the daemon**
```bash
sudo systemctl restart messaging-daemon
```

---

### Setting up Email (Protonmail)

Protonmail requires **Protonmail Bridge** running locally. Standard providers (Gmail, Fastmail, etc.) can skip to Step 3.

Ask the user to run the following in their terminal:

**Step 1 — Ensure Protonmail Bridge is running**

Bridge runs as a user service (it needs access to the desktop keychain). Check if it's already running:
```bash
systemctl --user status protonmail-bridge
```

If it's not running, start it:
```bash
systemctl --user enable protonmail-bridge
systemctl --user start protonmail-bridge
```

**Step 2 — Log in to Bridge and get the bridge password**

The bridge password is NOT your Protonmail account password — Bridge generates a separate one. Stop Bridge temporarily to open the CLI:
```bash
systemctl --user stop protonmail-bridge
protonmail-bridge --cli
>>> login          # enter your Protonmail email and account password
>>> login          # repeat for each additional account
>>> info 0         # shows IMAP/SMTP credentials including the bridge password
>>> info 1         # repeat for each account
>>> exit
systemctl --user start protonmail-bridge
```

**Step 3 — Add the email account to the daemon**

For Protonmail via Bridge:
```bash
sudo -u messaging-daemon messaging-daemon email add \
  --email you@proton.me \
  --password BRIDGE_PASSWORD_FROM_STEP_2 \
  --imap-host 127.0.0.1 --imap-port 1143 --imap-ssl false \
  --smtp-host 127.0.0.1 --smtp-port 1025
```

For standard IMAP providers (Gmail, Fastmail, etc.):
```bash
sudo -u messaging-daemon messaging-daemon email add \
  --email you@gmail.com \
  --password YOUR_APP_PASSWORD \
  --imap-host imap.gmail.com --imap-port 993 --imap-ssl true \
  --smtp-host smtp.gmail.com --smtp-port 587
```

**Step 4 — Confirm Protonmail Bridge is running, then restart the messaging daemon**

Bridge must be running before the daemon can poll email:
```bash
systemctl --user status protonmail-bridge   # should show active (running)
sudo systemctl restart messaging-daemon
```

**To remove an email account:**
```bash
sudo -u messaging-daemon messaging-daemon email remove --email you@proton.me
sudo systemctl restart messaging-daemon
```

---

## Reading Messages

All `/messages` queries work across both backends unless filtered with `?backend=`.

### All recent messages (most recent 100, all backends)
```bash
curl "http://localhost:6000/messages"
```

### Filter by backend
```bash
curl "http://localhost:6000/messages?backend=signal"
curl "http://localhost:6000/messages?backend=email"
```

### Filter by account
```bash
curl --get "http://localhost:6000/messages" --data-urlencode "account=bob@proton.me"
curl --get "http://localhost:6000/messages" --data-urlencode "account=+1234567890"
```

### Messages from the last hour
```bash
curl --get "http://localhost:6000/messages" \
  --data-urlencode "since=$(python3 -c 'import time; print(int(time.time()*1000) - 3600000)')"
```

### Messages from the last 24 hours
```bash
curl --get "http://localhost:6000/messages" \
  --data-urlencode "since=$(python3 -c 'import time; print(int(time.time()*1000) - 86400000)')"
```

### Filter by sender (substring match, works for both email addresses and Signal names)
```bash
curl --get "http://localhost:6000/messages" --data-urlencode "sender=alice"
curl --get "http://localhost:6000/messages" --data-urlencode "sender=alice@example.com"
curl --get "http://localhost:6000/messages" --data-urlencode "sender=+1987654321"
```

### Filter by subject (email only; null for Signal messages)
```bash
curl --get "http://localhost:6000/messages" --data-urlencode "subject=invoice"
```

### Filter by thread (group ID for Signal, Message-ID thread for email)
```bash
curl --get "http://localhost:6000/messages" \
  --data-urlencode "thread_id=AfL/co87TsyfTv4FqgJfcF6rNWoRkO2CYLybn83tfTU="
```

### Limit results
```bash
curl "http://localhost:6000/messages?limit=10"
```

### Between two timestamps (milliseconds since epoch)
```bash
curl "http://localhost:6000/messages?since=1774390000000&until=1774399000000"
```

### Response format
```json
{
  "count": 2,
  "messages": [
    {
      "id": 1,
      "backend": "email",
      "account": "bob@proton.me",
      "uid": "INBOX:1042",
      "sender": "Alice <alice@example.com>",
      "sender_name": null,
      "recipient": "bob@proton.me",
      "subject": "Hello there",
      "body": "Hi Bob, just checking in...",
      "thread_id": "<abc123@mail.proton.me>",
      "timestamp_ms": 1774397441614,
      "received_at": 1774397500000,
      "metadata": {"folder": "INBOX", "message_id": "<abc123@mail.proton.me>"}
    },
    {
      "id": 2,
      "backend": "signal",
      "account": "+1234567890",
      "uid": "1774397441614_+1987654321",
      "sender": "+1987654321",
      "sender_name": "Alice",
      "recipient": "+1234567890",
      "subject": null,
      "body": "Hey, just checking in!",
      "thread_id": null,
      "timestamp_ms": 1774397441614,
      "received_at": 1774397500000,
      "metadata": {"envelope": "..."}
    }
  ]
}
```

### Notes
- `subject` is null for Signal messages
- `thread_id` is the Signal group ID for group messages, null for direct messages
- `sender_name` is the Signal display name where available, null for email
- `timestamp_ms` and `received_at` are both milliseconds since Unix epoch
- Results are ordered by `timestamp_ms` descending (newest first)

---

## Sending Messages

### Signal — send to self (no confirmation required)
```bash
curl --get "http://localhost:6000/send" \
  --data-urlencode "backend=signal" \
  --data-urlencode "to=+1234567890" \
  --data-urlencode "body=Hello from the daemon"
```

### Signal — send to another person, group, or username (confirmation required)

The `to` parameter accepts:

| Format | Example | Description |
|--------|---------|-------------|
| Phone number | `+1987654321` | Must start with `+` |
| Group ID | `AfL/co87Ts...` | Base64 string from `thread_id` in /messages |
| Signal username | `alice.01` | Contains a dot, short length |

```bash
# To a phone number
curl --get "http://localhost:6000/send" \
  --data-urlencode "backend=signal" \
  --data-urlencode "to=+1987654321" \
  --data-urlencode "body=Hello Alice"

# To a group
curl --get "http://localhost:6000/send" \
  --data-urlencode "backend=signal" \
  --data-urlencode "to=AfL/co87TsyfTv4FqgJfcF6rNWoRkO2CYLybn83tfTU=" \
  --data-urlencode "body=Hello everyone"
```

### Email — send to self (no confirmation required)
```bash
curl --get "http://localhost:6000/send" \
  --data-urlencode "backend=email" \
  --data-urlencode "from=bob@proton.me" \
  --data-urlencode "to=bob@proton.me" \
  --data-urlencode "subject=Note to self" \
  --data-urlencode "body=Remember to do the thing."
```

### Email — send to another person (confirmation required)
```bash
curl --get "http://localhost:6000/send" \
  --data-urlencode "backend=email" \
  --data-urlencode "from=bob@proton.me" \
  --data-urlencode "to=alice@example.com" \
  --data-urlencode "subject=Hello" \
  --data-urlencode "body=Hi Alice, this is Bob."
```

### Response format — sent immediately
```json
{
  "ok": true,
  "backend": "signal",
  "from": "+1234567890",
  "to": "+1234567890"
}
```

### Response format — confirmation required
```json
{
  "pending": true,
  "confirm_url": "http://localhost:7000/confirm?token=...",
  "backend": "email",
  "from": "bob@proton.me",
  "to": "alice@example.com",
  "display_name": "alice@example.com",
  "message": "Open confirm_url in your browser to approve or deny."
}
```

> **When you receive a `pending: true` response, you must present the `confirm_url` to the user.** The message has NOT been sent. The user must open the URL in a browser and click "Send" to approve or "Don't send" to cancel. Do not silently discard the URL.

### Notes
- `backend=` is optional when only one backend has accounts configured
- `from=` is optional for email when only one email account is configured
- `subject=` is email-only; defaults to `(no subject)` if omitted

---

## Checking Daemon Status

```bash
curl "http://localhost:6000/status"
```

```json
{
  "uptime_seconds": 3600,
  "last_poll": "2026-03-29T12:00:00.000000+00:00",
  "message_count": 312,
  "pending_confirmations": 0,
  "backends": {
    "signal": ["+1234567890"],
    "email":  ["bob@proton.me", "alice@proton.me"]
  }
}
```

If `backends.signal` or `backends.email` are empty, no accounts are configured for that backend — present the setup instructions to the user and ask them to run the commands in their own terminal before retrying.

---

## Query Parameter Reference

| Parameter   | Endpoint  | Type   | Description                                                    |
|-------------|-----------|--------|----------------------------------------------------------------|
| `backend`   | /messages | string | Filter by backend: `signal` or `email`                         |
| `account`   | /messages | string | Filter by account (phone number or email address)              |
| `sender`    | /messages | string | Substring match against sender field                           |
| `subject`   | /messages | string | Substring match against subject (email only; null for Signal)  |
| `thread_id` | /messages | string | Exact match: Signal group ID or email thread ID                |
| `since`     | /messages | int    | Start timestamp in milliseconds (inclusive)                    |
| `until`     | /messages | int    | End timestamp in milliseconds (inclusive)                      |
| `limit`     | /messages | int    | Max messages to return (default: 100)                          |
| `backend`   | /send     | string | Which backend to send via (required if both have accounts)     |
| `from`      | /send     | string | Sender account (email only; optional if one account)           |
| `to`        | /send     | string | Recipient (required)                                           |
| `subject`   | /send     | string | Email subject (email only; default: "(no subject)")            |
| `body`      | /send     | string | Message body (required)                                        |

---

## Timestamp Quick Reference

All timestamps in this API are **milliseconds since Unix epoch**.

```bash
# Current time in ms
python3 -c 'import time; print(int(time.time()*1000))'

# 10 minutes ago
python3 -c 'import time; print(int(time.time()*1000) - 600000)'

# 1 hour ago
python3 -c 'import time; print(int(time.time()*1000) - 3600000)'

# 24 hours ago
python3 -c 'import time; print(int(time.time()*1000) - 86400000)'

# 7 days ago
python3 -c 'import time; print(int(time.time()*1000) - 604800000)'

# Start of today (UTC, in ms)
python3 -c 'import time; from datetime import datetime, timezone; t = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0); print(int(t.timestamp()*1000))'
```
