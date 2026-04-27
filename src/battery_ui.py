"""Frameless popup window showing detailed AirPods battery information.

Behaviour
─────────
  • Fades in over ~250 ms, fades out before destroy
  • Auto-dismisses after 5 s; timer resets to 2 s when cursor leaves window
  • Cursor inside the window pauses the dismiss timer
  • Clicking × or losing focus dismisses immediately (with fade)
  • Positioned just above the taskbar, flush to the right edge,
    using the Win32 work area so it respects any taskbar position/size
"""

import ctypes
import ctypes.wintypes
import tkinter as tk
from typing import Optional

from .ble_scanner import AirPodsBattery

# ── Geometry ──────────────────────────────────────────────────────────────────
_WIN_W         = 290
_PAD_X         = 14          # horizontal padding inside the content area
_BAR_W         = 134         # width of the battery fill bar in pixels
_BAR_H         = 10
_ROW_GAP       = 6           # vertical padding above/below each battery row
_HEADER_H      = 40          # fixed header height

# ── Timing ────────────────────────────────────────────────────────────────────
_AUTO_DISMISS_MS   = 5000    # auto-close after this long without interaction
_HOVER_DISMISS_MS  = 2000    # restart timer with this delay when cursor leaves
_FADE_ALPHA_STEP   = 1.0 / 14
_FADE_INTERVAL_MS  = 18      # ~14 × 18 ms ≈ 250 ms total fade
_CURSOR_POLL_MS    = 250     # how often to check if cursor is over window

# ── Colors ────────────────────────────────────────────────────────────────────
_C_BG        = "#1e1e1e"
_C_HEADER    = "#252525"
_C_BORDER    = "#3d3d3d"
_C_TEXT      = "#f0f0f0"
_C_DIM       = "#808080"
_C_CLOSE_FG  = "#606060"
_C_CLOSE_HOV = "#cc4444"
_C_BAR_BG    = "#363636"
_C_GREEN     = "#4caf50"
_C_AMBER     = "#ffc107"
_C_RED       = "#f44336"
_C_CHARGE    = "#ffd060"


def _bar_color(pct: int) -> str:
    if pct <= 20:
        return _C_RED
    if pct <= 50:
        return _C_AMBER
    return _C_GREEN


# ── Win32 work area ───────────────────────────────────────────────────────────

