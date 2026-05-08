"""
python_clipboard_sync — entry point.

Usage:
  python main.py                     # Start daemon (discover + sync clipboard)
  python main.py --gui               # Start with graphical interface
  python main.py --pair <ip>         # Initiate pairing with a specific device
  python main.py --ping <ip>         # Send a ping to a specific device
  python main.py --list-paired       # List all paired devices
  python main.py --unpair <device_id># Remove a paired device
  python main.py --set-name <name>   # Change this device's display name
"""

import argparse
import asyncio
import sys

from app import App
from config import Config, DeviceInfo
from storage.paired_devices import PairedDeviceStore
from utils.logger import get_logger, setup_file_logging
from pathlib import Path

log = get_logger("app")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="python_clipboard_sync — lightweight cross-platform clipboard sync"
    )
    p.add_argument("--gui",        action="store_true",  help="Launch graphical interface")
    p.add_argument("--pair",       metavar="IP",         help="Initiate pairing with device at IP")
    p.add_argument("--ping",       metavar="IP",         help="Ping a paired device at IP")
    p.add_argument("--list-paired",action="store_true",  help="List paired devices")
    p.add_argument("--unpair",     metavar="DEVICE_ID",  help="Remove a paired device")
    p.add_argument("--set-name",   metavar="NAME",       help="Change this device's display name")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg  = Config.load()
    info = DeviceInfo.load_or_create(cfg.data_dir)

    if args.gui:
        from gui import launch_gui
        launch_gui()
        return

    # Enable file logging for headless mode
    setup_file_logging(Path(cfg.data_dir) / "clipboard-sync.log")

    if args.set_name:
        info.device_name = args.set_name
        info.save(cfg.data_dir)
        print(f"Device name set to: {info.device_name}")
        return

    if args.list_paired:
        store = PairedDeviceStore(cfg.paired_devices_file)
        devices = store.all()
        if not devices:
            print("No paired devices.")
        for d in devices:
            print(f"  {d.device_name:30s}  id={d.device_id}  last_seen={d.last_seen}")
        return

    if args.unpair:
        store = PairedDeviceStore(cfg.paired_devices_file)
        store.remove(args.unpair)
        return

    app = App(cfg, info)

    if args.pair:
        asyncio.run(app.cmd_pair(args.pair))
        return

    if args.ping:
        asyncio.run(app.cmd_ping(args.ping))
        return

    # Default: run daemon
    try:
        asyncio.run(app.run())
    except OSError as exc:
        log.error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
