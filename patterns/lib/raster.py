"""Raster (PNG) rendering. Crisp by construction -- boxes are filled with integer numpy
slices, so a 1px feature is exactly one pixel with values in {0, intensity}. No anti-alias.

A single :func:`write_png` takes a fill ``color`` that multiplies the intensity buffer, so
red/white/blue are the same geometry projected through different DMD colour channels.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

# fill colours as per-channel multipliers applied to the 0..255 intensity buffer
COLORS = {
    "white": (1, 1, 1),
    "red": (1, 0, 0),
    "green": (0, 1, 0),
    "blue": (0, 0, 1),
}


def render_intensity(pattern) -> np.ndarray:
    """Render the pattern to a uint8 (H, W) intensity buffer (0 = black background)."""
    H, W = pattern.height_px, pattern.width_px
    arr = np.zeros((H, W), dtype=np.uint8)

    for b in pattern.iter_boxes():
        x0 = max(0, b.x)
        y0 = max(0, b.y)
        x1 = min(W, b.x + b.w)
        y1 = min(H, b.y + b.h)
        if x1 <= x0 or y1 <= y0:
            continue
        val = np.uint8(max(0, min(255, b.intensity)))
        sub = arr[y0:y1, x0:x1]
        np.maximum(sub, val, out=sub)  # overlaps take the brighter dose

    polygons = list(pattern.iter_polygons())
    if polygons:
        scratch = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(scratch)
        for p in polygons:
            val = int(max(0, min(255, p.intensity)))
            draw.polygon([(int(x), int(y)) for x, y in p.points], fill=val)  # no AA
        np.maximum(arr, np.asarray(scratch), out=arr)

    return arr


def render_rgb(pattern, color="white") -> np.ndarray:
    """Render to a uint8 (H, W, 3) RGB image; ``color`` multiplies the intensity."""
    col = COLORS.get(color, color)
    intensity = render_intensity(pattern).astype(np.uint16)
    rgb = intensity[..., None] * np.asarray(col, dtype=np.uint16)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def render_bgr(pattern, color="white") -> np.ndarray:
    """:func:`render_rgb` in OpenCV's channel order, e.g. for ui/server.py."""
    return np.ascontiguousarray(render_rgb(pattern, color)[..., ::-1])


def write_png(pattern, path, color="white") -> None:
    Image.fromarray(render_rgb(pattern, color), "RGB").save(str(path))


def checkerboard_image(square: int, width: int = None, height: int = None,
                       intensity: int = 255) -> np.ndarray:
    """A full-field checkerboard as a uint8 (H, W) image, top-left square lit.

    Same squares as :func:`lib.builders.checkerboard`, but drawn directly with numpy:
    at 1 px squares the builder would make ~4 million boxes. Default size: the
    projector's input (3840 x 2160)."""
    from .scene import DEFAULT_HEIGHT_PX, DEFAULT_WIDTH_PX
    s = int(square)
    if s < 1:
        raise ValueError("square size must be >= 1 px")
    w, h = width or DEFAULT_WIDTH_PX, height or DEFAULT_HEIGHT_PX
    lit = ((np.arange(h)[:, None] // s + np.arange(w)[None, :] // s) % 2) == 0
    return (lit * np.uint8(max(0, min(255, intensity)))).astype(np.uint8)
