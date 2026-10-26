#!/usr/bin/env python3
"""Move one arm's HAND by a small offset under a gafro CONTROLLER that takes the target POSE as its input. Slice 2 of the pose controllers (velocity);
the torque `track` controller of controllers.yaml uses the same runner (slice 3, not run on the arm yet: see engage_probe.py's kick).

    python track_hand.py --controller velocity --side right --model <xml> --hardware-config <yaml> --assets <assets> --supervised --offset-cm 4,0,0

What differs from goto_hand.py: there the arm replays a straight joint-space line in the driver's POSITION mode. Here a spec-built controller (the YAML spec of
gafro.control.spec, built the way engage.py builds `track`) runs in the interface's own loop. Its target pose is a MotorReferenceSource, which this publishes: the
hand path is iLQR's (hand_plan.py: geometry only), timed by a minimum-jerk law, as a pose reference and a joint (posture) reference. `velocity` is
type: velocity (pose -> velocity, resolved through the kinematics) in the driver's VELOCITY mode: kp = 0 and the command under kd, so the joint torque is
kd times the speed error. With kd 2 a 0.3 rad/s error is 0.6 N m, about the joint friction: the arm may stall. Raise `kd` (at most 5) in a COPY of the robot's
hardware config for this run.

Every gate of goto_hand.py applies (bus, CH5, e-stop, --supervised, joint range, straight-path and size limits, twin clearance, typed `hand`, CH6 held at the start and
for every permit renewal). The arm is left enabled and stiff.
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
sys.path.insert(0, os.path.join(HERE, ".."))      # bringup.py, engage.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)

import bringup  # noqa: E402
import engage  # noqa: E402
import goto_hand  # noqa: E402
import guards  # noqa: E402
import hand_plan  # noqa: E402
import limits as robot_limits  # noqa: E402
import motion_plan  # noqa: E402
import position_play  # noqa: E402

KNOTS = 40


def velocity_controller(gain: float, linear: float, angular: float, posture_gain: float, posture_speed: float, limits: bool,
                        integral: float = 0.0, integral_limit: float = 0.02) -> dict:
    """The spec of a velocity controller with a pose reference: the hand follows `reference`, the posture stage uses the redundancy.

    `integral` > 0 makes the pose law a PID: v = gain * e + integral * (the integral of e), the integral bounded by `integral_limit` and the speed by `linear`
    (the spec takes one number for every row there, so the angular cap is the same number). A stuck joint's few-millimetre error then builds up speed until it breaks free.
    """
    if integral > 0:
        law = {"pid": {"stiffness": gain, "damping": 0.0, "integral": integral, "limits": {"integral": integral_limit, "output": linear}}}
    else:
        law = {"pd": {"gain": gain, "max_speed": {"linear": linear, "angular": angular}}}
    description = {
        "inputs": {"reference": "pose", "posture": "joints"},
        "type": "velocity",
        "regularization": 0.05,
        "stages": [
            {"name": "pose", "input": "pose", "objective": {"pose": {"target": "reference"}}, "law": law, "output": "velocity"},
            {"name": "posture", "input": "joints", "objective": {"posture": {"target": "posture"}},
             "law": {"pd": {"gain": posture_gain, "max_speed": posture_speed}}, "output": "velocity"},
        ],
    }
    if limits:
        description["limits"] = {"joint_velocity": "model"}
    return description


def reference_knots(path: np.ndarray, duration: float, knots: int = KNOTS):
    """The iLQR path (7, N+1) timed by minimum jerk: `knots`+1 joint vectors at spacing duration/knots, leaving and arriving at rest."""
    steps = np.linalg.norm(np.diff(path, axis=1), axis=0)
    cumulative = np.r_[0.0, np.cumsum(steps)]
    total = cumulative[-1] if cumulative[-1] > 0 else 1.0
    out = []
    for k in range(knots + 1):
        s = engage.minimum_jerk(k / knots) * total
        out.append(np.array([np.interp(s, cumulative, path[j]) for j in range(path.shape[0])]))
    return out


def duration_for_path(path: np.ndarray, vmax: float) -> float:
    return engage.duration_for(path[:, 0], path[:, -1], vmax)


def velocity_report(t: np.ndarray, hand_mm: np.ndarray, goal_mm: np.ndarray, duration: float, speed: np.ndarray, torque: np.ndarray) -> str:
    """What a run did, from the recorded samples. `t` (n,) seconds from the moment the reference was published; `hand_mm` (n, 3) the hand's displacement from where it started;
    `goal_mm` (3,) the displacement asked; `speed`, `torque` (n, 7) the joints'. Progress is measured along the asked direction and compared with the unshifted minimum-jerk
    reference (the intended path: a --lead shifts what the controller is shown, not what was meant)."""
    length = float(np.linalg.norm(goal_mm))
    direction = np.asarray(goal_mm, dtype=float) / length
    progress = hand_mm @ direction
    ideal = length * np.array([engage.minimum_jerk(min(max(x / duration, 0.0), 1.0)) for x in t])

    def first(mask):
        hit = np.where(mask)[0]
        return float(t[hit[0]]) if len(hit) else None

    def show(value, unit="s"):
        return "never" if value is None else f"{value:.2f} {unit}"

    moving = t <= duration + 0.5
    lag = ideal - progress
    out = [f"{len(t)} samples over {t[-1]:.2f} s; the move asked {length:.1f} mm in {duration:.1f} s (minimum jerk)",
           f"  breakaway (hand 1 mm along the move): {show(first(progress > 1.0))}   (the reference reaches 1 mm at {show(first(ideal > 1.0))})",
           f"  halfway ({length / 2:.0f} mm): {show(first(progress >= length / 2))}   (reference: {duration / 2:.2f} s)",
           f"  90 %: {show(first(progress >= 0.9 * length))}   (reference: {show(first(ideal >= 0.9 * length))})",
           f"  peak lag behind the reference while it runs: {float(lag[moving].max()):.1f} mm at {float(t[moving][lag[moving].argmax()]):.2f} s",
           f"  end: {float(progress[-1]):.1f} mm along the move, {float(progress[-1] - length):+.1f} mm from the goal; overshoot {max(0.0, float(progress.max() - length)):.1f} mm",
           "  joints, peak speed rad/s | peak measured torque N m: " + "  ".join(f"{j + 1}: {np.abs(speed[:, j]).max():.2f}|{np.abs(torque[:, j]).max():.1f}" for j in range(speed.shape[1]))]
    fast = [f"j{j + 1} {np.abs(speed[:, j]).max():.2f}" for j in range(speed.shape[1]) if np.abs(speed[:, j]).max() > VIBRATION_SPEED]
    out.append(f"  VIBRATION: joint speeds over {VIBRATION_SPEED:g} rad/s ({', '.join(fast)}): a joint was shaking, whatever the hand did" if fast
               else f"  no joint went over {VIBRATION_SPEED:g} rad/s (no vibration)")
    return "\n".join(out)


VIBRATION_SPEED = 1.0       # rad/s: no joint of a 4 cm hand move goes this fast (the runs that worked peaked at 0.25); the gain-12 run reached 3.9 on the wrist
VIBRATION_SAMPLES = 6     # consecutive samples (~12 ms) over --max-joint-speed before the guard stops the run
STALL_LAG_MM = 15.0       # the hand this far behind the reference
STALL_ADVANCE_MM = 2.0    # and advanced less than this in the last second
STALL_TORQUE = 0.8        # N m measured on some joint: pushing, not coasting. Forward runs peaked at 1.0 only while the hand kept up (lag < 10 mm)


def stall_reason(ideal_mm: float, progress_now_mm: float, progress_then_mm: float, torque_peak: float) -> str | None:
    """Why a run should stop pushing, or None. All three at once: the hand far behind its reference, not advancing, and a joint drawing real torque. That is a joint held by
    something (friction it cannot overcome, or an obstruction the model does not have, such as the cables behind the arm), and more of the same command only pushes harder."""
    lag, advanced = ideal_mm - progress_now_mm, progress_now_mm - progress_then_mm
    if lag > STALL_LAG_MM and abs(advanced) < STALL_ADVANCE_MM and torque_peak >= STALL_TORQUE:
        return (f"STALL: the hand is {lag:.0f} mm behind its reference and moved {advanced:+.1f} mm in the last second while a joint draws {torque_peak:.1f} N m: "
                "something is holding the arm (an obstruction the model does not have?). The permit is no longer renewed; the arm holds where it is.")
    return None


STUCK_ERROR = 0.03        # rad: a joint this far from its planned angle
STUCK_MOVED = 0.003       # rad: and moved less than this in the last second


def stuck_joints(target: np.ndarray, q_now: np.ndarray, q_then: np.ndarray, torque_peak: np.ndarray) -> list:
    """The joints (numbered from 1) held by something: far from their planned angle, not moving, and drawing real torque. More precise than the hand-level check, since it does
    not wait for every other joint to stop (in the blocked runs joint 7 kept the hand moving for 1.5 s while joint 1 was already pushing at 1 N m against the cables)."""
    held = (np.abs(target - q_now) > STUCK_ERROR) & (np.abs(q_now - q_then) < STUCK_MOVED) & (np.abs(torque_peak) >= STALL_TORQUE)
    return [int(j) + 1 for j in np.where(held)[0]]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--controller", choices=("velocity", "track", "position"), default="velocity", help="`velocity` is built here; `track` is the torque controller of controllers.yaml; "
                   "`position` is built here too: a posture controller that plays the planned path (not a straight line) in the driver's POSITION mode, the rate along it fed forward")
    p.add_argument("--side", choices=("left", "right"), required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
    p.add_argument("--hardware-config", default="")
    p.add_argument("--assets", default=None)
    p.add_argument("--sim-dir", default=None)
    p.add_argument("--offset-cm", required=True)
    p.add_argument("--speed", type=float, default=0.1, help="the largest joint's peak-average speed of the reference, rad/s (at most 0.2)")
    p.add_argument("--gain", type=float, default=2.0, help="velocity controller: pose error to speed, 1/s")
    p.add_argument("--hand-speed", type=float, default=0.1, help="velocity controller: the hand's speed cap, m/s")
    p.add_argument("--integral", type=float, default=0.0, help="velocity controller: integral gain (1/s^2); above 0 the pose law is a PID that pushes through stiction")
    p.add_argument("--integral-limit", type=float, default=0.02, help="velocity controller: bound of the integral of the pose error (m s)")
    p.add_argument("--lead", type=float, default=0.0, help="seconds the pose reference runs ahead of its own clock. A proportional law on a ramp lags by speed/gain; shown the reference "
                   "1/gain ahead it has no steady lag on a ramp. 0 is the plain reference")
    p.add_argument("--position-gain", type=float, default=20.0, help="position controller: the posture error to speed, 1/s, on the controller's own anchor. High is fine: there is no actuator in that loop "
                   "(without the rate fed forward the anchor lags the plan by speed / gain)")
    p.add_argument("--max-lead", type=float, default=0.02, help="position controller: how far, rad per joint, the offset may take the command from the anchor. The offset integrates the anchor's distance "
                   "from the measurement so a joint held by friction is still asked to move; the motor's PD turns it into torque (kp * lead). It must exceed friction / kp or the joint never "
                   "breaks away; the offset hunts against friction by about the lead when --offset-gain is high")
    p.add_argument("--leash", type=float, default=0.15, help="position controller: how far, rad per joint, the anchor may get from the measured position. A held joint (a cable, a collision) is "
                   "never asked for more than kp * leash beyond the plan, and the anchor does not run away from an arm that has stopped; it must exceed the tracking error the plan needs")
    p.add_argument("--offset-gain", type=float, default=2.0, help="position controller: gain of the offset on the anchor's distance from the measurement, 1/s. Low: a high gain hunts against friction")
    p.add_argument("--max-command-speed", type=float, default=0.5, help="position controller: cap, rad/s, on the speed its feedback asks for (the rate fed forward is the plan's own)")
    p.add_argument("--no-feedforward", action="store_true", help="position controller: do not feed the planned rates forward (the arm then follows rate / gain behind the plan)")
    p.add_argument("--max-joint-speed", type=float, default=1.5, help="rad/s: if any joint stays faster than this for ~12 ms the run stops renewing the permit (the arm holds): a joint that fast during a 4 cm hand move is vibrating")
    p.add_argument("--csv", default=None, help="the raw samples (default ~/track_hand-<time>.csv)")
    p.add_argument("--weight", type=int, default=10)
    p.add_argument("--limits", default=None, help="this robot's joint limits (default $OPENDUBS_ARM_LIMITS or /etc/opendubs/arm-limits/joint_limits.yaml): narrows the model's ranges, never widens them")
    p.add_argument("--max-offset-cm", type=float, default=6.0)
    p.add_argument("--max-joint-deg", type=float, default=15.0)
    p.add_argument("--max-bend-deg", type=float, default=1.0)
    p.add_argument("--min-clearance-mm", type=float, default=motion_plan.DEFAULT_MIN_CLEARANCE_MM)
    p.add_argument("--skip-clearance", action="store_true")
    p.add_argument("--no-model-limits", action="store_true", help="build the velocity controller without limits: {joint_velocity: model}")
    p.add_argument("--confirm", default=None)
    p.add_argument("--supervised", action="store_true")
    p.add_argument("--dashboard", default=guards.DASHBOARD)
    p.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS)
    args = p.parse_args(argv)

    try:
        offset = np.array([float(v) for v in args.offset_cm.split(",")]) / 100.0
        assert offset.shape == (3,)
    except (ValueError, AssertionError):
        print("refused: --offset-cm wants three numbers, dx,dy,dz")
        return 1
    if not 0 < args.speed <= 0.2:
        print("refused: --speed must be above 0 and at most 0.2 rad/s")
        return 1
    if args.controller == "track":
        print("refused: the torque `track` controller kicked the real arm (engage, 2026-10-23); it is not run until that is understood. --controller velocity only.")
        return 1
    if not 0.0 <= args.lead <= 2.0:
        print("refused: --lead must be between 0 and 2 s")
        return 1
    if bringup.gate(args, motion=True):
        return 1

    from gafro import control

    robot, indices, position = bringup.connect(args)
    keeper = bringup.keeper_for(args)
    try:
        system = robot.get_system()
        task = system.get_task_space(args.side)
        state = lambda: np.asarray(robot.get_state().get_position(), dtype=float)  # noqa: E731
        start_all = state().copy()
        start = start_all[indices]
        try:
            try:
                plan = hand_plan.plan_hand(system, task, start, offset, vmax=2.0, weight=args.weight)
            except ValueError as error:
                raise guards.Refused(str(error)) from None
            lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
            upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
            lower, upper, limits_note = robot_limits.apply(args.side, lower, upper, args.limits)
            goto_hand.check_plan(plan, lower, upper, 180.0 if args.controller == "position" else args.max_bend_deg, args.max_joint_deg, args.max_offset_cm, offset)
            closest = None
            if not args.skip_clearance:
                clearance_of = motion_plan.twin_clearance(args.side, args.assets, args.sim_dir or os.environ.get("OPENDUBS_SIM_DIR"))
                poses = (motion_plan.path_poses(plan["states"]) if args.controller == "position"
                         else motion_plan.line_poses(plan["states"][:, 0], plan["states"][:, -1]))       # the path the controller follows, not the line between its ends
                closest = motion_plan.require_clearance(poses, clearance_of, args.min_clearance_mm)
            duration = duration_for_path(plan["states"], args.speed)
            print(limits_note)
            print(goto_hand.plan_text(args.side, plan, args.speed, closest, args.min_clearance_mm).split("\nDriven in position mode")[0])
            if args.controller == "position":
                print(f"Run by the `position` controller (a spec-built posture controller in the driver's POSITION mode: the motor's own PD and the driver's gravity feed-forward), "
                      f"playing iLQR's planned path (not a straight line) timed by minimum jerk over {duration:.1f} s: anchor gain {args.position_gain:g} /s, command speed cap "
                      f"{args.max_command_speed:g} rad/s, anchor within {args.leash:g} rad of the arm, offset gain {args.offset_gain:g} /s up to {args.max_lead:g} rad, "
                      f"{'planned rate fed forward' if not args.no_feedforward else 'no rate feed-forward (follows rate / gain behind the plan)'}.\n"
                      "The head and the camera mount are not in the model: head tracking must be OFF with the neck centred, and you judge the path by eye.\n"
                      "Hold CH6 from when you type `hand` until it ends; letting go stops it within about half a second and the arm holds where it is.")
            else:
                print(f"Run by the `{args.controller}` controller (a spec-built, pose -> velocity controller in the driver's VELOCITY mode: kp = 0, the command under kd), its pose reference "
                  f"timed by minimum jerk over {duration:.1f} s, gain {args.gain:g} /s, hand speed cap {args.hand_speed:g} m/s"
                  f"{f', integral {args.integral:g} /s^2 (bound {args.integral_limit:g} m s)' if args.integral > 0 else ''}"
                  f"{f', reference shown {args.lead:g} s ahead' if args.lead > 0 else ''}. It may stall short if kd is too low for the joint friction.\n"
                  "The head and the camera mount are not in the model: head tracking must be OFF with the neck centred, and you judge the path by eye.\n"
                  "Hold CH6 from when you type `hand` until it ends; letting go stops it within about half a second and the arm holds where it is.")
            typed = args.confirm if args.confirm is not None else input("Is the arm clear for that? Type `hand` to move, anything else to stop: ").strip()
            if typed != "hand":
                raise guards.Refused("not confirmed")
        except guards.Refused as refused:
            print(f"refused: {refused}")
            print("the arm is at (deg): " + " ".join(f"{np.degrees(v):+.2f}" for v in start))
            return 1
        if not bringup.deadman_held(args):
            return 1

        controllers = engage.load_controllers()
        controllers["velocity"] = velocity_controller(args.gain, args.hand_speed, 0.5, 1.0, 0.3, not args.no_model_limits, args.integral, args.integral_limit)
        pose0 = task.compute_ee_motor(start)
        posture = control.JointReferenceSource(start_all.copy(), horizon=0.5)
        posture.set_staleness(control.Staleness.Hold)
        posture.prime(2)
        reference = control.MotorReferenceSource(pose0, horizon=0.5)
        reference.set_staleness(control.Staleness.Hold)
        reference.prime(2)

        keeper.start()
        rates = None
        if args.controller == "position":
            posture, rates = position_play.start(robot, system, args.side, start_all, pose0, args.position_gain, args.max_lead, args.max_command_speed, not args.no_feedforward,
                                                  args.leash, args.offset_gain)
        else:
            robot.control(engage.build(args.controller, controllers, system, args.side, start_all, pose0, reference, posture), False)
        time.sleep(0.5)                                          # holding where it is before anything moves

        q_ref = reference_knots(plan["states"], duration)
        motors, joints = [], []
        for q in q_ref:
            motors.append(task.compute_ee_motor(q))
            full = start_all.copy()
            full[indices] = q
            joints.append(full)
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
                    keeper.stop()                                        # idempotent; the arm holds from the next cycle
                time.sleep(0.002)

        began = time.time()
        now = control.monotonic_now() - args.lead                # a lead starts the chunk in the past: the controller is shown the reference `lead` seconds ahead
        threading.Thread(target=record, daemon=True).start()
        if args.controller == "position":
            position_play.publish(posture, rates, now, duration / KNOTS, joints, position_play.rates_from_knots(np.array(q_ref), duration / KNOTS))
        else:
            reference.publish(control.MotorReferenceChunk(now, duration / KNOTS, motors))     # the spacing between knots, not the length of the chunk
            posture.publish(control.JointReferenceChunk(now, duration / KNOTS, joints))
        move_mm = (plan["goal"][:3] - plan["start"][:3]) * 1000
        direction = move_mm / np.linalg.norm(move_mm)
        stalled = None
        while time.time() - began < duration + 1.5:
            time.sleep(0.5)
            hand = hand_plan.hand_pose(task, state()[indices])
            elapsed = time.time() - began
            print(f"  [+{elapsed:4.1f}] hand from start {np.round((hand[:3] - plan['start'][:3]) * 1000, 1)} mm | farthest joint from its target "
                  f"{np.abs(state()[indices] - plan['states'][:, -1]).max():.3f} rad", flush=True)
            if vibrating["joints"] and not stalled:
                stalled = (f"VIBRATION: joint(s) {vibrating['joints']} exceeded {args.max_joint_speed:g} rad/s for ~{VIBRATION_SAMPLES * 2} ms. The permit is no longer renewed; the arm holds where it is. "
                           "Lower the gain or the wrist kd and try again.")
                print(stalled, flush=True)
                break
            recent = [x for x in list(samples) if x[0] - began > elapsed - 1.0]
            if len(recent) > 50:
                now_mm = float((hand[:3] - plan["start"][:3]) * 1000 @ direction)
                then_mm = float((hand_plan.hand_pose(task, recent[0][1])[:3] - plan["start"][:3]) * 1000 @ direction)
                ideal_mm = float(np.linalg.norm(move_mm) * engage.minimum_jerk(min(max(elapsed / duration, 0.0), 1.0)))
                stalled = stall_reason(ideal_mm, now_mm, then_mm, max(float(np.abs(x[3]).max()) for x in recent))
                held = stuck_joints(plan["states"][:, -1], recent[-1][1], recent[0][1], np.abs(np.array([x[3] for x in recent])).max(axis=0))
                if held and not stalled:
                    target_q = plan["states"][:, -1]
                    gaps = ", ".join("%.2f" % abs(target_q[j - 1] - recent[-1][1][j - 1]) for j in held)
                    stalled = (f"STALL: joint(s) {held} are held {gaps} rad from their planned angle, not moving, and drawing torque: something is holding the arm "
                               "(an obstruction the model does not have?). The permit is no longer renewed; the arm holds where it is.")
                if stalled:
                    print(stalled, flush=True)
                    keeper.stop()
                    break
        recording.set()
        time.sleep(0.02)
        end_q = state()[indices]
        hand = hand_plan.hand_pose(task, end_q)
        error = (hand[:3] - plan["goal"][:3]) * 1000
        joint_error = float(np.abs(end_q - plan["states"][:, -1]).max())
        print(f"the hand moved {np.round((hand[:3] - plan['start'][:3]) * 1000, 1)} mm (asked {np.round((plan['goal'][:3] - plan['start'][:3]) * 1000, 1)}); "
              f"{np.round(error, 1)} mm from the target, {joint_error:.4f} rad from the planned joints")
        if len(samples) > 10:
            ts = np.array([x[0] for x in samples]) - began
            qs = np.array([x[1] for x in samples])
            hand_mm = np.array([(hand_plan.hand_pose(task, q)[:3] - plan["start"][:3]) * 1000 for q in qs])
            speeds, torques = np.array([x[2] for x in samples]), np.array([x[3] for x in samples])
            print("\n=== recorded ===")
            print(velocity_report(ts, hand_mm, (plan["goal"][:3] - plan["start"][:3]) * 1000, duration, speeds, torques))
            path = args.csv or os.path.expanduser(f"~/track_hand-{time.strftime('%Y%m%d-%H%M%S')}.csv")
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["t", "hand_x_mm", "hand_y_mm", "hand_z_mm"] + [f"q{j + 1}" for j in range(7)] + [f"qd{j + 1}" for j in range(7)] + [f"tau{j + 1}" for j in range(7)])
                for k in range(len(ts)):
                    writer.writerow([f"{ts[k]:.4f}"] + [f"{x:.3f}" for x in hand_mm[k]] + [f"{x:.5f}" for x in np.r_[qs[k], speeds[k], torques[k]]])
            print(f"raw samples: {path}\n")
        ok = np.linalg.norm(error) <= goto_hand.HAND_TOLERANCE_MM and joint_error <= goto_hand.JOINT_TOLERANCE and not vibrating["joints"] and not stalled
        if vibrating["joints"] or stalled:
            return bringup.verdict(False, "the run was stopped: " + (stalled or "vibration").split(":")[0].lower() + " (see above)")
        return bringup.verdict(ok, "hand within tolerance under the velocity controller" if ok else
                               f"hand {np.linalg.norm(error):.1f} mm from the target, joints {joint_error:.3f} rad from the plan (a stall: kd too low for the friction, or CH6 released, or a trip)")
    finally:
        keeper.stop()
        robot.shutdown()


if __name__ == "__main__":
    sys.exit(main())
