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
    cam = open_camera(serial=args.serial, exposure_us=args.exposure,
                      gain=args.gain, fps=args.fps)

    window = "deflickered"
    # Resizable; aspect ratio is preserved by fit_letterbox, not by a flag.
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    window_sized = False
    print("keys: q quit | r recalibrate | [ ] threshold | space raw | s snapshot")

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
