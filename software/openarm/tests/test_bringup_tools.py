#!/usr/bin/env python3
"""The bring-up tools of software/openarm/gafro/tools: what each refuses, that each puts its gates in the right order before anything is commanded, and that the
torque probe's read-out finds a kick. No arm, no simulator, no bus: the gates are checked by what the tools refuse and by where their calls sit in the source.
Needs gafro (the tools import it); without it this prints SKIP and exits 0, as test_gafro_guards.py does for its engage case.
Run: python3 software/openarm/tests/test_bringup_tools.py
"""
import contextlib
import importlib.util
import inspect
import io
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "gafro", "tools")
sys.path.insert(0, os.path.join(HERE, "..", "gafro"))
sys.path.insert(0, TOOLS)

if importlib.util.find_spec("gafro") is None:
    print("SKIP: gafro is not importable here")
    sys.exit(0)

import goto_hand  # noqa: E402
import goto_stow  # noqa: E402
import make_config  # noqa: E402
import motion_plan  # noqa: E402
import play_show  # noqa: E402
import position_play  # noqa: E402
import track_hand  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def refuses(fn, *words):
    try:
        fn()
    except goto_stow.guards.Refused as refused:
        return all(w in str(refused) for w in words)
    return None


def run(main, *argv):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = main(list(argv))
    return code, out.getvalue()


def in_order(function, *calls):
    source = inspect.getsource(function)
    positions = [source.find(call) for call in calls]
    return positions == sorted(positions) and min(positions) >= 0


# -- goto_stow ---------------------------------------------------------------------------------------------------------------------------------
check("goto_stow: a start 30 deg from stow refuses, naming the joint", refuses(lambda: goto_stow.check_distance(np.radians([0, 0, 0, 30, 0, 0, 0]), np.zeros(7), 25), "joint 4", "30.0"), True)
check("goto_stow: 10 deg is allowed", goto_stow.check_distance(np.radians([0, 0, 0, 10, 0, 0, 0]), np.zeros(7), 25), None)
base = ["--side", "right", "--model", "m.xml"]
check("goto_stow: a speed over 0.3 refuses", run(goto_stow.main, *base, "--speed", "0.5")[0], 1)
check("goto_stow: no stow file refuses and does not guess", [run(goto_stow.main, *base, "--stow-conf", "/nonexistent")[0], "not guessed" in run(goto_stow.main, *base, "--stow-conf", "/nonexistent")[1]], [1, True])
check("goto_stow: gate, connect, clearance, typed `stow`, deadman, first permit",
      in_order(goto_stow.main, "bringup.gate(", "bringup.connect(", "require_clearance(", 'typed != "stow"', "bringup.deadman_held(", "keeper.start()"), True)

# -- goto_hand ---------------------------------------------------------------------------------------------------------------------------------
line = np.linspace(0, 1, 31)[None, :] * np.radians([5, 0, 0, 0, 0, 0, -5])[:, None]
bent = line.copy()
bent[1, 15] = np.radians(3)
lo, hi = -np.ones(7) * 2, np.ones(7) * 2
check("goto_hand: a path 3 deg off the straight line reads as 3 deg", round(goto_hand.path_bend_deg(bent), 2), 3.0)
check("goto_hand: a good plan passes", goto_hand.check_plan(dict(states=line), lo, hi, 1.0), None)
check("goto_hand: a large move is not refused for its size", goto_hand.check_plan(dict(states=line * 5), lo * 10, hi * 10, 1.0), None)
check("goto_hand: a joint leaving its range refuses", refuses(lambda: goto_hand.check_plan(dict(states=line), lo * 0.01, hi * 0.01, 1.0), "range"), True)
check("goto_hand: a bent path refuses", refuses(lambda: goto_hand.check_plan(dict(states=bent), lo, hi, 1.0), "bends", "3.0"), True)
check("goto_hand: a bad offset refuses", run(goto_hand.main, *base, "--offset-cm", "3,0")[0], 1)
check("goto_hand: gate, connect, plan, checks, clearance, typed `hand`, deadman, first permit",
      in_order(goto_hand.main, "bringup.gate(", "bringup.connect(", "hand_plan.plan_hand(", "check_plan(", "require_clearance(", 'typed != "hand"', "bringup.deadman_held(", "keeper.start()"), True)

check("goto_hand: the force cap names the joints that reached it, and only those", [goto_hand.over_torque(np.array([12.0, 3.0, 0, 0, 0, 0, 12.5]), 12.0), goto_hand.over_torque(np.array([10.8, 3.0, 0, 0, 0, 0, 1.0]), 12.0)], [[1, 7], []])
check("goto_hand: a pull in either direction counts (the cap is on the magnitude)", goto_hand.over_torque(np.array([-13.0, 0, 0, 0, 0, 0, 0]), 12.0), [1])

