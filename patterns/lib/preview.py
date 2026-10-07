"""Quick on-screen preview of a pattern using Pillow."""

from __future__ import annotations

from PIL import Image

from .raster import render_rgb


def preview(pattern, color: str = "white", max_dim: int = 1400, show: bool = False) -> Image.Image:
    """Return a downscaled PIL image of the pattern.

    Uses NEAREST resampling so downscaling doesn't invent smooth edges that the real
    (crisp) raster doesn't have. Set ``show=True`` to pop it open in the default viewer.
    """
    img = Image.fromarray(render_rgb(pattern, color), "RGB")
    w, h = img.size
    scale = max_dim / max(w, h)
    if scale < 1:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.NEAREST)
    if show:
        img.show()
    return img
