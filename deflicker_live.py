"""Step 2: live view with black (flicker) frames dropped.

Every grabbed frame is scored by mean intensity; frames below the threshold are
discarded and the previous good frame stays on screen, so the display is steady
instead of strobing. If no threshold is given the first --calib frames are used
to find one automatically (same 2-means split as capture_frames.py).

    python deflicker_live.py --exposure 5000
    python deflicker_live.py --threshold 12.5 --scale 0.5 --record out.avi

Keys:  q/Esc quit   r recalibrate   [ / ] lower/raise threshold
       s save a snapshot   space toggle deflicker on/off (to see the raw flicker)
"""

import argparse
import sys
import time
from collections import deque

import cv2
import numpy as np
from pypylon import pylon

from pylon_utils import frame_score, grab_frames, open_camera, suggest_threshold


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--threshold", type=float, default=None,
                   help="mean-intensity cutoff; below this a frame is dropped")
    p.add_argument("--calib", type=int, default=60,
                   help="frames used for auto threshold when none is given")
    p.add_argument("--margin", type=float, default=0.0,
                   help="add this to the auto threshold (raise to drop more)")
    p.add_argument("--scale", type=float, default=0.5, help="display scale factor")
    p.add_argument("--exposure", type=float, default=None, help="exposure in us")
    p.add_argument("--gain", type=float, default=None, help="gain in dB")
    p.add_argument("--fps", type=float, default=None, help="cap the frame rate")
    p.add_argument("--serial", default=None, help="camera serial number")
    p.add_argument("--record", default=None,
                   help="write the deflickered stream to this .avi file")
    p.add_argument("--record-fps", type=float, default=None,
                   help="frame rate stamped into the recording (default: measured)")
    return p.parse_args()


def overlay(image, lines):
    """Draw status text with a dark outline so it reads on any background."""
    canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image.copy()
    for i, text in enumerate(lines):
        origin = (10, 25 + 22 * i)
        cv2.putText(canvas, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 0), 1, cv2.LINE_AA)
    return canvas


def main():
    args = parse_args()
    cam = open_camera(serial=args.serial, exposure_us=args.exposure,
                      gain=args.gain, fps=args.fps)

    window = "deflickered (q quit, r recalibrate, [ ] threshold, space raw)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    threshold = args.threshold
    calibrating = threshold is None
    calib_means = []
    if calibrating:
        print(f"Calibrating on the first {args.calib} frames...")

    last_good = None
    deflicker_on = True
    total = kept = 0
    recent = deque(maxlen=120)       # (is_kept,) over the last ~3 s
    kept_times = deque(maxlen=60)
    writer = None
    snapshot_n = 0
    t_start = time.perf_counter()

    try:
        # LatestImageOnly: if the display falls behind, skip ahead rather than
        # showing a growing lag.
        for _, frame, _ in grab_frames(cam, pylon.GrabStrategy_LatestImageOnly):
            mean, _, _ = frame_score(frame)
            total += 1

            if calibrating:
                calib_means.append(mean)
                if len(calib_means) >= args.calib:
                    auto, dark_c, bright_c, bimodal = suggest_threshold(calib_means)
                    threshold = auto + args.margin
                    calibrating = False
                    print(f"  dark {dark_c:.2f} / bright {bright_c:.2f} "
                          f"-> threshold {threshold:.2f}")
                    if not bimodal:
                        print("  WARNING: brightness is not clearly bimodal; "
                              "set --threshold manually if the result is wrong.")
                # Show raw frames while calibrating so there is something on screen.
                display, is_black = frame, False
            else:
                is_black = mean < threshold
                if is_black and deflicker_on:
                    display = last_good
                else:
                    last_good = frame
                    display = frame

            if not calibrating and not (is_black and deflicker_on):
                kept += 1
                kept_times.append(time.perf_counter())
                if writer is None and args.record:
                    h, w = frame.shape[:2]
                    fps = args.record_fps or 20.0
                    writer = cv2.VideoWriter(args.record,
                                             cv2.VideoWriter_fourcc(*"MJPG"),
                                             fps, (w, h), isColor=False)
                    if not writer.isOpened():
                        print(f"! could not open {args.record} for writing",
                              file=sys.stderr)
                        writer = None
                if writer is not None:
                    writer.write(frame)
            recent.append(0 if (is_black and deflicker_on and not calibrating) else 1)

            if display is None:      # still waiting for the first lit frame
                continue

            if args.scale != 1.0:
                shown = cv2.resize(display, None, fx=args.scale, fy=args.scale,
                                   interpolation=cv2.INTER_AREA)
            else:
                shown = display

            in_rate = total / max(time.perf_counter() - t_start, 1e-6)
            out_rate = 0.0
            if len(kept_times) > 1:
                span = kept_times[-1] - kept_times[0]
                out_rate = (len(kept_times) - 1) / span if span > 0 else 0.0
            drop_pct = 100.0 * (1 - np.mean(recent)) if recent else 0.0
            status = [
                f"mean {mean:7.2f}   threshold {threshold if threshold else 0:.2f}"
                + ("   [CALIBRATING]" if calibrating else
                   ("   DROPPED" if is_black and deflicker_on else "")),
                f"kept {kept}/{total}   dropping {drop_pct:4.1f}% (recent)",
                f"in {in_rate:5.1f} fps -> out {out_rate:5.1f} fps"
                + ("   DEFLICKER OFF" if not deflicker_on else ""),
            ]
            cv2.imshow(window, overlay(shown, status))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("r"):
                calibrating, calib_means = True, []
                print("recalibrating...")
            elif key == ord("["):
                threshold = max(0.0, (threshold or 0) - 1.0)
            elif key == ord("]"):
                threshold = (threshold or 0) + 1.0
            elif key == ord(" "):
                deflicker_on = not deflicker_on
            elif key == ord("s"):
                name = f"snapshot_{snapshot_n:03d}.png"
                cv2.imwrite(name, display)
                print(f"saved {name}")
                snapshot_n += 1
    except KeyboardInterrupt:
        pass
    finally:
        cam.Close()
        if writer is not None:
            writer.release()
            print(f"recording written to {args.record}")
        cv2.destroyAllWindows()

    if total:
        print(f"\n{kept}/{total} frames kept "
              f"({100.0 * (total - kept) / total:.1f}% dropped as black)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