# -- track_hand --------------------------------------------------------------------------------------------------------------------------------
knots = track_hand.reference_knots(line, 2.0)
steps = np.linalg.norm(np.diff(np.array(knots), axis=0), axis=1)
check("track_hand: the reference leaves and arrives at rest", [np.allclose(knots[0], line[:, 0]), np.allclose(knots[-1], line[:, -1]), bool(steps[0] < steps[20] and steps[-1] < steps[20])], [True, True, True])
check("track_hand: no integral keeps the pd law, an integral makes a pid", [list(track_hand.velocity_controller(2, 0.15, 0.5, 1, 0.3, True)["stages"][0]["law"]),
      list(track_hand.velocity_controller(2, 0.15, 0.5, 1, 0.3, True, 5.0)["stages"][0]["law"])], [["pd"], ["pid"]])
check("track_hand: gate, connect, plan, checks, clearance, typed, deadman, first permit, controller",
      in_order(track_hand.main, "bringup.gate(", "bringup.connect(", "hand_plan.plan_hand(", "check_plan(", "require_clearance(", 'typed != "hand"', "bringup.deadman_held(", "keeper.start()", "robot.control("), True)

check("track_hand: a --lead outside 0..2 s refuses", run(track_hand.main, *base, "--offset-cm", "3,0,0", "--lead", "5")[0], 1)
duration, length = 2.0, 40.0
ts = np.arange(0, 4.0, 0.004)
ideal = length * np.array([track_hand.engage.minimum_jerk(min(max(x / duration, 0.0), 1.0)) for x in ts])
late = length * np.array([track_hand.engage.minimum_jerk(min(max((x - 0.5) / duration, 0.0), 1.0)) for x in ts])      # the same move, half a second late
direction = np.array([1.0, 0.0, 0.0])
report = track_hand.velocity_report(ts, late[:, None] * direction[None, :], np.array([length, 0.0, 0.0]), duration, np.zeros((len(ts), 7)), np.zeros((len(ts), 7)))
check("track_hand: the report of a move half a second late says halfway came at 1.50 s (reference 1.00 s) and no overshoot", ["halfway (20 mm): 1.50 s" in report, "reference: 1.00 s" in report, "overshoot 0.0 mm" in report], [True, True, True])
over = np.minimum(ideal * 1.0, length) + np.where(ts > 2.0, 14.0 * (1 - np.exp(-(ts - 2.0) * 5)), 0.0)
report = track_hand.velocity_report(ts, over[:, None] * direction[None, :], np.array([length, 0.0, 0.0]), duration, np.zeros((len(ts), 7)), np.zeros((len(ts), 7)))
check("track_hand: the report finds a 14 mm overshoot", "overshoot 13.9 mm" in report or "overshoot 14.0 mm" in report, True)
stuck = np.zeros(len(ts))
report = track_hand.velocity_report(ts, stuck[:, None] * direction[None, :], np.array([length, 0.0, 0.0]), duration, np.zeros((len(ts), 7)), np.zeros((len(ts), 7)))
check("track_hand: a move that never starts says breakaway never", "breakaway (hand 1 mm along the move): never" in report, True)

# -- the model's joint limits and the stall check (the cables behind the right arm, 2026-10-23) ---------------------------------------------------------------
import limits as robot_limits  # noqa: E402

class _Joint:
    def __init__(self, name, index, actuated):
        self.name, self.index, self.actuated = name, index, actuated

    def get_name(self):
        return self.name

    def get_index(self):
        return self.index

    def is_actuated(self):
        return self.actuated


class _Limits:
    def __init__(self, values):
        self.values = values

    def coefficients(self):
        return np.array(self.values)


class _System:
    """What of_model reads: joints with an index into the limit vectors, one of them not actuated."""

    def get_joints(self):
        return [_Joint("a", 0, True), _Joint("fixed", 1, False), _Joint("b", 2, True)]

    def get_joint_limits_min(self):
        return _Limits([-1.0, -9.0, -2.0])

    def get_joint_limits_max(self):
        return _Limits([1.0, 9.0, 2.5])


check("limits: of_model reads each actuated joint's range by its index and skips the others", robot_limits.of_model(_System()), {"a": (-1.0, 1.0), "b": (-2.0, 2.5)})
lo, hi = np.array([0.0] + [-1.396] * 6), np.array([3.49] * 7)      # a model whose right joint 1 stops at the cables
backward = np.linspace(0, 1, 31)[None, :] * np.radians([-5, 0, 0, 0, 0, 0, 5])[:, None]
check("goto_hand: a path backward past a model limit on joint 1 refuses", refuses(lambda: goto_hand.check_plan(dict(states=backward), lo, hi, 1.0), "range"), True)

