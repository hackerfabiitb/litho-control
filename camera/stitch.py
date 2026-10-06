"""Stitching camera tiles of the wafer into one mosaic (used by ui/scan.py).

Tiles are fitted-view camera frames (Fit transform), all in the camera's
orientation: moving the stage only translates the view, so a tile's place
in the mosaic is a 2-D offset in camera pixels.

  spiral()        grid positions (i, j) in a square spiral from (0, 0)
  FlatField       the static pattern every tile shares (illumination falloff,
                  the DMD fault band, dust on the optics), estimated as the
                  per-pixel median of tiles from different places; tiles are
                  divided by it so the mosaic shows only the wafer
  register()      refine a tile's predicted offset by correlating it with the
                  already stitched content where they overlap
  Mosaic          tiles + offsets -> preview / full-resolution image, with
                  feathered seams
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
    (removes the illumination falloff only)."""

    MIN, MAX, SCALE = 3, 15, 0.5

    def __init__(self):
        self.small = []
        self.shape = None
        self.flat = None

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


def correlate_plain(a, b, mask):
    """Shift of b relative to a (both float, same size) within `mask`
    (overlap), by phase correlation; returns (dx, dy, psr). No zero
    exclusion: here zero residual is a valid answer."""
    win = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 15)
    fa = cv2.dft(_highpass(a) * win, flags=cv2.DFT_COMPLEX_OUTPUT)
    fb = cv2.dft(_highpass(b) * win, flags=cv2.DFT_COMPLEX_OUTPUT)
    cross = cv2.mulSpectrums(fb, fa, 0, conjB=True)
    mag = cv2.magnitude(cross[..., 0], cross[..., 1])
    cross /= np.maximum(mag, 1e-9)[..., None]
    surf = np.fft.fftshift(cv2.idft(cross, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE))
    h, w = surf.shape
    y, x = np.unravel_index(int(np.argmax(surf)), surf.shape)
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
    dx, dy, psr = correlate_plain(canvas, small, overlap)
    # b (the tile's content) is shifted by (dx, dy) relative to the canvas:
    # the tile belongs (-dx, -dy) further along.
    rx, ry = -dx / scale, -dy / scale
    used = psr >= psr_min and np.hypot(rx, ry) <= max_shift
    if used:
        return (x + rx, y + ry), (rx, ry), psr, True
    return predicted, (rx, ry), psr, False
