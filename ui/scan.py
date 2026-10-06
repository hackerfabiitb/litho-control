"""Scan: image the wafer field by field in a spiral and stitch the tiles.

Started from the UI (POST /scan/start). From the stage's current position:

 1. projects white and picks an unclipped exposure (as optical tracking);
 2. calibrates: frames at the start, after +CAL_STEPS in X and in Y (each
    moved back) give, by phase correlation, how far the wafer's image moves
    per step in camera px -- rotation and real scale included. The three
    frames also seed the flat field (camera/stitch.py);
 3. walks a square spiral of fields at PITCH (default 0.7) of the field
    size, so neighbours overlap by ~30 %; positions outside the soft limits
    are skipped, and the scan ends when a whole ring is outside them;
 4. at each field: moves (at the firmware's current step delay), autofocuses
    (the UI's range / step / settle; sharpness = Laplacian variance of the
    fitted view, as the UI's autofocus), grabs the median of a few kept
    frames, places it from the calibration and refines that by correlating
    the overlap with what's stitched (camera/stitch.register);
 5. shows the stitched preview in the UI's projector preview pane;
 6. on Stop: finishes the current field, then saves to scans/<date>/:
    mosaic.png (full resolution, flat-field corrected, feathered seams),
    preview.jpg, every tile, and scan.json (positions in steps, Z, offsets,
    registration quality, the calibration). Then puts back the projector and
    the exposure. The stage stays where the scan ended.
"""

import datetime
import json
import os
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "camera"))
sys.path.insert(0, os.path.join(ROOT, "projector"))

import stitch  # noqa: E402
import tracker  # noqa: E402
from optics import FIELD_WIDTH_UM  # noqa: E402

SCANS = os.path.join(ROOT, "scans")
CAL_FILE = os.path.join(ROOT, "camera", "scan_calibration.json")   # last good one
CAL_STEPS = 40                 # backlash pre-load before measuring
CAL_STEP = 30                  # measured in 3 moves of this size
CAL_TOL = 0.12                 # the 3 moves and their total must agree this well
TILE_FRAMES = 5
MAX_MOSAIC_PX = 300e6          # above this the saved mosaic is made at half resolution
# Slowest step delay each axis needs to move the full distance, measured
# 2026-10-07 by imaging 40-150 step moves: X moved fully from 400 us (800
# used here for margin); Y moved only ~64 % at <= 2500 us and fully from
# 4000 us. Faster moves lose steps silently, and the tiles land in the wrong
# place. Z wasn't measured: it gets X's minimum as a precaution.
MIN_STEP_DELAY_US = {"X": 800, "Y": 4000, "Z": 800}   # Z: not measured, X's as a precaution


def shift_px(a, b):
    """Shift of b relative to a (camera px) by the tracker's correlation
    (ignores the zero-shift peak of the static pattern); (dx, dy, psr)."""
    h, w = int(a.shape[0] * tracker.SCALE), int(a.shape[1] * tracker.SCALE)
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    dx, dy, psr = tracker.correlate(tracker._prep(a, win), tracker._prep(b, win))
    return dx / tracker.SCALE, dy / tracker.SCALE, psr


