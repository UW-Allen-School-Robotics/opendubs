#!/usr/bin/env python3
"""Play one arm's part of a choreography show through the driver and gafro, in POSITION mode, without ROS.

    python play_show.py --show software/choreo/shows/shrug.yaml --side right --model <xml> --hardware-config <yaml> --assets <openarm_assets> --supervised --slow 2

What the choreography runner sends the real arm as one ROS goal (`arm_output.track_points`: the show's keyframes, each reached at its time along the smoothed path of `motion.py`)
is played here by a gafro position controller (position_play.py) that is given the WHOLE path at once and its analytic rate. The motor's own PD and the driver's gravity feed-forward
close the error, as under the choreography stack's impedance controller. The arm's part only: the show's other slots (head, face, grippers, sound, the base) are not played.

Gates, in addition to every gate of goto_hand.py (bus, CH5, e-stop, --supervised, per-robot limits, CH6 held at the start and for every permit renewal, the twin clearance --
here along the whole path -- and a typed confirmation):
  * the show must validate against the arm software's joint limits (the same file the choreography validator reads);
  * the arm must start where the show's path starts: zero, the stow pose, within --start-tolerance-deg (the runner moves it there first; here `goto_stow.py` does);
  * the path must stay in the joint ranges and under --max-speed, which --slow stretches in time;
  * while it runs, the run stops (the arm holds) if the tracking error passes --max-error, a joint's measured torque reaches --max-torque, or a joint is vibrating.
The angles are the choreography's own `pose_signed` (the left arm's mirrored joints already flipped), taken as this model's joint angles: compare the first run in the simulator against the twin.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))       # bringup.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)
CHOREO = os.path.normpath(os.path.join(HERE, "..", "..", "..", "choreo"))

import bringup  # noqa: E402
import guards  # noqa: E402
import limits as robot_limits  # noqa: E402
import motion_plan  # noqa: E402
import position_play  # noqa: E402

PERIOD = 0.02                  # s between the knots handed to the controller: what the choreography runner samples its own path at
LEAD_IN = 0.5                  # s between publishing the path and its first knot: the controller holds where it is until then
VIBRATION_SAMPLES = 6
SLOTS = {"left": "ARM_L", "right": "ARM_R"}


def sample_path(keyframes: list, slow: float = 1.0, period: float = PERIOD):
    """The show's arm path as (times (K,), joints (K, 7) rad, rates (K, 7) rad/s), sampled every `period` seconds of the (stretched) timeline.

    `keyframes` are the export's arm events ({start, t, pose_signed}); the path is `motion.py`'s, which starts at zero. `slow` >= 1 stretches it in time, and the rates with it.
    """
    if slow < 1.0:
        raise ValueError("--slow stretches the show in time: 1 is as written, and faster than written is not played")
    sys.path.insert(0, CHOREO)
    import motion

    path = motion.Path(motion.knots(sorted(keyframes, key=lambda k: k["t"]), "pose_signed"))
    duration = path.end * slow
    times = np.arange(0.0, duration + 1e-9, period)
    if duration - times[-1] > 1e-6:
        times = np.append(times, duration)
    joints = np.radians(np.array([path.at(t / slow) for t in times]))
    rates = np.radians(np.array([path.speed(t / slow) for t in times])) / slow
    return times, joints, rates


def tracking_error(times, reference, elapsed: float, measured) -> float:
    """The largest joint's distance from where the path is `elapsed` seconds in (held at its ends)."""
    wanted = np.array([np.interp(elapsed, times, reference[:, j]) for j in range(reference.shape[1])])
    return float(np.abs(np.asarray(measured, dtype=float) - wanted).max())


def start_refusal(measured, first, tolerance_deg: float):
    """Why the arm cannot start the show from where it is, or None: the path begins at `first` and the arm is not there."""
    away = np.degrees(np.abs(np.asarray(measured, dtype=float) - np.asarray(first, dtype=float)))
    if away.max() > tolerance_deg:
        return (f"the arm is {away.max():.1f} deg from where the show's path starts (joint {int(away.argmax()) + 1}; limit {tolerance_deg:g}): "
                "bring it there first (goto_stow.py, for a show that starts at zero)")
    return None


