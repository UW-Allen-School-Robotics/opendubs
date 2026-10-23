#!/usr/bin/env python3
"""What a bringup motion step says and refuses before it moves (software/openarm/gafro/motion_plan.py and the way bringup.py wires it in): a step with no
default size or direction, a lapse test that can pass, the clearance along the path, the plan, and the joint and direction typed back. Each refuses by
raising Refused or returning 1 before anything is enabled or moved. Off the robot: no gafro, no arm, no MuJoCo (the clearance is a stub).
Run: python3 software/openarm/tests/test_gafro_bringup.py
"""
import contextlib
import io
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GAFRO = os.path.join(HERE, "..", "gafro")
sys.path.insert(0, GAFRO)

import bringup  # noqa: E402
import guards  # noqa: E402
import motion_plan as mp  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def refuses(fn, *words):
    """Whether fn raises Refused naming all of `words`; None if it did not refuse."""
    try:
        fn()
    except guards.Refused as refused:
        return all(w in str(refused) for w in words)
    return None


def main_with(*argv):
    """(exit code, what it printed) of bringup.main() with these arguments."""
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["bringup.py", *argv]
    try:
        with contextlib.redirect_stdout(out):
            code = bringup.main()
    finally:
        sys.argv = old
    return code, out.getvalue()


# -- the step: a joint, a sign, a size -----------------------------------------------------------------------------------------------------
check("a small signed step is fine", mp.validate_step(3, 0.01), None)
check("a negative one too", mp.validate_step(2, -0.01), None)
check("no direction (zero) refuses and says why", refuses(lambda: mp.validate_step(3, 0.0), "sign", "no default direction"), True)
check("not a number refuses", refuses(lambda: mp.validate_step(3, float("nan")), "sign"), True)
check("a joint outside 1 to 7 refuses", refuses(lambda: mp.validate_step(8, 0.01), "1 to 7"), True)
check("0 is not a joint", refuses(lambda: mp.validate_step(0, 0.01), "1 to 7"), True)
check("a step over 5 deg refuses", refuses(lambda: mp.validate_step(3, math.radians(5.5)), "larger than", "0.01 rad"), True)
check("exactly 5 deg is allowed", mp.validate_step(3, math.radians(5.0)), None)
check("--allow-large lets a big one through", mp.validate_step(3, math.radians(20.0), allow_large=True), None)

# -- a lapse test that can pass --------------------------------------------------------------------------------------------------------------
check("the move's duration is the step over the speed", mp.duration(0.05, 0.1), 0.5)
check("the old defaults (0.05 rad at 0.1 rad/s, lapse after 1.0 s) cannot pass", mp.lapse_outlasts_permit(0.05, 0.1, 1.0), False)
check("lapse after 0.3 s at 0.1 rad/s cannot either: the 0.5 s lease outlasts a 0.5 s move", mp.lapse_outlasts_permit(0.05, 0.1, 0.3), False)
check("0.03 rad/s after 0.3 s can: a 1.67 s move against a permit that runs out near 0.8 s", mp.lapse_outlasts_permit(0.05, 0.03, 0.3), True)
check("a negative step is the same length of move", mp.lapse_outlasts_permit(-0.05, 0.03, 0.3), True)
check("the refusal for a lapse that cannot pass names the cause and the speed that would do",
      refuses(lambda: mp.require_lapse_can_pass(0.05, 0.1, 0.3), "cannot pass", "0.5", "rad/s"), True)
check("a lapse that can pass raises nothing", mp.require_lapse_can_pass(0.05, 0.03, 0.3), None)

