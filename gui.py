"""Minimal tkinter GUI for the clipboard-sync daemon."""

import asyncio
import queue
import threading
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog
from pathlib import Path
from typing import Optional

from config import Config, DeviceInfo
from storage.paired_devices import PairedDeviceStore
from utils.logger import (
    get_logger, get_gui_log_queue, enable_gui_logging, setup_file_logging,
)

log = get_logger("gui")

_POLL_MS    = 150    # log-queue drain interval
_REFRESH_MS = 3000   # paired-device list refresh interval
_MAX_LOG_LINES = 2000


# ── background daemon thread ──────────────────────────────────────────────────

class _DaemonThread:
    """Runs the async App in a background thread so tkinter stays responsive."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._app = None
        self._thread: Optional[threading.Thread] = None
        # "running" | "stopped" | "error:<msg>"
        self.status_queue: queue.Queue = queue.Queue()

    def start(self, cfg: Config, info: DeviceInfo) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(cfg, info), daemon=True, name="clipboard-sync"
        )
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._app:
            self._loop.call_soon_threadsafe(self._app.request_stop)

    def pair(self, host: str) -> None:
        if self._loop and self._app:
            asyncio.run_coroutine_threadsafe(
                self._app.cmd_pair(host), self._loop
            )

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internal ──────────────────────────────────────────────────────

    def _run(self, cfg: Config, info: DeviceInfo) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main(cfg, info))
        except Exception as exc:
            self.status_queue.put(f"error:{exc}")
        finally:
            self._loop.close()
            self._loop = None
            self._app  = None
            self.status_queue.put("stopped")

    async def _async_main(self, cfg: Config, info: DeviceInfo) -> None:
        from app import App
        self._app = App(cfg, info)
        self.status_queue.put("running")
        await self._app.run()


# ── main window ───────────────────────────────────────────────────────────────

class ClipboardSyncGUI:
    def __init__(self) -> None:
        enable_gui_logging()

        self._cfg  = Config.load()
        self._info = DeviceInfo.load_or_create(self._cfg.data_dir)

        setup_file_logging(Path(self._cfg.data_dir) / "clipboard-sync.log")

        self._daemon    = _DaemonThread()
        self._log_queue = get_gui_log_queue()

        self._root = tk.Tk()
        self._root.title("Clipboard Sync")
        self._root.geometry("680x540")
        self._root.minsize(520, 420)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._load_previous_log()

        self._root.after(_POLL_MS,    self._poll_log)
        self._root.after(_REFRESH_MS, self._refresh_devices)
        self._root.after(200,         self._poll_status)
        self._root.after(0,           self._toggle)  # auto-start daemon on launch

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

        # Header bar
        hdr = ttk.Frame(self._root, padding=(8, 6))
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(2, weight=1)

        self._dot = tk.Label(hdr, text="●", fg="#888888", font=("", 16))
        self._dot.grid(row=0, column=0, padx=(0, 6))

        self._status_var = tk.StringVar(value="Stopped")
        ttk.Label(hdr, textvariable=self._status_var).grid(row=0, column=1, sticky="w")

        ttk.Label(
            hdr,
            text=f"  {self._info.device_name}  ({self._info.device_id[:8]}…)",
            foreground="gray",
        ).grid(row=0, column=2, sticky="w")

        self._toggle_btn = ttk.Button(hdr, text="Start", width=8, command=self._toggle)
        self._toggle_btn.grid(row=0, column=3, padx=(8, 0))

        ttk.Separator(self._root).grid(row=0, column=0, sticky="ews", pady=(0, 0))

        # Body: vertical paned window
        pane = ttk.PanedWindow(self._root, orient="vertical")
        pane.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)

        # ── Paired devices ────────────────────────────────────────────
        dev_frm = ttk.LabelFrame(pane, text="Paired Devices", padding=4)
        pane.add(dev_frm, weight=1)
        dev_frm.columnconfigure(0, weight=1)
        dev_frm.rowconfigure(0, weight=1)

        self._dev_box = tk.Listbox(
            dev_frm, height=5, activestyle="none",
            font=("Courier", 9), selectmode="browse",
        )
        dev_sb = ttk.Scrollbar(dev_frm, command=self._dev_box.yview)
        self._dev_box.configure(yscrollcommand=dev_sb.set)
        self._dev_box.grid(row=0, column=0, sticky="nsew")
        dev_sb.grid(row=0, column=1, sticky="ns")

        dev_btns = ttk.Frame(dev_frm)
        dev_btns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(dev_btns, text="Pair new device…", command=self._on_pair).pack(side="left")
        ttk.Button(dev_btns, text="Refresh",          command=self._refresh_devices).pack(side="left", padx=4)

        # ── Log viewer ────────────────────────────────────────────────
        log_frm = ttk.LabelFrame(pane, text="Log", padding=4)
        pane.add(log_frm, weight=3)
        log_frm.columnconfigure(0, weight=1)
        log_frm.rowconfigure(0, weight=1)

        self._log_txt = tk.Text(
            log_frm, state="disabled", wrap="none",
            font=("Courier", 9),
            bg="#1a1a2e", fg="#dde1f0", insertbackground="white",
        )
        log_vsb = ttk.Scrollbar(log_frm, command=self._log_txt.yview)
        log_hsb = ttk.Scrollbar(log_frm, orient="horizontal", command=self._log_txt.xview)
        self._log_txt.configure(
            yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set
        )
        self._log_txt.grid(row=0, column=0, sticky="nsew")
        log_vsb.grid(row=0, column=1, sticky="ns")
        log_hsb.grid(row=1, column=0, sticky="ew")

        log_btns = ttk.Frame(log_frm)
        log_btns.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(log_btns, text="Clear",    command=self._clear_log).pack(side="left")
        ttk.Button(log_btns, text="Save log…", command=self._save_log).pack(side="left", padx=4)

    # ── daemon control ────────────────────────────────────────────────

    def _toggle(self) -> None:
        if self._daemon.is_alive():
            self._daemon.stop()
        else:
            self._daemon = _DaemonThread()
            self._daemon.start(self._cfg, self._info)
            self._toggle_btn.configure(text="Stop")

    def _on_pair(self) -> None:
        if not self._daemon.is_alive():
            messagebox.showwarning("Not running", "Start the daemon first.", parent=self._root)
            return
        ip = simpledialog.askstring(
            "Pair device", "Enter the IP address of the device to pair with:",
            parent=self._root,
        )
        if ip and ip.strip():
            self._daemon.pair(ip.strip())

    # ── periodic callbacks ────────────────────────────────────────────

    def _poll_log(self) -> None:
        try:
            while True:
                self._append_log(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        self._root.after(_POLL_MS, self._poll_log)

    def _poll_status(self) -> None:
        try:
            while True:
                self._apply_status(self._daemon.status_queue.get_nowait())
        except queue.Empty:
            pass
        self._root.after(200, self._poll_status)

    def _refresh_devices(self) -> None:
        store   = PairedDeviceStore(self._cfg.paired_devices_file)
        devices = store.all()
        self._dev_box.delete(0, tk.END)
        if not devices:
            self._dev_box.insert(tk.END, "  (no paired devices)")
        for d in devices:
            last = d.last_seen[:16].replace("T", " ") if d.last_seen else "never"
            self._dev_box.insert(tk.END, f"  {d.device_name:<22}  last seen: {last}")
        self._root.after(_REFRESH_MS, self._refresh_devices)

    # ── helpers ───────────────────────────────────────────────────────

    def _apply_status(self, status: str) -> None:
        if status == "running":
            self._dot.configure(fg="#22c55e")
            self._status_var.set("Running")
            self._toggle_btn.configure(text="Stop")
        elif status == "stopped":
            self._dot.configure(fg="#888888")
            self._status_var.set("Stopped")
            self._toggle_btn.configure(text="Start")
        elif status.startswith("error:"):
            msg = status[6:]
            self._dot.configure(fg="#ef4444")
            self._status_var.set("Error")
            self._toggle_btn.configure(text="Start")
            messagebox.showerror("Daemon error", msg, parent=self._root)

    def _append_log(self, text: str) -> None:
        self._log_txt.configure(state="normal")
        self._log_txt.insert(tk.END, text + "\n")
        self._log_txt.see(tk.END)
        # trim to keep memory bounded
        lines = int(self._log_txt.index("end-1c").split(".")[0])
        if lines > _MAX_LOG_LINES:
            self._log_txt.delete("1.0", f"{lines - _MAX_LOG_LINES}.0")
        self._log_txt.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_txt.configure(state="normal")
        self._log_txt.delete("1.0", tk.END)
        self._log_txt.configure(state="disabled")

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All", "*.*")],
            initialfile="clipboard-sync.log",
            parent=self._root,
        )
        if path:
            content = self._log_txt.get("1.0", tk.END)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)

    def _load_previous_log(self) -> None:
        """Show the tail of the persistent log file on startup."""
        log_file = Path(self._cfg.data_dir) / "clipboard-sync.log"
        if not log_file.exists():
            return
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            tail  = lines[-200:]  # last 200 lines
            self._log_txt.configure(state="normal")
            self._log_txt.insert(tk.END, "── previous session ──\n")
            self._log_txt.insert(tk.END, "\n".join(tail) + "\n")
            self._log_txt.insert(tk.END, "── current session ──\n")
            self._log_txt.see(tk.END)
            self._log_txt.configure(state="disabled")
        except OSError:
            pass

    def _on_close(self) -> None:
        if self._daemon.is_alive():
            self._daemon.stop()
        self._root.destroy()

    def run(self) -> None:
        self._root.mainloop()


# ── entry point ───────────────────────────────────────────────────────────────

def launch_gui() -> None:
    ClipboardSyncGUI().run()
