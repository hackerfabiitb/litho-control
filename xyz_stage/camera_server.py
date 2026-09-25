#!/usr/bin/env python3
"""
Basler camera MJPEG server for XYZ Stage Controller.

Requirements:
    pip install pypylon numpy
    pip install opencv-python        # preferred for JPEG encoding
    # OR: pip install Pillow         # fallback if opencv not available

Usage:
    python camera_server.py
    Then open stage_controller.html in Chrome/Edge and click "Connect Camera".
"""
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765

# ── Optional imports ─────────────────────────────────────────────────
try:
    from pypylon import pylon
    PYLON_OK = True
except ImportError:
    PYLON_OK = False
    print("ERROR: pypylon not found.  Run:  pip install pypylon")

try:
    import cv2
    ENCODE = "cv2"
except ImportError:
    try:
        from PIL import Image as PILImage
        ENCODE = "pil"
    except ImportError:
        ENCODE = None
        print("ERROR: neither opencv-python nor Pillow found.")
        print("       Run:  pip install opencv-python")

# ── Shared state ──────────────────────────────────────────────────────
_camera       = None
_cam_lock     = threading.Lock()
_latest_frame = None          # raw JPEG bytes of most recent grab
_frame_lock   = threading.Lock()
_new_frame    = threading.Event()

config = {
    "connected":   False,
    "error":       None,
    "exposure_us": 10000.0,   # µs
    "fps":         10.0,
}

# ── JPEG encoding helpers ─────────────────────────────────────────────

def _encode_jpeg(bgr_array):
    """Encode a BGR numpy array to JPEG bytes."""
    if ENCODE == "cv2":
        ok, buf = cv2.imencode(".jpg", bgr_array, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None
    if ENCODE == "pil":
        rgb = bgr_array[:, :, ::-1]
        img = PILImage.fromarray(rgb)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    return None


def _focus_score(jpeg_bytes):
    """Laplacian variance — higher = sharper image."""
    if ENCODE == "cv2":
        import numpy as np
        arr = np.frombuffer(jpeg_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    if ENCODE == "pil":
        import numpy as np
        img = PILImage.open(io.BytesIO(jpeg_bytes)).convert("L")
        arr = np.array(img, dtype=float)
        lap = (arr[1:-1, 1:-1] * 4
               - arr[:-2, 1:-1] - arr[2:, 1:-1]
               - arr[1:-1, :-2] - arr[1:-1, 2:])
        return float(lap.var())
    return 0.0


def _placeholder_jpeg():
    """Return a small grey JPEG used when the camera is unavailable."""
    import numpy as np
    h, w = 480, 640
    img = np.full((h, w, 3), 30, dtype="uint8")
    # Draw a simple cross-hair in the centre
    cy, cx = h // 2, w // 2
    img[cy - 1 : cy + 2, cx - 60 : cx + 60] = 70
    img[cy - 60 : cy + 60, cx - 1 : cx + 2] = 70
    return _encode_jpeg(img)


# ── Camera acquisition thread ─────────────────────────────────────────

def _camera_thread():
    global _camera, _latest_frame

    if not PYLON_OK:
        config["error"] = "pypylon not installed — run: pip install pypylon"
        return
    if ENCODE is None:
        config["error"] = "No JPEG encoder — run: pip install opencv-python"
        return

    import numpy as np

    while True:                      # outer loop: reconnect on disconnect
        try:
            tl = pylon.TlFactory.GetInstance()
            devices = tl.EnumerateDevices()
            if not devices:
                config["error"] = "No Basler camera detected"
                config["connected"] = False
                time.sleep(3)
                continue

            with _cam_lock:
                _camera = pylon.InstantCamera(tl.CreateFirstDevice())
                _camera.Open()
                _apply_config_locked()
                config["connected"] = True
                config["error"] = None

            converter = pylon.ImageFormatConverter()
            converter.OutputPixelFormat = pylon.PixelType_BGR8packed
            converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

            _camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

            while _camera.IsGrabbing():
                try:
                    result = _camera.RetrieveResult(
                        5000, pylon.TimeoutHandling_ThrowException
                    )
                except pylon.TimeoutException:
                    continue

                if result.GrabSucceeded():
                    img = converter.Convert(result)
                    arr = img.GetArray()
                    jpeg = _encode_jpeg(arr)
                    if jpeg:
                        with _frame_lock:
                            _latest_frame = jpeg
                        _new_frame.set()
                        _new_frame.clear()
                result.Release()

        except Exception as exc:
            config["connected"] = False
            config["error"] = str(exc)
            print(f"[camera] {exc}")
            with _cam_lock:
                if _camera and _camera.IsOpen():
                    try:
                        _camera.StopGrabbing()
                        _camera.Close()
                    except Exception:
                        pass
                _camera = None
            time.sleep(3)


def _apply_config_locked():
    """Apply exposure + fps to the already-open camera (call with _cam_lock held)."""
    cam = _camera
    if cam is None or not cam.IsOpen():
        return
    # Exposure: some older cameras use ExposureTimeAbs
    for attr in ("ExposureTime", "ExposureTimeAbs"):
        try:
            getattr(cam, attr).Value = float(config["exposure_us"])
            break
        except Exception:
            pass
    # Frame rate
    try:
        cam.AcquisitionFrameRateEnable.Value = True
        cam.AcquisitionFrameRate.Value = float(config["fps"])
    except Exception:
        pass


def apply_config():
    with _cam_lock:
        _apply_config_locked()


# ── HTTP handler ───────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress per-request log noise

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            if self.path.startswith("/stream"):
                self._serve_mjpeg()
            elif self.path in ("/config", "/ping"):
                body = json.dumps(config).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/focus_score":
                self._serve_focus_score()
            else:
                self.send_response(404)
                self.end_headers()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _serve_focus_score(self):
        with _frame_lock:
            frame = _latest_frame
        if frame is None:
            body = json.dumps({"score": 0.0, "error": "no frame"}).encode()
        else:
            score = _focus_score(frame)
            body = json.dumps({"score": score}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                if "exposure_us" in data:
                    config["exposure_us"] = float(data["exposure_us"])
                if "fps" in data:
                    config["fps"] = float(data["fps"])
                apply_config()
                resp = json.dumps(config).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(resp)
            except Exception as exc:
                self.send_response(400)
                self._cors()
                self.end_headers()
                self.wfile.write(str(exc).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )
        self.send_header("Cache-Control", "no-cache, no-store")
        self._cors()
        self.end_headers()

        placeholder = _placeholder_jpeg()

        try:
            while True:
                with _frame_lock:
                    frame = _latest_frame

                if frame is None:
                    frame = placeholder
                    wait = 0.5
                else:
                    wait = 0.05          # max ~20 fps poll cadence

                try:
                    self.wfile.write(
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break

                _new_frame.wait(timeout=wait)

        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Basler camera server  →  http://localhost:{PORT}")

    if PYLON_OK and ENCODE:
        t = threading.Thread(target=_camera_thread, daemon=True)
        t.start()
        print("Camera acquisition thread started.")
    else:
        print("Running in placeholder mode (missing dependencies).")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"Serving — open stage_controller.html and click 'Connect Camera'.")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
