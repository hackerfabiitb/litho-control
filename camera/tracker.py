"""Optical tracking of the wafer: how far the image of the wafer has moved,
in microns, measured from the camera rather than counted from motor steps.

Used by the UI's optical tracking (ui/server.py); runs in its own thread on
the CPU (one update ~15 ms at 896 x 512: plenty for the camera's 10-40 fps;
the pip OpenCV has no CUDA, and it isn't needed).

Method: FFT phase correlation of each frame against a reference frame
(keyframe), after downscaling 2x and high-pass filtering (illumination
falls off slowly; wafer features are edges).

Static content: the camera also sees things that do not move with the wafer
(the illumination's shape, the DMD fault band, dust on optics). They
correlate at zero shift. So the peak search skips a small radius around zero
and accepts the strongest other peak only if it stands out (peak-to-sidelobe
ratio >= PSR_MIN); otherwise the wafer is taken not to have moved. Cost: a
dead band of ~ZERO_RADIUS px (x2 for the downscale) around the reference.

Large moves: when the shift from the keyframe passes KEY_FRACTION of the
frame, the current frame becomes the keyframe and the shift so far is banked,
so the features seen at the start can leave the field entirely: the chain
of keyframes carries the position. If a frame doesn't match the keyframe
(it moved too far since), it is tried against the last frame that matched
and, if that works, becomes the new keyframe. Only if both fail (too fast,
out of the field, featureless area) does the tracker say "lost"; it keeps
trying against both.

Axes: the camera's axes are not the stage's. AxisMap learns, from moves of a
single stage axis, which camera direction is stage +X and +Y (direction
only: the scale stays the nominal field width, so a slipping motor shows up
as a difference instead of being calibrated away).
"""

import threading
import time

import cv2
import numpy as np

SCALE = 0.5            # downscale before correlating
HIGHPASS_SIGMA = 6.0   # px, at the downscaled size
ZERO_RADIUS = 2.0      # px (downscaled): the static peak's exclusion zone
PSR_MIN = 15.0         # real moving peaks: 44-91 in the synthetic test; noise: up to ~8
KEY_FRACTION = 0.22


def _prep(frame, window):
    h, w = window.shape
    img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32)
    img -= cv2.GaussianBlur(img, (0, 0), HIGHPASS_SIGMA)
    img /= max(float(img.std()), 1e-3)
    return cv2.dft(img * window, flags=cv2.DFT_COMPLEX_OUTPUT)


def _subpixel(surface, y, x):
    h, w = surface.shape

    def fit(m, c, p):
        d = m - 2 * c + p
        return 0.0 if abs(d) < 1e-12 else 0.5 * (m - p) / d

    dy = fit(surface[(y - 1) % h, x], surface[y, x], surface[(y + 1) % h, x])
    dx = fit(surface[y, (x - 1) % w], surface[y, x], surface[y, (x + 1) % w])
    return y + dy, x + dx


def correlate(ref_fft, cur_fft):
    """Shift (dx, dy) of cur relative to ref in downscaled px, ignoring the
    zero-shift (static) peak, and its peak-to-sidelobe ratio."""
    cross = cv2.mulSpectrums(cur_fft, ref_fft, 0, conjB=True)
    mag = cv2.magnitude(cross[..., 0], cross[..., 1])
    cross /= np.maximum(mag, 1e-9)[..., None]
    surface = np.fft.fftshift(cv2.idft(cross, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE))
    h, w = surface.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    search = surface.copy()
    search[(yy - cy) ** 2 + (xx - cx) ** 2 <= ZERO_RADIUS ** 2] = -np.inf
    y, x = np.unravel_index(int(np.argmax(search)), search.shape)
    peak = surface[y, x]
    side = np.ones_like(surface, bool)
    side[max(0, y - 5):y + 6, max(0, x - 5):x + 6] = False
    side[(yy - cy) ** 2 + (xx - cx) ** 2 <= (ZERO_RADIUS + 3) ** 2] = False
    lobes = surface[side]
    psr = float((peak - lobes.mean()) / max(float(lobes.std()), 1e-12))
    # A small wafer shift merges with the static peak at zero into one blob,
    # and its shoulder just outside ZERO_RADIUS looks like a peak. A separate
    # peak has a dip between it and zero; a shoulder doesn't.
    n = int(np.hypot(y - cy, x - cx))
    if n <= 4 * ZERO_RADIUS:
        ts = np.linspace(0.15, 0.85, 8)
        path = surface[np.round(cy + ts * (y - cy)).astype(int), np.round(cx + ts * (x - cx)).astype(int)]
        if path.min() > 0.8 * peak:
            psr = 0.0                         # a shoulder of the zero peak: no separate motion
    sy, sx = _subpixel(surface, y, x)
    return sx - cx, sy - cy, psr


