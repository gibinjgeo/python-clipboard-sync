# python-clipboard-sync

A lightweight, cross-platform clipboard synchronization daemon that syncs between Ubuntu Wayland and Windows over cloud infrastructure.

**Supported platforms:** Ubuntu (Wayland), Windows 10, Windows 11

---

## Features

| Feature | Status |
|---|---|
| Cloud-based clipboard sync | ✅ |
| Ubuntu Wayland clipboard support (wl-copy/wl-paste) | ✅ |
| Windows clipboard support | ✅ |
| Supabase backend integration | ✅ |
| Web-based management interface | ✅ |
| Clipboard loop prevention | ✅ |

---

## Project Structure

```
python-clipboard-sync/
├── cloud_client.py           ← Cloud synchronization client
├── index.html                ← Web UI for management
├── requirements.txt          ← Python dependencies
├── supabase_setup.sql        ← Database schema
│
├── clipboard/                ← Clipboard handling layer
│   ├── __init__.py
│   ├── backend.py            ← ClipboardBackend ABC and factory
│   ├── linux_wayland.py      ← Wayland backend (wl-paste/wl-copy)
│   └── windows.py            ← Windows backend (win32clipboard/pyperclip)
│
└── utils/                    ← Utility modules
    ├── __init__.py
    └── logger.py             ← Logging utilities
```

---

## Setup

### Ubuntu (Wayland)

```bash
# 1. Install system clipboard tool
sudo apt install wl-clipboard

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run the cloud client
python cloud_client.py
```

### Windows 10 / 11

```powershell
# 1. Install dependencies
pip install -r requirements.txt
pip install pywin32

# 2. Run the cloud client
python cloud_client.py
```

---

## Usage

```bash
# Start the clipboard sync daemon
python cloud_client.py

# Access the web management interface
# Open index.html in your browser or navigate to the hosted instance
```

---

## Architecture

### Data Flow

```
Local clipboard change
  → ClipboardMonitor detects change
  → cloud_client.py processes
  → Sends to Supabase backend
  → Remote device receives update
  → ClipboardMonitor.apply_received()
  → Platform-specific clipboard set (wl-copy or win32clipboard)
```

### Clipboard Backends

The clipboard layer is abstracted with platform-specific implementations:

- **Linux Wayland**: Uses `wl-paste` and `wl-copy` commands
- **Windows**: Uses `win32clipboard` or `pyperclip` libraries

Backend selection is automatic based on the detected platform.

---

## Configuration

Configuration is handled via environment variables or config files:

- **Supabase URL**: Set via `SUPABASE_URL`
- **Supabase API Key**: Set via `SUPABASE_KEY`
- **Polling interval**: Configurable clipboard check frequency
- **Cloud sync interval**: Configurable sync frequency with backend

---

## Database Schema

The `supabase_setup.sql` file contains the schema for:

- Device tracking
- Clipboard history
- Sync status
- User authentication

Run this script on your Supabase instance to initialize the database.

---

## How Clipboard Loop Prevention Works

1. When content arrives from the cloud backend, it's marked with a timestamp
2. When the local monitor detects a change, it checks if the new content was recently received
3. If yes → skip broadcast (prevents echo)
4. If no → send to cloud backend

---

## Technologies Used

| Technology | Purpose |
|---|---|
| Python 3.7+ | Core runtime |
| Supabase | Cloud backend & real-time sync |
| wl-clipboard | Linux Wayland clipboard access |
| pywin32 | Windows clipboard access |
| HTML/CSS/JS | Web management interface |

---

## Installation & Dependencies

Install required Python packages:

```bash
pip install -r requirements.txt
```

For Windows, also install:
```bash
pip install pywin32
```

For Linux, install system package:
```bash
sudo apt install wl-clipboard
```

---

## Development

To contribute or extend the project:

1. **Platform Support**: Add a new backend in `clipboard/` directory
2. **Logging**: Use the unified logger from `utils/logger.py`
3. **Cloud Integration**: Extend `cloud_client.py` for new sync logic

---

## License

[Add your license information here]

---

## Support

For issues and questions:
- Check the [GitHub Issues](https://github.com/gibinjgeo/python-clipboard-sync/issues)
- Review the project structure and existing implementations
