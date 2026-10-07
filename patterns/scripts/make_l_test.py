"""Nested L-shape test structure.

Run:  python patterns/scripts/make_l_test.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib._bootstrap import OUT
from lib import Pattern, nested_ls, write_png


p = Pattern("l_test")

# Nested L's, one per line width, sharing a common top-left origin.
group = nested_ls(n=5, line_widths=[4, 16, 35])
p.place(group, 100, 100)

write_png(p, OUT / "l_test_white.png", "white")

print(f"wrote {OUT / 'l_test_white.png'}")
