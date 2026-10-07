"""Placement helpers: pack groups into rows, and tile a block into the 4 quadrants."""

from __future__ import annotations

from .scene import ShapeGroup


def flow_layout(
    groups, max_width: int, gap: int = 24, sort_by_height: bool = True
) -> ShapeGroup:
    """Shelf-pack ``groups`` left->right, wrapping to a new row (shelf) at ``max_width``.

    With ``sort_by_height`` the groups are placed tallest-first, which keeps each shelf
    tightly filled and minimises total height (standard shelf-packing heuristic). Each
    group is normalised so its own bbox origin lands at the placement point. Returns one
    combined :class:`ShapeGroup` with origin (0, 0).
    """
    items = list(groups)
    if sort_by_height:
        items = sorted(items, key=lambda g: g.bbox()[3], reverse=True)

    out = ShapeGroup()
    x = 0
    y = 0
    row_h = 0
    for g in items:
        gx, gy, gw, gh = g.bbox()
        if gw > max_width:
            raise ValueError(f"group width {gw} exceeds max_width {max_width}")
        if x > 0 and x + gw > max_width:
            # wrap to next shelf
            x = 0
            y += row_h + gap
            row_h = 0
        # place so the group's bbox top-left lands at (x, y)
        out.add(g.translated(x - gx, y - gy))
        x += gw + gap
        row_h = max(row_h, gh)
    return out


def tile_quadrants(pattern, block: ShapeGroup, margin: int = 0) -> None:
    """Place ``block`` in each of the 4 canvas quadrants, inset by ``margin`` px.

    The margin leaves a black gutter between quadrants (and around the frame), which keeps
    the four copies visually separated on the projected field.
    """
    hw = pattern.width_px // 2
    hh = pattern.height_px // 2
    bx, by, bw, bh = block.bbox()
    max_w = hw - margin
    max_h = hh - margin
    if bw > max_w or bh > max_h:
        raise ValueError(
            f"block bbox {bw} x {bh} does not fit within quadrant {max_w} x {max_h} "
            f"after applying margin {margin}"
        )
    for ox in (0, hw):
        for oy in (0, hh):
            pattern.place(block, ox + margin, oy + margin)
