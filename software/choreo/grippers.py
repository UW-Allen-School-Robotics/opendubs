#!/usr/bin/env python3
"""Gripper calibration: a show's `open: 0..1` to and from each gripper's controller units.

    python3 grippers.py check [FILE]       # validate a calibration file (default: the robot's)
    python3 grippers.py template           # print an empty calibration file to fill in

A show says how open a gripper is, 0 = closed and 1 = open, the same on both sides (workplan
DEC-4). The two OpenArm grippers were zeroed in DIFFERENT physical states — on one robot the left
reads ~0 fully open while the right reads ~0 fully closed — so the controller value for "open" is
a property of each robot, measured on it, and lives in a file ON THE ROBOT. This repository never
holds those values (the per-robot rule); it holds this code, the template, and a test fixture with
made-up numbers.

WHERE THE FILE LIVES: `$OPENDUBS_GRIPPER_CAL`, else `/etc/opendubs/grippers.yaml` on the Thor.

HOW TO MEASURE IT (workplan 6.2, at the robot, gripper controllers loaded):
  1. command each gripper fully open; read its position from /joint_states -> `open`
  2. command it fully closed; read again -> `closed`
  3. write both sides into the file, run `grippers.py check`, then command open: 0.5 and look.
Nothing here commands a gripper; the runner and teach-by-capture use these conversions.
"""
from __future__ import annotations

import os
import sys

import yaml

DEFAULT_PATH = "/etc/opendubs/grippers.yaml"
SIDES = ("left", "right")
SLOT_SIDE = {"GRIP_L": "left", "GRIP_R": "right"}
MIN_TRAVEL = 0.005     # metres; less than this between open and closed is a mis-measurement
SANE_ABS = 0.2         # metres; a finger position beyond this is a unit mistake, not a gripper

TEMPLATE = """\
# Gripper calibration for THIS robot. Measured on it; never copied from another robot.
# Units: the gripper controller's own position units (metres of finger travel).
# How to measure: software/choreo/grippers.py (docstring). Check with: grippers.py check
robot: <name>          # which robot these numbers belong to
measured: <YYYY-MM-DD>
left:
  closed: <position when fully closed>
  open:   <position when fully open>
right:
  closed: <position when fully closed>
  open:   <position when fully open>
"""


class CalibrationError(ValueError):
    pass


def default_path() -> str:
    return os.environ.get("OPENDUBS_GRIPPER_CAL") or DEFAULT_PATH


def load(path: str | None = None) -> dict:
    """{side: (closed, open)} after checking the file makes physical sense."""
    path = path or default_path()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except FileNotFoundError as e:
        raise CalibrationError(f"no gripper calibration at {path} — measure it on the robot "
                               "(grippers.py template)") from e
    out, problems = {}, []
    for side in SIDES:
        d = doc.get(side)
        if not isinstance(d, dict):
            problems.append(f"{side}: missing")
            continue
        c, o = d.get("closed"), d.get("open")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (c, o)):
            problems.append(f"{side}: closed and open must both be numbers (got {c!r}, {o!r})")
            continue
        if max(abs(c), abs(o)) > SANE_ABS:
            problems.append(f"{side}: {c}, {o} — beyond {SANE_ABS} m; wrong units?")
        if abs(o - c) < MIN_TRAVEL:
            problems.append(f"{side}: open and closed are {abs(o - c) * 1000:.1f} mm apart — "
                            "too close to be a real measurement")
        out[side] = (float(c), float(o))
    if problems:
        raise CalibrationError(f"{path}: " + "; ".join(problems))
    return out


def to_controller(cal: dict, side: str, frac: float) -> float:
    """A show's 0..1 (0 closed, 1 open) as this robot's controller position for that side."""
    if not 0.0 <= frac <= 1.0:
        raise ValueError(f"open must be within 0..1, got {frac}")
    closed, opened = cal[side]
    return closed + (opened - closed) * frac


def from_controller(cal: dict, side: str, pos: float) -> float:
    """A measured controller position as 0..1, clamped: teach-by-capture reads grippers this way."""
    closed, opened = cal[side]
    return min(1.0, max(0.0, (pos - closed) / (opened - closed)))


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "template":
        sys.stdout.write(TEMPLATE)
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "check":
        path = sys.argv[2] if len(sys.argv) > 2 else default_path()
        try:
            cal = load(path)
        except CalibrationError as e:
            print("FAIL:", e)
            return 1
        for side, (c, o) in cal.items():
            print(f"{side:5s}  closed {c:+.4f}  open {o:+.4f}  "
                  f"(opens {'positive' if o > c else 'negative'}; half open = {to_controller(cal, side, 0.5):+.4f})")
        return 0
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main())
