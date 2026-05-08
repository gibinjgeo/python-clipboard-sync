# python_clipboard_sync

A lightweight, secure, cross-platform clipboard synchronisation daemon inspired by KDE Connect.

**Supported platforms:** Ubuntu (Wayland), Windows 10, Windows 11

---

## Features

| Feature | Status |
|---|---|
| LAN device discovery (UDP broadcast) | ✅ |
| Secure device pairing (X25519 ECDH) | ✅ |
| Authenticated, encrypted communication | ✅ |
| Clipboard synchronisation (text) | ✅ |
| Clipboard loop prevention | ✅ |
| Ping / pong latency check | ✅ |
| Persistent paired device storage | ✅ |
| Wayland clipboard (wl-copy/wl-paste) | ✅ |
| Windows clipboard (win32/pyperclip) | ✅ |

---

## Architecture

```
python_clipboard_sync/
├── main.py                   ← Entry point, App coordinator
├── config.py                 ← Config + DeviceInfo dataclasses
├── requirements.txt
├── device.json               ← Placeholder (actual file in ~/.python_clipboard_sync/)
│
├── clipboard/
│   ├── backend.py            ← ClipboardBackend ABC, factory, ClipboardMonitor
│   ├── linux_wayland.py      ← wl-paste / wl-copy backend
│   └── windows.py            ← win32clipboard / pyperclip backend
│
├── network/
│   ├── protocol.py           ← Packet dataclass, PacketBuilder, type constants
│   ├── security.py           ← SecurityManager (ECDH, Fernet, HMAC, nonce)
│   ├── transport.py          ← Connection (raw TCP reader/writer wrapper)
│   ├── server.py             ← ClipboardSyncServer + DeviceSession (protocol state machine)
│   ├── client.py             ← connect_to_peer()
│   ├── discovery.py          ← UDP broadcaster + listener
│   └── ping.py               ← PingManager (send/handle ping/pong)
│
├── pairing/
│   └── pair.py               ← PairingManager (ECDH key exchange, state machine)
│
├── storage/
│   └── paired_devices.py     ← PairedDeviceStore (JSON persistence)
│
└── utils/
    ├── logger.py             ← TaggedLogger ([CLIPBOARD], [NETWORK], etc.)
    └── helpers.py            ← device ID generation, IP helpers
```

### Data flow

```
Local clipboard change
  → ClipboardMonitor detects hash change
  → App.on_local_clipboard_change()
  → encrypt with Fernet + sign with HMAC
  → send to all authenticated DeviceSessions
  → peer receives, verifies HMAC, decrypts
  → ClipboardMonitor.apply_received()  ← marks hash to suppress echo
  → wl-copy / win32clipboard sets clipboard
```

---

## Setup

### Ubuntu (Wayland)

```bash
# 1. Install system clipboard tool
sudo apt install wl-clipboard

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run
python main.py
```

### Windows 10 / 11

```powershell
# 1. Install dependencies (run as user, not admin)
pip install -r requirements.txt
pip install pywin32

# 2. Run
python main.py
```

---

## Usage

```bash
# Start the sync daemon (auto-discovers LAN peers, syncs clipboard)
python main.py

# Launch the graphical interface
python main.py --gui

# Launch the interactive terminal CLI (fallback if GUI doesn't work)
python main.py --cli

# Initiate pairing with a specific device IP
python main.py --pair 192.168.1.42

# Ping a device (shows round-trip latency)
python main.py --ping 192.168.1.42

# List all paired devices
python main.py --list-paired

# Remove a paired device
python main.py --unpair <device_id>

# Change device display name
python main.py --set-name "My Laptop"
```

---

## Interactive Terminal CLI

Run `python main.py --cli` (or `python cli.py` directly) to get an interactive prompt.
The daemon runs in the background while you type commands.

```
=== Clipboard Sync — Interactive CLI ===
  Device : my-laptop  (a1b2c3d4...)
  IP     : 192.168.1.10
  Port   : 52300 (TCP/UDP)
  Type "help" for commands.

clipboardsync>
```

### Available commands

| Command | Description |
|---|---|
| `status` | Show device name, ID, IP, port, and active sessions |
| `scan` | List all devices discovered on the LAN (paired and unpaired) |
| `paired` | List all paired devices |
| `pair <ip>` | Initiate pairing with the device at the given IP |
| `unpair <id>` | Remove a paired device (device ID prefix or exact name) |
| `ping <ip>` | Ping a paired device and show round-trip time |
| `name <new_name>` | Rename this device |
| `help` | Show command reference |
| `quit` / `exit` / `q` | Stop the daemon and exit |

### Typical pairing workflow

