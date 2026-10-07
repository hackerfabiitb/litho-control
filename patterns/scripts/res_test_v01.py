"""Resolution test v01: nested L's and pitch=4x line arrays, with plus fiducials in the 4
corners. Single field -- no quadrant tiling.

Each logical group is one block for flow_layout: all the L's are one block, the line
arrays are one block.

The pieces are functions so other scripts can reuse them: the dose test's two patterns
(scripts/dose_test_patterns.py) are this pattern with an exposure-time label, and its
corner fiducials alone.

Run:  python patterns/scripts/res_test_v01.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import OUT  # noqa: E402
from lib import (  # noqa: E402
    Pattern,
    ShapeGroup,
    nested_ls,
    line_array,
    plus_fiducial,
    flow_layout,
    write_png,
)

# --- knobs -----------------------------------------------------------------------
MAX_W = 3600  # wrap width for the top-level block flow
GAP = 120  # spacing between blocks / packed arrays
MARGIN = 40  # fiducial inset from each canvas corner
FIDUCIAL_SQ = 60  # plus-fiducial square size (bbox is 3x this)
L_LINE_WIDTHS = [4, 17, 35]  # nested-L line widths (px)
ARRAY_WIDTHS = (17, 23, 29, 35)  # line-array widths (px); pitch = 4 x line_width
OUT_STEM = "res_test_v01"  # output file stem (also what live_reload.py watches)


def test_block() -> ShapeGroup:
    """The nested L's and the line arrays, origin (0, 0), ~3435 x 1303 px.

    The original layout (line arrays wrapped at 1200 px in one column beside the L's)
    was 3407 x 2312 px: taller than the 2160 px field, so the smallest L was cut off at
    the top and the arrays ran into the bottom-right fiducial. Same features now, laid
    out to fit between the corner fiducials: the two largest array widths beside the
    L's, two per row; the two smallest in a row under the L's."""
    # block 1: nested L's (one per line width) -- kept as a single block
    l_block = nested_ls(n=5, line_widths=L_LINE_WIDTHS)

    # line arrays, pitch = 4 x line_width, both orientations, at each width
    line_groups = [
        line_array(w, n_lines=4, pitch_factor=4, orientation=o)
        for w in ARRAY_WIDTHS
        for o in ("h", "v")
    ]
    small, large = line_groups[:4], line_groups[4:]          # 17, 23 px | 29, 35 px
    beside = flow_layout(large, max_width=1200, gap=GAP // 2)  # 2 per row
    under = flow_layout(small, max_width=MAX_W, gap=GAP // 2)  # one row
    _, _, lw, _ = l_block.bbox()
    left = flow_layout([l_block, under], max_width=lw, gap=GAP // 2, sort_by_height=False)

    # pack the blocks (each kept whole, order preserved)
    return flow_layout([left, beside], max_width=MAX_W, gap=GAP, sort_by_height=False)


def corner_fiducials(p: Pattern) -> list:
    """(group, x, y) for a plus fiducial in each of the 4 corners, MARGIN in."""
    fid = plus_fiducial(FIDUCIAL_SQ)
    fw, fh = fid.bbox()[2], fid.bbox()[3]
    return [(fid, fx, fy)
            for fx in (MARGIN, p.width_px - MARGIN - fw)
            for fy in (MARGIN, p.height_px - MARGIN - fh)]


def build(name: str = "res_test_v01") -> Pattern:
    """The whole pattern: the test block centred, fiducials in the corners."""
    p = Pattern(name)
    block = test_block()
    _, _, bw, bh = block.bbox()
    p.place(block, (p.width_px - bw) // 2, (p.height_px - bh) // 2)
    for fid, fx, fy in corner_fiducials(p):
        p.place(fid, fx, fy)
    return p


if __name__ == "__main__":
    p = build()
    _, _, bw, bh = test_block().bbox()
    print(f"combined block bbox: {bw} x {bh} px")
    # write_gds(p, OUT / f"{OUT_STEM}.gds")
    for c in ("white",):  # or ("white", "red", "blue")
        write_png(p, OUT / f"{OUT_STEM}_{c}.png", c)
    print(f"wrote {OUT / OUT_STEM}_white.png")