check("track_hand: the cable case (hand 20 mm behind, 0 mm advanced, 0.83 N m) stalls", track_hand.stall_reason(20.0, -0.8, -0.8, 0.83) is not None, True)
check("track_hand: a forward move keeping up (lag 8 mm, 1.0 N m) does not stall", track_hand.stall_reason(30.0, 22.0, 12.0, 1.0), None)
check("track_hand: the dead time before breakaway (lag 6 mm, no torque) does not stall", track_hand.stall_reason(6.0, 0.0, 0.0, 0.1), None)
check("track_hand: far behind but advancing 10 mm a second does not stall", track_hand.stall_reason(40.0, 15.0, 5.0, 1.2), None)

target = np.radians([-5.2, 0, 0, 0, 0, 0, 5.2])
held = track_hand.stuck_joints(target, np.array([-0.0008, 0, 0, 0, 0, 0, 0.06]), np.array([-0.0007, 0, 0, 0, 0, 0, 0.03]), np.array([0.83, 0.1, 0, 0.1, 0, 0.1, 0.2]))
check("track_hand: joint 1 held at 0.8 mrad of -90 mrad with 0.83 N m is stuck, joint 7 (still moving) is not", held, [1])
check("track_hand: a joint at rest at its target under load is not stuck (the forward runs held +0.99 N m at the goal)",
      track_hand.stuck_joints(np.array([0.09] + [0.0] * 6), np.array([0.089] + [0.0] * 6), np.array([0.089] + [0.0] * 6), np.array([0.99] + [0.0] * 6)), [])
check("track_hand: a joint in the dead time before breakaway (little torque) is not stuck",
      track_hand.stuck_joints(np.array([0.09] + [0.0] * 6), np.array([0.0] * 7), np.array([0.0] * 7), np.array([0.17] + [0.0] * 6)), [])

quiet_speed = np.full((len(ts), 7), 0.05)
loud_speed = quiet_speed.copy(); loud_speed[700:760, 4] = 3.5
r_quiet = track_hand.velocity_report(ts, ideal[:, None] * direction[None, :], np.array([length, 0.0, 0.0]), duration, quiet_speed, np.zeros((len(ts), 7)))
r_loud = track_hand.velocity_report(ts, ideal[:, None] * direction[None, :], np.array([length, 0.0, 0.0]), duration, loud_speed, np.zeros((len(ts), 7)))
check("track_hand: the report calls a 3.5 rad/s wrist a vibration and a quiet run quiet", ["no vibration" in r_quiet, "VIBRATION" in r_loud and "j5 3.50" in r_loud], [True, True])
check("track_hand: the vibration guard's limit is an option", "--max-joint-speed" in inspect.getsource(track_hand.main), True)

# -- make_config -------------------------------------------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    base_yaml, out_yaml = os.path.join(tmp, "base.yaml"), os.path.join(tmp, "out.yaml")
    with open(base_yaml, "w") as handle:
        handle.write("left_arm:\n  driver: openarm\n  bus: fake\nright_arm:\n  driver: openarm\n  bus: can\n  kd: [2, 2, 1.5, 1.5, 0.5, 0.5, 0.5]\n")
    before = open(base_yaml).read()
    check("make_config: a good change is written to the copy", run(make_config.main, "--base", base_yaml, "--out", out_yaml, "--set", "right_arm.torque_kd=0.5")[0], 0)
    check("make_config: the base is never written", open(base_yaml).read(), before)
    check("make_config: a key the driver does not know refuses", run(make_config.main, "--base", base_yaml, "--out", out_yaml, "--set", "right_arm.torq_kd=1")[0], 1)
    check("make_config: writing over the base refuses", run(make_config.main, "--base", base_yaml, "--out", base_yaml)[0], 1)
    check("make_config: a group the base lacks refuses", run(make_config.main, "--base", base_yaml, "--out", out_yaml, "--set", "middle_arm.kd=1")[0], 1)

# -- position_play, play_show ------------------------------------------------------------------------------------------------------------------
spec = position_play.position_controller(10.0, 0.03, 0.5)
check("position_play: the spec is a position controller with a lead, a leash and an offset gain, one posture stage, a joint-rates input and the model's velocity limit",
      [spec["type"], spec["maximum_lead"], spec["maximum_error"], spec["offset_gain"], [s["name"] for s in spec["stages"]], spec["inputs"], spec["limits"], spec["stages"][0]["law"]["pd"]["feedforward"]],
      ["position", 0.03, 0.15, 2.0, ["posture"], {"posture": "joints", "posture_rate": "joint_rates"}, {"joint_velocity": "model"}, "posture_rate"])
