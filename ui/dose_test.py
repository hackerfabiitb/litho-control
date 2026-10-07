"""Dose test: a row of exposure fields along X, each exposed for a different time.

    .\\.venv\\Scripts\\python.exe ui\\dose_test.py --dry-run     # the plan, nothing moves
    .\\.venv\\Scripts\\python.exe ui\\dose_test.py               # 10 fields, 10..100 s
    .\\.venv\\Scripts\\python.exe ui\\dose_test.py --times 10 20 30 --gap-um 200

Needs the UI running (ui\\start.ps1) and a Fit transform. It runs as a script
(ui/script_client.py): the UI locks, shows the progress in its Script pane, and
Stop ends it (projector black at once). For each field, left to right (X
increasing, as seen in the camera and projector previews):

  1. black; move X to the field (Y is never moved: it can't be trusted, see
     xyz_stage/README.md); every field is approached from -X, so backlash is
     the same for all
  2. project focus_markers (blue) and autofocus: a Z sweep (±10 steps in 1,
     approached from below), scored by the sharpness around the four markers
  3. project res_test(t) (blue, "<t>s" written in it) for t seconds
  4. black

The patterns are patterns/scripts/dose_test_patterns.py. Both are blue: the
resist is exposed by blue, and red and blue focus at different heights. The
markers get dose during autofocus too.

Fields: field width (1070 um) + gap apart, in steps at the stage's nominal
400 steps/mm (xyz_stage/homing.json; the scan measured X at ~2.43 um/step, so
the real gap comes out ~35 um smaller at a 200 um gap). By default the row is
centred in the X soft limits. The projector is left black at the end (not
put back: it could be showing something that exposes), the camera's exposure
and view are put back. A log and camera frames go to dose_tests/<date>/.
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
sys.path[:0] = [HERE, os.path.join(ROOT, "patterns"), os.path.join(ROOT, "patterns", "scripts"),
                os.path.join(ROOT, "projector")]
import dose_test_patterns as dp  # noqa: E402
from lib import Pattern, render_bgr  # noqa: E402
from optics import FIELD_WIDTH_UM, INPUT_PX  # noqa: E402
from script_client import BASE, Session, Stopped, _request  # noqa: E402

PRELOAD_STEPS = 40          # approach every position from below by this much (backlash)
ROI_PAD = 40                # camera px around each focus marker for the sharpness


def steps_per_mm_x():
    try:
        with open(os.path.join(ROOT, "xyz_stage", "homing.json"), encoding="utf-8") as fh:
            return float(json.load(fh)["axes"]["X"]["steps_per_mm"])
    except (OSError, ValueError, KeyError):
        return 400.0


def limits(state):
    for line in state["lines"]:
        if line.startswith("LIMITS "):
            v = [int(x) for x in line.split()[1:7]]
            return {"X": (v[0], v[1]), "Y": (v[2], v[3]), "Z": (v[4], v[5])}
    return None


def plan(args, state):
    """X positions of the fields; raises SystemExit if they don't fit."""
    lim = limits(state)
    if lim is None or lim["X"][0] == lim["X"][1]:
        sys.exit("X has no soft limits; calibrate them first (STAGE panel)")
    lo, hi = lim["X"]
    spm = steps_per_mm_x()
    pitch = round((FIELD_WIDTH_UM + args.gap_um) / 1000.0 * spm)
    n = len(args.times)
    span = (n - 1) * pitch
    fit = (hi - lo) // pitch + 1
    if span > hi - lo:
        sys.exit(f"{n} fields at {pitch} steps ({FIELD_WIDTH_UM:g} um field + {args.gap_um:g} um gap) "
                 f"need {span} steps; the X range {lo}..{hi} holds {fit}")
    start = args.start_x if args.start_x is not None else lo + (hi - lo - span) // 2
    xs = [start + i * pitch for i in range(n)]
    if xs[0] < lo or xs[-1] > hi:
        sys.exit(f"fields X {xs[0]}..{xs[-1]} leave the X range {lo}..{hi}")
    return xs, pitch, lim, fit


