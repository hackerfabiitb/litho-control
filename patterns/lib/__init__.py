"""lib -- script-driven lithography pattern generation for the HackerFab DMD projector.

One integer-pixel geometry model feeds both a vector GDSII export and crisp raster PNGs
(colourable red/white/blue, with per-pixel 0-255 intensity for grayscale dose).
"""

from .scene import Pattern, Box, Polygon, ShapeGroup
from .builders import (
    box,
    plus_fiducial,
    line_array,
    checkerboard,
    l_shape,
    nested_ls,
    circle,
    circle_grid,
    wafer,
    WAFER_SIZES_MM,
)
from .text import text
from .layout import flow_layout, tile_quadrants
from .gds import write_gds, write_cif
from .raster import (render_intensity, render_rgb, render_bgr, write_png, checkerboard_image,
                     COLORS)
from .preview import preview

__all__ = [
    "Pattern",
    "Box",
    "Polygon",
    "ShapeGroup",
    "box",
    "plus_fiducial",
    "line_array",
    "checkerboard",
    "l_shape",
    "nested_ls",
    "circle",
    "circle_grid",
    "wafer",
    "WAFER_SIZES_MM",
    "text",
    "flow_layout",
    "tile_quadrants",
    "write_gds",
    "write_cif",
    "render_intensity",
    "render_rgb",
    "render_bgr",
    "checkerboard_image",
    "write_png",
    "COLORS",
    "preview",
]