check("position_play: the leash and the offset gain are the caller's", [position_play.position_controller(5.0, 0.02, 0.5, maximum_error=0.3, offset_gain=4.0)[k] for k in ("maximum_error", "offset_gain")], [0.3, 4.0])
check("position_play: with nothing named the numbers are controllers.yaml's `follow_path`, which are the ones the path player always had",
      position_play.settings(), (20.0, 0.02, 0.5, 0.15, 2.0))
check("position_play: a number named replaces the yaml's alone", position_play.settings(gain=5.0), (5.0, 0.02, 0.5, 0.15, 2.0))
check("position_play: naming a number does not change the yaml's own spec for the next caller", [position_play.position_controller(1.0)["stages"][0]["law"]["pd"]["gain"],
      position_play.position_controller()["stages"][0]["law"]["pd"]["gain"]], [1.0, 20.0])
plain = position_play.position_controller(10.0, 0.03, 0.5, feedforward=False)
check("position_play: without feed-forward there is no rates input and no feedforward key", ["posture_rate" in plain["inputs"], "feedforward" in plain["stages"][0]["law"]["pd"]], [False, False])
ramp = np.outer(np.arange(0, 6), np.full(7, 0.1))                    # 0.1 rad per knot
check("position_play: the rate of a ramp is its slope, at both ends too", np.allclose(position_play.rates_from_knots(ramp, 0.05), 2.0), True)
check("position_play: a single knot has no rate", position_play.rates_from_knots(ramp[:1], 0.05).tolist(), [[0.0] * 7])
densified = motion_plan.path_poses(np.stack([np.zeros(7), np.full(7, 0.3), np.full(7, 0.3)], axis=1), steps=20)
check("motion_plan: the poses along a path include both ends, the corner and enough points between", [len(densified) >= 20, np.allclose(densified[0], 0), np.allclose(densified[-1], 0.3),
      any(np.allclose(q, 0.3) for q in densified)], [True, True, True, True])
keyframes = [{"start": 0.0, "t": 2.0, "pose_signed": {str(j): (30.0 if j == 7 else 0.0) for j in range(1, 8)}},
             {"start": 2.0, "t": 4.0, "pose_signed": {str(j): 0.0 for j in range(1, 8)}}]
times, joints, rates = play_show.sample_path(keyframes, slow=2.0)
check("play_show: the path starts at zero and ends at the last keyframe, stretched to twice as long", [times[-1], np.allclose(joints[0], 0), np.allclose(joints[-1], 0)], [8.0, True, True])
check("play_show: the wrist peaks at the keyframe, 30 deg, at twice the written time", [round(float(np.degrees(joints[:, 6]).max()), 2), round(float(times[np.argmax(joints[:, 6])]), 2)], [30.0, 4.0])
travelled = np.trapezoid(rates[:, 6], times)
check("play_show: the analytic rate integrates to the displacement (so it is the path's own, slowed with it)", abs(travelled) < 0.01 and float(np.abs(rates[:, 6]).max()) < 1.0, True)
raised = False
try:
    play_show.sample_path(keyframes, slow=0.5)
except ValueError:
    raised = True
check("play_show: --slow below 1 is refused", raised, True)
check("play_show: the tracking error is the largest joint's distance from the path at that time",
      round(play_show.tracking_error(times, joints, 4.0, np.r_[np.zeros(6), np.radians(30.0) - 0.05]), 3), 0.05)
check("play_show: an arm that is not where the path starts is refused, and one that is, is not",
      [play_show.start_refusal(np.full(7, np.radians(10.0)), np.zeros(7), 3.0) is not None, play_show.start_refusal(np.full(7, np.radians(1.0)), np.zeros(7), 3.0)], [True, None])
lower, upper = np.full(7, -1.0), np.full(7, 1.0)
check("play_show: a path in range and slow enough is accepted; one out of range or too fast is refused with the joint",
      [play_show.path_refusal(joints, rates, lower, upper, 1.3), "joint 7" in (play_show.path_refusal(joints, rates, lower, np.full(7, 0.1), 1.3) or ""),
       "joint 7" in (play_show.path_refusal(joints, rates, lower, upper, 0.01) or "")], [None, True, True])
check("play_show: the gates sit before the controller is built (clearance and typed confirmation, then CH6, then the controller)",
      [inspect.getsource(play_show.main).index(a) < inspect.getsource(play_show.main).index(b) for a, b in
       [("bringup.gate", "bringup.connect"), ("require_clearance", "input("), ("input(", "deadman_held"), ("deadman_held", "keeper.start()"), ("keeper.start()", "position_play.start")]],
      [True] * 5)

print("\nall bring-up tool checks pass" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
