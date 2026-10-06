"""Stitching camera tiles of the wafer into one mosaic (used by ui/scan.py).

Tiles are fitted-view camera frames (Fit transform), all in the camera's
orientation: moving the stage only translates the view, so a tile's place
in the mosaic is a 2-D offset in camera pixels.

  spiral()        grid positions (i, j) in a square spiral from (0, 0)
  FlatField       the static pattern every tile shares (illumination falloff,
                  the DMD fault band, dust on the optics), estimated as the
                  per-pixel median of tiles from different places; tiles are
                  divided by it so the mosaic shows only the wafer. Its
                  `usable` mask marks camera-fixed pixels the division
                  leaves (fault band, dust), which registration ignores
  register()      refine a tile's predicted offset by correlating it with the
                  already stitched content where they overlap
  Mosaic          tiles + offsets -> preview / full-resolution image, with
                  feathered seams
  refine_global() all tile positions from every neighbour pair (whole-frame
                  search, robust solve), for the final mosaic
  restitch()      re-make a saved scan:  python camera/stitch.py scans/<date>
"""

import cv2
import numpy as np


def spiral():
    """(0,0), (1,0), (1,1), (0,1), (-1,1), (-1,0), (-1,-1), (0,-1), (1,-1), (2,-1), ..."""
    x = y = 0
    yield x, y
    n, k = 1, 0
    dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    while True:
        for _ in range(2):
            dx, dy = dirs[k % 4]
            for _ in range(n):
                x, y = x + dx, y + dy
                yield x, y
            k += 1
        n += 1