```
# On both machines, start the CLI
python main.py --cli

# On machine A — wait a few seconds for discovery, then check who is on the LAN
clipboardsync> scan

# On machine A — pair with machine B's IP
clipboardsync> pair 192.168.1.20

# On machine B — a prompt appears automatically:
# [PAIRING] Accept pairing from 'machine-a'? [y/N]:
# Type y and press Enter

# On both machines — verify the pairing code matches, then clipboard sync is active
clipboardsync> paired
```

---

## How Pairing Works

Pairing uses **X25519 ECDH** (Elliptic Curve Diffie-Hellman) for secure key exchange:

1. Device A runs `--pair <ip>` and connects via TCP
2. A sends `pair_request` containing its X25519 public key
3. Device B displays: "Accept pairing from 'Device A'? [y/N]"
4. If accepted, B sends `pair_response` with its own X25519 public key
5. Both sides independently compute:
   ```
   raw_shared = X25519(own_private_key, peer_public_key)
   shared_secret = HKDF-SHA256(raw_shared, info="python-clipboard-sync-v1")
   ```
6. Both display a **verification code** (first 12 hex chars of SHA256(shared_secret))  
   — compare these to detect MITM attacks
7. Shared secret is stored in `~/.python_clipboard_sync/paired_devices.json`

On subsequent connections, devices auto-reconnect using the stored secret.

---

## How Security Works

Every authenticated packet includes:

```json
{
  "packet_type": "clipboard",
  "device_id":   "abc123...",
  "timestamp":   1715000000,
  "nonce":       "f3a1b2c4...",
  "payload":     "<fernet-encrypted-json-string>",
  "signature":   "<hmac-sha256-hex>"
}
```

| Layer | Mechanism | Purpose |
|---|---|---|
| Payload encryption | Fernet (AES-128-CBC + HMAC-SHA256) | Confidentiality |
| Packet integrity | HMAC-SHA256 over outer fields | Authenticity |
| Replay prevention | 30-second timestamp window | Block old packet replay |
| Replay prevention | Nonce tracking (LRU cache) | Block exact duplicate packets |
| Device identity | Stored ECDH-derived shared secret | Only paired devices accepted |

Packets with invalid HMAC, stale timestamps, or duplicate nonces are **silently dropped**.

---

## How Clipboard Loop Prevention Works

Without prevention, this cycle would happen:
```
A copies "hello" → sends to B → B sets clipboard → B's monitor detects change → B sends to A → …
```

Prevention mechanism in `clipboard/backend.py`:

1. When content arrives from network, `ClipboardMonitor.apply_received(text)` is called
2. It records `SHA256(text)` → `timestamp` in `_received_hashes`
3. When the monitor polls and sees a change, it checks if the new hash appears in `_received_hashes` and was recorded within `suppress_window_sec` (default: 2 seconds)
4. If yes → skip broadcast (it's content we just received)
5. If no → broadcast normally

---

## Packet Types

| Type | Direction | Auth required | Encrypted |
|---|---|---|---|
| `identity` | UDP broadcast | No | No |
| `pair_request` | TCP | No | No |
| `pair_response` | TCP | No | No |
| `ping` | TCP | Yes | No (no sensitive data) |
| `pong` | TCP | Yes | No |
| `clipboard` | TCP | Yes | Yes (Fernet) |

---

## Configuration

Config is auto-generated at `~/.python_clipboard_sync/config.json`:

```json
{
  "udp_port": 52300,
  "tcp_port": 52300,
  "broadcast_interval": 10.0,
  "timestamp_tolerance_sec": 30,
  "pairing_timeout_sec": 30,
  "clipboard_poll_interval": 0.5,
  "clipboard_suppress_window_sec": 2.0,
  "data_dir": "~/.python_clipboard_sync"
}
```

---

## Design Decisions

| Decision | Rationale |
|---|---|
| UDP port 52300 for discovery | Avoids conflict with KDE Connect (ports 1714–1764) |
| JSON identity broadcast over UDP | Simple plaintext announce — device_id, device_name, tcp_port |
| asyncio TCP streams for data | Native Python async, no extra framework needed |
| Explicit pair / accept model | User-controlled trust — no auto-pairing |
| Hash-based clipboard loop suppression | More reliable than timestamp-only approach |
| Clipboard + ping as packet types | Minimal protocol surface |
| HKDF version string in key derivation | Protocol version baked into shared secret |

## Out of Scope

| Feature | Reason |
|---|---|
| D-Bus interfaces | Not needed for clipboard-only sync |
| SSL/TLS certificates | ECDH + HMAC is simpler with no cert management overhead |
| Avahi/mDNS discovery | UDP broadcast is sufficient for LAN |
| File transfer | Out of scope |
| Bluetooth backend | Out of scope |
| Notification system | Out of scope |
