"""How the camera's response to the projected image depends on exposure and
content: linearity, saturation and glare.

For each pattern and exposure it grabs a burst of frames and records two
brightnesses, because the projector's light is pulsed (240 Hz, colour- and
bit-plane-sequential; see camera/README.md):

  avg   the per-pixel mean over all frames = what one exposure covering many
        projector periods would see. Proportional to exposure if linear.
  top   the mean of the brightest 10 % of frames = roughly what the
        deflickered feed shows.

and, for the "square" pattern (one white square on black), how much of the
square's light lands outside it (glare / bloom / ghost reflections).

    python camera/linearity_probe.py                       # defaults below
    python camera/linearity_probe.py --exposures 34 100 400 --frames 40

Needs the camera and the projector to itself: stop ui/server.py first.
Writes a CSV, a summary, and images to projector/captures/<stamp>_linearity/.
"""

import argparse
import csv
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "projector"))
sys.path.insert(0, HERE)

from capture import new_run_dir, open_cam  # noqa: E402
from display import PatternWindow, find_projector, make_pattern  # noqa: E402
from pylon_utils import _try_set, grab_frames  # noqa: E402

EXPOSURES = [34, 50, 100, 200, 400, 1000, 4167, 8333, 14000]
PATTERNS = ["black", "white", "gray128", "gray64", "calibration", "square"]
SQUARE = 400          # input px


def pattern_image(name, w, h):
    if name == "calibration":
        return cv2.imread(os.path.join(ROOT, "patterns", "calibrate_deflicker.png"))
    if name == "square":
        img = np.zeros((h, w), np.uint8)
        img[h // 2 - SQUARE // 2:h // 2 + SQUARE // 2, w // 2 - SQUARE // 2:w // 2 + SQUARE // 2] = 255
        return img
    return make_pattern(name, w, h)


def burst(cam, n):
    frames = []
    for i, frame, _ in grab_frames(cam):
        frames.append(frame.astype(np.float32))
        if i + 1 >= n:
            break
    stack = np.stack(frames)
    means = stack.mean(axis=(1, 2))
    top = stack[means >= np.percentile(means, 90)].mean(axis=0)
    return stack.mean(axis=0), top, means


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exposures", type=float, nargs="+", default=EXPOSURES)
    p.add_argument("--patterns", nargs="+", default=PATTERNS)
    p.add_argument("--frames", type=int, default=40)
    args = p.parse_args()

    out = new_run_dir("linearity")
    monitor = find_projector()
    cam = open_cam(args.exposures[0])
    rows, avgs = [], {}
    try:
        with PatternWindow(monitor) as win:
            for name in args.patterns:
                win.show(pattern_image(name, monitor.width, monitor.height), settle=1.0)
                for e in args.exposures:
                    _try_set(cam, ["ExposureTime", "ExposureTimeAbs"], float(e))
                    burst(cam, 4)                         # flush frames at the old exposure
                    avg, top, means = burst(cam, args.frames)
                    avgs[(name, e)] = avg
                    row = {"pattern": name, "exposure_us": e,
                           "avg_mean": round(float(avg.mean()), 2),
                           "avg_sat_pct": round(100 * float((avg >= 250).mean()), 2),
                           "top_mean": round(float(top.mean()), 2),
                           "top_sat_pct": round(100 * float((top >= 250).mean()), 2),
                           "frame_mean_min": round(float(means.min()), 2),
                           "frame_mean_max": round(float(means.max()), 2)}
                    rows.append(row)
                    print(" ".join(f"{k}={v}" for k, v in row.items()), flush=True)
                    if e in (50, 400, 4167, 14000):
                        cv2.imwrite(os.path.join(out, f"{name}_{int(e)}us_avg.png"),
                                    np.clip(avg, 0, 255).astype(np.uint8))
                        cv2.imwrite(os.path.join(out, f"{name}_{int(e)}us_top.png"),
                                    np.clip(top, 0, 255).astype(np.uint8))
    finally:
        cam.Close()

    with open(os.path.join(out, "linearity.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Glare: where the square is (from an unsaturated exposure), and how much
    # light (above black) falls outside it.
    lines = []
    if "square" in args.patterns and "black" in args.patterns:
        for e in args.exposures:
            sq, bl = avgs[("square", e)], avgs[("black", e)]
            diff = np.clip(sq - bl, 0, None)
            if diff.max() < 5:
                continue
            core = diff >= 0.5 * np.percentile(diff, 99.9)
            ring = cv2.dilate(core.astype(np.uint8), np.ones((61, 61), np.uint8)).astype(bool)
            outside = ~ring
            total = diff.sum()
            lines.append(f"square at {e:g} us: core {core.sum()} px, "
                         f"light outside (beyond 30 px) {100 * diff[outside].sum() / total:.1f} % of the total, "
                         f"mean outside {diff[outside].mean():.2f} vs core {diff[core].mean():.1f} "
                         f"(core saturated {100 * (sq[core] >= 250).mean():.0f} %)")
    summary = "\n".join(lines)
    print(summary)
    with open(os.path.join(out, "summary.txt"), "w") as fh:
        fh.write(summary + "\n")
    print("saved to", out)


if __name__ == "__main__":
    main()
