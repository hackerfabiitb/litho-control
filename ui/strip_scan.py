"""Strip scan: image a row along X only (Y and Z stay as they are) and stitch it.

    .\\.venv\\Scripts\\python.exe ui\\strip_scan.py                  # X 0..4800, a tile every 200 steps
    .\\.venv\\Scripts\\python.exe ui\\strip_scan.py --x0 1000 --x1 3000 --pitch 150

For looking at a row of fields, e.g. a developed dose test (ui/dose_test.py lays
its fields out along X, since Y moves can't be trusted). It runs as a script
through the UI (ui/script_client.py): the UI locks and shows its progress, and
Stop ends it. Whatever is projected (e.g. White) and the camera settings are
left as they are; the camera must be in the fitted view (Fit transform), which
the steps -> pixels calibration (camera/scan_calibration.json) is for.

Each tile is the median of --frames shown camera frames, taken after the move
has settled. The tiles are approached from -X, so backlash is the same for all.
They go to scans/<date>_strip/ as tile_NNN.png with a scan.json in ui/scan.py's
format, and are stitched with camera/stitch.py's global refinement into
mosaic_refined.png (+ preview.jpg).
"""

import argparse
import datetime
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(ROOT, "camera")]
import stitch  # noqa: E402
from script_client import BASE, Session, _request  # noqa: E402

PRELOAD_STEPS = 40
CAL_FILE = os.path.join(ROOT, "camera", "scan_calibration.json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--x0", type=int, help="first X (default: the X soft limit's low end)")
    p.add_argument("--x1", type=int, help="last X (default: the high end)")
    p.add_argument("--pitch", type=int, default=200,
                   help="X steps between tiles (default 200, ~45 %% of a field: plenty of overlap)")
    p.add_argument("--frames", type=int, default=5, help="frames per tile, median (default 5)")
    p.add_argument("--settle", type=float, default=0.3, help="s after each move (default 0.3)")
    p.add_argument("--step-delay", type=int, default=800, help="us per step (default 800)")
    args = p.parse_args()

    state = _request("GET", BASE + "/stage/state")
    if not state.get("connected") or state.get("pos") is None:
        sys.exit("the stage isn't connected to the UI server")
    x_now, y, z = state["pos"]
    lim = next(([int(v) for v in l.split()[1:3]] for l in state["lines"] if l.startswith("LIMITS ")), None)
    if lim is None or lim[0] == lim[1]:
        sys.exit("X has no soft limits; calibrate them first")
    x0 = max(lim[0], args.x0 if args.x0 is not None else lim[0])
    x1 = min(lim[1], args.x1 if args.x1 is not None else lim[1])
    xs = list(range(x0, x1 + 1, args.pitch))
    if xs[-1] != x1:
        xs.append(x1)
    with open(CAL_FILE, encoding="utf-8") as fh:
        cal = json.load(fh)["cam_px_per_step"]

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = os.path.join(ROOT, "scans", stamp + "_strip")
    os.makedirs(out, exist_ok=True)
    record = {"started": stamp, "kind": "strip along X", "start_steps": [x_now, y, z],
              "params": vars(args), "calibration": {"cam_px_per_step": cal}, "tiles": []}
    print(f"{len(xs)} tiles, X {xs[0]}..{xs[-1]} every {args.pitch} steps, at Y {y}, Z {z} -> {out}")

    with Session(f"Strip scan: X {xs[0]}..{xs[-1]}, {len(xs)} tiles") as s:
        cam = s.camera_stats()
        if not (cam.get("transform") and cam["transform"]["on"]):
            sys.exit("the camera isn't in the fitted view (Fit transform, View: fitted)")
        record["camera"] = {"exposure_us": cam["exposure_us"], "view": cam["transform"]["size"]}
        s.speed(args.step_delay)
        s.say(f"moving to X {xs[0]}")
        s.move(x=max(lim[0], xs[0] - PRELOAD_STEPS))
        for i, x in enumerate(xs):
            s.say(f"tile {i + 1}/{len(xs)}: X {x}")
            s.move(x=x)
            time.sleep(args.settle)
            seq, _ = s.frame()
            frames = []
            for _ in range(args.frames):
                seq, img = s.frame(after=seq, timeout=10)
                frames.append(img)
            tile = np.median(np.stack(frames), axis=0).astype(np.uint8)
            cv2.imwrite(os.path.join(out, f"tile_{i:03d}.png"), tile)
            record["tiles"].append({"index": i, "grid": [i, 0], "steps": [x, y, z]})
            with open(os.path.join(out, "scan.json"), "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2)
        s.say("stitching")
        path, info = stitch.restitch(out)
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        scale = min(1.0, 3000.0 / img.shape[1])
        cv2.imwrite(os.path.join(out, "preview.jpg"),
                    cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
        s.say(f"done: {info['pairs_used']}/{info['pairs_matched']} pair matches consistent, "
              f"{len(info['tiles_by_steps_only'])} tiles placed by steps only")
    print(f"mosaic: {path} ({img.shape[1]} x {img.shape[0]})")
    print({k: v for k, v in info.items() if k not in ("pairs", "offsets_px")})


if __name__ == "__main__":
    main()
