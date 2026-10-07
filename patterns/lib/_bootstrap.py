"""Shared paths for the scripts. Provides the project root and output dir, and ensures the
output dir exists.

Note: a script must put the project root on ``sys.path`` before it can import ``lib`` at
all, so each script keeps a two-line header for that, then imports this module for OUT.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"

OUT.mkdir(exist_ok=True)
