"""Live-reload preview: watch the source files, re-run a generation script whenever they
change, and show the resulting PNG in a window that refreshes automatically.

Usage:
    python scripts/live_reload.py [script] [output_png]

Defaults:
    script      = res_test_v01.py   (resolved inside scripts/)
    output_png  = out/res_test_v01_white.png

Edit a builder call, save, and the window updates. Errors in the script are shown in the
window title and printed; the watcher keeps running. The preview scales to fit the window
(aspect-preserving, LANCZOS interpolation) -- resize the window to zoom.

Stdlib + Pillow only (no watchdog). Uses mtime polling.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import ROOT, OUT

import subprocess
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

POLL_MS = 400
INIT_GEOMETRY = "1280x760"  # starting window size; resize freely afterwards
RESIZE_DEBOUNCE_MS = 60  # coalesce rapid resize events while dragging

SCRIPT = (
    Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "scripts" / "res_test_v01.py"
)
if not SCRIPT.is_absolute():
    SCRIPT = (
        (ROOT / "scripts" / SCRIPT).resolve()
        if not SCRIPT.exists()
        else SCRIPT.resolve()
    )
OUT_PNG = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT / "res_test_v01_white.png"


def watched_files():
    files = list((ROOT / "lib").glob("*.py"))
    files.append(SCRIPT)
    return files


def latest_source_mtime():
    return max((f.stat().st_mtime for f in watched_files() if f.exists()), default=0.0)


def run_script():
    """Run the generation script in a fresh subprocess. Returns (ok, message).

    The script's stdout/stderr are passed through to our own streams so its print()
    summary (sizes, diagonal, feature area, ...) shows up live in the terminal.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
        sys.stdout.flush()
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        sys.stderr.flush()
    if proc.returncode == 0:
        return True, "ok"
    msg = (proc.stderr or proc.stdout).strip().splitlines()
    return False, (msg[-1] if msg else "failed")


class Viewer:
    def __init__(self, root):
        self.root = root
        self.label = tk.Label(root, bg="#202020")
        self.label.pack(fill="both", expand=True)
        self._tk_img = None
        self._full_img = None  # full-resolution PNG, scaled on demand to fit the window
        self._rendered_size = None  # last (box_w, box_h) we rendered at
        self._resize_job = None
        self._src_mtime = -1.0
        self._png_mtime = -1.0
        self.label.bind("<Configure>", self.on_resize)
        self.tick()

    def on_resize(self, event):
        # coalesce the flood of Configure events during a drag into one render
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(
            RESIZE_DEBOUNCE_MS, lambda w=event.width, h=event.height: self.render(w, h)
        )

    def render(self, box_w=None, box_h=None):
        """Scale the full-res image to fit within (box_w, box_h), preserving aspect."""
        self._resize_job = None
        if self._full_img is None:
            return
        if box_w is None:
            box_w, box_h = self.label.winfo_width(), self.label.winfo_height()
        if box_w < 2 or box_h < 2:
            return
        if (box_w, box_h) == self._rendered_size:
            return
        self._rendered_size = (box_w, box_h)
        w, h = self._full_img.size
        scale = min(box_w / w, box_h / h)  # fit inside the window (up- or down-scale)
        new = (max(1, round(w * scale)), max(1, round(h * scale)))
        # LANCZOS interpolates instead of dropping pixels, so heavily-downscaled thin
        # features (single-px lines, small circles) stay visible instead of aliasing away
        img = self._full_img.resize(new, Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(img)
        self.label.config(image=self._tk_img)

    def load_png(self):
        try:
            img = Image.open(OUT_PNG)
            img.load()
        except (FileNotFoundError, OSError):
            return
        self._full_img = img
        self._rendered_size = (
            None  # force a re-fit even if the window size is unchanged
        )
        self.render()

    def tick(self):
        src = latest_source_mtime()
        if src > self._src_mtime:
            self._src_mtime = src
            self.root.title(f"live-reload: running {SCRIPT.name} ...")
            self.root.update_idletasks()
            ok, message = run_script()
            stamp = time.strftime("%H:%M:%S")
            if ok:
                self.root.title(
                    f"live-reload: {SCRIPT.name} -> {OUT_PNG.name}  ({stamp})"
                )
            else:
                self.root.title(f"live-reload: ERROR {message}  ({stamp})")
                print(
                    f"[{stamp}] error running {SCRIPT.name}: {message}", file=sys.stderr
                )

        # refresh the view if the PNG changed on disk
        if OUT_PNG.exists():
            m = OUT_PNG.stat().st_mtime
            if m > self._png_mtime:
                self._png_mtime = m
                self.load_png()

        self.root.after(POLL_MS, self.tick)


def main():
    root = tk.Tk()
    root.title("live-reload")
    root.geometry(INIT_GEOMETRY)
    root.configure(bg="#202020")
    Viewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
