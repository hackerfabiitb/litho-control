"""Laser-driller pattern: line arrays + circle grids, tiled 3x3.

Scale is fixed at 10 px : 1 um (PX_PER_UM) and the canvas is sized to the content, not
to the DMD field -- every knob below is in MICRONS and px() converts. The canvas is
derived from the assembled block's bbox, so the mask is exactly as big as it needs to
be; field_um is back-computed to hold the scale at whatever width results.

Content of one tile:
  - line arrays: 25/50/100 um widths x pitch 2x/4x x both orientations, 500 um long
  - circle grids: 25/50/75 um diameters at pitch 4x
  - plus fiducials in the 4 corners
That tile is replicated into a TILES x TILES grid, and a wafer outline (WAFER_NAME) is
overlaid at the centre so you can see how the dies sit on the substrate.

OUTPUT: GDS is the real deliverable. At 10 px/um a 2-inch wafer is 508000 px across, a
>200 gigapixel raster -- render_rgb alone would need terabytes of RAM, so a full-scale
PNG is not writable. GDS is vector and doesn't care. The PNG is therefore re-built at a
reduced scale (whole wafer ~PREVIEW_MAX_PX) purely to eyeball the layout; NOT the mask.
At that zoom the dies are a small central cluster -- open the GDS to inspect features.

Layout is manual (hstack/vstack, centred rows) rather than flow_layout, whose shelf
packing top-aligns and reorders.

Run:  python patterns/scripts/electroplating_pattern.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import OUT
from lib import (
    Pattern,
    ShapeGroup,
    box,
    line_array,
    circle_grid,
    plus_fiducial,
    wafer,
    WAFER_SIZES_MM,
    text,
    write_gds,
    write_png,
)

# --- knobs (all lengths in MICRONS) ----------------------------------------------
PX_PER_UM = 10  # projection scale for the mask: 10 px : 1 um
PREVIEW_MAX_PX = 2600  # eyeball PNG is scaled so the wafer spans ~this many px

WAFER_NAME = "2inch"  # substrate outline overlaid at pattern centre; see WAFER_SIZES_MM
WAFER_STROKE_UM = 50  # ring thickness of the wafer outline

ARRAY_WIDTHS_UM = (25, 50, 75, 100)  # line-array line widths
PITCH_FACTORS = (2,)  # line gap = pitch_factor x line_width (edge-to-edge)
N_LINES = 5  # lines per array
LINE_LEN_UM = 500  # long dimension of every line

CIRCLE_DIAMS_UM = (25, 50, 75, 100)  # circle-grid diameters
CIRCLE_PITCH_FACTOR = 1  # circle gap = this x diameter (edge-to-edge)
CIRCLE_N = 5  # circles per side of each grid

GAP_UM = 500  # spacing between adjacent groups / rows
MARGIN_UM = 50  # fiducial inset from each tile corner
FIDUCIAL_SQ_UM = 20  # plus-fiducial square (its bbox is 3x this)
TILE_GAP_UM = 3000  # spacing between tiles
TILES = 3  # tile the pattern TILES x TILES

DIE_LABEL_LINES = (  # printed on every die, one text row per line
    "2026-07-18 electroplating",
    "9-point uniformity test",
)
DIE_TEXT_HEIGHT_UM = 450  # cap height of each label line; the die widens to fit it
DIE_NUM_HEIGHT_UM = 800  # cap height of the per-die id (00..22 for TILES=3)
BIG_SQUARE_UM = 5000  # solid square beside the features, to add exposed plating area


# --- manual layout ----------------------------------------------------------------
def _stack(groups, gap: int, axis: int) -> ShapeGroup:
    """Lay groups out along ``axis`` (0 = x, 1 = y), centred on the other axis."""
    dims = [g.bbox() for g in groups]
    # bbox is (x, y, w, h), so the cross-axis size is index 3 for x, index 2 for y
    cross = max(d[3 - axis] for d in dims)
    out = ShapeGroup()
    pos = 0
    for g, (gx, gy, gw, gh) in zip(groups, dims):
        size, gcross = (gw, gh) if axis == 0 else (gh, gw)
        off = (cross - gcross) // 2
        dx, dy = (pos, off) if axis == 0 else (off, pos)
        out.add(g.translated(dx - gx, dy - gy))
        pos += size + gap
    return out


def hstack(groups, gap: int) -> ShapeGroup:
    """Left-to-right, vertically centred."""
    return _stack(groups, gap, axis=0)


def vstack(groups, gap: int) -> ShapeGroup:
    """Top-to-bottom, horizontally centred."""
    return _stack(groups, gap, axis=1)


# --- assembly ---------------------------------------------------------------------
def build(scale: float) -> Pattern:
    """Assemble the whole 3x3 mask at ``scale`` px per um.

    Geometry is defined in microns, so the same call yields the full-scale mask or a
    small preview depending on ``scale``.
    """

    def px(value_um: float) -> int:
        return round(value_um * scale)

    gap = px(GAP_UM)

    # one row per pitch factor: ascending width, h then v within each width
    line_rows = [
        hstack(
            [
                line_array(
                    px(w),
                    n_lines=N_LINES,
                    pitch_factor=pf,
                    orientation=o,
                    length=px(LINE_LEN_UM),
                )
                for w in ARRAY_WIDTHS_UM
                # for o in ("h", "v")
                for o in ("h")
            ],
            gap,
        )
        for pf in PITCH_FACTORS
    ]

    # one row of circle grids, ascending diameter
    circle_row = hstack(
        [
            circle_grid(px(d), n=CIRCLE_N, pitch_factor=CIRCLE_PITCH_FACTOR)
            for d in CIRCLE_DIAMS_UM
        ],
        gap,
    )

    # the resolution features, with one big solid square beside them for extra plating
    # area (row-centred by hstack)
    features = vstack(line_rows + [circle_row], gap)
    big = box(px(BIG_SQUARE_UM), px(BIG_SQUARE_UM))
    core = hstack([features, big], gap)

    # per-die label: the die id (00..22, read row-col) above the shared two-line label,
    # each at a fixed physical cap height. The die grows to fit, so DIE_TEXT_HEIGHT_UM /
    # DIE_NUM_HEIGHT_UM are the text-size knobs.
    unit_h = text(DIE_LABEL_LINES[0], scale=1).bbox()[3]  # line height at 1 font-px/px
    tscale = max(1, round(px(DIE_TEXT_HEIGHT_UM) / unit_h))
    nscale = max(1, round(px(DIE_NUM_HEIGHT_UM) / unit_h))
    line_gap = max(1, round(0.4 * tscale * unit_h))  # leading between text lines
    label_body = vstack([text(ln, scale=tscale) for ln in DIE_LABEL_LINES], line_gap)

    fid_sq = px(FIDUCIAL_SQ_UM)
    fid_span = 3 * fid_sq  # plus_fiducial's bbox is 3x its square
    inset = px(MARGIN_UM)
    pad = inset + fid_span + gap  # keeps the fiducials clear of the content
    fid = plus_fiducial(fid_sq)

    def make_die(die_id: str):
        """Frame one die: id line + shared label + features, with corner fiducials."""
        label = vstack([text(die_id, scale=nscale), label_body], line_gap)
        content = vstack([label, core], gap)
        cx, cy, cw, ch = content.bbox()
        tw, th = cw + 2 * pad, ch + 2 * pad
        die = ShapeGroup()
        die.add(content.translated(pad - cx, pad - cy))
        for fx in (inset, tw - inset - fid_span):
            for fy in (inset, th - inset - fid_span):
                die.add(fid.translated(fx, fy))
        return die, tw, th

    # every die is the same size (its id is always 2 chars); place them in the grid.
    # id = f"{row}{col}" so the grid reads 00 01 02 / 10 11 12 / 20 21 22.
    dies = {(i, j): make_die(f"{j}{i}") for i in range(TILES) for j in range(TILES)}
    tile_w, tile_h = dies[(0, 0)][1], dies[(0, 0)][2]

    # canvas is derived from the content: exactly the TILES x TILES grid
    tile_gap = px(TILE_GAP_UM)
    w = TILES * tile_w + (TILES - 1) * tile_gap
    h = TILES * tile_h + (TILES - 1) * tile_gap

    # field_um back-computed so um_per_px == 1 / scale holds at this width
    p = Pattern("laser_driller_pattern", field_um=w / scale, width_px=w, height_px=h)
    if abs(p.px_per_um - scale) > 1e-9:
        raise ValueError(f"scale drifted: {p.px_per_um} != {scale} px/um")

    for (i, j), (die, _, _) in dies.items():
        p.place(die, i * (tile_w + tile_gap), j * (tile_h + tile_gap))

    return p


def compose(scale: float) -> Pattern:
    """Build the die grid and overlay the wafer outline, both centred on one canvas.

    The wafer is far larger than the die grid, so the canvas grows to whichever is
    bigger and both are centred in it. The wafer is drawn as a ring (stroke), not a
    filled disk, so it frames the dies instead of painting over them.
    """
    dies = ShapeGroup(build(scale).shapes)
    dx, dy, dw, dh = dies.bbox()
    dies = dies.translated(-dx, -dy)  # normalise to origin before re-centring

    diam_mm, flat_mm = WAFER_SIZES_MM[WAFER_NAME]
    ring = wafer(
        round(diam_mm * 1000 * scale),
        round(flat_mm * 1000 * scale),
        stroke=max(1, round(WAFER_STROKE_UM * scale)),
    )
    _, _, ww, wh = ring.bbox()

    w, h = max(dw, ww), max(dh, wh)
    p = Pattern("laser_driller_pattern", field_um=w / scale, width_px=w, height_px=h)
    # p.place(ring, (w - ww) // 2, (h - wh) // 2)
    p.place(dies, (w - dw) // 2, (h - dh) // 2)
    return p


mask = compose(PX_PER_UM)
geom = ShapeGroup(mask.shapes)  # wrap so the new diagonal()/area() helpers are usable
diag_um = geom.diagonal() / PX_PER_UM
area_um2 = geom.area() / PX_PER_UM**2  # px -> um^2 (divide by px/um squared)
fill = geom.area() / (mask.width_px * mask.height_px)  # exposed fraction of the canvas
print(
    f"mask:    {mask.width_px} x {mask.height_px} px  "
    f"({mask.width_px / PX_PER_UM:g} x {mask.height_px / PX_PER_UM:g} um)  "
    f"= {mask.width_px * mask.height_px / 1e9:.2f} Gpx"
)
print(f"wafer:   {WAFER_NAME} ({WAFER_SIZES_MM[WAFER_NAME][0]} mm dia)")
print(f"shapes:  {len(mask.shapes)}")
print(f"diag:    {diag_um:.0f} um  ({diag_um / 1000:.2f} mm)")
print(f"feature: {area_um2 / 1e6:.3f} mm^2 exposed  ({fill:.2%} fill)")
write_gds(mask, OUT / "electroplating_pattern.gds")

# preview only -- a full-scale PNG is not writable (see OUTPUT in the module docstring).
# Scale so the whole wafer spans ~PREVIEW_MAX_PX; at that zoom the dies are a small
# central cluster (the wafer is ~5x wider than the pattern).
preview_scale = PREVIEW_MAX_PX / (WAFER_SIZES_MM[WAFER_NAME][0] * 1000)
pv = compose(preview_scale)
print(f"preview: {pv.width_px} x {pv.height_px} px @ {preview_scale:.4f} px/um")
write_png(pv, OUT / "electroplating_pattern_preview.png", "white")
