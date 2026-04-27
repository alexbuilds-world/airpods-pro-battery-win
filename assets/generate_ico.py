#!/usr/bin/env python3
"""Generate assets/airpods_icon.ico — multi-resolution Windows icon.

Run once before PyInstaller:
    cd airpods-battery-win
    python assets/generate_ico.py

Sizes embedded in the .ico: 256 × 256, 64 × 64, 48 × 48, 32 × 32, 16 × 16

Design: AirPod Pro silhouette (rounded body + stem) on a dark charcoal
background, rendered at 2× then scaled down with LANCZOS for crisp edges
at every resolution.

Reference geometry is the 64-px tray-icon canvas from icon_generator.py
so both the tray icon and the application icon share the same proportions.
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required:  pip install Pillow")

# ── Output location ───────────────────────────────────────────────────────────
_OUTPUT = Path(__file__).with_name("airpods_icon.ico")

# ── Target sizes (largest first — Pillow uses the first as the primary) ───────
_SIZES = [256, 64, 48, 32, 16]

# ── Color palette (RGBA) ──────────────────────────────────────────────────────
_C_BG        = ( 22,  22,  22, 255)   # dark charcoal background
_C_BODY      = (242, 242, 242, 255)   # near-white body
_C_STEM      = (200, 200, 200, 255)   # slightly dimmer stem (creates depth)
_C_GRILLE    = ( 22,  22,  22, 255)   # mesh dots — same as background
_C_SHIMMER   = (255, 255, 255,  70)   # semi-transparent highlight arc


# ── Renderer ──────────────────────────────────────────────────────────────────

def _draw_raw(size: int) -> Image.Image:
    """Render the icon at exactly `size` × `size`.

    All geometry is derived from the 64-px reference by the scale factor
    s = size / 64, so proportions are identical across all sizes.
    """
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    s    = size / 64.0

    def sc(v: float, minimum: int = 1) -> int:
        return max(minimum, round(v * s))

    # ── Background rounded square ─────────────────────────────────────
    bg_r = sc(10)
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius = bg_r,
        fill   = _C_BG,
    )

    # ── Body ──────────────────────────────────────────────────────────
    bx = sc(14)
    by = sc(8)
    bw = sc(36)
    bh = sc(28)
    br = sc(9)
    draw.rounded_rectangle(
        [bx, by, bx + bw, by + bh],
        radius = br,
        fill   = _C_BODY,
    )

    # Subtle top-of-body shimmer (light reflection)
    if bh >= 8:
        shim_h = max(2, bh // 5)
        shim_x_pad = bw // 5
        draw.rounded_rectangle(
            [bx + shim_x_pad, by + sc(1.5),
             bx + bw - shim_x_pad, by + shim_h],
            radius = max(1, br // 2),
            fill   = _C_SHIMMER,
        )

    # ── Stem ──────────────────────────────────────────────────────────
    sw = sc(10, minimum=2)
    sh = sc(20)
    sx = (size - sw) // 2
    sy = by + bh
    sr = sc(4)
    draw.rounded_rectangle(
        [sx, sy, sx + sw, sy + sh],
        radius = sr,
        fill   = _C_STEM,
    )

    # ── Speaker grille (3 mesh dots — visible at 48 px and above) ─────
    if size >= 48:
        dot_r   = max(1, round(1.8 * s))
        dot_y   = by + bh // 2
        dot_gap = sc(5)
        cx      = bx + bw // 2
        for dx in (-dot_gap, 0, dot_gap):
            x = cx + dx
            draw.ellipse(
                [x - dot_r, dot_y - dot_r, x + dot_r, dot_y + dot_r],
                fill = _C_GRILLE,
            )

    return img


def _render_at(output_size: int) -> Image.Image:
    """Return a `output_size`-px icon rendered at 2× and scaled down."""
    render_size = output_size * 2          # 2× supersampling
    raw         = _draw_raw(render_size)
    return raw.resize((output_size, output_size), Image.LANCZOS)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    images = [_render_at(s) for s in _SIZES]

    images[0].save(
        _OUTPUT,
        format        = "ICO",
        append_images = images[1:],
        sizes         = [(s, s) for s in _SIZES],
    )

    print(f"Saved  {_OUTPUT}")
    print(f"Sizes  {', '.join(f'{s}×{s}' for s in _SIZES)}")


if __name__ == "__main__":
    main()
