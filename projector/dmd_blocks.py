"""Find DMD columns that do not show what they are sent, and watch them over time.

Alternates two patterns, --repeats times:

  black    every mirror should be off. Score: brightness / white, per column.
           Healthy columns sit at the stray-light floor (~0.10 in this rig).
  hbars64  64-row stripes. Score: peak-to-peak stripe contrast / white, per
           column (~0.2-0.4 when healthy in this rig, ~0 where lost).

Columns whose black level is well above, or whose contrast is well below, the
local median (+-512 input px) are flagged and merged into ranges, reported in projector input
pixels (and DMD mirror columns = input x / 2). Also prints a per-block table
for each repeat so drift or intermittency shows up. Blocks are 256 input px
(128 mirrors) starting at --block-origin, which defaults to where the faulty
bands were found on 2026-09-25.

Needs projector/dmd_calibrate.py to have been run first.

    python projector/dmd_blocks.py
    python projector/dmd_blocks.py --repeats 20       # longer watch
"""

import argparse
import csv
import os

import cv2
import numpy as np

from capture import load_calib, lit_median, new_run_dir, open_cam, to_u8
from display import PatternWindow, find_projector

BAR = 64


def column_scores(black, hbars, calib, lit_rows):
    """Per camera column: black level, and hbars64 stripe contrast.

    Contrast is the amplitude of the stripe-frequency component of the
    column's vertical profile, scaled to peak-to-peak. Being phase-free, it
    does not care that the camera is slightly rotated relative to the DMD
    (which shifts the stripes by a few rows from one side to the other).
    """
    white = np.maximum(calib["white"], 1)
    rows = np.flatnonzero(lit_rows)
    b = black[rows] / white[rows]
    h = hbars[rows] / white[rows]
    black_level = np.median(b, axis=0)
    period = 2 * BAR * calib["ay"]                      # camera rows
    # whole number of periods so the DFT bin is clean
    n = int(len(rows) // period * period)
    phase = np.exp(-2j * np.pi * np.arange(n) / period)[:, None]
    amp = 2 * np.abs(((h[:n] - h[:n].mean(axis=0)) * phase).mean(axis=0))
    contrast = amp * np.pi / 2                          # square wave p-p
    return black_level, contrast


def local_median(values, valid, half_width):
    """Median of the valid entries within +-half_width columns of each column."""
    out = np.full(values.shape, np.nan)
    for i in range(len(values)):
        lo, hi = max(0, i - half_width), i + half_width + 1
        window = values[lo:hi][valid[lo:hi]]
        if window.size:
            out[i] = np.median(window)
    return out


def flag(black_level, contrast, valid, half_width):
    """Columns well off their neighbourhood. Compared against a local median
    (a 256-px faulty band is a minority of the window), because illumination
    and stray light make the healthy level drift across the image."""
    bl0 = local_median(black_level, valid, half_width)
    c0 = local_median(contrast, valid, half_width)
    bad = valid & ((black_level > 1.5 * bl0 + 0.02) | (contrast < 0.7 * c0))
    return bad, float(np.nanmedian(bl0[valid])), float(np.nanmedian(c0[valid]))


def ranges(mask, proj_x, min_width=16, max_gap=24):
    """Contiguous True runs of `mask` as (x0, x1) in projector input px.
    Runs separated by less than `max_gap` input px are merged: the noisy
    faulty bands dip below threshold here and there."""
    mask = np.asarray(mask, bool).copy()
    gap = int(max_gap * abs(proj_x[1] - proj_x[0]) ** -1)
    idx = np.flatnonzero(mask)
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= gap:
            mask[a:b] = True
    out, start = [], None
    for i, v in enumerate(np.append(mask, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            x0, x1 = proj_x[start], proj_x[i - 1]
            if x1 - x0 >= min_width:
                out.append((int(round(x0)), int(round(x1))))
            start = None
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeats", type=int, default=8)
    p.add_argument("--block-origin", type=int, default=128,
                   help="input-px x of the first block edge (default 128)")
    p.add_argument("--monitor", default=None)
    args = p.parse_args()

    calib = load_calib()
    mon = find_projector(args.monitor)
    out = new_run_dir("blocks")
    white = calib["white"]
    # The projected area comes from the mapping (with a margin for blur), not
    # from thresholding white — the stray-light surround is nearly as bright.
    proj_y = (np.arange(white.shape[0]) - calib["by"]) / calib["ay"]
    lit_rows = (proj_y >= 32) & (proj_y < mon.height - 32)
    cam_x = np.arange(white.shape[1])
    proj_x = (cam_x - calib["bx"]) / calib["ax"]
    valid = (proj_x >= 32) & (proj_x < mon.width - 32)

    edges = np.arange(args.block_origin - 256, mon.width + 256, 256)
    blocks = [(x0, x1) for x0, x1 in zip(edges[:-1], edges[1:])
              if valid[(proj_x >= x0 + 16) & (proj_x < x1 - 16)].any()]
    header = " ".join(f"{x0:>5d}" for x0, _ in blocks)

    bls, cons = [], []
    cam = open_cam(float(calib["exposure"]))
    try:
        with PatternWindow(mon) as win:
            print(f"per-block scores; columns are block start x (input px)\n"
                  f"rep  metric   {header}")
            for rep in range(args.repeats):
                win.show(win.pattern("black"))
                black = lit_median(cam)
                win.show(win.pattern("hbars64"))
                hbars = lit_median(cam)
                bl, con = column_scores(black, hbars, calib, lit_rows)
                bls.append(bl)
                cons.append(con)
                if rep == 0:
                    cv2.imwrite(os.path.join(out, "black.png"), to_u8(black, True))
                    cv2.imwrite(os.path.join(out, "hbars64.png"), to_u8(hbars, True))
                for label, arr in (("black", bl), ("contr", con)):
                    vals = []
                    for x0, x1 in blocks:
                        sel = valid & (proj_x >= x0 + 16) & (proj_x < x1 - 16)
                        vals.append(100 * np.median(arr[sel]))
                    print(f"{rep:3d}  {label:7s}  "
                          + " ".join(f"{v:5.0f}" for v in vals))
    finally:
        cam.Close()

    bls, cons = np.array(bls), np.array(cons)
    half = int(512 * calib["ax"])                    # +-512 input px
    bad_each = [flag(b, c, valid, half)[0] for b, c in zip(bls, cons)]
    bad_frac = np.mean(bad_each, axis=0)
    _, bl0, c0 = flag(np.median(bls, 0), np.median(cons, 0), valid, half)
    print(f"\nhealthy column: black level {bl0:.3f}, stripe contrast {c0:.3f}")
    always = ranges(bad_frac >= 0.99, proj_x)
    sometimes = ranges((bad_frac > 0) & (bad_frac < 0.99), proj_x)
    print("bad in every repeat (input px -> mirror cols):")
    for x0, x1 in always or [("none", "")]:
        print(f"  {x0}..{x1}" + (f"   mirrors {x0 // 2}..{x1 // 2}" if x1 != "" else ""))
    if sometimes:
        print("bad in some repeats only (intermittent):")
        for x0, x1 in sometimes:
            print(f"  {x0}..{x1}   mirrors {x0 // 2}..{x1 // 2}")
    seen = proj_x[valid]
    print(f"(camera covers input x {seen.min():.0f}..{seen.max():.0f} of 0..{mon.width})")

    with open(os.path.join(out, "columns.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["cam_x", "proj_x", "valid", "black_med", "black_max",
                     "contrast_med", "contrast_min", "bad_frac"])
        for i in range(len(cam_x)):
            wr.writerow([i, round(float(proj_x[i]), 1), int(valid[i]),
                         round(float(np.median(bls[:, i])), 4),
                         round(float(bls[:, i].max()), 4),
                         round(float(np.median(cons[:, i])), 4),
                         round(float(cons[:, i].min()), 4),
                         round(float(bad_frac[i]), 3)])
    print(f"per-column scores and first-repeat images in {out}")


if __name__ == "__main__":
    main()
