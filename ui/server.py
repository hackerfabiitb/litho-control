"""Control UI server: stage, projector display and deflickered camera.

    python ui/server.py                 # then open http://localhost:8765
    .\\ui\\start.ps1                     # starts it and opens the browser

One process owns the stage's serial port, the camera and the projector
window, and serves the UI page (ui/index.html). The stage connects by itself:
the server finds the Uno (xyz_stage/find_arduino.py) at startup, and again
every 3 s if it is unplugged, so the page never has to pick a port.

GET  /                       the UI
GET  /stage/events           server-sent events: `line` (each line from the
                             firmware), `replay` (cached state lines sent to a
                             new page: POS, LIMITS, CAL..., FOCUS) and
                             `status` (JSON: connected, port, idle_mode, error)
GET  /stage/status           that status JSON
POST /stage/send             {"line": "J X 10"} — one firmware command
POST /stage/connect          (re)connect automatically — the default
POST /stage/disconnect       release the COM port, e.g. to flash firmware
GET  /stream                 deflickered camera feed, MJPEG (full resolution)
GET  /config                 camera settings and live stats, JSON
POST /config                 {"exposure_us", "keep_frac", "max_hold", "deflicker",
                             "transform"} (any subset; deflicker false = raw feed;
                             transform false = the whole sensor, unwarped)
POST /camera/calibrate       project patterns/calibrate_deflicker.png for 5 s,
                             run the keep rule's cutoff on those frames once and
                             freeze it (saved in camera/deflicker_calibration.json);
                             valid at the exposure it was made at only
POST /camera/auto_exposure   set the exposure so the brightest frames of what is
                             projected now are bright but not clipped (p99 of the
                             lit frames in 120..235); answers {"exposure_us", "p99"}
POST /camera/fit_transform   project white, find the projected image's outline
                             (camera/fit_transform.py) and from then on show the
                             camera warped to an upright 16:9 view of just that
                             field (saved in camera/fit_transform.json)
GET  /focus_score            Laplacian variance of the latest shown camera frame
                             (sharpness: what autofocus maximises)
POST /scan/start             {"pitch": 0.7, "af_range", "af_step", "af_settle" (ms),
                             "autofocus": true}: image the wafer field by field in a
                             spiral from here and stitch it (ui/scan.py); the
                             stitched image replaces the projector preview
POST /scan/stop              finish the current field, save to scans/<date>/
GET  /scan/status            phase, fields done, last registration, saved folder
POST /track/start            optical tracking: project white, pick an unclipped
                             exposure, and measure the wafer's motion from the
                             camera (camera/tracker.py); needs Fit transform
POST /track/stop             stop it, put back projector and exposure
POST /track/zero             zero both readings (stepper and optical) here
GET  /track/status           stepper vs optical X/Y in um, their difference,
                             tracker status and quality, learned axes
GET  /focus                  the saved focus Z: {"z", "source", "date"} (z null if none)
POST /focus                  {"z": 1234, "source": "autofocus"}: save it
                             (xyz_stage/focus.json; loaded at startup, nothing moves)
GET  /projector/stream       what the projector displays, captured from its
                             screen, MJPEG — so it also shows anything else
                             that ends up on the projector, and the mouse
                             pointer when it is on the projector's screen
GET  /projector/status       JSON
POST /projector/image?fit=native|contain|cover|stretch&name=...
                             body: an image file; shown full-screen on the
                             projector at its native resolution
POST /projector/fit          {"fit": ...} re-fit the current image
POST /projector/blank        show black (window stays up)
POST /projector/remember     note what the projector shows (e.g. before autofocus
                             puts up its checkerboard) ...
POST /projector/restore      ... and put that back
POST /projector/pattern      {"name": "white_4K.jpg"}: show an image from patterns/
                             {"checker": 100}: a black/white checkerboard with
                             100 px squares at the projector's resolution
                             (top-left square white, as patterns/make_checkerboards.py)
POST /projector/close        close the window; the projector shows the desktop
POST /shutdown               stop the server cleanly (ui\\start.ps1 sends it
                             when the UI window is closed)

The camera runs the same keep rule as camera\\run.ps1 (camera/deflicker.py):
full sensor, 400 us, keep frames >= 0.8 x the recent top, never hold a frame
longer than 0.5 s. While the server runs, no other program can open the camera.
"""

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "camera"))
sys.path.insert(0, os.path.join(ROOT, "projector"))
sys.path.insert(0, os.path.join(ROOT, "xyz_stage"))

from deflicker import KeepFilter, relative_cutoff  # noqa: E402
from optics import FIELD_WIDTH_UM  # noqa: E402
from tracker import AxisMap, Tracker  # noqa: E402
from scan import Scan  # noqa: E402  (ui/scan.py)
from fit_transform import draw_debug, find_quad, make_transform  # noqa: E402
from display import (PatternWindow, detached_displays, extend_desktop,  # noqa: E402
                     find_projector, make_pattern)  # (importing display sets DPI awareness)
from find_arduino import BAUD, find_arduino  # noqa: E402
from pylon_utils import _try_set, frame_score, grab_frames, open_camera  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FITS = ("native", "contain", "cover", "stretch")
PATTERNS = os.path.join(ROOT, "patterns")
CAL_IMAGE = "calibrate_deflicker.png"
CAL_SECONDS = 5.0
CAL_FILE = os.path.join(ROOT, "camera", "deflicker_calibration.json")
FIT_FILE = os.path.join(ROOT, "camera", "fit_transform.json")
FOCUS_FILE = os.path.join(ROOT, "xyz_stage", "focus.json")
HOMING_FILE = os.path.join(ROOT, "xyz_stage", "homing.json")