def _work_area() -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the primary monitor work area."""
    rect = ctypes.wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


# ── BatteryPopup ──────────────────────────────────────────────────────────────

class BatteryPopup:
    """Frameless, auto-dismissing battery detail popup."""

    def __init__(self, battery: Optional[AirPodsBattery]) -> None:
        self._battery    = battery
        self._dismiss_id: Optional[str] = None
        self._closing    = False          # prevent double-close

        self._root = tk.Tk()
        self._root.overrideredirect(True)       # no title bar / frame
        self._root.attributes("-topmost", True) # above all windows
        self._root.attributes("-alpha", 0.0)    # invisible until fade-in
        self._root.resizable(False, False)
        self._root.configure(bg=_C_BORDER)      # outer 1-px border colour

        self._build_ui()
        self._position()
        self._bind_events()

        # Kick off animation and timer after the window is fully laid out
        self._root.after(50, self._start)

    # ── Public ───────────────────────────────────────────────────────────

    def show(self) -> None:
        self._root.mainloop()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        b = self._battery

        # 1-px border: root bg = border color; inner frame = content bg
        inner = tk.Frame(self._root, bg=_C_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_header(inner, b.model if b else "AirPods Battery")

        # Horizontal rule
        tk.Frame(inner, bg=_C_BORDER, height=1).pack(fill="x")

        if b is None:
            self._build_no_data(inner)
        elif b.single_battery is not None:
            self._build_single_battery(inner, b)
        else:
            self._build_battery_rows(inner, b)
            self._build_footer(inner, b)

    def _build_header(self, parent: tk.Widget, title: str) -> None:
        header = tk.Frame(parent, bg=_C_HEADER, height=_HEADER_H)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text=f"🎧  {title}",
            bg=_C_HEADER, fg=_C_TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(side="left", padx=(_PAD_X, 4), fill="y")

        close = tk.Label(
            header,
            text="×",
            bg=_C_HEADER, fg=_C_CLOSE_FG,
            font=("Segoe UI", 15, "bold"),
            cursor="hand2",
        )
        close.pack(side="right", padx=(4, 10), fill="y")
        close.bind("<Button-1>", lambda _e: self._dismiss())
        close.bind("<Enter>",    lambda _e: close.config(fg=_C_CLOSE_HOV))
        close.bind("<Leave>",    lambda _e: close.config(fg=_C_CLOSE_FG))

    def _build_no_data(self, parent: tk.Widget) -> None:
        body = tk.Frame(parent, bg=_C_BG)
        body.pack(fill="x", padx=_PAD_X, pady=16)

        tk.Label(
            body, text="No AirPods detected.",
            bg=_C_BG, fg=_C_TEXT,
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            body,
            text="Make sure they're nearby\nand connected to an Apple device.",
            bg=_C_BG, fg=_C_DIM,
            font=("Segoe UI", 9),
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def _build_battery_rows(self, parent: tk.Widget, b: AirPodsBattery) -> None:
        body = tk.Frame(parent, bg=_C_BG)
        body.pack(fill="x", padx=_PAD_X, pady=(10, 4))

        rows = [
            ("◀", "Left",  b.left_battery,  b.left_charging),
            ("▶", "Right", b.right_battery, b.right_charging),
            ("▫", "Case",  b.case_battery,  b.case_charging),
        ]
        for icon, label, pct, charging in rows:
            self._build_row(body, icon, label, pct, charging)

    def _build_single_battery(self, parent: tk.Widget, b: AirPodsBattery) -> None:
        body = tk.Frame(parent, bg=_C_BG)
        body.pack(fill="x", padx=_PAD_X, pady=(10, 10))
        self._build_row(
            body,
            "●",
            "Battery",
            b.single_battery,
            False,
            approximate=not b.exact,
        )

    def _build_row(
        self,
        parent:   tk.Widget,
        icon:     str,
        label:    str,
        pct:      Optional[int],
        charging: bool,
        approximate: bool = True,
    ) -> None:
        row = tk.Frame(parent, bg=_C_BG)
        row.pack(fill="x", pady=_ROW_GAP)

        tk.Label(
            row, text=icon, width=2,
            bg=_C_BG, fg=_C_DIM,
            font=("Segoe UI", 9),
        ).pack(side="left")

        tk.Label(
            row, text=label, width=5, anchor="w",
            bg=_C_BG, fg=_C_TEXT,
            font=("Segoe UI", 9),
        ).pack(side="left")

        if pct is None:
            tk.Label(
                row, text="Disconnected",
                bg=_C_BG, fg=_C_DIM,
                font=("Segoe UI", 9, "italic"),
            ).pack(side="left", padx=(4, 0))
            return

        # Battery fill bar
        color  = _bar_color(pct)
        canvas = tk.Canvas(
            row,
            width=_BAR_W, height=_BAR_H,
            bg=_C_BAR_BG, highlightthickness=0,
        )
        canvas.pack(side="left", padx=(4, 6))

        filled = max(1, int(_BAR_W * pct / 100))
        canvas.create_rectangle(0, 0, filled, _BAR_H, fill=color, outline="")
        # Subtle end-cap: 2-px lighter stripe on the right edge of the fill
        if filled > 2:
            canvas.create_rectangle(filled - 2, 0, filled, _BAR_H,
                                    fill=_lighten(color), outline="")

        # Percentage + charging indicator
        prefix = "≈" if approximate else ""
        pct_text = f"{prefix}{pct}%{'  ⚡' if charging else ''}"
        tk.Label(
            row, text=pct_text, width=7, anchor="w",
            bg=_C_BG, fg=_C_TEXT,
            font=("Segoe UI", 9),
        ).pack(side="left")

    def _build_footer(self, parent: tk.Widget, b: AirPodsBattery) -> None:
        footer = tk.Frame(parent, bg=_C_BG)
        footer.pack(fill="x", padx=_PAD_X, pady=(0, 10))

        # List any charging components
        charging_names = [
            name for name, flag in (
                ("Left",  b.left_charging),
                ("Right", b.right_charging),
                ("Case",  b.case_charging),
            ) if flag
        ]

        if charging_names:
            names = " & ".join(charging_names)
            tk.Label(
                footer,
                text=f"⚡  {names} charging",
                bg=_C_BG, fg=_C_CHARGE,
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(side="left")
        else:
            tk.Label(
                footer,
                text=f"Signal  {b.rssi} dBm",
                bg=_C_BG, fg=_C_DIM,
                font=("Segoe UI", 8),
                anchor="w",
            ).pack(side="left")

    # ── Positioning ───────────────────────────────────────────────────────

    def _position(self) -> None:
        self._root.update_idletasks()
        wh = self._root.winfo_reqheight()

        try:
            _wa_left, _wa_top, wa_right, wa_bottom = _work_area()
        except Exception:
            wa_right  = self._root.winfo_screenwidth()
            wa_bottom = self._root.winfo_screenheight() - 48  # taskbar fallback

        margin = 10
        x = wa_right  - _WIN_W - margin
        y = wa_bottom - wh     - margin
        self._root.geometry(f"{_WIN_W}x{wh}+{x}+{y}")

    # ── Fade animation ────────────────────────────────────────────────────

    def _fade_in(self, alpha: float = 0.0) -> None:
        if self._closing:
            return
        alpha = min(alpha + _FADE_ALPHA_STEP, 1.0)
        try:
            self._root.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if alpha < 1.0:
            self._root.after(_FADE_INTERVAL_MS, self._fade_in, alpha)

    def _fade_out(self, alpha: float = 1.0) -> None:
        alpha = max(alpha - _FADE_ALPHA_STEP, 0.0)
        try:
            self._root.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if alpha > 0.0:
            self._root.after(_FADE_INTERVAL_MS, self._fade_out, alpha)
        else:
            try:
                self._root.destroy()
            except tk.TclError:
                pass

    # ── Dismiss / timer ───────────────────────────────────────────────────

    def _start(self) -> None:
        """Called once after the first paint cycle."""
        self._fade_in()
        self._schedule_dismiss(_AUTO_DISMISS_MS)
        self._poll_cursor()
        self._root.focus_force()

    def _schedule_dismiss(self, delay_ms: int) -> None:
        self._cancel_dismiss()
        self._dismiss_id = self._root.after(delay_ms, self._dismiss)

    def _cancel_dismiss(self) -> None:
        if self._dismiss_id is not None:
            try:
                self._root.after_cancel(self._dismiss_id)
            except tk.TclError:
                pass
            self._dismiss_id = None

    def _dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_dismiss()
        self._fade_out()

    # ── Cursor polling (hover-to-pause) ───────────────────────────────────

    def _poll_cursor(self) -> None:
        """Poll cursor position every 250 ms to pause/resume the dismiss timer."""
        if self._closing:
            return
        try:
            px, py    = self._root.winfo_pointerxy()
            wx, wy    = self._root.winfo_rootx(), self._root.winfo_rooty()
            ww, wh    = self._root.winfo_width(), self._root.winfo_height()
            inside    = wx <= px <= wx + ww and wy <= py <= wy + wh

            if inside:
                self._cancel_dismiss()         # hovering — pause timer
            elif self._dismiss_id is None:
                self._schedule_dismiss(_HOVER_DISMISS_MS)   # left window — restart
        except tk.TclError:
            return

        self._root.after(_CURSOR_POLL_MS, self._poll_cursor)

    # ── Focus-loss dismiss ────────────────────────────────────────────────

    def _bind_events(self) -> None:
        self._root.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_out(self, _event: tk.Event) -> None:
        # Delay so focus can settle on a child widget before we check
        self._root.after(80, self._check_focus)

    def _check_focus(self) -> None:
        try:
            if self._root.focus_get() is None:
                self._dismiss()
        except tk.TclError:
            pass


# ── Color helper ──────────────────────────────────────────────────────────────

def _lighten(hex_color: str, amount: int = 40) -> str:
    """Return a slightly lighter version of a '#rrggbb' color."""
    r = min(int(hex_color[1:3], 16) + amount, 255)
    g = min(int(hex_color[3:5], 16) + amount, 255)
    b = min(int(hex_color[5:7], 16) + amount, 255)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Public entry point ────────────────────────────────────────────────────────

def show_battery_popup(battery: Optional[AirPodsBattery]) -> None:
    """Create and show the popup (blocking — run from a dedicated thread)."""
    BatteryPopup(battery).show()
