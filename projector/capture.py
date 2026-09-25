"""Camera side of the projector tests: grab a steady image of whatever the DMD
is showing, despite the flicker.

At short exposures most frames land between illumination pulses and come out
black (see camera/README.md). A single pattern is static, so rather than
thresholding live we grab a burst, keep the frames whose mean is at least half
the brightest one, and take the per-pixel median of those.
"""

import datetime
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "camera"))

from pylon_utils import frame_score, grab_frames, open_camera  # noqa: E402

CAPTURES = os.path.join(HERE, "captures")
CALIB_PATH = os.path.join(CAPTURES, "calib.npz")

# 100 us: short enough that white does not saturate (1000 us does), long
# enough that lit frames sit well above the sensor floor.
DEFAULT_EXPOSURE_US = 100.0


def open_cam(exposure_us=DEFAULT_EXPOSURE_US, gain=None):
    return open_camera(exposure_us=exposure_us, gain=gain, full=True,
                       verbose=False)


def lit_stack(cam, n=24):
    """Grab `n` frames and return (lit frames as float32 stack, all means)."""
    frames, means = [], []
    for i, frame, _ in grab_frames(cam):
        frames.append(frame)
        means.append(frame_score(frame)[0])
        if i + 1 >= n:
            break
    means = np.array(means)
    keep = means >= 0.5 * means.max()
    return np.stack([f for f, k in zip(frames, keep) if k]).astype(np.float32), means


def lit_median(cam, n=24):
    stack, _ = lit_stack(cam, n)
    return np.median(stack, axis=0)


def new_run_dir(tag):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(CAPTURES, f"{stamp}_{tag}")
    os.makedirs(path, exist_ok=True)
    return path


def to_u8(img, normalise=False):
    if normalise:
        img = img * 255.0 / max(float(img.max()), 1e-6)
    return np.clip(img, 0, 255).astype(np.uint8)


def load_calib():
    if not os.path.exists(CALIB_PATH):
        raise SystemExit(f"no calibration at {CALIB_PATH} — run "
                         "projector/dmd_calibrate.py first")
    c = np.load(CALIB_PATH)
    return {k: c[k] for k in c.files}