class Scan:
    def __init__(self, stage, camera, projector, mirror, tracking, encode, load_pattern,
                 fit_exposure_us, steps_per_mm):
        self.stage, self.camera, self.projector = stage, camera, projector
        self.mirror, self.tracking = mirror, tracking
        self.encode, self.load_pattern = encode, load_pattern
        self.fit_exposure_us, self.spmm = fit_exposure_us, steps_per_mm
        self.thread = None
        self.stop_flag = threading.Event()
        self.state = {"active": False}
        self.lock = threading.Lock()

    # ── public ──────────────────────────────────────────────────────────────
    def start(self, params):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("a scan is already running")
        if self.tracking.active:
            raise RuntimeError("stop optical tracking first")
        cam = self.camera
        if not cam.connected:
            raise RuntimeError(f"camera not connected ({cam.error})")
        if not cam.transform or not cam.transform_on:
            raise RuntimeError("run Fit transform first (and View: fitted)")
        if self.stage.ser is None or self._pos() is None:
            raise RuntimeError("stage not connected")
        self.params = {"pitch": float(params.get("pitch", 0.7)),
                       "af_range": int(params.get("af_range", 500)),
                       "af_step": max(1, int(params.get("af_step", 50))),
                       "af_settle": max(50, int(params.get("af_settle", 300))) / 1000.0,
                       "autofocus": bool(params.get("autofocus", True)),
                       "step_delay": max(20, int(params.get("step_delay", 800)))}
        self._delay_sent = None
        if not 0.2 <= self.params["pitch"] <= 0.95:
            raise RuntimeError("pitch must be between 0.2 and 0.95 of the field")
        self.stop_flag.clear()
        self._set(active=True, phase="starting", tiles=0, error=None, saved=None)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.stop_flag.set()
            self._set(phase=self.state.get("phase", "") + " (stopping after this field)")

    def status(self):
        with self.lock:
            return dict(self.state)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def _pos(self):
        try:
            return np.array([int(v) for v in self.stage.cache.get("POS").split()[1:4]])
        except (AttributeError, ValueError):
            return None

    def _limits(self):
        try:
            v = [int(x) for x in self.stage.cache.get("LIMITS").split()[1:7]]
            return {"X": (v[0], v[1]), "Y": (v[2], v[3]), "Z": (v[4], v[5])}
        except (AttributeError, ValueError):
            return None

    def _inside(self, x, y, z, lim):
        if lim is None:
            return True
        ok = True
        for a, val in zip("XYZ", (x, y, z)):
            lo, hi = lim[a]
            if lo != hi:                                    # 0 0 = no limits on that axis
                ok &= lo <= val <= hi
        return ok

    def _clamped(self, x, y, z):
        """Where the firmware will really go: it clamps to the soft limits."""
        lim = self._limits()
        out = []
        for a, v in zip("XYZ", (int(x), int(y), int(z))):
            lo, hi = lim[a] if lim else (0, 0)
            out.append(min(hi, max(lo, v)) if lo != hi else v)
        return out

    def _wait_line(self, send, match, timeout):
        """Send `send` and wait for a firmware line for which match(line) is
        true; False on timeout."""
        got = threading.Event()

        def on_line(line):
            if match(line):
                got.set()
        self.stage.listeners.append(on_line)
        try:
            self.stage.send(send)
            return got.wait(timeout)
        finally:
            self.stage.listeners.remove(on_line)

    def _set_speed(self, delay):
        """S <delay>, confirmed by the firmware's SPEED reply (a lost S would
        leave Y at a speed where it skips steps)."""
        if delay == self._delay_sent:
            return
        for _ in range(3):
            if self._wait_line(f"S {delay}", lambda l: l == f"SPEED {delay}", 2.0):
                self._delay_sent = delay
                return
        raise RuntimeError(f"the stage didn't confirm the step delay {delay} us")

    def _move(self, x, y, z, timeout=None):
        """Move and wait until it's finished. Done on DONE, or on a POS line
        at the target (either can arrive garbled). A command to the Uno can
        also get lost (seen 2026-10-07: after a 1-step Z move was "not
        confirmed", P showed the stage hadn't moved), so: if nothing comes,
        ask with P, and if it isn't there, send the move again -- it's an
        absolute target, so resending is safe. Up to 3 times."""
        target = self._clamped(x, y, z)
        start = self._pos()
        steps = max(abs(t - c) for t, c in zip(target, start)) if start is not None else 10000
        # speed: the user's step delay, slowed where an axis would lose steps
        delay = self.params["step_delay"]
        for i, a in enumerate("XYZ"):
            if start is not None and target[i] != start[i]:
                delay = max(delay, MIN_STEP_DELAY_US.get(a, 0))
        self._set_speed(delay)
        timeout = timeout or 2.0 + steps * (1.5 * delay * 1e-6 + 1e-3)

        def at_target(line):
            if line == "DONE":
                return True
            if line.startswith("POS "):
                try:
                    return [int(v) for v in line.split()[1:4]] == target
                except ValueError:
                    return False
            return False

        def pos_is_target(line):
            return at_target(line) and line != "DONE"

        for attempt in range(3):
            if self._wait_line(f"A {target[0]} {target[1]} {target[2]}", at_target, timeout):
                break
            if self._wait_line("P", pos_is_target, 2.0):    # got there, reply lost
                break
            print(f"[scan] move to {target} not confirmed (attempt {attempt + 1}); "
                  f"stage at {self._pos()}, sending it again", flush=True)
        else:
            raise RuntimeError(f"stage didn't reach {target} after 3 attempts (at {self._pos()})")
        time.sleep(0.05)

    def _frames(self, n, settle):
        """The next n kept (fitted) frames after `settle` seconds."""
        time.sleep(settle)
        out, seq = [], self.camera.frame_seq
        t_end = time.time() + 10
        while len(out) < n and time.time() < t_end:
            if self.camera.frame_seq != seq:
                seq = self.camera.frame_seq
                out.append(self.camera.last_frame.copy())
            time.sleep(0.01)
        if not out:
            raise RuntimeError("no camera frames")
        return out

    def _tile(self, settle):
        return np.median(np.stack(self._frames(TILE_FRAMES, settle)), axis=0).astype(np.uint8)

    def _sharpness(self, settle):
        f = self._frames(1, settle)[0]
        return float(cv2.Laplacian(f, cv2.CV_64F).var())

    def _autofocus(self, x, y, z0, lim):
        p = self.params
        zlo, zhi = (lim["Z"] if lim and lim["Z"][0] != lim["Z"][1] else (-10**9, 10**9))
        zs = sorted({int(min(zhi, max(zlo, z))) for z in range(z0 - p["af_range"], z0 + p["af_range"] + 1, p["af_step"])})
        best_z, best_s = z0, -1.0
        for k, z in enumerate(zs):
            self._set(phase=f"focusing {k + 1}/{len(zs)} (Z {z})")
            self._move(x, y, z)
            s = self._sharpness(p["af_settle"])
            if s > best_s:
                best_z, best_s = z, s
        self._move(x, y, best_z)
        return best_z, best_s

    @staticmethod
    def _predict(pos, placed, M):
        """Offset of a tile at `pos` (steps): from the nearest registered
        tile (errors don't pile up over the spiral)."""
        if not placed:
            return (0.0, 0.0)
        p = np.array(pos[:2], float)
        good = [t for t in placed if t[2]] or placed
        s, o, _ = min(good, key=lambda t: np.hypot(*(t[0] - p)))
        return tuple(o + M @ (p - s))

    @staticmethod
    def _refit(placed, M):
        """Least-squares mosaic px per step from the registered tiles, once
        they span both axes; otherwise keep M."""
        good = [t for t in placed if t[2]]
        if len(good) < 3:
            return M
        s0, o0, _ = good[0]
        S = np.array([t[0] - s0 for t in good[1:]])
        O = np.array([t[1] - o0 for t in good[1:]])
        if np.linalg.matrix_rank(S) < 2:
            return M
        sol, *_ = np.linalg.lstsq(S, O, rcond=None)          # O = S @ sol  ->  M = sol.T
        return sol.T

    def _publish(self, mosaic, flat):
        x0, y0, x1, y1 = mosaic.bounds()
        scale = min(1.0, 1600.0 / max(1.0, x1 - x0), 900.0 / max(1.0, y1 - y0))
        img, _ = mosaic.render(scale, flat)
        img = np.clip(img, 0, 255).astype(np.uint8)
        self.mirror.stream.put(self.encode(img))
        return img

    # ── the scan ────────────────────────────────────────────────────────────
    def _run(self):
        cam, proj = self.camera, self.projector
        exposure_before = cam.exposure_us
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_dir = os.path.join(SCANS, stamp)
        record = {"started": stamp, "params": self.params, "tiles": []}
        mosaic, flat = stitch.Mosaic(), stitch.FlatField()
        try:
            os.makedirs(out_dir, exist_ok=True)
            proj.remember()
            proj.source, proj.image_name, proj.fit = self.load_pattern("white_4K.jpg"), "white_4K.jpg", "native"
            if proj.do("show")["state"] != "image":
                raise RuntimeError(f"projector display not found ({proj.error})")
            self._set(phase="exposure")
            cam.auto_exposure(start=self.fit_exposure_us)
            self.mirror.paused = True
            lim = self._limits()
            start = self._pos()
            sx, sy, sz = (int(v) for v in start)
            record["start_steps"] = [sx, sy, sz]
            record["limits"] = lim
            record["exposure_us"] = cam.exposure_us

            # calibration: camera px per step for X and Y. Each axis is
            # approached from the far side first (no backlash in the
            # measurement), then measured in 3 moves of CAL_STEP; the three and
            # their total must agree, and be plausible against the nominal
            # step, or the last good calibration is used. (One 40-step
            # measurement over the HACKER FAB bar arrays locked onto a repeat
            # and came out 40 % short, 2026-10-07.)
            self._set(phase="calibrating")
            um_per_px = FIELD_WIDTH_UM / cam.transform["size"][0]
            nominal = [1000.0 / self.spmm[a] / um_per_px for a in "XY"]       # ~4.15 px/step
            per_step, cal_frames, cal_notes = [], [], []
            for i, a in enumerate("XY"):
                d = CAL_STEP
                far, behind = [sx, sy, sz], [sx, sy, sz]
                far[i] += 3 * d
                behind[i] -= CAL_STEPS
                if not (self._inside(*far, lim) and self._inside(*behind, lim)):
                    d = -d
                    far[i], behind[i] = start[i] + 3 * d, start[i] + CAL_STEPS
                self._move(*behind)
                self._move(sx, sy, sz)
                frames = [self._tile(0.4)]
                for k in (1, 2, 3):
                    tgt = [sx, sy, sz]
                    tgt[i] += k * d
                    self._move(*tgt)
                    frames.append(self._tile(0.4))
                self._move(sx, sy, sz)
                cal_frames += frames if not cal_frames else frames[1:]
                each = []
                for k in (1, 2, 3):
                    dx, dy, q = shift_px(frames[k - 1], frames[k])
                    if q >= tracker.PSR_MIN:
                        each.append(np.array([dx, dy]) / d)
                tdx, tdy, tq = shift_px(frames[0], frames[3])
                total = np.array([tdx, tdy]) / (3 * d) if tq >= tracker.PSR_MIN else None
                vec = np.median(np.array(each), axis=0) if each else None
                ok = (vec is not None and len(each) >= 2 and total is not None
                      and all(np.hypot(*(e - vec)) <= CAL_TOL * np.hypot(*vec) for e in each)
                      and np.hypot(*(total - vec)) <= CAL_TOL * np.hypot(*vec)
                      and 0.6 * nominal[i] <= np.hypot(*vec) <= 1.5 * nominal[i])
                if ok:
                    per_step.append(tuple(vec))
                    cal_notes.append(f"{a}: measured")
                else:
                    saved = self._saved_calibration()
                    if saved is None:
                        raise RuntimeError(
                            f"calibration of {a} is inconsistent (moves {[np.round(e, 2).tolist() for e in each]}, "
                            f"total {None if total is None else np.round(total, 2).tolist()} px/step, "
                            f"nominal {nominal[i]:.2f}) and there is no earlier one: start on an area "
                            f"with features that don't repeat")
                    per_step.append(tuple(saved[:, i]))
                    cal_notes.append(f"{a}: inconsistent here, using the last good calibration")
            C = np.array(per_step).T                         # columns: camera px per +1 step in X, Y
            if all(n.endswith("measured") for n in cal_notes):
                with open(CAL_FILE, "w", encoding="utf-8") as fh:
                    json.dump({"cam_px_per_step": C.tolist(), "date": stamp}, fh, indent=2)
            for f in cal_frames:
                flat.add(f)
            f0 = cal_frames[0]
            h, w = f0.shape
            pitch = []
            for i in range(2):                               # steps per field along X and Y
                # D steps move the view by D*|v_x| px across and D*|v_y| px
                # down; the overlap is the smaller of 1 - D|v_x|/w and
                # 1 - D|v_y|/h, so the pitch is set by the tighter one. (The
                # first version measured the field "along" a tilted axis and
                # left ~5 % overlap in Y.)
                # Guard: never assume a step is shorter than nominal, so an
                # under-measured axis can't leave the fields without overlap.
                v = C[:, i] * max(1.0, nominal[i] / max(np.hypot(*C[:, i]), 1e-9))
                vx, vy = abs(v[0]), abs(v[1])
                d = self.params["pitch"] * min(w / max(vx, 1e-9), h / max(vy, 1e-9))
                pitch.append(max(1, int(round(d))))
            record["calibration"] = {"cam_px_per_step": C.tolist(), "pitch_steps": pitch, "notes": cal_notes,
                                     "um_per_px": FIELD_WIDTH_UM / cam.transform["size"][0]}
            um_per_step = [np.hypot(*C[:, i]) * FIELD_WIDTH_UM / cam.transform["size"][0] for i in range(2)]
            self._set(calibration={"pitch_steps": pitch, "um_per_step": [round(u, 3) for u in um_per_step],
                                   "notes": cal_notes})

            # mosaic px per step: the view moves opposite to the image. Refitted
            # from the registered tiles as they come (the 40-step calibration
            # was ~7 % off for 300-step moves on 2026-10-07).
            M = -C
            placed = []                                      # (steps xy, offset px, registered)
            ring_valid, ring = False, 0
            for i, j in stitch.spiral():
                r = max(abs(i), abs(j))
                if r != ring:                                # finished a ring
                    if not ring_valid and ring > 0:
                        self._set(phase="spiral left the soft limits: done")
                        break
                    ring, ring_valid = r, False
                if self.stop_flag.is_set():
                    break
                tx, ty = sx + i * pitch[0], sy + j * pitch[1]
                if not self._inside(tx, ty, sz, lim):
                    continue
                ring_valid = True
                n = len(mosaic.tiles)
                self._set(phase=f"field {n + 1} ({i:+d},{j:+d}): moving", grid=[i, j])
                z = self._pos()[2]
                self._move(tx, ty, z)
                score = None
                if self.params["autofocus"] and self.params["af_range"] > 0:
                    z, score = self._autofocus(tx, ty, z, lim)
                self._set(phase=f"field {n + 1} ({i:+d},{j:+d}): capturing")
                tile = self._tile(self.params["af_settle"])
                pos = self._pos()
                predicted = self._predict(pos, placed, M)
                if n == 0:
                    offset, residual, q, used = predicted, (0.0, 0.0), 0.0, False
                else:
                    max_shift = 0.25 * min(w, h)
                    offset, residual, q, used = stitch.register(mosaic, flat, tile, predicted, max_shift)
                mosaic.tiles.append(tile)
                mosaic.offsets.append(tuple(float(v) for v in offset))
                placed.append((np.array(pos[:2], float), np.array(offset, float), n == 0 or used))
                M = self._refit(placed, M)
                flat.add(tile)
                cv2.imwrite(os.path.join(out_dir, f"tile_{n:03d}.png"), tile)
                record["px_per_step"] = M.tolist()
                record["tiles"].append({"index": n, "grid": [i, j], "steps": [int(v) for v in pos],
                                        "z": int(z), "sharpness": score,
                                        "predicted_px": [round(v, 1) for v in predicted],
                                        "offset_px": [round(v, 1) for v in offset],
                                        "residual_px": [round(v, 1) for v in residual],
                                        "registration_quality": round(q, 1), "registered": bool(used)})
                self._publish(mosaic, flat)
                self._set(tiles=n + 1, phase=f"field {n + 1} done", last_residual=[round(v, 1) for v in residual],
                          last_quality=round(q, 1), last_registered=bool(used))

            self._save(mosaic, out_dir, record)
            record["finished"] = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            with open(os.path.join(out_dir, "scan.json"), "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2)
            self._set(phase="saved", saved=os.path.relpath(out_dir, ROOT))
            print(f"[scan] saved {len(mosaic.tiles)} fields to {out_dir}", flush=True)
        except Exception as exc:                            # report it in the UI, keep what there is
            self._set(phase="failed", error=str(exc))
            print(f"[scan] failed: {exc}", flush=True)
            try:
                self._save(mosaic, out_dir, record)
                self._set(saved=os.path.relpath(out_dir, ROOT))
            except Exception as exc2:
                print(f"[scan] saving after the failure failed too: {exc2}", flush=True)
            try:
                with open(os.path.join(out_dir, "scan.json"), "w", encoding="utf-8") as fh:
                    json.dump({**record, "error": str(exc)}, fh, indent=2)
            except OSError:
                pass
        finally:
            try:
                self.stage.send(f"S {self.params['step_delay']}")   # the user's speed back
            except Exception:
                pass
            self.mirror.paused = False
            proj.restore()
            cam.configure(exposure_us=exposure_before)
            self._set(active=False)

    def _saved_calibration(self):
        try:
            with open(CAL_FILE, encoding="utf-8") as fh:
                return np.array(json.load(fh)["cam_px_per_step"], float)
        except (OSError, ValueError, KeyError):
            return None

    def _save(self, mosaic, out_dir, record):
        """mosaic.png (+ preview.jpg) from the tiles so far, after a global
        refinement of the tile positions (stitch.refine_global)."""
        if mosaic.tiles:
            self._set(phase="saving: refining positions")
            final = stitch.FlatField()
            for t in mosaic.tiles[-final.MAX:]:
                final.add(t)
            cal = record.get("calibration", {}).get("cam_px_per_step")
            if len(mosaic.tiles) > 1 and (cal is not None or "px_per_step" in record):
                # start from the calibration: the live refit can be far off
                M = -np.array(cal, float) if cal is not None else record["px_per_step"]
                offsets, info = stitch.refine_global(
                    mosaic.tiles, [t["grid"] for t in record["tiles"]],
                    [t["steps"][:2] for t in record["tiles"]], M, final,
                    log=lambda m: print("[scan] " + m, flush=True))
                mosaic.offsets = offsets
                record["global_refinement"] = info
                for t, o in zip(record["tiles"], offsets):
                    t["refined_px"] = [round(o[0], 1), round(o[1], 1)]
            self._set(phase="saving")
            x0, y0, x1, y1 = mosaic.bounds()
            scale = 1.0 if (x1 - x0) * (y1 - y0) <= MAX_MOSAIC_PX else 0.5
            img, _ = mosaic.render(scale, final)
            img = np.clip(img, 0, 255).astype(np.uint8)
            cv2.imwrite(os.path.join(out_dir, "mosaic.png"), img)
            prev = self._publish(mosaic, final)
            cv2.imwrite(os.path.join(out_dir, "preview.jpg"), prev)
            um = record.get("calibration", {}).get("um_per_px")
            record["mosaic"] = {"file": "mosaic.png", "scale": scale, "size_px": [img.shape[1], img.shape[0]],
                                "um_per_px": um / scale if um else None, "origin_px": [x0, y0]}
