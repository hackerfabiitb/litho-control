"""Single-call pattern builders. Each returns a :class:`ShapeGroup` with local origin
(0, 0), so results can be composed and placed anywhere with ``Pattern.place``.

All dimensions are in integer pixels unless they come from ``Pattern.um(...)``.
"""

from __future__ import annotations

import math

from .scene import Box, Polygon, ShapeGroup, DEFAULT_FIELD_UM, DEFAULT_WIDTH_PX
from .text import text

_DEFAULT_UM_PER_PX = DEFAULT_FIELD_UM / DEFAULT_WIDTH_PX


def box(w: int, h: int, intensity: int = 255) -> ShapeGroup:
    """A single filled rectangle with top-left at the local origin."""
    return ShapeGroup([Box(0, 0, w, h, intensity)])


def plus_fiducial(square_size: int, intensity: int = 255) -> ShapeGroup:
    """A plus/cross of 5 identical squares: one centre + 4 orthogonal neighbours.

    Overall bounding box is ``3 * square_size`` on each side.
    """
    s = square_size
    # grid positions (col, row) in units of s: centre plus up/down/left/right
    cells = [(1, 1), (1, 0), (1, 2), (0, 1), (2, 1)]
    return ShapeGroup([Box(c * s, r * s, s, s, intensity) for c, r in cells])


def line_array(
    line_width: int,
    n_lines: int = 4,
    pitch_factor: float = 2,
    orientation: str = "v",
    length: int | None = None,
    intensity: int = 255,
) -> ShapeGroup:
    """An array of ``n_lines`` parallel lines.

    ``pitch`` is the edge-to-edge gap between adjacent lines and equals
    ``pitch_factor * line_width`` (so the centre-to-centre period is
    ``line_width * (1 + pitch_factor)``).

    ``orientation``: ``'v'`` = vertical lines packed left->right; ``'h'`` = horizontal
    lines stacked top->down. ``length`` is the long dimension of each line (defaults to a
    compact value).
    """
    lw = int(line_width)
    if lw < 1:
        raise ValueError("line_width must be >= 1 px")
    gap = round(pitch_factor * lw)  # edge-to-edge spacing, integer px
    step = lw + gap  # centre-to-centre period
    cross = n_lines * lw + (n_lines - 1) * gap  # extent across the array
    if length is None:
        length = max(6 * lw, cross)

    shapes = []
    for i in range(n_lines):
        off = i * step
        if orientation == "v":
            shapes.append(Box(off, 0, lw, length, intensity))
        elif orientation == "h":
            shapes.append(Box(0, off, length, lw, intensity))
        else:
            raise ValueError("orientation must be 'v' or 'h'")
    return ShapeGroup(shapes)


def checkerboard(
    square_size: int, nx: int, ny: int, intensity: int = 255
) -> ShapeGroup:
    """An ``nx`` by ``ny`` checkerboard of filled squares (top-left square filled)."""
    s = int(square_size)
    shapes = []
    for j in range(ny):
        for i in range(nx):
            if (i + j) % 2 == 0:
                shapes.append(Box(i * s, j * s, s, s, intensity))
    return ShapeGroup(shapes)


