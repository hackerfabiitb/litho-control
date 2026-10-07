"""The dose test's two patterns, from res_test_v01:

  focus_markers   just res_test_v01's four corner plus fiducials, to autofocus on
  res_test(t)     the whole res_test_v01 pattern, with "t = <t> s" in the bottom-left
                  corner (right of the fiducial), so each developed field says its
                  exposure time

Both are projected in blue (COLOR): the resist is exposed by blue light, and red and
blue focus at different heights, so focusing must be done in the colour that exposes.
The markers therefore pick up dose during autofocus too, on top of t.

Run:  python patterns/scripts/dose_test_patterns.py [t ...]   # PNGs in patterns/out/
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import res_test_v01 as v01  # noqa: E402
from lib._bootstrap import OUT  # noqa: E402
from lib import Pattern, text, write_png  # noqa: E402

COLOR = "blue"
LABEL_SCALE = 10      # bitmap-font pixel -> 10 x 10 px: ~110 px (~30 um) tall text
LABEL_GAP = 60        # px between the bottom-left fiducial and the label


def focus_markers() -> Pattern:
    """res_test_v01's corner fiducials alone."""
    p = Pattern("focus_markers")
    for fid, fx, fy in v01.corner_fiducials(p):
        p.place(fid, fx, fy)
    return p


def res_test(t_s) -> Pattern:
    """res_test_v01 with "t = <t_s> s" bottom-left, bottom-aligned with the fiducial."""
    p = v01.build(f"res_test_t{t_s:g}s")
    fid, fx, fy = next((f, x, y) for f, x, y in v01.corner_fiducials(p)
                       if x < p.width_px // 2 and y > p.height_px // 2)
    _, _, fw, fh = fid.bbox()
    label = text(f"t = {t_s:g} s", scale=LABEL_SCALE)
    _, _, lw, lh = label.bbox()
    p.place(label, fx + fw + LABEL_GAP, fy + fh - lh)
    return p


if __name__ == "__main__":
    times = [float(t) for t in sys.argv[1:]] or [10, 120]
    path = OUT / f"focus_markers_{COLOR}.png"
    write_png(focus_markers(), path, COLOR)
    print(f"wrote {path}")
    for t in times:
        path = OUT / f"res_test_t{t:03g}s_{COLOR}.png"
        write_png(res_test(t), path, COLOR)
        print(f"wrote {path}")
