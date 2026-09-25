"""Map camera pixels to projector input pixels, and record a white reference.

Shows white, vbars64, hbars64 and a single marker square. Bar edges give the
scale and a set of candidate offsets (one per bar period); the marker's edge
picks the right one. The camera is assumed square-on enough that each axis is
a straight line:  cam_x = ax * proj_x + bx,  cam_y = ay * proj_y + by.

Rerun whenever the camera or projector moves, or the exposure changes (the
white reference is exposure-specific).

    python projector/dmd_calibrate.py
"""

import argparse
import os

import cv2
import numpy as np

from capture import (CALIB_PATH, CAPTURES, DEFAULT_EXPOSURE_US, lit_median,
                     open_cam, to_u8)
from display import PatternWindow, find_projector

PERIOD = 64          # bar width used for calibration, input px


def bar_edges(profile):
    """Edge positions (to the half pixel) of a 0/1 bar profile, outliers dropped."""
    valid = profile[np.isfinite(profile)]
    mid = 0.5 * (np.percentile(valid, 95) + np.percentile(valid, 5))
    binary = (np.nan_to_num(profile, nan=-1) > mid).astype(int)
    finite = np.isfinite(profile)
    # an edge only counts between two in-image samples, not at the image border
    step = np.flatnonzero(np.diff(binary) & finite[:-1] & finite[1:])
    edges = step + 0.5
    if len(edges) < 6:
        raise SystemExit("too few bar edges found — is the projector image in "
                         "the camera's view and in focus?")
    spacing = np.median(np.diff(edges))
    # index each edge by how many half-periods it sits from the first one, so
    # a missed edge leaves a gap instead of shifting every later index
    idx = np.round((edges - edges[0]) / spacing).astype(int)
    fit = np.polyfit(idx, edges, 1)
    ok = np.abs(np.polyval(fit, idx) - edges) < 0.25 * spacing
    return edges[ok], idx[ok]


def fit_axis(profile, marker_edge_cam, marker_edge_proj, label):
    edges, idx = bar_edges(profile)
    best = None
    # edges fall on multiples of PERIOD; try every assignment of the first one
    for k0 in range(0, 200):
        proj = (idx + k0) * PERIOD
        a, b = np.polyfit(proj, edges, 1)
        miss = abs(a * marker_edge_proj + b - marker_edge_cam)
        if best is None or miss < best[0]:
            resid = np.abs(np.polyval((a, b), proj) - edges).max()
            best = (miss, a, b, resid)
    miss, a, b, resid = best
    print(f"  {label}: cam = {a:.5f} * proj + {b:.1f}   "
          f"({len(edges)} edges, max resid {resid:.2f} px, marker off by {miss:.1f} px)")
    if miss > 0.25 * PERIOD * a:
        print(f"  ! {label}: marker does not line up with any bar edge — "
              "offset may be wrong")
    return float(a), float(b)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exposure", type=float, default=DEFAULT_EXPOSURE_US)
    p.add_argument("--monitor", default=None, help="e.g. DISPLAY5 (default: auto)")
    args = p.parse_args()

    mon = find_projector(args.monitor)
    print(f"projector: {mon.name} {mon.width}x{mon.height} at ({mon.x}, {mon.y})")
    cam = open_cam(args.exposure)
    shots = {}
    try:
        with PatternWindow(mon) as win:
            for name in ("black", "white", "vbars64", "hbars64", "marker"):
                win.show(win.pattern(name))
                shots[name] = lit_median(cam)
    finally:
        cam.Close()

    os.makedirs(CAPTURES, exist_ok=True)
    for k, v in shots.items():
        cv2.imwrite(os.path.join(CAPTURES, f"calib_{k}.png"), to_u8(v))

    # Marker: white square at x 1024..1280, y 512..768. Subtracting the black
    # capture cancels the stuck-on DMD bands, which would otherwise look like
    # marker pixels; of what is left, take the most square sizeable blob.
    diff = shots["marker"] - shots["black"]
    mask = (diff > 0.5 * np.percentile(diff, 99.9)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 400:
            continue
        squareness = min(w, h) / max(w, h) * area / (w * h)
        if best is None or squareness > best[0]:
            best = (squareness, x, y)
    if best is None or best[0] < 0.6:
        raise SystemExit("marker square not found — check calib_marker.png in "
                         f"{CAPTURES}")
    mx0, my0 = float(best[1]), float(best[2])

    # Bars span the whole image, so average along them. Horizontal bars break
    # in the faulty DMD bands, so take the median across columns for those.
    white = shots["white"]
    hprof = np.median(shots["hbars64"] / np.maximum(white, 1), axis=1)
    ay, by = fit_axis(hprof, my0, 512, "y")

    # The stray light around the projection carries faint ghost copies of the
    # bars, so restrict the x profile to rows inside the image and to columns
    # clearly brighter than that surround. Vertical bars are clean everywhere.
    h, w = white.shape
    r0 = int(max(0, by + 32 * ay))
    r1 = int(min(h, ay * mon.height + by - 32 * ay))
    wcol = white[r0:r1].mean(axis=0)
    inside = wcol > 0.5 * (np.percentile(wcol, 2) + np.percentile(wcol, 98))
    vprof = np.where(inside, shots["vbars64"][r0:r1].mean(axis=0)
                     / np.maximum(wcol, 1), np.nan)
    ax, bx = fit_axis(vprof, mx0, 1024, "x")

    px0, px1 = max(0, -bx / ax), min(mon.width, (w - 1 - bx) / ax)
    py0, py1 = max(0, -by / ay), min(mon.height, (h - 1 - by) / ay)
    print(f"  camera sees input x {px0:.0f}..{px1:.0f}, y {py0:.0f}..{py1:.0f}"
          f"  of {mon.width}x{mon.height}")

    np.savez(CALIB_PATH, ax=ax, bx=bx, ay=ay, by=by, white=white.astype(np.float32),
             exposure=args.exposure, proj_w=mon.width, proj_h=mon.height)
    print(f"saved {CALIB_PATH}")


if __name__ == "__main__":
    main()