def l_shape(
    line_width: int,
    n: int,
    q: int,
    um_per_px: float = _DEFAULT_UM_PER_PX,
    min_font_scale: int = 3,
    label: bool = True,
    intensity: int = 255,
) -> ShapeGroup:
    """``n`` horizontal lines of length ``q`` with a checkerboard block on the right.

    The checkerboard has side length ``2n - 1`` squares, with filled squares touching
    the horizontal lines exactly. ``n - 1`` additional stepped L-shapes extend rightward
    from the checkerboard edge and sit on the checkerboard gaps.

    When ``label`` is true, the line width is converted to microns
    (``line_width * um_per_px``) and drawn to 2 decimal places to the left of the L,
    vertically centered on the horizontal arm. The label's font scale tracks ``line_width``
    but never drops below ``min_font_scale`` (so small L's stay legible).
    """
    lw = int(line_width)
    if lw < 1:
        raise ValueError("line_width must be >= 1 px")
    n = int(n)

    shapes = []
    shapes.extend([Box(0, i * (lw + lw), q, lw, intensity) for i in range(n)])

    board_x = q
    board = checkerboard(lw, 2 * n - 1, 2 * n - 1, intensity)
    shapes.extend(shape.translated(board_x, 0) for shape in board.shapes)

    right_x = board_x + (2 * n - 1) * lw
    stem_len = round(1.5 * q)  # vertical stems are 1.5x the base length q
    for i in range(max(0, n - 1)):
        length = q + i * lw
        y = (2 * i + 1) * lw
        shapes.append(Box(right_x, y, length + i * lw, lw, intensity))
        # vertical line: all tops line up at (2*lw - stem_len); grow the height down to
        # the existing bottom (y + lw) so it still meets its horizontal segment
        shapes.append(
            Box(right_x + length + i * lw, 2 * lw - stem_len, lw, y - lw + stem_len, intensity)
        )

    if label:
        # micron value of the line width, to 2 decimal places
        lbl = text(f"{lw * um_per_px:.2f}", scale=max(lw, int(min_font_scale)), intensity=intensity)
        _, _, lbl_w, lbl_h = lbl.bbox()
        gap = 2 * max(lw, int(min_font_scale))
        arm_center = (2 * n - 1) * lw // 2  # vertical center of the horizontal arm block
        shapes.extend(lbl.translated(-gap - lbl_w, arm_center - lbl_h // 2).shapes)

    # normalize so the group's bbox top-left sits at the local origin (0, 0)
    group = ShapeGroup(shapes)
    bx, by, _, _ = group.bbox()
    return group.translated(-bx, -by)


def nested_ls(
    n: int,
    line_widths,
    q_factor: float = 12,
    um_per_px: float = _DEFAULT_UM_PER_PX,
    min_font_scale: int = 3,
    label: bool = True,
    intensity: int = 255,
) -> ShapeGroup:
    """Place one :func:`l_shape` per line width, all sharing a common (top-left) origin.

    Each L already has a top-left origin, so they are simply overlaid at (0, 0); larger
    line widths give larger L's that nest around the smaller ones. Intersections between
    L's are not checked. Labels (see :func:`l_shape`) form a column on the left.
    """
    out = ShapeGroup()
    for lw in line_widths:
        q = max(1, round(q_factor * lw))
        out.add(l_shape(lw, n, q, um_per_px, min_font_scale, label, intensity))
    return out


def circle(
    diameter: int, n_segments: int | None = None, intensity: int = 255
) -> ShapeGroup:
    """A filled circle of ``diameter`` px, approximated as a regular polygon.

    Top-left of its bbox sits at the local origin, matching the other builders. The
    default segment count holds the chord (flat-edge) error under half a pixel:
    error ~= r * (pi/n)^2 / 2, so n ~= pi * sqrt(r) is the break-even point.
    """
    if diameter < 1:
        raise ValueError("diameter must be >= 1 px")
    r = diameter / 2
    if n_segments is None:
        n_segments = max(32, math.ceil(math.pi * math.sqrt(r)))
    pts = [
        (
            round(r + r * math.cos(2 * math.pi * i / n_segments)),
            round(r + r * math.sin(2 * math.pi * i / n_segments)),
        )
        for i in range(n_segments)
    ]
    return ShapeGroup([Polygon(pts, intensity)])


def circle_grid(
    diameter: int,
    n: int = 3,
    pitch_factor: float = 4,
    intensity: int = 255,
) -> ShapeGroup:
    """An ``n`` x ``n`` square grid of circles.

    ``pitch_factor`` follows :func:`line_array`'s convention: the edge-to-edge gap is
    ``pitch_factor * diameter``, so the centre-to-centre period is
    ``diameter * (1 + pitch_factor)``.
    """
    d = int(diameter)
    step = d + round(pitch_factor * d)
    out = ShapeGroup()
    for i in range(n):
        for j in range(n):
            out.add(circle(d, intensity=intensity).translated(i * step, j * step))
    return out


# Standard wafer sizes: name -> (diameter_mm, flat_length_mm). Primary flat only.
WAFER_SIZES_MM = {
    "2inch": (50.8, 15.8),
    "4inch": (100.0, 32.5),
}


def wafer(
    diameter: int,
    flat_length: int,
    flat_side: str = "bottom",
    stroke: int = 0,
    n_segments: int | None = None,
    intensity: int = 255,
) -> ShapeGroup:
    """A wafer outline: a circle of ``diameter`` px with one straight primary flat.

    ``flat_length`` is the chord length of the flat, in px; it must be <= ``diameter``.
    ``flat_side`` places the flat on the ``"bottom"`` (default), ``"top"``, ``"left"``
    or ``"right"`` edge.

    ``stroke`` selects the representation:
      * ``0`` (default) -> a solid filled wafer (single polygon).
      * ``> 0``         -> a ring outline ``stroke`` px thick, so the wafer can be
                           overlaid on a pattern without painting over it. The ring is
                           the boundary scaled inward about the centre, emitted as a
                           strip of quads (no fill, no seam).

    Top-left of the bbox sits at the local origin, matching the other builders.
    """
    if diameter < 1:
        raise ValueError("diameter must be >= 1 px")
    r = diameter / 2
    half = flat_length / 2
    if not 0 < flat_length <= diameter:
        raise ValueError("flat_length must be in (0, diameter]")
    try:
        base = {
            "bottom": math.pi / 2,
            "top": -math.pi / 2,
            "right": 0.0,
            "left": math.pi,
        }[flat_side]
    except KeyError:
        raise ValueError(f"flat_side must be bottom/top/left/right, got {flat_side!r}")

    if n_segments is None:
        n_segments = max(64, math.ceil(math.pi * math.sqrt(r)))

    # The flat is the chord subtending half-angle phi about the centre; keep the major
    # arc (everything but the flat) and let the closing edge stand in for the flat.
    phi = math.asin(half / r)
    e_start = base + phi  # first flat endpoint
    span = 2 * math.pi - 2 * phi  # major arc swept the long way round
    outer = [
        (r * math.cos(e_start + span * i / n_segments),
         r * math.sin(e_start + span * i / n_segments))
        for i in range(n_segments + 1)
    ]

    if stroke <= 0:
        return ShapeGroup([Polygon(_to_origin(outer), intensity)])

    # Ring: scale the boundary inward about the centre and join with quads. Uniform
    # scaling keeps every quad convex (the wafer is convex), so there are no artefacts.
    if stroke >= r:
        raise ValueError("stroke must be < radius")
    k = (r - stroke) / r
    inner = [(x * k, y * k) for x, y in outer]
    o = _to_origin(outer)  # shared shift so outer/inner stay registered
    ox, oy = outer[0][0] - o[0][0], outer[0][1] - o[0][1]
    inn = [(round(x - ox), round(y - oy)) for x, y in inner]
    quads = [
        Polygon([o[i], o[(i + 1) % len(o)], inn[(i + 1) % len(inn)], inn[i]], intensity)
        for i in range(len(o))  # last i wraps once to bridge the two flat endpoints
    ]
    return ShapeGroup(quads)


def _to_origin(points):
    """Round float points to int px and shift so the bbox top-left is (0, 0)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    ox, oy = min(xs), min(ys)
    return [(round(x - ox), round(y - oy)) for x, y in points]
