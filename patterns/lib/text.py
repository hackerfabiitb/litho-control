"""Bitmap text rendering.

Uses Pillow's built-in bitmap font (``ImageFont.load_default()`` -- a simple ~6x10 px
bitmap face that ships with Pillow and renders without anti-aliasing). The glyphs are
thresholded to pure black/white and emitted as run-length :class:`Box` shapes, so text
stays perfectly crisp at any integer ``scale`` -- consistent with the rest of the pixel
-native geometry model.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .scene import Box, ShapeGroup

_DEFAULT_FONT = None


def _default_font():
    global _DEFAULT_FONT
    if _DEFAULT_FONT is None:
        # no size argument -> the classic embedded bitmap font (no anti-aliasing)
        _DEFAULT_FONT = ImageFont.load_default()
    return _DEFAULT_FONT


def text(
    string: str,
    scale: int = 1,
    intensity: int = 255,
    threshold: int = 128,
    font=None,
) -> ShapeGroup:
    """Render ``string`` as crisp filled pixels, returning a :class:`ShapeGroup`.

    ``scale`` enlarges every font pixel into a ``scale x scale`` block (so 1px font pixels
    become sharp ``scale``-px squares -- no blur). ``font`` may be any Pillow ``ImageFont``
    (e.g. a ``truetype`` face); it defaults to the built-in bitmap font. Anything at or above
    ``threshold`` in the rendered glyph counts as ink, so even an anti-aliasing font yields a
    binary result. Local origin is top-left.
    """
    if not string:
        return ShapeGroup([])

    fnt = font or _default_font()

    # measure, then draw the string onto a tight L (grayscale) buffer
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), string, font=fnt)
    w = max(1, right - left)
    h = max(1, bottom - top)
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).text((-left, -top), string, fill=255, font=fnt)

    mask = np.asarray(img) >= threshold

    # emit one Box per horizontal run of ink pixels in each row
    shapes = []
    for row in range(h):
        cols = mask[row]
        c = 0
        while c < w:
            if cols[c]:
                start = c
                while c < w and cols[c]:
                    c += 1
                shapes.append(
                    Box(start * scale, row * scale, (c - start) * scale, scale, intensity)
                )
            else:
                c += 1
    return ShapeGroup(shapes)
