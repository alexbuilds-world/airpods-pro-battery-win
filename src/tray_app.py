"""System tray application: icon, menu, threading, and autostart.

Thread model
────────────
  Main thread      pystray event loop  (pystray.Icon.run blocks here)
  airpods-ble      asyncio BLE scan loop  (AirPodsScanner daemon thread)
  popup-N          one-shot tkinter window per "Show Details" invocation

The BLE callback fires on the airpods-ble thread.  Writing to
``pystray.Icon.icon`` / ``.title`` from a non-main thread is safe on Windows
because pystray's Win32 backend serialises updates through PostMessage.

Menu labels use pystray's callable-text feature: each label is a lambda that
reads ``self._battery`` at the moment the user opens the menu, so the menu
stays correct without a full rebuild on every BLE advertisement.
"""

import logging
import sys
import threading
from typing import Optional

import pystray

from .ble_scanner import AirPodsBattery, AirPodsScanner
from .battery_ui import show_battery_popup
from .icon_generator import generate_tray_icon, make_disconnected_icon

log = logging.getLogger(__name__)

# ── Windows autostart via registry ────────────────────────────────────────────

_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "AirPodsBattery"


def _autostart_cmd() -> str:
    """Command string stored in the registry Run key."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle — sys.executable is the .exe
        return f'"{sys.executable}"'
    # Development — register as a module invocation
    return f'"{sys.executable}" -m src.main'


def _is_autostart() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            winreg.QueryValueEx(k, _REG_NAME)
            return True
    except Exception:
        return False


def _set_autostart(enable: bool) -> None:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _REG_PATH,
            access=winreg.KEY_SET_VALUE,
        ) as k:
            if enable:
                cmd = _autostart_cmd()
                winreg.SetValueEx(k, _REG_NAME, 0, winreg.REG_SZ, cmd)
                log.info("Autostart enabled: %s", cmd)
            else:
                try:
                    winreg.DeleteValue(k, _REG_NAME)
                    log.info("Autostart disabled")
                except FileNotFoundError:
                    pass  # was never set — no-op
    except OSError as exc:
        log.warning("Registry autostart update failed: %s", exc)


# ── Tooltip / label helpers ───────────────────────────────────────────────────

def _fmt(label: str, pct: Optional[int], charging: bool) -> str:
    """Format a single component for tooltip or menu."""
    if pct is None:
        return f"{label}: —"
    charge = " ⚡" if charging else ""
    return f"{label}: ≈{pct}%{charge}"


def _fmt_exact(label: str, pct: Optional[int]) -> str:
    if pct is None:
        return f"{label}: —"
    return f"{label}: {pct}%"


def _build_tooltip(b: AirPodsBattery) -> str:
    """'L: 80% | R: 75% | Case: 90%' style tooltip string."""
    if b.single_battery is not None:
        return _fmt_exact("Battery", b.single_battery)
    return " | ".join([
        _fmt("L",    b.left_battery,  b.left_charging),
        _fmt("R",    b.right_battery, b.right_charging),
        _fmt("Case", b.case_battery,  b.case_charging),
    ])


# ── TrayApp ───────────────────────────────────────────────────────────────────

class TrayApp:
    def __init__(self) -> None:
        self._battery:      Optional[AirPodsBattery] = None
        self._lock          = threading.Lock()
        self._icon:         Optional[pystray.Icon] = None
        self._scanner       = AirPodsScanner(on_update=self._on_battery_update)
        self._quit_requested = False

        # Popup guard — prevents multiple detail windows stacking
        self._popup_lock    = threading.Lock()
        self._popup_open    = False

    # ── Public entry point ────────────────────────────────────────────────

    def request_quit(self) -> None:
        """Graceful shutdown callable from any thread (SIGINT, external code)."""
        if self._quit_requested:
            return
        self._quit_requested = True
        if self._icon is not None:
            self._icon.stop()
        self._scanner.stop()

    @property
    def quit_requested(self) -> bool:
        return self._quit_requested

    def run(self) -> None:
        """Start the BLE scanner and block on the pystray event loop."""
        self._scanner.start()

        self._icon = pystray.Icon(
            name  = "airpods_battery",
            icon  = make_disconnected_icon(),
            title = "AirPods Battery — scanning…",
            menu  = self._build_menu(),
        )

        log.debug("Entering pystray run loop")
        self._icon.run()   # blocks until _on_quit calls icon.stop()

    # ── BLE scanner callback (called on airpods-ble thread) ───────────────

    def _on_battery_update(self, battery: Optional[AirPodsBattery]) -> None:
        with self._lock:
            self._battery = battery
        self._update_icon_and_title()

    # ── Icon + tooltip ────────────────────────────────────────────────────

    def _update_icon_and_title(self) -> None:
        """Update the visible icon image and tooltip; leave menu object alone."""
        if self._icon is None:
            return

        with self._lock:
            b = self._battery

        if b is None:
            self._icon.icon  = make_disconnected_icon()
            self._icon.title = "AirPods Battery — scanning…"
        else:
            icon_battery = b.single_battery if b.single_battery is not None else b.left_battery
            self._icon.icon  = generate_tray_icon(
                icon_battery, b.right_battery, b.case_battery
            )
            self._icon.title = _build_tooltip(b)

    # ── Menu (built once at startup; labels re-evaluated at open-time) ────

    def _build_menu(self) -> pystray.Menu:
        # "Show Details" is both the explicit menu action and the default
        # left-click action (pystray triggers default=True on left-click /
        # double-click depending on platform).
        show_details = pystray.MenuItem(
            "Show Details",
            self._on_show_details,
            default=True,
        )

        return pystray.Menu(
            # Title row — shows model name once detected
            pystray.MenuItem(
                lambda _: self._label_title(),
                action  = None,
                enabled = False,
            ),
            pystray.Menu.SEPARATOR,

            # Per-component battery readings — re-read battery at open-time
            pystray.MenuItem(
                lambda _: self._label_pod("Left Pod",  "left_battery",  "left_charging"),
                action  = None,
                enabled = False,
            ),
            pystray.MenuItem(
                lambda _: self._label_pod("Right Pod", "right_battery", "right_charging"),
                action  = None,
                enabled = False,
            ),
            pystray.MenuItem(
                lambda _: self._label_pod("Case",      "case_battery",  "case_charging"),
                action  = None,
                enabled = False,
            ),
            pystray.Menu.SEPARATOR,

            show_details,
            pystray.MenuItem(
                "Start with Windows",
                self._on_toggle_autostart,
                checked = lambda _: _is_autostart(),
            ),
            pystray.Menu.SEPARATOR,

            pystray.MenuItem("Quit", self._on_quit),
        )

    # ── Dynamic label callbacks (invoked by menu lambdas) ─────────────────

    def _label_title(self) -> str:
        with self._lock:
            b = self._battery
        return b.model if b else "AirPods Battery"

    def _label_pod(self, label: str, pct_attr: str, charge_attr: str) -> str:
        with self._lock:
            b = self._battery
        if b is None:
            return f"{label}: —"
        if pct_attr == "left_battery" and b.single_battery is not None:
            return _fmt_exact("Battery", b.single_battery)
        if b.single_battery is not None:
            return f"{label}: —"
        pct      = getattr(b, pct_attr)
        charging = getattr(b, charge_attr)
        return _fmt(label, pct, charging)

    # ── Menu action handlers ──────────────────────────────────────────────

    def _on_show_details(
        self, icon: pystray.Icon, item: pystray.MenuItem
    ) -> None:
        """Spawn a popup window on a fresh daemon thread.

        The popup_lock prevents a second window opening while one is already
        visible.  The flag is cleared in a finally block so a crash in the
        popup never permanently blocks future opens.
        """
        with self._popup_lock:
            if self._popup_open:
                log.debug("Detail popup already open — ignoring request")
                return
            self._popup_open = True

        with self._lock:
            battery = self._battery

        def _run_popup() -> None:
            try:
                show_battery_popup(battery)
            except Exception:
                log.exception("Detail popup crashed")
            finally:
                with self._popup_lock:
                    self._popup_open = False

        threading.Thread(target=_run_popup, daemon=True, name="popup").start()

    def _on_toggle_autostart(
        self, icon: pystray.Icon, item: pystray.MenuItem
    ) -> None:
        _set_autostart(not _is_autostart())

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        log.debug("Quit requested")
        if self._quit_requested:
            return
        self._quit_requested = True
        icon.stop()
        threading.Thread(
            target=self._scanner.stop,
            daemon=True,
            name="scanner-stop",
        ).start()
