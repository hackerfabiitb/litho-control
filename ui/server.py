"""Control UI server: stage UI, projector display and deflickered camera.

    python ui/server.py                 # then open http://localhost:8765
    .\\ui\\start.ps1                     # starts it and opens the browser

One process owns the camera and the projector window, and serves the UI page
(ui/index.html) over http://localhost, where Chrome/Edge still allow Web
Serial for the stage Arduino.

GET  /                       the UI
GET  /stream                 deflickered camera feed, MJPEG (full resolution)
GET  /config                 camera settings and live stats, JSON
POST /config                 {"exposure_us", "keep_frac", "max_hold"} (any subset)
GET  /focus_score            Laplacian variance of the latest shown camera frame
GET  /projector/stream       what the projector displays, captured from its
                             screen, MJPEG — so it also shows anything else
                             that ends up on the projector
GET  /projector/status       JSON
POST /projector/image?fit=native|contain|cover|stretch&name=...
                             body: an image file; shown full-screen on the
                             projector at its native resolution
POST /projector/fit          {"fit": ...} re-fit the current image
POST /projector/blank        show black (window stays up)
POST /projector/close        close the window; the projector shows the desktop

The camera runs the same keep rule as camera\\run.ps1 (camera/deflicker.py):
full sensor, 400 us, keep frames >= 0.8 x the recent top, never hold a frame
longer than 0.5 s. While the server runs, no other program can open the camera.
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "camera"))
sys.path.insert(0, os.path.join(ROOT, "projector"))

from deflicker import KeepFilter  # noqa: E402
from display import PatternWindow, find_projector  # noqa: E402  (sets DPI awareness)
from pylon_utils import _try_set, frame_score, grab_frames, open_camera  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FITS = ("native", "contain", "cover", "stretch")


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
        # appended by the grab thread, only read elsewhere
        self.in_times, self.shown_times = deque(maxlen=400), deque(maxlen=400)
        self.stop_flag = threading.Event()

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
        if "keep_frac" in pending:
            self.filter.keep_frac = float(pending["keep_frac"])
        if "max_hold" in pending:
            self.filter.max_hold = float(pending["max_hold"])

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
        }

    def run(self):
        from pypylon import pylon
        while not self.stop_flag.is_set():
            cam = None
            try:
                cam = open_camera(exposure_us=self.exposure_us, full=True,
                                  verbose=False)
                self.connected, self.error = True, None
                for _, frame, _ in grab_frames(cam, pylon.GrabStrategy_LatestImageOnly):
                    if self.stop_flag.is_set():
                        break
                    self._apply_pending(cam)
                    now = time.perf_counter()
                    self.in_times.append(now)
                    self.mean = frame_score(frame)[0]
                    show, _ = self.filter.decide(self.mean, now)
                    if show:
                        self.shown_times.append(now)
                        self.last_frame = frame
                        if self.stream.clients:
                            self.stream.put(encode(frame))
            except Exception as exc:         # busy camera, unplugged, ...
                self.connected = False
                self.error = str(exc).splitlines()[0][:200]
                print(f"[camera] {self.error}", file=sys.stderr)
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
    pumped by one thread, so every change goes through a queue."""

    def __init__(self, monitor):
        super().__init__(daemon=True)
        self.monitor = monitor
        self.commands = queue.Queue()
        self.source = None
        self.image_name = None
        self.fit = "native"
        self.state = "closed"                # closed | blank | image

    def status(self):
        m = self.monitor
        return {"monitor": m.name, "width": m.width, "height": m.height,
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
                continue
            try:
                if cmd == "close":
                    if win is not None:
                        win.__exit__(None, None, None)
                        win = None
                    self.state = "closed"
                    continue
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


class Mirror(threading.Thread):
    """Screen capture of the projector's display, while anyone is watching."""

    def __init__(self, monitor, width=1280, fps=5.0):
        super().__init__(daemon=True)
        self.monitor = monitor
        self.width = width
        self.period = 1.0 / fps
        self.stream = Latest()

    def run(self):
        import mss
        m = self.monitor
        rect = {"left": m.x, "top": m.y, "width": m.width, "height": m.height}
        height = round(self.width * m.height / m.width)
        with mss.MSS() as grabber:
            while True:
                if not self.stream.clients:
                    time.sleep(0.2)
                    continue
                t0 = time.perf_counter()
                shot = np.asarray(grabber.grab(rect))[:, :, :3]
                small = cv2.resize(shot, (self.width, height), interpolation=cv2.INTER_AREA)
                self.stream.put(encode(small))
                time.sleep(max(0.0, self.period - (time.perf_counter() - t0)))


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def make_handler(camera, projector, mirror):
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

        def do_GET(self):
            path = urlparse(self.path).path
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
                if url.path == "/config":
                    data = json.loads(self._body() or b"{}")
                    camera.configure(exposure_us=data.get("exposure_us"),
                                     keep_frac=data.get("keep_frac"),
                                     max_hold=data.get("max_hold"))
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
                    self._json(projector.do("show"))
                elif url.path == "/projector/fit":
                    fit = json.loads(self._body() or b"{}").get("fit")
                    if fit not in FITS:
                        return self._json({"error": f"fit must be one of {FITS}"}, 400)
                    projector.fit = fit
                    if projector.source is not None and projector.state == "image":
                        return self._json(projector.do("show"))
                    self._json(projector.status())
                elif url.path in ("/projector/blank", "/projector/close"):
                    self._json(projector.do(url.path.rsplit("/", 1)[1]))
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
    args = p.parse_args()

    monitor = find_projector(args.monitor)
    camera = Camera(args.exposure, args.keep_frac, args.max_hold)
    projector = Projector(monitor)
    mirror = Mirror(monitor)
    for t in (camera, projector, mirror):
        t.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(camera, projector, mirror))
    server.daemon_threads = True
    print(f"UI on http://localhost:{args.port}   projector {monitor.name} "
          f"{monitor.width}x{monitor.height}   Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop_flag.set()
        projector.do("close", timeout=2.0)
        server.server_close()


if __name__ == "__main__":
    main()