# -- the joint and direction typed back ------------------------------------------------------------------------------------------------------
check("the token is the joint and the sign", [mp.confirmation_token(3, 0.01), mp.confirmation_token(3, -0.01), mp.confirmation_token(7, 0.2)], ["3+", "3-", "7+"])
plan = mp.describe("right", 3, np.radians([2.2, 1.1, 0.1, 0.2, -0.2, -0.5, -0.3]), 0.05, 0.1, (39.8, "a <-> b"), 20.0)
check("the plan names the side, the joint, the direction and the pose", all(w in plan for w in ("right arm", "joint 3", "POSITIVE", "by 2.86 deg", "+0.0500 rad", "+2.20 deg", "this one moves")), True)
check("the plan says the other way when the step is negative", "NEGATIVE" in mp.describe("left", 2, [0.0] * 7, -0.02, 0.1, None, None), True)
check("the plan says plainly when clearance was not checked", "NOT checked" in mp.describe("left", 2, [0.0] * 7, -0.02, 0.1, None, None), True)
check("the plan states the twin's optimism", "6 deg optimistic" in plan, True)
quiet = lambda *_: None
check("the matching token goes ahead", mp.confirm(plan, 3, 0.05, ask=lambda _p: "3+", out=quiet), None)
check("a space around it does not matter", mp.confirm(plan, 3, 0.05, ask=lambda _p: " 3+ ", out=quiet), None)
check("the wrong direction does not confirm", refuses(lambda: mp.confirm(plan, 3, 0.05, ask=lambda _p: "3-", out=quiet), "not confirmed", "3+", "nothing moved"), True)
check("the wrong joint does not", refuses(lambda: mp.confirm(plan, 3, 0.05, ask=lambda _p: "2+", out=quiet), "not confirmed"), True)
check("a bare yes does not", refuses(lambda: mp.confirm(plan, 3, 0.05, ask=lambda _p: "y", out=quiet), "not confirmed"), True)
check("nothing typed does not", refuses(lambda: mp.confirm(plan, 3, 0.05, ask=lambda _p: "", out=quiet), "not confirmed"), True)


def no_terminal(_prompt):
    raise EOFError


check("with no terminal and no --confirm it refuses and says how to go on", refuses(lambda: mp.confirm(plan, 3, 0.05, ask=no_terminal, out=quiet), "terminal", "--confirm 3+"), True)
check("--confirm with the right token goes ahead without asking", mp.confirm(plan, 3, 0.05, supplied="3+", ask=no_terminal, out=quiet), None)
check("--confirm with the wrong direction refuses", refuses(lambda: mp.confirm(plan, 3, 0.05, supplied="3-", ask=no_terminal, out=quiet), "not confirmed"), True)
shown = io.StringIO()
mp.confirm(plan, 3, 0.05, supplied="3+", out=lambda text: shown.write(text))
check("the plan is printed before it is confirmed, even by --confirm", "joint 3 will turn POSITIVE" in shown.getvalue(), True)

# -- the clearance along the path ------------------------------------------------------------------------------------------------------------
start = np.zeros(7)
target = start.copy(); target[1] = -0.2
poses = mp.line_poses(start, target, 10)
check("the path has both ends and the steps between", [len(poses), float(poses[0][1]), float(poses[-1][1])], [11, 0.0, -0.2])
check("and it is the straight line", abs(float(poses[5][1]) + 0.1) < 1e-12, True)
tight_in_the_middle = lambda q: (50.0 if abs(float(q[1]) + 0.1) > 0.03 else 8.0, "arm <-> column")
check("a path that is clear at both ends and close in between is refused, naming how close and to what",
      refuses(lambda: mp.require_clearance(poses, tight_in_the_middle, 20.0), "8.0 mm", "arm <-> column", "20 mm"), True)
check("a path that is clear all along passes and reports the closest", mp.require_clearance(poses, lambda q: (40.0 + float(q[1]), "a <-> b"), 20.0), (39.8, "a <-> b"))
check("a start that is already too close is refused too", refuses(lambda: mp.require_clearance(poses, lambda q: (5.0 if float(q[1]) == 0.0 else 50.0, "hand <-> chest"), 20.0), "5.0 mm"), True)
check("exactly at the minimum passes", mp.require_clearance(poses, lambda q: (20.0, "a <-> b"), 20.0), (20.0, "a <-> b"))
check("a lower --min-clearance-mm lets a close path through", mp.require_clearance(poses, tight_in_the_middle, 5.0), (8.0, "arm <-> column"))
check("no poses refuses", refuses(lambda: mp.require_clearance([], lambda q: (1.0, "x"), 20.0), "no poses"), True)
check("the refusal calls the twin a floor and not a margin", refuses(lambda: mp.require_clearance(poses, tight_in_the_middle, 20.0), "floor", "6 deg"), True)

