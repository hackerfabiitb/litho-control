"""Full resolution + checkerboard test pattern, tiled into all 4 quadrants.

The test block is built inline here (not in ``lib``) because it's meant to be tweaked:
adjust the widths, pitch factors, line counts, checkerboard sizes, or layout to taste.

Contents of one block (fits within 1920 x 1080, then tiled to the 4 quadrants):
  * pixel line arrays:  widths 1..10 px, each x {h, v} x {pitch_factor 2, 5}
  * micron line arrays: widths 1..10 um -> nearest px, same 4 types
  * checkerboards at 5 um and 10 um square size
  * a plus fiducial in the corner (so every quadrant gets one)

Run:  python patterns/scripts/res_test_v00.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import OUT
from lib import (
    Pattern,
    line_array,
    checkerboard,
    plus_fiducial,
    flow_layout,
    tile_quadrants,
    write_gds,
    write_png,
)

# --- knobs -----------------------------------------------------------------------
MAX_W, MAX_H = 1880, 1040   # block must fit in a quadrant (leave a margin below 1920x1080)
GAP = 24                    # spacing between packed sub-blocks, px
MARGIN = 20                 # black gutter between the 4 quadrants, px
N_LINES = 3                 # lines per array
LENGTH = 60                 # line length px (short keeps the block compact; pitch is what matters)
PITCH_FACTORS = (2, 5)      # edge-to-edge gap = pitch_factor * line_width
ORIENTATIONS = ("v", "h")

p = Pattern("res_test")


def line_matrix(widths):
    """One line_array per (width x pitch_factor x orientation) -> 4 types per width."""
    groups = []
    for w in widths:
        for pf in PITCH_FACTORS:
            for orient in ORIENTATIONS:
                groups.append(
                    line_array(w, n_lines=N_LINES, pitch_factor=pf, orientation=orient, length=LENGTH)
                )
    return groups


# assemble the sub-blocks
groups = []
groups += line_matrix(range(1, 11))                        # 1..10 px
groups += line_matrix([p.um(u) for u in range(1, 11)])     # 1..10 um -> px
for size_um in (5, 10):                                    # checkerboards ~350 px on a side
    s = p.um(size_um)
    n = max(2, round(350 / s))
    groups.append(checkerboard(s, nx=n, ny=n))

# shelf-pack everything into one tight block
block = flow_layout(groups, max_width=MAX_W, gap=GAP)

# drop a plus fiducial into the block's bottom-right corner
fid = plus_fiducial(p.um(10))
_, _, bw, bh = block.bbox()
fw, fh = fid.bbox()[2], fid.bbox()[3]
block.add(fid.translated(min(bw, MAX_W - fw) - fw, max(0, bh - fh)))

_, _, bw, bh = block.bbox()
print(f"test block bbox: {bw} x {bh} px (must fit within {MAX_W} x {MAX_H})")

# tile into the 4 quadrants and export
tile_quadrants(p, block, margin=MARGIN)
write_gds(p, OUT / "res_test.gds")
for c in ("white", "red", "blue"):
    write_png(p, OUT / f"res_test_{c}.png", c)

print(f"wrote {OUT / 'res_test.gds'} and res_test_[white|red|blue].png")
