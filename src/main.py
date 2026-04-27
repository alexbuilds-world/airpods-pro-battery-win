"""AirPods Battery for Windows — entry point.

Startup sequence
────────────────
  1. Configure logging  (stderr WARNING+ and rotating file DEBUG+)
  2. Probe BLE adapter  (bail out with a MessageBox if unavailable)
  3. Install SIGINT handler for clean Ctrl+C shutdown
  4. Hand off to TrayApp.run() on the main thread

Thread map (full details in tray_app.py / ble_scanner.py)
──────────────────────────────────────────────────────────
  Main thread   pystray Win32 message loop
  airpods-ble   asyncio event loop + BleakScanner  (daemon)
  popup-N       tkinter detail window, one per invocation  (daemon)
"""

import asyncio
import ctypes
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path


log = logging.getLogger(__name__)
_SINGLE_INSTANCE_MUTEX_NAME = "Local\\AirPodsBatteryWin"
_single_instance_mutex = None

# ── Logging ───────────────────────────────────────────────────────────────────

_LOG_FMT_CONSOLE = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_LOG_FMT_FILE    = "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s"
_LOG_DATE        = "%H:%M:%S"


def _setup_logging() -> Path:
    """Configure root logger with a console handler and a rotating file handler.

    Returns the path to the log file so it can be reported in the startup message.
    """
    log_dir = (
        Path(os.environ["LOCALAPPDATA"])
        if "LOCALAPPDATA" in os.environ
        else Path.home() / "AppData" / "Local"
    ) / "AirPodsBattery"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "airpods-battery.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)          # individual handlers narrow this down

    # ── Console (stderr): WARNING+ so normal use is quiet ────────────────
    if sys.stderr is not None:            # may be None in --windowed EXE builds
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter(_LOG_FMT_CONSOLE, datefmt=_LOG_DATE))
        root.addHandler(ch)

    # ── Rotating file: DEBUG+, 3 × 512 KB ────────────────────────────────
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes  = 512 * 1024,
        backupCount = 3,
        encoding  = "utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_LOG_FMT_FILE))
    root.addHandler(fh)

    return log_path


# ── BLE adapter probe ─────────────────────────────────────────────────────────

async def _probe_ble_async() -> str | None:
    """Start and immediately stop a BleakScanner to confirm BLE is accessible.

    Returns None on success, or a short error string on failure.
    """
    try:
        from bleak import BleakScanner
        scanner = BleakScanner()
        await scanner.start()
        await scanner.stop()
        return None
    except Exception as exc:
        return str(exc)


def _check_ble() -> bool:
    """Probe BLE synchronously.  Shows an error dialog and returns False if unavailable."""
    log.debug("Probing BLE adapter…")
    error = asyncio.run(_probe_ble_async())

    if error is None:
        log.debug("BLE adapter OK")
        return True

    log.error("BLE adapter unavailable: %s", error)
    _show_ble_error(error)
    return False


def _show_ble_error(detail: str) -> None:
    """Display a Windows error dialog explaining the Bluetooth problem."""
    message = (
        "Could not access the Bluetooth LE adapter.\n\n"
        f"Detail: {detail}\n\n"
        "Please make sure Bluetooth is enabled in Windows Settings "
        "and restart the application.\n\n"
        "The log file may contain more information:\n"
        f"  %LOCALAPPDATA%\\AirPodsBattery\\airpods-battery.log"
    )
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "AirPods Battery — Bluetooth Unavailable",
            0x10,   # MB_ICONERROR
        )
    except Exception:
        pass    # non-Windows or no display — error is already in the log


def _show_tray_exit_error() -> None:
    """Display a Windows error dialog when tray startup exits unexpectedly."""
    message = (
        "AirPods Battery started but the system tray icon could not stay running.\n\n"
        "Please make sure Windows Explorer and the notification area are running,\n"
        "then try again.\n\n"
        "Log file:\n"
        "  %LOCALAPPDATA%\\AirPodsBattery\\airpods-battery.log"
    )
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "AirPods Battery — Tray Startup Failed",
            0x10,   # MB_ICONERROR
        )
    except Exception:
        pass


def _acquire_single_instance() -> bool:
    """Return False if another copy of the app is already running."""
    global _single_instance_mutex
    if os.name != "nt":
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        mutex = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not mutex:
            return True
        _single_instance_mutex = mutex
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        log.exception("Single-instance mutex check failed")
        return True


def _show_already_running() -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            "AirPods Battery is already running in the system tray.",
            "AirPods Battery",
            0x40,   # MB_ICONINFORMATION
        )
    except Exception:
        pass


# ── SIGINT / Ctrl+C ───────────────────────────────────────────────────────────

def _install_sigint_handler(tray: "TrayApp") -> None:  # noqa: F821
    """Route Ctrl+C to TrayApp.request_quit() for a clean shutdown."""
    def _handler(signum: int, frame: object) -> None:
        log.info("SIGINT received — requesting clean shutdown")
        tray.request_quit()

    try:
        signal.signal(signal.SIGINT, _handler)
    except (OSError, ValueError):
        # Fails when called from a non-main thread or with detached stdin;
        # not fatal — Ctrl+C will fall back to the default KeyboardInterrupt.
        log.debug("Could not install SIGINT handler (non-main thread?)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log_path = _setup_logging()
    log.info(
        "AirPods Battery starting — Python %s, pid %d, log: %s",
        sys.version.split()[0], os.getpid(), log_path,
    )

    if not _acquire_single_instance():
        log.info("Another AirPods Battery instance is already running")
        _show_already_running()
        return

    if not _check_ble():
        sys.exit(1)

    # Defer imports until logging is fully configured so every module-level
    # logger is attached to handlers when the module first loads.
    from .tray_app import TrayApp

    tray = TrayApp()
    _install_sigint_handler(tray)

    log.debug("Starting TrayApp on main thread")
    try:
        tray.run()              # blocks here — pystray Win32 message loop
        if not tray.quit_requested:
            log.error("Tray loop exited unexpectedly")
            _show_tray_exit_error()
    except KeyboardInterrupt:
        # Fallback: SIGINT wasn't caught by _handler (e.g. no console)
        log.info("KeyboardInterrupt in main — shutting down")
    finally:
        # Idempotent — safe even if tray already called _scanner.stop()
        tray.request_quit()

    log.info("AirPods Battery exited cleanly")


if __name__ == "__main__":
    main()
