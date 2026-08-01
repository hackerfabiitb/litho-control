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

from pylon_utils import (add_camera_args, add_threshold_args, camera_kwargs,
                         frame_score, full_scale, grab_frames, histogram,
                         open_camera, suggest_band)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--num", type=int, default=120, help="frames to grab (default 120)")
    p.add_argument("--outdir", default="frames", help="output directory")
    p.add_argument("--format", default="png", choices=["png", "jpg", "tif"],
                   help="image file format (default png)")
    p.add_argument("--save", default="all",
                   choices=["all", "black", "blown", "dropped", "none"],
                   help="which frames to write to disk (default all)")
    add_threshold_args(p)
    add_camera_args(p)
    return p.parse_args()


def main():
    args = parse_args()

    cam = open_camera(**camera_kwargs(args))
    frames, stats = [], []
    t0 = time.perf_counter()
    try:
        for index, frame, ts in grab_frames(cam):
            mean, p99, mx, sat = frame_score(frame)
            stats.append({"frame": index, "timestamp_ns": ts, "mean": mean,
                          "p99": p99, "max": mx, "sat_frac": round(sat, 5)})
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
    sats = np.array([s["sat_frac"] for s in stats])
    band = suggest_band(means, sats, scale=full_scale(frames[0]))

    low = args.threshold_low if args.threshold_low is not None else band.low
    if args.no_high:
        high = float("inf")
    elif args.threshold_high is not None:
        high = args.threshold_high
    else:
        high = band.high

    black = means < low
    blown = means > high

    for s, is_black, is_blown in zip(stats, black, blown):
        s["is_black"] = int(is_black)
        s["is_blown"] = int(is_blown)

    h, w = frames[0].shape[:2]
    print(f"\nCaptured {len(frames)} frames of {w}x{h} in {elapsed:.2f} s "
          f"({len(frames) / elapsed:.1f} fps)")
    print("\nMean-intensity distribution:")
    print(histogram(means))
    print(f"\n  dark cluster centre   {band.dark:.2f}")
    print(f"  lit cluster centre    {band.lit:.2f}")
    if band.blown_found:
        print(f"  blown cluster centre  {band.blown:.2f}")
    print(f"  suggested low  cutoff {band.low:.2f}")
    print(f"  suggested high cutoff "
          + (f"{band.high:.2f}" if band.blown_found
             else "none (no saturated cluster found)"))
    if not band.bimodal:
        print("  WARNING: the frames do not look bimodal — the flicker may not "
              "be full on/off, or the exposure is long enough to average over "
              "it. Check the histogram before trusting the threshold.")
    if args.threshold_low is not None:
        print(f"  using --threshold-low  {low:.2f}")
    if args.threshold_high is not None:
        print(f"  using --threshold-high {high:.2f}")

    kept = ~(black | blown)
    print(f"\n  BLACK FRAMES: {black.sum()} / {len(frames)} "
          f"= {100.0 * black.mean():.1f}%")
    print(f"  BLOWN FRAMES: {blown.sum()} / {len(frames)} "
          f"= {100.0 * blown.mean():.1f}%")
    print(f"  USABLE:       {kept.sum()} / {len(frames)} "
          f"= {100.0 * kept.mean():.1f}%")

    # Longest run of consecutive dropped frames: the worst-case gap the live
    # view will have to bridge by holding the previous good frame.
    longest, run = 0, 0
    for bad in (black | blown):
        run = run + 1 if bad else 0
        longest = max(longest, run)
    print(f"  longest dropped run: {longest} frames")

    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "frame_stats.csv")
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["frame", "timestamp_ns", "mean",
                                                "p99", "max", "sat_frac",
                                                "is_black", "is_blown"])
        writer.writeheader()
        writer.writerows(stats)
    print(f"\nStats written to {csv_path}")

    if args.save != "none":
        written = 0
        for s, frame in zip(stats, frames):
            tag = ("black" if s["is_black"] else
                   "blown" if s["is_blown"] else "lit")
            if args.save == "black" and tag != "black":
                continue
            if args.save == "blown" and tag != "blown":
                continue
            if args.save == "dropped" and tag == "lit":
                continue
            name = f"frame_{s['frame']:05d}_{tag}_mean{s['mean']:06.2f}.{args.format}"
            cv2.imwrite(os.path.join(args.outdir, name), frame)
            written += 1
        print(f"Wrote {written} images to {args.outdir}\\ "
              f"(filenames carry the black/blown/lit label and mean intensity)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