def marker_rois(view_size):
    """(x0, y0, x1, y1) in the fitted camera view around each focus marker."""
    W, H = view_size
    sx, sy = W / INPUT_PX[0], H / INPUT_PX[1]
    rois = []
    for fid, fx, fy in dp.fiducials(Pattern("rois")):
        _, _, fw, fh = fid.bbox()
        rois.append((max(0, int(fx * sx) - ROI_PAD), max(0, int(fy * sy) - ROI_PAD),
                     min(W, int((fx + fw) * sx) + ROI_PAD), min(H, int((fy + fh) * sy) + ROI_PAD)))
    return rois


def sharpness(img, rois):
    """Mean Laplacian variance over the marker regions."""
    f = img.astype(np.float32)
    return float(np.mean([cv2.Laplacian(f[y0:y1, x0:x1], cv2.CV_32F).var() for x0, y0, x1, y1 in rois]))


def marker_exposure(s, rois, target=170.0, lo=34.0, hi=500000.0):
    """Set the camera exposure so the brightest marker pixels sit near
    `target`: the markers are too small a part of the frame for the server's
    auto exposure, and with only the blue LED they are dim (peak ~40 at 40 ms
    on 2026-10-07)."""
    e = float(s.camera_stats()["exposure_us"])
    for _ in range(4):
        img = settled_frames(s, 0.2 + 2 * e / 1e6, 2)[-1].astype(np.float32)
        peak = max(float(np.percentile(img[y0:y1, x0:x1], 99.5)) for x0, y0, x1, y1 in rois)
        if 0.75 * target <= peak <= 1.2 * target or (peak < target and e >= hi):
            break
        e = min(hi, max(lo, e * (0.4 if peak >= 250 else target / max(peak, 1.0))))
        s.camera(exposure_us=round(e))
    return round(e), peak


def settled_frames(s, settle, n=2):
    time.sleep(settle)
    seq, _ = s.frame()
    out = []
    for _ in range(n):
        seq, img = s.frame(after=seq, timeout=10)
        out.append(img)
    return out


def autofocus(s, args, z0, zlim, rois):
    lo, hi = zlim if zlim[0] != zlim[1] else (-10**9, 10**9)
    zs = [z for z in range(z0 - args.af_range, z0 + args.af_range + 1, args.af_step) if lo <= z <= hi]
    s.move(z=max(lo, zs[0] - PRELOAD_STEPS))
    curve, best = [], None
    for k, z in enumerate(zs):
        s.move(z=z)
        score = float(np.mean([sharpness(f, rois) for f in settled_frames(s, args.af_settle)]))
        curve.append([z, round(score, 2)])
        if best is None or score > best[1]:
            best = (z, score)
        s.say(f"autofocus {k + 1}/{len(zs)}: Z {z}, sharpness {score:.1f} (best {best[1]:.1f} at Z {best[0]})")
    s.move(z=max(lo, best[0] - PRELOAD_STEPS))
    s.move(z=best[0])
    return best, curve


