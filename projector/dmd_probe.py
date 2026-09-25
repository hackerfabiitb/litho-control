"""Show test patterns on the projector and save what the camera sees.

For each pattern: display it full-screen, grab a burst, drop the black flicker
frames, and save the median of the rest (plus a temporal std map showing
pixels that changed between lit frames). A contact sheet of every pattern goes
in sheet.png.

    python projector/dmd_probe.py black white hbars64 vbars64 checker128
    python projector/dmd_probe.py grid --exposure 1000

Patterns: see display.PATTERN_HELP.
"""

import argparse
import os

import cv2
import numpy as np

from capture import DEFAULT_EXPOSURE_US, lit_stack, new_run_dir, open_cam, to_u8
from display import PATTERN_HELP, PatternWindow, find_projector


def main():
    p = argparse.ArgumentParser(description=__doc__ + "\n" + PATTERN_HELP,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("patterns", nargs="+")
    p.add_argument("--exposure", type=float, default=DEFAULT_EXPOSURE_US,
                   help="us (default 100; 1000 saturates white)")
    p.add_argument("--gain", type=float, default=None)
    p.add_argument("--n", type=int, default=40, help="frames per pattern")
    p.add_argument("--monitor", default=None)
    args = p.parse_args()

    mon = find_projector(args.monitor)
    out = new_run_dir("probe")
    cam = open_cam(args.exposure, args.gain)
    thumbs = []
    try:
        with PatternWindow(mon) as win:
            for name in args.patterns:
                win.show(win.pattern(name))
                stack, means = lit_stack(cam, args.n)
                med = np.median(stack, axis=0)
                std = stack.std(axis=0)
                cv2.imwrite(os.path.join(out, f"{name}.png"), to_u8(med))
                cv2.imwrite(os.path.join(out, f"{name}_std.png"), to_u8(std * 4))
                print(f"{name:12s} lit {len(stack):2d}/{len(means)}  "
                      f"image mean {med.mean():6.1f}  max {med.max():5.0f}"
                      f"{'  SATURATED' if med.max() >= 254 else ''}  "
                      f"temporal std {std.mean():5.2f}")
                t = cv2.resize(to_u8(med, normalise=True), (968, 608),
                               interpolation=cv2.INTER_AREA)
                cv2.putText(t, name, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 128, 2)
                thumbs.append(t)
    finally:
        cam.Close()

    if len(thumbs) % 2:
        thumbs.append(np.zeros_like(thumbs[0]))
    sheet = np.vstack([np.hstack(thumbs[i:i + 2]) for i in range(0, len(thumbs), 2)])
    cv2.imwrite(os.path.join(out, "sheet.png"), sheet)
    print(f"saved to {out}")


if __name__ == "__main__":
    main()
