"""Shared helpers for opening a Basler camera and scoring frame brightness.

Targets the acA1920-40um (USB3, mono) but the node handling falls back to the
older GigE/USB node names where they differ.
"""

import sys

import numpy as np
from pypylon import genicam, pylon


# --------------------------------------------------------------------------- #
# camera setup
# --------------------------------------------------------------------------- #

def _try_set(cam, names, value, label=None):
    """Set the first writable node in `names` to `value`.

    Returns the node name that took the value, or None if none were usable.
    Cameras differ in which nodes they expose (ExposureTime vs ExposureTimeAbs,
    Gain vs GainRaw, ...), so every write is best-effort.
    """
    for name in names:
        node = getattr(cam, name, None)
        if node is None:
            continue
        try:
            if not genicam.IsWritable(node):
                continue
            node.SetValue(value)
            return name
        except genicam.GenericException as exc:
            print(f"  ! {label or name}: {exc}", file=sys.stderr)
    return None


def _try_get(cam, names):
    for name in names:
        node = getattr(cam, name, None)
        if node is None:
            continue
        try:
            if genicam.IsReadable(node):
                return node.GetValue()
        except genicam.GenericException:
            continue
    return None


def open_camera(serial=None, exposure_us=None, gain=None, fps=None,
                pixel_format="Mono8", verbose=True):
    """Open and configure the camera. Caller is responsible for cam.Close()."""
    tlf = pylon.TlFactory.GetInstance()
    devices = tlf.EnumerateDevices()
    if not devices:
        raise RuntimeError("No Basler camera found. Check the USB3 cable and "
                           "that no other app (e.g. pylon Viewer) has it open.")

    if serial:
        match = [d for d in devices if d.GetSerialNumber() == str(serial)]
        if not match:
            found = ", ".join(d.GetSerialNumber() for d in devices)
            raise RuntimeError(f"Serial {serial} not found. Available: {found}")
        info = match[0]
    else:
        info = devices[0]

    try:
        cam = pylon.InstantCamera(tlf.CreateDevice(info))
        cam.Open()
    except genicam.GenericException as exc:
        if "exclusively opened" in str(exc):
            raise RuntimeError(
                f"{info.GetModelName()} ({info.GetSerialNumber()}) is already "
                "open in another process — a USB3 Basler allows only one client "
                "at a time. Close pylon Viewer (or the other script) and retry."
            ) from exc
        raise

    if verbose:
        print(f"Camera: {info.GetModelName()}  serial {info.GetSerialNumber()}")

    _try_set(cam, ["PixelFormat"], pixel_format)

    # Auto exposure / gain would fight the flicker we are trying to measure,
    # so both are forced off whenever the user pins a value.
    if exposure_us is not None:
        _try_set(cam, ["ExposureAuto"], "Off")
        _try_set(cam, ["ExposureTime", "ExposureTimeAbs"], float(exposure_us),
                 label="exposure")
    if gain is not None:
        _try_set(cam, ["GainAuto"], "Off")
        if _try_set(cam, ["Gain"], float(gain), label="gain") is None:
            _try_set(cam, ["GainRaw"], int(gain), label="gain")
    if fps is not None:
        _try_set(cam, ["AcquisitionFrameRateEnable"], True)
        _try_set(cam, ["AcquisitionFrameRate", "AcquisitionFrameRateAbs"],
                 float(fps), label="fps")

    if verbose:
        exp = _try_get(cam, ["ExposureTime", "ExposureTimeAbs"])
        g = _try_get(cam, ["Gain", "GainRaw"])
        rate = _try_get(cam, ["ResultingFrameRate", "ResultingFrameRateAbs"])
        pf = _try_get(cam, ["PixelFormat"])
        print(f"  pixel format {pf}, exposure {exp} us, gain {g}, "
              f"resulting rate {rate if rate is None else round(rate, 2)} fps")

    return cam


def grab_frames(cam, strategy=pylon.GrabStrategy_OneByOne, timeout_ms=5000):
    """Yield (index, numpy array, timestamp_ns) for each grabbed frame.

    Stops when the caller breaks out of the loop; grabbing is torn down in the
    finally block either way.
    """
    cam.StartGrabbing(strategy)
    index = 0
    try:
        while cam.IsGrabbing():
            result = cam.RetrieveResult(timeout_ms, pylon.TimeoutHandling_ThrowException)
            try:
                if not result.GrabSucceeded():
                    print(f"  ! grab failed: {result.GetErrorDescription()}",
                          file=sys.stderr)
                    continue
                # Copy: the array is a view into the buffer we are about to release.
                frame = result.GetArray().copy()
                try:
                    ts = result.GetTimeStamp()
                except genicam.GenericException:
                    ts = 0
                yield index, frame, ts
                index += 1
            finally:
                result.Release()
    finally:
        cam.StopGrabbing()


# --------------------------------------------------------------------------- #
# brightness scoring
# --------------------------------------------------------------------------- #

def frame_score(frame):
    """Brightness score used to separate lit frames from black ones.

    Mean intensity is the primary signal; it is stable and cheap. p99 is also
    returned because a frame can be mostly dark background yet still contain a
    lit feature.
    """
    flat = frame.reshape(-1)
    # Subsample large frames: 1920x1200 mean over every 4th pixel is within
    # noise of the full mean and ~4x cheaper.
    sub = flat[::4] if flat.size > 200_000 else flat
    return float(sub.mean()), float(np.percentile(sub, 99)), float(sub.max())


def suggest_threshold(means):
    """Pick a mean-intensity cutoff separating black frames from lit ones.

    1-D 2-means clustering (Otsu-equivalent for this data): if the frames really
    do fall into two brightness populations the split lands between them.
    Returns (threshold, dark_center, bright_center, bimodal).
    """
    means = np.asarray(means, dtype=float)
    if means.size < 2:
        return float(means.mean()) / 2 if means.size else 0.0, 0.0, 0.0, False

    lo, hi = means.min(), means.max()
    if hi - lo < 1e-9:
        return lo / 2, lo, hi, False

    c0, c1 = lo, hi
    for _ in range(50):
        mid = (c0 + c1) / 2
        dark, bright = means[means <= mid], means[means > mid]
        if dark.size == 0 or bright.size == 0:
            break
        n0, n1 = dark.mean(), bright.mean()
        if abs(n0 - c0) < 1e-6 and abs(n1 - c1) < 1e-6:
            break
        c0, c1 = n0, n1

    threshold = (c0 + c1) / 2
    # Separation of >5x between cluster centres (or a wide absolute gap) is the
    # signature of true on/off flicker rather than ordinary brightness noise.
    bimodal = (c1 > 5 * max(c0, 0.1)) or (c1 - c0 > 10)
    return float(threshold), float(c0), float(c1), bool(bimodal)


def histogram(means, bins=20, width=50):
    """Small text histogram so the mean distribution is visible in the terminal."""
    means = np.asarray(means, dtype=float)
    counts, edges = np.histogram(means, bins=bins)
    peak = counts.max() or 1
    lines = []
    for count, left, right in zip(counts, edges[:-1], edges[1:]):
        bar = "#" * int(round(width * count / peak))
        lines.append(f"  {left:7.2f}–{right:7.2f} | {bar} {count}")
    return "\n".join(lines)
