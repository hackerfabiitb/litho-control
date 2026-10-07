"""Write black/white checkerboard images at the projector's resolution.

    python patterns/scripts/make_checkerboards.py               # 100, 200, 300 px squares
    python patterns/scripts/make_checkerboards.py 50 128 256    # any square sizes
    python patterns/scripts/make_checkerboards.py 100 --size 1920x1080

Each one is saved as patterns/checker_<NNNN>px.png, the size zero-padded to
four digits so the files sort by size (checker_0001px.png ... checker_0300px.png):
lossless, 8-bit grey, top-left square white. Load one in the UI's Projector dialog at
"1:1 pixels" to show it unscaled. The squares come from lib.checkerboard_image, the
same as the UI's Checker button.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from lib import checkerboard_image  # noqa: E402

PATTERNS = pathlib.Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sizes", nargs="*", type=int, default=[100, 200, 300],
                   help="square sizes in pixels (default: 100 200 300)")
    p.add_argument("--size", default="3840x2160", help="image WIDTHxHEIGHT")
    p.add_argument("--out", default=str(PATTERNS), help="output folder (default: patterns/)")
    args = p.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    for n in args.sizes:
        if n <= 0:
            p.error(f"square size must be positive, got {n}")
        path = pathlib.Path(args.out) / f"checker_{n:04d}px.png"
        Image.fromarray(checkerboard_image(n, width, height), "L").save(path)
        print(f"{path}  {width}x{height}, {n} px squares "
              f"({width / n:g} x {height / n:g} squares)")


if __name__ == "__main__":
    main()
