"""Measure why the camera sees the projector flicker.

A DLP projector does not emit steady light: within every input frame it
cycles the LEDs and flips mirrors in binary-weighted time slices. A camera
exposure much shorter than that cycle catches a different slice each frame.

  waveform  Shrink the AOI to a few rows so the camera runs at ~780 fps with a
            34 us exposure, record brightness against the camera's hardware
            timestamps, and find the period at which the samples fold into a
            clean repeating waveform. Saves the folded waveform, and from it
            predicts the frame-to-frame flicker for any exposure.
  sweep     Measure the flicker for real at a list of camera frame rates and
            exposures (full-frame, as the live viewer runs).

    python projector/flicker.py waveform
    python projector/flicker.py sweep --fps 41 30 23 --exposure 1000 4167 16667

Outputs go to projector/captures/<timestamp>_flicker-*/.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

from capture import new_run_dir
from display import PatternWindow, find_projector, refresh_rate

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "camera"))
from pylon_utils import _try_set, grab_frames, open_camera  # noqa: E402

NS = 1e-9


# --------------------------------------------------------------------------- #
# camera helpers
# --------------------------------------------------------------------------- #

def set_rate(cam, fps):
    """fps None or 0 means as fast as the AOI and exposure allow."""
    if not fps:
        _try_set(cam, ["AcquisitionFrameRateEnable"], False)
    else:
        _try_set(cam, ["AcquisitionFrameRateEnable"], True)
        _try_set(cam, ["AcquisitionFrameRate"], float(fps))
    return cam.ResultingFrameRate.GetValue()


def record(cam, n):
    """Per-frame mean intensity and hardware timestamp (s) for n frames."""
    means, stamps = np.empty(n), np.empty(n)
    for i, frame, ts in grab_frames(cam):
        means[i] = frame.mean()
        stamps[i] = ts * NS
        if i + 1 >= n:
            break
    return means, stamps - stamps[0]


# --------------------------------------------------------------------------- #
# period search by epoch folding
# --------------------------------------------------------------------------- #

def fold_score(t, y, period, bins=100):
    """Fraction of the variance explained by folding at `period` (0..1).
    Near 1 when every sample lands on a clean, repeatable waveform."""
    idx = ((t / period) % 1.0 * bins).astype(int)
    count = np.bincount(idx, minlength=bins)
    total = np.bincount(idx, weights=y, minlength=bins)
    ok = count > 0
    means = total[ok] / count[ok]
    return float((count[ok] * (means - y.mean()) ** 2).sum()
                 / max(((y - y.mean()) ** 2).sum(), 1e-12))


def refine(t, y, freq, rel=1e-3, n=2001, bins=100):
    """Best-folding frequency within +-rel of `freq`, and its score."""
    fs = np.linspace(freq * (1 - rel), freq * (1 + rel), n)
    scores = np.array([fold_score(t, y, 1 / f, bins) for f in fs])
    k = int(np.argmax(scores))
    return float(fs[k]), float(scores[k])


def find_period(t, y, refresh_hz, multiples=(1, 2, 3, 4, 6, 8)):
    """The illumination period, searched only at multiples of the display
    refresh rate.

    A free search does not work here: the camera samples at an exactly
    uniform rate, so some unrelated frequencies fold almost as well as the
    true one (320 Hz scored 90.8 % against 91.3 % for 240 Hz on 0.5 s of
    data). A DLP controller locks its sequence to the input frame, so the
    true rate is a multiple of the refresh rate anyway. A waveform with rate
    f also folds cleanly at f/2, f/4...; the highest multiple that scores
    within 1 % of the best is the true one.
    """
    found = []
    for m in multiples:
        f, s = refine(t, y, m * refresh_hz, bins=max(50, int(400 / m)))
        found.append((m, f, s))
    best = max(s for _, _, s in found)
    m, f, s = max((c for c in found if c[2] >= best - 0.01), key=lambda c: c[0])
    return 1.0 / f, s, found


def folded(t, y, period, bins=400):
    idx = ((t / period) % 1.0 * bins).astype(int)
    count = np.bincount(idx, minlength=bins)
    total = np.bincount(idx, weights=y, minlength=bins)
    prof = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    # fill any empty bins by interpolation so the profile can be integrated
    bad = np.isnan(prof)
    if bad.any():
        x = np.arange(bins)
        prof[bad] = np.interp(x[bad], x[~bad], prof[~bad], period=bins)
    return prof


def predict(profile, period, exposure_s):
    """Frame brightness for every exposure start phase: the waveform averaged
    over a window of `exposure_s`, wrapped around the period."""
    bins = len(profile)
    width = exposure_s / period * bins
    whole, frac = int(width // bins), (width % bins)
    ext = np.concatenate([profile, profile])
    csum = np.concatenate([[0.0], np.cumsum(ext)])
    k = int(frac)
    part = (csum[np.arange(bins) + k] - csum[np.arange(bins)]) \
        + (frac - k) * ext[np.arange(bins) + k]
    return (whole * profile.sum() + part) / max(width, 1e-9)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_waveform(args):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mon = find_projector(args.monitor)
    out = new_run_dir("flicker-waveform")
    cam = open_camera(exposure_us=args.exposure, gain=args.gain,
                      width=args.width, height=args.rows,
                      offset_x=args.offset_x, offset_y=args.offset_y,
                      binning=1, verbose=False)
    try:
        rate = set_rate(cam, None)
        print(f"AOI {cam.Width.GetValue()}x{cam.Height.GetValue()} at "
              f"({cam.OffsetX.GetValue()}, {cam.OffsetY.GetValue()}), "
              f"exposure {cam.ExposureTime.GetValue():.0f} us, {rate:.1f} fps")
        with PatternWindow(mon) as win:
            win.show(win.pattern(args.pattern))
            y, t = record(cam, args.n)
            win.show(win.pattern("black"))
            y_black, _ = record(cam, 400)
    finally:
        cam.Close()

    dt = np.diff(t)
    print(f"{len(y)} samples over {t[-1]:.2f} s, mean interval "
          f"{dt.mean() * 1e3:.3f} ms (jitter {dt.std() * 1e6:.1f} us)")
    print(f"brightness: mean {y.mean():.1f}, min {y.min():.1f}, max {y.max():.1f}"
          f"  (black pattern: {y_black.mean():.1f})")

    hz = refresh_rate(mon)
    period, score, found = find_period(t, y, hz)
    print(f"display refresh {hz} Hz; fold score at multiples of it "
          "(share of the brightness variance explained):")
    for m, f, s in found:
        print(f"  {m} x {hz} Hz -> best {f:9.4f} Hz: {100 * s:5.1f} %")
    print(f"illumination period {period * 1e3:.4f} ms = {1 / period:.4f} Hz")

    # Plot one whole input frame too: the sub-frames may differ for other
    # patterns even though they are identical for white.
    frame_period = 1.0 / refine(t, y, hz, bins=400)[0]
    frame_prof = np.clip(folded(t, y, frame_period, bins=800) - y_black.mean(), 0, None)

    prof = folded(t, y, period) - y_black.mean()
    prof = np.clip(prof, 0, None)
    duty = float((prof > 0.1 * prof.max()).mean())
    print(f"light is on (>10 % of peak) for {100 * duty:.0f} % of the period")

    np.savez(os.path.join(out, "waveform.npz"), t=t, y=y, y_black=y_black,
             period=period, profile=prof, exposure_us=args.exposure)

    exposures_us = np.array([34, 100, 250, 500, 1000, 2000, 4000,
                             period * 1e6 / 2, period * 1e6 * 0.75,
                             period * 1e6, period * 1e6 * 1.5,
                             period * 1e6 * 2, frame_period * 1e6])
    rows = []
    print("\npredicted flicker for a free-running camera (random phase):")
    print("  exposure_us   CV %   dark %  (dark = below half the median)")
    for e in sorted(exposures_us):
        b = predict(prof, period, e * 1e-6)
        cv = 100 * b.std() / max(b.mean(), 1e-9)
        dark = 100 * float((b < 0.5 * np.median(b)).mean())
        rows.append({"exposure_us": float(e), "cv_pct": cv, "dark_pct": dark})
        print(f"  {e:10.0f}  {cv:6.1f}  {dark:6.1f}")
    with open(os.path.join(out, "prediction.json"), "w") as fh:
        json.dump({"period_s": period, "fold_score": score, "rows": rows}, fh,
                  indent=1)

    fig, ax = plt.subplots(3, 1, figsize=(10, 10))
    ph = np.linspace(0, frame_period * 1e3, len(frame_prof), endpoint=False)
    ax[0].plot(ph, frame_prof, lw=1)
    for k in range(1, int(round(frame_period / period))):
        ax[0].axvline(k * period * 1e3, color="gray", ls=":", lw=1)
    ax[0].set_xlabel("time within one input frame (ms); dotted = illumination periods")
    ax[0].set_ylabel("brightness above black")
    ax[0].set_title(f"{args.pattern}: one {1 / frame_period:.4f} Hz input frame, "
                    f"{args.exposure:.0f} us samples")
    ph = np.linspace(0, period * 1e3, len(prof), endpoint=False)
    ax[1].plot(ph, prof, lw=1)
    ax[1].set_xlabel("time within one illumination period (ms)")
    ax[1].set_ylabel("brightness above black")
    ax[1].set_title(f"folded at {period * 1e3:.4f} ms ({1 / period:.3f} Hz)")
    e_axis = np.geomspace(34, 3 * period * 1e6, 300)
    cvs = []
    for e in e_axis:
        b = predict(prof, period, e * 1e-6)
        cvs.append(100 * b.std() / max(b.mean(), 1e-9))
    ax[2].semilogx(e_axis, cvs)
    for k in (1, 2, 3):
        ax[2].axvline(k * period * 1e6, color="gray", ls=":", lw=1)
    ax[2].set_xlabel("camera exposure (us); dotted = whole illumination periods")
    ax[2].set_ylabel("predicted frame-to-frame CV (%)")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "waveform.png"), dpi=110)
    print(f"\nsaved to {out}")


def dark_runs(dark):
    runs, run = [], 0
    for d in dark:
        if d:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


def cmd_sweep(args):
    mon = find_projector(args.monitor)
    out = new_run_dir("flicker-sweep")
    cam = open_camera(exposure_us=args.exposure[0], gain=args.gain, full=True,
                      verbose=False)
    results = []
    print(" fps_set  fps_real  exposure_us   mean    CV%   dark%  max_dark_run  "
          "max_gap_ms  beat_s")
    try:
        with PatternWindow(mon) as win:
            win.show(win.pattern(args.pattern))
            for fps in args.fps:
                for exp in args.exposure:
                    _try_set(cam, ["ExposureTime"], float(exp))
                    real = set_rate(cam, fps)
                    n = max(40, int(round(args.seconds * real)))
                    y, t = record(cam, n)
                    med = np.median(y)
                    dark = y < 0.5 * med
                    runs = dark_runs(dark)
                    # longest wait for a frame at least half the median, i.e.
                    # how long the live view would freeze
                    lit_t = t[~dark]
                    gap = float(np.diff(lit_t).max()) if len(lit_t) > 1 else t[-1]
                    # slow beat: dominant period of the mean sequence
                    spec = np.abs(np.fft.rfft(y - y.mean()))
                    freqs = np.fft.rfftfreq(len(y), d=np.diff(t).mean())
                    k = 1 + int(np.argmax(spec[1:]))
                    beat = 1 / freqs[k]
                    r = {"fps_set": fps, "fps_real": real, "exposure_us": exp,
                         "mean": float(y.mean()), "cv_pct": float(100 * y.std() / max(y.mean(), 1e-9)),
                         "dark_pct": float(100 * dark.mean()),
                         "max_dark_run": max(runs) if runs else 0,
                         "max_gap_ms": 1e3 * gap, "beat_s": float(beat),
                         "saturated_pct": float(100 * (y > 250).mean())}
                    results.append(r)
                    print(f" {fps if fps else 'max':>7}  {real:8.2f}  {exp:11.0f}  "
                          f"{r['mean']:5.1f}  {r['cv_pct']:5.1f}  {r['dark_pct']:5.1f}  "
                          f"{r['max_dark_run']:12d}  {r['max_gap_ms']:10.0f}  {beat:6.2f}"
                          + ("  SATURATED" if r["saturated_pct"] > 1 else ""))
                    np.savez(os.path.join(out, f"fps{fps}_exp{exp:.0f}.npz"), y=y, t=t)
    finally:
        set_rate(cam, None)          # the camera would otherwise keep the cap
        cam.Close()
    with open(os.path.join(out, "sweep.json"), "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"saved to {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--monitor", default=None)
    p.add_argument("--pattern", default="white")
    p.add_argument("--gain", type=float, default=0.0)
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("waveform", help="high-speed illumination waveform")
    w.add_argument("--exposure", type=float, default=34.0, help="us (min 34)")
    w.add_argument("--n", type=int, default=6000, help="samples (~780/s)")
    w.add_argument("--rows", type=int, default=16)
    w.add_argument("--width", type=int, default=1024)
    w.add_argument("--offset-x", type=int, default=600,
                   help="AOI inside the projected image (camera px)")
    w.add_argument("--offset-y", type=int, default=400)

    s = sub.add_parser("sweep", help="measure flicker vs fps and exposure")
    s.add_argument("--fps", type=float, nargs="+", default=[0.0],
                   help="camera frame rates to test; 0 = as fast as possible "
                        "(default: 0 only)")
    s.add_argument("--exposure", type=float, nargs="+", default=[1000.0],
                   help="exposures in us")
    s.add_argument("--seconds", type=float, default=6.0,
                   help="recording length per point")

    args = p.parse_args()
    {"waveform": cmd_waveform, "sweep": cmd_sweep}[args.cmd](args)


if __name__ == "__main__":
    main()