def expose(s, seconds):
    """Wait `seconds` with the pattern up, checking Stop; returns the time waited."""
    t0 = time.monotonic()
    while True:
        left = seconds - (time.monotonic() - t0)
        if left <= 0:
            return time.monotonic() - t0
        s.check()
        time.sleep(min(0.2, left))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--times", type=float, nargs="+", default=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
                   help="exposure time per field, s, left to right (default 10..100: 10 fields fit "
                        "the X range at a 200 um gap)")
    p.add_argument("--gap-um", type=float, default=200.0, help="gap between fields, um (default 200)")
    p.add_argument("--start-x", type=int, help="X of the first field (default: the row centred in the X limits)")
    p.add_argument("--af-range", type=int, default=10, help="autofocus Z range, +- steps (default 10)")
    p.add_argument("--af-step", type=int, default=1, help="autofocus Z step (default 1)")
    p.add_argument("--af-settle", type=float, default=0.3, help="s after each Z move (default 0.3)")
    p.add_argument("--step-delay", type=int, default=800, help="us per step for the moves (default 800)")
    p.add_argument("--dry-run", action="store_true", help="print the plan; move nothing")
    args = p.parse_args()

    state = _request("GET", BASE + "/stage/state")
    if not state.get("connected") or state.get("pos") is None:
        sys.exit("the stage isn't connected to the UI server")
    xs, pitch, lim, fit = plan(args, state)
    x_now, y_now, z_now = state["pos"]
    gap_nominal = pitch * 1000.0 / steps_per_mm_x() - FIELD_WIDTH_UM
    af_s = (2 * args.af_range // args.af_step + 1) * (args.af_settle + 0.4)
    print(f"{len(xs)} fields along X at Y {y_now}: X {xs[0]}..{xs[-1]}, every {pitch} steps "
          f"({FIELD_WIDTH_UM:g} um field + {gap_nominal:.0f} um gap, nominal); the X range holds {fit}")
    for x, t in zip(xs, args.times):
        print(f"  X {x:5d}: {t:g} s")
    print(f"about {sum(args.times) / 60 + len(xs) * (af_s + 5) / 60:.0f} min "
          f"({sum(args.times):g} s exposure, ~{af_s:.0f} s autofocus per field)")
    if args.dry_run:
        return

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = os.path.join(ROOT, "dose_tests", stamp)
    os.makedirs(out, exist_ok=True)
    log = {"started": stamp, "times_s": args.times, "x_steps": xs, "pitch_steps": pitch,
           "gap_um_nominal": round(gap_nominal, 1), "y": y_now, "z_start": z_now,
           "params": vars(args), "fields": []}

    def save():
        with open(os.path.join(out, "log.json"), "w", encoding="utf-8") as fh:
            json.dump(log, fh, indent=2)

    markers = render_bgr(dp.focus_markers(), dp.COLOR)
    with Session(f"Dose test: {len(xs)} fields, {args.times[0]:g}..{args.times[-1]:g} s") as s:
        cam0 = s.camera_stats()
        if not cam0.get("transform"):
            sys.exit("no Fit transform: run it first (CAMERA panel)")
        try:
            s.black()
            s.camera(transform=True)                         # the fitted view: marker positions known
            view = tuple(cam0["transform"]["size"])
            rois = marker_rois(view)
            s.speed(args.step_delay)
            z = z_now
            s.say(f"moving to the first field, X {xs[0]} (from -X)")
            s.move(x=max(lim["X"][0], xs[0] - PRELOAD_STEPS))
            exposure_set = False
            for i, (x, t) in enumerate(zip(xs, args.times)):
                tag = f"field {i + 1}/{len(xs)} ({t:g} s)"
                s.black()
                s.say(f"{tag}: moving to X {x}")
                s.move(x=x)
                s.image(markers, name="focus_markers")
                if not exposure_set:                         # once: the markers look the same everywhere
                    s.say(f"{tag}: picking a camera exposure for the markers")
                    e, peak = marker_exposure(s, rois)
                    log["camera_exposure_us"] = e
                    s.say(f"{tag}: camera exposure {e} us (marker peak {peak:.0f})")
                    exposure_set = True
                (z, score), curve = autofocus(s, args, z, lim["Z"], rois)
                focus_frame = settled_frames(s, args.af_settle, 1)[0]
                cv2.imwrite(os.path.join(out, f"field_{i + 1:02d}_focus.png"), focus_frame)
                s.say(f"{tag}: exposing res_test for {t:g} s (focus Z {z})")
                s.image(render_bgr(dp.res_test(t), dp.COLOR), name=f"res_test {t:g}s")
                started = datetime.datetime.now().strftime("%H:%M:%S")
                rec = {"field": i + 1, "x": x, "t_s": t, "z": z, "sharpness": round(score, 2),
                       "af_curve": curve, "exposure_started": started}
                log["fields"].append(rec)
                try:
                    waited = expose(s, t)
                    rec["exposed_s"] = round(waited, 2)
                finally:
                    s.black()
                    if "exposed_s" not in rec:
                        rec["exposed_s"] = "stopped early"
                    save()
                print(f"[dose] {tag}: X {x}, Z {z}, exposed {rec['exposed_s']} s", flush=True)
            log["finished"] = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            s.say(f"done: {len(xs)} fields exposed; projector black. Log: dose_tests/{stamp}")
        except Stopped:
            log["stopped"] = True
            raise
        finally:
            try:
                _request("POST", BASE + "/projector/blank", {})       # also after Stop
                _request("POST", BASE + "/config", {"exposure_us": cam0["exposure_us"],
                                                    "transform": bool(cam0["transform"]["on"])})
            finally:
                save()
    print(f"log: {os.path.join(out, 'log.json')}")


if __name__ == "__main__":
    main()
