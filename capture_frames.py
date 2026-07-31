"""Step 1: grab a burst of frames, save them, and report what fraction are black.

Frames are buffered in RAM while grabbing (PNG encoding is far slower than the
41 fps the camera runs at, so writing inline would drop frames), then written to
disk afterwards along with a per-frame CSV of brightness stats.

    python capture_frames.py --num 120 --exposure 5000 --outdir frames

Read the printed histogram and "suggested threshold" — that value feeds
deflicker_live.py --threshold.
"""

import argparse
import csv
import os
import sys
import time

import cv2
import numpy as np

from pylon_utils import (frame_score, grab_frames, histogram, open_camera,
                         suggest_threshold)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--num", type=int, default=120, help="frames to grab (default 120)")
    p.add_argument("--outdir", default="frames", help="output directory")
    p.add_argument("--format", default="png", choices=["png", "jpg", "tif"],
                   help="image file format (default png)")
    p.add_argument("--save", default="all", choices=["all", "black", "none"],
                   help="which frames to write to disk (default all)")
    p.add_argument("--exposure", type=float, default=None,
                   help="exposure time in microseconds (also disables auto exposure)")
    p.add_argument("--gain", type=float, default=None, help="gain in dB")
    p.add_argument("--fps", type=float, default=None, help="cap the frame rate")
    p.add_argument("--serial", default=None, help="camera serial number")
    p.add_argument("--threshold", type=float, default=None,
                   help="mean-intensity cutoff for 'black'; default is auto")
    return p.parse_args()


def main():
    args = parse_args()

    cam = open_camera(serial=args.serial, exposure_us=args.exposure,
                      gain=args.gain, fps=args.fps)
    frames, stats = [], []
    t0 = time.perf_counter()
    try:
        for index, frame, ts in grab_frames(cam):
            mean, p99, mx = frame_score(frame)
            stats.append({"frame": index, "timestamp_ns": ts, "mean": mean,
                          "p99": p99, "max": mx})
            frames.append(frame)
            if index + 1 >= args.num:
                break
    except KeyboardInterrupt:
        print("\ninterrupted — analysing what was captured")
    finally:
        cam.Close()

    elapsed = time.perf_counter() - t0
    if not frames:
        print("No frames captured.", file=sys.stderr)
        return 1

    means = np.array([s["mean"] for s in stats])
    auto_thr, dark_c, bright_c, bimodal = suggest_threshold(means)
    threshold = args.threshold if args.threshold is not None else auto_thr
    black = means < threshold

    for s, is_black in zip(stats, black):
        s["is_black"] = int(is_black)

    h, w = frames[0].shape[:2]
    print(f"\nCaptured {len(frames)} frames of {w}x{h} in {elapsed:.2f} s "
          f"({len(frames) / elapsed:.1f} fps)")
    print("\nMean-intensity distribution:")
    print(histogram(means))
    print(f"\n  dark cluster centre   {dark_c:.2f}")
    print(f"  bright cluster centre {bright_c:.2f}")
    print(f"  suggested threshold   {auto_thr:.2f}")
    if not bimodal:
        print("  WARNING: the frames do not look bimodal — the flicker may not "
              "be full on/off, or the exposure is long enough to average over "
              "it. Check the histogram before trusting the threshold.")
    if args.threshold is not None:
        print(f"  using --threshold     {threshold:.2f}")
    print(f"\n  BLACK FRAMES: {black.sum()} / {len(frames)} "
          f"= {100.0 * black.mean():.1f}%")

    # Longest run of consecutive black frames: tells you the worst-case gap the
    # live view will have to bridge.
    longest, run = 0, 0
    for b in black:
        run = run + 1 if b else 0
        longest = max(longest, run)
    print(f"  longest black run:  {longest} frames")

    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "frame_stats.csv")
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["frame", "timestamp_ns", "mean",
                                                "p99", "max", "is_black"])
        writer.writeheader()
        writer.writerows(stats)
    print(f"\nStats written to {csv_path}")

    if args.save != "none":
        written = 0
        for s, frame in zip(stats, frames):
            if args.save == "black" and not s["is_black"]:
                continue
            tag = "black" if s["is_black"] else "lit"
            name = f"frame_{s['frame']:05d}_{tag}_mean{s['mean']:06.2f}.{args.format}"
            cv2.imwrite(os.path.join(args.outdir, name), frame)
            written += 1
        print(f"Wrote {written} images to {args.outdir}\\ "
              f"(filenames carry the black/lit label and mean intensity)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