# -- the twin that cannot be built is a refusal, not a pass ---------------------------------------------------------------------------------
check("no twin directory refuses and offers --skip-clearance", refuses(lambda: mp.twin_clearance("right", sim_dir="/nonexistent/sim"), "not at", "--skip-clearance"), True)
import tempfile


def stub_twin(build_body):
    """A sim/ directory whose scene.build does `build_body`, so that "cannot be built" is deterministic and needs no MuJoCo model."""
    d = tempfile.mkdtemp(prefix="stub_sim_")
    with open(os.path.join(d, "scene.py"), "w") as f:
        f.write(f"import sys\ndef build(assets=None):\n    {build_body}\n")
    with open(os.path.join(d, "clearance.py"), "w") as f:
        f.write("")
    return d


class FakeMujoco:                      # stands in for `import mujoco` where it is not installed
    pass


sys.modules.setdefault("mujoco", FakeMujoco())
broken = stub_twin('raise RuntimeError("no such mesh")')
check("a twin that cannot be built refuses, says why, and does not read as a pass",
      refuses(lambda: mp.twin_clearance("right", assets="/x", sim_dir=broken), "could not be built", "no such mesh", "--skip-clearance"), True)
exits = stub_twin('sys.exit("cannot find the OpenArm model")')
check("a twin whose assets finder calls sys.exit is a refusal too, not the end of the process",
      refuses(lambda: mp.twin_clearance("right", assets="/x", sim_dir=exits), "could not be built", "cannot find the OpenArm model"), True)
check("the twin's directory is not left on sys.path", [broken in sys.path, exits in sys.path], [False, False])
fine = stub_twin("return object()")
os.remove(os.path.join(fine, "clearance.py"))
open(os.path.join(fine, "clearance.py"), "w").write("def set_pose(*a, **k): pass\ndef clearances(m, d): return [(30.0, 'oa_openarm_left_hand', 'body'), (42.0, 'oa_openarm_right_hand', 'body')]\n")
FakeMujoco.MjData = staticmethod(lambda model: object())
twin = mp.twin_clearance("right", assets="/x", sim_dir=fine)
check("a twin that builds counts only the arm being moved: the closer pair of the other arm is ignored", twin([0.0] * 7), (42.0, "oa_openarm_right_hand <-> body"))
check("and the other side's number is its own", mp.twin_clearance("left", assets="/x", sim_dir=fine)([0.0] * 7), (30.0, "oa_openarm_left_hand <-> body"))

# -- bringup.py: nothing is enabled for a step that cannot be taken ---------------------------------------------------------------------------
base = ["--side", "right", "--model", "m.xml", "--backend", "mujoco"]
code, text = main_with("move", *base)
check("move without --delta refuses before connecting", [code, "no default direction" in text], [1, True])
code, text = main_with("move", *base, "--delta", "0.0")
check("a zero step refuses", [code, "sign" in text], [1, True])
code, text = main_with("move", *base, "--delta", "+0.2")
check("a 11 deg step refuses", [code, "larger than" in text], [1, True])
code, text = main_with("lapse", *base, "--delta", "+0.05", "--speed", "0.1", "--after", "1.0")
check("the old lapse defaults refuse, saying the test cannot pass", [code, "cannot pass" in text], [1, True])

# -- the helpers in env.sh ------------------------------------------------------------------------------------------------------------------
env = os.path.join(GAFRO, "env.sh")
for call in ("arm_move right 3", "arm_lapse right", "arm_move"):
    run = subprocess.run(["bash", "-c", f"ARM_CODING=/nonexistent ARM_VENV=/nonexistent . '{env}' >/dev/null 2>&1; {call}"], capture_output=True, text=True)
    check(f"`{call}` without a signed step refuses with a usage line", [run.returncode, "WITH A SIGN" in run.stderr], [1, True])

run = subprocess.run(["bash", "-c", f"unset ARM_CODING; . '{env}'"], capture_output=True, text=True)
check("env.sh without ARM_CODING says so and does not guess a layout", [run.returncode, "ARM_CODING" in run.stderr], [1, True])

print("\nall bringup checks pass" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
