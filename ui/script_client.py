"""Run a test script through the UI server, in script mode.

The server owns the camera, the stage's COM port and the projector, so a
script that opened them itself would have to stop the UI, and nobody could
watch. Through the server instead, the UI stays open: its controls lock
(yellow), the Script pane shows the script's name and progress, the previews
show what is projected and what the camera sees, the position fields follow
the stage, and Stop ends the script.

    import sys; sys.path.insert(0, "ui")
    from script_client import Session

    with Session("Y step test") as s:              # needs ui\\start.ps1 running
        s.say("projecting white")
        s.white()
        seq, img = s.frame()                        # deflickered, fitted view
        s.say("Y +10")
        s.move(y=s.pos()[1] + 10)                   # absolute move, waits for DONE
        seq, img2 = s.frame(after=seq)
        s.check()                                   # raises Stopped if Stop was pressed

Every call checks Stop first; a heartbeat thread keeps the script alive
(the server ends a script that is silent for 15 s). Leaving the `with`
block ends it; an exception ends it as failed.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import cv2
import numpy as np

BASE = "http://127.0.0.1:8765"


class Stopped(Exception):
    """Stop was pressed in the UI's Script pane."""


class ServerError(RuntimeError):
    pass


def _request(method, url, body=None, timeout=30.0, raw=False):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return (payload, dict(r.headers)) if raw else json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error") or str(exc)
        except ValueError:
            msg = str(exc)
        raise ServerError(f"{method} {url}: {msg}") from None
    except urllib.error.URLError as exc:
        raise ServerError(f"the UI server isn't answering at {url} ({exc.reason}); "
                          f"start it with .\\ui\\start.ps1") from None


class Session:
    """One script: `with Session(name) as s:` ... (see the module docstring)."""

    HEARTBEAT_S = 4.0

    def __init__(self, name, source="Claude (test script)", base=BASE):
        self.name, self.source, self.base = name, source, base
        self.token = None
        self.stop_requested = False
        self._detail = ""
        self._quit = threading.Event()
        self._thread = None

    # ── lifetime ────────────────────────────────────────────────────────────
    def __enter__(self):
        st = _request("POST", self.base + "/script/start", {"name": self.name, "source": self.source})
        self.token = st["token"]
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, kind, exc, tb):
        self._quit.set()
        if kind is None:
            outcome = "stopped" if self.stop_requested else "done"
        elif kind is Stopped:
            outcome = "stopped"
        else:
            outcome = f"failed: {kind.__name__}: {exc}"
        try:
            _request("POST", self.base + "/script/end", {"token": self.token, "outcome": outcome})
        except ServerError:
            pass
        return kind is Stopped                       # Stop is a normal way out

    def _beat(self):
        while not self._quit.wait(self.HEARTBEAT_S):
            try:
                self._update(None)
            except ServerError:
                pass

    def _update(self, detail):
        st = _request("POST", self.base + "/script/update", {"token": self.token, "detail": detail},
                      timeout=5.0)
        if st.get("stop_requested"):
            self.stop_requested = True
        return st

    # ── progress and Stop ───────────────────────────────────────────────────
    def say(self, detail):
        """Show `detail` in the Script pane (and check Stop)."""
        self._detail = str(detail)
        self._update(self._detail)
        print(f"[{self.name}] {detail}", flush=True)
        self.check()

    def check(self):
        """Raise Stopped if Stop was pressed."""
        if self.stop_requested:
            raise Stopped(self.name)

    def _get(self, path, **kw):
        self.check()
        return _request("GET", self.base + path, **kw)

    def _post(self, path, body=None, **kw):
        self.check()
        return _request("POST", self.base + path, body if body is not None else {}, **kw)

    # ── stage ───────────────────────────────────────────────────────────────
    def send(self, line, wait=None, timeout=10.0):
        """One firmware command; with wait="DONE" (or another reply's start),
        returns that reply line."""
        r = self._post("/stage/send", {"line": line, "wait": wait, "timeout": timeout},
                       timeout=timeout + 5)
        return r.get("reply")

    def pos(self):
        """[x, y, z] in steps (the firmware's count)."""
        return self._get("/stage/state")["pos"]

    def move(self, x=None, y=None, z=None, timeout=60.0):
        """Absolute move (A x y z; unspecified axes stay), waits for DONE."""
        p = self.pos()
        target = [p[0] if x is None else int(x), p[1] if y is None else int(y),
                  p[2] if z is None else int(z)]
        self.send(f"A {target[0]} {target[1]} {target[2]}", wait="DONE", timeout=timeout)
        return target

    def speed(self, delay_us):
        """Step delay in µs, confirmed by the firmware."""
        return self.send(f"S {int(delay_us)}", wait="SPEED", timeout=5.0)

    # ── projector ───────────────────────────────────────────────────────────
    def pattern(self, name):
        """Show an image from patterns/ (by file name)."""
        return self._post("/projector/pattern", {"name": name})

    def white(self):
        return self.pattern("white_4K.jpg")

    def checker(self, px):
        return self._post("/projector/pattern", {"checker": int(px)})

    def black(self):
        return self._post("/projector/blank")

    def image(self, img, name="test image", fit="native"):
        """Project a numpy image (BGR or grey)."""
        ok, buf = cv2.imencode(".png", img)
        self.check()
        req = urllib.request.Request(f"{self.base}/projector/image?fit={fit}&name={urllib.parse.quote(name)}",
                                     data=buf.tobytes(), method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def remember_projector(self):
        return self._post("/projector/remember")

    def restore_projector(self):
        return _request("POST", self.base + "/projector/restore", {})   # also after Stop

    # ── camera ──────────────────────────────────────────────────────────────
    def frame(self, after=-1, timeout=3.0):
        """(seq, image): the newest shown camera frame (deflickered, the fitted
        view if it's on); after=seq waits for a newer one."""
        self.check()
        body, headers = _request("GET", f"{self.base}/camera/frame?after={after}&timeout={timeout}",
                                 timeout=timeout + 5, raw=True)
        img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_UNCHANGED)
        return int(headers.get("X-Frame-Seq", -1)), img

    def camera(self, **settings):
        """Camera settings (exposure_us, keep_frac, max_hold, deflicker,
        transform); returns its stats."""
        return self._post("/config", settings)

    def camera_stats(self):
        return self._get("/config")

    def calibrate_deflicker(self):
        """Calibrate deflicker as a step of this script (Stop reaches it)."""
        return self._post(f"/camera/calibrate?token={self.token}", timeout=60)

    def fit_transform(self):
        """Fit transform as a step of this script."""
        return self._post(f"/camera/fit_transform?token={self.token}", timeout=60)
