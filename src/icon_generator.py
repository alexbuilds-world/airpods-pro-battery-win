"""Generates PIL images used as the Windows system tray icon.

Icon design: AirPod Pro silhouette (rounded body + stem) with a rising battery
fill level inside the body and the minimum pod percentage as a centered number.

Layout (64×64 canvas):

    y=8  ┌────────────┐  ← body top
         │   rounded  │
         │    body    │  body: 36×28, corner r=9
         │    "75"    │
    y=36 └──────┬─────┘  ← body bottom / stem top
                │  stem  │  stem: 10×20, corner r=4
    y=56        └────────┘  ← stem bottom
"""

import os
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# ── Canvas ──────────────────────────────────────────────────────────────────
_SIZE = 64

# ── Colors (RGBA) ────────────────────────────────────────────────────────────
_COLOR_GREEN  = ( 60, 195,  60, 255)   # > 50 %
_COLOR_YELLOW = (220, 175,   0, 255)   # 21–50 %
_COLOR_RED    = (210,  45,  45, 255)   # 0–20 %
_COLOR_GRAY   = (105, 105, 105, 255)   # disconnected / None
_COLOR_BG     = ( 26,  26,  26, 255)   # icon background
_COLOR_WHITE  = (255, 255, 255, 255)
_COLOR_DARK   = ( 20,  20,  20, 255)   # text on yellow

# ── Silhouette geometry ──────────────────────────────────────────────────────
# Body (centered horizontally, leaving equal top/bottom margin)
_BX, _BY = 14, 8    # top-left corner
_BW, _BH = 36, 28   # width × height
_BR      = 9        # corner radius

# Stem (centered under body)
_SX      = _BX + (_BW - 10) // 2   # = 27
_SY      = _BY + _BH               # = 36  (flush against body bottom)
_SW, _SH = 10, 20
_SR      = 4


# ── Public API ───────────────────────────────────────────────────────────────

def get_color_for_level(pct: Optional[int]) -> tuple:
    """Map a battery percentage to an RGBA color.

    None  → gray   (pod not connected)
    0–20  → red    (critical)
    21–50 → yellow (low)
    51–100→ green  (good)
    """
    if pct is None:
        return _COLOR_GRAY
    if pct <= 20:
        return _COLOR_RED
    if pct <= 50:
        return _COLOR_YELLOW
    return _COLOR_GREEN


def generate_tray_icon(
    left_pct:  Optional[int],
    right_pct: Optional[int],
    case_pct:  Optional[int] = None,
) -> Image.Image:
    """Return a 64×64 RGBA tray icon reflecting current AirPods battery levels.

    Color and text are determined by the *lowest* connected pod (left/right).
    BLE AirPods advertisements expose coarse 10% buckets, so the number is an
    approximate value rather than an exact Windows battery reading.
    ``case_pct`` is accepted but does not affect the visual (case battery is
    shown in the popup window instead).
    """
    lowest = _min_connected(left_pct, right_pct)
    color  = get_color_for_level(lowest)

    img  = Image.new("RGBA", (_SIZE, _SIZE), _COLOR_BG)
    draw = ImageDraw.Draw(img)

    _draw_stem(draw, color)
    _draw_body(draw, color, lowest)
    return img


# ── Convenience wrappers ─────────────────────────────────────────────────────

def make_disconnected_icon() -> Image.Image:
    """Gray icon shown while scanning or when AirPods are out of range."""
    return generate_tray_icon(None, None, None)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _min_connected(*values: Optional[int]) -> Optional[int]:
    connected = [v for v in values if v is not None]
    return min(connected) if connected else None


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf",   # Arial Bold — preferred, thicker at small sizes
        "arial.ttf",
        # Explicit Windows path in case Pillow doesn't search Fonts/
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arialbd.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
        # Linux / CI fallback
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, TypeError):
            continue
    return ImageFont.load_default()


def _draw_stem(draw: ImageDraw.ImageDraw, color: tuple) -> None:
    draw.rounded_rectangle(
        [_SX, _SY, _SX + _SW, _SY + _SH],
        radius=_SR,
        fill=color,
    )


def _draw_body(
    draw:  ImageDraw.ImageDraw,
    color: tuple,
    pct:   Optional[int],
) -> None:
    bx, by, bw, bh, br = _BX, _BY, _BW, _BH, _BR

    # 1. Dark background well (full body)
    draw.rounded_rectangle(
        [bx, by, bx + bw, by + bh],
        radius=br,
        fill=_COLOR_BG,
    )

    # 2. Colored fill — rises from the bottom proportional to battery level.
    #    Technique: paint the full body in `color`, then paint a dark rectangle
    #    over the unfilled top portion.  The colored outline drawn last hides
    #    the straight edge of the dark overlay.
    if pct is not None:
        fill_h = max(2, int(bh * pct / 100))
        fill_y = by + bh - fill_h

        # Full body in color
        draw.rounded_rectangle(
            [bx, by, bx + bw, by + bh],
            radius=br,
            fill=color,
        )
        # Dark overlay on the unfilled top portion
        if fill_h < bh:
            draw.rectangle(
                [bx + 1, by + 1, bx + bw - 1, fill_y],
                fill=_COLOR_BG,
            )

    # 3. Outline drawn last so it sits on top of everything
    draw.rounded_rectangle(
        [bx, by, bx + bw, by + bh],
        radius=br,
        outline=color,
        width=2,
    )

    # 4. Percentage number centered in the body
    label     = f"{pct}" if pct is not None else "–"
    font_size = 13 if (pct is not None and pct == 100) else 15
    font      = _load_font(font_size)
    cx        = bx + bw // 2
    cy        = by + bh // 2

    # Dark text on yellow for contrast; white on everything else
    text_color = _COLOR_DARK if color == _COLOR_YELLOW else _COLOR_WHITE
    draw.text((cx, cy), label, font=font, fill=text_color, anchor="mm")