class Tracker:
    """Feed it frames (feed()); read the wafer's shift with state()."""

    def __init__(self, um_per_px):
        self.um_per_px = um_per_px            # at full (fitted-view) resolution
        self.lock = threading.Lock()
        self._latest = None
        self._event = threading.Event()
        self._stop = threading.Event()
        self.window = None
        self.key = None                       # keyframe spectrum
        self.last_good = None                 # (spectrum, shift) of the last frame that matched
        self.banked = np.zeros(2)             # shift of the keyframe from the start, px (full res)
        self.shift = np.zeros(2)              # current shift from the start, px (full res)
        self.psr = 0.0
        self.status = "starting"
        self.updates = 0
        self.rate = 0.0
        self._times = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def feed(self, frame):
        self._latest = frame
        self._event.set()

    def stop(self):
        self._stop.set()
        self._event.set()

    def reset(self):
        with self.lock:
            self.key = None
            self.last_good = None
            self.banked[:] = 0
            self.shift[:] = 0
            self.status = "starting"

    def state(self):
        with self.lock:
            u, v = self.shift * self.um_per_px
            return {"status": self.status, "u_um": round(float(u), 2), "v_um": round(float(v), 2),
                    "psr": round(self.psr, 1), "rate": round(self.rate, 1)}

    def _run(self):
        while not self._stop.is_set():
            self._event.wait(1.0)
            self._event.clear()
            frame, self._latest = self._latest, None
            if frame is None or self._stop.is_set():
                continue
            self._update(frame)

    def _update(self, frame):
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = int(frame.shape[0] * SCALE), int(frame.shape[1] * SCALE)
        if self.window is None or self.window.shape != (h, w):
            self.window = cv2.createHanningWindow((w, h), cv2.CV_32F)
        cur = _prep(frame, self.window)
        now = time.perf_counter()
        with self.lock:
            self._times = [t for t in self._times if now - t < 2.0] + [now]
            self.rate = len(self._times) / 2.0
            self.updates += 1
            if self.key is None:
                self.key, self.status = cur, "tracking"
                self.shift = self.banked.copy()
                self.last_good = (cur, self.shift.copy())
                return
            key, last_good = self.key, self.last_good
        dx, dy, psr = correlate(key, cur)
        fallback = None
        if psr < PSR_MIN and last_good is not None and last_good[0] is not key:
            fdx, fdy, fpsr = correlate(last_good[0], cur)
            if fpsr >= PSR_MIN:
                fallback = (fdx, fdy, fpsr)
        with self.lock:
            self.psr = psr
            if psr >= PSR_MIN:
                d = np.array([dx, dy]) / SCALE
                self.shift = self.banked + d
                self.status = "tracking"
                self.last_good = (cur, self.shift.copy())
                if max(abs(dx) / w, abs(dy) / h) > KEY_FRACTION:
                    self.key, self.banked = cur, self.shift.copy()
            elif fallback is not None:          # too far from the keyframe: chain on
                fdx, fdy, self.psr = fallback
                self.shift = last_good[1] + np.array([fdx, fdy]) / SCALE
                self.key, self.banked = cur, self.shift.copy()
                self.last_good = (cur, self.shift.copy())
                self.status = "tracking"
            elif np.hypot(*(self.shift - self.banked)) > 3 * ZERO_RADIUS / SCALE:
                self.status = "lost"          # we had a moving peak and it's gone
            else:
                self.shift = self.banked.copy()   # no clear motion: at the keyframe
                self.status = "tracking"


class AxisMap:
    """Camera direction of stage +X and +Y, learned from single-axis moves."""

    MIN_STEPS = 20

    def __init__(self):
        self.samples = {"X": [], "Y": []}     # (unit vector, measured um / nominal um)

    def add(self, d_steps, d_nominal_um, d_cam_um):
        """d_steps: (dx, dy) of the move in steps; d_nominal_um: same in um;
        d_cam_um: (du, dv) seen by the camera."""
        moved = [a for a, d in zip("XY", d_steps) if d != 0]
        if len(moved) != 1:
            return
        a = moved[0]
        i = "XY".index(a)
        if abs(d_steps[i]) < self.MIN_STEPS:
            return
        cam = np.asarray(d_cam_um, float)
        n = np.hypot(*cam)
        if n < 1e-6:
            return
        sign = np.sign(d_nominal_um[i])
        self.samples[a].append((cam / n * sign, float(n / abs(d_nominal_um[i]))))
        self.samples[a] = self.samples[a][-9:]

    def learned(self):
        return {a: bool(self.samples[a]) for a in "XY"}

    def direction(self, a):
        v = np.median(np.array([s[0] for s in self.samples[a]]), axis=0)
        return v / np.hypot(*v)

    def scale(self, a):
        return None if not self.samples[a] else float(np.median([s[1] for s in self.samples[a]]))

    def to_stage(self, cam_um):
        """Camera shift -> stage X, Y (um), once both axes are learned."""
        if not all(self.learned().values()):
            return None
        m = np.column_stack([self.direction("X"), self.direction("Y")])
        x, y = np.linalg.solve(m, np.asarray(cam_um, float))
        return float(x), float(y)