# The focus Z lives on disk, owned by the server: "Set focus here" and an
# accepted autofocus result save it; it is loaded at startup without moving
# anything; "Go to focus" is a plain move to it. (The Uno's own FOCUS SET
# can only store the current Z; its stored value is adopted once, when no
# file exists yet.)
def load_focus():
    try:
        with open(FOCUS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"z": None, "source": None, "date": None}


def save_focus(z, source):
    focus = {"z": int(z), "source": str(source), "date": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(FOCUS_FILE, "w", encoding="utf-8") as fh:
        json.dump(focus, fh, indent=2)
    print(f"[stage] focus saved: Z = {focus['z']} ({focus['source']})", flush=True)
    return focus
FIT_DEBUG = os.path.join(ROOT, "camera", "fit_transform_debug.jpg")
FIT_EXPOSURE_US = 50.0      # white unsaturated (camera/README.md, 2026-10-07)


def load_pattern(name):
    """An image from patterns/ (by file name only)."""
    path = os.path.join(PATTERNS, os.path.basename(name))
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"no image patterns/{os.path.basename(name)}")
    return img


class Latest:
    """The newest JPEG of a stream, with a condition to wait for the next."""

    def __init__(self):
        self.cond = threading.Condition()
        self.jpeg = None
        self.seq = 0
        self.clients = 0

    def put(self, jpeg):
        with self.cond:
            self.jpeg = jpeg
            self.seq += 1
            self.cond.notify_all()

    def wait(self, seq, timeout=1.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != seq, timeout)
            return self.seq, self.jpeg


def encode(img, quality=85):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #

class Stage(threading.Thread):
    """Owns the Uno's serial port and fans its output out to every page.

    Opening the port resets the Uno, which then sits in its bootloader for
    ~1.5 s — bytes sent then are read as bootloader commands ('P' is one).
    So the server sends nothing until the firmware's READY, then only a 'P'
    to replace the boot banner's often-garbled POS line. A newly opened page
    gets the state from cached lines instead of by querying the firmware.
    """

    RETRY_S = 3.0

    def __init__(self):
        super().__init__(daemon=True)
        self.ser = None
        self.port = None
        self.error = None
        self.want = True                     # stay connected / keep retrying
        self.idle_mode = "release"           # xyz1 boots releasing at idle
        self.cond = threading.Condition()
        self.events = deque(maxlen=1000)     # (seq, kind, data)
        self.seq = 0
        self.cache = {}                      # state lines replayed to new pages
        self.write_lock = threading.Lock()
        self.listeners = []                  # callables given every firmware line
        self.last_send = 0.0                 # time.monotonic() of the last command

    def status(self):
        # released: the port was let go on request (the UI's Disconnect, or
        # POST /stage/disconnect) and isn't being retried
        return {"connected": self.ser is not None, "port": self.port,
                "idle_mode": self.idle_mode, "error": self.error,
                "released": not self.want}

    def emit(self, kind, data):
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, kind, data))
            self.cond.notify_all()

    def replay(self):
        with self.cond:
            return list(self.cache.values())

    def send(self, line):
        with self.write_lock:
            if self.ser is None:
                raise RuntimeError("stage not connected")
            self.ser.write((line.strip() + "\n").encode())
            self.last_send = time.monotonic()

    def connect(self):
        if not self.want:
            print("[stage] connect requested: taking the port again", flush=True)
        self.want = True

    def disconnect(self, reason="Disconnect in the UI"):
        if self.want:
            print(f"[stage] releasing {self.port or 'the port'} ({reason}); "
                  f"click Connect (STAGE panel) to take it back", flush=True)
        self.want = False
        deadline = time.time() + 2
        while self.ser is not None and time.time() < deadline:
            time.sleep(0.05)

    def _remember(self, line):
        """Keep the latest line of each kind that describes state."""
        words = line.split()
        if line.startswith("CAL ") and len(words) >= 3 and words[2] == "DONE":
            key = "CAL " + words[1]          # per-axis calibration
        elif line == "CAL CLEARED":
            for axis in "XYZ":
                self.cache.pop("CAL " + axis, None)
            return
        elif line.startswith("CAL CLEAR ") and len(words) == 3:
            self.cache.pop("CAL " + words[2], None)
            return
        elif line.startswith("CAL INFO"):
            key = "CAL INFO"
        elif line in ("CALIBRATED", "UNCALIBRATED"):
            key = "CALSTATE"
        elif words and words[0] in ("POS", "LIMITS", "FOCUS", "LS"):
            key = words[0]
        else:
            return
        with self.cond:
            self.cache.pop(key, None)        # re-insert: replay in recency order
            self.cache[key] = line

    def run(self):
        import serial
        while True:
            if not self.want:
                time.sleep(0.2)
                continue
            try:
                port = find_arduino()
                ser = serial.Serial(port, BAUD, timeout=0.2)
            except Exception as exc:         # no Uno, or COM4 held elsewhere
                error = str(exc).splitlines()[0][:200]
                if error != self.error:      # once per new problem, not per retry
                    print(f"[stage] {error} (retrying every {self.RETRY_S:.0f} s)",
                          file=sys.stderr, flush=True)
                self.error = error
                self.emit("status", json.dumps(self.status()))
                time.sleep(self.RETRY_S)
                continue
            with self.cond:
                self.cache.clear()           # the Uno just reset
            self.ser, self.port, self.error = ser, port, None
            self.idle_mode = "release"
            self.emit("status", json.dumps(self.status()))
            print(f"[stage] connected on {port}", flush=True)
            buf = b""
            try:
                while self.want:
                    chunk = ser.read(256)
                    if not chunk:
                        continue
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for raw in lines:
                        line = raw.decode(errors="replace").strip()
                        if not line:
                            continue
                        mode = {"ENABLED": "hold", "DISABLED": "release",
                                "READY": "release"}.get(line)
                        self._remember(line)
                        if (line.startswith("FOCUS ") and line[6:].lstrip("-").isdigit()
                                and load_focus()["z"] is None):
                            save_focus(int(line[6:]), "adopted from the Uno (FOCUS SET)")
                        self.emit("line", line)
                        for listener in list(self.listeners):
                            listener(line)
                        if line == "READY":
                            # The first line after a reset often arrives with
                            # bytes missing (seen: "POS -704 648 9038" for
                            # "POS -17084 6848 9046"). READY means the firmware
                            # is running, past the bootloader, so it is now safe
                            # to ask for the position again.
                            self.send("P")
                        if mode and mode != self.idle_mode:
                            self.idle_mode = mode
                            self.emit("status", json.dumps(self.status()))
            except (serial.SerialException, OSError) as exc:
                self.error = str(exc).splitlines()[0][:200]
                print(f"[stage] {self.error}", file=sys.stderr)
            finally:
                with self.write_lock:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    self.ser = None
                self.emit("status", json.dumps(self.status()))
            if self.want:
                time.sleep(self.RETRY_S)


