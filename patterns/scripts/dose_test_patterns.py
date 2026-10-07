"""The dose test's two patterns, built from res_test_v01's pieces:

  focus_markers   four plus fiducials near the corners, to autofocus on
  res_test(t)     the same fiducials, res_test_v01's nested L's (with a short reach, so
                  they're narrower) on the left, and on the right the exposure time in
                  big text ("60s") with "HELLO / FRESHIES!!" under it

The right-hand fiducials and everything else stay left of the DMD fault band at input
x 3456-3712 (mirror columns 1728-1856, measured in the camera 2026-10-07: patterns/
README.md), which shows wrong data. res_test_v01's line arrays aren't used.

Both are projected in blue (COLOR): the resist is exposed by blue light, and red and
blue focus at different heights, so focusing is done in the colour that exposes. The
markers therefore pick up dose during autofocus too, on top of t.

Run:  python patterns/scripts/dose_test_patterns.py [t ...]   # PNGs in patterns/out/
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import res_test_v01 as v01  # noqa: E402
from lib._bootstrap import OUT  # noqa: E402
from lib import Pattern, ShapeGroup, nested_ls, plus_fiducial, text, write_png  # noqa: E402

COLOR = "blue"
BAND_X = (3456, 3712)        # DMD fault band, input px: keep everything left of it
MARGIN = v01.MARGIN          # 40 px: fiducial inset from the field's corners (and the band)
GAP = v01.GAP                # 120 px between blocks
REACH_FACTOR = 2             # L's: checkerboard -> first vertical line = 2 x line width
                             # (res_test_v01: 12 x)
WIDEST_TIME = "888s"         # the exposure text is sized for this, the same for every t
GREETING = ("HELLO", "FRESHIES!!")


def _right_edge(p: Pattern) -> int:
    """x the pattern must stay left of."""
    return min(p.width_px, BAND_X[0]) - MARGIN


def fiducials(p: Pattern) -> list:
    """(group, x, y): a plus fiducial in each corner of the usable field (the right
    pair left of the fault band)."""
    fid = plus_fiducial(v01.FIDUCIAL_SQ)
    _, _, fw, fh = fid.bbox()
    return [(fid, fx, fy)
            for fx in (MARGIN, _right_edge(p) - fw)
            for fy in (MARGIN, p.height_px - MARGIN - fh)]


def focus_markers() -> Pattern:
    p = Pattern("focus_markers")
    for fid, fx, fy in fiducials(p):
        p.place(fid, fx, fy)
    return p


def _text(string, scale) -> ShapeGroup:
    """Bitmap text with its bounding box at the origin (glyphs start a little in)."""
    g = text(string, scale=scale)
    bx, by, _, _ = g.bbox()
    return g.translated(-bx, -by)


def _text_lines(lines, scale) -> ShapeGroup:
    """Lines of bitmap text, each centred on the widest, one line height apart."""
    groups = [_text(s, scale) for s in lines]
    width = max(g.bbox()[2] for g in groups)
    line_h = max(g.bbox()[3] for g in groups)
    out = ShapeGroup()
    for i, g in enumerate(groups):
        out.add(g.translated((width - g.bbox()[2]) // 2, i * round(1.4 * line_h)))
    return out


def _largest_scale(lines, max_w, max_h) -> int:
    s = 1
    while True:
        _, _, w, h = _text_lines(lines, s + 1).bbox()
        if w > max_w or h > max_h:
            return s
        s += 1


def res_test(t_s) -> Pattern:
    p = Pattern(f"res_test_{t_s:g}s")
    for fid, fx, fy in fiducials(p):
        p.place(fid, fx, fy)

    l_block = nested_ls(n=5, line_widths=v01.L_LINE_WIDTHS, reach_factor=REACH_FACTOR)
    _, _, lw, lh = l_block.bbox()

    # the text column: everything between the L's and the right edge, between the
    # fiducial rows; the time as big as fits, the greeting under it, half its scale
    fh = 3 * v01.FIDUCIAL_SQ
    top, bottom = MARGIN + fh + GAP // 2, p.height_px - MARGIN - fh - GAP // 2
    x0 = MARGIN + lw + GAP
    col_w, col_h = _right_edge(p) - x0, bottom - top
    big = _largest_scale([WIDEST_TIME], col_w, col_h * 0.55)
    small = min(_largest_scale(GREETING, col_w, col_h * 0.45), max(1, big // 2))
    time_txt = _text(f"{t_s:g}s", big)
    greet = _text_lines(GREETING, small)
    _, _, tw, th = time_txt.bbox()
    _, _, gw, gh = greet.bbox()
    block_h = th + GAP + gh
    y = top + (col_h - block_h) // 2
    p.place(time_txt, x0 + (col_w - tw) // 2, y)
    p.place(greet, x0 + (col_w - gw) // 2, y + th + GAP)

    # the L's, vertically centred, left of the text
    p.place(l_block, MARGIN, (p.height_px - lh) // 2)
    return p


if __name__ == "__main__":
    times = [float(t) for t in sys.argv[1:]] or [10, 60, 120]
    path = OUT / f"focus_markers_{COLOR}.png"
    write_png(focus_markers(), path, COLOR)
    print(f"wrote {path}")
    for t in times:
        path = OUT / f"res_test_{t:03g}s_{COLOR}.png"
        write_png(res_test(t), path, COLOR)
        print(f"wrote {path}")
