"""Step 2: live view with black and blown-out frames dropped.

Every grabbed frame is scored by mean intensity and kept only if it lands inside
the band [--threshold-low, --threshold-high]: too dark is a black flicker frame,
too bright is a blown-out one. Dropped frames leave the previous good frame on
screen, so the display is steady instead of strobing. Without thresholds the
first --calib frames are used to find the band (same split as capture_frames.py).

    python camera/deflicker_live.py --exposure 5000
    python camera/deflicker_live.py --threshold-low 150 --threshold-high 240 --scale 0.5
    python camera/deflicker_live.py --width 640 --height 480      # ~99 fps instead of 41

Keys:  q/Esc quit   r recalibrate   s save a snapshot
       [ / ]  lower / raise the low cutoff
       { / }  lower / raise the high cutoff  (shift + [ / ])
       space  toggle deflicker off/on, to see the raw flicker
"""

import argparse
import sys
import time
from collections import deque

import cv2
import numpy as np
from pypylon import pylon

from pylon_utils import (add_camera_args, add_threshold_args, camera_kwargs,
                         frame_score, full_scale, grab_frames, open_camera,
                         suggest_band)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calib", type=int, default=60,
                   help="frames used for the auto band when none is given")
    p.add_argument("--margin", type=float, default=0.0,
                   help="widen the auto cutoffs inwards by this much "
                        "(raise to drop more marginal frames)")
    p.add_argument("--scale", type=float, default=0.5,
                   help="initial window size as a fraction of the frame")
    p.add_argument("--record", default=None,
                   help="write the deflickered stream to this .avi file")
    p.add_argument("--record-fps", type=float, default=None,
                   help="frame rate stamped into the recording (default: measured)")
    add_threshold_args(p)
    add_camera_args(p)
    return p.parse_args()


FONT = cv2.FONT_HERSHEY_SIMPLEX


def fit_letterbox(frame, win_w, win_h):
    """Scale `frame` to fit win_w x win_h without distortion, padding with black.

    cv2.WINDOW_KEEPRATIO is 0 — a no-op — and the Windows HighGUI backend
    stretches whatever it is handed across the full client area. Handing it a
    canvas that already matches the window means the blit is 1:1 and the aspect
    ratio survives being dragged into any shape.
    """
    h, w = frame.shape[:2]
    scale = min(win_w / w, win_h / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (new_w, new_h), interpolation=interp)
    if new_w == win_w and new_h == win_h:
        return resized

    shape = (win_h, win_w) if frame.ndim == 2 else (win_h, win_w, frame.shape[2])
    canvas = np.zeros(shape, dtype=frame.dtype)
    x0, y0 = (win_w - new_w) // 2, (win_h - new_h) // 2
    canvas[y0:y0 + new_h, x0:x0 + new_w] = resized
    return canvas


def overlay(image, lines):
    """Draw a status panel: single-weight text on a translucent dark strip.

    Called on the already-scaled display image, so the glyphs are rasterised at
    the size they are shown at. Font size tracks the image width so the panel
    stays legible at any --scale.
    """
    canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image.copy()
    if not lines:
        return canvas

    h, w = canvas.shape[:2]
    scale = min(0.9, max(0.4, w / 1400.0))
    thickness = 2 if scale >= 0.7 else 1
    pad = max(6, int(8 * scale / 0.6))

    (_, text_h), baseline = cv2.getTextSize("Ag", FONT, scale, thickness)
    line_h = text_h + baseline + pad // 2
    panel_h = min(h, pad * 2 + line_h * len(lines))
    text_w = max(cv2.getTextSize(t, FONT, scale, thickness)[0][0] for t in lines)
    panel_w = min(w, text_w + pad * 2)

    roi = canvas[:panel_h, :panel_w]
    canvas[:panel_h, :panel_w] = cv2.addWeighted(roi, 0.35,
                                                 np.zeros_like(roi), 0.65, 0)
    for i, text in enumerate(lines):
        y = pad + line_h * (i + 1) - baseline
        cv2.putText(canvas, text, (pad, y), FONT, scale, (0, 255, 0),
                    thickness, cv2.LINE_AA)
    return canvas