# --------------------------------------------------------------------------- #
# camera
# --------------------------------------------------------------------------- #

class Camera(threading.Thread):
    def __init__(self, exposure_us, keep_frac, max_hold):
        super().__init__(daemon=True)
        self.stream = Latest()
        self.filter = KeepFilter(keep_frac, max_hold)
        self.exposure_us = exposure_us
        self.pending = {}                    # settings to apply on the grab thread
        self.lock = threading.Lock()
        self.connected = False
        self.error = None
        self.last_frame = None
        self.mean = 0.0
        self.calibration = None              # see calibrate(); loaded below
        self.collect = None                  # frame means go here while calibrating
        self.collect_frames = None           # whole frames, while fitting the transform
        self.calibration_stale = False
        self.transform = None                # see fit_transform(); loaded below
        self.transform_on = True
        self._warp = None                    # (matrix, size) while the transform is on
        self.tracker = None                  # a camera/tracker.Tracker while tracking
        self.frame_seq = 0                   # counts kept frames (scan waits on it)
        # appended by the grab thread, only read elsewhere
        self.in_times, self.shown_times = deque(maxlen=400), deque(maxlen=400)
        self.stop_flag = threading.Event()
        self._load_calibration()
        self._load_transform()

    # The deflicker's cutoff is keep_frac x "the top of the recent frame
    # means" (camera/deflicker.py). Live, that top follows the projected
    # image, so an all-white image pushes it up. Calibration runs the same
    # computation once, on CAL_SECONDS of a reference image, and freezes the
    # top. It only holds at the exposure it was made at: below one projector
    # period (4.2 ms) the brightest frames are not proportional to exposure
    # (measured 2026-10-07: the calibration image's top is 19 at 400 us but
    # 143 at 14 ms, not 400/14000 of it). At any other exposure the live rule
    # runs and the UI asks for a recalibration.

    def _load_calibration(self):
        try:
            with open(CAL_FILE, encoding="utf-8") as fh:
                self.calibration = json.load(fh)
        except (OSError, ValueError):
            return
        self._apply_calibration()

    def _apply_calibration(self):
        c = self.calibration
        self.calibration_stale = abs(c["exposure_us"] - self.exposure_us) > 0.5
        self.filter.fixed_top = None if self.calibration_stale else c["top"]

    def calibrate(self, projector):
        """Show the reference image, collect frame means for CAL_SECONDS,
        freeze the top, then put back what the projector was showing."""
        if not self.connected:
            raise RuntimeError(f"camera not connected ({self.error})")
        image = load_pattern(CAL_IMAGE)
        projector.remember()
        projector.source, projector.image_name, projector.fit = image, CAL_IMAGE, "native"
        try:
            if projector.do("show")["state"] != "image":
                raise RuntimeError(f"projector display not found ({projector.error})")
            time.sleep(0.5)                  # let the new image reach the camera
            self.collect = []
            time.sleep(CAL_SECONDS)
            means, self.collect = self.collect, None
        finally:
            self.collect = None
            projector.restore()
        if len(means) < 20:
            raise RuntimeError(f"only {len(means)} frames in {CAL_SECONDS:.0f} s")
        top = relative_cutoff(means, 1.0)    # the live rule's top, run once
        self.calibration = {
            "top": round(top, 2), "exposure_us": self.exposure_us,
            "frames": len(means), "image": CAL_IMAGE,
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._apply_calibration()
        with open(CAL_FILE, "w", encoding="utf-8") as fh:
            json.dump(self.calibration, fh, indent=2)
        print(f"[camera] deflicker calibrated: top {top:.1f} at {self.exposure_us:g} us"
              f" from {len(means)} frames -> cutoff {self.filter.cutoff:.1f}", flush=True)
        return self.calibration

    def _load_transform(self):
        try:
            with open(FIT_FILE, encoding="utf-8") as fh:
                self.transform = json.load(fh)
        except (OSError, ValueError):
            return
        self._apply_transform()

    def _apply_transform(self):
        t = self.transform
        self._warp = None
        if t and self.transform_on:
            self._warp = (np.array(t["matrix"], np.float64), tuple(t["size"]))

    def _grab_top(self, exposure_us, seconds=0.8):
        """Mean of the brightest frames at `exposure_us` (the lit ones)."""
        self.configure(exposure_us=exposure_us)
        time.sleep(0.6)                      # applied, and the old frames gone
        self.collect_frames = []
        time.sleep(seconds)
        frames, self.collect_frames = self.collect_frames, None
        if len(frames) < 5:
            raise RuntimeError(f"only {len(frames)} frames in {seconds} s")
        means = np.array([f.mean() for f in frames])
        top = [f for f, m in zip(frames, means) if m >= np.percentile(means, 85)]
        return np.mean(np.stack(top).astype(np.float32), axis=0)

    def auto_exposure(self, lo=120.0, hi=235.0, start=None):
        """Find an exposure where the lit frames of the current projection are
        bright but not clipped, set it, and return (exposure_us, p99).
        Autofocus needs this: at 400 us a checkerboard saturates (veil +
        clipping, camera/README.md 2026-10-07) and its edges vanish."""
        if not self.connected:
            raise RuntimeError(f"camera not connected ({self.error})")
        e = float(start or self.exposure_us)
        p99 = None
        for _ in range(7):
            img = self._grab_top(e, seconds=0.5)
            p99 = float(np.percentile(img, 99))
            if p99 >= 250 and e > 34:                       # clipped: no idea by how much
                e = max(34.0, e * 0.4)
            elif p99 > hi and e > 34:
                e = max(34.0, e * hi * 0.9 / p99)
            elif p99 < lo and e < 5000:
                e = min(5000.0, e * min(4.0, (lo + hi) / 2 / max(p99, 1.0)))
            else:
                break
        e = round(e)
        self.configure(exposure_us=e)
        print(f"[camera] auto exposure: {e} us (p99 of lit frames {p99:.0f})", flush=True)
        return e, p99

    def fit_transform(self, projector):
        """Project white, find its outline, store the transform to 16:9, then
        put back the exposure and what the projector was showing."""
        if not self.connected:
            raise RuntimeError(f"camera not connected ({self.error})")
        image = load_pattern("white_4K.jpg")
        projector.remember()
        exposure = self.exposure_us
        projector.source, projector.image_name, projector.fit = image, "white_4K.jpg", "native"
        try:
            if projector.do("show")["state"] != "image":
                raise RuntimeError(f"projector display not found ({projector.error})")
            e = FIT_EXPOSURE_US
            for _ in range(4):               # keep white unsaturated, but well lit
                img = self._grab_top(e)
                p99 = float(np.percentile(img, 99))
                if p99 > 245 and e > 34:
                    e = max(34.0, e / 2)
                elif p99 < 40 and e < 800:
                    e = e * 2
                else:
                    break
        finally:
            self.collect_frames = None
            self.configure(exposure_us=exposure)
            projector.restore()
        corners = find_quad(img)
        matrix, size, src = make_transform(corners)
        self.transform = {
            "matrix": matrix.tolist(), "size": list(size),
            "corners": np.round(src, 1).tolist(), "exposure_us": e,
            "sensor": [int(img.shape[1]), int(img.shape[0])],
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.transform_on = True
        self._apply_transform()
        with open(FIT_FILE, "w", encoding="utf-8") as fh:
            json.dump(self.transform, fh, indent=2)
        cv2.imwrite(FIT_DEBUG, draw_debug(img, src))
        print(f"[camera] fit transform: corners {self.transform['corners']} -> "
              f"{size[0]}x{size[1]} (white at {e:g} us)", flush=True)
        return self.transform

    def configure(self, **kw):
        with self.lock:
            self.pending.update({k: v for k, v in kw.items() if v is not None})

    def _apply_pending(self, cam):
        with self.lock:
            pending, self.pending = self.pending, {}
        if "exposure_us" in pending:
            self.exposure_us = float(pending["exposure_us"])
            _try_set(cam, ["ExposureTime"], self.exposure_us)
            self.filter.reset()              # the brightness scale changed
            if self.calibration:
                self._apply_calibration()    # only valid at its own exposure
        if "keep_frac" in pending:
            self.filter.keep_frac = float(pending["keep_frac"])
        if "max_hold" in pending:
            self.filter.max_hold = float(pending["max_hold"])
        if "deflicker" in pending:
            self.filter.enabled = bool(pending["deflicker"])
        if "transform" in pending:
            self.transform_on = bool(pending["transform"])
            self._apply_transform()

    @staticmethod
    def _rate(times, now, span=2.0):
        return sum(1 for t in list(times) if now - t < span) / span

    def stats(self):
        now = time.perf_counter()
        f = self.filter
        return {
            "connected": self.connected, "error": self.error,
            "exposure_us": self.exposure_us, "keep_frac": f.keep_frac,
            "max_hold": f.max_hold, "mean": round(self.mean, 1),
            "cutoff": round(f.cutoff, 1),
            "in_fps": round(self._rate(self.in_times, now), 1),
            "shown_fps": round(self._rate(self.shown_times, now), 1),
            "kept": f.kept, "forced": f.forced, "total": f.total,
            "longest_hold_ms": round(1e3 * f.longest_hold),
            "deflicker": f.enabled, "calibration": self.calibration,
            "calibration_stale": self.calibration_stale,
            "transform": None if not self.transform else {
                "on": self.transform_on, "size": self.transform["size"],
                "date": self.transform["date"]},
        }

    def run(self):
        from pypylon import pylon
        while not self.stop_flag.is_set():
            cam = None
            try:
                cam = open_camera(exposure_us=self.exposure_us, full=True,
                                  verbose=False)
                self.connected, self.error = True, None
                print("[camera] open", flush=True)
                for _, frame, _ in grab_frames(cam, pylon.GrabStrategy_LatestImageOnly):
                    if self.stop_flag.is_set():
                        break
                    self._apply_pending(cam)
                    now = time.perf_counter()
                    self.in_times.append(now)
                    self.mean = frame_score(frame)[0]
                    if self.collect is not None:
                        self.collect.append(self.mean)
                    if self.collect_frames is not None and len(self.collect_frames) < 80:
                        self.collect_frames.append(frame.copy())
                    show, _ = self.filter.decide(self.mean, now)
                    if show:
                        self.shown_times.append(now)
                        warp = self._warp
                        if warp is not None:         # just the projected field, 16:9
                            frame = cv2.warpPerspective(frame, warp[0], warp[1],
                                                        flags=cv2.INTER_LINEAR)
                        self.last_frame = frame
                        self.frame_seq += 1
                        tracker = self.tracker
                        if tracker is not None:
                            tracker.feed(frame)
                        if self.stream.clients:
                            self.stream.put(encode(frame))
            except Exception as exc:         # busy camera, unplugged, ...
                self.connected = False
                error = str(exc).splitlines()[0][:200]
                if error != self.error:      # once per new problem, not per retry
                    print(f"[camera] {error} (retrying every 3 s)",
                          file=sys.stderr, flush=True)
                self.error = error
                self.stop_flag.wait(3.0)
            finally:
                if cam is not None:
                    try:
                        cam.Close()
                    except Exception:
                        pass


# --------------------------------------------------------------------------- #
# projector: display thread + screen mirror
# --------------------------------------------------------------------------- #

def compose(src, fit, width, height):
    """Place `src` on a width x height black canvas.

    native: 1 image px = 1 projector px, centred (cropped if larger) — the
    mode for lithography masks. contain/cover scale to fit inside / fill.
    Upscaling uses nearest-neighbour so binary masks stay binary.
    """
    if fit == "stretch":
        return cv2.resize(src, (width, height), interpolation=cv2.INTER_NEAREST)
    h, w = src.shape[:2]
    scale = {"native": 1.0, "contain": min(width / w, height / h),
             "cover": max(width / w, height / h)}[fit]
    if scale != 1.0:
        interp = cv2.INTER_NEAREST if scale > 1 else cv2.INTER_AREA
        src = cv2.resize(src, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=interp)
        h, w = src.shape[:2]
    canvas = np.zeros((height, width, 3), np.uint8)
    # centre, cropping whichever of source/canvas is larger
    sx, dx = max(0, (w - width) // 2), max(0, (width - w) // 2)
    sy, dy = max(0, (h - height) // 2), max(0, (height - h) // 2)
    cw, ch = min(w, width), min(h, height)
    canvas[dy:dy + ch, dx:dx + cw] = src[sy:sy + ch, sx:sx + cw]
    return canvas


class Projector(threading.Thread):
    """Owns the full-screen OpenCV window. HighGUI windows must be created and
    pumped by one thread, so every change goes through a queue.

    The projector's monitor may be missing at startup (Windows set to "PC
    screen only", or the projector off). The stage and camera don't need it,
    so the server starts anyway and looks for the monitor again every
    RETRY_S seconds, and before each command. If a monitor is plugged in but
    left out of the desktop, it runs Win+P -> Extend (DisplaySwitch /extend),
    at most every EXTEND_S seconds. A switched-off projector lists no monitor,
    so then nothing is switched."""

    RETRY_S = 3.0
    EXTEND_S = 30.0

    def __init__(self, monitor_name=None, auto_extend=True):
        super().__init__(daemon=True)
        self.monitor_name = monitor_name     # --monitor, else the one non-primary
        self.auto_extend = auto_extend
        self._saved = None                   # see remember() / restore()
        self.monitor = None
        self.error = None
        self._found_at = 0.0
        self._extended_at = -1e9
        self.commands = queue.Queue()
        self.source = None
        self.image_name = None
        self.fit = "native"
        self.state = "closed"                # closed | blank | image
        self.find()

    def find(self):
        """Look for the projector's monitor; True if it is there."""
        had = self.monitor
        try:
            self.monitor, self.error = find_projector(self.monitor_name), None
        except RuntimeError as exc:
            self.monitor, self.error = None, str(exc)
            detached = self.auto_extend and detached_displays()
            if detached and time.monotonic() - self._extended_at > self.EXTEND_S:
                self._extended_at = time.monotonic()
                print(f"projector plugged in but not in the desktop "
                      f"({', '.join(detached)}): extending it (Win+P -> Extend)",
                      flush=True)
                extend_desktop()             # found on the next retry
        self._found_at = time.monotonic()
        if self.monitor and not had:
            m = self.monitor
            print(f"projector: {m.name} {m.width}x{m.height}", flush=True)
        return self.monitor is not None

    def remember(self):
        """Note what is showing, to put it back with restore()."""
        self._saved = (self.source, self.image_name, self.fit, self.state)

    def restore(self):
        saved, self._saved = getattr(self, "_saved", None), None
        if saved is None:
            return self.status()
        self.source, self.image_name, self.fit, state = saved
        if state == "image" and self.source is not None:
            return self.do("show")
        return self.do("blank" if state == "blank" else "close")

    def status(self):
        m = self.monitor
        return {"monitor": m and m.name, "width": m and m.width,
                "height": m and m.height, "error": self.error,
                "state": self.state, "image": self.image_name, "fit": self.fit,
                "image_size": None if self.source is None
                else [int(self.source.shape[1]), int(self.source.shape[0])]}

    def do(self, cmd, timeout=5.0):
        """Run a command on the window thread and wait until it is on screen,
        so the status returned to the UI is the real one."""
        done = threading.Event()
        self.commands.put((cmd, done))
        done.wait(timeout)
        return self.status()

    def run(self):
        win = None
        while True:
            try:
                cmd, done = self.commands.get(timeout=0.03)
            except queue.Empty:
                if win is not None:
                    cv2.waitKey(1)           # keep the window responsive
                elif (self.monitor is None
                      and time.monotonic() - self._found_at > self.RETRY_S):
                    self.find()
                continue
            try:
                if cmd == "close":
                    if win is not None:
                        win.__exit__(None, None, None)
                        win = None
                    self.state = "closed"
                    continue
                if win is None and not self.find():
                    continue                 # no projector: status carries the error
                if win is None:
                    win = PatternWindow(self.monitor, title="litho-projector").__enter__()
                m = self.monitor
                if cmd == "blank":
                    win.show(np.zeros((m.height, m.width), np.uint8), settle=0.05)
                    self.state = "blank"
                elif cmd == "show":
                    win.show(compose(self.source, self.fit, m.width, m.height), settle=0.05)
                    self.state = "image"
            finally:
                done.set()


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


def cursor_position():
    """The mouse pointer's position on the desktop in physical pixels (this
    process is per-monitor DPI aware, like the monitor rectangles), or None
    while the pointer is hidden."""
    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)) or not info.flags & 1:
        return None
    return info.ptScreenPos.x, info.ptScreenPos.y


# The standard arrow pointer, tip at (0, 0), in pixels of the mirror image.
# Screen captures don't include the pointer, so the mirror draws this one.
POINTER = np.array([[0, 0], [0, 17], [4, 13], [7, 20], [10, 19], [7, 12], [12, 12]],
                   np.float32) * 1.4


def draw_pointer(img, x, y):
    pts = np.round(POINTER + (x, y)).astype(np.int32)
    cv2.fillPoly(img, [pts], (255, 255, 255), cv2.LINE_AA)
    cv2.polylines(img, [pts], True, (0, 0, 0), 1, cv2.LINE_AA)


class Mirror(threading.Thread):
    """Screen capture of the projector's display, while anyone is watching
    and the projector's monitor is there."""

    def __init__(self, projector, width=1280, fps=5.0):
        super().__init__(daemon=True)
        self.projector = projector
        self.width = width
        self.period = 1.0 / fps
        self.stream = Latest()
        self.paused = False                  # a scan shows its stitched image here instead

    def run(self):
        import mss
        with mss.MSS() as grabber:
            while True:
                m = self.projector.monitor
                if not self.stream.clients or m is None or self.paused:
                    time.sleep(0.2)
                    continue
                rect = {"left": m.x, "top": m.y, "width": m.width, "height": m.height}
                height = round(self.width * m.height / m.width)
                t0 = time.perf_counter()
                try:
                    shot = np.asarray(grabber.grab(rect))[:, :, :3]
                except Exception:            # display removed mid-grab
                    time.sleep(1.0)
                    continue
                small = np.ascontiguousarray(
                    cv2.resize(shot, (self.width, height), interpolation=cv2.INTER_AREA))
                pointer = cursor_position()
                if pointer and (m.x <= pointer[0] < m.x + m.width
                                and m.y <= pointer[1] < m.y + m.height):
                    k = self.width / m.width
                    draw_pointer(small, (pointer[0] - m.x) * k, (pointer[1] - m.y) * k)
                self.stream.put(encode(small))
                time.sleep(max(0.0, self.period - (time.perf_counter() - t0)))


# --------------------------------------------------------------------------- #
# optical tracking
# --------------------------------------------------------------------------- #

def steps_per_mm():
    try:
        with open(HOMING_FILE, encoding="utf-8") as fh:
            axes = json.load(fh)["axes"]
        return {a: float(axes[a]["steps_per_mm"]) for a in "XYZ"}
    except (OSError, ValueError, KeyError):
        return {a: 400.0 for a in "XYZ"}


class Tracking:
    """Stepper position vs the wafer's optically measured motion.

    Stepper: the firmware's POS, relative to where tracking started (or was
    zeroed), in um via steps_per_mm. Optical: camera/tracker.Tracker on the
    fitted view, scaled by the nominal field width (FIELD_WIDTH_UM over the
    fitted view's width). The camera's axes are mapped to the stage's from
    settled single-axis moves (AxisMap): 0.7 s after each DONE, unless another
    command went out meanwhile, the pair (stepper, optical) is recorded."""

    SETTLE_S = 0.7

    def __init__(self, stage, camera, projector):
        self.stage, self.camera, self.projector = stage, camera, projector
        self.tracker = None
        self.axes = AxisMap()
        self.spmm = steps_per_mm()
        self.start_pos = None
        self.anchor = None                   # (pos, (u, v)) at the last settled point
        self.exposure_before = None
        self.um_per_px = None
        self.lock = threading.Lock()
        stage.listeners.append(self._on_line)

    @property
    def active(self):
        return self.tracker is not None

    def _pos(self):
        line = self.stage.cache.get("POS")
        try:
            return np.array([int(v) for v in line.split()[1:4]])
        except (AttributeError, ValueError):
            return None

    def start(self):
        with self.lock:
            if self.active:
                return
            cam = self.camera
            if not cam.connected:
                raise RuntimeError(f"camera not connected ({cam.error})")
            if not cam.transform or not cam.transform_on:
                raise RuntimeError("run Fit transform first (and View: fitted): the "
                                   "tracker's micron scale is the projected field's width")
            if self._pos() is None:
                raise RuntimeError("no stage position yet (is the stage connected?)")
            self.projector.remember()
            self.projector.source = load_pattern("white_4K.jpg")
            self.projector.image_name, self.projector.fit = "white_4K.jpg", "native"
            if self.projector.do("show")["state"] != "image":
                self.projector.restore()
                raise RuntimeError(f"projector display not found ({self.projector.error})")
            self.exposure_before = cam.exposure_us
            cam.auto_exposure(start=FIT_EXPOSURE_US)
            time.sleep(0.4)
            self.um_per_px = FIELD_WIDTH_UM / cam.transform["size"][0]
            self.tracker = Tracker(self.um_per_px)
            self.start_pos = self._pos()
            self.anchor = (self.start_pos, np.zeros(2))   # so the first move is learned too
            cam.tracker = self.tracker
            print(f"[track] started: {self.um_per_px:.4f} um per fitted px, "
                  f"exposure {cam.exposure_us:g} us", flush=True)

    def stop(self):
        with self.lock:
            if not self.active:
                return
            self.camera.tracker = None
            self.tracker.stop()
            self.tracker = None
            self.projector.restore()
            if self.exposure_before:
                self.camera.configure(exposure_us=self.exposure_before)
            print("[track] stopped", flush=True)

    def zero(self):
        if self.active:
            self.tracker.reset()
            self.start_pos = self._pos()
            self.anchor = (self.start_pos, np.zeros(2))

    def _on_line(self, line):
        if line == "DONE" and self.active:
            done_at = time.monotonic()
            threading.Timer(self.SETTLE_S, self._settled, args=(done_at,)).start()

    def _settled(self, done_at):
        tr = self.tracker
        if tr is None or self.stage.last_send > done_at:   # another move started
            return
        st, pos = tr.state(), self._pos()
        if st["status"] != "tracking" or pos is None:
            return
        cam = np.array([st["u_um"], st["v_um"]])
        if self.anchor is not None:
            last_pos, last_cam = self.anchor
            d = pos - last_pos
            nominal = [d[i] / self.spmm[a] * 1000 for i, a in enumerate("XY")]
            self.axes.add((int(d[0]), int(d[1])), nominal, cam - last_cam)
        self.anchor = (pos, cam)

    def status(self):
        tr = self.tracker
        if tr is None:
            return {"active": False, "axes": self.axes.learned()}
        st, pos = tr.state(), self._pos()
        d = (pos - self.start_pos) if pos is not None else np.zeros(3)
        nominal = {a: round(d[i] / self.spmm[a] * 1000, 2) for i, a in enumerate("XY")}
        optical = self.axes.to_stage((st["u_um"], st["v_um"]))
        out = {"active": True, **st, "stepper_um": nominal,
               "optical_um": None if optical is None else
               {"X": round(optical[0], 2), "Y": round(optical[1], 2)},
               "axes": self.axes.learned(),
               "scale": {a: self.axes.scale(a) for a in "XY"},
               "um_per_px": round(self.um_per_px, 4), "exposure_us": self.camera.exposure_us}
        if optical is not None:
            out["diff_um"] = {a: round(out["optical_um"][a] - nominal[a], 2) for a in "XY"}
        return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class UIServer(ThreadingHTTPServer):
    """One server per port: no SO_REUSEADDR, and SO_EXCLUSIVEADDRUSE on
    Windows, so a second copy fails to bind instead of sharing the port."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_handler(stage, camera, projector, mirror, shutdown, tracking, scan):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(n) if n else b""

        def _mjpeg(self, latest):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with latest.cond:
                latest.clients += 1
            seq = -1
            try:
                while True:
                    seq, jpeg = latest.wait(seq, timeout=1.0)
                    if jpeg is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                                     + jpeg + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                with latest.cond:
                    latest.clients -= 1

        def _projector_reply(self, st):
            if st["monitor"] is None and st["state"] == "closed" and st["error"]:
                return self._json({**st, "error": "projector display not found: "
                                   + st["error"]}, 409)
            self._json(st)

        def _events(self):
            """Server-sent events from the stage: status first, then the
            cached state lines as `replay`, then everything live."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            def event(kind, data):
                self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode())

            try:
                with stage.cond:
                    last = stage.seq
                event("status", json.dumps(stage.status()))
                for line in stage.replay():
                    event("replay", line)
                self.wfile.flush()
                while True:
                    with stage.cond:
                        stage.cond.wait_for(lambda: stage.seq != last, timeout=15)
                        new = [e for e in stage.events if e[0] > last]
                    if not new:
                        self.wfile.write(b": keepalive\n\n")
                    for seq, kind, data in new:
                        event(kind, data)
                        last = seq
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/stage/events":
                return self._events()
            if path == "/stage/status":
                return self._json(stage.status())
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            elif path == "/stream":
                self._mjpeg(camera.stream)
            elif path == "/projector/stream":
                self._mjpeg(mirror.stream)
            elif path in ("/config", "/ping"):
                self._json(camera.stats())
            elif path == "/projector/status":
                self._json(projector.status())
            elif path == "/focus":
                self._json(load_focus())
            elif path == "/track/status":
                self._json(tracking.status())
            elif path == "/scan/status":
                self._json(scan.status())
            elif path == "/focus_score":
                f = camera.last_frame
                score = 0.0 if f is None else float(cv2.Laplacian(f, cv2.CV_64F).var())
                self._json({"score": score})
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/stage/send":
                    line = str(json.loads(self._body() or b"{}").get("line", "")).strip()
                    if not line or "\n" in line or "\r" in line:
                        return self._json({"error": "need one command line"}, 400)
                    try:
                        stage.send(line)
                    except (RuntimeError, OSError) as exc:
                        return self._json({"error": str(exc)}, 409)
                    self._json({"ok": True})
                elif url.path == "/stage/connect":
                    stage.connect()
                    self._json(stage.status())
                elif url.path == "/stage/disconnect":
                    stage.disconnect("Disconnect clicked, or POST /stage/disconnect")
                    self._json(stage.status())
                elif url.path == "/config":
                    data = json.loads(self._body() or b"{}")
                    camera.configure(exposure_us=data.get("exposure_us"),
                                     keep_frac=data.get("keep_frac"),
                                     max_hold=data.get("max_hold"),
                                     deflicker=data.get("deflicker"),
                                     transform=data.get("transform"))
                    time.sleep(0.1)          # let the grab thread pick it up
                    self._json(camera.stats())
                elif url.path == "/projector/image":
                    fit = q.get("fit", projector.fit)
                    if fit not in FITS:
                        return self._json({"error": f"fit must be one of {FITS}"}, 400)
                    img = cv2.imdecode(np.frombuffer(self._body(), np.uint8),
                                       cv2.IMREAD_COLOR)
                    if img is None:
                        return self._json({"error": "could not decode image "
                                           "(PNG, JPG, BMP, TIFF)"}, 400)
                    projector.source, projector.image_name = img, q.get("name", "image")
                    projector.fit = fit
                    self._projector_reply(projector.do("show"))
                elif url.path == "/projector/fit":
                    fit = json.loads(self._body() or b"{}").get("fit")
                    if fit not in FITS:
                        return self._json({"error": f"fit must be one of {FITS}"}, 400)
                    projector.fit = fit
                    if projector.source is not None and projector.state == "image":
                        return self._projector_reply(projector.do("show"))
                    self._json(projector.status())
                elif url.path == "/camera/calibrate":
                    try:
                        camera.calibrate(projector)
                    except RuntimeError as exc:
                        return self._json({**camera.stats(), "error": str(exc)}, 409)
                    self._json(camera.stats())
                elif url.path == "/camera/auto_exposure":
                    try:
                        e, p99 = camera.auto_exposure()
                    except RuntimeError as exc:
                        return self._json({**camera.stats(), "error": str(exc)}, 409)
                    time.sleep(0.3)                  # applied before the stats
                    self._json({**camera.stats(), "exposure_us": e, "p99": round(p99, 1)})
                elif url.path == "/camera/fit_transform":
                    try:
                        camera.fit_transform(projector)
                    except RuntimeError as exc:
                        return self._json({**camera.stats(), "error": str(exc)}, 409)
                    self._json(camera.stats())
                elif url.path == "/projector/pattern":
                    # Made or stored at the projector's resolution, so every
                    # fit shows it 1:1; the fit selection is left as it is.
                    data = json.loads(self._body() or b"{}")
                    if "checker" in data:
                        n = int(data["checker"])
                        if n < 1:
                            raise ValueError("checker square size must be >= 1 px")
                        m = projector.monitor
                        w, h = (m.width, m.height) if m else (3840, 2160)
                        grey = 255 - make_pattern(f"checker{n}", w, h)
                        projector.source = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
                        projector.image_name = f"checker {n} px"
                    else:
                        name = data.get("name", "")
                        projector.source = load_pattern(name)
                        projector.image_name = os.path.basename(name)
                    self._projector_reply(projector.do("show"))
                elif url.path == "/scan/start":
                    try:
                        scan.start(json.loads(self._body() or b"{}"))
                    except RuntimeError as exc:
                        return self._json({**scan.status(), "error": str(exc)}, 409)
                    self._json(scan.status())
                elif url.path == "/scan/stop":
                    scan.stop()
                    self._json(scan.status())
                elif url.path in ("/track/start", "/track/stop", "/track/zero"):
                    if url.path == "/track/start" and scan.status().get("active"):
                        return self._json({**tracking.status(), "error": "a scan is running"}, 409)
                    try:
                        getattr(tracking, url.path.rsplit("/", 1)[1])()
                    except RuntimeError as exc:
                        return self._json({**tracking.status(), "error": str(exc)}, 409)
                    self._json(tracking.status())
                elif url.path == "/focus":
                    data = json.loads(self._body() or b"{}")
                    if not isinstance(data.get("z"), (int, float)):
                        return self._json({"error": "need {\"z\": number}"}, 400)
                    self._json(save_focus(data["z"], data.get("source", "UI")))
                elif url.path == "/projector/remember":
                    projector.remember()
                    self._json(projector.status())
                elif url.path == "/projector/restore":
                    self._projector_reply(projector.restore())
                elif url.path == "/shutdown":
                    self._json({"ok": True})
                    shutdown()
                elif url.path in ("/projector/blank", "/projector/close"):
                    self._projector_reply(projector.do(url.path.rsplit("/", 1)[1]))
                else:
                    self._send(404, b"not found", "text/plain")
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": str(exc)}, 400)

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--exposure", type=float, default=400.0, help="us")
    p.add_argument("--keep-frac", type=float, default=0.8)
    p.add_argument("--max-hold", type=float, default=0.5, help="s")
    p.add_argument("--monitor", default=None, help="projector, e.g. DISPLAY5")
    p.add_argument("--no-extend", action="store_true",
                   help="don't extend the desktop to a projector left out of it")
    args = p.parse_args()

    stage = Stage()
    camera = Camera(args.exposure, args.keep_frac, args.max_hold)
    projector = Projector(args.monitor, auto_extend=not args.no_extend)
    mirror = Mirror(projector)
    tracking = Tracking(stage, camera, projector)
    scan = Scan(stage, camera, projector, mirror, tracking, encode, load_pattern,
                FIT_EXPOSURE_US, steps_per_mm())
    def shutdown():
        # serve_forever() returns once shutdown() is called from another
        # thread; then the cleanup below runs, as for Ctrl+C.
        print("shutdown requested (UI window closed)", flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    # Take the port first, and exclusively: on Windows the default
    # SO_REUSEADDR lets a second server bind the same port (seen 2026-10-07:
    # two servers answered on 8765, one of them unable to get COM4 or the
    # camera). A second copy now stops here, before touching any hardware.
    try:
        server = UIServer(("127.0.0.1", args.port),
                          make_handler(stage, camera, projector, mirror,
                                       shutdown, tracking, scan))
    except OSError as exc:
        print(f"port {args.port} is already in use ({exc}).\n"
              f"  Is the UI already running? Close its window (or Ctrl+C in its "
              f"terminal) first.", flush=True)
        sys.exit(1)
    for t in (stage, camera, projector, mirror):
        t.start()
    print(f"UI on http://localhost:{args.port}   Ctrl+C to stop", flush=True)
    if projector.monitor is None:
        print(f"projector display not found ({projector.error}).\n"
              f"  Stage and camera work without it. Is the projector on? The"
              f" server checks again every {projector.RETRY_S:.0f} s and extends"
              f" the desktop to it once it is plugged in.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop_flag.set()
        stage.disconnect("server stopping")
        projector.do("close", timeout=2.0)
        server.server_close()


if __name__ == "__main__":
    main()
