#!/usr/bin/env python3
"""Diagnose the torque controller of engage.py on the real arm: engage.py unchanged (every one of its gates applies, including CH6 at launch), plus
a high-rate recorder and a read-out of what happened in the first moments. Written because engage KICKED the right arm (joint 7 reached 6.1 rad/s and tripped the
driver, 2026-10-23) and a 2 Hz print cannot show a few-millisecond event.

    python torque_probe.py --mode hold --side right --model <xml> --backend hardware --hardware-config <yaml> --supervised --start-tolerance-deg 20
    python torque_probe.py --mode park --nf-scale 0.25 --side right ... --speed 0.1 --start-tolerance-deg 20

`--park` is added if you leave it out (ensure_park): this tool only ever runs engage's park path, never its move to the `ready` pose.

  --mode hold      engage to the arm's CURRENT pose: nothing is asked to move. This isolates the TRANSITION from the driver's position hold (with its gravity feed-forward) into the
                   torque controller (which the driver gives no feed-forward). If the arm kicks or drifts here, it is the transition or the gravity term, not the gains.
  --mode park      engage's own park to the stow file's pose (pass --park, as for engage.py).
  --nf-scale F     multiplies every natural_frequency of the controllers by F (0.25 turns the 40 rad/s posture stage into 10): does the kick shrink with the gain?
  --record S       seconds recorded from the moment the interface is up (default 8).
  --csv PATH       the raw samples (default ~/torque_probe-<time>.csv): t, q1..q7, qd1..qd7, tau1..tau7 (measured).

Everything else goes to engage.py. The read-out says, per joint, when it began to move, how far it went, its peak speed and peak measured torque and when, and lists any
sample over 1 rad/s; then a millisecond-level window around the first big event. The torque is the motors' own reply, not the command (the interface does not expose that).
For a torque_kd or gain experiment, make a config copy with make_config.py and pass it as --hardware-config.
"""
from __future__ import annotations

import argparse
import copy
import csv
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))      # bringup.py, engage.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)

import gafro  # noqa: E402,F401  -- before gafro_robot_interface
import gafro_robot_interface as ri  # noqa: E402

import engage  # noqa: E402

KICK_SPEED = 1.0          # rad/s: a sample over this is listed
ONSET = 0.004             # rad: a joint has begun to move when it is this far from where it was first seen
PERIOD = 0.002            # s between samples (the interface updates at the driver's 250 Hz; sampling faster only repeats a frame)


def scale_natural_frequencies(node, factor: float):
    """A copy of a controllers dict with every `natural_frequency` multiplied by `factor` and nothing else changed."""
    node = copy.deepcopy(node)

    def walk(x):
        if isinstance(x, dict):
            for key, value in x.items():
                if key == "natural_frequency" and isinstance(value, (int, float)):
                    x[key] = value * factor
                else:
                    walk(value)
        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(node)
    return node


def ensure_park(argv: list) -> list:
    """engage.py goes through park() only with --park, and without it moves the arm to the `ready` keyframe. This tool only ever diagnoses the park path (hold is park
    patched to the arm's own pose), so --park is added if it is missing: it must never run the move to ready by leaving one flag out."""
    return list(argv) if "--park" in argv else list(argv) + ["--park"]