def path_refusal(joints, rates, lower, upper, max_speed: float):
    """Why the path cannot be played, or None: a joint leaves its range, or the path is faster than `max_speed` rad/s."""
    below, above = np.where(joints < np.asarray(lower)[None, :] - 1e-9), np.where(joints > np.asarray(upper)[None, :] + 1e-9)
    for found in (below, above):
        if found[0].size:
            return f"the path leaves joint {int(found[1][0]) + 1}'s range (at {found[0][0] * PERIOD:.2f} s)"
    fastest = np.abs(rates).max(axis=0)
    if fastest.max() > max_speed:
        return f"joint {int(fastest.argmax()) + 1} would move at {fastest.max():.2f} rad/s; the limit is {max_speed:g} (stretch the show with --slow)"
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--show", required=True, help="a choreography show (software/choreo/shows/*.yaml)")
    p.add_argument("--side", choices=("left", "right"), required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
    p.add_argument("--hardware-config", default="")
    p.add_argument("--assets", default=None)
    p.add_argument("--sim-dir", default=None)
    p.add_argument("--slow", type=float, default=2.0, help="stretch the show in time by this factor (1 is as written; the default plays it at half speed)")
    p.add_argument("--max-speed", type=float, default=1.3, help="rad/s: refuse a path faster than this on any joint, after --slow")
    p.add_argument("--start-tolerance-deg", type=float, default=3.0)
    p.add_argument("--gain", type=float, default=20.0, help="the posture error to speed, 1/s, on the controller's own anchor: high is fine, there is no actuator in that loop. "
                   "The plan's own rate is fed forward, so this only corrects the anchor's drift from the show")
    p.add_argument("--max-lead", type=float, default=0.02, help="rad per joint: how far the offset may take the command from the anchor. The offset closes what the actuator did not do and "
                   "must exceed friction / kp of the joint or it never breaks away; it hunts against friction by about the lead when --offset-gain is high")
    p.add_argument("--leash", type=float, default=0.15, help="rad per joint: how far the anchor may get from the measured position; a held joint is never asked for more than kp * leash beyond the show")
    p.add_argument("--offset-gain", type=float, default=2.0, help="1/s: gain of the offset on the anchor's distance from the measurement")
    p.add_argument("--max-command-speed", type=float, default=0.5, help="rad/s: cap on the speed the controller's feedback asks for")
    p.add_argument("--no-feedforward", action="store_true")
    p.add_argument("--max-error", type=float, default=0.2, help="rad: stop (the arm holds) if any joint is further than this from the path")
    p.add_argument("--tolerance", type=float, default=0.08, help="rad: the largest tracking error a PASS may have")
    p.add_argument("--max-torque", type=float, default=12.0, help="N m: stop if any joint's measured torque reaches this")
    p.add_argument("--max-joint-speed", type=float, default=2.5, help="rad/s: a joint faster than this for ~12 ms is vibrating: stop")
    p.add_argument("--limits", default=None, help="this robot's joint limits (default $OPENDUBS_ARM_LIMITS or /etc/opendubs/arm-limits/joint_limits.yaml): the show is validated against "
                   "them and the model's ranges are narrowed by them")
    p.add_argument("--min-clearance-mm", type=float, default=motion_plan.DEFAULT_MIN_CLEARANCE_MM)
    p.add_argument("--skip-clearance", action="store_true")
    p.add_argument("--csv", default=None, help="the raw samples (default ~/play_show-<time>.csv)")
    p.add_argument("--confirm", default=None, help="`show`, for a script that has read the plan")
    p.add_argument("--supervised", action="store_true")
    p.add_argument("--dashboard", default=guards.DASHBOARD)
    p.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS)
    args = p.parse_args(argv)

    limits_path = args.limits or robot_limits.DEFAULT
    if not os.path.exists(limits_path):
        print(f"refused: no joint-limits file at {limits_path}: the show cannot be validated without the arm software's limits (--limits, or $OPENDUBS_ARM_LIMITS)")
        return 1
    sys.path.insert(0, CHOREO)
    import cue

    slot = SLOTS[args.side]
    try:
        show = cue.load_show(args.show)
        timeline = show.export(cue.load_limits(limits_path))
    except Exception as error:                                   # a show that does not even parse is refused with the reason, not a traceback
        print(f"refused: the show does not load: {error}")
        return 1
    if not timeline["valid"]:
        print("refused: the show does not validate: " + "; ".join(q["msg"] for q in timeline["problems"][:3]))
        return 1
    keyframes = [e for e in timeline["tracks"].get(slot, []) if "pose_signed" in e]
    if not keyframes:
        print(f"refused: the show has no {slot} arm moves to play")
        return 1
    try:
        times, joints, rates = sample_path(keyframes, args.slow)
    except ValueError as error:
        print(f"refused: {error}")
        return 1
    if not 0 < args.max_command_speed:
        print("refused: --max-command-speed must be positive")
        return 1
    if bringup.gate(args, motion=True):
        return 1

    robot, indices, position = bringup.connect(args)
    keeper = bringup.keeper_for(args)
    try:
        system = robot.get_system()
        task = system.get_task_space(args.side)
        start_all = np.asarray(robot.get_state().get_position(), dtype=float).copy()
        start = start_all[indices]
        try:
            lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
            upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
            lower, upper, limits_note = robot_limits.apply(args.side, lower, upper, limits_path)
            why = start_refusal(start, joints[0], args.start_tolerance_deg) or path_refusal(joints, rates, lower, upper, args.max_speed)
            if why:
                raise guards.Refused(why)
            closest = None
            if not args.skip_clearance:
                clearance_of = motion_plan.twin_clearance(args.side, args.assets, args.sim_dir or os.environ.get("OPENDUBS_SIM_DIR"))
                closest = motion_plan.require_clearance(list(joints[:: max(1, int(0.1 / PERIOD))]) + [joints[-1]], clearance_of, args.min_clearance_mm)
            print(limits_note)
            span = np.degrees(joints.max(axis=0) - joints.min(axis=0))
            print(f"Show `{timeline['show']['id']}`, the {slot} arm: {times[-1]:.1f} s ({args.slow:g}x slower than written), {len(times)} knots every {PERIOD * 1000:g} ms.")
            print("  joint range travelled (deg): " + "  ".join(f"{j + 1}: {span[j]:.0f}" for j in range(7)))
            print("  peak speed (rad/s):          " + "  ".join(f"{j + 1}: {np.abs(rates[:, j]).max():.2f}" for j in range(7)))
            if closest:
                print(f"Closest approach along the whole path in the lane's twin: {closest[0]:.1f} mm ({closest[1]}); refused below {args.min_clearance_mm:g} mm. "
                      "The twin was 6 deg optimistic on the one pose measured on the robot.")
            print(f"Played by a position controller (the motor's PD and the driver's gravity feed-forward): anchor gain {args.gain:g} /s, anchor within {args.leash:g} rad of the arm, "
                  f"offset gain {args.offset_gain:g} /s up to {args.max_lead:g} rad, "
                  f"{'planned rate fed forward' if not args.no_feedforward else 'no rate feed-forward'}. Only this arm; the show's other slots are not played.\n"
                  "The head and the camera mount are not in the model: head tracking must be OFF with the neck centred, and you judge the path by eye.\n"
                  "Hold CH6 from when you type `show` until it ends; letting go stops it within about half a second and the arm holds where it is.")
            typed = args.confirm if args.confirm is not None else input("Is the arm clear for that? Type `show` to move, anything else to stop: ").strip()
            if typed != "show":
                raise guards.Refused("not confirmed")
        except guards.Refused as refused:
            print(f"refused: {refused}")
            print("the arm is at (deg): " + " ".join(f"{np.degrees(v):+.2f}" for v in start))
            return 1
        if not bringup.deadman_held(args):
            return 1

        from gafro import control

        full = np.tile(start_all, (len(times), 1))
        full[:, indices] = joints
        pose0 = task.compute_ee_motor(start)
        samples, recording = [], threading.Event()
        vibrating = {"run": 0, "joints": []}

        def record():
            while not recording.is_set():
                s = robot.get_state()
                v = np.asarray(s.get_velocity(), dtype=float)[indices]
                samples.append((time.time(), np.asarray(s.get_position(), dtype=float)[indices], v, np.asarray(s.get_torque(), dtype=float)[indices]))
                over = np.abs(v) > args.max_joint_speed
                vibrating["run"] = vibrating["run"] + 1 if over.any() else 0
                if vibrating["run"] >= VIBRATION_SAMPLES and not vibrating["joints"]:
                    vibrating["joints"] = [int(j) + 1 for j in np.where(over)[0]]
                    keeper.stop()
                time.sleep(0.002)

        keeper.start()
        posture, rate_source = position_play.start(robot, system, args.side, start_all, pose0, args.gain, args.max_lead, args.max_command_speed, not args.no_feedforward, args.leash,
                                                   args.offset_gain)
        time.sleep(0.5)                                          # holding where it is before anything moves
        begin_clock = control.monotonic_now() + LEAD_IN
        began = time.time() + LEAD_IN
        threading.Thread(target=record, daemon=True).start()
        position_play.publish(posture, rate_source, begin_clock, PERIOD, full, rates if rate_source is not None else None)

        stopped = None
        worst = 0.0
        while time.time() - began < times[-1] + 1.5:
            time.sleep(0.5)
            elapsed = time.time() - began
            q = position()
            error = tracking_error(times, joints, elapsed, q) if elapsed > 0 else 0.0
            worst = max(worst, error)
            recent = [x[3] for x in list(samples) if x[0] > time.time() - 0.5]
            peak = np.abs(np.array(recent)).max(axis=0) if recent else np.zeros(7)
            print(f"  [{elapsed:+5.1f}] tracking error {error:.3f} rad | largest torque {peak.max():.1f} N m (joint {int(peak.argmax()) + 1})", flush=True)
            if vibrating["joints"]:
                stopped = f"VIBRATION: joint(s) {vibrating['joints']} exceeded {args.max_joint_speed:g} rad/s. The permit is no longer renewed; the arm holds where it is."
            elif error > args.max_error:
                stopped = f"TRACKING: a joint is {error:.2f} rad from the path (--max-error {args.max_error:g}). The permit is no longer renewed; the arm holds where it is."
            elif peak.max() >= args.max_torque:
                stopped = f"FORCE LIMIT: joint {int(peak.argmax()) + 1} reached {args.max_torque:g} N m. The permit is no longer renewed; the arm holds where it is."
            if stopped:
                print(stopped, flush=True)
                keeper.stop()
                break
        recording.set()
        time.sleep(0.02)

        end_q = position()
        final = float(np.abs(end_q - joints[-1]).max())
        print(f"largest tracking error {worst:.3f} rad; {final:.4f} rad from the path's end")
        if len(samples) > 10:
            ts = np.array([x[0] for x in samples]) - began
            qs = np.array([x[1] for x in samples])
            errors = np.array([tracking_error(times, joints, t, q) for t, q in zip(ts, qs)])
            moving = errors[(ts > 0) & (ts < times[-1])]
            if moving.size:
                print(f"while moving: mean {moving.mean():.3f} rad, 95th percentile {np.percentile(moving, 95):.3f} rad, largest {moving.max():.3f} rad")
            path = args.csv or os.path.expanduser(f"~/play_show-{time.strftime('%Y%m%d-%H%M%S')}.csv")
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["t"] + [f"q{j + 1}" for j in range(7)] + [f"qd{j + 1}" for j in range(7)] + [f"tau{j + 1}" for j in range(7)] + ["error"])
                for k in range(len(ts)):
                    writer.writerow([f"{ts[k]:.4f}"] + [f"{x:.5f}" for x in np.r_[qs[k], samples[k][2], samples[k][3]]] + [f"{errors[k]:.5f}"])
            print(f"raw samples: {path}")
        if stopped:
            return bringup.verdict(False, "the run was stopped: " + stopped.split(":")[0].lower() + " (see above)")
        ok = worst <= args.tolerance and final <= 0.03
        return bringup.verdict(ok, f"the arm followed the show within {args.tolerance:g} rad" if ok else
                               f"largest tracking error {worst:.3f} rad (limit {args.tolerance:g}), {final:.3f} rad from the end (limit 0.03)")
    finally:
        keeper.stop()
        robot.shutdown()


if __name__ == "__main__":
    sys.exit(main())
