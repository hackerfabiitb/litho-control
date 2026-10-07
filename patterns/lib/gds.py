"""GDSII export via the standalone KLayout scripting API (``klayout.db``).

Geometry is derived from the pattern's integer pixels, so the GDS matches the raster
exactly. Per-pixel intensity is intentionally *ignored*: GDSII stores binary polygons on a
layer/datatype and has no notion of grayscale dose.

Every GDS write also emits a matching ``.cif`` beside it, for the writer tools that want
CIF instead of GDSII.
"""

from __future__ import annotations

import os
import pathlib
import subprocess


def find_strm2cif() -> str:
    """Locate KLayout's ``strm2cif`` converter.

    A default Windows KLayout install does not put it on the PATH, hence the explicit
    lookup; the PATH name is the last resort (and what a POSIX install uses).
    """
    candidates = [
        pathlib.Path(os.environ.get("APPDATA", "")) / "KLayout" / "strm2cif.exe",
        pathlib.Path(r"C:\Program Files\KLayout\strm2cif.exe"),
        pathlib.Path(r"C:\Program Files (x86)\KLayout\strm2cif.exe"),
    ]
    for exe in candidates:
        if exe.is_file():
            return str(exe)
    return "strm2cif"


def write_cif(gds_path, cif_path=None) -> pathlib.Path | None:
    """Convert an existing GDS to CIF with ``strm2cif``.

    Returns the CIF path, or None if the conversion failed -- a missing KLayout install
    warns rather than raising, so it never costs you the GDS you just wrote.
    """
    gds_path = pathlib.Path(gds_path)
    cif_path = pathlib.Path(cif_path) if cif_path else gds_path.with_suffix(".cif")
    try:
        subprocess.check_call([find_strm2cif(), str(gds_path), str(cif_path)])
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"WARNING: CIF export failed ({e}). Is KLayout installed?")
        return None
    return cif_path


def write_gds(pattern, path, cif: bool = True) -> None:
    # imported here, so the rest of lib works without KLayout (pip install klayout)
    import klayout.db as kdb

    layout = kdb.Layout()
    layout.dbu = 0.001  # 1 nm database grid
    top = layout.create_cell(pattern.name)
    lyr = layout.layer(*pattern.layer)

    W, H, s = pattern.width_px, pattern.height_px, pattern.um_per_px

    def to_um(px, py):
        # centre the origin on the field and flip y so GDS is y-up
        return ((px - W / 2) * s, (H / 2 - py) * s)

    for b in pattern.iter_boxes():
        x0, y0 = to_um(b.x, b.y)
        x1, y1 = to_um(b.x + b.w, b.y + b.h)
        # intensity (b.intensity) deliberately unused -- GDS is binary
        top.shapes(lyr).insert(kdb.DBox(x0, y1, x1, y0))

    for p in pattern.iter_polygons():
        pts = [kdb.DPoint(*to_um(px, py)) for px, py in p.points]
        top.shapes(lyr).insert(kdb.DPolygon(pts))

    layout.write(str(path))

    if cif:
        write_cif(path)
