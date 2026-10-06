"""Find the projected image's outline in a camera frame of an all-white
projection, and the perspective transform that maps it to an upright 16:9
rectangle. Used by the UI's "Fit transform" (ui/server.py); also runs on a
saved image:

    python camera/fit_transform.py white_frame.png [--out debug.png]

Why not a plain threshold: on this rig the white field reads only ~75-87
against ~52-80 outside it, because a white area spreads ~10 % of its light
over the whole frame (veil) and an 18 % ghost copy sits ~440 px above it
(camera/README.md, 2026-10-07). The margin above the image is lit by that
ghost, and the DMD fault band makes a straight dark stripe just inside the
right edge. So each side is found on its own:

  1. a rough outline (Otsu threshold of a heavily blurred frame, largest
     region, minimum-area rectangle) gives the four sides and "inward";
  2. along each side, brightness profiles across it give every
     dark-outside -> bright-inside step;
  3. RANSAC fits straight lines through those steps; among the lines with
     strong support, the outermost wins (the real edge lies outside the fault
     band's edge and the fringe patches);
  4. adjacent lines intersect at the corners.
"""

import argparse

import cv2
import numpy as np

ASPECT = 16 / 9


def order_corners(pts):
    """Corners as top-left, top-right, bottom-right, bottom-left (camera view)."""
    pts = np.asarray(pts, np.float32)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]                       # clockwise in image coords
    start = np.argmin(pts.sum(axis=1))               # top-left: smallest x + y
    return np.roll(pts, -start, axis=0)


def _side_points(sm, p0, p1, inward, span, samples=140, half=6):
    """Points of dark->bright steps (going inward) across the side p0-p1."""
    h, w = sm.shape
    s = np.linspace(0.08, 0.92, samples)[:, None]
    base = p0[None, :] + s * (p1 - p0)[None, :]                # (samples, 2)
    offs = np.arange(-span, span + 1, dtype=np.float32)        # along inward
    xy = base[:, None, :] + offs[None, :, None] * inward[None, None, :]
    prof = cv2.remap(sm, xy[..., 0].astype(np.float32), xy[..., 1].astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan)
    pts, steps = [], []
    k = np.ones(half, np.float32) / half
    for i in range(samples):
        p = prof[i]
        if np.isnan(p).mean() > 0.5:
            continue
        p = np.nan_to_num(p, nan=np.nanmean(p))
        mean = np.convolve(p, k, mode="same")
        step = np.roll(mean, -half // 2 - 1) - np.roll(mean, half // 2 + 1)   # inside - outside
        step[:half + 1] = step[-half - 1:] = 0
        thr = max(2.5, 0.35 * step.max())
        peaks = np.flatnonzero((step > thr) & (step >= np.roll(step, 1)) & (step >= np.roll(step, -1)))
        for j in peaks:
            pts.append(xy[i, j])
            steps.append(step[j])
    return np.array(pts, np.float32), np.array(steps, np.float32), base


def _fit_side(pts, inward, origin, n_samples, rng):
    """RANSAC lines through pts; return the outermost well-supported one as
    (point, direction)."""
    if len(pts) < 10:
        raise RuntimeError("too few edge points on one side")
    lines = []
    for _ in range(400):
        a, b = pts[rng.choice(len(pts), 2, replace=False)]
        d = b - a
        if np.hypot(*d) < 20:
            continue
        d = d / np.hypot(*d)
        nrm = np.array([-d[1], d[0]])
        inl = np.abs((pts - a) @ nrm) < 2.0
        if inl.sum() < 0.3 * n_samples:
            continue
        depth = float(((pts[inl] - origin) @ inward).mean())   # how far inward
        lines.append((int(inl.sum()), depth, inl))
    if not lines:
        raise RuntimeError("no straight edge found on one side")
    best = max(n for n, _, _ in lines)
    strong = [l for l in lines if l[0] >= 0.6 * best]
    _, _, inl = min(strong, key=lambda l: l[1])                  # outermost
    vx, vy, x0, y0 = cv2.fitLine(pts[inl], cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return np.array([x0, y0]), np.array([vx, vy])


def _intersect(l1, l2):
    (p, d), (q, e) = l1, l2
    a = np.array([[d[0], -e[0]], [d[1], -e[1]]])
    t = np.linalg.solve(a, q - p)
    return p + t[0] * d


def find_quad(img):
    """The four corners (TL, TR, BR, BL, camera view) of the bright projected
    field in a grey camera frame (float or uint8)."""
    img = img.astype(np.float32)
    rough = cv2.GaussianBlur(img, (0, 0), 15)
    u8 = cv2.normalize(rough, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        raise RuntimeError("no bright region found")
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    cnts, _ = cv2.findContours((lab == big).astype(np.uint8), cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_NONE)
    box = order_corners(cv2.boxPoints(cv2.minAreaRect(max(cnts, key=cv2.contourArea))))
    centre = box.mean(axis=0)
    sm = cv2.GaussianBlur(img, (0, 0), 2)
    rng = np.random.default_rng(0)
    lines = []
    for i in range(4):
        p0, p1 = box[i], box[(i + 1) % 4]
        d = (p1 - p0) / np.hypot(*(p1 - p0))
        inward = np.array([-d[1], d[0]], np.float32)
        if (centre - p0) @ inward < 0:
            inward = -inward
        other = box[(i + 2) % 4]
        span = int(0.18 * abs((other - p0) @ inward))           # search +-18 % of the depth
        pts, _, _ = _side_points(sm, p0, p1, inward, span)
        lines.append(_fit_side(pts, inward, p0, 140, rng))
    corners = np.array([_intersect(lines[i - 1], lines[i]) for i in range(4)], np.float32)
    return order_corners(corners)


def make_transform(corners, max_width=1920):
    """Perspective matrix mapping the corners onto an upright 16:9 rectangle,
    and that rectangle's size. The longer pair of sides becomes the width."""
    tl, tr, br, bl = corners
    if np.hypot(*(tr - tl)) + np.hypot(*(br - bl)) < np.hypot(*(bl - tl)) + np.hypot(*(br - tr)):
        tl, tr, br, bl = bl, tl, tr, br                          # taller than wide: turn
    src = np.array([tl, tr, br, bl], np.float32)
    width = min(max_width, int(round(max(np.hypot(*(tr - tl)), np.hypot(*(br - bl))))))
    width -= width % 16
    height = int(round(width / ASPECT))
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    return cv2.getPerspectiveTransform(src, dst), (width, height), src


def draw_debug(img, corners):
    out = cv2.cvtColor(cv2.normalize(img.astype(np.float32), None, 0, 255,
                                     cv2.NORM_MINMAX).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    pts = np.round(corners).astype(np.int32)
    cv2.polylines(out, [pts], True, (0, 0, 255), 3, cv2.LINE_AA)
    for i, (x, y) in enumerate(pts):
        cv2.circle(out, (int(x), int(y)), 10, (0, 255, 255), 3)
        cv2.putText(out, "TL TR BR BL".split()[i], (int(x) + 12, int(y) - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("--out", default=None, help="debug image with the found outline")
    args = p.parse_args()
    img = cv2.imread(args.image, cv2.IMREAD_GRAYSCALE)
    corners = find_quad(img)
    m, size, _ = make_transform(corners)
    print("corners (TL TR BR BL):", np.round(corners, 1).tolist())
    print("output:", size)
    if args.out:
        cv2.imwrite(args.out, draw_debug(img, corners))
        cv2.imwrite(args.out.replace(".", "_warped.", 1),
                    cv2.warpPerspective(img, m, size, flags=cv2.INTER_LINEAR))


if __name__ == "__main__":
    main()