def main():
    args = parse_args()
    cam = open_camera(**camera_kwargs(args))

    window = "deflickered"
    # Resizable; aspect ratio is preserved by fit_letterbox, not by a flag.
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    window_sized = False
    print("keys: q quit | r recalibrate | [ ] low cutoff | { } high cutoff | "
          "space raw | s snapshot")

    low = args.threshold_low
    high = float("inf") if args.no_high else args.threshold_high
    # Only calibrate what the user did not pin. --no-high counts as pinned.
    calibrating = low is None or high is None
    calib_means, calib_sats = [], []
    if calibrating:
        print(f"Calibrating on the first {args.calib} frames...")

    last_good = None
    deflicker_on = True
    unreliable = False               # auto split found only one population
    total = kept = 0
    n_black = n_blown = 0
    recent = deque(maxlen=120)       # (is_kept,) over the last ~3 s
    kept_times = deque(maxlen=60)
    writer = None
    snapshot_n = 0
    t_start = time.perf_counter()

    try:
        # LatestImageOnly: if the display falls behind, skip ahead rather than
        # showing a growing lag.
        for _, frame, _ in grab_frames(cam, pylon.GrabStrategy_LatestImageOnly):
            mean, _, _, sat = frame_score(frame)
            total += 1

            if calibrating:
                calib_means.append(mean)
                calib_sats.append(sat)
                if len(calib_means) >= args.calib:
                    band = suggest_band(calib_means, calib_sats,
                                        scale=full_scale(frame))
                    # Whatever the user pinned on the command line wins.
                    if args.threshold_low is None:
                        low = band.low + args.margin
                    if high is None:
                        high = (band.high - args.margin if band.blown_found
                                else float("inf"))
                    calibrating = False
                    unreliable = not band.bimodal
                    print(f"  dark {band.dark:.2f} / lit {band.lit:.2f}"
                          + (f" / blown {band.blown:.2f}" if band.blown_found else "")
                          + f"  ->  keep {low:.2f} .. "
                          + ("inf" if high == float("inf") else f"{high:.2f}"))
                    if not band.blown_found and high == float("inf"):
                        print("  no saturated cluster in the calibration frames — "
                              "nothing is dropped as blown out. Use "
                              "--threshold-high to set one by hand.")
                    if not band.bimodal:
                        print("  WARNING: brightness is not clearly bimodal; set "
                              "--threshold-low manually if the result is wrong.")
                # Show raw frames while calibrating so there is something on screen.
                display, is_black, is_blown = frame, False, False
            else:
                is_black = mean < low
                is_blown = mean > high
                n_black += is_black
                n_blown += is_blown
                if (is_black or is_blown) and deflicker_on:
                    display = last_good
                else:
                    last_good = frame
                    display = frame

            dropped = (is_black or is_blown) and deflicker_on
            if not calibrating and not dropped:
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
            recent.append(0 if (dropped and not calibrating) else 1)

            if display is None:      # still waiting for the first lit frame
                continue

            if not window_sized:
                # --scale only picks the starting size; the window is resizable
                # afterwards and fit_letterbox holds the aspect ratio.
                fh, fw = display.shape[:2]
                cv2.resizeWindow(window, round(fw * args.scale),
                                 round(fh * args.scale))
                window_sized = True

            rect = cv2.getWindowImageRect(window)   # (x, y, w, h)
            if rect[2] > 0 and rect[3] > 0:
                shown = fit_letterbox(display, rect[2], rect[3])
            else:                                   # window closed or unmapped
                shown = display

            in_rate = total / max(time.perf_counter() - t_start, 1e-6)
            out_rate = 0.0
            if len(kept_times) > 1:
                span = kept_times[-1] - kept_times[0]
                out_rate = (len(kept_times) - 1) / span if span > 0 else 0.0
            drop_pct = 100.0 * (1 - np.mean(recent)) if recent else 0.0
            hi_text = "inf" if high in (None, float("inf")) else f"{high:.1f}"
            state = ("   [CALIBRATING]" if calibrating else
                     "   DROPPED: BLACK" if is_black and deflicker_on else
                     "   DROPPED: BLOWN" if is_blown and deflicker_on else "")
            status = [
                f"mean {mean:7.2f}   keep {low if low else 0:.1f} .. {hi_text}"
                + state,
                f"kept {kept}/{total}   dropping {drop_pct:4.1f}% (recent)   "
                f"black {n_black}  blown {n_blown}",
                f"in {in_rate:5.1f} fps -> out {out_rate:5.1f} fps"
                + ("   DEFLICKER OFF" if not deflicker_on else ""),
            ]
            if unreliable and args.threshold_low is None:
                # One brightness population: the auto low cutoff has split good
                # frames in half rather than found a flicker.
                status.append("auto cutoff unreliable - set --threshold-low")
            cv2.imshow(window, overlay(shown, status))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("r"):
                calibrating = True
                calib_means, calib_sats = [], []
                if args.threshold_low is None:
                    low = None
                if args.threshold_high is None and not args.no_high:
                    high = None
                print("recalibrating...")
            elif key == ord("["):
                low = max(0.0, (low or 0) - 1.0)
            elif key == ord("]"):
                low = (low or 0) + 1.0
            elif key == ord("{"):
                # Coming down from inf, start at full scale rather than jumping
                # to 254 and dropping half the stream.
                base = full_scale(frame) if high == float("inf") else high
                high = max((low or 0) + 1.0, base - 1.0)
            elif key == ord("}"):
                high = full_scale(frame) if high == float("inf") else high + 1.0
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
              f"({100.0 * (total - kept) / total:.1f}% dropped: "
              f"{n_black} black, {n_blown} blown out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
