"""Step-height test: four 100 um squares at the corners of a 9 mm square.

The 9 mm square is the layout reference, not geometry -- nothing is drawn for it. The
four squares sit flush in its corners, so the mask bbox is exactly 9 x 9 mm and the
corner-to-corner spacing between square centres is 9000 - 100 = 8900 um.

Scale is fixed at 1 px : 1 um (PX_PER_UM), so a 100 um square is exactly 100 px and the
canvas is 9000 x 9000 px. GDS (plus the CIF write_gds emits beside it) is the
deliverable; the PNG is re-rendered at a reduced scale (~PREVIEW_MAX_PX across) purely
to eyeball the layout.

Run:  python patterns/scripts/step_test.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import OUT
from lib import Pattern, box, write_gds, write_png

# --- knobs (all lengths in MICRONS) ----------------------------------------------
PX_PER_UM = 1  # projection scale for the mask: 1 px : 1 um
PREVIEW_MAX_PX = 1200  # eyeball PNG is scaled so the field spans ~this many px

FIELD_UM = 9000  # side of the reference square whose corners hold the features
SQUARE_UM = 100  # side of each corner square


def build(scale: float) -> Pattern:
    """Place the four corner squares on a FIELD_UM square canvas at ``scale`` px/um."""

    def px(value_um: float) -> int:
        return round(value_um * scale)

    side = px(FIELD_UM)
    sq = max(1, px(SQUARE_UM))  # stays visible even at preview scale

    p = Pattern("step_test", field_um=side / scale, width_px=side, height_px=side)
    for x in (0, side - sq):
        for y in (0, side - sq):
            p.place(box(sq, sq), x, y)
    return p


mask = build(PX_PER_UM)
print(
    f"mask:    {mask.width_px} x {mask.height_px} px  "
    f"({FIELD_UM / 1000:g} x {FIELD_UM / 1000:g} mm)"
)
print(f"squares: 4 x {SQUARE_UM} um, centres {FIELD_UM - SQUARE_UM} um apart")
write_gds(mask, OUT / "step_test.gds")

preview_scale = PREVIEW_MAX_PX / FIELD_UM
pv = build(preview_scale)
print(f"preview: {pv.width_px} x {pv.height_px} px @ {preview_scale:.4f} px/um")
write_png(pv, OUT / "step_test_white.png", "white")
print(f"wrote {OUT / 'step_test'}.{{gds,cif}} and {OUT / 'step_test_white.png'}")