def analyse(t: np.ndarray, q: np.ndarray, v: np.ndarray, tau: np.ndarray, window: int = 24) -> str:
    """Per-joint onset, displacement, peak speed and torque, every sample over KICK_SPEED, and a window around the first big event. t in s from the first sample."""
    n, dof = q.shape
    out = [f"{n} samples over {t[-1]:.2f} s (about {n / max(t[-1], 1e-9):.0f} per s)"]
    out.append("joint | began to move | farthest from its first reading | peak speed (at)     | peak |torque| (at)")
    first_event, first_joint = None, None
    for j in range(dof):
        moved = np.where(np.abs(q[:, j] - q[0, j]) > ONSET)[0]
        onset = f"{t[moved[0]]:6.3f} s" if len(moved) else "    never"
        k_v, k_tau = int(np.abs(v[:, j]).argmax()), int(np.abs(tau[:, j]).argmax())
        out.append(f"  {j + 1}   | {onset}      | {np.abs(q[:, j] - q[0, j]).max():8.4f} rad                 | {np.abs(v[k_v, j]):6.2f} rad/s ({t[k_v]:5.3f}) | {np.abs(tau[k_tau, j]):6.2f} N m ({t[k_tau]:5.3f})")
    over = np.argwhere(np.abs(v) > KICK_SPEED)
    if len(over):
        out.append(f"samples over {KICK_SPEED:g} rad/s: {len(over)}; the first few (time, joint, speed):")
        for k, j in over[:6]:
            out.append(f"   {t[k]:6.3f} s  joint {j + 1}  {v[k, j]:+.2f} rad/s")
        first_event, first_joint = int(over[0][0]), int(over[0][1])
    else:
        onsets = [(t[np.where(np.abs(q[:, j] - q[0, j]) > ONSET)[0][0]], j) for j in range(dof) if np.any(np.abs(q[:, j] - q[0, j]) > ONSET)]
        if onsets:
            when, first_joint = min(onsets)
            first_event = int(np.searchsorted(t, when))
            out.append(f"no sample over {KICK_SPEED:g} rad/s. The first joint to move is joint {first_joint + 1}, at {when:.3f} s.")
        else:
            out.append(f"no joint moved more than {ONSET:g} rad and no sample exceeded {KICK_SPEED:g} rad/s: the transition was quiet.")
    if first_event is not None:
        lo, hi = max(0, first_event - window // 3), min(n, first_event + window)
        out.append(f"window around the first event, joint {first_joint + 1} (t, q - q0, speed, measured torque):")
        for k in range(lo, hi, max(1, (hi - lo) // 14)):
            out.append(f"   {t[k]:7.3f} s  {q[k, first_joint] - q[0, first_joint]:+8.4f} rad  {v[k, first_joint]:+7.2f} rad/s  {tau[k, first_joint]:+7.2f} N m")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--mode", choices=("park", "hold"), default="park")
    ap.add_argument("--nf-scale", type=float, default=1.0)
    ap.add_argument("--record", type=float, default=8.0)
    ap.add_argument("--csv", default=None)
    own, rest = ap.parse_known_args(sys.argv[1:])
    if "-h" in rest or "--help" in rest:
        print(__doc__)
        return 0
    rest = ensure_park(rest)
    sys.argv = [sys.argv[0]] + rest
    side = rest[rest.index("--side") + 1] if "--side" in rest else "right"
    if own.nf_scale <= 0:
        print("refused: --nf-scale must be above 0")
        return 1

    real_load = engage.load_controllers
    engage.load_controllers = lambda path=None: scale_natural_frequencies(real_load(path), own.nf_scale)

    if own.mode == "hold":
        def park_hold(robot, side_, model_path, conf=None, speed=engage.JOINT_SPEED, controllers=None, log=print, start_tolerance=None):
            system = robot.get_system()
            here = np.asarray(robot.get_state().get_position(), dtype=float).copy()
            names = ("left", "right") if side_ == "both" else (side_,)
            stows = [here[np.asarray(system.get_task_space(name).get_joint_indices())] for name in names]
            log(f"HOLD: engage to the arm's own current pose {np.round(np.concatenate(stows), 3)}: nothing is asked to move")
            return engage.engage(robot, side_, model_path, "hold", speed, controllers, log, target_all=here, then="hold",
                                 known_starts={"stow": np.concatenate(stows)}, start_tolerance=start_tolerance)

        engage.park = park_hold

    samples, stop = [], threading.Event()
    real_initialize = ri.RobotInterface.initialize

    def record(robot):
        indices = np.asarray(robot.get_system().get_task_space(side).get_joint_indices())
        began = time.monotonic()
        while not stop.is_set() and time.monotonic() - began < own.record:
            state = robot.get_state()
            now = time.monotonic() - began
            samples.append((now, np.asarray(state.get_position(), dtype=float)[indices], np.asarray(state.get_velocity(), dtype=float)[indices],
                            np.asarray(state.get_torque(), dtype=float)[indices]))
            time.sleep(PERIOD)

    def initialize(self, *args, **kwargs):
        result = real_initialize(self, *args, **kwargs)
        threading.Thread(target=record, args=(self,), daemon=True).start()
        return result

    ri.RobotInterface.initialize = initialize
    print(f"torque_probe: mode {own.mode}, natural frequencies x{own.nf_scale:g}, recording {own.record:g} s")
    try:
        code = engage.main()
    finally:
        stop.set()
        time.sleep(0.05)
        if len(samples) > 3:
            t = np.array([s[0] for s in samples]); q = np.array([s[1] for s in samples]); v = np.array([s[2] for s in samples]); tau = np.array([s[3] for s in samples])
            t = t - t[0]
            path = own.csv or os.path.expanduser(f"~/torque_probe-{time.strftime('%Y%m%d-%H%M%S')}.csv")
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["t"] + [f"q{j + 1}" for j in range(q.shape[1])] + [f"qd{j + 1}" for j in range(q.shape[1])] + [f"tau{j + 1}" for j in range(q.shape[1])])
                for k in range(len(t)):
                    writer.writerow([f"{t[k]:.4f}"] + [f"{x:.5f}" for x in np.r_[q[k], v[k], tau[k]]])
            print("\n=== recorded ===")
            print(analyse(t, q, v, tau))
            print(f"raw samples: {path}")
        else:
            print("torque_probe: nothing was recorded (engage refused before the interface came up)")
    return code


if __name__ == "__main__":
    sys.exit(main())
