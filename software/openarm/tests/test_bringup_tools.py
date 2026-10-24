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
import torque_probe  # noqa: E402
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
check("goto_hand: a good plan passes", goto_hand.check_plan(dict(states=line), lo, hi, 1.0, 15, 6.0, np.array([0.03, 0, 0])), None)
check("goto_hand: an offset over the limit refuses", refuses(lambda: goto_hand.check_plan(dict(states=line), lo, hi, 1.0, 15, 6.0, np.array([0.07, 0, 0])), "limit is 6 cm"), True)
check("goto_hand: a joint leaving its range refuses", refuses(lambda: goto_hand.check_plan(dict(states=line), lo * 0.01, hi * 0.01, 1.0, 15, 6.0, np.array([0.03, 0, 0])), "range"), True)
check("goto_hand: a bent path refuses", refuses(lambda: goto_hand.check_plan(dict(states=bent), lo, hi, 1.0, 15, 6.0, np.array([0.03, 0, 0])), "bends", "3.0"), True)
check("goto_hand: a bad offset refuses", run(goto_hand.main, *base, "--offset-cm", "3,0")[0], 1)
check("goto_hand: gate, connect, plan, checks, clearance, typed `hand`, deadman, first permit",
      in_order(goto_hand.main, "bringup.gate(", "bringup.connect(", "hand_plan.plan_hand(", "check_plan(", "require_clearance(", 'typed != "hand"', "bringup.deadman_held(", "keeper.start()"), True)

# -- track_hand --------------------------------------------------------------------------------------------------------------------------------
code, text = run(track_hand.main, *base, "--offset-cm", "3,0,0", "--controller", "track")
check("track_hand: the torque `track` controller is refused, saying why", [code, "kicked" in text], [1, True])
knots = track_hand.reference_knots(line, 2.0)
steps = np.linalg.norm(np.diff(np.array(knots), axis=0), axis=1)
check("track_hand: the reference leaves and arrives at rest", [np.allclose(knots[0], line[:, 0]), np.allclose(knots[-1], line[:, -1]), bool(steps[0] < steps[20] and steps[-1] < steps[20])], [True, True, True])
check("track_hand: no integral keeps the pd law, an integral makes a pid", [list(track_hand.velocity_controller(2, 0.15, 0.5, 1, 0.3, True)["stages"][0]["law"]),
      list(track_hand.velocity_controller(2, 0.15, 0.5, 1, 0.3, True, 5.0)["stages"][0]["law"])], [["pd"], ["pid"]])
check("track_hand: gate, connect, plan, checks, clearance, typed, deadman, first permit, controller",
      in_order(track_hand.main, "bringup.gate(", "bringup.connect(", "hand_plan.plan_hand(", "check_plan(", "require_clearance(", 'typed != "hand"', "bringup.deadman_held(", "keeper.start()", "robot.control("), True)

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

# -- torque_probe ------------------------------------------------------------------------------------------------------------------------------
controllers = {"a": {"stages": [{"law": {"pd": {"natural_frequency": 40, "damping_ratio": 1.0, "max_speed": 0.6}}}, {"stack": [{"law": {"pd": {"natural_frequency": 20}}}]}]}}
scaled = torque_probe.scale_natural_frequencies(controllers, 0.25)
check("torque_probe: every natural_frequency is scaled, nothing else, and the original is untouched",
      [scaled["a"]["stages"][0]["law"]["pd"], scaled["a"]["stages"][1]["stack"][0]["law"]["pd"]["natural_frequency"], controllers["a"]["stages"][0]["law"]["pd"]["natural_frequency"]],
      [{"natural_frequency": 10.0, "damping_ratio": 1.0, "max_speed": 0.6}, 5.0, 40])
t = np.arange(0, 2.0, 0.004)
q = np.zeros((len(t), 7)); v = np.zeros((len(t), 7)); tau = np.zeros((len(t), 7))
quiet = torque_probe.analyse(t, q, v, tau)
check("torque_probe: a quiet record is called quiet", "the transition was quiet" in quiet, True)
q[100:, 6] = -0.026; v[100:103, 6] = [3.0, 6.1, 2.0]; tau[100:103, 6] = [1.0, 2.5, 1.0]
kick = torque_probe.analyse(t, q, v, tau)
check("torque_probe: a 6 rad/s spike on joint 7 is found, with its time, and the window names the joint",
      ["joint 7  +6.10 rad/s" in kick, "0.400 s" in kick, "window around the first event, joint 7" in kick], [True, True, True])

print("\nall bring-up tool checks pass" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
