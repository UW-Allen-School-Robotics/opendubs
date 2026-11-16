#!/usr/bin/env python3
"""Times coverage.py's reach grid with and without the SSIK pre-screen, and checks they give the same
cells. A desk check for a change to the screen; nothing here talks to the robot.

    uv run --python 3.12 --with mujoco --with pyyaml --with numpy --with matplotlib --with ssik \\
        python3 description/coverage_ssik_check.py --heights 0.75,1.0

Exits 1 if any cell differs. Each run is uncached (a fresh temporary directory).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--heights", default="0.75,1.0", help="bucket heights above the floor (m)")
    heights = [float(v) for v in ap.parse_args().heights.split(",")]
    runs = {}
    for ssik in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            _, _, cells = coverage.reach_grid(0.0, heights, Path(tmp), ssik=ssik)
            runs[ssik] = (time.perf_counter() - t0, cells)
    (t_old, old), (t_new, new) = runs[False], runs[True]
    bad = 0
    for k in sorted(old):
        lost, extra = old[k] - new[k], new[k] - old[k]
        bad += len(lost) + len(extra)
        print(f"{k[0]:5s} {k[1]:.2f} m: {len(old[k])} cells without the screen, {len(new[k])} with; "
              f"lost {sorted(lost) or 'none'}, extra {sorted(extra) or 'none'}")
    print(f"reach grid at {heights}: {t_old:.1f} s without the screen, {t_new:.1f} s with ({t_old / t_new:.2f}x); "
          f"{'SAME cells' if not bad else f'{bad} cells DIFFER'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
