"""Starter pattern: a centred square plus a corner plus-fiducial.

Run:  python patterns/scripts/make_box.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import OUT
from lib import Pattern, box, plus_fiducial, write_gds, write_png

p = Pattern("box_test")

# a 500x500 px square, centred on the canvas
side = 500
p.place(box(side, side), (p.width_px - side) // 2, (p.height_px - side) // 2)

# a plus fiducial near the top-left corner
p.place(plus_fiducial(p.um(10)), 60, 60)

write_gds(p, OUT / "box_test.gds")
for c in ("white", "red", "blue"):
    write_png(p, OUT / f"box_test_{c}.png", c)

print(f"wrote {OUT / 'box_test.gds'} and box_test_[white|red|blue].png")
