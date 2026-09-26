"""Put full-screen test patterns on the projector over HDMI.

The projector is an ordinary extended Windows display, so a borderless OpenCV
window moved onto its monitor and made full-screen is enough to drive every
input pixel. The process is made per-monitor DPI aware first: the projector
runs at 150 % scaling, and without this Windows reports it as 2560x1440 and
upscales whatever we draw.
"""

import ctypes
import time
from ctypes import wintypes
from typing import NamedTuple

import cv2
import numpy as np

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
except OSError:
    pass


class Monitor(NamedTuple):
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool


def list_monitors():
    """All attached monitors in physical pixels."""
    user32 = ctypes.windll.user32

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    found = []

    def callback(handle, _hdc, _rect, _data):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        r = info.rcMonitor
        found.append(Monitor(info.szDevice, r.left, r.top, r.right - r.left,
                             r.bottom - r.top, bool(info.dwFlags & 1)))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.EnumDisplayMonitors(None, None, proc(callback), 0)
    return found


def find_projector(name=None):
    """The projector's monitor: `name` (e.g. DISPLAY5) if given, else the one
    non-primary monitor. The DLP471TPEVM shows up as a 3840x2160 display."""
    monitors = list_monitors()
    if name:
        match = [m for m in monitors if m.name.upper().endswith(name.upper())]
        if not match:
            raise RuntimeError(f"no monitor {name}; have "
                               + ", ".join(m.name for m in monitors))
        return match[0]
    others = [m for m in monitors if not m.primary]
    if len(others) != 1:
        raise RuntimeError("expected exactly one non-primary monitor (the "
                           "projector), found: "
                           + ", ".join(f"{m.name} {m.width}x{m.height}"
                                       for m in monitors)
                           + " — pass --monitor")
    return others[0]


class _DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD)]


def refresh_rate(monitor):
    """The refresh rate Windows is sending to `monitor`, in Hz (an integer:
    Windows reports 59.94 Hz modes as 59). The projector runs at 60."""
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(mode)
    ENUM_CURRENT_SETTINGS = -1
    if not ctypes.windll.user32.EnumDisplaySettingsW(monitor.name,
                                                     ENUM_CURRENT_SETTINGS,
                                                     ctypes.byref(mode)):
        raise RuntimeError(f"could not read display mode of {monitor.name}")
    return int(mode.dmDisplayFrequency)


# --------------------------------------------------------------------------- #
# patterns — all in input pixels (3840x2160). The DMD has 1920x1080 mirrors and
# uses XPR shifting to show 4K, so 2 input px ~ 1 mirror in each axis.
# --------------------------------------------------------------------------- #

PATTERN_HELP = """black, white, grayN (N=0..255), grid (labelled, lines every 240 px),
vbarsN / hbarsN (N-px alternating columns / rows), checkerN, hramp, vramp,
linesN / gapsN (2-px white lines on black / black lines on white, every N px),
dotsN (2x2 white dots on black, every N px in x and y),
marker (one white 256x256 square at x=1024, y=512 — used by calibrate)"""


def make_pattern(name, width, height):
    img = np.zeros((height, width), np.uint8)
    if name == "black":
        pass
    elif name == "white":
        img[:] = 255
    elif name.startswith("gray"):
        img[:] = int(name[4:])
    elif name == "grid":
        for x in range(0, width, 240):
            img[:, x:x + (12 if x % 960 == 0 else 4)] = 255
        for y in range(0, height, 240):
            img[y:y + 4, :] = 255
        for x in range(0, width, 480):
            for y in (200, 1100):
                cv2.putText(img, str(x), (x + 20, y), cv2.FONT_HERSHEY_SIMPLEX,
                            4, 255, 10)
    elif name.startswith("vbars"):
        k = int(name[5:])
        img[:] = (((np.arange(width) // k) % 2) * 255)[None, :]
    elif name.startswith("hbars"):
        k = int(name[5:])
        img[:] = (((np.arange(height) // k) % 2) * 255)[:, None]
    elif name.startswith("checker"):
        k = int(name[7:])
        yy, xx = np.mgrid[0:height, 0:width]
        img[:] = ((yy // k + xx // k) % 2 * 255).astype(np.uint8)
    elif name.startswith("lines"):
        img[:, ::int(name[5:])] = 255
        img[:, 1::int(name[5:])] = 255
    elif name.startswith("gaps"):
        img[:] = 255
        img[:, ::int(name[4:])] = 0
        img[:, 1::int(name[4:])] = 0
    elif name.startswith("dots"):
        k = int(name[4:])
        for dy in (0, 1):
            for dx in (0, 1):
                img[dy::k, dx::k] = 255
    elif name == "hramp":
        img[:] = np.linspace(0, 255, width).astype(np.uint8)[None, :]
    elif name == "vramp":
        img[:] = np.linspace(0, 255, height).astype(np.uint8)[:, None]
    elif name == "marker":
        img[512:768, 1024:1280] = 255
    else:
        raise ValueError(f"unknown pattern {name!r}; known: {PATTERN_HELP}")
    return img


class PatternWindow:
    """A full-screen window on the projector. Use as a context manager."""

    def __init__(self, monitor, title="projector-pattern"):
        self.monitor = monitor
        self.title = title

    def __enter__(self):
        m = self.monitor
        cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
        cv2.moveWindow(self.title, m.x, m.y)
        cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN)
        self.show(make_pattern("black", m.width, m.height), settle=0.3)
        rect = cv2.getWindowImageRect(self.title)
        if (rect[2], rect[3]) != (m.width, m.height):
            print(f"  ! pattern window is {rect[2]}x{rect[3]}, monitor is "
                  f"{m.width}x{m.height} — patterns will be rescaled")
        # Keep the cursor off the projected image.
        ctypes.windll.user32.SetCursorPos(100, 100)
        return self

    def pattern(self, name):
        return make_pattern(name, self.monitor.width, self.monitor.height)

    def show(self, img, settle=0.8):
        """Display `img` and pump the event loop for `settle` seconds so the
        frame has actually reached the DMD before the caller grabs."""
        cv2.imshow(self.title, img)
        end = time.time() + settle
        while time.time() < end:
            cv2.waitKey(20)

    def __exit__(self, *exc):
        cv2.destroyWindow(self.title)
        cv2.waitKey(1)