class FlatField:
    """Median of up to MAX tiles (at half resolution, then upsampled) once
    there are MIN of them; before that, a heavy blur of the tiles' mean
    (removes the illumination falloff only).

    `usable` (float, 1 = wafer, 0 = camera-fixed) marks what dividing by the
    flat doesn't remove: the DMD fault band (x ~1580-1720 of the fitted
    view, its bars change from frame to frame) and dust. Fixed features match
    perfectly at zero shift, so registration ignores them, and the mosaic
    takes those pixels from a neighbouring tile. Estimated once there are
    STATIC_MIN tiles: pixels whose fine detail is the same in most tiles."""

    MIN, MAX, SCALE = 3, 15, 0.5
    STATIC_MIN = 8

    def __init__(self):
        self.small = []
        self.shape = None
        self.flat = None
        self.usable = None

    def add(self, tile):
        self.shape = tile.shape
        s = cv2.resize(tile, None, fx=self.SCALE, fy=self.SCALE, interpolation=cv2.INTER_AREA)
        self.small = (self.small + [s])[-self.MAX:]
        if len(self.small) >= self.MIN:
            med = np.median(np.stack(self.small), axis=0).astype(np.float32)
        else:
            med = cv2.GaussianBlur(np.mean(np.stack(self.small), axis=0).astype(np.float32), (0, 0), 25)
        self.flat = cv2.resize(med, (self.shape[1], self.shape[0]), interpolation=cv2.INTER_LINEAR)
        self.flat = np.maximum(self.flat, 1.0)
        if len(self.small) >= self.STATIC_MIN:
            self.usable = self._usable()

    def _usable(self):
        hp = np.stack([_highpass(s) for s in self.small])
        med = np.median(hp, axis=0)
        spread = np.median(np.abs(hp - med), axis=0) + 1e-3
        ratio = cv2.GaussianBlur(np.abs(med), (0, 0), 3) / cv2.GaussianBlur(spread, (0, 0), 3)
        static = (ratio > 1.0).astype(np.uint8)
        static = cv2.morphologyEx(static, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        static = cv2.dilate(static, np.ones((9, 9), np.uint8))
        static = cv2.morphologyEx(static, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
        u = cv2.GaussianBlur(1.0 - static.astype(np.float32), (0, 0), 2)
        return cv2.resize(u, (self.shape[1], self.shape[0]), interpolation=cv2.INTER_LINEAR)

    def apply(self, tile):
        """Tile / flat, scaled back to the flat's mean brightness (uint8)."""
        if self.flat is None:
            return tile
        out = tile.astype(np.float32) / self.flat * float(self.flat.mean())
        return np.clip(out, 0, 255).astype(np.uint8)


def _feather(h, w):
    """Weights that fall to ~0 at the tile edges (smooth seams)."""
    wy = np.minimum(np.arange(h) + 1, np.arange(h)[::-1] + 1).astype(np.float32)
    wx = np.minimum(np.arange(w) + 1, np.arange(w)[::-1] + 1).astype(np.float32)
    return np.minimum(wy[:, None] / (0.15 * h), 1) * np.minimum(wx[None, :] / (0.15 * w), 1) + 1e-3


def _highpass(img):
    img = img.astype(np.float32)
    img = img - cv2.GaussianBlur(img, (0, 0), 6)
    return img / max(float(img.std()), 1e-3)


def correlate_plain(a, b, mask, max_shift=None):
    """Shift of b relative to a (both float, same size) within `mask`
    (overlap, bool or 0..1 weights), by phase correlation; returns
    (dx, dy, psr). No zero exclusion: here zero residual is a valid answer.
    max_shift: only look for the peak this far (px) from zero."""
    win = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 15)
    fa = cv2.dft(_highpass(a) * win, flags=cv2.DFT_COMPLEX_OUTPUT)
    fb = cv2.dft(_highpass(b) * win, flags=cv2.DFT_COMPLEX_OUTPUT)
    cross = cv2.mulSpectrums(fb, fa, 0, conjB=True)
    mag = cv2.magnitude(cross[..., 0], cross[..., 1])
    cross /= np.maximum(mag, 1e-9)[..., None]
    surf = np.fft.fftshift(cv2.idft(cross, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE))
    h, w = surf.shape
    search = surf
    if max_shift is not None:
        yy, xx = np.ogrid[:h, :w]
        near = (xx - w // 2) ** 2 + (yy - h // 2) ** 2 <= max_shift ** 2
        search = np.where(near, surf, -np.inf)
    y, x = np.unravel_index(int(np.argmax(search)), surf.shape)
    side = np.ones_like(surf, bool)
    side[max(0, y - 5):y + 6, max(0, x - 5):x + 6] = False
    psr = float((surf[y, x] - surf[side].mean()) / max(float(surf[side].std()), 1e-12))
    return x - w // 2, y - h // 2, psr


class Mosaic:
    """Tiles (already flat-corrected or not) with their offsets in camera px
    (top-left of each tile in mosaic coordinates; the first tile at 0, 0)."""

    def __init__(self):
        self.tiles = []        # uint8 images (raw)
        self.offsets = []      # (x, y) float, full-res px

    def bounds(self):
        h, w = self.tiles[0].shape
        xs = [o[0] for o in self.offsets]
        ys = [o[1] for o in self.offsets]
        return min(xs), min(ys), max(xs) + w, max(ys) + h

    def render(self, scale, flat=None, region=None):
        """The mosaic at `scale` (float32 image, NaN-free) and its weight.
        region: (x0, y0, x1, y1) in full-res mosaic px, default everything."""
        x0, y0, x1, y1 = region or self.bounds()
        W, H = int(np.ceil((x1 - x0) * scale)), int(np.ceil((y1 - y0) * scale))
        acc = np.zeros((H, W), np.float32)
        wsum = np.zeros((H, W), np.float32)
        th, tw = self.tiles[0].shape
        sh, sw = max(1, int(round(th * scale))), max(1, int(round(tw * scale)))
        feather = _feather(sh, sw)
        if flat is not None and flat.usable is not None:    # camera-fixed pixels from a neighbour
            feather = feather * np.maximum(cv2.resize(flat.usable, (sw, sh), interpolation=cv2.INTER_AREA), 1e-4)
        for tile, (ox, oy) in zip(self.tiles, self.offsets):
            t = flat.apply(tile) if flat is not None else tile
            small = cv2.resize(t, (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
            px, py = (ox - x0) * scale, (oy - y0) * scale
            ix, iy = int(np.floor(px)), int(np.floor(py))
            # sub-pixel placement
            m = np.float32([[1, 0, px - ix], [0, 1, py - iy]])
            small = cv2.warpAffine(small, m, (sw, sh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            ax0, ay0 = max(ix, 0), max(iy, 0)
            ax1, ay1 = min(ix + sw, W), min(iy + sh, H)
            if ax1 <= ax0 or ay1 <= ay0:
                continue
            sx0, sy0 = ax0 - ix, ay0 - iy
            sl = (slice(sy0, sy0 + ay1 - ay0), slice(sx0, sx0 + ax1 - ax0))
            acc[ay0:ay1, ax0:ax1] += small[sl] * feather[sl]
            wsum[ay0:ay1, ax0:ax1] += feather[sl]
        img = np.where(wsum > 0, acc / np.maximum(wsum, 1e-6), 0)
        return img, wsum


def register(mosaic, flat, tile, predicted, max_shift, scale=0.5, min_overlap=0.08, psr_min=15.0):
    """Refine `predicted` (x, y), the new tile's offset in mosaic px, by
    correlating its overlap with the stitched tiles. Returns
    ((x, y), residual (dx, dy), psr, used) -- `used` False if the match was
    too weak or too far, in which case the prediction is kept."""
    if not mosaic.tiles:
        return predicted, (0.0, 0.0), 0.0, False
    th, tw = tile.shape
    x, y = predicted
    region = (x, y, x + tw, y + th)
    canvas, wsum = mosaic.render(scale, flat, region)
    small = cv2.resize(flat.apply(tile) if flat is not None else tile,
                       (canvas.shape[1], canvas.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
    overlap = wsum > 0.05
    if overlap.mean() < min_overlap:
        return predicted, (0.0, 0.0), 0.0, False
    weight = overlap.astype(np.float32)
    if flat is not None and flat.usable is not None:
        weight *= cv2.resize(flat.usable, (canvas.shape[1], canvas.shape[0]), interpolation=cv2.INTER_AREA)
    dx, dy, psr = correlate_plain(canvas, small, weight, max_shift * scale)
    # b (the tile's content) is shifted by (dx, dy) relative to the canvas:
    # the tile belongs (-dx, -dy) further along.
    rx, ry = -dx / scale, -dy / scale
    used = psr >= psr_min and np.hypot(rx, ry) <= max_shift
    if used:
        return (x + rx, y + ry), (rx, ry), psr, True
    return predicted, (rx, ry), psr, False


# ── global refinement ──────────────────────────────────────────────────────
# Each tile is placed live against what is already stitched, so one wrong
# match (periodic structure such as bar arrays aliases easily) is never
# revisited. Once all tiles are in, register every pair of grid neighbours on
# its own overlap, then solve all positions together by robust least squares:
# a false match disagrees with the loops it is in, and the solve follows the
# majority. Weak priors from the stage steps keep tiles without good pairs
# where the steps put them.

def pair_offset(a, b, predicted, scale=0.5, margin=150, usable=None):
    """Offset of tile b relative to tile a (px), searched within `margin` of
    `predicted` on their overlap; returns (offset, quality) or None if they
    don't overlap. usable: FlatField.usable (camera-fixed pixels ignored)."""
    h, w = a.shape
    ox, oy = predicted
    x0, x1 = max(0.0, ox) - margin, min(w, ox + w) + margin
    y0, y1 = max(0.0, oy) - margin, min(h, oy + h) + margin
    x0, y0, x1, y1 = int(max(0, x0)), int(max(0, y0)), int(min(w, x1)), int(min(h, y1))
    if x1 - x0 < 96 or y1 - y0 < 96:
        return None
    jx0, jy0 = int(round(x0 - ox)), int(round(y0 - oy))

    def crop_b(img, fill):
        pad = cv2.copyMakeBorder(img, h, h, w, w, cv2.BORDER_CONSTANT, value=fill)
        return pad[jy0 + h:jy0 + h + (y1 - y0), jx0 + w:jx0 + w + (x1 - x0)]
    ra, rb = a[y0:y1, x0:x1], crop_b(b, float(b.mean()))
    sa = cv2.resize(ra, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    size = (sa.shape[1], sa.shape[0])
    sb = cv2.resize(rb, size, interpolation=cv2.INTER_AREA)
    weight = np.ones(sa.shape, np.float32)
    if usable is not None:
        weight = (cv2.resize(usable[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA)
                  * cv2.resize(crop_b(usable, 0.0), size, interpolation=cv2.INTER_AREA))
    dx, dy, q = correlate_plain(sa, sb, weight, margin * scale)
    return np.array(predicted, float) - np.array([dx, dy]) / scale, q


COARSE_SCALE = 0.25


def coarse_detail(img, scale=COARSE_SCALE):
    """A tile's fine detail at the coarse scale (input of coarse_offset)."""
    x = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA).astype(np.float32)
    x = x - cv2.GaussianBlur(x, (0, 0), 3)
    return x / max(float(x.std()), 1e-3)


def coarse_offset(A, B, predicted, radius, usable=None, scale=COARSE_SCALE, min_shift=50):
    """Offset of tile b relative to tile a (px) from whole-frame phase
    correlation of their coarse_detail A, B, the peak searched within
    `radius` px of `predicted` and at least `min_shift` px from zero (the
    camera-fixed residue peaks there); returns (offset, quality). For when
    the stage's move is unknown to hundreds of px (Y moved anywhere from 0
    to 125 % of its steps on 2026-10-07)."""
    H, W = A.shape
    win = cv2.createHanningWindow((W, H), cv2.CV_32F) ** 0.25     # soft edges, keeps most of the frame
    if usable is not None:
        win = win * cv2.resize(usable, (W, H), interpolation=cv2.INTER_AREA)
    fa = cv2.dft(cv2.copyMakeBorder(A * win, 0, H, 0, W, cv2.BORDER_CONSTANT), flags=cv2.DFT_COMPLEX_OUTPUT)
    fb = cv2.dft(cv2.copyMakeBorder(B * win, 0, H, 0, W, cv2.BORDER_CONSTANT), flags=cv2.DFT_COMPLEX_OUTPUT)
    cross = cv2.mulSpectrums(fa, fb, 0, conjB=True)                 # peak at b's offset in a's frame
    cross /= np.maximum(cv2.magnitude(cross[..., 0], cross[..., 1]), 1e-9)[..., None]
    surf = np.fft.fftshift(cv2.idft(cross, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE))
    yy, xx = np.mgrid[0:2 * H, 0:2 * W]
    ox, oy = (xx - W) / scale, (yy - H) / scale
    inside = ((ox - predicted[0]) ** 2 + (oy - predicted[1]) ** 2 <= radius ** 2) & (ox ** 2 + oy ** 2 >= min_shift ** 2)
    y, x = np.unravel_index(int(np.argmax(np.where(inside, surf, -np.inf))), surf.shape)
    rest = surf[inside]
    q = float((surf[y, x] - rest.mean()) / max(float(rest.std()), 1e-12))
    return np.array([ox[y, x], oy[y, x]]), q


def _step_map(pairs, steps, M):
    """Mosaic px per step re-measured from matched pairs that moved along one
    axis only (median per axis); M where an axis has fewer than 3."""
    M = np.array(M, float)
    for k in range(2):
        v = [off / (steps[j] - steps[i])[k] for i, j, off, _ in pairs
             if (steps[j] - steps[i])[1 - k] == 0 and (steps[j] - steps[i])[k] != 0]
        if len(v) >= 3:
            M[:, k] = np.median(v, axis=0)
    return M


def _robust_solve(n, meas, priors, prior_weight, X=None):
    """Positions (n, 2) from measurements (i, j, offset, quality) meaning
    X[j] - X[i] = offset, by graduated iteratively reweighted least squares:
    Cauchy weights whose scale shrinks from 400 px to 8 px, so the solve
    settles on the largest consistent set of pairs rather than averaging
    false matches in. Tile 0 is held at the origin."""
    q = np.array([m[3] for m in meas], float)

    def solve(w):
        rows, rhs = [], []
        for (i, j, off, _), wk in zip(meas, w):
            r = np.zeros(n); r[j], r[i] = 1, -1
            rows.append(r * np.sqrt(wk)); rhs.append(np.asarray(off) * np.sqrt(wk))
        for k in range(n):                     # weak pull towards the steps
            r = np.zeros(n); r[k] = prior_weight
            rows.append(r); rhs.append(priors[k] * prior_weight)
        r = np.zeros(n); r[0] = 100.0           # tile 0 at the origin
        rows.append(r); rhs.append(np.zeros(2))
        return np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]

    def residuals(X):
        return np.array([np.hypot(*(X[j] - X[i] - off)) for i, j, off, _ in meas])
    if X is None:
        X = solve(q)
    c = 400.0
    while True:
        for _ in range(5):
            res = residuals(X)
            X = solve(q * c ** 2 / (c ** 2 + res ** 2))
        if c <= 8:
            return X, residuals(X)
        c /= 1.6


def refine_global(tiles, grid, steps, px_per_step, flat=None, q_min=6.0,
                  coarse_min=10.0, tol=10.0, prior_weight=0.02, log=print):
    """Tile offsets (list of (x, y), tile 0 at 0, 0) from all neighbour pairs.
    grid: (i, j) per tile; steps: stage (x, y) per tile; px_per_step: 2x2
    mosaic px per step (column per axis), the starting guess.

    The stage's moves can't be trusted (2026-10-07: 154 Y steps moved the
    view anywhere from ~0 to 750 px), so each pair is searched over the
    whole frame, from zero shift to beyond the step map's prediction
    (coarse_offset), then refined within 40 px (pair_offset). Camera-fixed
    features (flat.usable) are ignored: with them, every pair matched at
    zero shift. Positions come from a robust solve (_robust_solve); then
    every neighbour pair is matched again around those positions (pairs
    whose whole-frame match was a bar-array alias), and solved again. A pair
    counts as consistent within `tol` px."""
    n = len(tiles)
    imgs = [(flat.apply(t) if flat is not None else t).astype(np.float32) for t in tiles]
    usable = flat.usable if flat is not None else None
    steps = np.asarray(steps, float)
    M = np.asarray(px_per_step, float)
    detail = np.stack([coarse_detail(t) for t in imgs])
    detail -= np.median(detail, axis=0)            # what every tile shares is the camera's, not the wafer's
    neighbours = [(i, j) for i in range(n) for j in range(i + 1, n)
                  if max(abs(grid[i][0] - grid[j][0]), abs(grid[i][1] - grid[j][1])) == 1]

    def accept(r):              # strong enough, and not snapped to the camera-fixed residue at zero
        return r is not None and r[1] >= q_min and np.hypot(*r[0]) >= 50
    meas = []
    for i, j in neighbours:
        pred = M @ (steps[j] - steps[i])
        off, q0 = coarse_offset(detail[i], detail[j], pred, np.hypot(*pred) + 200, usable)
        if q0 >= coarse_min:
            r = pair_offset(imgs[i], imgs[j], off, margin=40, usable=usable)
            if accept(r):
                meas.append((i, j, r[0], r[1]))
    first = len(meas)
    M = _step_map(meas, steps, M)                  # typical px per step, for the priors
    priors = [M @ (steps[k] - steps[0]) for k in range(n)]
    if not meas:
        log("global refinement: no neighbour pairs matched; placed by the steps")
        return [tuple(map(float, p)) for p in priors], {"px_per_step": np.round(M, 4).tolist(),
                                                         "pairs_matched": 0, "pairs_used": 0}
    X, _ = _robust_solve(n, meas, priors, prior_weight)
    for i, j in neighbours:                        # again, around the solved positions
        r = pair_offset(imgs[i], imgs[j], X[j] - X[i], margin=100, usable=usable)
        if accept(r) and not any(a == i and b == j and np.hypot(*(o - r[0])) < tol for a, b, o, _ in meas):
            meas.append((i, j, r[0], r[1]))
    X, res = _robust_solve(n, meas, priors, prior_weight, X)
    ok = res <= tol
    linked = {k for m, good in zip(meas, ok) if good for k in m[:2]}
    log(f"global refinement: {len(meas)} pair matches ({first} whole-frame, {len(meas) - first} around "
        f"the solution), {int(ok.sum())} consistent within {tol:g} px; "
        f"{n - len(linked)} of {n} tiles placed by the steps only")
    return [tuple(map(float, X[k])) for k in range(n)], {
        "px_per_step": np.round(M, 4).tolist(),
        "pairs_matched": len(meas), "pairs_used": int(ok.sum()),
        "max_residual_px": round(float(res[ok].max()), 2) if ok.any() else None,
        "tiles_by_steps_only": sorted(set(range(n)) - linked),
        "pairs": [{"tiles": [int(i), int(j)], "offset_px": np.round(off, 1).tolist(), "quality": round(float(q), 1),
                   "residual_px": round(float(e), 1)} for (i, j, off, q), e in zip(meas, res)]}


def restitch(scan_dir, out=None, scale=None):
    """Re-make a saved scan's mosaic with refine_global."""
    import json
    import os
    rec = json.load(open(os.path.join(scan_dir, "scan.json"), encoding="utf-8"))
    tiles = [cv2.imread(os.path.join(scan_dir, f"tile_{t['index']:03d}.png"), cv2.IMREAD_GRAYSCALE)
             for t in rec["tiles"]]
    flat = FlatField()
    for t in tiles[-FlatField.MAX:]:
        flat.add(t)
    # start from the calibration: the live refit can be far off (2026-10-07)
    cal = rec.get("calibration", {}).get("cam_px_per_step")
    M = -np.array(cal, float) if cal is not None else np.array(rec["px_per_step"], float)
    offsets, info = refine_global(tiles, [t["grid"] for t in rec["tiles"]],
                                  [t["steps"][:2] for t in rec["tiles"]], M, flat)
    m = Mosaic()
    m.tiles, m.offsets = tiles, offsets
    x0, y0, x1, y1 = m.bounds()
    scale = scale or (1.0 if (x1 - x0) * (y1 - y0) <= 300e6 else 0.5)
    img, _ = m.render(scale, flat)
    out = out or os.path.join(scan_dir, "mosaic_refined.png")
    cv2.imwrite(out, np.clip(img, 0, 255).astype(np.uint8))
    # positions and pair matches beside it, for checking
    info = {**info, "scale": scale, "origin_px": [x0, y0],
            "offsets_px": [[round(x, 1), round(y, 1)] for x, y in offsets]}
    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return out, info


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python camera/stitch.py scans/<date> [out.png]  -- re-stitch a saved scan")
    path, info = restitch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(path, {k: v for k, v in info.items() if k not in ("pairs", "offsets_px")})
